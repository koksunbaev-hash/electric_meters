// Декодер payload для ChirpStack v4
// Устройство: Ebyte EWD95M, DevEUI 0080E115053FE291
// Источник данных: счётчик CHZSJ DDSD6868, прошивка esp32-meter-lorawan-v7
//
// Куда вставить: ChirpStack -> Device profiles -> EWD95M -> вкладка Codec ->
// Payload codec: JavaScript functions -> вставить целиком в поле Functions -> Submit.
//
// Формат пакета, 12 байт, порт 2:
//   [0]      версия формата, всегда 0x01
//   [1..2]   напряжение,        uint16 big-endian, множитель 0.1   -> В
//   [3..4]   ток,               uint16 big-endian, множитель 0.01  -> А
//   [5..6]   активная мощность, uint16 big-endian                  -> Вт
//   [7]      коэффициент мощности, uint8, множитель 0.01
//   [8..11]  активная энергия,  uint32 big-endian, множитель 0.01  -> кВт*ч
//
// Проверено на реальном пакете AQh1AAAAAGQAAAA2:
//   01 08 75 00 00 00 00 64 00 00 00 36
//   -> 216.5 В, 0.00 А, 0 Вт, PF 1.00, 0.54 кВт*ч

function decodeUplink(input) {
  var b = input.bytes;

  if (b.length < 12) {
    return { data: {}, errors: ["korotkiy paket: " + b.length + " bayt, nuzhno 12"] };
  }
  if (b[0] !== 0x01) {
    return { data: {}, errors: ["neizvestnaya versiya formata: 0x" + b[0].toString(16)] };
  }

  var u16 = function (i) { return (b[i] << 8) | b[i + 1]; };
  var u32 = function (i) {
    return ((b[i] << 24) >>> 0) + (b[i + 1] << 16) + (b[i + 2] << 8) + b[i + 3];
  };

  var voltage_v  = u16(1) / 10;
  var current_a  = u16(3) / 100;
  var power_w    = u16(5);
  var pf         = b[7] / 100;
  var energy_kwh = u32(8) / 100;

  return {
    data: {
      // имена совпадают с признаками twin в OpenTwins
      voltage_a_v: voltage_v,
      current_a_a: current_a,
      active_power_w: power_w,
      power_factor_total: pf,
      active_energy_positive_kwh: energy_kwh,

      // дубликаты с короткими именами, удобно смотреть в интерфейсе ChirpStack
      voltage: voltage_v,
      current: current_a,
      power: power_w,
      power_factor: pf,
      energy: energy_kwh
    }
  };
}

// Команд вниз устройство не принимает, но ChirpStack требует эту функцию.
function encodeDownlink(input) {
  return { bytes: [] };
}
