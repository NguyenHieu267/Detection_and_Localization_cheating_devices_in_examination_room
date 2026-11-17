"""Optional Collection branch: original frames plus derived, labelled RSSI vectors."""
from __future__ import annotations

import logging
import sqlite3
import threading
import time
from types import SimpleNamespace
from uuid import uuid4

from config import (EXPECTED_NODE_IDS, COLLECTION_DURATION_S, COLLECTION_RECEIVE_WAIT_MS,
                    COLLECTION_VECTOR_WAIT_MS)

logger = logging.getLogger(__name__)


def now_ms():
    return int(time.time() * 1000)


class DatasetCollection:
    def __init__(self, runtime):
        self.runtime = runtime
        self.database = runtime.database
        self.database.init_collection_schema()
        self.lock = threading.RLock()
        self.status = {}
        self.flush_ms = COLLECTION_RECEIVE_WAIT_MS
        self.duration_s = COLLECTION_DURATION_S

    def on_raw(self, report):
        """Store each original RSSI and timestamp, without median, EMA or WCL."""
        received = now_ms()
        with self.lock:
            old = self.status.get(report.node_id)
            info = {
                "session_id": report.session_id,
                "node_id": report.node_id,
                "last_seen_ms": received,
                "clock_synced": report.clock_synced,
                "capture_end_ms": report.sniff_end_unix_ms,
                "queue_drop_count": report.queue_drop_count,
                "batch_drop_count": report.batch_drop_count,
            }
            # A delayed report cannot make an old clock look READY.
            if old is None or info["capture_end_ms"] >= old["capture_end_ms"]:
                self.status[report.node_id] = info

            try:
                self.database.store_raw_chunk(report)
                if report.clock_synced:
                    tokens = {sample.device_token for sample in report.observations}
                    samples = [
                        SimpleNamespace(
                            timestamp_ms=sample.capture_unix_ms,
                            rssi_dbm=sample.rssi_dbm,
                            frame_type=sample.frame_type,
                            frame_subtype=sample.frame_subtype,
                            channel=sample.channel,
                        )
                        for sample in report.observations
                        if self.runtime.should_localize(sample.device_token, len(tokens))
                    ]
                    incoming = SimpleNamespace(
                        session_id=report.session_id,
                        node_id=report.node_id,
                        message_id=f"{report.node_id}-{report.epoch_id}-{report.chunk_index}",
                        samples=samples,
                        model_dump_json=report.model_dump_json,
                    )
                    self.database.store_collection_samples(incoming, received)

                if old:
                    for measurement in self.database.measurements(active_only=True):
                        inside = measurement["start_ms"] <= received < measurement["end_ms"]
                        same_session = measurement["session"] == report.session_id
                        dropped = (
                            info["queue_drop_count"] > old["queue_drop_count"]
                            or info["batch_drop_count"] > old["batch_drop_count"]
                        )
                        if same_session and inside and dropped:
                            self.database.update_measurement(
                                measurement["measurement_id"], measurement["status"], True
                            )
            except sqlite3.Error:
                logger.exception(
                    "Collection SQLite write failed; original raw_chunks remain available"
                )

    def nodes(self, session, timestamp=None):
        timestamp = now_ms() if timestamp is None else timestamp
        timeout = self.runtime.settings.node_offline_timeout_ms
        with self.lock:
            result = []
            for node in EXPECTED_NODE_IDS:
                state = self.status.get(node, {})
                received = state.get("last_seen_ms")
                online = received is not None and timestamp - received <= timeout
                ready = bool(
                    online
                    and self.runtime.mqtt.connected
                    and state.get("clock_synced")
                    and state.get("session_id") == session
                    and abs(timestamp - state.get("capture_end_ms", 0)) <= timeout
                )
                result.append({
                    **state,
                    "node_id": node,
                    "online": online,
                    "ready": ready,
                    "seconds_since_seen": (
                        None if received is None else round((timestamp - received) / 1000, 1)
                    ),
                })
            return result

    def room_snapshot(self):
        settings = self.runtime.settings
        return {
            "width_m": settings.room_width_m,
            "length_m": settings.room_length_m,
            "origin": "ESP01 top-left; X right, Y down; units metres",
            "cell_width_m": settings.grid_cell_width_m,
            "cell_length_m": settings.grid_cell_length_m,
            "nodes": {
                "ESP01": [0, 0],
                "ESP02": [settings.room_width_m, 0],
                "ESP03": [0, settings.room_length_m],
                "ESP04": [settings.room_width_m, settings.room_length_m],
            },
        }

    def ensure_idle(self):
        if self.database.measurements(active_only=True):
            raise ValueError("Wait for the active measurement to finish receiving")

    def start(self, session, zone_id, timestamp=None):
        timestamp = now_ms() if timestamp is None else timestamp
        with self.lock:
            self.ensure_idle()
            if not all(node["ready"] for node in self.nodes(session, timestamp)):
                raise ValueError("All four ESP32 need recent MQTT data, valid NTP and this session")
            zone = next(
                (zone for zone in self.runtime.localizer.grid_payload()["zones"]
                 if zone["zone_id"] == zone_id),
                None,
            )
            if zone is None:
                raise ValueError("Select a cell in the current grid")

            # Start at the button request time, with no countdown or second-boundary delay.
            measurement = {
                "measurement_id": uuid4().hex,
                "session": session,
                "zone": zone_id,
                "true_x_m": round(zone["x0_m"] + zone["width_m"] / 2, 6),
                "true_y_m": round(zone["y0_m"] + zone["length_m"] / 2, 6),
                "start_ms": timestamp,
                "end_ms": timestamp + self.duration_s * 1000,
                "room": self.room_snapshot(),
                "created_at_ms": timestamp,
            }
            self.database.create_measurement(measurement)
            return self.database.measurements(session, limit=1)[0]

    def tick(self, timestamp=None):
        timestamp = now_ms() if timestamp is None else timestamp
        with self.lock:
            self.database.rebuild_collection_vectors(timestamp, COLLECTION_VECTOR_WAIT_MS)
            changed = self.database.changed_measurements()
            pending = {
                measurement["measurement_id"]: measurement
                for measurement in self.database.measurements(active_only=True)
            }
            if changed:
                pending.update({
                    measurement["measurement_id"]: measurement
                    for measurement in self.database.measurements(measurement_ids=changed)
                })
            for measurement in pending.values():
                interrupted = bool(measurement["interrupted"])
                if timestamp < measurement["end_ms"]:
                    status = "Measuring"
                    interrupted |= not all(
                        node["ready"] for node in self.nodes(measurement["session"], timestamp)
                    )
                elif timestamp < measurement["end_ms"] + self.flush_ms:
                    status = "Receiving"
                else:
                    complete = measurement["complete_seconds"] == measurement["duration_s"] and not interrupted
                    status = "Completed" if complete else "Partial"
                self.database.update_measurement(
                    measurement["measurement_id"], status, interrupted
                )
            self.database.clear_changed_measurements(changed)

    def state(self, session):
        with self.lock:
            active = self.database.measurements(active_only=True)
            return {
                "server_now_ms": now_ms(),
                "duration_s": self.duration_s,
                "receive_wait_ms": self.flush_ms,
                "nodes": self.nodes(session),
                "measurements": self.database.measurements(session),
                "active_measurement": active[0] if active else None,
            }
