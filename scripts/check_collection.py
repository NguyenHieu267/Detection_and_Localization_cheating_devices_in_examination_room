"""Collection vectors and raw regression checks; uses temporary SQLite only."""
from dataclasses import replace
import csv
import io
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "gateway"))
os.environ["DATABASE_PATH"] = ":memory:"

import main
from collection import DatasetCollection
from models import RawEpochObservation

BASE = 1800000000000
NODES = main.EXPECTED_NODE_IDS


def report(node, start, values=(-51, -53), index=0, count=1, offset=100):
    return RawEpochObservation(
        schema_version=2,
        session_id="S01",
        node_id=node,
        epoch_id=start // 1000,
        epoch_ms=1000,
        clock_synced=True,
        sniff_start_unix_ms=start,
        sniff_end_unix_ms=start + 700,
        chunk_index=index,
        chunk_count=count,
        queue_drop_count=0,
        batch_drop_count=0,
        mqtt_publish_fail_count=0,
        observations=[
            {
                "device_token": "a" * 64,
                "rssi_dbm": value,
                "channel": 6,
                "frame_type": "mgmt",
                "frame_subtype": 8,
                "capture_mono_us": 100 + i,
                "capture_unix_ms": start + offset + i,
            }
            for i, value in enumerate(values)
        ],
    )


