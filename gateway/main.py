from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import threading
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, WebSocket
from fastapi.responses import FileResponse, StreamingResponse

from config import DEFAULT_SETTINGS, EXPECTED_NODE_IDS, build_settings
from csv_store import CsvStore
from epoch_reconstructor import EpochReconstructor
from localization import WCLLocalizationEngine
from models import LocalizationResult, NodeReady, ObservationItem, RoomConfigRequest
from mqtt_ingest import MqttGateway
from position_filter import PositionEMA
from collection import DatasetCollection
from models import AIPosition
from models import MeasurementRequest

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(name)s | %(message)s")
logger = logging.getLogger("rf-gateway")


def token_for_mac(mac_text: str, salt: str) -> str:
    raw = mac_text.replace(":", "").replace("-", "").strip()
    if len(raw) != 12:
        raise ValueError("MAC must contain 12 hexadecimal characters")
    try:
        mac_bytes = bytes.fromhex(raw)
    except ValueError as exc:
        raise ValueError("MAC contains non-hexadecimal characters") from exc
    return hashlib.sha256(mac_bytes + salt.encode("utf-8")).hexdigest()


class WebSocketManager:
    def __init__(self) -> None:
        self.clients: dict[WebSocket, str] = {}
        self.loop: asyncio.AbstractEventLoop | None = None
        self.lock = asyncio.Lock()

    async def add(self, websocket: WebSocket, session_id: str) -> bool:
        await websocket.accept()
        async with self.lock:
            if len(self.clients) >= runtime.settings.max_ws_clients:
                await websocket.close(code=1013, reason="Too many clients")
                return False
            self.clients[websocket] = session_id
        return True

    async def remove(self, websocket: WebSocket) -> None:
        async with self.lock:
            self.clients.pop(websocket, None)

    async def broadcast(self, data: dict[str, Any]) -> None:
        session_id = data.get("session_id")
        async with self.lock:
            clients = [(ws, sid) for ws, sid in self.clients.items() if sid == session_id]
        stale = []
        for websocket, _ in clients:
            try:
                await websocket.send_json(data)
            except Exception:
                stale.append(websocket)
        for websocket in stale:
            await self.remove(websocket)


