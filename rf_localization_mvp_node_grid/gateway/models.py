from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, StrictFloat, StrictInt, StrictStr


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class NodeReady(StrictModel):
    schema_version: StrictStr = Field(pattern=r"^1\.0$")
    message_type: Literal["NODE_READY"]
    message_id: StrictStr = Field(min_length=1, max_length=128)
    session_id: StrictStr = Field(min_length=1, max_length=128)
    node_id: StrictStr = Field(pattern=r"^ESP\d{2}$")
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
    node_id: StrictStr = Field(pattern=r"^ESP\d{2}$")
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


class EpochDeviceEvidence(StrictModel):
    session_id: StrictStr
    epoch_id: StrictInt
    device_token: StrictStr
    node_rssi_dbm: dict[str, float]
    node_packet_count: dict[str, int]
    nodes_observed: StrictInt = Field(ge=0)
    sniff_start_ms: StrictInt = Field(ge=0)
    sniff_end_ms: StrictInt = Field(gt=0)
