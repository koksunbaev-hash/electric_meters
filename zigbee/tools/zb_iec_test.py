#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
zb_iec_test.py — сеанс IEC 62056-21 со счётчиком через ZigBee-мост.

Цепочка: ПК → координатор DRF2658C (USB, 4800 8E1) → ZigBee → роутер DRF2659C (RS-485, 4800 8N1) → счётчик (7E1).
Роутер передаёт 8N1, а кадр 8N1 совпадает по длине с кадром 7E1, поэтому бит чётности 7E1
программа сама кладёт в старший бит каждого байта. В ответе старший бит проверяется и отбрасывается.

Запуск:  python zb_iec_test.py [порт координатора]     (по умолчанию COM11)
"""
import sys
import time
import serial

PORT = sys.argv[1] if len(sys.argv) > 1 else "COM11"
SOH, STX, ETX, ACK = b"\x01", b"\x02", b"\x03", b"\x06"


def e7(data):
    """7E1 поверх 8N1: бит 7 = чётность младших 7 бит."""
    return bytes((b & 0x7F) | (0x80 if bin(b & 0x7F).count("1") % 2 else 0) for b in data)


def d7(data):
    """Обратное преобразование: проверка чётности и отбрасывание бита 7. Возвращает (данные, число ошибок)."""
    bad = sum(1 for b in data if bin(b).count("1") % 2)
    return bytes(b & 0x7F for b in data), bad


def txt(b):
    names = {0x0D: "<CR>", 0x0A: "<LF>", 0x02: "<STX>", 0x03: "<ETX>", 0x06: "<ACK>", 0x15: "<NAK>", 0x01: "<SOH>"}
    return "".join(names.get(x, chr(x) if 0x20 <= x < 0x7F else ".") for x in b)


def bcc(data):
    x = 0
    for b in data:
        x ^= b
    return bytes([x & 0x7F])


def frame(cmd, body):
    inner = cmd + STX + body + ETX
    return SOH + inner + bcc(inner)


def ask(ser, data, label, first=3.0, gap=0.4, total=8.0):
    ser.reset_input_buffer()
    t = time.monotonic()
    ser.write(e7(data))
    ser.flush()
    buf, last = bytearray(), None
    while time.monotonic() - t < total:
        n = ser.in_waiting
        if n:
            buf += ser.read(n)
            last = time.monotonic()
            m = bytes(b & 0x7F for b in buf)
            if (m.startswith(b"/") and m.endswith(b"\r\n")) or m in (ACK, b"\x15"):
                break
            k = m.rfind(ETX)
            if k != -1 and len(m) > k + 1:
                break
        elif last is None and time.monotonic() - t > first:
            break
        elif last is not None and time.monotonic() - last > gap:
            break
        else:
            time.sleep(0.005)
    raw = bytes(buf)
    data_, bad = d7(raw)
    dt = time.monotonic() - t
    print(f"→ {label:<16} {txt(data)}")
    print(f"← {'':<16} {txt(data_) if raw else '(пусто)'}"
          + (f"   [{dt:.2f} с, ошибок чётности: {bad}]" if raw else f"   [ждал {dt:.1f} с]"))
    return data_


def main():
    ser = serial.Serial(PORT, 4800, bytesize=8, parity=serial.PARITY_EVEN, stopbits=1, timeout=0.05)
    time.sleep(0.3)
    ident = ask(ser, b"/?!\r\n", "/?!")
    if not ident.startswith(b"/"):
        print("\nНет приветствия от счётчика через мост.")
        ser.close()
        return
    time.sleep(0.3)
    p0 = ask(ser, ACK + b"041\r\n", "ACK 041")
    if b"P0" in p0:
        time.sleep(0.3)
        ask(ser, frame(b"P1", b"(00000000)"), "P1 (00000000)")
    time.sleep(0.3)
    ser.write(e7(frame(b"B0", b"")))
    ser.flush()
    print("→ B0 (конец сессии)")
    ser.close()


if __name__ == "__main__":
    main()
