from __future__ import annotations

import json
import logging
from typing import Callable

import paho.mqtt.client as mqtt
from pydantic import ValidationError

from config import Settings
from epoch_reconstructor import EpochReconstructor
from models import EpochObservation, NodeReady

logger = logging.getLogger(__name__)


class MqttGateway:
    def __init__(
        self,
        settings: Settings,
        reconstructor: EpochReconstructor,
        on_node_ready: Callable,
        on_node_seen: Callable,
    ):
        self.settings = settings
        self.reconstructor = reconstructor
        self.on_node_ready = on_node_ready
        self.on_node_seen = on_node_seen
        self.client = mqtt.Client(
            callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
            client_id=settings.mqtt_client_id,
            protocol=mqtt.MQTTv311,
        )
        if settings.mqtt_username:
            self.client.username_pw_set(settings.mqtt_username, settings.mqtt_password)
        self.client.on_connect = self._on_connect
        self.client.on_disconnect = self._on_disconnect
        self.client.on_message = self._on_message
        self.connected = False

    def start(self) -> None:
        logger.info("Connecting MQTT to %s:%d", self.settings.mqtt_host, self.settings.mqtt_port)
        self.client.connect_async(self.settings.mqtt_host, self.settings.mqtt_port, keepalive=30)
        self.client.loop_start()

    def stop(self) -> None:
        try:
            self.client.loop_stop()
            self.client.disconnect()
        finally:
            self.connected = False

    def _on_connect(self, client, userdata, flags, reason_code, properties=None) -> None:
        self.connected = reason_code == 0
        if not self.connected:
            logger.error("MQTT connect failed: %s", reason_code)
            return
        client.subscribe(self.settings.observation_topic, qos=0)
        client.subscribe(self.settings.ready_topic, qos=0)
        logger.info("MQTT connected; subscriptions installed")

    def _on_disconnect(self, client, userdata, disconnect_flags=None, reason_code=None, properties=None) -> None:
        self.connected = False
        logger.warning("MQTT disconnected: %s", reason_code)

    def _on_message(self, client, userdata, msg) -> None:
        try:
            payload = json.loads(msg.payload.decode("utf-8"))
            topic_parts = msg.topic.split("/")
            topic_node_id = topic_parts[3] if len(topic_parts) == 5 else None
            message_type = payload.get("message_type")

            if message_type == "EPOCH_OBSERVATION":
                report = EpochObservation.model_validate(payload)
                if topic_node_id != report.node_id:
                    raise ValueError("topic node_id does not match payload node_id")
                self.on_node_seen(report.node_id, report.session_id, "EPOCH_OBSERVATION")
                self.reconstructor.add(report)
            elif message_type == "NODE_READY":
                ready = NodeReady.model_validate(payload)
                if topic_node_id != ready.node_id:
                    raise ValueError("topic node_id does not match payload node_id")
                self.on_node_seen(ready.node_id, ready.session_id, "NODE_READY")
                self.on_node_ready(ready)
            else:
                logger.warning("Unsupported message_type=%r on %s", message_type, msg.topic)
        except (UnicodeDecodeError, json.JSONDecodeError, ValidationError, ValueError) as exc:
            logger.warning("MQTT message rejected on %s: %s", msg.topic, exc)
