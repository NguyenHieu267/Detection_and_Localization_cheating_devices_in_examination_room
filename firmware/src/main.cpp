#include <Arduino.h>
#include <WiFi.h>
#include <PubSubClient.h>
#include <ArduinoJson.h>
#include <esp_wifi.h>
#include <esp_timer.h>
#include <esp_sntp.h>
#include <mbedtls/sha256.h>
#include <sys/time.h>
#include <atomic>
#include "web_config.h"

using namespace std;

WiFiClient wifiClient;
PubSubClient mqttClient(wifiClient);
QueueHandle_t observationQueue;
portMUX_TYPE captureMux = portMUX_INITIALIZER_UNLOCKED;
uint8_t nodeMac[6], uplinkBssid[6], targetMac[6];
bool haveTarget = false, haveBssid = false;
bool radioEnabled = false;
atomic<bool> clockSynced{false};
atomic<bool> mqttConnected{false};
atomic<uint32_t> queueDrops{0};
atomic<uint32_t> batchDrops{0};
atomic<uint32_t> publishFails{0};
atomic<uint32_t> captured{0};
atomic<uint32_t> queueHighWater{0};
atomic<uint32_t> publishLatencyMs{0};
atomic<uint32_t> markerDrops{0};
int64_t unixOffsetUs = 0;
struct RawRssiSample {
    uint8_t mac[6];
    int8_t rssi;
  uint8_t channel, frameType, subtype;
    int64_t monoUs;
  uint64_t unixMs, epochId, startMs, endMs;
  bool marker, synced, aborted;
};
RawRssiSample activeWindow{};
bool captureOpen = false;
static RawRssiSample samples[MAX_SAMPLES_PER_EPOCH];
static char mqttPayload[8192];
static uint16_t chunkEnds[MAX_SAMPLES_PER_EPOCH];

bool macEquals(const uint8_t *a, const uint8_t *b) {
    return memcmp(a, b, 6) == 0;
}

String makeDeviceToken(const uint8_t mac[6]) {
    // Preserve the original SHA256(raw MAC bytes + shared salt) token contract.
    uint8_t digest[32];
    mbedtls_sha256_context ctx;
    mbedtls_sha256_init(&ctx);
    mbedtls_sha256_starts_ret(&ctx, 0);
    mbedtls_sha256_update_ret(&ctx, mac, 6);
    mbedtls_sha256_update_ret(&ctx, reinterpret_cast<const unsigned char *>(DEVICE_TOKEN_SALT),
                              strlen(DEVICE_TOKEN_SALT));
    mbedtls_sha256_finish_ret(&ctx, digest);
    mbedtls_sha256_free(&ctx);
    char hex[65];
    for (size_t i = 0; i < 32; ++i) {
        snprintf(hex + i * 2, 3, "%02x", digest[i]);
    }
    hex[64] = 0;
    return String(hex);
}

void timeSynced(struct timeval *tv) {
    const int64_t mono = esp_timer_get_time();
    portENTER_CRITICAL(&captureMux);
    unixOffsetUs = static_cast<int64_t>(tv->tv_sec) * 1000000LL + tv->tv_usec - mono;
    clockSynced = tv->tv_sec >= 1700000000;
    portEXIT_CRITICAL(&captureMux);
    Serial.println("[TIME] SNTP notification received");
}

