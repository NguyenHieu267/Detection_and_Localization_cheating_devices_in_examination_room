from __future__ import annotations

import json
import csv
import hashlib
import io
import sqlite3
import statistics
import threading
import time
from pathlib import Path
from typing import Any

from config import resolve_database_path
from config import EXPECTED_NODE_IDS


class Database:
    def __init__(self, path: str):
        self.path = resolve_database_path(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path, check_same_thread=False)
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

                CREATE TABLE IF NOT EXISTS raw_chunks (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT NOT NULL,
                    epoch_id INTEGER NOT NULL,
                    node_id TEXT NOT NULL,
                    chunk_index INTEGER NOT NULL,
                    payload_json TEXT NOT NULL,
                    received_at_ms INTEGER NOT NULL,
                    UNIQUE(session_id, epoch_id, node_id, chunk_index)
                );
                CREATE TABLE IF NOT EXISTS epoch_quality (
                    session_id TEXT NOT NULL,
                    epoch_id INTEGER NOT NULL,
                    quality_json TEXT NOT NULL,
                    PRIMARY KEY(session_id, epoch_id)
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
            # Nullable columns preserve old records and never touch raw_chunks.
            position_columns = {
                "raw_x_m": "REAL", "raw_y_m": "REAL", "raw_zone_id": "TEXT",
                "filtered_x_m": "REAL", "filtered_y_m": "REAL", "filtered_zone_id": "TEXT",
                "position_filter_mode": "TEXT", "position_ema_alpha": "REAL",
            }
            existing = {row[1] for row in self.conn.execute("PRAGMA table_info(localization_updates)")}
            for name, sql_type in position_columns.items():
                if name not in existing:
                    self.conn.execute(f"ALTER TABLE localization_updates ADD COLUMN {name} {sql_type}")

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

    def init_collection_schema(self):
        """Add derived vectors beside original frames; migrate by adding work, never deleting raw."""
        with self.lock, self.conn:
            self.conn.executescript("""
                CREATE TABLE IF NOT EXISTS measurements (
                    measurement_id TEXT PRIMARY KEY, session TEXT NOT NULL, zone TEXT NOT NULL,
                    run_number INTEGER NOT NULL, true_x_m REAL NOT NULL, true_y_m REAL NOT NULL,
                    start_ms INTEGER NOT NULL, end_ms INTEGER NOT NULL,
                    duration_s INTEGER NOT NULL DEFAULT 30 CHECK(duration_s>0),
                    status TEXT NOT NULL, interrupted INTEGER NOT NULL DEFAULT 0,
                    room_json TEXT NOT NULL, created_at_ms INTEGER NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_measurement_time
                    ON measurements(session,start_ms,end_ms);
                CREATE TABLE IF NOT EXISTS received_messages (
                    session TEXT NOT NULL, node_id TEXT NOT NULL, message_id TEXT NOT NULL,
                    payload_hash TEXT NOT NULL, received_ms INTEGER NOT NULL,
                    PRIMARY KEY(session,node_id,message_id)
                );
                CREATE TABLE IF NOT EXISTS raw_rssi_samples (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, session TEXT NOT NULL,
                    measurement_id TEXT REFERENCES measurements(measurement_id),
                    timestamp_ms INTEGER NOT NULL, node_id TEXT NOT NULL,
                    rssi_dbm INTEGER NOT NULL, message_id TEXT NOT NULL,
                    sample_index INTEGER NOT NULL, channel INTEGER,
                    frame_type TEXT, frame_subtype INTEGER, gateway_received_ms INTEGER NOT NULL,
                    UNIQUE(session,node_id,message_id,sample_index)
                );
                CREATE INDEX IF NOT EXISTS idx_samples_time
                    ON raw_rssi_samples(session,timestamp_ms);
                CREATE INDEX IF NOT EXISTS idx_samples_measurement
                    ON raw_rssi_samples(measurement_id,timestamp_ms);
                CREATE TABLE IF NOT EXISTS changed_measurements (
                    measurement_id TEXT PRIMARY KEY
                );
                CREATE TABLE IF NOT EXISTS rssi_vectors (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, session TEXT NOT NULL,
                    timestamp INTEGER NOT NULL, zone TEXT,
                    esp01_rssi REAL, esp02_rssi REAL, esp03_rssi REAL, esp04_rssi REAL,
                    vector_json TEXT NOT NULL, measurement_id TEXT,
                    sample_counts_json TEXT NOT NULL, quality_status TEXT NOT NULL
                );
                CREATE UNIQUE INDEX IF NOT EXISTS idx_vector_key
                    ON rssi_vectors(session,COALESCE(measurement_id,''),timestamp);
                CREATE INDEX IF NOT EXISTS idx_vector_measurement
                    ON rssi_vectors(measurement_id,timestamp);
                CREATE TABLE IF NOT EXISTS vector_jobs (
                    session TEXT NOT NULL, timestamp INTEGER NOT NULL,
                    PRIMARY KEY(session,timestamp)
                );
                CREATE TABLE IF NOT EXISTS collection_migrations (name TEXT PRIMARY KEY);
            """)
            migrated = self.conn.execute(
                "SELECT 1 FROM collection_migrations WHERE name='vectors_from_raw_v1'"
            ).fetchone()
            if not migrated:
                # Recover vectors for previously collected frames, including empty measured seconds.
                self.conn.execute("""
                    INSERT OR IGNORE INTO vector_jobs
                    SELECT DISTINCT session,(timestamp_ms/1000)*1000 FROM raw_rssi_samples
                """)
                for measurement in self.conn.execute("SELECT * FROM measurements").fetchall():
                    self._prepare_measurement_vectors(dict(measurement))
                self.conn.execute(
                    "INSERT INTO collection_migrations VALUES('vectors_from_raw_v1')"
                )

        self._upgrade_measurement_duration()

    def _upgrade_measurement_duration(self):
        # Older databases restricted runs to 30 seconds. Preserve rows and foreign keys.
        with self.lock:
            sql = self.conn.execute(
                "SELECT sql FROM sqlite_master WHERE name='measurements'"
            ).fetchone()[0]
            if "CHECK(duration_s=30)" not in sql:
                return
            foreign_keys = self.conn.execute("PRAGMA foreign_keys").fetchone()[0]
            self.conn.execute("PRAGMA foreign_keys=OFF")
            try:
                with self.conn:
                    self.conn.execute("BEGIN")
                    self.conn.execute(sql.replace("measurements", "measurements_new", 1)
                                      .replace("CHECK(duration_s=30)", "CHECK(duration_s>0)"))
                    self.conn.execute("INSERT INTO measurements_new SELECT * FROM measurements")
                    self.conn.execute("DROP TABLE measurements")
                    self.conn.execute("ALTER TABLE measurements_new RENAME TO measurements")
                    self.conn.execute("CREATE INDEX idx_measurement_time "
                                      "ON measurements(session,start_ms,end_ms)")
                    if self.conn.execute("PRAGMA foreign_key_check").fetchone():
                        raise sqlite3.IntegrityError("Measurement migration foreign key mismatch")
            finally:
                self.conn.execute(f"PRAGMA foreign_keys={foreign_keys}")

    def store_collection_samples(self, report, received_ms):
        """Persist every original frame and its capture-time label in one transaction."""
        payload_hash = hashlib.sha256(report.model_dump_json().encode()).hexdigest()
        key = (report.session_id, report.node_id, report.message_id)
        with self.lock, self.conn:
            old = self.conn.execute(
                "SELECT payload_hash FROM received_messages "
                "WHERE session=? AND node_id=? AND message_id=?",
                key,
            ).fetchone()
            if old:
                if old[0] != payload_hash:
                    raise ValueError("message_id reused with different payload")
                return False

            self.conn.execute(
                "INSERT INTO received_messages VALUES(?,?,?,?,?)",
                (*key, payload_hash, received_ms),
            )
            for index, sample in enumerate(report.samples):
                measurement = self.conn.execute(
                    "SELECT measurement_id FROM measurements "
                    "WHERE session=? AND start_ms<=? AND end_ms>?",
                    (report.session_id, sample.timestamp_ms, sample.timestamp_ms),
                ).fetchone()
                measurement_id = measurement[0] if measurement else None
                self.conn.execute("""
                    INSERT INTO raw_rssi_samples(
                        session,measurement_id,timestamp_ms,node_id,rssi_dbm,message_id,
                        sample_index,channel,frame_type,frame_subtype,gateway_received_ms
                    ) VALUES(?,?,?,?,?,?,?,?,?,?,?)
                """, (
                    report.session_id, measurement_id, sample.timestamp_ms, report.node_id,
                    sample.rssi_dbm, report.message_id, index, sample.channel,
                    sample.frame_type, sample.frame_subtype, received_ms,
                ))
                if measurement_id:
                    self.conn.execute(
                        "INSERT OR IGNORE INTO changed_measurements VALUES(?)",
                        (measurement_id,),
                    )
            self.conn.executemany(
                "INSERT OR IGNORE INTO vector_jobs VALUES(?,?)",
                ((report.session_id, start) for start in {
                    sample.timestamp_ms // 1000 * 1000 for sample in report.samples
                }),
            )
        return True

    def create_measurement(self, measurement):
        item = measurement
        with self.lock, self.conn:
            run = self.conn.execute(
                "SELECT COALESCE(MAX(run_number),0)+1 FROM measurements "
                "WHERE session=? AND zone=?",
                (item["session"], item["zone"]),
            ).fetchone()[0]
            self.conn.execute("""
                INSERT INTO measurements(
                    measurement_id,session,zone,run_number,true_x_m,true_y_m,
                    start_ms,end_ms,duration_s,status,room_json,created_at_ms
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
            """, (
                item["measurement_id"], item["session"], item["zone"], run,
                item["true_x_m"], item["true_y_m"], item["start_ms"], item["end_ms"],
                (item["end_ms"] - item["start_ms"]) // 1000,
                "Measuring", json.dumps(item["room"]), item["created_at_ms"],
            ))
            self._prepare_measurement_vectors(item)

    def _prepare_measurement_vectors(self, measurement):
        # Start remains immediate: a non-aligned 30 s run may overlap 31 Unix windows.
        for start in range(measurement["start_ms"] // 1000 * 1000, measurement["end_ms"], 1000):
            self.conn.execute("INSERT OR IGNORE INTO vector_jobs VALUES(?,?)", (
                measurement["session"], start,
            ))
            self._write_vector(
                measurement["session"], start, measurement["measurement_id"],
                measurement["zone"], [None] * 4, {node: 0 for node in EXPECTED_NODE_IDS},
                insert_only=True,
            )

    def _write_vector(self, session, start, measurement_id, zone, values, counts, insert_only=False):
        # These derived rows never overwrite raw_rssi_samples.
        conflict = "DO NOTHING" if insert_only else """DO UPDATE SET
            zone=excluded.zone,esp01_rssi=excluded.esp01_rssi,esp02_rssi=excluded.esp02_rssi,
            esp03_rssi=excluded.esp03_rssi,esp04_rssi=excluded.esp04_rssi,
            vector_json=excluded.vector_json,sample_counts_json=excluded.sample_counts_json,
            quality_status=excluded.quality_status"""
        self.conn.execute("""
            INSERT INTO rssi_vectors(
                session,timestamp,zone,esp01_rssi,esp02_rssi,esp03_rssi,esp04_rssi,
                vector_json,measurement_id,sample_counts_json,quality_status
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT """ + conflict, (
            session, start, zone, *values, json.dumps(values, separators=(",", ":")),
            measurement_id, json.dumps(counts),
            "complete" if all(counts.values()) else "partial",
        ))

    def rebuild_collection_vectors(self, timestamp, wait_ms=400):
        """Capture-time windows, fixed node order, and independent Ground Truth for each run."""
        with self.lock, self.conn:
            jobs = self.conn.execute(
                "SELECT session,timestamp FROM vector_jobs "
                "WHERE timestamp+1000+?<=? ORDER BY timestamp LIMIT 200",
                (wait_ms, timestamp),
            ).fetchall()
            for job in jobs:
                session, start = job
                rows = self.conn.execute("""
                    SELECT node_id,rssi_dbm,measurement_id FROM raw_rssi_samples
                    WHERE session=? AND timestamp_ms>=? AND timestamp_ms<?
                """, (session, start, start + 1000)).fetchall()
                measurements = self.conn.execute("""
                    SELECT measurement_id,zone FROM measurements
                    WHERE session=? AND start_ms<? AND end_ms>?
                """, (session, start + 1000, start)).fetchall()
                groups = [(None, None, rows)] if rows else []
                groups.extend(
                    (measurement[0], measurement[1], [row for row in rows
                     if row["measurement_id"] == measurement[0]])
                    for measurement in measurements
                )
                for measurement_id, zone, samples in groups:
                    by_node = {node: [] for node in EXPECTED_NODE_IDS}
                    for sample in samples:
                        by_node[sample["node_id"]].append(sample["rssi_dbm"])
                    values = [statistics.median(by_node[node]) if by_node[node] else None
                              for node in EXPECTED_NODE_IDS]
                    values = [int(value) if value is not None and value == int(value) else value
                              for value in values]
                    counts = {node: len(by_node[node]) for node in EXPECTED_NODE_IDS}
                    self._write_vector(session, start, measurement_id, zone, values, counts)
                self.conn.execute(
                    "DELETE FROM vector_jobs WHERE session=? AND timestamp=?", (session, start)
                )
        return len(jobs)

    def latest_vectors(self, session, limit=50, measurement_id=None):
        suffix = " AND measurement_id=?" if measurement_id else ""
        args = (session, measurement_id, limit) if measurement_id else (session, limit)
        with self.lock:
            rows = self.conn.execute("""
                SELECT * FROM rssi_vectors WHERE session=? AND measurement_id IS NOT NULL
            """ + suffix + " ORDER BY timestamp DESC,id DESC LIMIT ?", args).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            item["vector"] = json.loads(item.pop("vector_json"))
            item["sample_counts"] = json.loads(item.pop("sample_counts_json"))
            result.append(item)
        return result

    def export_collection_vectors(self, session, measurement_id=None):
        """The main dataset CSV has exactly the eight requested columns."""
        buffer = io.StringIO()
        writer = csv.writer(buffer)
        writer.writerow([
            "session", "timestamp", "Zone", "ESP01 - RSSI", "ESP02 - RSSI",
            "ESP03 - RSSI", "ESP04 - RSSI", "Vector RSSI",
        ])
        yield buffer.getvalue()
        suffix = " AND measurement_id=?" if measurement_id else ""
        after = 0
        with self.lock:
            upper = self.conn.execute(
                "SELECT COALESCE(MAX(id),0) FROM rssi_vectors"
            ).fetchone()[0]
        while after < upper:
            args = (session, after, upper, measurement_id) if measurement_id else (
                session, after, upper
            )
            with self.lock:
                rows = self.conn.execute("""
                    SELECT id,session,timestamp,zone,esp01_rssi,esp02_rssi,
                           esp03_rssi,esp04_rssi,vector_json
                    FROM rssi_vectors WHERE session=? AND id>? AND id<=?
                    AND measurement_id IS NOT NULL
                """ + suffix + " ORDER BY id LIMIT 500", args).fetchall()
            if not rows:
                break
            buffer.seek(0)
            buffer.truncate()
            for row in rows:
                writer.writerow(
                    "NULL" if value is None else (
                        int(value) if isinstance(value, float) and value.is_integer() else value
                    )
                    for value in tuple(row)[1:]
                )
            yield buffer.getvalue()
            after = rows[-1]["id"]

    def measurements(self, session=None, active_only=False, limit=100, measurement_ids=None):
        clauses, args = [], []
        if session is not None:
            clauses.append("session=?")
            args.append(session)
        if active_only:
            clauses.append("status NOT IN ('Completed','Partial')")
        if measurement_ids:
            clauses.append("measurement_id IN (" + ",".join("?" for _ in measurement_ids) + ")")
            args.extend(measurement_ids)
        where = " WHERE " + " AND ".join(clauses) if clauses else ""

        with self.lock:
            rows = self.conn.execute(
                "SELECT * FROM measurements" + where + " ORDER BY start_ms DESC LIMIT ?",
                (*args, limit),
            ).fetchall()
            result = []
            for row in rows:
                item = dict(row)
                item["room"] = json.loads(item.pop("room_json"))
                counts = {node: 0 for node in EXPECTED_NODE_IDS}
                coverage = {}
                # Counts indicate missing data only; RSSI values are never aggregated or changed.
                buckets = self.conn.execute("""
                    SELECT node_id, (timestamp_ms-?)/1000 AS second_index, COUNT(*) AS total
                    FROM raw_rssi_samples WHERE measurement_id=?
                    GROUP BY node_id,second_index
                """, (item["start_ms"], item["measurement_id"])).fetchall()
                for bucket in buckets:
                    counts[bucket["node_id"]] += bucket["total"]
                    coverage.setdefault(bucket["second_index"], set()).add(bucket["node_id"])
                item["sample_counts"] = counts
                item["complete_seconds"] = sum(
                    nodes == set(EXPECTED_NODE_IDS) for nodes in coverage.values()
                )
                result.append(item)
        return result

    def update_measurement(self, measurement_id, status, interrupted):
        with self.lock, self.conn:
            self.conn.execute(
                "UPDATE measurements SET status=?,interrupted=? WHERE measurement_id=?",
                (status, int(interrupted), measurement_id),
            )

    def changed_measurements(self):
        with self.lock:
            return [
                row[0] for row in self.conn.execute(
                    "SELECT measurement_id FROM changed_measurements"
                )
            ]

    def clear_changed_measurements(self, ids):
        with self.lock, self.conn:
            self.conn.executemany(
                "DELETE FROM changed_measurements WHERE measurement_id=?",
                ((measurement_id,) for measurement_id in ids),
            )

    def latest_samples(self, session, limit=50, measurement_id=None):
        suffix = " AND s.measurement_id=?" if measurement_id else ""
        args = (session, measurement_id, limit) if measurement_id else (session, limit)
        with self.lock:
            rows = self.conn.execute("""
                SELECT s.*, m.zone, m.true_x_m, m.true_y_m, m.run_number
                FROM raw_rssi_samples s
                LEFT JOIN measurements m ON s.measurement_id=m.measurement_id
                WHERE s.session=?
            """ + suffix + " ORDER BY s.timestamp_ms DESC,s.id DESC LIMIT ?", args).fetchall()
        return [dict(row) for row in rows]

    def export_collection_raw(self, session, measurement_id=None):
        """Export exact raw values plus their Ground Truth, in bounded pages."""
        headers = [
            "id", "session", "measurement_id", "timestamp_ms", "node_id", "rssi_dbm",
            "message_id", "sample_index", "channel", "frame_type", "frame_subtype",
            "gateway_received_ms", "zone", "true_x_m", "true_y_m", "run_number", "room_metadata",
        ]
        buffer = io.StringIO()
        writer = csv.writer(buffer)
        writer.writerow(headers)
        yield buffer.getvalue()
        after = 0
        with self.lock:
            upper = self.conn.execute(
                "SELECT COALESCE(MAX(id),0) FROM raw_rssi_samples"
            ).fetchone()[0]
        while after < upper:
            suffix = " AND s.measurement_id=?" if measurement_id else ""
            args = (session, after, upper, measurement_id) if measurement_id else (
                session, after, upper
            )
            with self.lock:
                rows = self.conn.execute("""
                    SELECT s.*, m.zone, m.true_x_m, m.true_y_m, m.run_number, m.room_json
                    FROM raw_rssi_samples s
                    LEFT JOIN measurements m ON s.measurement_id=m.measurement_id
                    WHERE s.session=? AND s.id>? AND s.id<=?
                """ + suffix + " ORDER BY s.id LIMIT 500", args).fetchall()
            if not rows:
                break
            buffer.seek(0)
            buffer.truncate()
            for row in rows:
                writer.writerow(tuple(row))
            yield buffer.getvalue()
            after = rows[-1]["id"]


    def store_raw_chunk(self, report) -> None:
        with self.lock, self.conn:
            existing = self.conn.execute(
                "SELECT payload_json FROM raw_chunks WHERE session_id=? AND epoch_id=? AND node_id=? AND chunk_index=?",
                (report.session_id, report.epoch_id, report.node_id, report.chunk_index)).fetchone()
            if existing and json.loads(existing[0]) != report.model_dump():
                raise ValueError("conflicting persisted chunk")
            self.conn.execute(
                "INSERT OR IGNORE INTO raw_chunks(session_id,epoch_id,node_id,chunk_index,payload_json,received_at_ms) VALUES(?,?,?,?,?,?)",
                (report.session_id, report.epoch_id, report.node_id, report.chunk_index,
                 report.model_dump_json(), int(time.time() * 1000)))

    def store_quality(self, quality: dict) -> None:
        with self.lock, self.conn:
            self.conn.execute("INSERT OR IGNORE INTO epoch_quality VALUES(?,?,?)",
                              (quality["session_id"], quality["epoch_id"], json.dumps(quality)))

    def epoch_closed(self, session_id: str, epoch_id: int) -> bool:
        with self.lock:
            return self.conn.execute("SELECT 1 FROM epoch_quality WHERE session_id=? AND epoch_id=?",
                                     (session_id, epoch_id)).fetchone() is not None

    def latest_quality(self, limit: int = 50) -> list[dict]:
        with self.lock:
            rows = self.conn.execute("SELECT quality_json FROM epoch_quality ORDER BY rowid DESC LIMIT ?", (limit,)).fetchall()
        return [json.loads(row[0]) for row in rows]

    def export_raw(self, session_id: str):
        """Bounded pages; JSONL keeps exact raw samples and per-chunk diagnostics."""
        after = 0
        with self.lock:
            upper = self.conn.execute("SELECT COALESCE(MAX(id),0) FROM raw_chunks").fetchone()[0]
        while after < upper:
            with self.lock:
                rows = self.conn.execute(
                    "SELECT id,payload_json FROM raw_chunks WHERE session_id=? AND id>? AND id<=? ORDER BY id LIMIT 100",
                    (session_id, after, upper)).fetchall()
            if not rows:
                break
            for row in rows:
                yield row["payload_json"] + "\n"
            after = rows[-1]["id"]

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
                    rssi_vector_json, created_at_ms,
                    raw_x_m, raw_y_m, raw_zone_id, filtered_x_m, filtered_y_m, filtered_zone_id,
                    position_filter_mode, position_ema_alpha
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(session_id, epoch_id, track_id) DO UPDATE SET
                    zone_id=excluded.zone_id,
                    x_m=excluded.x_m,
                    y_m=excluded.y_m,
                    confidence=excluded.confidence,
                    nodes_used=excluded.nodes_used,
                    rssi_vector_json=excluded.rssi_vector_json,
                    created_at_ms=excluded.created_at_ms,
                    raw_x_m=excluded.raw_x_m,
                    raw_y_m=excluded.raw_y_m,
                    raw_zone_id=excluded.raw_zone_id,
                    filtered_x_m=excluded.filtered_x_m,
                    filtered_y_m=excluded.filtered_y_m,
                    filtered_zone_id=excluded.filtered_zone_id,
                    position_filter_mode=excluded.position_filter_mode,
                    position_ema_alpha=excluded.position_ema_alpha
                """,
                (
                    kwargs["session_id"], kwargs["epoch_id"], kwargs["track_id"], kwargs["zone_id"],
                    kwargs["x_m"], kwargs["y_m"], kwargs["confidence"], kwargs["nodes_used"],
                    json.dumps(kwargs["rssi_vector"], separators=(",", ":")), kwargs["created_at_ms"],
                    kwargs.get("raw_x_m", kwargs["x_m"]), kwargs.get("raw_y_m", kwargs["y_m"]),
                    kwargs.get("raw_zone_id", kwargs["zone_id"]),
                    kwargs.get("filtered_x_m", kwargs["x_m"]), kwargs.get("filtered_y_m", kwargs["y_m"]),
                    kwargs.get("filtered_zone_id", kwargs["zone_id"]),
                    kwargs.get("position_filter_mode", "off"), kwargs.get("position_ema_alpha"),
                ),
            )

    def latest_localization(self, limit: int = 50) -> list[dict[str, Any]]:
        with self.lock:
            rows = self.conn.execute(
                """
                SELECT session_id, epoch_id, track_id, zone_id,
                       x_m, y_m, confidence, nodes_used,
                       rssi_vector_json, created_at_ms,
                       COALESCE(raw_x_m, x_m) AS raw_x_m,
                       COALESCE(raw_y_m, y_m) AS raw_y_m,
                       COALESCE(raw_zone_id, zone_id) AS raw_zone_id,
                       COALESCE(filtered_x_m, x_m) AS filtered_x_m,
                       COALESCE(filtered_y_m, y_m) AS filtered_y_m,
                       COALESCE(filtered_zone_id, zone_id) AS filtered_zone_id,
                       COALESCE(position_filter_mode, 'off') AS position_filter_mode,
                       position_ema_alpha
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
            with self.lock:
                quality_row = self.conn.execute("SELECT quality_json FROM epoch_quality WHERE session_id=? AND epoch_id=?",
                    (item["session_id"], item["epoch_id"])).fetchone()
            if quality_row:
                q = json.loads(quality_row[0])
                item["sync_status"] = q["tokens"].get(item["track_id"], q["status"])
                item["overlap_ms"] = q["overlap_ms"]
                item["queue_drop_count"] = q["queue_drop_count"]
                item["node_packet_count"] = {n: s["packet_count"] for n, s in q["node_stats"].get(item["track_id"], {}).items()}
            result.append(item)
        return result
