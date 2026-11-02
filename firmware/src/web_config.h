#pragma once
#include <Arduino.h>
#include <ArduinoJson.h>
#include "config.h"

extern NodeConfig nodeConfig;  // Immutable after setup; save routes write a copy and reboot.
extern bool provisioning;
void loadConfig();
void startWebConfig();
void serviceWebConfig();
void factoryResetConfig();
void fillStatus(JsonDocument &doc);