// Wi-Fi driver context, not an ISR: no JSON, allocation, crypto, MQTT or statistics.
void promiscuousCallback(void *buf, wifi_promiscuous_pkt_type_t type) {
    const int64_t mono = esp_timer_get_time();
    if (!buf || (type != WIFI_PKT_MGMT && type != WIFI_PKT_DATA)) {
        return;
    }
    const auto *packet = static_cast<wifi_promiscuous_pkt_t *>(buf);
    if (packet->rx_ctrl.sig_len < 24) {
        return;
    }
    if (packet->rx_ctrl.rssi > 0 || packet->rx_ctrl.rssi < -127 || packet->rx_ctrl.channel < 1 ||
        packet->rx_ctrl.channel > 14) {
        return;
    }
    const uint8_t *f = packet->payload;
    const uint8_t ft = (f[0] >> 2) & 3;
    if (ft != 0 && ft != 2) {
        return;
    }
    if ((f[0] & 3) != 0) {
        return; // Unsupported protocol version.
    }
    const bool toDS = f[1] & 1;
    const bool fromDS = f[1] & 2;
    const bool qos = ft == 2 && (f[0] & 0x80);
    const size_t headerBytes =
        24 + (ft == 2 && toDS && fromDS ? 6 : 0) + (qos ? 2 : 0) + (qos && (f[1] & 0x80) ? 4 : 0);
    if (packet->rx_ctrl.sig_len < headerBytes) {
        return;
    }
    // Track the physical RF TRANSMITTER (TA), not a bridged Ethernet source.
    // TA is addr2 for supported management and data frames, including WDS.
    // FromDS source is addr3 (addr4 for WDS), but RSSI belongs to addr2/AP.
    const uint8_t *ta = f + 10;
    const uint8_t zero[6] = {};
    if ((ta[0] & 1) || macEquals(ta, zero) || macEquals(ta, nodeMac)) {
        return;
    }
    const bool target = haveTarget && macEquals(ta, targetMac);
    if (nodeConfig.captureMode == "target_only" && !target) {
        return;
    }
    if (haveBssid && macEquals(ta, uplinkBssid) && !target) {
        return;
    }
    RawRssiSample s{};
    memcpy(s.mac, ta, 6);
    s.rssi = packet->rx_ctrl.rssi;
    s.channel = packet->rx_ctrl.channel;
    s.frameType = ft;
    s.subtype = f[0] >> 4;
    s.monoUs = mono;
    portENTER_CRITICAL(&captureMux);
    const uint64_t utc = clockSynced ? (mono + unixOffsetUs) / 1000 : 0;
    const uint64_t now = clockSynced ? utc : mono / 1000;
    if (captureOpen && now >= activeWindow.startMs && now < activeWindow.endMs &&
        activeWindow.synced == clockSynced.load()) {
        s.epochId = activeWindow.epochId;
        s.startMs = activeWindow.startMs;
        s.endMs = activeWindow.endMs;
        s.synced = activeWindow.synced;
        s.unixMs = utc;
        ++captured;
        if (xQueueSend(observationQueue, &s, 0) != pdTRUE) {
            ++queueDrops;
        }
    }
    portEXIT_CRITICAL(&captureMux);
}

void header(JsonDocument &d, const RawRssiSample &w, unsigned index, unsigned count) {
    d["schema_version"] = 2;
    d["message_type"] = "EPOCH_OBSERVATION";
    d["session_id"] = nodeConfig.sessionId;
    d["node_id"] = nodeConfig.nodeId;
    d["epoch_id"] = w.epochId;
    d["epoch_ms"] = nodeConfig.epochMs;
    d["clock_synced"] = w.synced;
    d["sniff_start_unix_ms"] = w.synced ? w.startMs : 0;
    d["sniff_end_unix_ms"] = w.synced ? w.endMs : 0;
    d["chunk_index"] = index;
    d["chunk_count"] = count;
    d["queue_drop_count"] = queueDrops.load();
    d["batch_drop_count"] = batchDrops.load();
    d["mqtt_publish_fail_count"] = publishFails.load();
    d["observations"].to<JsonArray>();
}

void appendSample(JsonDocument &d, const RawRssiSample &s) {
    auto o = d["observations"].as<JsonArray>().add<JsonObject>();
    o["device_token"] = makeDeviceToken(s.mac);
    o["rssi_dbm"] = s.rssi;
    o["channel"] = s.channel;
    o["frame_type"] = s.frameType == 0 ? "mgmt" : "data";
    o["frame_subtype"] = s.subtype;
    o["capture_mono_us"] = s.monoUs;
    o["capture_unix_ms"] = s.unixMs;
}

void mqttEnsureConnected() {
    static uint32_t lastAttempt = 0;
    if (mqttClient.connected() || WiFi.status() != WL_CONNECTED) {
        return;
    }
    if (millis() - lastAttempt < MQTT_RECONNECT_MS) {
        return;
    }
    lastAttempt = millis();
    String id = "rf-node-" + nodeConfig.nodeId + "-" +
                String(static_cast<uint32_t>(ESP.getEfuseMac()), HEX);
    if (mqttClient.connect(id.c_str())) {
        JsonDocument d;
        d["schema_version"] = "1.0";
        d["message_type"] = "NODE_READY";
        d["message_id"] = id + "-" + String(millis());
        d["session_id"] = nodeConfig.sessionId;
        d["node_id"] = nodeConfig.nodeId;
        struct timeval tv;
        gettimeofday(&tv, nullptr);
        d["timestamp_ms"] =
            clockSynced ? static_cast<uint64_t>(tv.tv_sec) * 1000 + tv.tv_usec / 1000 : 0;
        char ready[512];
        const size_t n = serializeJson(d, ready, sizeof(ready));
        mqttClient.publish(("rf/v1/nodes/" + nodeConfig.nodeId + "/ready").c_str(),
                           reinterpret_cast<uint8_t *>(ready), n);
    }
}

