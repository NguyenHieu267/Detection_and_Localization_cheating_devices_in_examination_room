"""Run with .venv/Scripts/python.exe scripts/check_position_filter.py; uses temporary DBs only."""
from __future__ import annotations

from dataclasses import replace
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "gateway"))
# Importing main creates its runtime: isolate it from the measurement database.
os.environ["DATABASE_PATH"] = ":memory:"
os.environ["POSITION_FILTER_MODE"] = "ema"
os.environ["POSITION_EMA_ALPHA"] = "0.35"
os.environ["POSITION_EMA_RESET_GAP"] = "3"

from config import Settings, build_settings
from database import Database
import main
from models import LocalizationResult, ObservationItem, RawEpochObservation
from position_filter import PositionEMA


class PositionFilterChecks(unittest.TestCase):
    def test_ema_and_independent_states(self):
        ema = PositionEMA()
        self.assertEqual(ema.update("s", "a", 100, 2, 4), (2, 4))
        x, y = ema.update("s", "a", 101, 4, 6)
        self.assertAlmostEqual(x, 2.7)
        self.assertAlmostEqual(y, 4.7)
        self.assertEqual(ema.update("s", "b", 101, 8, 10), (8, 10))
        self.assertEqual(ema.update("other", "a", 101, 6, 8), (6, 8))
        state = dict(ema.states)
        self.assertIsNone(ema.update("s", "a", 100, 99, 99))
        self.assertIsNone(ema.update("s", "a", 101, 99, 99))
        self.assertEqual(ema.states, state)
        self.assertEqual(ema.update("s", "a", 105, 9, 11), (9, 11))
        ema.reset()
        self.assertFalse(ema.states)

    def test_configuration(self):
        base = Settings(position_filter_mode="off", position_ema_alpha=0.5, position_ema_reset_gap=5)
        room = build_settings(10, 20, base=base)
        self.assertEqual((room.position_filter_mode, room.position_ema_alpha, room.position_ema_reset_gap),
                         ("off", 0.5, 5))
        for values in ({"position_filter_mode": "unknown"}, {"position_ema_alpha": 0},
                       {"position_ema_alpha": 1.1}, {"position_ema_alpha": float("nan")},
                       {"position_ema_reset_gap": 0}):
            with self.assertRaises(ValueError):
                Settings(**values)

    def test_legacy_migration_preserves_raw_and_history(self):
        with tempfile.TemporaryDirectory() as folder:
            path = str(Path(folder) / "legacy.db")
            with sqlite3.connect(path) as conn:
                conn.executescript("""
                    CREATE TABLE localization_updates (
                        session_id TEXT, epoch_id INTEGER, track_id TEXT, zone_id TEXT,
                        x_m REAL, y_m REAL, confidence REAL, nodes_used INTEGER,
                        rssi_vector_json TEXT, created_at_ms INTEGER,
                        PRIMARY KEY(session_id, epoch_id, track_id));
                    INSERT INTO localization_updates VALUES ('old', 1, 'a', 'Z1', 1, 2, .8, 4, '{}', 1);
                    CREATE TABLE raw_chunks (id INTEGER PRIMARY KEY AUTOINCREMENT,
                        session_id TEXT, epoch_id INTEGER, node_id TEXT, chunk_index INTEGER,
                        payload_json TEXT, received_at_ms INTEGER,
                        UNIQUE(session_id, epoch_id, node_id, chunk_index));
                    INSERT INTO raw_chunks VALUES (1, 'old', 1, 'ESP01', 0, '{"untouched":true}', 1);
                """)
            conn.close()
            for _ in range(2):  # Migration must also be safe on the next restart.
                db = Database(path)
                try:
                    item = db.latest_localization()[0]
                    self.assertEqual((item["raw_x_m"], item["filtered_x_m"], item["x_m"]), (1, 1, 1))
                    self.assertEqual(item["position_filter_mode"], "off")
                    self.assertEqual(db.conn.execute("SELECT payload_json FROM raw_chunks").fetchone()[0],
                                     '{"untouched":true}')
                finally:
                    db.close()

    def test_raw_pipeline_ema_off_zone_and_room_reset(self):
        runtime = main.GatewayRuntime()
        runtime.base_settings = replace(runtime.base_settings, target_device_mac="", target_device_token="",
                                        localize_target_only=False, room_width_m=8., room_length_m=12.,
                                        grid_cell_width_m=2., grid_cell_length_m=4.)
        runtime.settings = runtime.base_settings
        runtime.localizer = main.WCLLocalizationEngine(runtime.settings)
        token = "a" * 64

        def emit(epoch, levels, nodes=4):
            for number, rssi in enumerate(levels[:nodes], 1):
                runtime.reconstructor.add(RawEpochObservation(
                    schema_version=2, session_id="check", node_id=f"ESP0{number}",
                    epoch_id=epoch, epoch_ms=1000, clock_synced=True,
                    sniff_start_unix_ms=epoch * 1000, sniff_end_unix_ms=epoch * 1000 + 700,
                    chunk_index=0, chunk_count=1, queue_drop_count=0, batch_drop_count=0,
                    mqtt_publish_fail_count=0,
                    observations=[dict(device_token=token, rssi_dbm=rssi, channel=6,
                                       frame_type="mgmt", frame_subtype=8, capture_mono_us=1,
                                       capture_unix_ms=epoch * 1000 + 100)]))
            return runtime.database.latest_localization()[0]

        try:
            first = emit(100, [-40, -80, -80, -80])
            second = emit(101, [-80, -80, -80, -40])
            first_estimate = runtime.localizer.localize(dict(zip(main.EXPECTED_NODE_IDS, [-40, -80, -80, -80])))
            second_estimate = runtime.localizer.localize(dict(zip(main.EXPECTED_NODE_IDS, [-80, -80, -80, -40])))
            self.assertEqual(second["x_m"], round(.35 * second_estimate.x_m + .65 * first_estimate.x_m, 3))
            self.assertEqual(second["zone_id"], runtime.localizer.zone_id(second["x_m"], second["y_m"]))
            self.assertNotEqual(second["raw_zone_id"], second["filtered_zone_id"])
            self.assertEqual(second["confidence"], first["confidence"])
            self.assertEqual(second["x_m"], second["filtered_x_m"])
            LocalizationResult.model_validate(second)
            # A delayed duplicate must not replace the newer filter state.
            before = dict(runtime.position_filter.states)
            items = {n: ObservationItem(device_token=token, median_rssi_dbm=-60., packet_count=1,
                                       mad_rssi_db=0., mean_rssi_dbm=-60., min_rssi_dbm=-60., max_rssi_dbm=-60.)
                     for n in main.EXPECTED_NODE_IDS}
            runtime.on_unified_epoch("check", 100, {token: items}, 100000, 100700)
            self.assertEqual(runtime.position_filter.states, before)
            runtime.set_room(8, 12, 2, 4)
            self.assertFalse(runtime.position_filter.states)
            reset = emit(102, [-80, -80, -80, -40])
            self.assertEqual(reset["raw_x_m"], reset["filtered_x_m"])
            runtime.settings = replace(runtime.settings, position_filter_mode="off")
            off = emit(103, [-40, -80, -80, -80])
            expected = runtime.localizer.localize(dict(zip(main.EXPECTED_NODE_IDS, [-40, -80, -80, -80])))
            self.assertEqual((off["x_m"], off["y_m"], off["zone_id"]),
                             (round(expected.x_m, 3), round(expected.y_m, 3), expected.zone_id))
            self.assertEqual(off["x_m"], off["filtered_x_m"])
            self.assertEqual(off["position_filter_mode"], "off")
            emit(104, [-60] * 4, nodes=3)
            runtime.reconstructor.timeout_s = -1
            runtime.reconstructor.flush_expired()
            self.assertEqual(runtime.reconstructor.counters["missing_node"], 1)
            self.assertEqual(runtime.database.conn.execute("SELECT COUNT(*) FROM localization_updates").fetchone()[0], 4)
            self.assertEqual(runtime.database.conn.execute("SELECT COUNT(*) FROM raw_chunks").fetchone()[0], 19)
        finally:
            runtime.database.close()


if __name__ == "__main__":
    unittest.main()
