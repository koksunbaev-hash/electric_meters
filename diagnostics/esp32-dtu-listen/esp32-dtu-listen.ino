// Пассивный слушатель линии к модулю EWD95M.
//
// Ничего не передаёт: DE держится низким на всех наборах выводов.
// Слушает ОДИН набор выводов непрерывно, чтобы не пропустить короткую посылку.
//
// Как пользоваться: сними и снова подай питание на модуль EWD95M.
// При загрузке он сам выдаёт в порт "NVM DATA STORED" и "+EVT:JOINED".
//
//   Текст пришёл  -> линия и передатчик модуля исправны, виновата наша передача.
//   Ничего не пришло -> модуль не запитан либо линия оборвана.
//
// Счётчик параллельно опрашивается, чтобы видеть, что плата жива.

#include <HardwareSerial.h>

#define MET_RX 26
#define MET_TX 27
#define MET_DE  5
#define METER_ADDR 1

struct PinSet { int rx, tx, de; };
const PinSet CAND[] = { {19,18,23} };   // слушаем только рабочий набор, непрерывно
const int NCAND = sizeof(CAND) / sizeof(CAND[0]);

HardwareSerial Meter(2);
HardwareSerial Dtu(1);

uint32_t lastMeter = 0;

uint16_t crc16(const uint8_t* d, size_t n) {
  uint16_t c = 0xFFFF;
  for (size_t i = 0; i < n; i++) {
    c ^= d[i];
    for (uint8_t b = 0; b < 8; b++) c = (c & 1) ? (c >> 1) ^ 0xA001 : (c >> 1);
  }
  return c;
}

void pollMeter() {
  uint8_t req[8] = { METER_ADDR, 0x03, 0, 0, 0, 1, 0, 0 };
  uint16_t c = crc16(req, 6); req[6] = c & 0xFF; req[7] = c >> 8;
  while (Meter.available()) Meter.read();
  digitalWrite(MET_DE, HIGH); delayMicroseconds(300);
  Meter.write(req, 8); Meter.flush();
  delayMicroseconds(2000); digitalWrite(MET_DE, LOW);
  uint8_t rx[32]; size_t n = 0;
  uint32_t t0 = millis(), wait = 600;
  while (millis() - t0 < wait && n < sizeof(rx)) {
    if (Meter.available()) { rx[n++] = Meter.read(); t0 = millis(); wait = 90; }
  }
  if (n >= 7 && rx[0] == METER_ADDR && (rx[5] | (rx[6] << 8)) == crc16(rx, 5))
    Serial.printf("[schetchik] U=%.1f V\n", ((rx[3] << 8) | rx[4]) / 10.0);
  else
    Serial.println("[schetchik] ne otvetil");
}

void setup() {
  Serial.begin(115200);
  pinMode(MET_DE, OUTPUT); digitalWrite(MET_DE, LOW);
  Meter.begin(9600, SERIAL_8E1, MET_RX, MET_TX);
  for (int i = 0; i < NCAND; i++) { pinMode(CAND[i].de, OUTPUT); digitalWrite(CAND[i].de, LOW); }
  delay(1200);
  Serial.println();
  Serial.println("=== PASSIVNYY SLUSHATEL LINII EWD95M ===");
  Serial.println("Snimi i poday pitanie na modul. Ya napechatayu vsyo, chto on vydast.");
  Serial.println();
}

void loop() {
  for (int i = 0; i < NCAND; i++) {
    Dtu.end(); delay(20);
    digitalWrite(CAND[i].de, LOW);              // только слушаем
    Dtu.begin(9600, SERIAL_8N1, CAND[i].rx, CAND[i].tx);
    delay(40);
    while (Dtu.available()) Dtu.read();

    uint32_t t0 = millis();
    while (millis() - t0 < 600000) {
      if (Dtu.available()) {
        uint8_t buf[192]; size_t n = 0;
        uint32_t t1 = millis();
        while (millis() - t1 < 80 && n < sizeof(buf)) {
          if (Dtu.available()) { buf[n++] = Dtu.read(); t1 = millis(); }
        }
        if (n) {
          Serial.printf(">>> RO=%d DI=%d DE=%d prinyato %u bayt: ",
                        CAND[i].rx, CAND[i].tx, CAND[i].de, (unsigned)n);
          for (size_t k = 0; k < n; k++) {
            char ch = buf[k];
            if (ch >= 32 && ch < 127) Serial.print(ch);
            else if (ch == '\r') Serial.print("<CR>");
            else if (ch == '\n') Serial.print("<LF>");
            else Serial.printf("<%02X>", buf[k]);
          }
          Serial.println();
        }
      }
      if (millis() - lastMeter > 20000UL) { lastMeter = millis(); pollMeter(); }
      delay(5);
    }
  }
}