void publishBatch(const RawRssiSample &w, uint16_t count) {
    uint32_t started = millis();
    uint16_t chunks = 0;
    uint16_t begin = 0;
    // Preflight exact JSON sizes, then serialize again with final chunk_count.
    // A single publisher owns both buffers; callbacks continue filling the queue.
    do {
        JsonDocument d;
        header(d, w, 1023, 1024);
        uint16_t end = begin;
        while (end < count && end - begin < nodeConfig.batchSamples) {
            appendSample(d, samples[end]);
            if (measureJson(d) > nodeConfig.batchBytes) {
                break;
            }
            ++end;
        }
        if (end == begin && begin < count) {
            batchDrops += count - begin;
            return;
        }
        chunkEnds[chunks++] = end;
        begin = end;
    } while (begin < count);
    begin = 0;
    for (uint16_t i = 0; i < chunks; ++i) {
        JsonDocument d;
        header(d, w, i, chunks);
        for (uint16_t j = begin; j < chunkEnds[i]; ++j) {
            appendSample(d, samples[j]);
        }
        size_t bytes = measureJson(d);
        bool ok = false;
        if (bytes <= nodeConfig.batchBytes && bytes <= sizeof(mqttPayload)) {
            serializeJson(d, mqttPayload, sizeof(mqttPayload));
            const String topic = "rf/v1/nodes/" + nodeConfig.nodeId + "/observation";
            for (int attempt = 0; attempt < 2 && !ok; ++attempt) {
                if (mqttClient.connected()) {
                    ok = mqttClient.publish(topic.c_str(), reinterpret_cast<uint8_t *>(mqttPayload),
                                            bytes, false);
                }
                if (!ok) {
                    ++publishFails;
                }
                taskYIELD();
            }
        }
        if (!ok) {
            batchDrops += chunkEnds[i] - begin;
        }
        begin = chunkEnds[i];
        mqttClient.loop();
        vTaskDelay(1);
    }
    publishLatencyMs = millis() - started;
    Serial.printf("[RAW] epoch=%llu synced=%d samples=%u chunks=%u queue_drops=%u batch_drops=%u "
                  "mqtt_fails=%u heap=%u latency_ms=%u\n",
                  static_cast<unsigned long long>(w.epochId), w.synced, count, chunks,
                  queueDrops.load(), batchDrops.load(), publishFails.load(), ESP.getFreeHeap(),
                  publishLatencyMs.load());
}

void publisherTask(void *) {
    RawRssiSample s{};
    RawRssiSample window{};
    uint16_t count = 0;
    bool pending = false;
    for (;;) {
        mqttEnsureConnected();
        mqttClient.loop();
        mqttConnected = mqttClient.connected();
        if (xQueueReceive(observationQueue, &s, pdMS_TO_TICKS(10)) == pdTRUE) {
            if (pending && (s.epochId != window.epochId || s.synced != window.synced)) {
                // A lost end marker means the window cannot be certified complete.
                batchDrops += count;
                count = 0;
                pending = false;
            }
            if (!pending) {
                window = s;
                pending = true;
            }
            if (!s.marker) {
                if (count < MAX_SAMPLES_PER_EPOCH) {
                    samples[count++] = s;
                } else {
                    ++batchDrops;
                }
            } else {
                if (s.aborted) {
                    batchDrops += count;
                } else {
                    publishBatch(s, count);
                }
                pending = false;
                count = 0;
            }
        }
        uint32_t depth = uxQueueMessagesWaiting(observationQueue);
        if (depth > queueHighWater) {
            queueHighWater = depth;
        }
    }
}

