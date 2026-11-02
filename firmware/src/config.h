#pragma once

#define DEVICE_TOKEN_SALT "secret-key"
#define NTP_SERVER_1 "pool.ntp.org"
#define NTP_SERVER_2 "time.nist.gov"
#define OBSERVATION_QUEUE_LENGTH 512          // Queue giữa Wi-Fi Callback và Publisher Task -> Publisher
#define MAX_SAMPLES_PER_EPOCH 512             // Publisher đã lấy khỏi Queue. -> Epoch
#define MQTT_BUFFER_SIZE 8256
#define MQTT_RECONNECT_MS 3000
#define CONFIG_RESET_PIN 0

struct NodeConfig {
  String nodeId = "ESP01",        // Node ID
                   ssid,          // Tên Mạng
                   wifiPassword,  // Pass
                   mqttHost;      // IPv4 sau khi kết nối với hotspot

  String sessionId = "session-dev-001",             // Phiên đo
                     targetMac,                     // MAC
                     captureMode = "target_only";   // target_only-
  uint16_t mqttPort = 1883;
  uint32_t epochMs = 1000, sniffWindowMs = 700;
  uint16_t batchSamples = 16, batchBytes = 4096;
};
