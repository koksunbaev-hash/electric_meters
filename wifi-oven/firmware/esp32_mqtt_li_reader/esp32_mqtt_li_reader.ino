#include <WiFi.h>
#include <PubSubClient.h>
#include <HardwareSerial.h>
#include <ArduinoJson.h>
#include "secrets.h"   // имя и пароль Wi-Fi, логин MQTT: скопируйте secrets.example.h в secrets.h

// --- НАСТРОЙКИ WIFI ---
const char* ssid = WIFI_SSID;
const char* password = WIFI_PASSWORD;

// --- НАСТРОЙКИ MQTT ---
const char* mqtt_server = "192.168.0.199";
const int mqtt_port = 30511;
const char* mqtt_user = MQTT_USER;
const char* mqtt_password = MQTT_PASSWORD;
const char* mqtt_topic = "opentwins/lower_lab:oven";
const int MQTT_RETRY_LIMIT = 20;
// ⭐️ ИЗМЕНЕНО: Задайте имя вашего устройства здесь
const char* DEVICE_NAME = "lower_lab:oven";

// --- КЛИЕНТЫ ---
WiFiClient espClient;
PubSubClient client(espClient);

// --- НАСТРОЙКИ СЧЁТЧИКА ---
#define DE_RE_PIN 4
#define RXD2 16
#define TXD2 17

// --- НАСТРОЙКИ ТАЙМЕРА ---
const long READ_INTERVAL = 30000;
unsigned long previousMillis = 0;

// --- СТРУКТУРА ДЛЯ ХРАНЕНИЯ КОМАНД ---
struct MeterCommand {
  const char* influx_key;
  const char* hex_command;
  int divisor;
};

// --- КОМАНДЫ ДЛЯ СЧЁТЧИКА ---
const char* sessionCommands[] = {"7F7F7F2F3F210D0A", "7F7F7F063034310D0A"};
const char* closeCommand = "7F7F7F0142300371";

// ⭐️ ОБНОВЛЕНО: Возвращены все команды для опроса всех фаз
MeterCommand dataCommands[] = {
    // --- Напряжение по фазам ---
    {"voltage_a_v", "7F7F7F01523202433930302829031A", 100},      // C900 - Напряжение фазы A
    {"voltage_b_v", "7F7F7F01523202433930312829031B", 100},      // C901 - Напряжение фазы B
    {"voltage_c_v", "7F7F7F015232024339303228290318", 100},      // C902 - Напряжение фазы C
    // --- Ток по фазам ---
    {"current_a_a", "7F7F7F01523202433931302829031B", 100},      // C910 - Ток фазы A
    {"current_b_a", "7F7F7F01523202433931312829031A", 100},      // C911 - Ток фазы B
    {"current_c_a", "7F7F7F015232024339313228290319", 100},      // C912 - Ток фазы C
    // --- Общие показатели ---
    {"power_factor_total", "7F7F7F01523202433935302829031F", 1000},   // C950 - Общий коэффициент мощности
    {"frequency_hz", "7F7F7F01523202433937302829031D", 100},       // C970 - Частота сети
    {"reactive_energy_positive_varh", "7F7F7F01523202393036302829036F", 100}, // 3.8.0 - Реактивная энергия A+
    {"reactive_energy_negative_varh", "7F7F7F015232023930413028290318", 100},  // 4.8.0 - Реактивная энергия A-
    {"active_energy_positive_kwh", "7F7F7F015232023930313028290368", 100}, // 1.8.0 - Общая активная энергия A+
    {"active_energy_negative_kwh", "7F7F7F01523202393035302829036C", 100}  // 2.8.0 - Общая активная энергия A-
};
const int dataCommandCount = sizeof(dataCommands) / sizeof(dataCommands[0]);


// --- ФУНКЦИИ WIFI и MQTT ---
void setup_wifi() {
  delay(10); Serial.print("\nConnecting to "); Serial.println(ssid);
  WiFi.begin(ssid, password);
  while (WiFi.status() != WL_CONNECTED) {
    delay(500); Serial.print(".");
  }
  Serial.println("\nWiFi connected"); Serial.print("IP address: "); Serial.println(WiFi.localIP());
}

void reconnect_mqtt() {
  int retries = 0;
  while (!client.connected()) {
    Serial.print("Attempting MQTT connection...");
    if (client.connect(DEVICE_NAME, mqtt_user, mqtt_password)) {
      Serial.println("connected");
    } else {
      Serial.print("failed, rc=");
      Serial.print(client.state());
      Serial.println(" try again in 5 seconds");
      delay(5000);
      if (++retries >= MQTT_RETRY_LIMIT) {
        Serial.println("Failed to connect to MQTT broker. Restarting...");
        delay(1000);
        ESP.restart();
      }
    }
  }

}

// --- ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ (СЧЁТЧИК) ---
void enableTransmission() { digitalWrite(DE_RE_PIN, HIGH); }
void enableReception() { digitalWrite(DE_RE_PIN, LOW); }