void enterAP() {
    portENTER_CRITICAL(&captureMux);
    captureOpen = false;
    portEXIT_CRITICAL(&captureMux);
    if (radioEnabled) {
        esp_wifi_set_promiscuous(false);
    }
    radioEnabled = false;
    provisioning = true;
    WiFi.disconnect();
    WiFi.mode(WIFI_AP);
    String ssid = "RF-Setup-" + String(static_cast<uint32_t>(ESP.getEfuseMac()), HEX);
    WiFi.softAP(ssid.c_str());
    Serial.printf("[CONFIG] Provisioning AP=%s IP=%s\n", ssid.c_str(),
                  WiFi.softAPIP().toString().c_str());
}

void captureControl() {
    if (!radioEnabled) {
        return;
    }
    int64_t offset;
    portENTER_CRITICAL(&captureMux);
    offset = unixOffsetUs;
    portEXIT_CRITICAL(&captureMux);
    const bool synced = clockSynced;
    const uint64_t now = (synced ? esp_timer_get_time() + offset : esp_timer_get_time()) / 1000;
    const uint64_t epoch = now / nodeConfig.epochMs;
    const uint64_t boundary = epoch * nodeConfig.epochMs;
    portENTER_CRITICAL(&captureMux);
    if (captureOpen && (now >= activeWindow.endMs || activeWindow.synced != synced ||
                        activeWindow.epochId != epoch)) {
        captureOpen = false;
        RawRssiSample marker = activeWindow;
        marker.marker = true;
        if (xQueueSend(observationQueue, &marker, 0) != pdTRUE) {
            ++markerDrops;
        }
    }
    // Actual activation time is recorded, rather than claiming an ideal full window.
    static uint64_t lastEpoch = UINT64_MAX;
    static bool lastSynced = false;
    if (!captureOpen && (epoch != lastEpoch || synced != lastSynced) &&
        now < boundary + nodeConfig.sniffWindowMs) {
        activeWindow = {};
        activeWindow.epochId = epoch;
        activeWindow.startMs = now;
        activeWindow.endMs = boundary + nodeConfig.sniffWindowMs;
        activeWindow.synced = synced;
        captureOpen = true;
        lastEpoch = epoch;
        lastSynced = synced;
    }
    portEXIT_CRITICAL(&captureMux);
}

void fillStatus(JsonDocument &d) {
    d["node_id"] = nodeConfig.nodeId;
    d["session_id"] = nodeConfig.sessionId;
    d["mode"] = provisioning ? "AP" : "STA";
    d["ip"] = (provisioning ? WiFi.softAPIP() : WiFi.localIP()).toString();
    d["wifi_channel"] = WiFi.channel();
    d["wifi_connected"] = WiFi.status() == WL_CONNECTED;
    d["mqtt_connected"] = mqttConnected.load();
    d["clock_synced"] = clockSynced.load();
    d["queue_depth"] = uxQueueMessagesWaiting(observationQueue);
    d["queue_capacity"] = OBSERVATION_QUEUE_LENGTH;
    d["queue_high_water"] = queueHighWater.load();
    d["queue_drop_count"] = queueDrops.load();
    d["batch_drop_count"] = batchDrops.load();
    d["mqtt_publish_fail_count"] = publishFails.load();
    d["captured_sample_count"] = captured.load();
    d["free_heap"] = ESP.getFreeHeap();
    d["publish_latency_ms"] = publishLatencyMs.load();
    d["marker_drop_count"] = markerDrops.load();
    portENTER_CRITICAL(&captureMux);
    const uint64_t epoch = activeWindow.epochId;
    portEXIT_CRITICAL(&captureMux);
    d["epoch_id"] = epoch;
    d["mac_semantics"] = "802.11 transmitter address (TA)";
}

