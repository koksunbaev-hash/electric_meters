"""
Поиск регистров счётчика DDSD6868 (CHZSJ) по Modbus RTU и проверка DL/T645-2007.
Подключение: клемма 6 (A) -> A адаптера, клемма 5 (B) -> B адаптера, порт COM5.

  python ddsd6868_scan.py                 # перебор скоростей, адресов, регистров 0x0000-0x00FF
  python ddsd6868_scan.py --baud 9600 --slave 1 --start 0 --count 64

Сверяй найденные значения с дисплеем счётчика (Menu -> листать показания):
напряжение ~220 (или 2200 при x0.1), частота 50.00, энергия в кВт*ч.
"""
import argparse, struct, time
import serial


def crc16(data: bytes) -> bytes:
    crc = 0xFFFF
    for b in data:
        crc ^= b
        for _ in range(8):
            crc = (crc >> 1) ^ 0xA001 if crc & 1 else crc >> 1
    return crc.to_bytes(2, "little")


def mb_request(slave, func, reg, count):
    body = bytes([slave, func]) + reg.to_bytes(2, "big") + count.to_bytes(2, "big")
    return body + crc16(body)


def mb_read(ser, slave, func, reg, count, wait=0.4):
    ser.reset_input_buffer()
    ser.write(mb_request(slave, func, reg, count))
    ser.flush()
    time.sleep(wait)
    r = ser.read(300)
    if len(r) >= 5 and r[0] == slave and r[1] == func and crc16(r[:-2]) == r[-2:]:
        return r[3:3 + r[2]]
    if len(r) >= 5 and r[0] == slave and r[1] == func | 0x80:
        return b""  # исключение Modbus: адрес есть, регистра нет
    return None


def show_words(reg, data):
    words = [int.from_bytes(data[i:i + 2], "big") for i in range(0, len(data) - 1, 2)]
    out = []
    for i, w in enumerate(words):
        line = f"  0x{reg + i:04X}: u16={w:6d}"
        if i + 1 < len(words):
            raw = data[2 * i:2 * i + 4]
            u32 = int.from_bytes(raw, "big")
            f_be = struct.unpack(">f", raw)[0]
            f_sw = struct.unpack(">f", raw[2:4] + raw[0:2])[0]
            line += f"  u32={u32:10d}  f32_be={f_be:12.4g}  f32_swap={f_sw:12.4g}"
        out.append(line)
    return "\n".join(out)


def dlt645_probe(ser):
    """DL/T645-2007: широковещательный адрес, чтение адреса счётчика (DI 04000401)."""
    for baud, par in [(2400, "E"), (1200, "E"), (9600, "N")]:
        ser.baudrate, ser.parity = baud, par
        frame = bytes([0x68]) + b"\xAA" * 6 + bytes([0x68, 0x13, 0x00])
        frame += bytes([sum(frame) & 0xFF, 0x16])
        ser.reset_input_buffer()
        ser.write(b"\xFE" * 4 + frame)
        ser.flush()
        time.sleep(0.8)
        r = ser.read(100)
        if 0x68 in r:
            i = r.index(0x68)
            addr = r[i + 1:i + 7][::-1].hex()
            print(f"DL/T645 @ {baud} 8{par}1: ответ {r[i:].hex(' ')}  адрес счётчика = {addr}")
            return baud, par, addr
    print("DL/T645: ответа нет")
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", default="COM5")
    ap.add_argument("--baud", type=int)
    ap.add_argument("--slave", type=int)
    ap.add_argument("--start", type=lambda x: int(x, 0), default=0)
    ap.add_argument("--count", type=int, default=256)
    a = ap.parse_args()

    ser = serial.Serial(a.port, 9600, bytesize=8, parity="N", stopbits=1, timeout=0.2)
    ser.rts = False
    ser.dtr = False

    bauds = [a.baud] if a.baud else [9600, 2400, 4800, 19200]
    slaves = [a.slave] if a.slave else [1, 2, 0x72, 0x21, 0x26, 0x11, 0x25]  # 0x72.. — из номера 2511262172
    found = None
    for baud in bauds:
        for par in ("N", "E"):
            ser.baudrate, ser.parity = baud, par
            for slave in slaves:
                for func in (0x03, 0x04):
                    r = mb_read(ser, slave, func, a.start, 2)
                    if r is not None:
                        print(f"Modbus ответ: {baud} 8{par}1, адрес {slave}, функция 0x{func:02X}")
                        found = (baud, par, slave, func)
                        break
                if found:
                    break
            if found:
                break
        if found:
            break

    if not found:
        print("Modbus: ответа нет ни на одной комбинации. Пробую DL/T645-2007...")
        dlt645_probe(ser)
        return

    baud, par, slave, func = found
    print(f"\nЧитаю регистры 0x{a.start:04X}..0x{a.start + a.count - 1:04X} блоками по 16\n")
    reg = a.start
    while reg < a.start + a.count:
        n = min(16, a.start + a.count - reg)
        r = mb_read(ser, slave, func, reg, n)
        if r:
            print(show_words(reg, r))
        elif r == b"":
            print(f"  0x{reg:04X}..: нет такого регистра")
        else:
            print(f"  0x{reg:04X}..: нет ответа")
        reg += n
        time.sleep(0.1)


if __name__ == "__main__":
    main()
