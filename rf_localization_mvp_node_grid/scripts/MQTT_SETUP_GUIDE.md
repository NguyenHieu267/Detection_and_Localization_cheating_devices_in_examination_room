# RF Localization MVP — MQTT / Gateway Setup Guide

## 1. Architecture

Example development network:

    Oppo A58 hotspot/router
    10.32.4.169
             |
       +-----+-------------------+
       |                         |
    ESP01                     Gateway PC
    10.32.4.61                10.32.4.143
                                  |
                     +------------+------------+
                     |                         |
               Mosquitto MQTT             Python Gateway
                  TCP 1883                    :8000

The ESP32 MUST use the Gateway PC's LAN IPv4 address as `MQTT_HOST`.

The Python Gateway may use `127.0.0.1` because Mosquitto runs on the same PC.

---

## 2. Install Mosquitto once

Install the Windows x64 Mosquitto package.

After installation, the executable is normally:

    C:\Program Files\mosquitto\mosquitto.exe

Test it:

    & "C:\Program Files\mosquitto\mosquitto.exe" -h

If this works, Mosquitto is installed even if `mosquitto` is not in PATH.

---

## 3. Add the project broker config

Create:

    rf_localization_mvp_node_grid\mqtt\mosquitto.conf

Put exactly:

    listener 1883
    listener_allow_anonymous true
    socket_domain ipv4

For this isolated development PoC:

- `listener 1883` opens the MQTT listener.
- `listener_allow_anonymous true` allows the ESP32 and Python Gateway to connect without credentials.
- `socket_domain ipv4` keeps this setup on IPv4.

For production, use authentication and TLS instead of anonymous access.

---

## 4. Use only ONE Mosquitto broker

Windows may install Mosquitto as a service. During development, use the project broker manually so the configuration is predictable.

Open Administrator PowerShell:

    Get-Service *mosquitto*

If it is running:

    Stop-Service -Name mosquitto -Force

Then verify:

    netstat -ano | findstr :1883

There should be no listener before starting the project broker.

Do not run the Windows Mosquitto service and the project broker at the same time.

---

## 5. Find the Gateway PC IP

Connect the Gateway PC to the same hotspot/router as the ESP32.

Run:

    ipconfig

Example:

    IPv4 Address    : 10.32.4.143
    Default Gateway : 10.32.4.169

Use:

    10.32.4.143

as the MQTT server IP.

Do NOT use `10.32.4.169` as `MQTT_HOST`.

Do NOT use `127.0.0.1` in the ESP32.

---

## 6. ESP32 MQTT configuration

In every ESP32 `config.h`:

    #define WIFI_SSID "Oppo A58"
    #define WIFI_PASSWORD "dat2007a"

    #define MQTT_HOST "10.32.4.143"
    #define MQTT_PORT 1883

    #define MQTT_USERNAME ""
    #define MQTT_PASSWORD ""

Change only `MQTT_HOST` when the Gateway PC's IP changes.

Use:

    ESP01
    ESP02
    ESP03
    ESP04

for the four node IDs.

---

## 7. Start Mosquitto

From the project root, run:

    start_mqtt.bat

Or directly:

    "C:\Program Files\mosquitto\mosquitto.exe" -c "C:\path\to\rf_localization_mvp_node_grid\mqtt\mosquitto.conf" -v

Keep this terminal open.

---

## 8. Verify the MQTT listener

Open another PowerShell:

    netstat -ano | findstr :1883

You want a LAN/all-interface listener such as:

    TCP    0.0.0.0:1883    0.0.0.0:0    LISTENING

or:

    TCP    10.32.4.143:1883    0.0.0.0:0    LISTENING

If you only see:

    127.0.0.1:1883

the ESP32 cannot reach the broker.

---

## 9. Test the TCP connection

Run:

    Test-NetConnection 10.32.4.143 -Port 1883

You want:

    TcpTestSucceeded : True

