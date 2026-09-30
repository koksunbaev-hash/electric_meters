// Проверка передатчика мультиметром.
//
// Передатчик модуля включён постоянно, вход DI медленно переключается.
// На клеммах A и B должно меняться напряжение, и менять знак.
//   DI высокий -> A выше B, примерно +2...+5 V
//   DI низкий  -> B выше A, примерно -2...-5 V
// Если знак меняется, передатчик исправен и сигнал на линию выдаёт.
// Если на A/B стоит почти ноль и не шевелится, передатчик мёртв.
//
// Щупы мультиметра: красный на A, чёрный на B, режим постоянного напряжения.
// Сначала три круга по модулю счётчика, потом три по модулю модема для сравнения.

#define MET_DI 27
#define MET_DE  5
#define MOD_DI 18
#define MOD_DE 22

void idleAll() {
  pinMode(MET_DE, OUTPUT); digitalWrite(MET_DE, LOW);
  pinMode(MOD_DE, OUTPUT); digitalWrite(MOD_DE, LOW);
  pinMode(MET_DI, OUTPUT); digitalWrite(MET_DI, HIGH);
  pinMode(MOD_DI, OUTPUT); digitalWrite(MOD_DI, HIGH);
}

void drive(const char* name, int di, int de, int rounds) {
  Serial.printf("\n=== %s: peredatchik vklyuchen, meryay mezhdu A i B ===\n", name);
  digitalWrite(de, HIGH);
  for (int k = 0; k < rounds; k++) {
    digitalWrite(di, HIGH);
    Serial.println("   DI vysokiy  -> zhdu  A dolzhno byt VYSHE B  (plyus)");
    delay(3000);
    digitalWrite(di, LOW);
    Serial.println("   DI nizkiy   -> zhdu  B dolzhno byt VYSHE A  (minus)");
    delay(3000);
  }
  digitalWrite(di, HIGH);
  digitalWrite(de, LOW);
}

void setup() {
  Serial.begin(115200);
  idleAll();
  delay(1500);
  Serial.println();
  Serial.println("=== PROVERKA PEREDATCHIKOV MULTIMETROM ===");
  Serial.println("Krasnyy schup na A, chernyy na B, rezhim postoyannogo napryazheniya.");
}

void loop() {
  drive("MODUL SCHETCHIKA", MET_DI, MET_DE, 3);
  Serial.println("\n   pauza 10 sekund, perestav schupy na modul modema");
  delay(10000);
  drive("MODUL MODEMA (obrazec)", MOD_DI, MOD_DE, 3);
  Serial.println("\n   pauza 10 sekund, perestav schupy obratno na schetchik");
  delay(10000);
}
