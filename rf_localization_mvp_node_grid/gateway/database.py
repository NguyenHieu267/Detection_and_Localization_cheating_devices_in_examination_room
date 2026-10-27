from __future__ import annotations

import json
import sqlite3
import threading
import time
from typing import Any


class Database:
    def __init__(self, path: str):
        self.path = path
        self.conn = sqlite3.connect(path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.lock = threading.Lock()
        self._init_schema()

    def _init_schema(self) -> None:
        with self.lock, self.conn:
            self.conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS epoch_device_evidence (
                    session_id TEXT NOT NULL,
                    epoch_id INTEGER NOT NULL,
                    device_token TEXT NOT NULL,
                    sniff_start_ms INTEGER NOT NULL,
                    sniff_end_ms INTEGER NOT NULL,
                    nodes_observed INTEGER NOT NULL,
                    node_rssi_json TEXT NOT NULL,
                    node_packet_count_json TEXT NOT NULL,
                    created_at_ms INTEGER NOT NULL,
                    PRIMARY KEY(session_id, epoch_id, device_token)
                );

                CREATE TABLE IF NOT EXISTS localization_updates (
                    session_id TEXT NOT NULL,
                    epoch_id INTEGER NOT NULL,
                    track_id TEXT NOT NULL,
                    zone_id TEXT NOT NULL,
                    x_m REAL NOT NULL,
                    y_m REAL NOT NULL,
                    confidence REAL NOT NULL,
                    nodes_used INTEGER NOT NULL,
                    rssi_vector_json TEXT NOT NULL,
                    created_at_ms INTEGER NOT NULL,
                    PRIMARY KEY(session_id, epoch_id, track_id)
                );

                CREATE TABLE IF NOT EXISTS node_status (
                    node_id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL,
                    last_ready_ms INTEGER NOT NULL,
                    last_seen_ms INTEGER
                );

                CREATE TABLE IF NOT EXISTS room_config (
                    id INTEGER PRIMARY KEY CHECK (id = 1),
                    width_m REAL NOT NULL,
                    length_m REAL NOT NULL,
                    grid_cols INTEGER,
                    grid_rows INTEGER,
                    grid_cell_width_m REAL,
                    grid_cell_length_m REAL,
                    updated_at_ms INTEGER NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_localization_session_epoch
                    ON localization_updates(session_id, epoch_id);
                """
            )

            # Lightweight migration for databases created by earlier MVP builds.
            node_columns = {row[1] for row in self.conn.execute("PRAGMA table_info(node_status)").fetchall()}
            if "last_seen_ms" not in node_columns:
                self.conn.execute("ALTER TABLE node_status ADD COLUMN last_seen_ms INTEGER")

            room_columns = {row[1] for row in self.conn.execute("PRAGMA table_info(room_config)").fetchall()}
            if "grid_cols" not in room_columns:
                self.conn.execute("ALTER TABLE room_config ADD COLUMN grid_cols INTEGER")
            if "grid_rows" not in room_columns:
                self.conn.execute("ALTER TABLE room_config ADD COLUMN grid_rows INTEGER")
            if "grid_cell_width_m" not in room_columns:
                self.conn.execute("ALTER TABLE room_config ADD COLUMN grid_cell_width_m REAL")
            if "grid_cell_length_m" not in room_columns:
                self.conn.execute("ALTER TABLE room_config ADD COLUMN grid_cell_length_m REAL")

    def close(self) -> None:
        with self.lock:
            self.conn.close()

    def upsert_node_ready(self, *, node_id: str, session_id: str, timestamp_ms: int) -> None:
        with self.lock, self.conn:
            self.conn.execute(
                """
                INSERT INTO node_status(node_id, session_id, last_ready_ms, last_seen_ms)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(node_id) DO UPDATE SET
                    session_id=excluded.session_id,
                    last_ready_ms=excluded.last_ready_ms,
                    last_seen_ms=excluded.last_seen_ms
                """,
                (node_id, session_id, timestamp_ms, timestamp_ms),
            )

    def upsert_node_seen(self, *, node_id: str, session_id: str, seen_ms: int) -> None:
        with self.lock, self.conn:
            self.conn.execute(
                """
                INSERT INTO node_status(node_id, session_id, last_ready_ms, last_seen_ms)
                VALUES (?, ?, 0, ?)
                ON CONFLICT(node_id) DO UPDATE SET
                    session_id=excluded.session_id,
                    last_seen_ms=excluded.last_seen_ms
                """,
                (node_id, session_id, seen_ms),
            )

    def get_node_status(self) -> list[dict[str, Any]]:
        with self.lock:
            rows = self.conn.execute(
                "SELECT node_id, session_id, last_ready_ms, last_seen_ms FROM node_status"
            ).fetchall()
        return [dict(row) for row in rows]

    def save_room_config(
        self,
        width_m: float,
        length_m: float,
        grid_cell_width_m: float,
        grid_cell_length_m: float,
    ) -> None:
        now = int(time.time() * 1000)
        with self.lock, self.conn:
            self.conn.execute(
                """
                INSERT INTO room_config(
                    id, width_m, length_m, grid_cols, grid_rows,
                    grid_cell_width_m, grid_cell_length_m, updated_at_ms
                )
                VALUES (1, ?, ?, NULL, NULL, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    width_m=excluded.width_m,
                    length_m=excluded.length_m,
                    grid_cell_width_m=excluded.grid_cell_width_m,
                    grid_cell_length_m=excluded.grid_cell_length_m,
                    updated_at_ms=excluded.updated_at_ms
                """,
                (width_m, length_m, grid_cell_width_m, grid_cell_length_m, now),
            )

    def load_room_config(self) -> dict[str, float | int | None] | None:
        with self.lock:
            row = self.conn.execute(
                """
                SELECT width_m, length_m, grid_cols, grid_rows,
                       grid_cell_width_m, grid_cell_length_m
                FROM room_config WHERE id=1
                """
            ).fetchone()
        if not row:
            return None
        data = dict(row)
        # Backward compatibility: old DBs stored only row/column counts.
        if data.get("grid_cell_width_m") is None and data.get("grid_cols"):
            data["grid_cell_width_m"] = float(data["width_m"]) / int(data["grid_cols"])
        if data.get("grid_cell_length_m") is None and data.get("grid_rows"):
            data["grid_cell_length_m"] = float(data["length_m"]) / int(data["grid_rows"])
        return data

    def upsert_epoch_device_evidence(
        self,
        *,
        session_id: str,
        epoch_id: int,
        device_token: str,
        nodes_observed: int,
        sniff_start_ms: int,
        sniff_end_ms: int,
        node_rssi: dict[str, float],
        node_packet_count: dict[str, int],
        created_at_ms: int,
    ) -> None:
        with self.lock, self.conn:
            self.conn.execute(
                """
                INSERT INTO epoch_device_evidence(
                    session_id, epoch_id, device_token,
                    sniff_start_ms, sniff_end_ms, nodes_observed,
                    node_rssi_json, node_packet_count_json, created_at_ms
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(session_id, epoch_id, device_token) DO UPDATE SET
                    sniff_start_ms=excluded.sniff_start_ms,
                    sniff_end_ms=excluded.sniff_end_ms,
                    nodes_observed=excluded.nodes_observed,
                    node_rssi_json=excluded.node_rssi_json,
                    node_packet_count_json=excluded.node_packet_count_json,
                    created_at_ms=excluded.created_at_ms
                """,
                (
                    session_id,
                    epoch_id,
                    device_token,
                    sniff_start_ms,
                    sniff_end_ms,
                    nodes_observed,
                    json.dumps(node_rssi, separators=(",", ":")),
                    json.dumps(node_packet_count, separators=(",", ":")),
                    created_at_ms,
                ),
            )

    def upsert_localization_update(self, **kwargs: Any) -> None:
        with self.lock, self.conn:
            self.conn.execute(
                """
                INSERT INTO localization_updates(
                    session_id, epoch_id, track_id, zone_id,
                    x_m, y_m, confidence, nodes_used,
                    rssi_vector_json, created_at_ms
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(session_id, epoch_id, track_id) DO UPDATE SET
                    zone_id=excluded.zone_id,
                    x_m=excluded.x_m,
                    y_m=excluded.y_m,
                    confidence=excluded.confidence,
                    nodes_used=excluded.nodes_used,
                    rssi_vector_json=excluded.rssi_vector_json,
                    created_at_ms=excluded.created_at_ms
                """,
                (
                    kwargs["session_id"], kwargs["epoch_id"], kwargs["track_id"], kwargs["zone_id"],
                    kwargs["x_m"], kwargs["y_m"], kwargs["confidence"], kwargs["nodes_used"],
                    json.dumps(kwargs["rssi_vector"], separators=(",", ":")), kwargs["created_at_ms"],
                ),
            )

    def latest_localization(self, limit: int = 50) -> list[dict[str, Any]]:
        with self.lock:
            rows = self.conn.execute(
                """
                SELECT session_id, epoch_id, track_id, zone_id,
                       x_m, y_m, confidence, nodes_used,
                       rssi_vector_json, created_at_ms
                FROM localization_updates
                ORDER BY created_at_ms DESC, epoch_id DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()

        result = []
        for row in rows:
            item = dict(row)
            item["rssi_vector_dbm"] = json.loads(item.pop("rssi_vector_json"))
            item["timestamp_ms"] = item.pop("created_at_ms")
            result.append(item)
        return result
