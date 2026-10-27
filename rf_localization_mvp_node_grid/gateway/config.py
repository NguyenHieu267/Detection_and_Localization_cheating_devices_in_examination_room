from __future__ import annotations

from dataclasses import dataclass, field
import os
import math
from pathlib import Path

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover
    load_dotenv = None

if load_dotenv is not None:
    load_dotenv()


@dataclass(frozen=True)
class NodePosition:
    node_id: str
    x_m: float
    y_m: float


@dataclass(frozen=True)
class GridConfig:
    nx: int
    ny: int
    cell_width_m: float
    cell_length_m: float


@dataclass(frozen=True)
class Settings:
    # MQTT
    mqtt_host: str = os.getenv("MQTT_HOST", "172.20.10.2")
    mqtt_port: int = int(os.getenv("MQTT_PORT", "1883"))
    mqtt_username: str = os.getenv("MQTT_USERNAME", "")
    mqtt_password: str = os.getenv("MQTT_PASSWORD", "")
    mqtt_client_id: str = os.getenv("MQTT_CLIENT_ID", "rf-gateway-mvp")
    observation_topic: str = "rf/v1/nodes/+/observation"
    ready_topic: str = "rf/v1/nodes/+/ready"

    # Epoch reconstruction.
    epoch_timeout_ms: int = int(os.getenv("EPOCH_TIMEOUT_MS", "1800"))

    # SQLite.
    database_path: str = os.getenv("DATABASE_PATH", "rf_gateway.db")

    # Room and editable grid CELL SIZE. The Gateway automatically derives
    # the number of columns/rows from the room dimensions.
    room_width_m: float = float(os.getenv("ROOM_WIDTH_M", "8.0"))
    room_length_m: float = float(os.getenv("ROOM_LENGTH_M", "12.0"))
    grid_cell_width_m: float = float(os.getenv("GRID_CELL_WIDTH_M", "2.0"))
    grid_cell_length_m: float = float(os.getenv("GRID_CELL_LENGTH_M", "4.0"))

    # First-test device focus. The firmware may still publish every device;
    # the gateway localizes only this one when a target is configured.
    target_device_mac: str = os.getenv("TARGET_DEVICE_MAC", "")
    target_device_token: str = os.getenv("TARGET_DEVICE_TOKEN", "")
    device_token_salt: str = os.getenv("DEVICE_TOKEN_SALT", "rf-mvp-shared-salt-change-me")

    # When false, gateway localizes every observed device.
    localize_target_only: bool = os.getenv("LOCALIZE_TARGET_ONLY", "true").lower() in {
        "1", "true", "yes", "on"
    }

    # Node is considered offline when no MQTT message has been received for
    # this duration. Nodes normally publish one observation every epoch.
    node_offline_timeout_ms: int = int(os.getenv("NODE_OFFLINE_TIMEOUT_MS", "3500"))

    max_ws_clients: int = int(os.getenv("MAX_WS_CLIENTS", "64"))

    node_positions: tuple[NodePosition, ...] = field(default_factory=tuple)

    @property
    def grid(self) -> GridConfig:
        return make_grid(
            self.room_width_m,
            self.room_length_m,
            self.grid_cell_width_m,
            self.grid_cell_length_m,
        )


EXPECTED_NODE_IDS = ("ESP01", "ESP02", "ESP03", "ESP04")


def make_grid(room_width_m: float, room_length_m: float, cell_width_m: float, cell_length_m: float) -> GridConfig:
    """Build a grid from the requested physical cell size.

    The number of columns/rows is derived automatically with ceil(). If the
    requested cell size does not divide the room exactly, the final column or
    row is narrower/shorter so the grid still covers the whole room.
    """
    width = max(float(room_width_m), 0.1)
    length = max(float(room_length_m), 0.1)
    target_cell_width = max(float(cell_width_m), 0.1)
    target_cell_length = max(float(cell_length_m), 0.1)
    nx = max(1, math.ceil(width / target_cell_width))
    ny = max(1, math.ceil(length / target_cell_length))
    return GridConfig(
        nx=nx,
        ny=ny,
        cell_width_m=target_cell_width,
        cell_length_m=target_cell_length,
    )


def build_settings(
    room_width_m: float,
    room_length_m: float,
    *,
    base: Settings,
    grid_cell_width_m: float | None = None,
    grid_cell_length_m: float | None = None,
) -> Settings:
    """Return settings with room-scaled corner anchors and cell-size-based grid."""
    cell_width = base.grid_cell_width_m if grid_cell_width_m is None else max(0.1, float(grid_cell_width_m))
    cell_length = base.grid_cell_length_m if grid_cell_length_m is None else max(0.1, float(grid_cell_length_m))
    return Settings(
        mqtt_host=base.mqtt_host,
        mqtt_port=base.mqtt_port,
        mqtt_username=base.mqtt_username,
        mqtt_password=base.mqtt_password,
        mqtt_client_id=base.mqtt_client_id,
        observation_topic=base.observation_topic,
        ready_topic=base.ready_topic,
        epoch_timeout_ms=base.epoch_timeout_ms,
        database_path=base.database_path,
        room_width_m=float(room_width_m),
        room_length_m=float(room_length_m),
        grid_cell_width_m=cell_width,
        grid_cell_length_m=cell_length,
        target_device_mac=base.target_device_mac,
        target_device_token=base.target_device_token,
        device_token_salt=base.device_token_salt,
        localize_target_only=base.localize_target_only,
        node_offline_timeout_ms=base.node_offline_timeout_ms,
        max_ws_clients=base.max_ws_clients,
        node_positions=(
            NodePosition("ESP01", 0.0, 0.0),
            NodePosition("ESP02", float(room_width_m), 0.0),
            NodePosition("ESP03", 0.0, float(room_length_m)),
            NodePosition("ESP04", float(room_width_m), float(room_length_m)),
        ),
    )


DEFAULT_SETTINGS = Settings(
    room_width_m=float(os.getenv("ROOM_WIDTH_M", "8.0")),
    room_length_m=float(os.getenv("ROOM_LENGTH_M", "12.0")),
    grid_cell_width_m=float(os.getenv("GRID_CELL_WIDTH_M", "2.0")),
    grid_cell_length_m=float(os.getenv("GRID_CELL_LENGTH_M", "4.0")),
)


def default_config_path() -> Path:
    return Path(DEFAULT_SETTINGS.database_path).resolve()
