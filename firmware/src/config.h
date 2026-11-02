#pragma once

// No network credentials in source. Configure each board through its web UI.
#define DEVICE_TOKEN_SALT "rf-mvp-shared-salt-change-me"
#define NTP_SERVER_1 "pool.ntp.org"
#define NTP_SERVER_2 "time.nist.gov"
#define OBSERVATION_QUEUE_LENGTH 512
#define MAX_SAMPLES_PER_EPOCH 512
#define MQTT_BUFFER_SIZE 8256
#define MQTT_RECONNECT_MS 3000
#define WIFI_RECONNECT_MS 5000
#define CONFIG_RESET_PIN 0

struct NodeConfig {
  String nodeId = "ESP01", ssid, wifiPassword, mqttHost;
  String sessionId = "session-dev-001", targetMac, captureMode = "target_only";
  String adminPassword;
  uint16_t mqttPort = 1883;
  uint32_t epochMs = 1000, sniffWindowMs = 700;
  uint16_t batchSamples = 16, batchBytes = 4096;
};
