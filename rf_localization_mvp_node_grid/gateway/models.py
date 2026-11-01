from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, StrictStr, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class NodeReady(StrictModel):
    schema_version: StrictStr = Field(pattern=r"^1\.0$")
    message_type: Literal["NODE_READY"]
    message_id: StrictStr = Field(min_length=1, max_length=128)
    session_id: StrictStr = Field(min_length=1, max_length=128)
    node_id: StrictStr = Field(pattern=r"^ESP0[1-4]$")
    timestamp_ms: StrictInt = Field(ge=0)


class ObservationItem(StrictModel):
    device_token: StrictStr = Field(min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$")
    median_rssi_dbm: float = Field(ge=-127.0, le=0.0)
    packet_count: StrictInt = Field(ge=1)
    mad_rssi_db: float = Field(ge=0.0, le=127.0)
    mean_rssi_dbm: float = Field(ge=-127.0, le=0.0)
    min_rssi_dbm: float = Field(ge=-127.0, le=0.0)
    max_rssi_dbm: float = Field(ge=-127.0, le=0.0)


class EpochObservation(StrictModel):
    schema_version: StrictStr = Field(pattern=r"^1\.0$")
    message_type: Literal["EPOCH_OBSERVATION"]
    message_id: StrictStr = Field(min_length=1, max_length=128)
    session_id: StrictStr = Field(min_length=1, max_length=128)
    epoch_id: StrictInt = Field(ge=0)
    node_id: StrictStr = Field(pattern=r"^ESP0[1-4]$")
    sniff_start_ms: StrictInt = Field(ge=0)
    sniff_end_ms: StrictInt = Field(gt=0)
    observations: list[ObservationItem]

    def validate_epoch_window(self) -> None:
        if self.sniff_end_ms <= self.sniff_start_ms:
            raise ValueError("sniff_end_ms must be greater than sniff_start_ms")


class RoomConfigRequest(StrictModel):
    width_m: float = Field(gt=0.1, le=1000.0)
    length_m: float = Field(gt=0.1, le=1000.0)
    grid_cell_width_m: float = Field(gt=0.1, le=1000.0)
    grid_cell_length_m: float = Field(gt=0.1, le=1000.0)


class LocalizationResult(StrictModel):
    track_id: StrictStr
    session_id: StrictStr
    epoch_id: StrictInt
    zone_id: StrictStr
    x_m: float
    y_m: float
    confidence: float = Field(ge=0.0, le=1.0)
    nodes_used: StrictInt = Field(ge=0)
    rssi_vector_dbm: dict[str, float]
    timestamp_ms: StrictInt = Field(ge=0)
    sync_status: str = "complete"
    node_packet_count: dict[str, int] = Field(default_factory=dict)
    queue_drop_count: dict[str, int] = Field(default_factory=dict)
    overlap_ms: int = 0


class EpochDeviceEvidence(StrictModel):
    session_id: StrictStr
    epoch_id: StrictInt
    device_token: StrictStr
    node_rssi_dbm: dict[str, float]
    node_packet_count: dict[str, int]
    nodes_observed: StrictInt = Field(ge=0)
    sniff_start_ms: StrictInt = Field(ge=0)
    sniff_end_ms: StrictInt = Field(gt=0)


class RawObservation(StrictModel):
    device_token: StrictStr = Field(pattern=r"^[0-9a-f]{64}$")
    rssi_dbm: StrictInt = Field(ge=-127, le=0)
    channel: StrictInt = Field(ge=1, le=14)
    frame_type: Literal["mgmt", "data"]
    frame_subtype: StrictInt = Field(ge=0, le=15)
    capture_mono_us: StrictInt = Field(ge=0)
    capture_unix_ms: StrictInt = Field(ge=0)


class RawEpochObservation(StrictModel):
    schema_version: Literal[2]
    message_type: Literal["EPOCH_OBSERVATION"] = "EPOCH_OBSERVATION"
    session_id: StrictStr = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_.-]+$")
    node_id: StrictStr = Field(pattern=r"^ESP0[1-4]$")
    epoch_id: StrictInt = Field(ge=0)
    epoch_ms: StrictInt = Field(ge=100, le=60000)
    clock_synced: StrictBool
    sniff_start_unix_ms: StrictInt = Field(ge=0)
    sniff_end_unix_ms: StrictInt = Field(ge=0)
    chunk_index: StrictInt = Field(ge=0, le=1023)
    chunk_count: StrictInt = Field(ge=1, le=1024)
    queue_drop_count: StrictInt = Field(ge=0)
    batch_drop_count: StrictInt = Field(ge=0)
    mqtt_publish_fail_count: StrictInt = Field(ge=0)
    observations: list[RawObservation] = Field(max_length=2048)

    @model_validator(mode="after")
    def validate_window(self):
        if self.chunk_index >= self.chunk_count:
            raise ValueError("chunk_index must be below chunk_count")
        start, end = self.sniff_start_unix_ms, self.sniff_end_unix_ms
        if self.clock_synced:
            if not (self.epoch_id * self.epoch_ms <= start < end <= (self.epoch_id + 1) * self.epoch_ms):
                raise ValueError("window must lie inside capture epoch")
            if any(not start <= item.capture_unix_ms < end for item in self.observations):
                raise ValueError("sample timestamp outside capture window")
        elif start or end or any(item.capture_unix_ms for item in self.observations):
            raise ValueError("unsynced data must not claim Unix time")
        return self
