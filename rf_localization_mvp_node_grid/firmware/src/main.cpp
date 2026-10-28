#include <Arduino.h>
#include <WiFi.h>
#include <PubSubClient.h>
#include <ArduinoJson.h>
#include <esp_wifi.h>
#include <esp_system.h>
#include <mbedtls/sha256.h>
#include <sys/time.h>
#include <time.h>
#include <cstring>
#include <cmath>

#include "config.h"

#ifndef SNIFF_WINDOW_MS
#define SNIFF_WINDOW_MS 700ULL
#endif

namespace {

WiFiClient wifiClient;
PubSubClient mqttClient(wifiClient);
QueueHandle_t observationQueue = nullptr;

uint8_t nodeMac[6] = {0};
uint8_t uplinkBssid[6] = {0};
bool haveUplinkBssid = false;

uint8_t focusTargetMac[6] = {0};
bool haveFocusTargetMac = false;

int64_t unixOffsetMs = 0;
uint64_t activeEpochId = 0;
uint64_t sniffStartMs = 0;
uint32_t messageCounter = 0;
unsigned long lastWifiAttemptMs = 0;
unsigned long lastMqttAttemptMs = 0;

bool clockSynced = false;
bool promiscuousEnabled = false;
bool epochInitialized = false;
uint64_t nextEpochStartMs = 0;
uint64_t sniffEndMs = 0;
uint32_t queueDropCount = 0;

// Keep the MQTT serialization buffer OUT of the Arduino loop task stack.
// A 16384-byte local array can overflow the ESP32 loop task stack.
static char mqttPayload[4096];

struct RawObservation {
  uint8_t mac[6];
  int8_t rssi;
  uint8_t channel;
};

struct DeviceAccumulator {
  bool active = false;
  uint8_t mac[6] = {0};
  uint32_t packetCount = 0;
  uint16_t sampleCount = 0;
  int8_t samples[MAX_SAMPLES_PER_DEVICE] = {0};
};

DeviceAccumulator accumulators[MAX_DEVICES_PER_EPOCH];

String macToString(const uint8_t mac[6]) {
  char out[18];
  snprintf(out, sizeof(out), "%02X:%02X:%02X:%02X:%02X:%02X",
           mac[0], mac[1], mac[2], mac[3], mac[4], mac[5]);
  return String(out);
}

bool macEquals(const uint8_t a[6], const uint8_t b[6]) { return memcmp(a, b, 6) == 0; }
bool isUnicastMac(const uint8_t mac[6]) { return (mac[0] & 0x01U) == 0; }

bool parseMac(const char *text, uint8_t out[6]) {
  if (text == nullptr || strlen(text) == 0) return false;
  unsigned int b[6];
  if (sscanf(text, "%2x:%2x:%2x:%2x:%2x:%2x", &b[0], &b[1], &b[2], &b[3], &b[4], &b[5]) != 6 &&
      sscanf(text, "%2x-%2x-%2x-%2x-%2x-%2x", &b[0], &b[1], &b[2], &b[3], &b[4], &b[5]) != 6) {
    return false;
  }
  for (int i = 0; i < 6; ++i) out[i] = static_cast<uint8_t>(b[i]);
  return true;
}

uint64_t unixMs() {
  return static_cast<uint64_t>(static_cast<int64_t>(unixOffsetMs) + millis());
}

String makeMessageId(uint64_t epochId) {
  ++messageCounter;
  char id[128];
  snprintf(id, sizeof(id), "%s-%llu-%lu-%08lX",
           NODE_ID, (unsigned long long)epochId,
           (unsigned long)messageCounter, (unsigned long)esp_random());
  return String(id);
}

String makeDeviceToken(const uint8_t mac[6]) {
  uint8_t digest[32];
  mbedtls_sha256_context ctx;
  mbedtls_sha256_init(&ctx);
  mbedtls_sha256_starts_ret(&ctx, 0);
  mbedtls_sha256_update_ret(&ctx, mac, 6);
  mbedtls_sha256_update_ret(
      &ctx,
      reinterpret_cast<const unsigned char *>(DEVICE_TOKEN_SALT),
      strlen(DEVICE_TOKEN_SALT));
  mbedtls_sha256_finish_ret(&ctx, digest);
  mbedtls_sha256_free(&ctx);

  char hex[65];
  for (size_t i = 0; i < sizeof(digest); ++i) snprintf(&hex[i * 2], 3, "%02x", digest[i]);
  hex[64] = '\0';
  return String(hex);
}

void syncClock() {
  configTime(0, 0, NTP_SERVER_1, NTP_SERVER_2);
  Serial.println("[TIME] Waiting for NTP sync...");
  for (int i = 0; i < 40; ++i) {
    struct timeval tv;
    gettimeofday(&tv, nullptr);
    if (tv.tv_sec >= 1700000000) {
      uint64_t nowUnix = static_cast<uint64_t>(tv.tv_sec) * 1000ULL + static_cast<uint64_t>(tv.tv_usec) / 1000ULL;
      unixOffsetMs = static_cast<int64_t>(nowUnix) - static_cast<int64_t>(millis());
      Serial.printf("[TIME] Synced. unix_ms=%llu\n", (unsigned long long)nowUnix);
      return;
    }
    delay(500);
  }
  unixOffsetMs = 0;
  Serial.println("[TIME] WARNING: NTP sync failed; using millis().");
}

// -----------------------------------------------------------------------------
// Promiscuous capture
// -----------------------------------------------------------------------------
void IRAM_ATTR promiscuousCallback(void *buf, wifi_promiscuous_pkt_type_t type) {
  if (buf == nullptr || observationQueue == nullptr) return;
  if (type != WIFI_PKT_MGMT && type != WIFI_PKT_DATA) return;

  wifi_promiscuous_pkt_t *packet = reinterpret_cast<wifi_promiscuous_pkt_t *>(buf);
  if (packet->rx_ctrl.sig_len < 24) return;

  const uint8_t *frame = packet->payload;
  const uint8_t frameType = (frame[0] >> 2) & 0x03U;
  if (frameType == 1) return; // Control.

  const uint8_t *src = frame + 10; // 802.11 transmitter address for normal frames.
  if (!isUnicastMac(src)) return;
  if (macEquals(src, nodeMac)) return;

  // Normally ignore the Wi-Fi AP's own BSSID to reduce noise.
  // However, during the focused-phone test the phone may itself be
  // the hotspot/AP, so allow that MAC through when it is the focus target.
  const bool isFocusTarget =
      haveFocusTargetMac &&
      macEquals(src, focusTargetMac);

  // First-test optimization: when focus-only publishing is enabled, also
  // filter at the callback so high-volume unrelated traffic never enters
  // the application queue. This greatly reduces load on the Wi-Fi driver.
  if (PUBLISH_ONLY_FOCUS_TARGET && haveFocusTargetMac && !isFocusTarget) return;

  // Ignore the connected AP unless it is explicitly the focus target.
  if (haveUplinkBssid && macEquals(src, uplinkBssid) && !isFocusTarget) return;

  RawObservation obs;
  memcpy(obs.mac, src, 6);
  obs.rssi = packet->rx_ctrl.rssi;
  obs.channel = packet->rx_ctrl.channel;
  if (xQueueSend(observationQueue, &obs, 0) != pdTRUE) {
    ++queueDropCount;
  }
}

void resetAccumulators() { memset(accumulators, 0, sizeof(accumulators)); }

DeviceAccumulator *findAccumulator(const uint8_t mac[6]) {
  DeviceAccumulator *freeSlot = nullptr;
  for (size_t i = 0; i < MAX_DEVICES_PER_EPOCH; ++i) {
    if (accumulators[i].active) {
      if (macEquals(accumulators[i].mac, mac)) return &accumulators[i];
    } else if (freeSlot == nullptr) {
      freeSlot = &accumulators[i];
    }
  }
  if (freeSlot != nullptr) {
    freeSlot->active = true;
    memcpy(freeSlot->mac, mac, 6);
  }
  return freeSlot;
}

size_t drainObservationQueue(size_t maxItems = 64) {
  RawObservation obs;
  size_t processed = 0;
  while (processed < maxItems && xQueueReceive(observationQueue, &obs, 0) == pdTRUE) {
    DeviceAccumulator *acc = findAccumulator(obs.mac);
    if (acc != nullptr) {
      ++acc->packetCount;
      if (acc->sampleCount < MAX_SAMPLES_PER_DEVICE) {
        acc->samples[acc->sampleCount++] = obs.rssi;
      } else {
        const uint16_t slot = acc->packetCount % MAX_SAMPLES_PER_DEVICE;
        acc->samples[slot] = obs.rssi;
      }
    }
    ++processed;
    if ((processed % 16) == 0) {
      taskYIELD();
    }
  }
  return processed;
}

float medianOf(int8_t *values, size_t count) {
  if (count == 0) return NAN;
  for (size_t i = 1; i < count; ++i) {
    int8_t key = values[i];
    size_t j = i;
    while (j > 0 && values[j - 1] > key) {
      values[j] = values[j - 1];
      --j;
    }
    values[j] = key;
  }
  if (count & 1U) return static_cast<float>(values[count / 2]);
  return 0.5f * static_cast<float>(values[count / 2 - 1] + values[count / 2]);
}

void computeStats(const DeviceAccumulator &acc, float &median, float &mean,
                  float &mad, float &minRssi, float &maxRssi) {
  int8_t values[MAX_SAMPLES_PER_DEVICE];
  memcpy(values, acc.samples, acc.sampleCount * sizeof(int8_t));
  median = medianOf(values, acc.sampleCount);

  float sum = 0.0f;
  minRssi = 127.0f;
  maxRssi = -127.0f;
  for (uint16_t i = 0; i < acc.sampleCount; ++i) {
    float v = static_cast<float>(acc.samples[i]);
    sum += v;
    minRssi = min(minRssi, v);
    maxRssi = max(maxRssi, v);
  }
  mean = sum / static_cast<float>(acc.sampleCount);

  float absDev[MAX_SAMPLES_PER_DEVICE];
  for (uint16_t i = 0; i < acc.sampleCount; ++i) {
    absDev[i] = fabsf(static_cast<float>(acc.samples[i]) - median);
  }
  for (uint16_t i = 1; i < acc.sampleCount; ++i) {
    float key = absDev[i];
    uint16_t j = i;
    while (j > 0 && absDev[j - 1] > key) {
      absDev[j] = absDev[j - 1];
      --j;
    }
    absDev[j] = key;
  }
  mad = (acc.sampleCount & 1U)
      ? absDev[acc.sampleCount / 2]
      : 0.5f * (absDev[acc.sampleCount / 2 - 1] + absDev[acc.sampleCount / 2]);
}

// -----------------------------------------------------------------------------
// MQTT
// -----------------------------------------------------------------------------
bool publishReady() {
  if (!mqttClient.connected()) return false;
  JsonDocument doc;
  doc["schema_version"] = "1.0";
  doc["message_type"] = "NODE_READY";
  doc["message_id"] = makeMessageId(activeEpochId);
  doc["session_id"] = SESSION_ID;
  doc["node_id"] = NODE_ID;
  doc["timestamp_ms"] = unixMs();
  char payload[768];
  size_t len = serializeJson(doc, payload, sizeof(payload));
  String topic = String("rf/v1/nodes/") + NODE_ID + "/ready";
  bool ok = mqttClient.publish(
      topic.c_str(),
      reinterpret_cast<const uint8_t *>(payload),
      len,
      false
  );
  Serial.printf("[MQTT] NODE_READY %s\n", ok ? "published" : "FAILED");
  return ok;
}

bool publishEpochObservation(uint64_t epochId, uint64_t epochStartMs, uint64_t epochEndMs) {
  if (!mqttClient.connected()) return false;

  Serial.printf("[MQTT] Building epoch %llu JSON...\n", (unsigned long long)epochId);
  JsonDocument doc;
  doc["schema_version"] = "1.0";
  doc["message_type"] = "EPOCH_OBSERVATION";
  doc["message_id"] = makeMessageId(epochId);
  doc["session_id"] = SESSION_ID;
  doc["epoch_id"] = epochId;
  doc["node_id"] = NODE_ID;
  doc["sniff_start_ms"] = epochStartMs;
  doc["sniff_end_ms"] = epochEndMs;
  JsonArray observations = doc["observations"].to<JsonArray>();

  // This loop still processes every captured device. The optional filter only
  // decides which device records are placed into the outgoing MQTT report.
  for (size_t i = 0; i < MAX_DEVICES_PER_EPOCH; ++i) {
    const DeviceAccumulator &acc = accumulators[i];
    if (!acc.active || acc.sampleCount == 0) continue;
    if (PUBLISH_ONLY_FOCUS_TARGET && haveFocusTargetMac && !macEquals(acc.mac, focusTargetMac)) continue;

    float median, mean, mad, minRssi, maxRssi;
    computeStats(acc, median, mean, mad, minRssi, maxRssi);
    JsonObject item = observations.add<JsonObject>();
    item["device_token"] = makeDeviceToken(acc.mac);
    item["median_rssi_dbm"] = roundf(median * 100.0f) / 100.0f;
    item["packet_count"] = acc.packetCount;
    item["mad_rssi_db"] = roundf(mad * 100.0f) / 100.0f;
    item["mean_rssi_dbm"] = roundf(mean * 100.0f) / 100.0f;
    item["min_rssi_dbm"] = roundf(minRssi * 100.0f) / 100.0f;
    item["max_rssi_dbm"] = roundf(maxRssi * 100.0f) / 100.0f;
  }

  size_t len = serializeJson(doc, mqttPayload, sizeof(mqttPayload));
  if (len == 0 || len >= sizeof(mqttPayload)) {
    Serial.println("[MQTT] EPOCH serialization FAILED (buffer too small)");
    return false;
  }
  String topic = String("rf/v1/nodes/") + NODE_ID + "/observation";
  bool ok = mqttClient.publish(topic.c_str(), reinterpret_cast<const uint8_t*>(mqttPayload), len, false);
  Serial.printf("[MQTT] EPOCH %llu -> %u bytes, %s\n",
                (unsigned long long)epochId, static_cast<unsigned>(len), ok ? "published" : "FAILED");
  return ok;
}

// -----------------------------------------------------------------------------
// Connectivity
// -----------------------------------------------------------------------------
void connectWifiIfNeeded() {
  if (WiFi.status() == WL_CONNECTED) return;
  if (millis() - lastWifiAttemptMs < WIFI_RECONNECT_MS) return;
  lastWifiAttemptMs = millis();

  WiFi.mode(WIFI_STA);
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
  Serial.printf("[WIFI] Connecting to %s...\n", WIFI_SSID);

  unsigned long start = millis();
  while (WiFi.status() != WL_CONNECTED && millis() - start < 15000) {
    delay(250);
    Serial.print('.');
  }
  Serial.println();

  if (WiFi.status() != WL_CONNECTED) {
    Serial.println("[WIFI] Connection failed.");
    return;
  }

  Serial.print("[WIFI] Connected IP=");
  Serial.println(WiFi.localIP());
  esp_wifi_get_mac(WIFI_IF_STA, nodeMac);

  wifi_ap_record_t apInfo;
  if (esp_wifi_sta_get_ap_info(&apInfo) == ESP_OK) {
    memcpy(uplinkBssid, apInfo.bssid, 6);
    haveUplinkBssid = true;
    Serial.printf("[WIFI] AP BSSID=%s channel=%u\n",
                  macToString(uplinkBssid).c_str(),
                  apInfo.primary);
  }
}

bool enablePromiscuousMode() {
  wifi_promiscuous_filter_t filter{};
  // Management traffic is enough for the first controlled WCL test and is
  // much lighter than forwarding every data frame from the hotspot.
  filter.filter_mask = WIFI_PROMIS_FILTER_MASK_MGMT;

  esp_err_t e1 = esp_wifi_set_promiscuous_filter(&filter);
  esp_err_t e2 = esp_wifi_set_promiscuous_rx_cb(&promiscuousCallback);
  esp_err_t e3 = esp_wifi_set_promiscuous(true);

  if (e1 != ESP_OK || e2 != ESP_OK || e3 != ESP_OK) {
    Serial.printf("[RF] Promiscuous enable FAILED: filter=%d cb=%d enable=%d\n",
                  (int)e1, (int)e2, (int)e3);
    return false;
  }

  Serial.println("[RF] Promiscuous mode ENABLED (MGMT only)");
  return true;
}

void disablePromiscuousMode() {
  esp_err_t err = esp_wifi_set_promiscuous(false);
  if (err == ESP_OK) {
    Serial.println("[RF] Promiscuous mode DISABLED");
  } else {
    Serial.printf("[RF] Promiscuous disable error=%d\n", (int)err);
  }
}

void mqttEnsureConnected() {
  if (mqttClient.connected() || WiFi.status() != WL_CONNECTED) return;
  if (millis() - lastMqttAttemptMs < MQTT_RECONNECT_MS) return;
  lastMqttAttemptMs = millis();

  String clientId = String("rf-node-") + NODE_ID + "-" + String((uint32_t)ESP.getEfuseMac(), HEX);
  bool connected;
  if (strlen(MQTT_USERNAME) == 0) connected = mqttClient.connect(clientId.c_str());
  else connected = mqttClient.connect(clientId.c_str(), MQTT_USERNAME, MQTT_PASSWORD);

  if (connected) {
    Serial.println("[MQTT] Connected.");
    publishReady();
  } else {
    Serial.printf("[MQTT] Failed rc=%d\n", mqttClient.state());
  }
}

bool scheduleFirstEpoch() {
  const uint64_t now = unixMs();
  if (now == 0) {
    Serial.println("[EPOCH] ERROR: unixMs() is 0; clock not initialized");
    return false;
  }
  nextEpochStartMs = ((now / EPOCH_MS) + 1ULL) * EPOCH_MS;
  epochInitialized = false;
  Serial.printf("[EPOCH] First epoch scheduled at %llu\n",
                (unsigned long long)nextEpochStartMs);
  return true;
}

void startEpochIfReady() {
  if (epochInitialized) return;
  const uint64_t now = unixMs();
  if (now < nextEpochStartMs) return;

  activeEpochId = nextEpochStartMs / EPOCH_MS;
  if (activeEpochId == 0) {
    Serial.printf("[EPOCH] ERROR: nextEpochStartMs=%llu\n", (unsigned long long)nextEpochStartMs);
    nextEpochStartMs = ((unixMs() / EPOCH_MS) + 1ULL) * EPOCH_MS;
    activeEpochId = nextEpochStartMs / EPOCH_MS;
  }
  sniffStartMs = nextEpochStartMs;
  sniffEndMs = sniffStartMs + SNIFF_WINDOW_MS;
  resetAccumulators();
  queueDropCount = 0;

  if (!enablePromiscuousMode()) return;
  epochInitialized = true;
  Serial.printf("[EPOCH] Start %llu | sniff=%llums\n",
                (unsigned long long)activeEpochId,
                (unsigned long long)SNIFF_WINDOW_MS);
}

void closeEpochIfReady() {
  if (!epochInitialized) return;
  const uint64_t now = unixMs();
  if (now < sniffEndMs) return;

  Serial.printf("[EPOCH] Closing %llu at now=%llu\n",
                (unsigned long long)activeEpochId,
                (unsigned long long)now);

  // Stop RF capture first. Give the Wi-Fi task a short recovery window
  // before doing CPU/heap-heavy work or MQTT traffic.
  disablePromiscuousMode();
  promiscuousEnabled = false;
  delay(50);

  Serial.printf("[EPOCH] Queue pending=%u\n",
                (unsigned)uxQueueMessagesWaiting(observationQueue));

  // Drain in bounded chunks and yield between chunks. This prevents a large
  // burst of Wi-Fi packets from monopolizing the main task and triggering the
  // watchdog.
  while (uxQueueMessagesWaiting(observationQueue) > 0) {
    const size_t n = drainObservationQueue(64);
    if (n == 0) break;
    delay(1);
  }

  Serial.printf("[EPOCH] Queue drained. drops=%lu\n",
                (unsigned long)queueDropCount);

  mqttClient.loop();
  delay(10);

  Serial.println("[EPOCH] Publishing observation...");
  const bool published = publishEpochObservation(
      activeEpochId, sniffStartMs, sniffEndMs);
  Serial.printf("[EPOCH] Publish result=%s\n",
                published ? "OK" : "FAILED");

  epochInitialized = false;
  nextEpochStartMs = sniffStartMs + EPOCH_MS;

  Serial.printf("[EPOCH] Next epoch at %llu\n",
                (unsigned long long)nextEpochStartMs);
}


} // namespace

