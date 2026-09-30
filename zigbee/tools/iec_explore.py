#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
iec_explore.py — разведка счётчика по IEC 62056-21 (4800 7E1) напрямую через переходник.
Только чтение: режим readout (ACK 0Z0) и режим программирования (ACK 0Z1) с командами R1.
Команды записи (W1) не отправляются.

Запуск:  python iec_explore.py [порт]      (по умолчанию COM5)
"""
import sys
import time
import serial

PORT = sys.argv[1] if len(sys.argv) > 1 else "COM5"
SOH, STX, ETX, ACK = b"\x01", b"\x02", b"\x03", b"\x06"

# Кандидаты кодов OBIS (C.D.E): энергия, напряжение, ток, мощность, cos φ, частота, дата/время, серийный номер
OBIS = ["1.8.0", "1.8.1", "32.7.0", "31.7.0", "21.7.0", "1.7.0", "13.7.0", "33.7.0", "14.7.0",
        "0.9.1", "0.9.2", "C.1.0", "0.0.0", "96.1.0"]


def txt(b):
    names = {0x0D: "<CR>", 0x0A: "<LF>", 0x02: "<STX>", 0x03: "<ETX>", 0x06: "<ACK>", 0x15: "<NAK>", 0x01: "<SOH>"}
    return "".join(names.get(x & 0x7F, chr(x & 0x7F) if 0x20 <= (x & 0x7F) < 0x7F else ".") for x in b)


def bcc(data):
    x = 0
    for b in data:
        x ^= b
    return bytes([x & 0x7F])


def frame(cmd, body):
    """SOH C D STX body ETX BCC (BCC — XOR от байта после SOH до ETX включительно)."""
    inner = cmd + STX + body + ETX
    return SOH + inner + bcc(inner)


def rx(ser, first=2.0, gap=0.3, total=8.0):
    t0 = time.monotonic()
    last = None
    buf = bytearray()
    while time.monotonic() - t0 < total:
        n = ser.in_waiting
        if n:
            buf += ser.read(n)
            last = time.monotonic()
            m = bytes(b & 0x7F for b in buf)
            k = m.rfind(ETX)
            if k != -1 and len(m) > k + 1:   # ETX + BCC — кадр закончен
                break
            if m.endswith(b"\r\n") and m.startswith(b"/"):
                break
        elif last is None and time.monotonic() - t0 > first:
            break
        elif last is not None and time.monotonic() - last > gap:
            break
        else:
            time.sleep(0.005)
    return bytes(buf)


def send(ser, data, label):
    ser.reset_input_buffer()
    ser.write(data)
    ser.flush()
    r = rx(ser)
    if r.startswith(data):
        r = r[len(data):]
    print(f"→ {label:<22} {txt(data)}")
    print(f"← {'':<22} {txt(r) if r else '(пусто)'}")
    return r


def hello(ser):
    r = send(ser, b"/?!\r\n", "/?!")
    return r.startswith(b"/")


def main():
    ser = serial.Serial(PORT, 4800, bytesize=7, parity=serial.PARITY_EVEN, stopbits=1, timeout=0.05)
    time.sleep(0.3)

    if "--readout" in sys.argv:
        print("=== 1. Readout (ACK 0 4 0), ждём до 8 с ===")
        if hello(ser):
            time.sleep(0.3)
            send(ser, ACK + b"040\r\n", "ACK 040 (readout)")
        ser.write(frame(b"B0", b""))
        ser.flush()
        time.sleep(5.0)

    print("\n=== 2. Режим программирования (ACK 0 4 1) ===")
    if not hello(ser):
        print("нет приветствия")
        return
    time.sleep(0.3)
    r = send(ser, ACK + b"041\r\n", "ACK 041 (программир.)")
    if not r:
        print("Счётчик не вошёл в режим программирования")
        return
    pw = next((a.split("=", 1)[1] for a in sys.argv if a.startswith("--password=")), None)
    if pw is not None:
        # ровно одна попытка: при любом ответе, кроме ACK, закрываем сессию
        time.sleep(0.25)
        r = send(ser, frame(b"P1", b"(" + pw.encode() + b")"), f"P1 ({pw})")
        if not r or (r[0] & 0x7F) != 0x06:
            print("Пароль не принят — закрываю сессию, повторных попыток нет")
            ser.write(frame(b"B0", b""))
            ser.flush()
            return
        print("Пароль принят (ACK)")
    codes = next((a.split("=", 1)[1].split(",") for a in sys.argv if a.startswith("--codes=")), OBIS)
    for code in codes:
        time.sleep(0.25)
        send(ser, frame(b"R1", code.encode() + b"()"), f"R1 {code}")
    time.sleep(0.25)
    ser.write(frame(b"B0", b""))  # закрыть сессию
    ser.flush()
    print("\n→ B0 (конец сессии)")
    ser.close()


if __name__ == "__main__":
    main()
