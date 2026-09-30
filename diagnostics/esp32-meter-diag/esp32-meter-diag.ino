// Диагностика линии счётчика DDSD6868.
// Перебирает порядок выводов RO/DI, чётность и адрес, печатает сырые байты ответа.
// Рабочая комбинация: RO=26 DI=27 DE=5, 9600 8E1, адрес 1.
//
// ВНИМАНИЕ. Не добавлять сюда проверку с переставленными RO и DI.
// В обратной раскладке ESP32 начинает передавать на GPIO26, а на этой же ножке
// выход RO модуля MAX485, который тоже её держит. Два выхода упираются друг
// в друга, порт после этого не работает до перезагрузки, а ножке и микросхеме
// это вредит. Порядок выводов проверяется только мультиметром.

#include <HardwareSerial.h>
HardwareSerial Meter(2);

struct Try { int rx, tx, de; uint32_t cfg; const char* cfgName; };
const Try T[] = {
  { 26, 27, 5, SERIAL_8E1, "8E1" },
  { 26, 27, 5, SERIAL_8N1, "8N1" },
};
const int NT = sizeof(T) / sizeof(T[0]);

uint16_t crc16(const uint8_t* d, size_t n) {
  uint16_t c = 0xFFFF;
  for (size_t i = 0; i < n; i++) {
    c ^= d[i];
    for (uint8_t b = 0; b < 8; b++) c = (c & 1) ? (c >> 1) ^ 0xA001 : (c >> 1);
  }
  return c;
}

bool ask(const Try& t, uint8_t addr) {
  uint8_t req[8] = { addr, 0x03, 0, 0, 0, 1, 0, 0 };
  uint16_t c = crc16(req, 6); req[6] = c & 0xFF; req[7] = c >> 8;

  while (Meter.available()) Meter.read();
  digitalWrite(t.de, HIGH); delayMicroseconds(300);
  Meter.write(req, 8); Meter.flush();
  delayMicroseconds(2000); digitalWrite(t.de, LOW);

  uint8_t rx[64]; size_t n = 0;
  uint32_t t0 = millis(), wait = 600;
  while (millis() - t0 < wait && n < sizeof(rx)) {
    if (Meter.available()) { rx[n++] = Meter.read(); t0 = millis(); wait = 90; }
  }
  if (!n) return false;
  Serial.printf("      adres %u -> %u bayt: ", addr, (unsigned)n);
  for (size_t k = 0; k < n; k++) Serial.printf("%02X ", rx[k]);
  if (n >= 7 && rx[0] == addr && (rx[5] | (rx[6] << 8)) == crc16(rx, 5))
    Serial.printf("  <<< OTVET VERNYY, U=%.1f V", ((rx[3] << 8) | rx[4]) / 10.0);
  Serial.println();
  return true;
}

void setup() {
  Serial.begin(115200);
  delay(1500);
  Serial.println();
  Serial.println("=== DIAGNOSTIKA LINII SCHETCHIKA ===");
}

void loop() {
  for (int i = 0; i < NT; i++) {
    Meter.end(); delay(30);
    pinMode(T[i].de, OUTPUT); digitalWrite(T[i].de, LOW);
    Meter.begin(9600, T[i].cfg, T[i].rx, T[i].tx);
    delay(80);
    Serial.printf("  RO=%d DI=%d DE=%d  %s\n", T[i].rx, T[i].tx, T[i].de, T[i].cfgName);
    bool any = false;
    for (uint8_t a = 1; a <= 3; a++) if (ask(T[i], a)) any = true;
    if (!any) Serial.println("      tishina");
  }

  // пассивное прослушивание: вдруг на линии вообще что-то есть
  Meter.end(); delay(30);
  digitalWrite(5, LOW);
  Meter.begin(9600, SERIAL_8N1, 26, 27);
  Serial.println("  passivnoe proslushivanie linii 5 sekund...");
  uint32_t t0 = millis(); size_t n = 0;
  while (millis() - t0 < 5000) {
    if (Meter.available()) { Serial.printf("%02X ", Meter.read()); n++; }
  }
  Serial.printf("\n      prinyato %u bayt\n\n", (unsigned)n);
  delay(2000);
}