If it is False, allow TCP 1883 through the Windows Firewall on the Private profile:

    New-NetFirewallRule `
        -DisplayName "RF Localization MQTT 1883" `
        -Direction Inbound `
        -Protocol TCP `
        -LocalPort 1883 `
        -Action Allow `
        -Profile Private

Then repeat the test.

---

## 10. Test MQTT messages

Open another terminal:

    "C:\Program Files\mosquitto\mosquitto_sub.exe" -h 10.32.4.143 -p 1883 -t "rf/v1/nodes/#" -v

Leave it running.

When ESP01 connects, you should see:

    rf/v1/nodes/ESP01/ready {...}

Then:

    rf/v1/nodes/ESP01/observation {...}

approximately once per epoch.

---

## 11. Start the Python Gateway

From:

    D:\my_project\DAKT\rf_localization_mvp_node_grid\gateway

run:

    python main.py

The Python Gateway can use:

    MQTT_HOST=127.0.0.1
    MQTT_PORT=1883

because Mosquitto and Python are on the same PC.

You should see:

    Starting RF Localization Gateway...
    Application startup complete.
    Uvicorn running on http://0.0.0.0:8000

---

## 12. Open the frontend

On the Gateway PC:

    http://localhost:8000/

From another computer on the same LAN:

    http://10.32.4.143:8000/

The Windows Firewall may need TCP 8000 allowed for remote browser access.

---

## 13. Recommended startup order

1. Connect the Gateway PC to the test Wi-Fi/hotspot.
2. Connect ESP32 nodes to the same Wi-Fi.
3. Start `start_mqtt.bat`.
4. Check `netstat -ano | findstr :1883`.
5. Optionally start `mosquitto_sub`.
6. Start `start_gateway.bat`.
7. Open `http://localhost:8000/`.
8. Power/reset the ESP32 nodes.
9. Confirm `[MQTT] Connected.` and `[MQTT] NODE_READY published`.
10. Confirm `rf/v1/nodes/ESPxx/ready`.
11. Confirm node status becomes ONLINE in the dashboard.

---

## 14. If the Gateway PC IP changes

Phone hotspots can assign a different IP after reconnecting.

If the Gateway PC changes from:

    10.32.4.143

to:

    10.32.4.120

update all ESP32 boards:

    #define MQTT_HOST "10.32.4.120"

The Python Gateway can stay on:

    127.0.0.1:1883

and the project Mosquitto config can stay on:

    listener 1883

---

## 15. Troubleshooting

### ESP32: `[MQTT] Failed rc=-2`

Check:

1. Mosquitto is running.
2. `MQTT_HOST` is the Gateway PC IP.
3. `netstat -ano | findstr :1883` shows a LAN/all-interface listener.
4. `Test-NetConnection <Gateway-IP> -Port 1883` succeeds.
5. Windows Firewall allows TCP 1883.
6. ESP32 and Gateway PC are on the same network.

### Mosquitto: `Only one usage of each socket address...`

Another Mosquitto process already owns port 1883.

Run:

    netstat -ano | findstr :1883

Stop the Windows Mosquitto service before starting the project broker.

### Mosquitto: `Starting in local only mode`

The broker was started without an effective network listener. Start it explicitly with:

    mosquitto.exe -c <project>\mqtt\mosquitto.conf -v

### Gateway says `Connecting MQTT to 127.0.0.1:1883`

That is correct when Mosquitto is on the same PC.

Do not change the ESP32 to `127.0.0.1`.

### Frontend works but ESP32 is OFFLINE

Check the ESP32 Serial Monitor first, then the `mosquitto_sub` terminal. This isolates MQTT from FastAPI.

---

## 16. Daily workflow

After the one-time installation:

Terminal 1:

    start_mqtt.bat

Terminal 2:

    start_gateway.bat

Then power/reset the ESP32 nodes.

Browser:

    http://localhost:8000/

Optional MQTT monitor:

    mosquitto_sub -h <Gateway-IP> -p 1883 -t "rf/v1/nodes/#" -v