class GatewayRuntime:
    def __init__(self) -> None:
        self.base_settings = DEFAULT_SETTINGS
        self.settings = self.base_settings
        self.storage = CsvStore(self.settings.collection_csv_path, self.settings.realtime_csv_path)
        logger.info("Collection CSV: %s", self.storage.dataset_path)

        self.localizer = WCLLocalizationEngine(self.settings)
        # Room changes run on asyncio, while MQTT/expiry can produce positions.
        self.position_lock = threading.RLock()
        self.position_filter = PositionEMA(
            self.settings.position_ema_alpha, self.settings.position_ema_reset_gap
        )
        self.ws_manager = WebSocketManager()
        self.reconstructor = EpochReconstructor(
            expected_nodes=set(EXPECTED_NODE_IDS),
            timeout_ms=self.settings.epoch_timeout_ms,
            on_complete=self.on_unified_epoch,
            min_overlap_ms=self.settings.min_overlap_ms,
            on_quality=self.on_epoch_quality,
        )
        self.mqtt = MqttGateway(
            self.settings,
            self.reconstructor,
            self.on_node_ready,
            self.on_node_seen,
        )
        self.collection = None
        if COLLECTION_MODE:
            self.collection = DatasetCollection(self)
            self.mqtt.collection_handler = self.collection.on_raw
        self.node_last_seen_ms: dict[str, int | None] = {node_id: None for node_id in EXPECTED_NODE_IDS}
        self.node_session: dict[str, str | None] = {node_id: None for node_id in EXPECTED_NODE_IDS}
        self.node_message_type: dict[str, str | None] = {node_id: None for node_id in EXPECTED_NODE_IDS}

    @property
    def target_token(self) -> str | None:
        if self.settings.target_device_token.strip():
            return self.settings.target_device_token.strip().lower()
        if self.settings.target_device_mac.strip():
            try:
                return token_for_mac(self.settings.target_device_mac, self.settings.device_token_salt)
            except ValueError:
                logger.error("Invalid TARGET_DEVICE_MAC=%r", self.settings.target_device_mac)
        return None

    def set_room(self, width_m: float, length_m: float, grid_cell_width_m: float, grid_cell_length_m: float) -> None:
        with self.position_lock:
            self.settings = build_settings(
                width_m,
                length_m,
                grid_cell_width_m=grid_cell_width_m,
                grid_cell_length_m=grid_cell_length_m,
                base=self.base_settings,
            )
            self.localizer = WCLLocalizationEngine(self.settings)
            self.position_filter.reset()
        logger.info(
            "Room changed: %.2f x %.2f m -> cell %.2f x %.2f m -> grid %d cols x %d rows",
            width_m, length_m, grid_cell_width_m, grid_cell_length_m,
            self.settings.grid.nx, self.settings.grid.ny,
        )

    def on_node_seen(self, node_id: str, session_id: str, message_type: str) -> None:
        now_ms = int(time.time() * 1000)
        self.node_last_seen_ms[node_id] = now_ms
        self.node_session[node_id] = session_id
        self.node_message_type[node_id] = message_type

    def on_node_ready(self, ready: NodeReady) -> None:
        logger.info("NODE_READY: %s session=%s", ready.node_id, ready.session_id)

    def node_status(self) -> list[dict[str, Any]]:
        now_ms = int(time.time() * 1000)
        items = []
        for node_id in EXPECTED_NODE_IDS:
            last_seen = self.node_last_seen_ms[node_id]
            online = last_seen is not None and (now_ms - last_seen) <= self.settings.node_offline_timeout_ms
            items.append(
                {
                    "node_id": node_id,
                    "online": online,
                    "last_seen_ms": last_seen,
                    "seconds_since_seen": None if last_seen is None else round(max(0, now_ms - last_seen) / 1000.0, 1),
                    "session_id": self.node_session[node_id],
                    "last_message_type": self.node_message_type[node_id],
                    "timeout_ms": self.settings.node_offline_timeout_ms,
                }
            )
        return items

    def should_localize(self, device_token: str, device_count: int) -> bool:
        target = self.target_token
        if not self.settings.localize_target_only:
            return True
        if target is None:
            return device_count == 1
        return device_token == target

    def on_unified_epoch(
        self,
        session_id: str,
        epoch_id: int,
        merged: dict[str, dict[str, ObservationItem]],
        sniff_start_ms: int,
        sniff_end_ms: int,
    ) -> None:
        created_at_ms = int(time.time() * 1000)

        for device_token, node_items in merged.items():
            node_rssi = {node_id: item.median_rssi_dbm for node_id, item in node_items.items()}
            node_packet_count = {node_id: item.packet_count for node_id, item in node_items.items()}

            if not self.should_localize(device_token, len(merged)):
                continue

            with self.position_lock:
                estimate = self.localizer.localize(node_rssi)
                if estimate is None:
                    continue
                x, y = estimate.x_m, estimate.y_m
                mode = self.settings.position_filter_mode
                if mode == "ema":
                    filtered = self.position_filter.update(session_id, device_token, epoch_id, x, y)
                    if filtered is None:
                        continue
                    x, y = filtered
                # The displayed zone follows the displayed point, not the raw WCL point.
                zone = self.localizer.zone_id(x, y) if mode == "ema" else estimate.zone_id
                result = LocalizationResult(
                    track_id=device_token,
                    session_id=session_id,
                    epoch_id=epoch_id,
                    zone_id=zone,
                    x_m=round(x, 3),
                    y_m=round(y, 3),
                    raw_x_m=round(estimate.x_m, 3),
                    raw_y_m=round(estimate.y_m, 3),
                    raw_zone_id=estimate.zone_id,
                    filtered_x_m=round(x, 3),
                    filtered_y_m=round(y, 3),
                    filtered_zone_id=zone,
                    position_filter_mode=mode,
                    position_ema_alpha=self.settings.position_ema_alpha if mode == "ema" else None,
                    confidence=round(estimate.confidence, 3),
                    nodes_used=estimate.nodes_used,
                    rssi_vector_dbm=node_rssi,
                    timestamp_ms=created_at_ms,
                    node_packet_count=node_packet_count,
                    queue_drop_count=self.reconstructor.last_quality.get("queue_drop_count", {}),
                    overlap_ms=self.reconstructor.last_quality.get("overlap_ms", 0),
                )

            self.storage.save_localization(result.model_dump())

            if self.ws_manager.loop is not None:
                asyncio.run_coroutine_threadsafe(
                    self.ws_manager.broadcast(result.model_dump()),
                    self.ws_manager.loop,
                )
            logger.info(
                "LOCALIZATION epoch=%s token=%s zone=%s x=%.2f y=%.2f nodes=%d vector=%s",
                epoch_id, device_token[:10], result.zone_id, result.x_m, result.y_m, result.nodes_used, node_rssi,
            )

    def on_epoch_quality(self, quality: dict) -> None:
        self.storage.store_quality(quality)
        logger.info("EPOCH session=%s epoch=%s status=%s overlap=%dms",
                    quality["session_id"], quality["epoch_id"], quality["status"], quality["overlap_ms"])
        if self.ws_manager.loop is not None:
            asyncio.run_coroutine_threadsafe(self.ws_manager.broadcast(
                {"message_type": "EPOCH_QUALITY", **quality}), self.ws_manager.loop)


