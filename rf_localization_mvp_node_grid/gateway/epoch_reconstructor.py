from __future__ import annotations

from dataclasses import dataclass, field
import time
from typing import Callable

from models import EpochObservation, ObservationItem


@dataclass
class EpochBucket:
    session_id: str
    epoch_id: int
    created_monotonic: float
    reports: dict[str, EpochObservation] = field(default_factory=dict)


class EpochReconstructor:
    def __init__(self, expected_nodes: set[str], timeout_ms: int, on_complete: Callable):
        self.expected_nodes = expected_nodes
        self.timeout_s = timeout_ms / 1000.0
        self.on_complete = on_complete
        self._buckets: dict[tuple[str, int], EpochBucket] = {}

    def add(self, report: EpochObservation) -> None:
        report.validate_epoch_window()
        key = (report.session_id, report.epoch_id)
        bucket = self._buckets.get(key)
        if bucket is None:
            bucket = EpochBucket(report.session_id, report.epoch_id, time.monotonic())
            self._buckets[key] = bucket
        bucket.reports[report.node_id] = report
        if self.expected_nodes.issubset(bucket.reports.keys()):
            self._emit(key, bucket)

    def flush_expired(self) -> None:
        now = time.monotonic()
        for key, bucket in list(self._buckets.items()):
            if now - bucket.created_monotonic >= self.timeout_s:
                self._emit(key, bucket)

    def _emit(self, key: tuple[str, int], bucket: EpochBucket) -> None:
        self._buckets.pop(key, None)
        merged: dict[str, dict[str, ObservationItem]] = {}
        sniff_start_ms = min(r.sniff_start_ms for r in bucket.reports.values())
        sniff_end_ms = max(r.sniff_end_ms for r in bucket.reports.values())

        for node_id, report in bucket.reports.items():
            for item in report.observations:
                merged.setdefault(item.device_token, {})[node_id] = item

        self.on_complete(
            bucket.session_id,
            bucket.epoch_id,
            merged,
            sniff_start_ms,
            sniff_end_ms,
        )
