"""
Мост ChirpStack -> OpenTwins для счётчика DDSD6868.

Подписывается на MQTT ChirpStack, берёт пришедший пакет и публикует его
в MQTT OpenTwins в том виде, который понимает маппер Ditto:

    topic:   opentwins/lower_lab:lorawan-meter
    payload: {"device_id": "lower_lab:lorawan-meter", "data": {...}}

Пакет разбирается двумя способами. Если в ChirpStack установлен декодер,
берётся готовое поле object. Если декодера нет, те же 12 байт разбираются
здесь самостоятельно, поэтому мост работает в любом случае.

Запуск:
    python3 chirpstack_to_opentwins.py
    python3 chirpstack_to_opentwins.py --dry-run       только печатать, не публиковать
    python3 chirpstack_to_opentwins.py --chirpstack 192.168.8.122:1883 \
                                       --opentwins 192.168.0.199:30511

Адреса можно задать и переменными окружения CHIRPSTACK_MQTT и OPENTWINS_MQTT.

Требования: pip3 install paho-mqtt
Хост должен видеть обе сети: ChirpStack в 192.168.8.x и OpenTwins в 192.168.0.x.
"""
import argparse
import base64
import json
import logging
import os
import signal
import sys
import threading
import time

import paho.mqtt.client as mqtt

DEFAULT_CHIRPSTACK = os.environ.get("CHIRPSTACK_MQTT", "192.168.8.122:1883")
DEFAULT_OPENTWINS = os.environ.get("OPENTWINS_MQTT", "192.168.0.199:30511")

# Соответствие устройства LoRaWAN и twin в OpenTwins
DEVICES = {
    "0080e115053fe291": "lower_lab:lorawan-meter",
}

UP_TOPIC = "application/+/device/+/event/up"

KEYS = ("voltage_a_v", "current_a_a", "active_power_w",
        "power_factor_total", "active_energy_positive_kwh")

log = logging.getLogger("bridge")
stop = threading.Event()


def hostport(s, default_port):
    if ":" in s:
        h, p = s.rsplit(":", 1)
        return h, int(p)
    return s, default_port


def decode_payload(raw: bytes) -> dict:
    """Разбор 12 байт от esp32-meter-lorawan-v7. Тот же формат, что и в декодере ChirpStack."""
    if len(raw) < 12 or raw[0] != 0x01:
        return {}
    u16 = lambda i: (raw[i] << 8) | raw[i + 1]
    return {
        "voltage_a_v": u16(1) / 10,
        "current_a_a": u16(3) / 100,
        "active_power_w": u16(5),
        "power_factor_total": raw[7] / 100,
        "active_energy_positive_kwh": int.from_bytes(raw[8:12], "big") / 100,
    }


def extract(event: dict) -> dict:
    """Сначала пробуем готовый object от декодера ChirpStack, иначе разбираем сами."""
    obj = event.get("object") or {}
    data = {k: obj[k] for k in KEYS if k in obj}
    if data:
        return data
    b64 = event.get("data")
    if b64:
        try:
            return decode_payload(base64.b64decode(b64))
        except Exception:
            log.warning("не смог разобрать base64: %r", b64)
    return {}


class Publisher:
    """Издатель в OpenTwins с восстановлением связи."""

    def __init__(self, host, port, enabled=True):
        self.host, self.port, self.enabled = host, port, enabled
        self.cli = None
        if enabled:
            self.cli = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
            self.cli.on_disconnect = lambda c, u, d, rc, p: log.warning(
                "связь с OpenTwins потеряна, rc=%s, восстанавливаю", rc)
            self.cli.reconnect_delay_set(min_delay=1, max_delay=30)
            self._connect()
            self.cli.loop_start()

    def _connect(self):
        while not stop.is_set():
            try:
                self.cli.connect(self.host, self.port, keepalive=60)
                log.info("подключён к OpenTwins %s:%s", self.host, self.port)
                return
            except Exception as e:
                log.warning("OpenTwins недоступен (%s), повтор через 5 с", e)
                stop.wait(5)

    def send(self, topic, body):
        if not self.enabled:
            return True
        try:
            info = self.cli.publish(topic, body, qos=0)
            return info.rc == mqtt.MQTT_ERR_SUCCESS
        except Exception as e:
            log.error("не удалось опубликовать: %s", e)
            return False

    def close(self):
        if self.cli:
            self.cli.loop_stop()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--chirpstack", default=DEFAULT_CHIRPSTACK, help="адрес MQTT ChirpStack")
    ap.add_argument("--opentwins", default=DEFAULT_OPENTWINS, help="адрес MQTT OpenTwins")
    ap.add_argument("--dry-run", action="store_true", help="не публиковать, только печатать")
    ap.add_argument("--quiet", action="store_true", help="писать только ошибки")
    a = ap.parse_args()

    logging.basicConfig(
        level=logging.WARNING if a.quiet else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        stream=sys.stdout,
    )

    cs_host, cs_port = hostport(a.chirpstack, 1883)
    ot_host, ot_port = hostport(a.opentwins, 1883)

    pub = Publisher(ot_host, ot_port, enabled=not a.dry_run)
    stats = {"up": 0, "sent": 0, "skipped": 0}

    def on_connect(client, userdata, flags, rc, props):
        log.info("подключён к ChirpStack %s:%s, rc=%s", cs_host, cs_port, rc)
        client.subscribe(UP_TOPIC)

    def on_disconnect(client, userdata, disc_flags, rc, props):
        log.warning("связь с ChirpStack потеряна, rc=%s, восстанавливаю", rc)

    def on_message(client, userdata, msg):
        try:
            event = json.loads(msg.payload)
        except Exception:
            return
        stats["up"] += 1

        dev_eui = (event.get("deviceInfo") or {}).get("devEui", "").lower()
        thing = DEVICES.get(dev_eui)
        if not thing:
            log.info("пакет от неизвестного устройства %s, пропускаю", dev_eui)
            stats["skipped"] += 1
            return

        data = extract(event)
        if not data:
            log.warning("пустой разбор, data=%s", event.get("data"))
            stats["skipped"] += 1
            return

        body = json.dumps({"device_id": thing, "data": data})
        ok = pub.send(f"opentwins/{thing}", body)
        if ok:
            stats["sent"] += 1
        rssi = ((event.get("rxInfo") or [{}])[0]).get("rssi")
        log.info("%s  U=%s В  P=%s Вт  E=%s кВт*ч  RSSI=%s  %s",
                 thing, data.get("voltage_a_v"), data.get("active_power_w"),
                 data.get("active_energy_positive_kwh"), rssi,
                 "пробный запуск" if a.dry_run else ("отправлено" if ok else "ОШИБКА отправки"))

    cli = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
    cli.on_connect = on_connect
    cli.on_disconnect = on_disconnect
    cli.on_message = on_message
    cli.reconnect_delay_set(min_delay=1, max_delay=30)

    def shutdown(signum, frame):
        log.info("остановка: принято %d, отправлено %d, пропущено %d",
                 stats["up"], stats["sent"], stats["skipped"])
        stop.set()
        cli.disconnect()

    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)

    while not stop.is_set():
        try:
            cli.connect(cs_host, cs_port, keepalive=60)
            break
        except Exception as e:
            log.warning("ChirpStack недоступен (%s), повтор через 5 с", e)
            stop.wait(5)

    try:
        cli.loop_forever(retry_first_connection=True)
    finally:
        pub.close()


if __name__ == "__main__":
    main()
