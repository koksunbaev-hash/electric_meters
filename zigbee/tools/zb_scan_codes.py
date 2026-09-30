#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
zb_scan_codes.py — перебор кодов R1 (например C900..C999) через ZigBee-мост, печать всех, кроме заглушки.
Только чтение.

Запуск:  python zb_scan_codes.py [порт] [префикс] [от] [до] [команда]
         python zb_scan_codes.py COM8 C 900 999 R1
"""
import sys
import time
import serial

from zb_poller import Meter, MeterError, frame, GARBAGE
import re

PORT = sys.argv[1] if len(sys.argv) > 1 else "COM8"
PREFIX = sys.argv[2] if len(sys.argv) > 2 else "C"
FROM = int(sys.argv[3]) if len(sys.argv) > 3 else 900
TO = int(sys.argv[4]) if len(sys.argv) > 4 else 999
CMD = (sys.argv[5] if len(sys.argv) > 5 else "R1").encode()


def main():
    m = Meter(PORT, retries=1)
    m.open()
    print("сеанс:", m.open_session())
    found = 0
    for n in range(FROM, TO + 1):
        code = f"{PREFIX}{n}"
        try:
            time.sleep(0.15)
            resp = m._xfer(frame(CMD, code.encode() + b"()"), first=1.5)
        except MeterError as e:
            print(f"{code}: {e}")
            continue
        mm = re.search(rb"\(([^)]*)\)", resp)
        raw = mm.group(1).decode(errors="replace") if mm else resp.decode(errors="replace")
        if raw != GARBAGE and b"B0" not in resp:
            found += 1
            print(f"{CMD.decode()} {code}: {raw}")
    m.end_session()
    m.close()
    print(f"найдено кодов: {found}")


if __name__ == "__main__":
    main()