size_t hexStringToBytes(const char* h, uint8_t* b, size_t m) {
  size_t l = strlen(h), c = 0;
  for (size_t i = 0; i < l && c < m; i += 2) {
    char s[3] = {h[i], h[i+1], '\0'};
    b[c++] = (uint8_t)strtol(s, NULL, 16);
  } return c;
}

bool sendCommand(const char* cmd, uint8_t* resp, size_t& len) {
  uint8_t req[64]; size_t reqLen = hexStringToBytes(cmd, req, sizeof(req));
  while (Serial2.available()) Serial2.read();
  enableTransmission(); Serial2.write(req, reqLen); Serial2.flush(); enableReception();
  unsigned long start = millis(); len = 0;
  while (millis() - start < 2000) {
    if (Serial2.available()) {
      if (len < 255) {
         resp[len++] = Serial2.read();
      } else {
         Serial2.read();
      }
      start = millis();
    }
  }
  resp[len] = '\0';
  return len > 0;
}

String extractValue(uint8_t* resp, size_t len) {
  String val = ""; bool cap = false;
  for (size_t i = 0; i < len; i++) {
    char c = (char)resp[i];
    if (c == ')') break; if (cap) val += c; if (c == '(') cap = true;
  } return val;
}

// --- ГЛАВНЫЕ ФУНКЦИИ ---
void setup() {
  Serial.begin(115200);
  Serial.printf("Device Name: %s\n", DEVICE_NAME);

  pinMode(DE_RE_PIN, OUTPUT);
  enableReception();
  Serial2.begin(4800, SERIAL_7E1, RXD2, TXD2);

  setup_wifi();
  client.setServer(mqtt_server, mqtt_port);
  client.setBufferSize(512);

  Serial.println("Setup complete. Starting main loop.");
}

void loop() {
  if (WiFi.status() != WL_CONNECTED) {
    Serial.println("WiFi disconnected. Retrying connection...");
    setup_wifi();
  }
  if (!client.connected()) {
    reconnect_mqtt();
  }
  client.loop();

  unsigned long currentMillis = millis();
  if (currentMillis - previousMillis >= READ_INTERVAL) {
    previousMillis = currentMillis;
    uint8_t buffer[256]; size_t len;
    bool session_ok = true;

    Serial.println("--- Starting new meter reading session ---");

    for (const char* cmd : sessionCommands) {
      if (!sendCommand(cmd, buffer, len)) {
        session_ok = false;
        Serial.println("Failed to send session command.");
        break;
      }
      delay(500);
    }

    if (session_ok) {
      StaticJsonDocument<768> doc; // Увеличил размер JSON документа
      JsonObject data = doc.createNestedObject("data");
      doc["device_id"] = DEVICE_NAME;

      for (int i = 0; i < dataCommandCount; i++) {
        if (sendCommand(dataCommands[i].hex_command, buffer, len)) {
          String valueStr = extractValue(buffer, len);
          if (valueStr.length() > 0) {
            float raw_value = valueStr.toFloat();
            int divisor = dataCommands[i].divisor;
            float final_value = (divisor != 0) ? (raw_value / divisor) : raw_value;
            data[dataCommands[i].influx_key] = final_value;
          } else {
             Serial.printf("No value extracted for %s\n", dataCommands[i].influx_key);
          }
        } else {
           Serial.printf("No response for command %s\n", dataCommands[i].influx_key);
        }
        delay(500);
        client.loop();
      }
      
      // ⭐️ ОБНОВЛЕНО: Блок расчета мощности снова использует все три фазы
      float voltage_a = data["voltage_a_v"] | 0;
      float voltage_b = data["voltage_b_v"] | 0;
      float voltage_c = data["voltage_c_v"] | 0;

      float current_a = data["current_a_a"] | 0;
      float current_b = data["current_b_a"] | 0;
      float current_c = data["current_c_a"] | 0;

      float power_factor = data["power_factor_total"] | 0;

      // Рассчитываем мощность для каждой фазы (P = V * I * PF)
      float power_a = voltage_a * current_a * power_factor;
      float power_b = voltage_b * current_b * power_factor;
      float power_c = voltage_c * current_c * power_factor;

      // Суммируем мощность всех фаз
      float total_active_power = power_a + power_b + power_c;

      // Добавляем рассчитанное значение в JSON с ключом, который ожидает сервер
      data["active_power_w"] = total_active_power;
      
      String output;
      serializeJson(doc, output);
      Serial.println("Publishing formatted data to MQTT:");
      Serial.println(output);

      if (client.publish(mqtt_topic, output.c_str())) {
        Serial.println("-> MQTT message published successfully.");
      } else {
        Serial.println("-> ERROR: MQTT message publish failed.");
      }

    } else {
      Serial.println("Failed to open session with meter.");
    }
    
    sendCommand(closeCommand, buffer, len);
    Serial.println("--- Session closed ---");
  }
}

