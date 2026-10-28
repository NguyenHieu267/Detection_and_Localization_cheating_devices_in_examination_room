#pragma once

// -----------------------------------------------------------------------------
// Per-node identity
// Change NODE_ID for each board: ESP01 / ESP02 / ESP03 / ESP04
// -----------------------------------------------------------------------------
#define NODE_ID "ESP04"

// All four nodes use the same session ID during one physical test.
#define SESSION_ID "session-dev-001"

// -----------------------------------------------------------------------------
// Uplink Wi-Fi / MQTT
// -----------------------------------------------------------------------------
#define WIFI_SSID "iPhone"
#define WIFI_PASSWORD "123456789"

#define MQTT_HOST "172.20.10.2"
#define MQTT_PORT 1883
#define MQTT_USERNAME ""
#define MQTT_PASSWORD ""

// -----------------------------------------------------------------------------
// First-test target focus
// -----------------------------------------------------------------------------
// Put the phone MAC here when you know it, e.g. "AA:BB:CC:DD:EE:FF".
// Leave empty to aggregate every Wi-Fi device seen by this node.
#define FOCUS_TARGET_MAC "DA:DE:3A:62:30:64"

// true  = publish ONLY FOCUS_TARGET_MAC. This is convenient for the first
//          one-phone WCL test.
// false = publish every observed device, while the Gateway can still focus
//          localization on the configured target.
//
// This does NOT change packet capture; it is only an output filter.
#define PUBLISH_ONLY_FOCUS_TARGET true

// Must be IDENTICAL on all four nodes and on the Gateway when deriving tokens.
#define DEVICE_TOKEN_SALT "rf-mvp-shared-salt-change-me"

// -----------------------------------------------------------------------------
// Epoch / memory
// -----------------------------------------------------------------------------
#define EPOCH_MS 1000ULL
#define MAX_DEVICES_PER_EPOCH 32
#define MAX_SAMPLES_PER_DEVICE 256
#define OBSERVATION_QUEUE_LENGTH 1024
#define MQTT_BUFFER_SIZE 4096

#define NTP_SERVER_1 "pool.ntp.org"
#define NTP_SERVER_2 "time.nist.gov"

#define MQTT_RECONNECT_MS 3000
#define WIFI_RECONNECT_MS 5000
