# RF Localization MVP — first physical WCL zone test

This version is intentionally focused on the first physical experiment:

```text
Phone Wi-Fi traffic
      ↓
4 ESP32 nodes at room corners
      ↓
1-second RSSI aggregation per node
      ↓
MQTT EPOCH_OBSERVATION
      ↓
Python Gateway
      ↓
merge the same device across ESP01..ESP04
      ↓
YOUR EXISTING relative-RSSI WCL formula
      ↓
physical (x,y)
      ↓
automatically generated rectangular grid
      ↓
Zone ID + live room visualization
```

The first test uses **one target device**, but the firmware does not need to lock the radio to that MAC. It can keep observing all Wi-Fi devices; a publish-time target option can reduce the outgoing payload to the one phone for easier testing.

## 1. Automatic grid behavior

The Gateway asks only for:

- room width (m)
- room length (m)

It calculates the number of columns/rows automatically using a target zone size of 2 m wide × 4 m long by default.

For example:

```text
room = 8 m × 12 m

columns = ceil(8 / 2)  = 4
rows    = ceil(12 / 4) = 3

=> 4 columns × 3 rows = 12 zones
```

The final cells are resized equally so the complete room is covered exactly.

Change `TARGET_ZONE_WIDTH_M` and `TARGET_ZONE_LENGTH_M` if you want a different grid density.

## 2. Room coordinate system

```text
ESP01 (0,0)                 ESP02 (width,0)
       +-----+-----+-----+
       | Z1  | Z2  | Z3  | ...
       +-----+-----+-----+
       | ...              |
       +------------------+
ESP03 (0,length)            ESP04 (width,length)
```

The dashboard shows the four anchors and the latest estimated device position.

## 3. WCL used in this version

The Gateway intentionally matches the WCL implementation in the provided Member-A PoC:

```text
max_rssi = strongest observed RSSI
weight_i = 10^((rssi_i - max_rssi) / 10)

u = sum(weight_i * anchor_u_i) / sum(weight_i)
v = sum(weight_i * anchor_v_i) / sum(weight_i)

x = u * room_width
 y = v * room_length
```

Anchors are normalized:

```text
ESP01 = (0,0)
ESP02 = (1,0)
ESP03 = (0,1)
ESP04 = (1,1)
```

This makes this first test directly comparable with your existing WCL experiment.

## 4. Firmware target-device test

Edit `esp32_firmware/src/config.h`.

Set the same Wi-Fi, MQTT broker, token salt, and session ID on all boards. Change only `NODE_ID` per board.

For the first one-phone test:

```cpp
#define FOCUS_TARGET_MAC "AA:BB:CC:DD:EE:FF"
#define PUBLISH_ONLY_FOCUS_TARGET true
```

When you want the node to send every device again:

```cpp
#define PUBLISH_ONLY_FOCUS_TARGET false
```

The radio still passively captures frames; this setting only controls which aggregated device records are placed into the outgoing MQTT report.

**Important:** if the phone changes its Wi-Fi MAC because MAC randomization is active, the token will also change. A configured MAC is therefore suitable for a controlled PoC target only when the phone is using a stable MAC for the traffic you are measuring.

## 5. Run Gateway

```bash
python -m venv .venv

# Windows
.venv\\Scripts\\activate

# Linux/macOS
source .venv/bin/activate

pip install -r gateway/requirements.txt

# Optional: copy gateway/.env.example to gateway/.env and edit it.
# The Gateway loads gateway/.env automatically.

uvicorn gateway.main:app --host 0.0.0.0 --port 8000
```

Open:

```text
http://127.0.0.1:8000/
```

From another machine on the same LAN:

```text
http://<gateway-ip>:8000/
```

The dashboard lets you enter width/length and immediately redraws the room/grid.

## 6. Gateway target focus

The ESP32 already sends only `device_token`, not the raw MAC. To tell the Gateway which device to localize, set either:

```text
TARGET_DEVICE_MAC=AA:BB:CC:DD:EE:FF
```

or:

```text
TARGET_DEVICE_TOKEN=...
```

with:

```text
LOCALIZE_TARGET_ONLY=true
```

The Gateway still stores incoming evidence for all devices, but only the selected target produces `LOCALIZATION_UPDATE` records and WebSocket position messages.

For a simple first test, you can leave both target fields empty: when an epoch contains exactly one device, that single device is localized automatically. Once other Wi-Fi devices appear, configure `TARGET_DEVICE_MAC` or `TARGET_DEVICE_TOKEN` to identify the phone explicitly.

If `LOCALIZE_TARGET_ONLY=false`, every observed device is localized.

## 7. MQTT contract

Observation topic:

```text
rf/v1/nodes/{node_id}/observation
```

Required payload fields:

```json
{
  "schema_version": "1.0",
  "message_type": "EPOCH_OBSERVATION",
  "message_id": "...",
  "session_id": "...",
  "epoch_id": 123,
  "node_id": "ESP01",
  "sniff_start_ms": 123000,
  "sniff_end_ms": 124000,
  "observations": [
    {
      "device_token": "64 hex chars",
      "median_rssi_dbm": -52.0,
      "packet_count": 20,
      "mad_rssi_db": 1.5,
      "mean_rssi_dbm": -52.2,
      "min_rssi_dbm": -57.0,
      "max_rssi_dbm": -49.0
    }
  ]
}
```

Ready topic:

```text
rf/v1/nodes/{node_id}/ready
```

## 8. Test without ESP32

Set a target MAC in the Gateway environment matching the simulator's `SIM_DEVICE_MAC` and run:

```bash
python -m gateway.simulate_mqtt
```

The four synthetic reports reconstruct into one epoch and produce a WCL result.

## 9. ESP32 radio limitation

This MVP keeps the ESP32 connected as a Wi-Fi station so MQTT can operate. Promiscuous mode is enabled at the same time, but the radio remains constrained to the connected AP's channel; the first PoC should therefore use an AP/channel where the phone's traffic is actually present. Espressif documents sniffer operation in station mode and warns that it can affect station throughput. citeturn477758search25

This MVP is Wi-Fi sniffing only; BLE is intentionally not part of this first test.
