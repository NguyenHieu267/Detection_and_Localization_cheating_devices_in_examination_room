from __future__ import annotations

from dataclasses import dataclass, field, replace
import os
import math
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATABASE_DIR = PROJECT_ROOT / "database"


def resolve_database_path(path: str | Path | None = None) -> str:
    """Resolve SQLite files independently of the terminal's working directory.

    Relative paths live under database/. Old default locations are aliases for
    database/rf_gateway.db; existing files are never moved or merged implicitly.
    Explicit absolute paths to other files and :memory: remain supported.
    """
    mode = os.getenv("COLLECTION_MODE", "false").strip().lower() in {"true", "1", "yes"}
    default_path = os.getenv("COLLECTION_DATABASE_PATH" if mode else "REALTIME_DATABASE_PATH", "")
    value = str(path if path is not None else (os.getenv("DATABASE_PATH") or default_path)).strip()
    if value == ":memory:":
        return value
    requested = Path(value or "rf_gateway.db").expanduser()
    legacy_defaults = {
        (PROJECT_ROOT / "rf_gateway.db").resolve(),
        (PROJECT_ROOT / "gateway" / "rf_gateway.db").resolve(),
        (PROJECT_ROOT / "rf_localization_mvp_node_grid" / "rf_gateway.db").resolve(),
        (PROJECT_ROOT / "rf_localization_mvp_node_grid" / "gateway" / "rf_gateway.db").resolve(),
    }
    candidate = (PROJECT_ROOT / requested).resolve()
    if candidate in legacy_defaults:
        return str(DATABASE_DIR / "rf_gateway.db")
    if requested.is_absolute():
        return str(candidate)
    if requested.parts and requested.parts[0].lower() == "database":
        requested = Path(*requested.parts[1:])
    candidate = (DATABASE_DIR / requested).resolve()
    if not candidate.is_relative_to(DATABASE_DIR):
        raise ValueError("Relative DATABASE_PATH must stay inside the database directory")
    return str(candidate)

from dotenv import load_dotenv

load_dotenv(PROJECT_ROOT / ".env")


COLLECTION_DURATION_S = int(os.getenv("COLLECTION_DURATION_S", "30"))
COLLECTION_RECEIVE_WAIT_MS = int(os.getenv("COLLECTION_RECEIVE_WAIT_MS", "3000"))
COLLECTION_VECTOR_WAIT_MS = int(os.getenv("COLLECTION_VECTOR_WAIT_MS", "400"))
if COLLECTION_DURATION_S < 1 or COLLECTION_RECEIVE_WAIT_MS < 0 or COLLECTION_VECTOR_WAIT_MS < 0:
    raise ValueError("Collection duration must be positive; wait times must be non-negative")


@dataclass(frozen=True)
class GridConfig:
    nx: int
    ny: int
    cell_width_m: float
    cell_length_m: float


