#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
bus_check.py — проверка проводки RS-485 до клемм счётчика с помощью роутера DRF2659C.

Роутер DRF2659C (4800 8E1, питание 12 В) подключается ПАРАЛЛЕЛЬНО переходнику
прямо к клеммам A и B счётчика. Тогда:
  CH340 (COM8) → провода → клеммы счётчика → роутер → ZigBee → координатор (COM5)
и обратно. Если байты проходят — провода до клемм счётчика исправны.
Заодно всё, что скажет сам счётчик, тоже попадёт на COM5 и COM8.

Запуск:  python bus_check.py [порт CH340] [порт координатора]     (по умолчанию COM8 COM5)
"""
import sys
import time
import serial

CH340 = sys.argv[1] if len(sys.argv) > 1 else "COM8"
COORD = sys.argv[2] if len(sys.argv) > 2 else "COM5"


def hx(b):
    return b.hex(" ").upper() if b else "(пусто)"


def xfer(src, dst, payload, wait=2.0):
    src.reset_input_buffer()
    dst.reset_input_buffer()
    src.write(payload)
    src.flush()
    got, t0 = b"", time.time()
    while time.time() - t0 < wait and len(got) < len(payload):
        got += dst.read(256)
    return got


def main():
    r = serial.Serial(CH340, 4800, bytesize=8, parity=serial.PARITY_EVEN, stopbits=1, timeout=0.1)
    c = serial.Serial(COORD, 38400, timeout=0.1)
    time.sleep(0.5)
    ok_fwd = ok_back = 0
    for i in range(5):
        p1 = b"CH340-TO-BUS-%d" % i
        g1 = xfer(r, c, p1)
        p2 = b"ROUTER-TO-BUS-%d" % i
        g2 = xfer(c, r, p2)
        ok_fwd += g1 == p1
        ok_back += g2 == p2
        print(f"#{i + 1}  CH340 → шина → роутер → COM5: {'OK' if g1 == p1 else 'НЕТ  ' + hx(g1)[:40]}"
              f"   |   COM5 → роутер → шина → CH340: {'OK' if g2 == p2 else 'НЕТ  ' + hx(g2)[:40]}")
        time.sleep(0.3)
    print(f"\nИТОГ: CH340 → роутер {ok_fwd}/5, роутер → CH340 {ok_back}/5")
    if ok_fwd == 5 and ok_back == 5:
        print("Провода до клемм счётчика исправны, полярность переходника и роутера совпадает.\n"
              "Раз счётчик при этом молчит — дело в интерфейсе самого счётчика.")
    elif ok_fwd == 0 and ok_back == 0:
        print("Байты не проходят ни в одну сторону: обрыв, плохой контакт, роутер без 12 В\n"
              "или A/B роутера подключены наоборот относительно переходника.")
    else:
        print("Проходит частично: плохой контакт или перепутана полярность одного из устройств.")
    r.close()
    c.close()


if __name__ == "__main__":
    main()
