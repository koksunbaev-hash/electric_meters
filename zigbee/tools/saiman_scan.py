#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
saiman_scan.py — поиск скорости, формата и протокола RS-485 счётчика
Saiman «Орман СО-Э711» через переходник USB-RS485 (CH340).

Подключение: переходник напрямую к клеммам A и B счётчика, на шине больше
ничего нет (роутер DRF2659C от A/B отключён). Счётчик под напряжением.

Запуск:
    python saiman_scan.py --port COM8            # этап 1 + этап 2 (Modbus-адреса 1..32)
    python saiman_scan.py --port COM8 --quick    # только этап 1 (~3-4 мин)
    python saiman_scan.py --port COM8 --bauds 4800 --formats 7E1,8N1

Этап 1: для каждой скорости и формата — IEC 62056-21 (/?!, /?адрес!),
        Modbus RTU (адрес 1, ф.03 и 04), Modbus ASCII, DLMS/HDLC (SNRM),
        протокол «Меркурий», DL/T 645.
Этап 2: Modbus RTU, адреса 1..32 (+41, 247), функции 03 и 04, только 8-битные форматы.

Всё, что печатается, дублируется в лог-файл рядом со скриптом.
"""
import argparse
import datetime
import os
import re
import sys
import time

try:
    import serial
except ImportError:
    sys.exit("Нет pyserial: выполните  pip install pyserial")

ALL_BAUDS = [4800, 9600, 2400, 1200, 19200, 600, 300]
ALL_FORMATS = ["7E1", "8N1", "8E1", "7O1", "8O1", "7N1"]
PARITY = {"N": serial.PARITY_NONE, "E": serial.PARITY_EVEN, "O": serial.PARITY_ODD}
IEC_BAUD_CODE = {300: "0", 600: "1", 1200: "2", 2400: "3", 4800: "4", 9600: "5", 19200: "6"}
IEC_CODE_BAUD = {v: k for k, v in IEC_BAUD_CODE.items()}
EXTRA_MODBUS_ADDRS = [41, 247]  # 41 — хвост серийного номера, 247 — максимальный адрес Modbus


# ---------------------------------------------------------------- вывод
class Log:
    def __init__(self, path):
        self.path = path
        self.f = open(path, "w", encoding="utf-8")

    def __call__(self, *parts):
        s = " ".join(str(p) for p in parts)
        print(s, flush=True)
        self.f.write(s + "\n")
        self.f.flush()


def hx(b):
    return b.hex(" ").upper() if b else "(пусто)"


def txt(b):
    """Текст ответа; старший бит отбрасывается (так 7E1, прочитанный как 8N1, становится читаемым)."""
    names = {0x0D: "<CR>", 0x0A: "<LF>", 0x02: "<STX>", 0x03: "<ETX>", 0x06: "<ACK>", 0x15: "<NAK>", 0x01: "<SOH>"}
    out = []
    for x in b:
        c = x & 0x7F
        out.append(names.get(c, chr(c) if 0x20 <= c < 0x7F else "."))
    return "".join(out)


def parity_hint(resp):
    if len(resp) < 4:
        return ""
    even = sum(1 for b in resp if bin(b).count("1") % 2 == 0)
    high = sum(1 for b in resp if b & 0x80)
    if high and even == len(resp):
        return "во всех байтах чётное число единиц, старший бит занят → это 7E1, прочитанный как 8 бит"
    if high and even == 0:
        return "во всех байтах нечётное число единиц, старший бит занят → это 7O1, прочитанный как 8 бит"
    return ""


# ---------------------------------------------------------------- контрольные суммы и кадры
def crc16_modbus(data):
    crc = 0xFFFF
    for b in data:
        crc ^= b
        for _ in range(8):
            crc = (crc >> 1) ^ 0xA001 if crc & 1 else crc >> 1
    return crc


def crc16_x25(data):
    crc = 0xFFFF
    for b in data:
        crc ^= b
        for _ in range(8):
            crc = (crc >> 1) ^ 0x8408 if crc & 1 else crc >> 1
    return crc ^ 0xFFFF


def modbus_frame(addr, fc, reg, cnt):
    body = bytes([addr, fc, reg >> 8, reg & 0xFF, cnt >> 8, cnt & 0xFF])
    c = crc16_modbus(body)
    return body + bytes([c & 0xFF, c >> 8])


def modbus_ascii_frame(addr, fc, reg, cnt):
    body = bytes([addr, fc, reg >> 8, reg & 0xFF, cnt >> 8, cnt & 0xFF])
    lrc = (-sum(body)) & 0xFF
    return b":" + (body + bytes([lrc])).hex().upper().encode() + b"\r\n"


def hdlc_frame(dest, src, ctrl):
    """Кадр HDLC (DLMS) без информационного поля: 7E A0 LL dest src ctrl HCS 7E."""
    inner = dest + bytes([src, ctrl])
    head = bytes([0xA0, 2 + len(inner) + 2]) + inner
    c = crc16_x25(head)
    return b"\x7e" + head + bytes([c & 0xFF, c >> 8]) + b"\x7e"


def mercury_test(addr):
    body = bytes([addr, 0x00])
    c = crc16_modbus(body)
    return body + bytes([c & 0xFF, c >> 8])


DLT645_READ_ADDR = bytes.fromhex("FEFEFEFE68AAAAAAAAAAAA681300DF16")
DLMS_CLIENT = 0x21  # публичный клиент 16
HDLC_CTRL = {0x73: "UA (соединение принято)", 0x1F: "DM (отказ: не тот адрес/режим)",
             0x97: "FRMR (ошибка кадра)", 0x53: "DISC", 0x93: "SNRM"}


# ---------------------------------------------------------------- распознавание ответов
def find_modbus(resp, addr, fc):
    for i in range(len(resp) - 1):
        if resp[i] != addr:
            continue
        f = resp[i + 1]
        if f == fc and i + 2 < len(resp):
            length = 3 + resp[i + 2] + 2
        elif f == (fc | 0x80):
            length = 5
        else:
            continue
        fr = resp[i:i + length]
        if len(fr) == length and crc16_modbus(fr[:-2]) == (fr[-2] | fr[-1] << 8):
            return fr
    return None


def find_modbus_ascii(resp):
    m = re.search(rb":([0-9A-Fa-f]{6,})\r\n", bytes(b & 0x7F for b in resp))
    if not m or len(m.group(1)) % 2:
        return None
    raw = bytes.fromhex(m.group(1).decode())
    return m.group(0) if sum(raw) & 0xFF == 0 else None


IEC_ID_RE = re.compile(rb"/([A-Za-z]{2}[A-Za-z0-9])([0-9A-Z])(\\\d)?([\x20-\x7E]*)\r\n")


def find_iec_id(resp):
    m = IEC_ID_RE.search(bytes(b & 0x7F for b in resp))
    if not m:
        return None
    return {"maker": m.group(1).decode(), "z": chr(m.group(2)[0]), "ident": m.group(4).decode()}


def find_hdlc(resp):
    for i in range(len(resp) - 2):
        if resp[i] == 0x7E and (resp[i + 1] & 0xF0) == 0xA0:
            length = ((resp[i + 1] & 0x07) << 8) | resp[i + 2]
            fr = resp[i + 1:i + 1 + length]
            if len(fr) < length or length < 5:
                return resp[i:], False, None
            ok = crc16_x25(fr[:-2]) == (fr[-2] | fr[-1] << 8)
            # после формата: адрес получателя, адрес отправителя (последний байт адреса имеет бит0=1), затем control
            j = 2
            for _ in range(2):
                while j < len(fr) and not fr[j] & 1:
                    j += 1
                j += 1
            ctrl = fr[j] if j < len(fr) else None
            return resp[i:i + length + 2], ok, ctrl
    return None


def find_mercury(resp):
    for i in range(len(resp) - 3):
        fr = resp[i:i + 4]
        if fr[1] <= 0x05 and crc16_modbus(fr[:2]) == (fr[2] | fr[3] << 8):
            return fr
    return None


def find_dlt645(resp):
    for i in range(len(resp) - 9):
        if resp[i] == 0x68 and resp[i + 7] == 0x68:
            ln = resp[i + 9]
            fr = resp[i:i + 12 + ln]
            if len(fr) == 12 + ln and fr[-1] == 0x16 and sum(fr[:-2]) & 0xFF == fr[-2]:
                return fr
    return None


def analyze(resp, meta):
    """Возвращает (протокол, описание) или (None, None)."""
    r = find_iec_id(resp)
    if r:
        z = IEC_CODE_BAUD.get(r["z"])
        return "IEC 62056-21", (f"производитель «{r['maker']}», идентификатор «{r['ident']}», "
                                f"код скорости Z='{r['z']}'" + (f" (до {z} бод)" if z else ""))
    if meta:
        fr = find_modbus(resp, *meta)
        if fr:
            kind = "исключение, код %d" % fr[2] if fr[1] & 0x80 else "данные " + hx(fr[3:-2])
            return "Modbus RTU", f"адрес {meta[0]}, функция {meta[1]:02X}: {kind}"
    fr = find_modbus_ascii(resp)
    if fr:
        return "Modbus ASCII", fr.decode(errors="replace").strip()
    h = find_hdlc(resp)
    if h:
        fr, ok, ctrl = h
        return "DLMS/HDLC", (f"кадр {hx(fr)}, FCS {'верна' if ok else 'НЕ сходится'}, "
                             f"control {ctrl:02X} = {HDLC_CTRL.get(ctrl, '?')}" if ctrl is not None else f"кадр {hx(fr)}")
    fr = find_dlt645(resp)
    if fr:
        return "DL/T 645", f"кадр {hx(fr)}, адрес счётчика {fr[1:7][::-1].hex()}"
    fr = find_mercury(resp)
    if fr:
        return "Меркурий", f"адрес {fr[0]}, код {fr[1]}"
    return None, None


# ---------------------------------------------------------------- порт и обмен
def char_time(baud, fmt):
    return (1 + int(fmt[0]) + (0 if fmt[1] == "N" else 1) + int(fmt[2])) / baud


def open_port(port, baud, fmt):
    return serial.Serial(port, baud, bytesize=int(fmt[0]), parity=PARITY[fmt[1]],
                         stopbits=int(fmt[2]), timeout=0.05, write_timeout=5)


def transact(ser, req, baud, fmt, first_timeout, gap, max_total=4.0):
    """Отправить запрос и собрать ответ до паузы gap. Эхо собственного запроса вырезается."""
    ct = char_time(baud, fmt)
    ser.reset_input_buffer()
    ser.write(req)
    ser.flush()
    t0 = time.monotonic()
    tx = len(req) * ct
    buf = bytearray()
    last = None
    while True:
        n = ser.in_waiting
        now = time.monotonic()
        if n:
            buf += ser.read(n)
            last = now
            continue
        echo_only = len(buf) <= len(req) and req[:len(buf)] == bytes(buf)
        if (last is None or echo_only) and now - t0 > tx + first_timeout:
            break
        if last is not None and not echo_only and now - last > gap:
            break
        if now - t0 > tx + max_total:
            break
        time.sleep(0.003)
    resp = bytes(buf)
    echo = bool(req) and resp.startswith(req)
    return (resp[len(req):] if echo else resp), echo


def iec_readout(ser, baud, log, max_time=25.0):
    code = IEC_BAUD_CODE.get(baud)
    if code is None:
        return
    ack = b"\x060" + code.encode() + b"0\r\n"
    log(f"      → чтение данных IEC (режим C, остаёмся на {baud}): {hx(ack)}  {txt(ack)}")
    ser.reset_input_buffer()
    ser.write(ack)
    ser.flush()
    t0 = last = time.monotonic()
    buf = bytearray()
    while time.monotonic() - t0 < max_time:
        n = ser.in_waiting
        if n:
            buf += ser.read(n)
            last = time.monotonic()
            m = bytes(b & 0x7F for b in buf)
            k = m.rfind(b"\x03")
            if k != -1 and len(m) > k + 1:
                break
        elif time.monotonic() - last > 2.5:
            break
        else:
            time.sleep(0.005)
    data = bytes(buf)
    if data.startswith(ack):
        data = data[len(ack):]
    if not data:
        log("      ← на запрос чтения ничего не пришло")
        return
    m = bytes(b & 0x7F for b in data)
    s, e = m.find(b"\x02"), m.rfind(b"\x03")
    bcc_note = ""
    if s != -1 and e > s and len(m) > e + 1:
        bcc = 0
        for b in m[s + 1:e + 1]:
            bcc ^= b
        bcc_note = "BCC верна" if bcc == m[e + 1] else f"BCC НЕ сходится ({bcc:02X} против {m[e + 1]:02X})"
    log(f"      ← получено {len(data)} байт. {bcc_note}")
    if len(data) > 256:
        log("        (больше 256 байт — через ZigBee-мост одним куском не пройдёт, читать нужно по отдельным кодам)")
    for line in m.replace(b"\x02", b"").split(b"\r\n"):
        if line.strip():
            log("        " + txt(line))
    time.sleep(1.6)


# ---------------------------------------------------------------- этапы
def stage1_probes(fmt, serial_no):
    p = [("IEC /?!", b"/?!\r\n", None)]
    digits = re.sub(r"\D", "", serial_no)
    for a in dict.fromkeys([digits, re.sub(r"[^0-9A-Za-z]", "", serial_no)]):
        if a:
            p.append((f"IEC /?{a}!", f"/?{a}!\r\n".encode(), None))
    p.append(("Modbus ASCII 01/03", modbus_ascii_frame(1, 3, 0, 2), None))
    if fmt[0] == "8":
        p += [("Modbus RTU 01/03", modbus_frame(1, 3, 0, 2), (1, 3)),
              ("Modbus RTU 01/04", modbus_frame(1, 4, 0, 2), (1, 4)),
              ("DLMS SNRM сервер 1", hdlc_frame(b"\x03", DLMS_CLIENT, 0x93), None),
              ("DLMS SNRM широковещ.", hdlc_frame(b"\xff", DLMS_CLIENT, 0x93), None),
              ("Меркурий тест", mercury_test(0), None),
              ("DL/T 645 адрес", DLT645_READ_ADDR, None)]
    return p


def run_stage1(a, log, hits, reactions):
    log("\n=== ЭТАП 1: все протоколы, все скорости и форматы ===")
    for baud in a.bauds:
        for fmt in a.formats:
            try:
                ser = open_port(a.port, baud, fmt)
            except (serial.SerialException, ValueError) as e:
                log(f"{baud:>6} {fmt}: порт не открылся в этом формате: {e}")
                continue
            got_any = False
            readout_done = False
            with ser:
                time.sleep(0.15)
                ser.reset_input_buffer()
                for name, req, meta in stage1_probes(fmt, a.serial):
                    is_iec = name.startswith("IEC")
                    resp, echo = transact(ser, req, baud, fmt,
                                          a.timeout + (0.4 if is_iec else 0), a.gap)
                    if not resp:
                        continue
                    got_any = True
                    proto, detail = analyze(resp, meta)
                    head = f"{baud:>6} {fmt}  {name:<22}"
                    if proto:
                        log(f"{head} ★ {proto}: {detail}")
                        hits.append({"baud": baud, "fmt": fmt, "probe": name, "proto": proto, "detail": detail})
                    else:
                        log(f"{head} ? ответ не распознан")
                        reactions.append({"baud": baud, "fmt": fmt, "probe": name, "resp": resp})
                    log(f"{'':>30}hex : {hx(resp)}" + ("   (эхо запроса вырезано)" if echo else ""))
                    log(f"{'':>30}text: {txt(resp)}")
                    hint = parity_hint(resp) if fmt[0] == "8" else ""
                    if hint:
                        log(f"{'':>30}подсказка: {hint}")
                    if proto == "IEC 62056-21" and not readout_done and not a.no_readout:
                        readout_done = True
                        iec_readout(ser, baud, log)
                    elif proto == "DLMS/HDLC":
                        transact(ser, hdlc_frame(b"\x03", DLMS_CLIENT, 0x53), baud, fmt, 0.5, a.gap)
                    if is_iec:
                        time.sleep(1.6)  # счётчик ждёт подтверждения ~1.5 с, даём сессии закрыться
            if not got_any:
                log(f"{baud:>6} {fmt}: тишина")


def run_stage2(a, log, hits, reactions):
    fmts = [f for f in a.formats if f[0] == "8"]
    addrs = list(range(1, a.maxaddr + 1)) + [x for x in EXTRA_MODBUS_ADDRS if x > a.maxaddr]
    log(f"\n=== ЭТАП 2: Modbus RTU, адреса {addrs[0]}..{a.maxaddr} + {EXTRA_MODBUS_ADDRS}, функции 03 и 04 ===")
    for baud in a.bauds:
        for fmt in fmts:
            try:
                ser = open_port(a.port, baud, fmt)
            except (serial.SerialException, ValueError) as e:
                log(f"{baud:>6} {fmt}: порт не открылся: {e}")
                continue
            n_found = 0
            with ser:
                time.sleep(0.15)
                for addr in addrs:
                    for fc in (3, 4):
                        req = modbus_frame(addr, fc, 0, 2)
                        resp, echo = transact(ser, req, baud, fmt, a.timeout * 0.6, a.gap)
                        if not resp:
                            continue
                        fr = find_modbus(resp, addr, fc)
                        if fr:
                            n_found += 1
                            kind = "исключение, код %d" % fr[2] if fr[1] & 0x80 else "данные " + hx(fr[3:-2])
                            detail = f"адрес {addr}, функция {fc:02X}: {kind}"
                            log(f"{baud:>6} {fmt}  ★ Modbus RTU: {detail}   hex: {hx(fr)}")
                            hits.append({"baud": baud, "fmt": fmt, "probe": f"Modbus {addr}/{fc:02X}",
                                         "proto": "Modbus RTU", "detail": detail})
                        else:
                            log(f"{baud:>6} {fmt}  ? адрес {addr} ф.{fc:02X}: {hx(resp)}  |  {txt(resp)}")
                            reactions.append({"baud": baud, "fmt": fmt, "probe": f"Modbus {addr}/{fc:02X}", "resp": resp})
            log(f"{baud:>6} {fmt}: опрошено {len(addrs)} адресов — " + (f"ответов Modbus: {n_found}" if n_found else "Modbus молчит"))


def summarize(log, hits, reactions):
    log("\n" + "=" * 70 + "\nИТОГ")
    if hits:
        groups = {}
        for h in hits:
            groups.setdefault((h["proto"], h["baud"]), []).append(h)
        log("НАЙДЕНО:")
        for (proto, baud), hs in groups.items():
            fmts = sorted({h["fmt"] for h in hs}, key=ALL_FORMATS.index)
            log(f"  {proto:<13} {baud:>6} бод   форматы: {', '.join(fmts)}")
            for d in dict.fromkeys(h["detail"] for h in hs):
                log(f"      {d}")
        log("\nЕсли один протокол сработал в нескольких форматах (например 7E1 и 8N1), это нормально:\n"
            "CH340 под Windows не отбрасывает байты с ошибкой чётности, а счётчик может прощать\n"
            "ошибки в запросе. Правильный формат — тот, где ответ читается без подсказки про старший бит.")
    elif reactions:
        log("Ни один протокол не распознан, но на некоторых настройках счётчик что-то отвечал:")
        seen = {}
        for r in reactions:
            seen.setdefault((r["baud"], r["fmt"]), 0)
            seen[(r["baud"], r["fmt"])] += 1
        for (b, f), n in seen.items():
            log(f"  {b:>6} {f}: ответов {n}")
        log("Пришлите лог целиком — по байтам видно, какая скорость «почти» подходит.")
    else:
        log("ПОЛНАЯ ТИШИНА. Проверки по порядку (каждая отсекает половину причин):\n"
            "  1) Горит ли дисплей счётчика? Нет — дальше смысла нет.\n"
            "  2) Поменяйте местами A и B на переходнике и запустите  --quick  ещё раз.\n"
            "  3) Посмотрите в меню дисплея счётчика параметры интерфейса (адрес, скорость).")


def main():
    ap = argparse.ArgumentParser(description="Поиск протокола RS-485 счётчика")
    ap.add_argument("--port", default="COM8")
    ap.add_argument("--bauds", default=",".join(map(str, ALL_BAUDS)))
    ap.add_argument("--formats", default=",".join(ALL_FORMATS))
    ap.add_argument("--serial", default="CI-044741", help="серийный номер для адресных запросов IEC")
    ap.add_argument("--maxaddr", type=int, default=32)
    ap.add_argument("--timeout", type=float, default=0.6, help="ожидание первого байта ответа, с")
    ap.add_argument("--gap", type=float, default=0.12, help="пауза, после которой ответ считается законченным, с")
    ap.add_argument("--quick", action="store_true", help="только этап 1")
    ap.add_argument("--full", action="store_true", help="этап 2 даже если Modbus уже найден")
    ap.add_argument("--no-readout", action="store_true", help="не запрашивать чтение данных по IEC")
    a = ap.parse_args()
    a.bauds = [int(x) for x in a.bauds.split(",")]
    a.formats = [x.strip().upper() for x in a.formats.split(",")]
    bad = [f for f in a.formats if f not in ALL_FORMATS]
    if bad:
        sys.exit(f"Неизвестный формат: {bad}. Допустимые: {ALL_FORMATS}")

    # самопроверка контрольных сумм по эталонным значениям
    assert crc16_modbus(b"123456789") == 0x4B37
    assert crc16_x25(b"123456789") == 0x906E
    assert modbus_ascii_frame(1, 3, 0, 2) == b":010300000002FA\r\n"

    here = os.path.dirname(os.path.abspath(__file__))
    log = Log(os.path.join(here, datetime.datetime.now().strftime("saiman_scan_%Y%m%d_%H%M%S.log")))
    log(f"Поиск протокола счётчика  {datetime.datetime.now():%Y-%m-%d %H:%M:%S}")
    log(f"Порт {a.port}, скорости {a.bauds}, форматы {a.formats}, лог: {log.path}")

    try:
        ser = open_port(a.port, a.bauds[0], "8N1")
    except serial.SerialException as e:
        log(f"\nНе открывается {a.port}: {e}\n"
            "Закройте Modbus Poll, Termite, Realterm, монитор порта Arduino — порт может держать только одна программа.")
        return
    with ser:
        log(f"\nПроверка тишины на линии (2 с, {a.bauds[0]} 8N1, без запросов)...")
        time.sleep(2.0)
        noise = ser.read(ser.in_waiting or 0)
        if noise:
            log(f"  ВНИМАНИЕ: без запроса пришло {len(noise)} байт: {hx(noise[:40])}\n"
                "  На шине есть другой передатчик (роутер DRF2659C?) или помеха. Отключите всё, кроме счётчика.")
        else:
            log("  тихо, хорошо")

    hits, reactions = [], []
    t0 = time.time()
    try:
        run_stage1(a, log, hits, reactions)
        if not a.quick and (a.full or not any(h["proto"] == "Modbus RTU" for h in hits)):
            run_stage2(a, log, hits, reactions)
    except KeyboardInterrupt:
        log("\nОстановлено по Ctrl+C")
    log(f"\nВремя работы: {(time.time() - t0) / 60:.1f} мин")
    summarize(log, hits, reactions)
    log(f"\nЛог сохранён: {log.path}")


if __name__ == "__main__":
    main()
