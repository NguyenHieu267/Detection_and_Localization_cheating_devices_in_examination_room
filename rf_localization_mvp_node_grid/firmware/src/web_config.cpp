#include <WiFi.h>
#include <WebServer.h>
#include <Preferences.h>
#include <cctype>
#include "web_config.h"

NodeConfig nodeConfig;
bool provisioning = false;
namespace {
WebServer server(80);
uint32_t rebootAt = 0;
constexpr int CONFIG_SCHEMA = 1;

void toJson(JsonDocument &d, const NodeConfig &c, bool secrets) {
  d["config_schema"] = CONFIG_SCHEMA;
  d["node_id"] = c.nodeId; d["wifi_ssid"] = c.ssid;
  d["mqtt_host"] = c.mqttHost; d["mqtt_port"] = c.mqttPort;
  d["session_id"] = c.sessionId; d["target_mac"] = c.targetMac;
  d["capture_mode"] = c.captureMode; d["epoch_ms"] = c.epochMs;
  d["sniff_window_ms"] = c.sniffWindowMs;
  d["mqtt_batch_max_samples"] = c.batchSamples; d["mqtt_batch_max_bytes"] = c.batchBytes;
  if (secrets) {
    d["wifi_password"] = c.wifiPassword; d["web_admin_password"] = c.adminPassword;
  }
}

bool validMac(const String &s) {
  if (s.length() != 17) return false;
  for (int i = 0; i < 17; ++i)
    if (i % 3 == 2 ? s[i] != ':' : !isxdigit(static_cast<unsigned char>(s[i]))) return false;
  unsigned first = strtoul(s.substring(0, 2).c_str(), nullptr, 16);
  return !(first & 1) && s != "00:00:00:00:00:00";
}

bool valid(const NodeConfig &c) {
  if (!(c.nodeId == "ESP01" || c.nodeId == "ESP02" || c.nodeId == "ESP03" || c.nodeId == "ESP04")) return false;
  if (c.ssid.isEmpty() || c.ssid.length() > 32 || c.wifiPassword.length() > 63 ||
      (!c.wifiPassword.isEmpty() && c.wifiPassword.length() < 8)) return false;
  if (c.mqttHost.isEmpty() || c.mqttHost.length() > 128 || !c.mqttPort) return false;
  for (char x : c.mqttHost) if (!(isalnum(static_cast<unsigned char>(x)) || x == '.' || x == '-')) return false;
  if (c.sessionId.isEmpty() || c.sessionId.length() > 64) return false;
  for (char x : c.sessionId) if (!(isalnum(static_cast<unsigned char>(x)) || x == '-' || x == '_' || x == '.')) return false;
  if (c.captureMode != "target_only" && c.captureMode != "all") return false;
  if ((c.captureMode == "target_only" || !c.targetMac.isEmpty()) && !validMac(c.targetMac)) return false;
  return c.epochMs >= 100 && c.epochMs <= 60000 && c.sniffWindowMs >= 50 && c.sniffWindowMs <= c.epochMs &&
         c.batchSamples >= 1 && c.batchSamples <= 64 && c.batchBytes >= 1024 && c.batchBytes <= 8192 &&
         c.adminPassword.length() >= 8 && c.adminPassword.length() <= 64;
}

bool apply(JsonDocument &d, NodeConfig &c) {
  const char *strings[] = {"node_id", "wifi_ssid", "wifi_password", "mqtt_host", "session_id", "target_mac", "capture_mode", "web_admin_password"};
  for (const char *key : strings) if (!d[key].isNull() && !d[key].is<String>()) return false;
  const char *numbers[] = {"mqtt_port", "epoch_ms", "sniff_window_ms", "mqtt_batch_max_samples", "mqtt_batch_max_bytes"};
  for (const char *key : numbers) if (!d[key].isNull() && (!d[key].is<uint32_t>() || d[key].as<uint32_t>() > 65535)) return false;
  c.nodeId = d["node_id"] | c.nodeId; c.ssid = d["wifi_ssid"] | c.ssid;
  c.wifiPassword = d["wifi_password"] | c.wifiPassword;
  c.mqttHost = d["mqtt_host"] | c.mqttHost; c.mqttPort = d["mqtt_port"] | c.mqttPort;
  c.sessionId = d["session_id"] | c.sessionId; c.targetMac = d["target_mac"] | c.targetMac;
  c.targetMac.toUpperCase(); c.captureMode = d["capture_mode"] | c.captureMode;
  c.epochMs = d["epoch_ms"] | c.epochMs; c.sniffWindowMs = d["sniff_window_ms"] | c.sniffWindowMs;
  c.batchSamples = d["mqtt_batch_max_samples"] | c.batchSamples; c.batchBytes = d["mqtt_batch_max_bytes"] | c.batchBytes;
  c.adminPassword = d["web_admin_password"] | c.adminPassword;
  return valid(c);
}

bool authorized() {
  if (rebootAt) { server.send(503, "application/json", "{\"error\":\"restart pending\"}"); return false; }
  // First-use bootstrap is available only on the provisioning AP, before an admin exists.
  if (provisioning && nodeConfig.adminPassword.isEmpty()) return true;
  if (server.authenticate("admin", nodeConfig.adminPassword.c_str())) return true;
  server.requestAuthentication(); return false;
}

void sendJson(JsonDocument &d) {
  String out; serializeJson(d, out);
  server.sendHeader("Cache-Control", "no-store"); server.send(200, "application/json", out);
}

bool body(JsonDocument &d) {
  if (!authorized()) return false;
  const String origin = server.header("Origin");
  if ((!origin.isEmpty() && origin != "http://" + server.hostHeader()) ||
      !server.header("Content-Type").startsWith("application/json")) {
    server.send(403, "application/json", "{\"error\":\"invalid origin or content type\"}"); return false;
  }
  if (server.arg("plain").length() > 2048 || deserializeJson(d, server.arg("plain")) || !d.is<JsonObject>()) {
    server.send(400, "application/json", "{\"error\":\"invalid JSON\"}"); return false;
  }
  return true;
}

const char PAGE[] PROGMEM = R"HTML(<!doctype html><html><meta name="viewport" content="width=device-width"><title>RF node configuration</title>
<style>body{font:16px system-ui;max-width:700px;margin:30px auto;padding:16px}label{display:block;margin:12px 0}input,select{float:right;max-width:60%}button{margin:8px;padding:10px}pre{white-space:pre-wrap}</style>
<h1>RF node</h1><p>STA: sign in as <b>admin</b>. First provisioning: set an admin password. Empty password inputs keep the saved values; select open Wi-Fi to clear its password.</p>
<p>Use the same session and epoch on all four nodes. A phone MAC may be randomized. Capture all produces high traffic. HTTP is for a trusted LAN.</p>
<form id="config"></form><label><input type="checkbox" id="openWifi">Open Wi-Fi (clear password)</label><button onclick="save()">Save and restart</button><button onclick="status()">Status</button><button onclick="exportConfig()">Export without secrets</button><button onclick="action('restart')">Restart</button><button onclick="action('factory-reset')">Factory reset</button><pre id="status"></pre>
<script>
const form=document.getElementById('config'),out=document.getElementById('status');
async function call(path, data){const r=await fetch('/api/'+path,data?{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(data)}:{});if(!r.ok)throw Error('Request failed: '+r.status);return r.json()}
async function init(){const c=await call('config');for(const [k,v] of Object.entries(c)){if(k==='config_schema')continue;const l=document.createElement('label');l.textContent=k;const i=document.createElement('input');i.name=k;i.value=v;i.type=typeof v==='number'?'number':'text';l.append(i);form.append(l)}for(const k of ['wifi_password','web_admin_password']){const l=document.createElement('label');l.textContent=k;const i=document.createElement('input');i.type='password';i.autocomplete='new-password';i.name=k;l.append(i);form.append(l)}await status()}
async function status(){try{out.textContent=JSON.stringify(await call('status'),null,2)}catch(e){out.textContent=e.message}}
async function save(){const c={};for(const i of form.elements){if(i.type==='password'&&!i.value)continue;c[i.name]=i.type==='number'?Number(i.value):i.value}if(document.getElementById('openWifi').checked)c.wifi_password='';try{out.textContent=JSON.stringify(await call('config',c))}catch(e){out.textContent=e.message}}
async function action(a){if(!confirm(a+'?'))return;try{out.textContent=JSON.stringify(await call(a,{confirm:a}))}catch(e){out.textContent=e.message}}
async function exportConfig(){const c=await call('config'),a=document.createElement('a');const url=URL.createObjectURL(new Blob([JSON.stringify(c,null,2)],{type:'application/json'}));a.href=url;a.download='node-config.json';a.click();URL.revokeObjectURL(url)}
init().catch(e=>out.textContent=e.message);
</script></html>)HTML";
}

