#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
zb_poller.py — опрос счётчика Saiman «Орман» (код CI) через ZigBee-мост и публикация в MQTT OpenEgiz.

Цепочка: ПК → COM (координатор DRF2658C, 4800 8E1) ~ ZigBee ~ роутер DRF2659C (4800 8N1) → счётчик (IEC 62056-21, 7E1)
         → MQTT 192.168.0.199:30511, топик opentwins/<thing> → Ditto → Telegraf → InfluxDB → Grafana

Формат сообщения тот же, что у LoRaWAN-моста и Wi-Fi-счётчиков:
    {"device_id": "lower_lab:zigbee-meter", "data": {"voltage_a_v": ..., "current_a_a": ..., ...}}

Защита от сбоев:
  * повтор сеанса и каждого чтения при тайм-ауте;
  * проверка чётности каждого байта (7E1 эмулируется программно) и BCC кадра;
  * отбрасывание ответа-заглушки (D000D000000000) и значений вне физических пределов
    (в том числе случайных нулей напряжения/частоты и «откатов» энергии).

Запуск:
    python zb_poller.py                 # бесконечный опрос каждые 30 с
    python zb_poller.py --once          # один цикл (проверка)
    python zb_poller.py --dry-run       # без отправки в MQTT
    python zb_poller.py --port COM11 --interval 30 --thing lower_lab:zigbee-meter