class CollectionChecks(unittest.TestCase):
    def setUp(self):
        with patch("main.COLLECTION_MODE", False):
            self.runtime = main.GatewayRuntime()
        self.runtime.settings = replace(
            self.runtime.settings,
            target_device_mac="",
            target_device_token="",
            localize_target_only=False,
        )
        self.runtime.mqtt.connected = True
        self.collect = DatasetCollection(self.runtime)

    def tearDown(self):
        self.runtime.database.close()

    def feed(self, start, nodes=NODES):
        with patch("collection.now_ms", return_value=start + 900):
            for node in nodes:
                self.collect.on_raw(report(node, start))
        self.collect.tick(start + 1500)

    def test_disabled_flag_preserves_realtime_pipeline(self):
        self.assertIsNone(self.runtime.mqtt.collection_handler)
        self.assertIsNone(self.runtime.collection)

    def test_raw_values_and_duplicates_with_missing_nodes(self):
        self.feed(BASE)
        self.feed(BASE + 1000, ("ESP01", "ESP03", "ESP04"))
        self.feed(BASE + 2000, ("ESP01",))
        with patch("collection.now_ms", return_value=BASE + 4000):
            self.collect.on_raw(report("ESP01", BASE))
        rows = self.runtime.database.latest_samples("S01", 100)
        self.assertEqual(len(rows), 16)
        self.assertEqual({row["rssi_dbm"] for row in rows}, {-51, -53})
        self.assertTrue(all(row["measurement_id"] is None for row in rows))
        self.assertFalse(self.runtime.database.latest_localization())
        self.assertTrue(self.runtime.database.conn.execute(
            "SELECT name FROM sqlite_master WHERE name='rssi_vectors'"
        ).fetchall())

    def test_requested_eight_columns_medians_and_null_node_order(self):
        self.feed(BASE - 1000)
        measurement = self.collect.start("S01", "Z1", True, BASE)
        windows = [
            {"ESP01": [-50, -51, -52], "ESP02": [-62], "ESP03": [-70], "ESP04": [-58]},
            {"ESP01": [-53], "ESP03": [-72], "ESP04": [-60]},
            {"ESP02": [-64], "ESP04": [-61]},
        ]
        for second, nodes in enumerate(windows):
            start = BASE + second * 1000
            with patch("collection.now_ms", return_value=start + 900):
                for node, values in nodes.items():
                    self.collect.on_raw(report(node, start, values))
            self.collect.tick(start + 1500)

        vectors = sorted(self.runtime.database.latest_vectors(
            "S01", 100, measurement["measurement_id"]
        ), key=lambda row: row["timestamp"])
        self.assertEqual(len(vectors), 30)
        expected = [
            [-51, -62, -70, -58],
            [-53, None, -72, -60],
            [None, -64, None, -61],
        ]
        self.assertEqual([row["vector"] for row in vectors[:3]], expected)
        self.assertTrue(all(row["zone"] == "Z1" for row in vectors))
        self.assertEqual(vectors[3]["vector"], [None] * 4)
        original = self.runtime.database.latest_samples("S01", 100, measurement["measurement_id"])
        self.assertEqual(len(original), 11)
        self.assertEqual(sorted(row["rssi_dbm"] for row in original if row["node_id"] == "ESP01"),
                         [-53, -52, -51, -50])

        exported = list(csv.reader(io.StringIO("".join(
            self.runtime.database.export_collection_vectors("S01")
        ))))
        self.assertEqual(exported[0], [
            "session", "timestamp", "Zone", "ESP01 - RSSI", "ESP02 - RSSI",
            "ESP03 - RSSI", "ESP04 - RSSI", "Vector RSSI",
        ])
        self.assertTrue(all(len(row) == 8 for row in exported))
        self.assertEqual(exported[1], [
            "S01", str(BASE), "Z1", "-51", "-62", "-70", "-58", "[-51,-62,-70,-58]",
        ])
        self.assertEqual(exported[2][4], "NULL")
        self.assertEqual(exported[2][7], "[-53,null,-72,-60]")

    def test_immediate_start_and_exact_capture_boundaries(self):
        self.feed(BASE - 1000)
        measurement = self.collect.start("S01", "Z1", True, BASE + 337)
        self.assertEqual(measurement["start_ms"], BASE + 337)
        self.assertEqual(measurement["end_ms"], BASE + 30337)
        self.assertEqual(measurement["status"], "Measuring")
        before = report("ESP01", BASE, (-70, -71), offset=336)
        after = report("ESP01", BASE + 30000, (-72, -73), offset=336)
        with patch("collection.now_ms", return_value=BASE + 32000):
            self.collect.on_raw(before)
            self.collect.on_raw(after)
        rows = self.runtime.database.latest_samples("S01", 100, measurement["measurement_id"])
        self.assertEqual({row["rssi_dbm"] for row in rows}, {-71, -72})
        self.collect.tick(BASE + 32000)
        vectors = sorted(self.runtime.database.latest_vectors(
            "S01", 100, measurement["measurement_id"]
        ), key=lambda row: row["timestamp"])
        self.assertEqual(len(vectors), 31)
        self.assertEqual(vectors[0]["vector"], [-71, None, None, None])
        self.assertEqual(vectors[-1]["vector"], [-72, None, None, None])

    def test_two_runs_receiving_and_late_ground_truth(self):
        collector = self.collect
        with self.assertRaises(ValueError):
            collector.start("S01", "Z1", True, BASE)
        self.feed(BASE - 1000)
        first = collector.start("S01", "Z1", True, BASE)
        self.assertEqual((first["true_x_m"], first["true_y_m"]), (1.0, 2.0))
        with self.assertRaises(ValueError):
            collector.start("S01", "Z2", True, BASE)
        for start in range(first["start_ms"], first["end_ms"], 1000):
            self.feed(start)
        collector.tick(first["end_ms"])
        self.assertEqual(self.runtime.database.measurements()[0]["status"], "Receiving")
        collector.tick(first["end_ms"] + 3000)
        self.assertEqual(self.runtime.database.measurements()[0]["status"], "Completed")
        self.feed(first["end_ms"] + 3000)
        second = collector.start("S01", "Z1", True, first["end_ms"] + 4000)
        self.assertEqual(second["run_number"], 2)
        self.assertNotEqual(second["measurement_id"], first["measurement_id"])
        late = report("ESP01", first["start_ms"], (-90,), index=1, count=2)
        with patch("collection.now_ms", return_value=second["start_ms"] + 1000):
            collector.on_raw(late)
        collector.tick(second["start_ms"] + 1500)
        row = self.runtime.database.conn.execute(
            "SELECT measurement_id FROM raw_rssi_samples WHERE rssi_dbm=-90"
        ).fetchone()
        self.assertEqual(row[0], first["measurement_id"])
        vectors = self.runtime.database.latest_vectors("S01", 100, first["measurement_id"])
        first_vector = next(vector for vector in vectors if vector["timestamp"] == first["start_ms"])
        self.assertEqual(first_vector["vector"][0], -53)
        collector.tick(second["end_ms"] + 3000)
        self.assertEqual(self.runtime.database.measurements()[0]["status"], "Partial")
        exported = "".join(self.runtime.database.export_collection_raw("S01"))
        rows = list(csv.DictReader(io.StringIO(exported)))
        self.assertTrue(any(row["rssi_dbm"] == "-90" for row in rows))
        self.assertEqual(rows[0]["rssi_dbm"], "-51")
        self.assertIn("true_x_m", rows[0])

    def test_disconnect_keeps_surviving_raw(self):
        self.feed(BASE - 1000)
        measurement = self.collect.start("S01", "Z2", True, BASE)
        self.feed(measurement["start_ms"], ("ESP01",))
        self.collect.tick(measurement["start_ms"] + 5000)
        self.collect.tick(measurement["end_ms"] + 3000)
        result = self.runtime.database.measurements()[0]
        self.assertEqual(result["status"], "Partial")
        self.assertTrue(result["interrupted"])
        self.assertEqual(result["sample_counts"]["ESP01"], 2)
        self.assertEqual(result["sample_counts"]["ESP02"], 0)

    def test_restart_preserves_original_raw_and_updates_late_counts(self):
        with tempfile.TemporaryDirectory() as folder:
            path = str(Path(folder) / "old.db")
            original = main.Database(path)
            original.store_raw_chunk(report("ESP01", BASE))
            before = original.conn.execute("SELECT payload_json FROM raw_chunks").fetchone()[0]
            original.close()
            for attempt in range(2):
                database = main.Database(path)
                runtime = type("Runtime", (), {
                    "database": database,
                    "settings": self.runtime.settings,
                    "should_localize": lambda self, token, count: True,
                })()
                collector = DatasetCollection(runtime)
                if attempt == 0:
                    with patch("collection.now_ms", return_value=BASE + 900):
                        collector.on_raw(report("ESP01", BASE))
                else:
                    rows = database.latest_samples("S01")
                    self.assertEqual({row["rssi_dbm"] for row in rows}, {-51, -53})
                self.assertEqual(
                    database.conn.execute("SELECT payload_json FROM raw_chunks").fetchone()[0],
                    before,
                )
                database.close()


if __name__ == "__main__":
    unittest.main()
