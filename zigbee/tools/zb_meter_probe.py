#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
zb_meter_probe.py — запросы к счётчику через ZigBee-мост:
компьютер → COM5 (DRF2658C) → ZigBee → DRF2659C → RS-485 → счётчик и обратно.

Запросы отправляются в двух видах:
  plain — обычный ASCII (бит 7 = 0);
  7E1   — бит 7 заменён на бит чётности (эмуляция 7E1 поверх 8-битного UART).
Ответ печатается как есть (hex) и в виде текста с отброшенным старшим битом.

Запуск:  python zb_meter_probe.py            (COM5, 38400)
"""
import sys
import time
import serial

PORT = sys.argv[1] if len(sys.argv) > 1 else "COM5"


def hx(b):
    return b.hex(" ").upper() if b else "(пусто)"


def txt(b):
    names = {0x0D: "<CR>", 0x0A: "<LF>", 0x02: "<STX>", 0x03: "<ETX>", 0x06: "<ACK>", 0x15: "<NAK>"}
    return "".join(names.get(x & 0x7F, chr(x & 0x7F) if 0x20 <= (x & 0x7F) < 0x7F else ".") for x in b)


def e7(data):
    """Эмуляция 7E1: бит 7 = чётность младших 7 бит."""
    return bytes((b & 0x7F) | (0x80 if bin(b & 0x7F).count("1") % 2 else 0) for b in data)


def parity_stats(b):
    if not b:
        return ""
    even = sum(1 for x in b if bin(x).count("1") % 2 == 0)
    high = sum(1 for x in b if x & 0x80)
    return f"байтов {len(b)}, с чётным числом единиц {even}, со старшим битом {high}"


def crc16_modbus(data):
    crc = 0xFFFF
    for b in data:
        crc ^= b
        for _ in range(8):
            crc = (crc >> 1) ^ 0xA001 if crc & 1 else crc >> 1
    return crc


def mb(addr, fc):
    body = bytes([addr, fc, 0, 0, 0, 2])
    c = crc16_modbus(body)
    return body + bytes([c & 0xFF, c >> 8])


PROBES = [
    ("IEC /?!  plain", b"/?!\r\n"),
    ("IEC /?!  7E1", e7(b"/?!\r\n")),
    ("IEC /?044741!  plain", b"/?044741!\r\n"),
    ("IEC /?044741!  7E1", e7(b"/?044741!\r\n")),
    ("Modbus 01/03", mb(1, 3)),
    ("Modbus 01/04", mb(1, 4)),
]


def ask(ser, req, wait=3.0, gap=0.6):
    ser.reset_input_buffer()
    ser.write(req)
    ser.flush()
    t0 = time.monotonic()
    last = None
    buf = bytearray()
    while time.monotonic() - t0 < wait:
        n = ser.in_waiting
        if n:
            buf += ser.read(n)
            last = time.monotonic()
        elif last and time.monotonic() - last > gap:
            break
        else:
            time.sleep(0.01)
    resp = bytes(buf)
    return resp[len(req):] if resp.startswith(req) else resp


def main():
    ser = serial.Serial(PORT, 38400, timeout=0.1)
    time.sleep(0.5)
    idle = ser.read(ser.in_waiting or 0)
    print(f"до запросов на {PORT}: {hx(idle)}")
    for name, req in PROBES:
        resp = ask(ser, req)
        print(f"\n{name:<22} → {hx(req)}")
        print(f"{'':<22} ← {hx(resp)}")
        if resp:
            print(f"{'':<22}   текст: {txt(resp)}")
            print(f"{'':<22}   {parity_stats(resp)}")
        time.sleep(1.6)  # IEC: даём счётчику сбросить незавершённую сессию
    ser.close()


if __name__ == "__main__":
    main()
