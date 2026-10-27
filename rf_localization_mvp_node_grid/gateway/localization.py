from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Iterable

from config import Settings


@dataclass(frozen=True)
class LocalizationEstimate:
    zone_id: str
    x_m: float
    y_m: float
    u: float
    v: float
    confidence: float
    nodes_used: int
    weights: dict[str, float]


class WCLLocalizationEngine:
    """The same relative-RSSI WCL calculation used by the original PoC.

    Weight_i = 10^((RSSI_i - strongest_RSSI) / 10)
    The strongest node therefore has weight 1.0 and all other weights are <= 1.
    """

    def __init__(self, settings: Settings):
        self.settings = settings
        self.positions = {
            "ESP01": (0.0, 0.0),
            "ESP02": (1.0, 0.0),
            "ESP03": (0.0, 1.0),
            "ESP04": (1.0, 1.0),
        }

    def localize(self, rssi_by_node: dict[str, float]) -> LocalizationEstimate | None:
        valid: dict[str, float] = {}
        for node_id, rssi in rssi_by_node.items():
            if node_id not in self.positions:
                continue
            try:
                value = float(rssi)
            except (TypeError, ValueError):
                continue
            if not math.isfinite(value) or not (-127.0 <= value <= 0.0):
                continue
            valid[node_id] = value

        if not valid:
            return None

        max_rssi = max(valid.values())
        weighted_u = 0.0
        weighted_v = 0.0
        total_weight = 0.0
        weights: dict[str, float] = {}

        for node_id, rssi in valid.items():
            weight = 10.0 ** ((rssi - max_rssi) / 10.0)
            u, v = self.positions[node_id]
            weighted_u += weight * u
            weighted_v += weight * v
            total_weight += weight
            weights[node_id] = weight

        if total_weight <= 0.0 or not math.isfinite(total_weight):
            return None

        u = max(0.0, min(1.0, weighted_u / total_weight))
        v = max(0.0, min(1.0, weighted_v / total_weight))
        x = u * self.settings.room_width_m
        y = v * self.settings.room_length_m
        zone_id = self.zone_id(x, y)
        confidence = self._confidence(valid, u, v)

        return LocalizationEstimate(
            zone_id=zone_id,
            x_m=x,
            y_m=y,
            u=u,
            v=v,
            confidence=confidence,
            nodes_used=len(valid),
            weights=weights,
        )

    def zone_id(self, x_m: float, y_m: float) -> str:
        grid = self.settings.grid
        eps = 1e-9
        col = min(
            grid.nx - 1,
            max(0, int(max(0.0, x_m - eps) / self.settings.grid_cell_width_m)),
        )
        row = min(
            grid.ny - 1,
            max(0, int(max(0.0, y_m - eps) / self.settings.grid_cell_length_m)),
        )
        return f"Z{row * grid.nx + col + 1}"

    def grid_payload(self) -> dict:
        grid = self.settings.grid
        zones = []
        for row in range(grid.ny):
            for col in range(grid.nx):
                x0 = col * self.settings.grid_cell_width_m
                y0 = row * self.settings.grid_cell_length_m
                zones.append(
                    {
                        "zone_id": f"Z{row * grid.nx + col + 1}",
                        "row": row,
                        "col": col,
                        "x0_m": x0,
                        "y0_m": y0,
                        "width_m": min(self.settings.grid_cell_width_m, self.settings.room_width_m - x0),
                        "length_m": min(self.settings.grid_cell_length_m, self.settings.room_length_m - y0),
                    }
                )
        return {
            "nx": grid.nx,
            "ny": grid.ny,
            "cell_width_m": self.settings.grid_cell_width_m,
            "cell_length_m": self.settings.grid_cell_length_m,
            "zones": zones,
        }

    def _confidence(self, valid: dict[str, float], u: float, v: float) -> float:
        """Simple deterministic confidence for the first visualization test."""
        node_factor = min(len(valid) / 4.0, 1.0)
        if len(valid) == 1:
            return 0.20

        # Prefer positions that are not strongly dominated by one node.
        normalized = []
        max_rssi = max(valid.values())
        for rssi in valid.values():
            normalized.append(10.0 ** ((rssi - max_rssi) / 10.0))
        weight_sum = sum(normalized)
        dominant = max(normalized) / weight_sum if weight_sum else 1.0
        balance = 1.0 - max(0.0, dominant - 0.25) / 0.75

        # Stay conservative when only two or three nodes are present.
        return max(0.0, min(1.0, 0.70 * node_factor + 0.30 * balance))
