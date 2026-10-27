"""Publish one synthetic target device across the four node topics."""
from __future__ import annotations

import hashlib
import json
import os
import time
import uuid

import paho.mqtt.publish as publish

MQTT_HOST = os.getenv("MQTT_HOST", "127.0.0.1")
MQTT_PORT = int(os.getenv("MQTT_PORT", "1883"))
SESSION_ID = os.getenv("SESSION_ID", "session-dev-001")
DEVICE_MAC = os.getenv("SIM_DEVICE_MAC", "02:11:22:33:44:55")
SALT = os.getenv("DEVICE_TOKEN_SALT", "rf-mvp-shared-salt-change-me")


def token_for_mac(mac: str) -> str:
    raw = mac.replace(":", "").replace("-", "")
    return hashlib.sha256(bytes.fromhex(raw) + SALT.encode()).hexdigest()


def publish_epoch(epoch_id: int) -> None:
    token = token_for_mac(DEVICE_MAC)
    sniff_start_ms = epoch_id * 1000
    sniff_end_ms = sniff_start_ms + 1000
    rssi = {"ESP01": -55.0, "ESP02": -63.0, "ESP03": -61.0, "ESP04": -69.0}

    messages = []
    for node_id, median in rssi.items():
        payload = {
            "schema_version": "1.0",
            "message_type": "EPOCH_OBSERVATION",
            "message_id": str(uuid.uuid4()),
            "session_id": SESSION_ID,
            "epoch_id": epoch_id,
            "node_id": node_id,
            "sniff_start_ms": sniff_start_ms,
            "sniff_end_ms": sniff_end_ms,
            "observations": [{
                "device_token": token,
                "median_rssi_dbm": float(median),
                "packet_count": 20,
                "mad_rssi_db": 1.5,
                "mean_rssi_dbm": float(median - 0.2),
                "min_rssi_dbm": float(median - 3.0),
                "max_rssi_dbm": float(median + 2.0),
            }],
        }
        messages.append({
            "topic": f"rf/v1/nodes/{node_id}/observation",
            "payload": json.dumps(payload),
            "qos": 0,
        })

    publish.multiple(messages, hostname=MQTT_HOST, port=MQTT_PORT)
    print(f"Published epoch {epoch_id}; token={token}")


if __name__ == "__main__":
    publish_epoch(int(time.time()))
