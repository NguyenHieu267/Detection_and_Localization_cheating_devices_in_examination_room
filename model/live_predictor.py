from collections import defaultdict, deque
import math
import joblib
import numpy as np
from train_regression import window_features


class OnlineLocator:
    def __init__(self, model_path, min_valid_rows_ratio=0.6):
        self.bundle = joblib.load(model_path)  # Load ONLY models you trust.
        self.model = self.bundle["model"]
        self.window_size = int(self.bundle["window_size"])
        self.min_valid_rows_ratio = min_valid_rows_ratio
        self.buffers = defaultdict(lambda: deque(maxlen=self.window_size))
        self.last_timestamps = {}

    def add_vector(self, session, target_mac, timestamp_ms, vector):
        """A vector represents ONE target MAC and ONE 1-second bucket.

        Return None while buffering, or prediction/status dict after warm-up.
        Do not call separately for each ESP32: merge 4 RSSIs first.
        """
        if not isinstance(timestamp_ms, (int, np.integer)):
            raise ValueError("timestamp_ms must be a real integer UNIX epoch in milliseconds")
        if len(vector) != 4:
            raise ValueError("vector must have 4 RSSI fields, in ESP01..ESP04 order")
        key = (str(session), str(target_mac).lower())
        prior = self.last_timestamps.get(key)
        if prior is not None:
            dt = timestamp_ms - prior
            if dt <= 0:
                return {"status": "STALE_OR_DUPLICATE_TIMESTAMP"}
            if dt > 2100:
                self.buffers[key].clear()  # An old window is no longer continuous.
        self.last_timestamps[key] = timestamp_ms
        values = [float(v) if v is not None else np.nan for v in vector]
        buf = self.buffers[key]
        buf.append(values)
        if len(buf) < self.window_size:
            return {"status": "WARMING_UP", "collected": len(buf), "needed": self.window_size}
        a = np.asarray(buf, dtype=float)
        good_rows = np.isfinite(a).sum(axis=1) >= 3
        if good_rows.mean() < self.min_valid_rows_ratio:
            return {"status": "INSUFFICIENT_RSSI", "valid_rows":int(good_rows.sum())}
        f = window_features(a)
        if f is None:
            return {"status": "INSUFFICIENT_RSSI"}
        raw_x, raw_y = map(float, self.model.predict(f.reshape(1, -1))[0])
        width, length = self.bundle["room_width"], self.bundle["room_length"]
        x, y = float(np.clip(raw_x, 0, width)), float(np.clip(raw_y, 0, length))
        col = min(int(x/self.bundle["cell_width"]),self.bundle["grid_columns"]-1)
        rows = math.ceil(length / self.bundle["cell_length"] - 1e-9)
        row = min(int(y/self.bundle["cell_length"]), rows - 1)
        zone = f"Z{row*self.bundle['grid_columns']+col+1}"
        return {
            "status":"OK", "session":key[0],"target_mac":key[1],
            "timestamp_ms":int(timestamp_ms),
            "x_raw_m":round(raw_x,3), "y_raw_m":round(raw_y,3),
            "x_m":round(x,3), "y_m":round(y,3),
            "zone":zone,"trained_zone":zone in self.bundle["allowed_zones"],
            "inside_room_raw":0<=raw_x<=width and 0<=raw_y<=length,
        }