void setup() {
  Serial.begin(115200);
  delay(500);
  Serial.println();
  Serial.println("================================================");
  Serial.printf("RF Localization Node %s | %s\n", NODE_ID, SESSION_ID);
  Serial.println("================================================");

  observationQueue = xQueueCreate(OBSERVATION_QUEUE_LENGTH, sizeof(RawObservation));
  if (!observationQueue) {
    Serial.println("[FATAL] Queue creation failed.");
    while (true) delay(1000);
  }

  haveFocusTargetMac = parseMac(FOCUS_TARGET_MAC, focusTargetMac);
  if (strlen(FOCUS_TARGET_MAC) > 0 && !haveFocusTargetMac) {
    Serial.printf("[TARGET] Invalid FOCUS_TARGET_MAC=%s\n", FOCUS_TARGET_MAC);
  } else if (haveFocusTargetMac) {
    Serial.printf("[TARGET] Focus MAC=%s | publish_only=%s\n",
                  macToString(focusTargetMac).c_str(),
                  PUBLISH_ONLY_FOCUS_TARGET ? "true" : "false");
  } else {
    Serial.println("[TARGET] No focus MAC configured; observing all Wi-Fi devices.");
  }

  // Configure MQTT BEFORE any Wi-Fi retry can happen.
  // The previous version returned from setup() when the first Wi-Fi
  // attempt failed, so mqttClient.setServer() was never called.
  mqttClient.setServer(MQTT_HOST, MQTT_PORT);
  mqttClient.setBufferSize(MQTT_BUFFER_SIZE);

  WiFi.mode(WIFI_STA);
  WiFi.setSleep(false);
  esp_wifi_get_mac(WIFI_IF_STA, nodeMac);
  Serial.printf("[NODE] STA MAC=%s\n", macToString(nodeMac).c_str());

  // Do not make an initial Wi-Fi failure fatal.
  // loop() will retry Wi-Fi, then initialize NTP/MQTT/radio.
  connectWifiIfNeeded();

  if (WiFi.status() == WL_CONNECTED) {
    syncClock();
    clockSynced = true;

    mqttEnsureConnected();
    scheduleFirstEpoch();
  } else {
    Serial.println("[STARTUP] Wi-Fi not connected yet; will retry in loop().");
  }
}

void loop() {
  connectWifiIfNeeded();

  if (WiFi.status() == WL_CONNECTED) {
    if (!clockSynced) {
      syncClock();
      clockSynced = true;
    }

    mqttEnsureConnected();

    startEpochIfReady();
  }

  if (mqttClient.connected()) mqttClient.loop();

  // Drain a bounded number of packets per loop so the main task always yields.
  drainObservationQueue(32);

  closeEpochIfReady();

  // Explicitly yield to Wi-Fi/FreeRTOS.
  delay(5);
}
