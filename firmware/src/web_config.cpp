#include <WiFi.h>
#include <WebServer.h>
#include <Preferences.h>
#include "web_config.h"

NodeConfig nodeConfig;
bool provisioning = false; // STA/AP Mode -> 
WebServer server(80);
uint32_t rebootAt = 0;
constexpr int CONFIG_SCHEMA = 1;

// Chang NodeConfig -> JSON to store in NVS
void toJson(JsonDocument &d, const NodeConfig &c, bool secrets) {
    d["config_schema"] = CONFIG_SCHEMA;
    d["node_id"] = c.nodeId;
    d["wifi_ssid"] = c.ssid;
    d["mqtt_host"] = c.mqttHost;
    d["mqtt_port"] = c.mqttPort;
    d["session_id"] = c.sessionId;
    d["target_mac"] = c.targetMac;
    d["capture_mode"] = c.captureMode;
    d["epoch_ms"] = c.epochMs;
    d["sniff_window_ms"] = c.sniffWindowMs;
    d["mqtt_batch_max_samples"] = c.batchSamples;
    d["mqtt_batch_max_bytes"] = c.batchBytes;
    if (secrets) {
        d["wifi_password"] = c.wifiPassword;
    }
}

// 
void apply(JsonDocument &d, NodeConfig &c) {
    c.nodeId = d["node_id"] | c.nodeId;
    c.ssid = d["wifi_ssid"] | c.ssid;
    c.wifiPassword = d["wifi_password"] | c.wifiPassword;
    c.mqttHost = d["mqtt_host"] | c.mqttHost;
    c.mqttPort = d["mqtt_port"] | c.mqttPort;
    c.sessionId = d["session_id"] | c.sessionId;
    c.targetMac = d["target_mac"] | c.targetMac;
    c.targetMac.toUpperCase();
    c.captureMode = d["capture_mode"] | c.captureMode;
    c.epochMs = d["epoch_ms"] | c.epochMs;
    c.sniffWindowMs = d["sniff_window_ms"] | c.sniffWindowMs;
    c.batchSamples = d["mqtt_batch_max_samples"] | c.batchSamples;
    c.batchBytes = d["mqtt_batch_max_bytes"] | c.batchBytes;
}

// Check if ESP32 reboot or not
bool requestAllowed() {
    if (rebootAt) {
        server.send(503, "application/json", "{\"error\":\"restart pending\"}");
        return false;
    }
    return true;
}

// Send JSON to Browse without cache
void sendJson(JsonDocument &d) {
    String out;
    serializeJson(d, out);
    server.sendHeader("Cache-Control", "no-store");
    server.send(200, "application/json", out);
}

// Read JSON that was sent 
bool body(JsonDocument &d) {
    if (!requestAllowed()) {
        return false;
    }
    const String origin = server.header("Origin");
    if ((!origin.isEmpty() && origin != "http://" + server.hostHeader()) ||
        !server.header("Content-Type").startsWith("application/json")) {
        server.send(403, "application/json", "{\"error\":\"invalid origin or content type\"}");
        return false;
    }
    if (deserializeJson(d, server.arg("plain")) || !d.is<JsonObject>()) {
        server.send(400, "application/json", "{\"error\":\"invalid JSON\"}");
        return false;
    }
    return true;
}

const char PAGE[] PROGMEM = R"HTML(
<!doctype html>
<html>
<head>
    <meta name="viewport" content="width=device-width">
    <title>RF node configuration</title>

    <style>
        body {
            font: 16px system-ui;
            max-width: 700px;
            margin: 30px auto;
            padding: 16px;
        }

        label {
            display: block;
            margin: 12px 0;
        }

        input,
        select {
            float: right;
            max-width: 60%;
        }

        button {
            margin: 8px;
            padding: 10px;
        }

        pre {
            white-space: pre-wrap;
        }
    </style>
