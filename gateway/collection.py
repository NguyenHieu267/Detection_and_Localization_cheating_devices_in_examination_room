"""Collection mode: one CSV row for each original ESP32 sample, without aggregation."""
from __future__ import annotations

import json
import threading
import time

from config import EXPECTED_NODE_IDS, COLLECTION_DURATION_S, COLLECTION_RECEIVE_WAIT_MS
from csv_store import DATASET_COLUMNS, utc_timestamp


def now_ms():
    return int(time.time() * 1000)


class DatasetCollection:
    def __init__(self, runtime):
        self.runtime = runtime
        self.storage = runtime.storage
        self.lock = threading.RLock()
        self.status = {}
        self.flush_ms = COLLECTION_RECEIVE_WAIT_MS
        self.duration_s = COLLECTION_DURATION_S
        self.active = None
        self.seen = set()
        self.last_saved_ms = 0

    def on_raw(self, report):
        """Write every selected frame once, using its own ESP32 capture timestamp."""
        received = now_ms()
        with self.lock:
            old = self.status.get(report.node_id)
            info = {
                "session_id": report.session_id, "node_id": report.node_id,
                "last_seen_ms": received, "clock_synced": report.clock_synced,
                "capture_end_ms": report.sniff_end_unix_ms,
                "queue_drop_count": report.queue_drop_count,
                "batch_drop_count": report.batch_drop_count,
            }
            if old is None or info["capture_end_ms"] >= old["capture_end_ms"]:
                self.status[report.node_id] = info
            run = self.active
            if (run is None or not report.clock_synced or report.session_id != run["session"]
                    or received >= run["end_ms"] + self.flush_ms):
                return
            key = (report.node_id, report.epoch_id, report.chunk_index)
            if key in self.seen:
                return
            if old and (info["queue_drop_count"] > old["queue_drop_count"] or
                        info["batch_drop_count"] > old["batch_drop_count"]):
                run["interrupted"] = True
            tokens = {sample.device_token for sample in report.observations}
            node_index = EXPECTED_NODE_IDS.index(report.node_id)
            rows = []
            for sample in report.observations:
                if (run["start_ms"] <= sample.capture_unix_ms < run["end_ms"] and
                        self.runtime.should_localize(sample.device_token, len(tokens))):
                    vector = [None] * 4
                    vector[node_index] = sample.rssi_dbm
                    rows.append(dict(zip(DATASET_COLUMNS, [
                        run["session"], utc_timestamp(sample.capture_unix_ms), run["run_label"],
                        *("NULL" if value is None else value for value in vector),
                        json.dumps(vector, separators=(",", ":")),
                    ])))
            if rows:
                self.storage.append_samples(rows)
                run["sample_counts"][report.node_id] += len(rows)
            self.seen.add(key)

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
        if self.active is not None:
            raise ValueError("Wait for the active measurement to finish receiving")

    def start(self, session, zone_id, timestamp=None):
        timestamp = now_ms() if timestamp is None else timestamp
        with self.lock:
            self.ensure_idle()
            if not all(node["ready"] for node in self.nodes(session, timestamp)):
                raise ValueError("All four ESP32 need recent MQTT data, valid NTP and this session")
            zone = next((zone for zone in self.runtime.localizer.grid_payload()["zones"]
                         if zone["zone_id"] == zone_id), None)
            if zone is None:
                raise ValueError("Select a cell in the current grid")
            number = self.storage.next_run_number(session, zone_id)
            label = f"{zone_id}_run{number:02d}"
            run = {
                "measurement_id": f"{session}:{label}", "session": session,
                "zone": zone_id, "run_number": number, "run_label": label,
                "true_x_m": round(zone["x0_m"] + zone["width_m"] / 2, 6),
                "true_y_m": round(zone["y0_m"] + zone["length_m"] / 2, 6),
                "start_ms": timestamp, "end_ms": timestamp + self.duration_s * 1000,
                "duration_s": self.duration_s, "room": self.room_snapshot(),
                "status": "Measuring", "interrupted": False,
                "sample_counts": dict.fromkeys(EXPECTED_NODE_IDS, 0),
            }
            self.storage.save_run(run)
            self.active = run
            self.seen.clear()
            self.last_saved_ms = timestamp
            return dict(run)

    def tick(self, timestamp=None, stopping=False):
        timestamp = now_ms() if timestamp is None else timestamp
        with self.lock:
            run = self.active
            if run is None:
                return
            previous = run["status"]
            if stopping:
                run.update(status="Partial", interrupted=True)
            elif timestamp < run["end_ms"]:
                run["interrupted"] |= not all(
                    node["ready"] for node in self.nodes(run["session"], timestamp)
                )
            elif timestamp < run["end_ms"] + self.flush_ms:
                run["status"] = "Receiving"
            else:
                complete = all(run["sample_counts"].values()) and not run["interrupted"]
                run["status"] = "Completed" if complete else "Partial"
            if stopping or previous != run["status"] or timestamp - self.last_saved_ms >= 1000:
                self.storage.save_run(run)
                self.last_saved_ms = timestamp
            if run["status"] in {"Completed", "Partial"}:
                self.active = None
                self.seen.clear()

    def state(self, session):
        with self.lock:
            return {
                "server_now_ms": now_ms(), "duration_s": self.duration_s,
                "receive_wait_ms": self.flush_ms, "nodes": self.nodes(session),
                "measurements": self.storage.measurements(session),
                "active_measurement": dict(self.active) if self.active else None,
            }