@dataclass(frozen=True)
class Settings:
    # MQTT
    mqtt_host: str = os.getenv("MQTT_HOST", "127.0.0.1")
    mqtt_port: int = int(os.getenv("MQTT_PORT", "1883"))
    mqtt_username: str = os.getenv("MQTT_USERNAME", "")
    mqtt_password: str = os.getenv("MQTT_PASSWORD", "")
    mqtt_client_id: str = os.getenv("MQTT_CLIENT_ID", "rf-gateway-mvp")
    observation_topic: str = "rf/v1/nodes/+/observation"
    ready_topic: str = "rf/v1/nodes/+/ready"

    # Epoch reconstruction.
    epoch_timeout_ms: int = int(os.getenv("EPOCH_TIMEOUT_MS", "1800"))
    min_overlap_ms: int = int(os.getenv("MIN_OVERLAP_MS", "600"))
    max_mqtt_bytes: int = 65536

    # Filter coordinates after WCL, leaving RSSI and confidence unchanged.
    position_filter_mode: str = os.getenv("POSITION_FILTER_MODE", "ema").strip().lower()
    position_ema_alpha: float = float(os.getenv("POSITION_EMA_ALPHA", "0.35"))
    position_ema_reset_gap: int = int(os.getenv("POSITION_EMA_RESET_GAP", "3"))

    # SQLite.
    database_path: str = field(default_factory=resolve_database_path)

    # Room and editable grid CELL SIZE. The Gateway automatically derives
    # the number of columns/rows from the room dimensions.
    room_width_m: float = float(os.getenv("ROOM_WIDTH_M", "8.0"))
    room_length_m: float = float(os.getenv("ROOM_LENGTH_M", "12.0"))
    grid_cell_width_m: float = float(os.getenv("GRID_CELL_WIDTH_M", "2.0"))
    grid_cell_length_m: float = float(os.getenv("GRID_CELL_LENGTH_M", "4.0"))

    # First-test device focus. The firmware may still publish every device;
    # the gateway localizes only this one when a target is configured.
    target_device_mac: str = os.getenv("TARGET_DEVICE_MAC", "")
    target_device_token: str = os.getenv("TARGET_DEVICE_TOKEN", "")
    device_token_salt: str = os.getenv("DEVICE_TOKEN_SALT", "secret-key")

    # When false, gateway localizes every observed device.
    localize_target_only: bool = os.getenv("LOCALIZE_TARGET_ONLY", "true").lower() in {
        "1", "true", "yes", "on"
    }

    # Node is considered offline when no MQTT message has been received for
    # this duration. Nodes normally publish one observation every epoch.
    node_offline_timeout_ms: int = int(os.getenv("NODE_OFFLINE_TIMEOUT_MS", "3500"))

    max_ws_clients: int = int(os.getenv("MAX_WS_CLIENTS", "64"))

    def __post_init__(self) -> None:
        if self.position_filter_mode not in {"ema", "off"}:
            raise ValueError("POSITION_FILTER_MODE must be ema or off")
        if not 0 < self.position_ema_alpha <= 1:
            raise ValueError("POSITION_EMA_ALPHA must be in (0, 1]")
        if self.position_ema_reset_gap < 1:
            raise ValueError("POSITION_EMA_RESET_GAP must be >= 1")

    @property
    def grid(self) -> GridConfig:
        return make_grid(
            self.room_width_m,
            self.room_length_m,
            self.grid_cell_width_m,
            self.grid_cell_length_m,
        )


EXPECTED_NODE_IDS = ("ESP01", "ESP02", "ESP03", "ESP04")


def make_grid(room_width_m: float, room_length_m: float, cell_width_m: float, cell_length_m: float) -> GridConfig:
    """Build a grid from the requested physical cell size.

    The number of columns/rows is derived automatically with ceil(). If the
    requested cell size does not divide the room exactly, the final column or
    row is narrower/shorter so the grid still covers the whole room.
    """
    width = max(float(room_width_m), 0.1)
    length = max(float(room_length_m), 0.1)
    target_cell_width = max(float(cell_width_m), 0.1)
    target_cell_length = max(float(cell_length_m), 0.1)
    nx = max(1, math.ceil(width / target_cell_width))
    ny = max(1, math.ceil(length / target_cell_length))
    return GridConfig(
        nx=nx,
        ny=ny,
        cell_width_m=target_cell_width,
        cell_length_m=target_cell_length,
    )


def build_settings(
    room_width_m: float,
    room_length_m: float,
    *,
    base: Settings,
    grid_cell_width_m: float | None = None,
    grid_cell_length_m: float | None = None,
) -> Settings:
    """Return settings with room-scaled corner anchors and cell-size-based grid."""
    cell_width = base.grid_cell_width_m if grid_cell_width_m is None else max(0.1, float(grid_cell_width_m))
    cell_length = base.grid_cell_length_m if grid_cell_length_m is None else max(0.1, float(grid_cell_length_m))
    return replace(
        base,
        room_width_m=float(room_width_m),
        room_length_m=float(room_length_m),
        grid_cell_width_m=cell_width,
        grid_cell_length_m=cell_length,
    )


DEFAULT_SETTINGS = Settings()
