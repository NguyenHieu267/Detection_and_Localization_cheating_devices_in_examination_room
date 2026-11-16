"""Read live RSSI vectors from the existing gateway; print AI coordinates separately."""

import argparse
import json
import math
import os
from pathlib import Path
import time
from urllib.parse import quote, urlsplit, urlunsplit
from urllib.request import Request, urlopen

from websockets.sync.client import connect

from live_predictor import OnlineLocator
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

NODES = ("ESP01", "ESP02", "ESP03", "ESP04")


def publish_result(base, result, source):
    if result["status"] not in ("OK", "WARMING_UP", "INSUFFICIENT_RSSI"):
        return
    payload = {
        "session_id": source["session_id"],
        "track_id": source["track_id"],
        "timestamp_ms": source["epoch_id"] * 1000,
        "status": result["status"],
    }
    for key in ("x_m", "y_m", "zone", "collected", "needed"):
        if key in result:
            payload[key] = result[key]
    request = Request(
        base + "/api/v1/ai/position",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urlopen(request, timeout=5) as response:
        response.read()


def get_json(base, path):
    with urlopen(base + path, timeout=5) as response:
        return json.load(response)


def check_gateway(base, bundle):
    if get_json(base, "/api/v1/mode")["collection_mode"]:
        raise ValueError("Set COLLECTION_MODE=false in project .env and restart gateway")
    health = get_json(base, "/health")
    if not health["target_configured"]:
        raise ValueError("Set TARGET_DEVICE_MAC or TARGET_DEVICE_TOKEN before starting gateway")
    room = get_json(base, "/api/v1/room")
    checks = (
        (room["width_m"], bundle["room_width"]),
        (room["length_m"], bundle["room_length"]),
        (room["grid_config"]["cell_width_m"], bundle["cell_width"]),
        (room["grid_config"]["cell_length_m"], bundle["cell_length"]),
    )
    if not all(math.isclose(actual, expected, abs_tol=1e-7) for actual, expected in checks):
        raise ValueError("Gateway room/cell dimensions do not match the trained model")
    if room["grid_config"]["columns"] != bundle["grid_columns"]:
        raise ValueError("Gateway grid columns do not match the trained model")


def process_message(locator, payload, session, earliest_ms, current_ms):
    if payload.get("session_id") != session or "rssi_vector_dbm" not in payload:
        return None
    # The gateway sends old database history on connect. Only use fresh epochs.
    epoch = payload.get("epoch_id")
    if not isinstance(epoch, int):
        return None
    timestamp = epoch * 1000
    if timestamp < earliest_ms or abs(current_ms - timestamp) > 5000:
        return None
    token = payload.get("track_id")
    if not token:
        return None
    rssi = payload["rssi_vector_dbm"]
    vector = [rssi.get(node) for node in NODES]
    result = locator.add_vector(session, token, timestamp, vector)
    if result is not None:
        result["rssi_vector"] = vector
    return result


def run(args):
    locator = OnlineLocator(args.model)
    base = args.gateway.rstrip("/")
    check_gateway(base, locator.bundle)
    parts = urlsplit(base)
    scheme = "wss" if parts.scheme == "https" else "ws"
    path = parts.path.rstrip("/") + "/ws/v1/sessions/" + quote(args.session, safe="")
    ws_url = urlunsplit((scheme, parts.netloc, path, "", ""))
    earliest_ms = int(time.time()) * 1000
    print(f"Model: {locator.bundle['model_type']}; window: {locator.window_size}", flush=True)
    print("AI results appear here and on the gateway dashboard (purple AI point).", flush=True)
    with connect(ws_url, open_timeout=10) as socket:
        print(f"Connected: {ws_url}; waiting for fresh RSSI...", flush=True)
        while True:
            try:
                message = socket.recv(timeout=10)
            except TimeoutError:
                print("No new vector: check MQTT, four nodes, NTP, session and target MAC/salt.",
                      flush=True)
                continue
            payload = json.loads(message)
            result = process_message(
                locator, payload, args.session, earliest_ms, int(time.time() * 1000)
            )
            if result is not None:
                print(json.dumps(result, ensure_ascii=False), flush=True)
                publish_result(base, result, payload)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--gateway", default=os.getenv("GATEWAY_URL", "http://127.0.0.1:8000"))
    parser.add_argument("--session", default=os.getenv("SESSION_ID", "session-dev-001"))
    parser.add_argument("--model", default=str(ROOT / os.getenv(
        "AI_MODEL_PATH", "model/output/model.joblib")))
    options = parser.parse_args()
    try:
        run(options)
    except KeyboardInterrupt:
        pass
    except Exception as error:
        parser.exit(1, f"Realtime AI stopped: {error}\n")
