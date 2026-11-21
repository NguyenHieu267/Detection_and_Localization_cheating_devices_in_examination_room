"""CSV persistence for individual, unmodified ESP32 samples."""

from collections import deque
import csv
from datetime import datetime, timezone
import io
from itertools import islice
import json
import os
from pathlib import Path
import re
import threading

DATASET_COLUMNS = [
    "session", "timestamp", "Zone", "ESP01 - RSSI", "ESP02 - RSSI",
    "ESP03 - RSSI", "ESP04 - RSSI", "Vector RSSI",
]
RUN_COLUMNS = ["measurement_id", "details_json"]
LIVE_COLUMNS = ["timestamp", "session", "Zone", "x_m", "y_m", "details_json"]
RUN_LABEL = re.compile(r"^(Z[1-9][0-9]*)_run([0-9]+)$")


def utc_timestamp(timestamp_ms):
    seconds, milliseconds = divmod(int(timestamp_ms), 1000)
    date = datetime.fromtimestamp(seconds, timezone.utc)
    return date.strftime("%Y-%m-%dT%H:%M:%S") + f".{milliseconds:03d}Z"


def unix_ms(timestamp):
    date = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
    if date.utcoffset() is None:
        raise ValueError("CSV timestamp must include a timezone")
    delta = date - datetime(1970, 1, 1, tzinfo=timezone.utc)
    return delta.days * 86400000 + delta.seconds * 1000 + delta.microseconds // 1000


def read_rows(path, columns):
    if path.exists():
        with path.open(encoding="utf-8-sig", newline="") as source:
            reader = csv.DictReader(source)
            if reader.fieldnames != columns:
                raise ValueError(f"Unexpected CSV columns: {path}")
            yield from reader


def append_rows(path, columns, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    header = not path.exists() or path.stat().st_size == 0
    with path.open("a", encoding="utf-8", newline="") as output:
        writer = csv.DictWriter(output, fieldnames=columns)
        if header:
            writer.writeheader()
        writer.writerows(rows)
        output.flush()
        os.fsync(output.fileno())


class CsvStore:
    def __init__(self, dataset_path, realtime_path):
        self.dataset_path = Path(dataset_path)
        self.realtime_path = Path(realtime_path)
        self.runs_path = self.dataset_path.with_name(self.dataset_path.stem + "_runs.csv")
        self.lock = threading.RLock()
        self.recent = deque(maxlen=1000)
        self.history = deque(maxlen=200)
        self.quality = deque(maxlen=200)
        self.run_numbers = {}
        self.row_count = 0
        counts = {}
        for row in read_rows(self.dataset_path, DATASET_COLUMNS):
            unix_ms(row["timestamp"])
            match = RUN_LABEL.fullmatch(row["Zone"])
            if not match:
                raise ValueError("Collection CSV requires labels such as Z1_run01")
            key = (row["session"], match[1])
            self.run_numbers[key] = max(self.run_numbers.get(key, 0), int(match[2]))
            vector = json.loads(row["Vector RSSI"])
            count = counts.setdefault((row["session"], row["Zone"]), [0, 0, 0, 0])
            for index, value in enumerate(vector):
                count[index] += value is not None
            self.recent.append(row)
            self.row_count += 1
        self.runs = {}
        for row in read_rows(self.runs_path, RUN_COLUMNS):
            run = json.loads(row["details_json"])
            observed = counts.get((run["session"], run["run_label"]), [0] * 4)
            run["sample_counts"] = dict(zip(("ESP01", "ESP02", "ESP03", "ESP04"), observed))
            if run["status"] in {"Measuring", "Receiving"}:
                run.update(status="Partial", interrupted=True)
            self.runs[run["measurement_id"]] = run
        for row in read_rows(self.realtime_path, LIVE_COLUMNS):
            self.history.append(json.loads(row["details_json"]))

    def next_run_number(self, session, zone):
        with self.lock:
            previous = [run["run_number"] for run in self.runs.values()
                        if run["session"] == session and run["zone"] == zone]
            return max([self.run_numbers.get((session, zone), 0), *previous]) + 1

    def save_run(self, run):
        with self.lock:
            self.runs[run["measurement_id"]] = dict(run)
            self.runs_path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.runs_path.with_suffix(".csv.tmp")
            with temporary.open("w", encoding="utf-8", newline="") as output:
                writer = csv.DictWriter(output, fieldnames=RUN_COLUMNS)
                writer.writeheader()
                writer.writerows({"measurement_id": key, "details_json": json.dumps(value)}
                                 for key, value in self.runs.items())
                output.flush()
                os.fsync(output.fileno())
            temporary.replace(self.runs_path)

    def append_samples(self, rows):
        with self.lock:
            append_rows(self.dataset_path, DATASET_COLUMNS, rows)
            self.recent.extend(rows)
            self.row_count += len(rows)

    def measurements(self, session=None):
        with self.lock:
            return sorted([dict(run) for run in self.runs.values()
                           if session is None or run["session"] == session],
                          key=lambda run: run["start_ms"], reverse=True)

    def latest_samples(self, session, limit=50, measurement_id=None):
        with self.lock:
            label = self.runs.get(measurement_id, {}).get("run_label")
            rows = [row for row in self.recent if row["session"] == session and
                    (not measurement_id or row["Zone"] == label)]
            return [{"session": row["session"], "timestamp": row["timestamp"],
                     "zone": row["Zone"], "vector": json.loads(row["Vector RSSI"])}
                    for row in reversed(rows[-limit:])]

    def export_samples(self, session, measurement_id=None):
        with self.lock:
            label = self.runs.get(measurement_id, {}).get("run_label")
            row_count = self.row_count
        buffer = io.StringIO(newline="")
        writer = csv.DictWriter(buffer, fieldnames=DATASET_COLUMNS)
        writer.writeheader()
        yield buffer.getvalue()
        for row in islice(read_rows(self.dataset_path, DATASET_COLUMNS), row_count):
            if row["session"] == session and (not measurement_id or row["Zone"] == label):
                buffer.seek(0)
                buffer.truncate()
                writer.writerow(row)
                yield buffer.getvalue()

    def save_localization(self, result):
        with self.lock:
            append_rows(self.realtime_path, LIVE_COLUMNS, [dict(zip(LIVE_COLUMNS, [
                utc_timestamp(result["timestamp_ms"]), result["session_id"], result["zone_id"],
                result["x_m"], result["y_m"], json.dumps(result, separators=(",", ":")),
            ]))])
            self.history.append(result)

    def latest_localization(self, limit=50):
        with self.lock:
            return list(reversed(self.history))[:limit]

    def store_quality(self, quality):
        with self.lock:
            self.quality.append(quality)

    def latest_quality(self, limit=50):
        with self.lock:
            return list(reversed(self.quality))[:limit]
