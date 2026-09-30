// Проверка выводов новой платы ESP32 перед подключением обвязки.
//
// ESP32 умеет читать реальный уровень на собственной ножке, пока её же удерживает.
// Если ножка исправна, чтение совпадает с тем, что мы выставили.
// Плата должна быть голой: ничего, кроме USB.
//
// Проверяем ровно те выводы, которые займёт схема счётчика.

const int PINS[] = { 26, 27, 5, 19, 18, 22, 21, 13, 4, 16, 17 };
const char* NOTE[] = { "schetchik RO", "schetchik DI", "schetchik DE",
                       "modem RO", "modem DI", "modem DE",
                       "zapas", "zapas", "zapas", "zapas", "zapas" };
const int N = sizeof(PINS) / sizeof(PINS[0]);

bool checkPin(int p, const char* note) {
  pinMode(p, OUTPUT);

  digitalWrite(p, HIGH);
  delayMicroseconds(600);
  bool hi = digitalRead(p) == HIGH;

  digitalWrite(p, LOW);
  delayMicroseconds(600);
  bool lo = digitalRead(p) == LOW;

  pinMode(p, INPUT_PULLUP);
  delayMicroseconds(600);
  bool pu = digitalRead(p) == HIGH;

  pinMode(p, INPUT_PULLDOWN);
  delayMicroseconds(600);
  bool pd = digitalRead(p) == LOW;

  pinMode(p, INPUT);

  Serial.printf("  GPIO %2d  %-14s  vysokiy:%s  nizkiy:%s  podtyazhka vverh:%s  vniz:%s   %s\n",
                p, note, hi ? "da " : "NET", lo ? "da " : "NET",
                pu ? "da " : "NET", pd ? "da " : "NET",
                (hi && lo) ? "ISPRAVEN" : "NEISPRAVEN");
  return hi && lo;
}

void setup() {
  Serial.begin(115200);
  delay(1500);
  Serial.println();
  Serial.println("=== PROVERKA VYVODOV NOVOY PLATY ===");
}

void loop() {
  int bad = 0;
  for (int i = 0; i < N; i++) {
    if (!checkPin(PINS[i], NOTE[i])) bad++;
    delay(60);
  }
  Serial.printf("\nitogo neispravnyh vyvodov: %d\n\n", bad);
  delay(4000);
}