</head>
<body>
    <h1>RF node</h1>

    <p>
        No web login is required. Anyone who can access this node can change its
        configuration. Leave the Wi-Fi password empty to keep its saved value;
        select open Wi-Fi to clear it.
    </p>

    <p>
        Use the same session and epoch on all four nodes. A phone MAC may be
        randomized. Capture all produces high traffic. HTTP is for a trusted LAN.
    </p>

    <form id="config"></form>

    <label>
        <input type="checkbox" id="openWifi">
        Open Wi-Fi (clear password)
    </label>

    <button onclick="save()">Save and restart</button>
    <button onclick="status()">Status</button>
    <button onclick="exportConfig()">Export without secrets</button>
    <button onclick="action('restart')">Restart</button>
    <button onclick="action('factory-reset')">Factory reset</button>

    <pre id="status"></pre>

    <script>
        const form = document.getElementById('config');
        const out = document.getElementById('status');

        async function call(path, data) {
            const r = await fetch(
                '/api/' + path,
                data
                    ? {
                        method: 'POST',
                        headers: {'Content-Type': 'application/json'},
                        body: JSON.stringify(data)
                    }
                    : {}
            );

            if (!r.ok) {
                throw Error('Request failed: ' + r.status);
            }

            return r.json();
        }

        async function init() {
            const c = await call('config');

            for (const [k, v] of Object.entries(c)) {
                if (k === 'config_schema') {
                    continue;
                }

                const l = document.createElement('label');
                l.textContent = k;

                const i = document.createElement('input');
                i.name = k;
                i.value = v;
                i.type = typeof v === 'number' ? 'number' : 'text';

                l.append(i);
                form.append(l);
            }

            const l = document.createElement('label');
            l.textContent = 'wifi_password';

            const i = document.createElement('input');
            i.type = 'password';
            i.autocomplete = 'new-password';
            i.name = 'wifi_password';

            l.append(i);
            form.append(l);

            await status();
        }

        async function status() {
            try {
                out.textContent = JSON.stringify(await call('status'), null, 2);
            } catch (e) {
                out.textContent = e.message;
            }
        }

        async function save() {
            const c = {};

            for (const i of form.elements) {
                if (i.type === 'password' && !i.value) {
                    continue;
                }

                c[i.name] = i.type === 'number' ? Number(i.value) : i.value;
            }

            if (document.getElementById('openWifi').checked) {
                c.wifi_password = '';
            }

            try {
                out.textContent = JSON.stringify(await call('config', c));
            } catch (e) {
                out.textContent = e.message;
            }
        }

        async function action(a) {
            if (!confirm(a + '?')) {
                return;
            }

            try {
                out.textContent = JSON.stringify(
                    await call(a, {confirm: a})
                );
            } catch (e) {
                out.textContent = e.message;
            }
        }

        async function exportConfig() {
            const c = await call('config');
            const a = document.createElement('a');
            const url = URL.createObjectURL(
                new Blob(
                    [JSON.stringify(c, null, 2)],
                    {type: 'application/json'}
                )
            );

            a.href = url;
            a.download = 'node-config.json';
            a.click();

            URL.revokeObjectURL(url);
        }

        init().catch(e => out.textContent = e.message);
    </script>
</body>
</html>
)HTML";

// Read data in NVS -> nodeConfig
void loadConfig() {
    Preferences p;
    if (!p.begin("rf-node", true)) {
        return;
    }
    String saved = p.getString("config", "");
    p.end();
    JsonDocument d;
    if (!deserializeJson(d, saved) && d["config_schema"] == CONFIG_SCHEMA) {
        apply(d, nodeConfig);
    }
}

// Delete config in NVS
void factoryResetConfig() {
    Preferences p;
    p.begin("rf-node", false);
    p.clear();
    p.end();
}

void startWebConfig() {
    const char *headers[] = {"Origin", "Content-Type"};
    server.collectHeaders(headers, 2);
    server.on("/", HTTP_GET, [] {
        if (requestAllowed()) {
            server.send_P(200, "text/html", PAGE);
        }
    });
    server.on("/api/config", HTTP_GET, [] {
        if (!requestAllowed()) {
            return;
        }
        JsonDocument d;
        toJson(d, nodeConfig, false);
        sendJson(d);
    });
    server.on("/api/status", HTTP_GET, [] {
        if (!requestAllowed()) {
            return;
        }
        JsonDocument d;
        fillStatus(d);
        sendJson(d);
    });
    server.on("/api/config", HTTP_POST, [] {
        JsonDocument d;
        if (!body(d)) {
            return;
        }
        NodeConfig candidate = nodeConfig;
        apply(d, candidate);
        JsonDocument saved;
        toJson(saved, candidate, true);
        String value;
        serializeJson(saved, value);
        Preferences p;
        if (!p.begin("rf-node", false)) {
            server.send(500, "application/json", "{\"error\":\"NVS unavailable\"}");
            return;
        }
        size_t written = p.putString("config", value);
        p.end();
        if (written != value.length()) {
            server.send(500, "application/json", "{\"error\":\"NVS save failed\"}");
            return;
        }
        server.send(200, "application/json", "{\"saved\":true,\"restarting\":true}");
        rebootAt = millis() + 1000;
    });
    server.on("/api/restart", HTTP_POST, [] {
        JsonDocument d;
        if (!body(d)) {
            return;
        }
        server.send(200, "application/json", "{\"restarting\":true}");
        rebootAt = millis() + 1000;
    });
    server.on("/api/factory-reset", HTTP_POST, [] {
        JsonDocument d;
        if (!body(d)) {
            return;
        }
        if (d["confirm"] != "factory-reset") {
            server.send(400, "application/json", "{\"error\":\"confirmation required\"}");
            return;
        }
        factoryResetConfig();
        server.send(200, "application/json", "{\"reset\":true}");
        rebootAt = millis() + 1000;
    });
    server.begin();
}

void serviceWebConfig() {
    server.handleClient();
    if (rebootAt && static_cast<int32_t>(millis() - rebootAt) >= 0) {
        ESP.restart();
    }
}
