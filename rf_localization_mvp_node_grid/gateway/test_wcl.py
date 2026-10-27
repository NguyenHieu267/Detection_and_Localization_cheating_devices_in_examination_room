"""Standalone WCL + automatic-grid smoke test.

Run from the project root:
    python -m gateway.test_wcl

Change WIDTH/LENGTH/RSSI below to test your room before connecting MQTT.
"""
from __future__ import annotations

from config import DEFAULT_SETTINGS, build_settings
from localization import WCLLocalizationEngine

WIDTH = 8.0
LENGTH = 12.0
RSSI = {
    "ESP01": -55.0,
    "ESP02": -63.0,
    "ESP03": -61.0,
    "ESP04": -69.0,
}


def main() -> None:
    settings = build_settings(WIDTH, LENGTH, base=DEFAULT_SETTINGS)
    engine = WCLLocalizationEngine(settings)
    result = engine.localize(RSSI)

    grid = settings.grid
    print("================================================")
    print("WCL standalone smoke test")
    print("================================================")
    print(f"Room             : {WIDTH:.2f} m x {LENGTH:.2f} m")
    print(f"Automatic grid   : {grid.nx} columns x {grid.ny} rows")
    print(f"Zone size        : {grid.cell_width_m:.2f} m x {grid.cell_length_m:.2f} m")
    print(f"RSSI vector      : {RSSI}")

    if result is None:
        print("Result           : no valid position")
        return

    print(f"Normalized (u,v) : ({result.u:.3f}, {result.v:.3f})")
    print(f"Physical (x,y)   : ({result.x_m:.3f}, {result.y_m:.3f}) m")
    print(f"Zone             : {result.zone_id}")
    print(f"Nodes used       : {result.nodes_used}/4")
    print(f"Confidence       : {result.confidence:.3f}")
    print(f"Weights          : {result.weights}")
    print("================================================")


if __name__ == "__main__":
    main()
