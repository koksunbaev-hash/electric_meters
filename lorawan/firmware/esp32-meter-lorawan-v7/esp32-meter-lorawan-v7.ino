// DDSD6868 -> ESP32 -> EWD95M -> LoRaWAN
// v7: модуль LoRaWAN ищется на НЕСКОЛЬКИХ наборах выводов.
//
// Счётчик остаётся на своей линии: RO=26 DI=27 DE=5, 9600 8E1. Её не трогаем.
// Модуль LoRaWAN подключается отдельным MAX485 на любой из наборов ниже,
// прошивка сама найдёт, на каком он отзовётся. Общую шину не используем:
// проверено, что счётчик и модуль на одной паре мешают друг другу.
//
// Наборы выводов для модуля LoRaWAN (RO, DI, DE+RE):
//   33, 32, 25    текущий
//   16, 17, 4     проверен на плате счётчика печи
//   19, 18, 23
//   22, 21, 13
//
// Регион берётся с модуля как есть (EU868), не навязывается.

#include <HardwareSerial.h>

#define MET_RX 26
#define MET_TX 27
#define MET_DE  5

#define METER_ADDR 1
#define LORA_PORT  2
#define SEND_MS    40000UL   // период передачи показаний, 40 секунд

struct PinSet { int rx, tx, de; };
const PinSet CAND[] = { {19,18,22}, {19,18,21}, {19,18,13}, {16,17,4} };   // GPIO23 i GPIO25 neispravny, ne ispolzuem
const int NCAND = sizeof(CAND) / sizeof(CAND[0]);

HardwareSerial Meter(2);
HardwareSerial Dtu(1);

int dtuIdx = -1;                 // индекс найденного набора, -1 если не найден
bool joined = false;
uint8_t regionId = 5;
uint32_t lastSend = 0, lastJoin = 0, lastProbe = 0;

uint16_t crc16(const uint8_t* d, size_t n) {
  uint16_t c = 0xFFFF;
  for (size_t i = 0; i < n; i++) {
    c ^= d[i];
    for (uint8_t b = 0; b < 8; b++) c = (c & 1) ? (c >> 1) ^ 0xA001 : (c >> 1);
  }
  return c;
}

// ---------------- счётчик ----------------
bool readRegs(uint16_t start, uint16_t count, uint16_t* out) {
  uint8_t req[8] = { METER_ADDR, 0x03, (uint8_t)(start >> 8), (uint8_t)start,
                     (uint8_t)(count >> 8), (uint8_t)count, 0, 0 };
  uint16_t c = crc16(req, 6);
  req[6] = c & 0xFF; req[7] = c >> 8;

  while (Meter.available()) Meter.read();
  digitalWrite(MET_DE, HIGH); delayMicroseconds(300);
  Meter.write(req, 8); Meter.flush();
  delayMicroseconds(2000); digitalWrite(MET_DE, LOW);

  uint8_t rx[128]; size_t n = 0;
  uint32_t t0 = millis(), wait = 700;
  while (millis() - t0 < wait && n < sizeof(rx)) {
    if (Meter.available()) {
      uint8_t b = Meter.read();
      if (n == 0 && b != METER_ADDR) continue;
      rx[n++] = b; t0 = millis(); wait = 90;
    }
  }
  if (n < 5 || rx[1] != 0x03) return false;
  uint8_t nb = rx[2];
  if (n < (size_t)(3 + nb + 2)) return false;
  if ((rx[3 + nb] | (rx[4 + nb] << 8)) != crc16(rx, 3 + nb)) return false;
  for (uint16_t i = 0; i < count && i * 2 + 1 < nb; i++)
    out[i] = (rx[3 + i * 2] << 8) | rx[4 + i * 2];
  return true;
}

// ---------------- модуль LoRaWAN ----------------
void dtuUsePins(int i) {
  Dtu.end(); delay(30);
  pinMode(CAND[i].de, OUTPUT); digitalWrite(CAND[i].de, LOW);
  Dtu.begin(9600, SERIAL_8N1, CAND[i].rx, CAND[i].tx);
  delay(80);
  while (Dtu.available()) Dtu.read();
}

String dtuTalk(int i, const String& cmd, uint32_t timeoutMs) {
  while (Dtu.available()) Dtu.read();
  String s = cmd + "\r\n";
  digitalWrite(CAND[i].de, HIGH); delayMicroseconds(300);
  Dtu.print(s); Dtu.flush();
  delayMicroseconds(100); digitalWrite(CAND[i].de, LOW);   // 2500 srezalo nachalo otveta modulya

  String out; uint32_t t0 = millis();
  while (millis() - t0 < timeoutMs) {
    while (Dtu.available() && out.length() < 512) out += (char)Dtu.read();
    if (out.endsWith("OK\r\n") || out.indexOf("AT_") >= 0) break;
    delay(5);
  }
  out.trim();
  return out;
}

bool looksReal(const String& s) {
  return s.indexOf("OK") >= 0 || s.indexOf("AT_") >= 0 || s.indexOf("+EVT") >= 0;
}