void loadConfig() {
  Preferences p;
  if (!p.begin("rf-node", true)) return;
  String saved = p.getString("config", ""); p.end();
  JsonDocument d;
  NodeConfig candidate;
  if (!deserializeJson(d, saved) && d["config_schema"] == CONFIG_SCHEMA && apply(d, candidate)) nodeConfig = candidate;
}

void factoryResetConfig() {
  Preferences p; p.begin("rf-node", false); p.clear(); p.end();
}

void startWebConfig() {
  const char *headers[] = {"Origin", "Content-Type"}; server.collectHeaders(headers, 2);
  server.on("/", HTTP_GET, [] { if (authorized()) server.send_P(200, "text/html", PAGE); });
  server.on("/api/config", HTTP_GET, [] { if (!authorized()) return; JsonDocument d; toJson(d, nodeConfig, false); sendJson(d); });
  server.on("/api/status", HTTP_GET, [] { if (!authorized()) return; JsonDocument d; fillStatus(d); sendJson(d); });
  server.on("/api/config", HTTP_POST, [] {
    JsonDocument d; if (!body(d)) return;
    NodeConfig candidate = nodeConfig;
    if (!apply(d, candidate)) { server.send(400, "application/json", "{\"error\":\"invalid configuration\"}"); return; }
    JsonDocument saved; toJson(saved, candidate, true); String value; serializeJson(saved, value);
    Preferences p;
    if (!p.begin("rf-node", false)) { server.send(500, "application/json", "{\"error\":\"NVS unavailable\"}"); return; }
    size_t written = p.putString("config", value); p.end();
    if (written != value.length()) { server.send(500, "application/json", "{\"error\":\"NVS save failed\"}"); return; }
    server.send(200, "application/json", "{\"saved\":true,\"restarting\":true}"); rebootAt = millis() + 1000;
  });
  server.on("/api/restart", HTTP_POST, [] { JsonDocument d; if (!body(d)) return; server.send(200,"application/json","{\"restarting\":true}"); rebootAt = millis() + 1000; });
  server.on("/api/factory-reset", HTTP_POST, [] {
    JsonDocument d; if (!body(d)) return;
    if (d["confirm"] != "factory-reset") { server.send(400,"application/json","{\"error\":\"confirmation required\"}"); return; }
    factoryResetConfig(); server.send(200,"application/json","{\"reset\":true}"); rebootAt = millis() + 1000;
  });
  server.begin();
}

void serviceWebConfig() {
  server.handleClient();
  if (rebootAt && static_cast<int32_t>(millis() - rebootAt) >= 0) ESP.restart();
}