"""
import argparse
import datetime
import json
import logging
import logging.handlers
import os
import re
import time
import uuid

import serial

try:
    import paho.mqtt.client as mqtt
except ImportError:
    mqtt = None

SOH, STX, ETX, ACK = b"\x01", b"\x02", b"\x03", b"\x06"
PASSWORD = os.environ.get("METER_PASSWORD", "00000000").encode("ascii")  # заводской пароль счётчика; свой задайте переменной METER_PASSWORD
GARBAGE = "D000D000000000"

# поле JSON: (код, делитель, (мин, макс)) — пределы отсекают битые ответы
FIELDS = {
    "voltage_a_v":                ("C900", 100.0, (150.0, 280.0)),
    "current_a_a":                ("C910", 100.0, (0.0, 80.0)),
    "active_power_w":             ("C921", 0.1, (0.0, 20000.0)),      # в единицах 10 Вт (0,01 кВт); сверено: C921/C941 = cos φ
    "power_factor_total":         ("C950", 1000.0, (0.0, 1.0)),
    "frequency_hz":               ("C970", 100.0, (45.0, 55.0)),
    "active_energy_positive_kwh": ("D513", 100.0, (0.0, 999999.99)),
}

log = logging.getLogger("zb_poller")


# ---------------------------------------------------------------- кадры IEC 62056-21
def e7(data):
    """7E1 поверх 8-битного UART: бит 7 = чётность младших 7 бит."""
    return bytes((b & 0x7F) | (0x80 if bin(b & 0x7F).count("1") % 2 else 0) for b in data)


def bcc(data):
    x = 0
    for b in data:
        x ^= b
    return x & 0x7F


def frame(cmd, body):
    inner = cmd + STX + body + ETX
    return SOH + inner + bytes([bcc(inner)])


class MeterError(Exception):
    pass


class Meter:
    def __init__(self, port, retries=3):
        self.port = port
        self.retries = retries
        self.ser = None

    def resolve_port(self):
        """--port auto: найти координатор по USB-идентификатору CH340 (номер COM меняется при переподключении)."""
        if self.port.lower() != "auto":
            return self.port
        from serial.tools import list_ports
        for p in list_ports.comports():
            if p.vid == 0x1A86 and p.pid == 0x7523:
                return p.device
        raise serial.SerialException("координатор (CH340) не найден среди COM-портов")

    def open(self):
        if self.ser is None or not self.ser.is_open:
            port = self.resolve_port()
            log.info("открываю %s", port)
            self.ser = serial.Serial(port, 4800, bytesize=8, parity=serial.PARITY_EVEN,
                                     stopbits=1, timeout=0.05)
            time.sleep(0.3)

    def close(self):
        if self.ser is not None:
            try:
                self.ser.close()
            except Exception:
                pass
        self.ser = None

    def _xfer(self, data, first=3.0, gap=0.4, total=6.0):
        """Отправить запрос, собрать ответ. Возвращает байты без бита чётности; ошибка чётности → MeterError."""
        self.ser.reset_input_buffer()
        self.ser.write(e7(data))
        self.ser.flush()
        t0 = time.monotonic()
        buf, last = bytearray(), None
        while time.monotonic() - t0 < total:
            n = self.ser.in_waiting
            if n:
                buf += self.ser.read(n)
                last = time.monotonic()
                m = bytes(b & 0x7F for b in buf)
                if (m.startswith(b"/") and m.endswith(b"\r\n")) or m in (ACK, b"\x15"):
                    break
                s = m.find(STX)
                k = m.find(ETX, s + 1) if s != -1 else -1  # первый ETX после STX: BCC может сам быть равен 0x03
                if k != -1 and len(m) > k + 1:
                    break
            elif last is None and time.monotonic() - t0 > first:
                break
            elif last is not None and time.monotonic() - last > gap:
                break
            else:
                time.sleep(0.005)
        if not buf:
            raise MeterError("тайм-аут")
        if any(bin(b).count("1") % 2 for b in buf):
            raise MeterError("ошибка чётности")
        return bytes(b & 0x7F for b in buf)

    def open_session(self):
        for attempt in range(1, self.retries + 1):
            try:
                ident = self._xfer(b"/?!\r\n")
                if not ident.startswith(b"/"):
                    raise MeterError(f"нет приветствия: {ident!r}")
                time.sleep(0.3)
                p0 = self._xfer(ACK + b"041\r\n")
                if b"P0" not in p0:
                    raise MeterError(f"нет P0: {p0!r}")
                time.sleep(0.3)
                if self._xfer(frame(b"P1", b"(" + PASSWORD + b")")) != ACK:
                    raise MeterError("пароль не принят")
                return ident.strip().decode(errors="replace")
            except MeterError as e:
                log.warning("сеанс, попытка %d/%d: %s", attempt, self.retries, e)
                self.end_session()
                time.sleep(2.0)
        raise MeterError("не удалось открыть сеанс")

    def end_session(self):
        try:
            self.ser.write(e7(frame(b"B0", b"")))
            self.ser.flush()
        except Exception:
            pass

    def read(self, code):
        """Прочитать сырое значение кода (строка в скобках) с проверкой BCC и повторами."""
        for attempt in range(1, self.retries + 1):
            try:
                time.sleep(0.2)
                resp = self._xfer(frame(b"R1", code.encode() + b"()"))
                s = resp.find(STX)
                e = resp.find(ETX, s + 1) if s != -1 else -1
                if s == -1 or e <= s or len(resp) < e + 2:
                    raise MeterError(f"неполный кадр {resp!r}")
                if bcc(resp[s + 1:e + 1]) != resp[e + 1]:
                    raise MeterError("BCC не сходится")
                m = re.search(rb"\(([^)]*)\)", resp[s:e])
                if not m:
                    raise MeterError(f"нет данных в скобках {resp!r}")
                raw = m.group(1).decode()
                if raw == GARBAGE:
                    raise MeterError("счётчик не знает код")
                return raw
            except MeterError as e:
                log.warning("%s, попытка %d/%d: %s", code, attempt, self.retries, e)
        raise MeterError(f"{code}: не прочитан")


# ---------------------------------------------------------------- цикл опроса
def parse(code, raw, div):
    digits = "".join(ch for ch in raw if ch.isdigit())
    if code == "D513":
        digits = digits[:8]  # первые 8 цифр — сумма по тарифам
    if not digits:
        raise ValueError(f"нет цифр в {raw!r}")
    return int(digits) / div


def poll_once(meter, last_energy):
    """Один сеанс: возвращает словарь data только с прошедшими проверку значениями."""
    meter.open()
    meter.open_session()
    data = {}
    try:
        for key, (code, div, (lo, hi)) in FIELDS.items():
            for attempt in range(2):  # второй заход — против случайного нуля или выброса
                try:
                    val = parse(code, meter.read(code), div)
                except (MeterError, ValueError) as e:
                    log.warning("%s: %s", key, e)
                    break
                ok = lo <= val <= hi
                if key == "active_energy_positive_kwh" and last_energy is not None and val < last_energy - 0.01:
                    ok = False  # энергия не может уменьшаться
                if ok:
                    data[key] = round(val, 3)
                    break
                log.warning("%s = %s вне допустимого диапазона, повтор", key, val)
    finally:
        meter.end_session()
    return data


def make_mqtt(host, port):
    cli = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="zigbee-poller-" + uuid.uuid4().hex[:8])
    cli.on_connect = lambda c, u, f, rc, p=None: log.info("MQTT %s:%s подключён (rc=%s)", host, port, rc)
    cli.on_disconnect = lambda c, u, f, rc, p=None: log.warning("MQTT отключён (rc=%s), переподключусь", rc)
    cli.reconnect_delay_set(1, 60)
    cli.connect_async(host, port, 60)
    cli.loop_start()
    return cli


def main():
    ap = argparse.ArgumentParser(description="Опрос счётчика через ZigBee и публикация в MQTT OpenEgiz")
    ap.add_argument("--port", default="COM11", help="COM-порт координатора DRF2658C")
    ap.add_argument("--mqtt-host", default="192.168.0.199")
    ap.add_argument("--mqtt-port", type=int, default=30511)
    ap.add_argument("--thing", default="lower_lab:zigbee-meter", help="имя двойника в OpenEgiz")
    ap.add_argument("--interval", type=float, default=30.0, help="период опроса, с")
    ap.add_argument("--once", action="store_true", help="один цикл и выход")
    ap.add_argument("--dry-run", action="store_true", help="не отправлять в MQTT")
    a = ap.parse_args()

    here = os.path.dirname(os.path.abspath(__file__))
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s",
        handlers=[logging.StreamHandler(),
                  # ротация, чтобы лог не заполнил SD-карту Raspberry Pi: 3 файла по 1 МБ
                  logging.handlers.RotatingFileHandler(os.path.join(here, "zb_poller.log"), maxBytes=1_000_000,
                                                       backupCount=3, encoding="utf-8")])

    cli = None
    if not a.dry_run:
        if mqtt is None:
            raise SystemExit("Нет paho-mqtt: pip install paho-mqtt")
        cli = make_mqtt(a.mqtt_host, a.mqtt_port)
        time.sleep(1.0)

    topic = "opentwins/" + a.thing
    meter = Meter(a.port)
    last_energy = None
    log.info("старт: порт %s, топик %s, период %.0f с", a.port, topic, a.interval)

    while True:
        t0 = time.monotonic()
        try:
            data = poll_once(meter, last_energy)
            if data:
                last_energy = data.get("active_energy_positive_kwh", last_energy)
                payload = json.dumps({"device_id": a.thing, "data": data}, ensure_ascii=False)
                if cli is not None:
                    info = cli.publish(topic, payload, qos=0)
                    log.info("→ %s %s %s", topic, payload, "" if info.rc == 0 else f"(ошибка rc={info.rc})")
                else:
                    log.info("(dry-run) %s %s", topic, payload)
            else:
                log.error("цикл без данных — ничего не отправлено")
        except MeterError as e:
            log.error("счётчик: %s", e)
        except serial.SerialException as e:
            log.error("COM-порт: %s — переоткрою через 5 с", e)
            meter.close()
            time.sleep(5)
        if a.once:
            break
        time.sleep(max(1.0, a.interval - (time.monotonic() - t0)))

    if cli is not None:
        time.sleep(1.0)
        cli.loop_stop()
        cli.disconnect()
    meter.close()


if __name__ == "__main__":
    main()

