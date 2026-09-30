// Проверка, жив ли модуль RS-485 на линии счётчика.
//
// У исправного и запитанного MAX485 в режиме приёма выход RO стоит в высоком
// уровне, пока линия свободна. Это состояние покоя RS-485.
//   RO высокий  -> модуль запитан, приёмник работает, ищем дальше в проводах A/B
//   RO низкий   -> нет питания модуля, либо A и B перепутаны, либо приёмник мёртв
//
// Для сравнения смотрим тот же вывод у модуля модема, он заведомо исправен.

#define MET_RO 26
#define MET_DE  5
#define MOD_RO 19
#define MOD_DE 22

int level(int ro, int de) {
  pinMode(de, OUTPUT); digitalWrite(de, LOW);   // режим приёма
  pinMode(ro, INPUT);
  delay(20);
  int hi = 0;
  for (int k = 0; k < 200; k++) { hi += digitalRead(ro); delayMicroseconds(200); }
  return hi;   // 200 = всё время высокий, 0 = всё время низкий
}

void setup() {
  Serial.begin(115200);
  delay(1500);
  Serial.println();
  Serial.println("=== SOSTOYANIE POKOYA NA VYHODAH RO ===");
}

void loop() {
  int m = level(MET_RO, MET_DE);
  int d = level(MOD_RO, MOD_DE);
  Serial.printf("  schetchik GPIO26: %3d/200 -> %s\n", m,
                m > 180 ? "VYSOKIY, modul zapitan" : (m < 20 ? "NIZKIY, modul mertv ili bez pitaniya" : "skachet, est aktivnost"));
  Serial.printf("  modem    GPIO19: %3d/200 -> %s\n", d,
                d > 180 ? "VYSOKIY, modul zapitan" : (d < 20 ? "NIZKIY, modul mertv ili bez pitaniya" : "skachet, est aktivnost"));
  Serial.println();
  delay(3000);
}
