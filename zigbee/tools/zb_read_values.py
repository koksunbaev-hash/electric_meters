#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
zb_read_values.py — чтение величин счётчика Saiman «Орман» (код CI) через ZigBee-мост.
Коды взяты из скриптов предыдущего сотрудника (volt_read_meter2.py, all_available.py, esp32_mqtt_meter_Ci_2_last.ino).
Печатает сырой ответ и пересчитанное значение. Только чтение.

Запуск:  python zb_read_values.py [порт координатора]     (по умолчанию COM11)
"""
import re
import sys
import time
import serial

from zb_iec_test import e7, d7, txt, frame, ACK

PORT = sys.argv[1] if len(sys.argv) > 1 else "COM11"

# (команда, код, делитель, подпись)
CODES = [
    ("R1", "C900", 100.0, "Напряжение, В"),
    ("R1", "C910", 100.0, "Ток, А"),
    ("R1", "C911", 1e6, "Активная мощность, кВт"),
    ("R2", "C921", 1.0, "Активная мощность (R2), Вт?"),
    ("R1", "E40", 1e6, "Активная мощность, кВт"),
    ("R1", "E41", 1e6, "Реактивная мощность, квар"),
    ("R1", "E42", 1e6, "Полная мощность, кВА"),
    ("R1", "9051", 1000.0, "Активная мощность (ESP32), ?"),
    ("R1", "C950", 1000.0, "cos φ"),
    ("R1", "C970", 100.0, "Частота, Гц"),
    ("R1", "D513", 100.0, "Активная энергия, кВт·ч"),
    ("R1", "C504", None, "Серийный номер"),
    ("R1", "C695", None, "Дата"),
    ("R1", "C680", None, "Время"),
]


def exchange(ser, data, first=3.0, gap=0.4, total=6.0):
    ser.reset_input_buffer()
    ser.write(e7(data))
    ser.flush()
    t = time.monotonic()
    buf, last = bytearray(), None
    while time.monotonic() - t < total:
        n = ser.in_waiting
        if n:
            buf += ser.read(n)
            last = time.monotonic()
            m = bytes(b & 0x7F for b in buf)
            if (m.startswith(b"/") and m.endswith(b"\r\n")) or m in (ACK, b"\x15"):
                break
            k = m.rfind(b"\x03")
            if k != -1 and len(m) > k + 1:
                break
        elif last is None and time.monotonic() - t > first:
            break
        elif last is not None and time.monotonic() - last > gap:
            break
        else:
            time.sleep(0.005)
    return d7(bytes(buf))


def main():
    ser = serial.Serial(PORT, 4800, bytesize=8, parity=serial.PARITY_EVEN, stopbits=1, timeout=0.05)
    time.sleep(0.3)
    ident, _ = exchange(ser, b"/?!\r\n")
    print("Приветствие:", txt(ident) or "(пусто)")
    if not ident.startswith(b"/"):
        return
    time.sleep(0.3)
    print("ACK 041     :", txt(exchange(ser, ACK + b"041\r\n")[0]) or "(пусто)")
    time.sleep(0.3)
    print("P1          :", txt(exchange(ser, frame(b"P1", b"(00000000)"))[0]) or "(пусто)")
    print()
    for cmd, code, div, name in CODES:
        time.sleep(0.3)
        t = time.monotonic()
        resp, bad = exchange(ser, frame(cmd.encode(), code.encode() + b"()"))
        dt = time.monotonic() - t
        m = re.search(rb"\(([^)]*)\)", resp)
        raw = m.group(1).decode(errors="replace") if m else None
        val = ""
        if raw and div and raw != "D000D000000000":
            digits = "".join(ch for ch in raw if ch.isdigit())
            if code == "D513":
                digits = digits[:8]
            if digits:
                val = f"= {int(digits) / div:g}"
        shown = raw if raw is not None else (txt(resp) or "(пусто)")
        print(f"{cmd} {code:<5} {name:<30} сырое: {shown:<22} {val}   [{dt:.2f} с, ош. чётн. {bad}]")
    ser.write(e7(frame(b"B0", b"")))
    ser.flush()
    ser.close()


if __name__ == "__main__":
    main()