async def expiry_loop() -> None:
    while True:
        if COLLECTION_MODE:
            try:
                runtime.collection.tick()
            except OSError:
                logger.exception("Collection CSV write failed; close Excel if it locks the CSV, then retry")
        else:
            runtime.reconstructor.flush_expired()
        await asyncio.sleep(0.1)


@asynccontextmanager
async def lifespan(app: FastAPI):
    runtime.ws_manager.loop = asyncio.get_running_loop()
    runtime.mqtt.start()
    task = asyncio.create_task(expiry_loop())
    try:
        yield
    finally:
        task.cancel()
        runtime.mqtt.stop()
        if runtime.collection is not None:
            runtime.collection.tick(stopping=True)


app = FastAPI(title="RF Localization Gateway MVP", version="1.2.0", lifespan=lifespan)


@app.get("/", include_in_schema=False)
async def dashboard() -> FileResponse:
    return FileResponse(Path(__file__).with_name("static") / "index.html")


@app.get("/health")
async def health() -> dict[str, Any]:
    grid = runtime.settings.grid
    return {
        "status": "ok",
        "collection_mode": COLLECTION_MODE,
        "collection_csv_path": str(runtime.storage.dataset_path),
        "realtime_csv_path": str(runtime.storage.realtime_path),
        "vector_mode": "strict_4",
        "min_overlap_ms": runtime.settings.min_overlap_ms,
        "epoch_counters": dict(runtime.reconstructor.counters),
        "mqtt_connected": runtime.mqtt.connected,
        "expected_nodes": list(EXPECTED_NODE_IDS),
        "nodes": runtime.node_status(),
        "target_configured": runtime.target_token is not None,
        "position_filter": {
            "mode": runtime.settings.position_filter_mode,
            "alpha": runtime.settings.position_ema_alpha,
            "reset_gap_epochs": runtime.settings.position_ema_reset_gap,
        },
        "room": {
            "width_m": runtime.settings.room_width_m,
            "length_m": runtime.settings.room_length_m,
        },
        "grid": {
            "nx": grid.nx,
            "ny": grid.ny,
            "cell_width_m": runtime.settings.grid_cell_width_m,
            "cell_length_m": runtime.settings.grid_cell_length_m,
        },
    }


@app.get("/api/v1/nodes")
async def get_nodes(session_id: str = "session-dev-001") -> list[dict[str, Any]]:
    if COLLECTION_MODE:
        return runtime.collection.nodes(session_id)
    return runtime.node_status()


@app.get("/api/v1/epochs/quality")
async def epoch_quality(limit: int = 50):
    return runtime.storage.latest_quality(min(max(limit, 1), 200))


@app.get("/api/v1/room")
async def get_room() -> dict[str, Any]:
    grid = runtime.settings.grid
    return {
        "width_m": runtime.settings.room_width_m,
        "length_m": runtime.settings.room_length_m,
        "grid": runtime.localizer.grid_payload(),
        "nodes": {
            "ESP01": {"x_m": 0.0, "y_m": 0.0, "label": "Top Left"},
            "ESP02": {"x_m": runtime.settings.room_width_m, "y_m": 0.0, "label": "Top Right"},
            "ESP03": {"x_m": 0.0, "y_m": runtime.settings.room_length_m, "label": "Bottom Left"},
            "ESP04": {"x_m": runtime.settings.room_width_m, "y_m": runtime.settings.room_length_m, "label": "Bottom Right"},
        },
        "grid_config": {
            "columns": grid.nx,
            "rows": grid.ny,
            "cell_width_m": runtime.settings.grid_cell_width_m,
            "cell_length_m": runtime.settings.grid_cell_length_m,
            "actual_last_column_width_m": min(
                runtime.settings.grid_cell_width_m,
                runtime.settings.room_width_m - runtime.settings.grid_cell_width_m * (grid.nx - 1),
            ),
            "actual_last_row_length_m": min(
                runtime.settings.grid_cell_length_m,
                runtime.settings.room_length_m - runtime.settings.grid_cell_length_m * (grid.ny - 1),
            ),
        },
    }