void setup() {
    Serial.begin(115200);
    pinMode(CONFIG_RESET_PIN, INPUT_PULLUP);
    loadConfig();
    observationQueue = xQueueCreate(OBSERVATION_QUEUE_LENGTH, sizeof(RawRssiSample));
    if (!observationQueue) {
        Serial.println("[FATAL] Queue allocation failed");
        while (true) {
            delay(1000);
        }
    }
    if (!nodeConfig.targetMac.isEmpty()) {
        // Convert the configured MAC; input validation is intentionally omitted.
        unsigned b[6] = {};
        sscanf(nodeConfig.targetMac.c_str(), "%2x:%2x:%2x:%2x:%2x:%2x", &b[0], &b[1],
               &b[2], &b[3], &b[4], &b[5]);
        for (int i = 0; i < 6; ++i) {
            targetMac[i] = b[i];
        }
        haveTarget = true;
    }
    WiFi.mode(WIFI_STA);
    WiFi.setSleep(false);
    esp_wifi_get_mac(WIFI_IF_STA, nodeMac);
    mqttClient.setServer(nodeConfig.mqttHost.c_str(), nodeConfig.mqttPort);
    if (!mqttClient.setBufferSize(MQTT_BUFFER_SIZE)) {
        Serial.println("[FATAL] MQTT buffer allocation failed");
        while (true) {
            delay(1000);
        }
    }
    mqttClient.setSocketTimeout(1);
    wifiClient.setTimeout(1000);
    if (nodeConfig.ssid.isEmpty()) {
        enterAP();
    } else {
        WiFi.begin(nodeConfig.ssid.c_str(), nodeConfig.wifiPassword.c_str());
    }
    startWebConfig();
#if CONFIG_FREERTOS_UNICORE
    const BaseType_t publisherCore = 0;
#else
    const BaseType_t publisherCore = 1;
#endif
    if (xTaskCreatePinnedToCore(publisherTask, "raw-publisher", 8192, nullptr, 1, nullptr,
                                publisherCore) != pdPASS) {
        Serial.println("[FATAL] Publisher task allocation failed");
        while (true) {
            delay(1000);
        }
    }
    Serial.printf("[BOOT] cores=%u publisher_core=%d queue=%u max_samples=%u heap=%u\n",
                  ESP.getChipCores(), publisherCore, OBSERVATION_QUEUE_LENGTH,
                  MAX_SAMPLES_PER_EPOCH, ESP.getFreeHeap());
}

void loop() {
    static uint32_t wifiAttemptAt = millis();
    static uint32_t heldAt = 0;
    static unsigned wifiFailures = 0;
    static bool ntpStarted = false;
    serviceWebConfig();
    if (digitalRead(CONFIG_RESET_PIN) == LOW) {
        if (!heldAt) {
            heldAt = millis();
        }
        if (millis() - heldAt >= 3000) {
            factoryResetConfig();
            ESP.restart();
        }
    } else {
        heldAt = 0;
    }
    if (!provisioning) {
        if (WiFi.status() == WL_CONNECTED) {
            wifiFailures = 0;
            wifiAttemptAt = millis();
            if (!ntpStarted) {
                sntp_set_time_sync_notification_cb(timeSynced);
                configTime(0, 0, NTP_SERVER_1,
                           NTP_SERVER_2); // Keep original SNTP servers/mechanism.
                ntpStarted = true;
                Serial.println("[TIME] Waiting for SNTP; unsynced until notification");
            }
            if (!radioEnabled) {
                wifi_ap_record_t ap{};
                if (esp_wifi_sta_get_ap_info(&ap) == ESP_OK) {
                    memcpy(uplinkBssid, ap.bssid, 6);
                    haveBssid = true;
                }
                wifi_promiscuous_filter_t filter{};
                filter.filter_mask = WIFI_PROMIS_FILTER_MASK_MGMT | WIFI_PROMIS_FILTER_MASK_DATA;
                if (esp_wifi_set_promiscuous_filter(&filter) == ESP_OK &&
                    esp_wifi_set_promiscuous_rx_cb(promiscuousCallback) == ESP_OK &&
                    esp_wifi_set_promiscuous(true) == ESP_OK) {
                    radioEnabled = true;
                    Serial.printf("[WIFI] STA IP=%s channel=%u; capture enabled\n",
                                  WiFi.localIP().toString().c_str(), WiFi.channel());
                }
            }
            captureControl();
        } else {
            portENTER_CRITICAL(&captureMux);
            if (captureOpen) {
                RawRssiSample marker = activeWindow;
                marker.marker = true;
                // A disconnected window is diagnostic only; never claim full overlap.
                marker.aborted = true;
                if (xQueueSend(observationQueue, &marker, 0) != pdTRUE) {
                    ++markerDrops;
                }
            }
            captureOpen = false;
            portEXIT_CRITICAL(&captureMux);
            if (radioEnabled) {
                esp_wifi_set_promiscuous(false);
                radioEnabled = false;
            }
            if (millis() - wifiAttemptAt > 15000) {
                wifiAttemptAt = millis();
                if (++wifiFailures >= 3) {
                    enterAP();
                } else {
                    WiFi.reconnect();
                }
            }
        }
    }
    delay(2);
}
