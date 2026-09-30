#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
zb_link_quality.py — надёжность канала «ПК → ZigBee → счётчик»: N раз /?!, после каждого B0.

Запуск:  python zb_link_quality.py [порт координатора] [число попыток]    (по умолчанию COM11 10)
"""
import sys
import time
import serial

from zb_iec_test import e7, frame

PORT = sys.argv[1] if len(sys.argv) > 1 else "COM11"
N = int(sys.argv[2]) if len(sys.argv) > 2 else 10


def main():
    s = serial.Serial(PORT, 4800, parity=serial.PARITY_EVEN, timeout=0.1)
    time.sleep(0.3)
    ok = 0
    for i in range(N):
        s.reset_input_buffer()
        t = time.time()
        s.write(e7(b"/?!\r\n"))
        s.flush()
        r = b""
        while time.time() - t < 3 and not r.endswith(b"\x0a"):
            r += s.read(64)
        good = r.startswith(b"\xaf") and r.endswith(b"\x0a")
        ok += good
        print(f"{i + 1:2} {'OK ' if good else 'НЕТ'} {time.time() - t:4.2f} с  {r.hex(' ').upper()[:36]}")
        s.write(e7(frame(b"B0", b"")))
        s.flush()
        time.sleep(2.5)
    print(f"итог: {ok} / {N}")
    s.close()


if __name__ == "__main__":
    main()