@app.post("/api/v1/room")
async def update_room(request: RoomConfigRequest) -> dict[str, Any]:
    if COLLECTION_MODE:
        try:
            runtime.collection.ensure_idle()
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc
    runtime.set_room(
        request.width_m,
        request.length_m,
        request.grid_cell_width_m,
        request.grid_cell_length_m,
    )
    return await get_room()


@app.post("/api/v1/ai/position")
async def publish_ai_position(position: AIPosition):
    if COLLECTION_MODE:
        raise HTTPException(409, "AI display requires realtime mode")
    if position.track_id != runtime.target_token:
        raise HTTPException(409, "AI target does not match gateway target")
    if abs(int(time.time() * 1000) - position.timestamp_ms) > 5000:
        raise HTTPException(409, "AI position is stale")
    if position.status == "OK":
        if position.x_m is None or position.y_m is None or position.zone is None:
            raise HTTPException(422, "AI position requires coordinates and zone")
        if (position.x_m > runtime.settings.room_width_m or
                position.y_m > runtime.settings.room_length_m):
            raise HTTPException(422, "AI position is outside room")
    await runtime.ws_manager.broadcast({"message_type": "AI_POSITION", **position.model_dump()})
    return {"status": "ok"}


@app.get("/api/v1/localization/latest")
async def latest_localization(limit: int = 50) -> list[dict[str, Any]]:
    limit = min(max(limit, 1), 200)
    return runtime.storage.latest_localization(limit)


@app.websocket("/ws/v1/sessions/{session_id}")
async def localization_websocket(websocket: WebSocket, session_id: str) -> None:
    accepted = await runtime.ws_manager.add(websocket, session_id)
    if not accepted:
        return
    try:
        history = runtime.storage.latest_localization(50)
        for item in reversed(history):
            if item.get("session_id") == session_id:
                await websocket.send_json(item)
        while True:
            await websocket.receive_text()
    except Exception:
        await runtime.ws_manager.remove(websocket)


@app.get("/api/v1/mode")
async def get_mode():
    return {"collection_mode": COLLECTION_MODE, "mode": "collection" if COLLECTION_MODE else "realtime",
            "session_id": os.getenv("SESSION_ID", "session-dev-001")}


def require_collection():
    if not COLLECTION_MODE:
        raise HTTPException(409, "Set COLLECTION_MODE=true in project .env and restart gateway")


@app.get("/api/v1/collection")
async def collection_state(session_id: str):
    require_collection()
    return runtime.collection.state(session_id)


@app.post("/api/v1/measurements")
async def start_measurement(request: MeasurementRequest):
    require_collection()
    try:
        return runtime.collection.start(request.session_id, request.zone)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc


@app.get("/api/v1/collection/samples")
async def collection_samples(session_id: str, measurement_id: str | None = None, limit: int = 50):
    require_collection()
    return runtime.storage.latest_samples(session_id, min(max(limit, 1), 200), measurement_id)


@app.get("/api/v1/dataset/export")
def export_dataset(session_id: str, measurement_id: str | None = None):
    require_collection()
    return StreamingResponse(
        runtime.storage.export_samples(session_id, measurement_id),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="dataset-raw.csv"'},
    )


# Read the mode from project .env; false keeps the original realtime pipeline.
COLLECTION_MODE = os.getenv("COLLECTION_MODE", "false").strip().lower() in {"true", "1", "yes"}
runtime = GatewayRuntime()


if __name__ == "__main__":
    import uvicorn

    print("Starting RF Localization Gateway...")
    uvicorn.run("main:app", host=os.getenv("GATEWAY_BIND_HOST", "0.0.0.0"),
                port=int(os.getenv("GATEWAY_PORT", "8000")), reload=False)
