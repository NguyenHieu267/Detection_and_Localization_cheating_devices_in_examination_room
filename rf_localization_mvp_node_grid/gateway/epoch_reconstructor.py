from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
import statistics
import threading
import time
from typing import Callable

from models import EpochObservation, ObservationItem, RawEpochObservation


@dataclass
class EpochBucket:
    session_id: str
    epoch_id: int
    created_monotonic: float
    chunks: dict[str, dict[int, RawEpochObservation]] = field(default_factory=dict)
    legacy: dict[str, EpochObservation] = field(default_factory=dict)


class EpochReconstructor:
    """MQTT ingestion and asyncio expiry share one lock; never emit an epoch twice."""

    def __init__(self, expected_nodes: set[str], timeout_ms: int, on_complete: Callable,
                 min_overlap_ms: int = 600, on_raw=None, on_quality=None, is_closed=None):
        self.expected_nodes = expected_nodes
        self.timeout_s = timeout_ms / 1000.0
        self.on_complete = on_complete
        self.min_overlap_ms = min_overlap_ms
        self.on_raw = on_raw
        self.on_quality = on_quality
        self.is_closed = is_closed
        self._buckets = {}
        self._closed = set()
        self._floor = {}
        self._lock = threading.RLock()
        self.counters = Counter()
        self.last_quality = {}

    def add(self, report: RawEpochObservation | EpochObservation) -> str:
        with self._lock:
            if report.node_id not in self.expected_nodes:
                raise ValueError("unknown node")
            if isinstance(report, EpochObservation):
                report.validate_epoch_window()
            key = (report.session_id, report.epoch_id)
            if key in self._closed or report.epoch_id <= self._floor.get(report.session_id, -1) or (self.is_closed and self.is_closed(*key)):
                self.counters["late"] += 1
                return "late"
            bucket = self._buckets.get(key)
            if bucket and time.monotonic() - bucket.created_monotonic >= self.timeout_s:
                self._emit(key, bucket)
                self.counters["late"] += 1
                return "late"
            if bucket is None:
                if len(self._buckets) >= 256:
                    oldest = next(iter(self._buckets))
                    self._emit(oldest, self._buckets[oldest])
                bucket = EpochBucket(*key, time.monotonic())
                self._buckets[key] = bucket
            if isinstance(report, EpochObservation):
                if report.node_id in bucket.legacy or report.node_id in bucket.chunks:
                    self.counters["duplicate"] += 1
                    return "duplicate"
                bucket.legacy[report.node_id] = report
            else:
                if report.node_id in bucket.legacy:
                    raise ValueError("cannot mix schemas in one node epoch")
                chunks = bucket.chunks.setdefault(report.node_id, {})
                if report.chunk_index in chunks:
                    if chunks[report.chunk_index] != report:
                        raise ValueError("conflicting duplicate chunk")
                    self.counters["duplicate"] += 1
                    return "duplicate"
                if sum(len(c.observations) for c in chunks.values()) + len(report.observations) > 8192:
                    raise ValueError("node epoch exceeds sample limit")
                if chunks:
                    first = next(iter(chunks.values()))
                    fields = ("chunk_count", "epoch_ms", "clock_synced", "sniff_start_unix_ms", "sniff_end_unix_ms")
                    if any(getattr(first, f) != getattr(report, f) for f in fields):
                        raise ValueError("inconsistent chunk metadata")
                if self.on_raw:
                    self.on_raw(report)
                chunks[report.chunk_index] = report
            complete_nodes = set(bucket.legacy) | {
                node for node, chunks in bucket.chunks.items()
                if len(chunks) == next(iter(chunks.values())).chunk_count
            }
            if self.expected_nodes <= complete_nodes:
                self._emit(key, bucket)
            return "accepted"

    def flush_expired(self) -> None:
        with self._lock:
            for key, bucket in list(self._buckets.items()):
                if time.monotonic() - bucket.created_monotonic >= self.timeout_s:
                    self._emit(key, bucket)

    def _emit(self, key, bucket):
        self._buckets.pop(key, None)
        self._closed.add(key)
        if len(self._closed) > 4096:
            oldest = min(self._closed, key=lambda k: k[1])
            self._closed.remove(oldest)
            self._floor[oldest[0]] = max(self._floor.get(oldest[0], -1), oldest[1])
        merged = {}
        bucket.chunks = {node: chunks for node, chunks in bucket.chunks.items() if chunks}
        firsts = {node: next(iter(chunks.values())) for node, chunks in bucket.chunks.items()}
        present = set(firsts) | set(bucket.legacy)
        missing_chunk = any(len(chunks) != firsts[node].chunk_count for node, chunks in bucket.chunks.items())
        synced = not bucket.legacy and all(r.clock_synced for r in firsts.values())
        start = max((r.sniff_start_unix_ms for r in firsts.values()), default=0)
        end = min((r.sniff_end_unix_ms for r in firsts.values()), default=0)
        overlap = max(0, end - start)
        epoch_lengths = {r.epoch_ms for r in firsts.values()}
        for node, chunks in bucket.chunks.items():
            if len(chunks) != firsts[node].chunk_count:
                continue
            samples = {}
            for chunk in chunks.values():
                for item in chunk.observations:
                    samples.setdefault(item.device_token, []).append(item.rssi_dbm)
            for token, values in samples.items():
                med = statistics.median(values)
                merged.setdefault(token, {})[node] = ObservationItem(
                    device_token=token, median_rssi_dbm=med, packet_count=len(values),
                    mad_rssi_db=statistics.median(abs(v - med) for v in values),
                    mean_rssi_dbm=statistics.mean(values), min_rssi_dbm=min(values), max_rssi_dbm=max(values))
        for node, report in bucket.legacy.items():
            for item in report.observations:
                merged.setdefault(item.device_token, {})[node] = item
        if missing_chunk:
            status = "missing_chunk"
        elif present != self.expected_nodes:
            status = "missing_node"
        elif not synced:
            status = "unsynced"
        elif len(epoch_lengths) != 1 or overlap < self.min_overlap_ms:
            status = "low_overlap"
        elif not merged:
            status = "empty_target"
        else:
            status = "complete"
        tokens = {token: "complete" if status == "complete" and set(items) == self.expected_nodes
                  else ("missing_node" if status == "complete" else status) for token, items in merged.items()}
        if status == "complete" and "complete" not in tokens.values():
            status = "missing_node"
        quality = {"session_id": key[0], "epoch_id": key[1], "status": status,
                   "overlap_ms": overlap, "tokens": tokens,
                   "queue_drop_count": {n: max(r.queue_drop_count for r in chunks.values()) for n, chunks in bucket.chunks.items()},
                   "node_stats": {token: {n: item.model_dump() for n, item in items.items()} for token, items in merged.items()},
                   "node_diagnostics": {n: {"clock_synced": r.clock_synced,
                       "sniff_start_unix_ms": r.sniff_start_unix_ms, "sniff_end_unix_ms": r.sniff_end_unix_ms,
                       "batch_drop_count": max(c.batch_drop_count for c in bucket.chunks[n].values()),
                       "mqtt_publish_fail_count": max(c.mqtt_publish_fail_count for c in bucket.chunks[n].values())} for n, r in firsts.items()}}
        self.last_quality = quality
        self.counters[status] += 1
        if self.on_quality:
            self.on_quality(quality)
        valid = {t: items for t, items in merged.items() if tokens[t] == "complete"}
        if valid:
            self.on_complete(*key, valid, start, end)