String dtuCmd(const String& cmd, uint32_t timeoutMs = 2500) {
  if (dtuIdx < 0) return "";
  Serial.print("[>] "); Serial.println(cmd);
  String r = dtuTalk(dtuIdx, cmd, timeoutMs);
  if (!r.length()) { Serial.println("[<] (net otveta)"); return r; }
  if (looksReal(r)) {
    Serial.print("[<] "); Serial.println(r);
    if (r.indexOf("JOINED") >= 0) joined = true;
  } else {
    Serial.print("[?] musor: ");
    for (size_t k = 0; k < r.length() && k < 20; k++) Serial.printf("%02X ", (uint8_t)r[k]);
    Serial.println();
  }
  return r;
}

void findDtu() {
  for (int i = 0; i < NCAND; i++) {
    dtuUsePins(i);
    String r = dtuTalk(i, "AT", 1000);
    Serial.printf("   RO=%2d DI=%2d DE=%2d -> ", CAND[i].rx, CAND[i].tx, CAND[i].de);
    if (!r.length()) Serial.println("tishina");
    else if (looksReal(r)) {
      Serial.print("OTVET: "); Serial.println(r);
      dtuIdx = i;
      Serial.printf(">>> MODUL NAYDEN na vyvodah %d/%d/%d\n", CAND[i].rx, CAND[i].tx, CAND[i].de);
      return;
    } else {
      Serial.print("musor: ");
      for (size_t k = 0; k < r.length() && k < 12; k++) Serial.printf("%02X ", (uint8_t)r[k]);
      Serial.println();
    }
  }
}

void dtuInit() {
  dtuCmd("AT+CDEVEUI=?");
  String r = dtuCmd("AT+REGION=?");
  int p = r.indexOf(':');
  if (p > 0 && p < 3) regionId = r.substring(0, p).toInt();
  Serial.printf("=== region: %s ===\n", regionId == 9 ? "RU864" : "EU868");
  dtuCmd("AT+CADR=1");
  dtuCmd("AT+CJOIN=1:1", 4000);
  lastJoin = millis();
}

// ---------------- main ----------------
void setup() {
  Serial.begin(115200);
  pinMode(MET_DE, OUTPUT); digitalWrite(MET_DE, LOW);
  Meter.begin(9600, SERIAL_8E1, MET_RX, MET_TX);
  delay(1200);
  Serial.println();
  Serial.println("=== DDSD6868 -> EWD95M -> LoRaWAN  v7 ===");
  Serial.println("Modul LoRaWAN ischetsya na 4 naborah vyvodov.");
  findDtu();
  if (dtuIdx >= 0) dtuInit();
  else Serial.println("!!! modul ne nayden, budu iskat kazhdye 10 s");
}

void loop() {
  // ловим события модуля
  if (dtuIdx >= 0 && Dtu.available()) {
    String line = Dtu.readStringUntil('\n'); line.trim();
    if (line.length() && looksReal(line)) {
      Serial.print("[*] "); Serial.println(line);
      if (line.indexOf("JOINED") >= 0) joined = true;
    }
  }

  if (dtuIdx < 0 && millis() - lastProbe > 10000UL) {
    lastProbe = millis();
    findDtu();
    if (dtuIdx >= 0) dtuInit();
  }

  if (dtuIdx >= 0 && !joined && millis() - lastJoin > 90000UL) {
    dtuCmd("AT+CJOIN=1:1", 4000); lastJoin = millis();
  }

  if (lastSend != 0 && millis() - lastSend < SEND_MS) { delay(20); return; }
  lastSend = millis();

  uint16_t r[32];
  bool ok = false;
  for (uint8_t a = 0; a < 3 && !ok; a++) { if (readRegs(0, 31, r) && r[0] != 0) ok = true; else delay(400); }
  if (!ok) { Serial.println("schetchik ne otvetil"); return; }

  uint16_t u = r[0], i = r[3], p = r[8], pf = r[19], f = r[26];
  uint32_t e = ((uint32_t)r[29] << 16) | r[30];
  Serial.printf("U=%.1f V  I=%.2f A  P=%u W  PF=%.3f  F=%.2f Hz  E=%.2f kWh\n",
                u / 10.0, i / 100.0, p, pf / 1000.0, f / 100.0, e / 100.0);

  if (dtuIdx < 0) { Serial.println("modul ne nayden, ne otpravlyayu"); return; }

  uint8_t pkt[12] = { 0x01, (uint8_t)(u >> 8), (uint8_t)u, (uint8_t)(i >> 8), (uint8_t)i,
                      (uint8_t)(p >> 8), (uint8_t)p, (uint8_t)(pf / 10),
                      (uint8_t)(e >> 24), (uint8_t)(e >> 16), (uint8_t)(e >> 8), (uint8_t)e };
  char hex[25];
  for (uint8_t k = 0; k < 12; k++) sprintf(hex + k * 2, "%02X", pkt[k]);
  hex[24] = 0;

  String resp = dtuCmd(String("AT+SEND=") + LORA_PORT + ":1:0:" + hex, 10000);
  if (resp.indexOf("NO_NETWORK") >= 0) { joined = false; Serial.println("set poteryana"); }
}
