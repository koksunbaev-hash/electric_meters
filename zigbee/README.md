# Канал Zigbee: счётчик Saiman «Орман» СО-Э711

Счётчик опрашивается без микроконтроллера. Программа на Raspberry Pi посылает запросы IEC 62056-21 через USB-координатор DTK, радиомост Zigbee прозрачно передаёт их роутеру у счётчика, а роутер выдаёт их в линию RS-485.

```text
Счётчик Saiman ──RS-485──> роутер DRF2659C (12 В)
   ~~Zigbee~~> координатор DRF2658C ──USB──> Raspberry Pi ──MQTT──> OpenEgiz

Порты: счётчик 4800 7E1, роутер 4800 8N1, координатор 4800 8E1
```

## Что в папке

| Файл | Назначение |
|---|---|
| [`zb_poller.py`](zb_poller.py) | Опрос счётчика раз в 30 с и публикация в `opentwins/lower_lab:zigbee-meter` |
| [`zb-poller.service`](zb-poller.service) | Служба systemd для Raspberry Pi |
| [`start_zb_poller.bat`](start_zb_poller.bat) | Запуск опросчика в цикле на Windows (для отладки с ноутбука) |
| [`tools/zb_read_values.py`](tools/zb_read_values.py) | Разовое чтение всех кодов через мост |
| [`tools/zb_link_quality.py`](tools/zb_link_quality.py) | Доля ответов через радиоканал |
| [`tools/zb_scan_codes.py`](tools/zb_scan_codes.py) | Перебор кодов, например C900–C999 |
| [`tools/zb_iec_test.py`](tools/zb_iec_test.py) | Базовый сеанс IEC через мост |
| [`tools/saiman_scan.py`](tools/saiman_scan.py), [`tools/iec_explore.py`](tools/iec_explore.py) | Разведка протокола счётчика напрямую через переходник USB-RS485 |
| [`tools/bus_check.py`](tools/bus_check.py), [`tools/zb_meter_probe.py`](tools/zb_meter_probe.py) | Диагностика шины и моста |

Все инструменты только читают данные и ничего не записывают в счётчик.

## 1. Подключение к счётчику

<p align="center"><img src="../docs/images/01_meter_terminals.jpg" alt="Клеммы счётчика Saiman" width="360"></p>

RS-485 — две маленькие клеммы **A** и **B** правее центра колодки. «+ −» слева — импульсный выход, клеммы 1–4 силовые, на них 220 В. Многожильный провод обжимай наконечником НШВИ 0,5 и заводи под пластину, а не наматывай на винт.

## 2. Настройка роутера DRF2659C

<p align="center"><img src="../docs/images/04_dtk_router_settings.png" alt="Утилита DTK" width="420"></p>

Утилита DTK «Zigbee Module Configure CC2630/50», подключение через переходник USB-RS485, READ на 38400. Меняется **только** Baud Rate: 38400 → 4800, затем WRITE. PAN ID `2A01`, канал 20, Transparency не трогать. Роутеру нужно питание **12 В**: от 5 В его передатчик RS-485 не работает.

## 3. Подтяжка шины

Без неё счётчик отвечал 0 раз из 10, с ней — 10 из 10. Резисторы ставятся на клеммы роутера:

```text
+12 В ── 4,7 кОм ── A ── 470 Ом ── B ── 4,7 кОм ── GND
```

## 4. Установка на Raspberry Pi

Координатор втыкается в USB напрямую, без хаба. Пользователь должен быть в группе `dialout`.

```bash
mkdir -p ~/zigbee && cp zb_poller.py requirements.txt ~/zigbee/ && cd ~/zigbee
python3 -m venv venv
venv/bin/pip install -r requirements.txt
venv/bin/python zb_poller.py --port auto --once --dry-run      # один опрос без отправки

sudo cp zb-poller.service /etc/systemd/system/
sudo sed -i "s#/home/pi#$HOME#g; s/^User=pi/User=$USER/" /etc/systemd/system/zb-poller.service
sudo systemctl daemon-reload
sudo systemctl enable --now zb-poller
journalctl -u zb-poller -f
```

<p align="center"><img src="../docs/images/08_pi_service_running.png" alt="Служба zb-poller работает" width="760"></p>

Так выглядит рабочее состояние: `enabled`, `active (running)`, координатор найден на `/dev/ttyUSB0`, MQTT подключён.

Ключи опросчика: `--port` (`auto` ищет CH340 по VID:PID), `--interval`, `--thing`, `--mqtt-host`, `--mqtt-port`, `--once`, `--dry-run`. Заводской пароль счётчика берётся из переменной `METER_PASSWORD`.

## Коды величин

| Величина | Код | Пересчёт |
|---|---|---|
| Напряжение, В | C900 | ÷ 100 |
| Ток, А | C910 | ÷ 100 |
| Активная мощность, Вт | C921 | × 10 (дискретность 10 Вт) |
| Коэффициент мощности | C950 | ÷ 1000 |
| Частота, Гц | C970 | ÷ 100 |
| Энергия, кВт·ч | D513 | первые 8 цифр ÷ 100 |

Код C911 из старых скриптов — не мощность, он всегда равен нулю.

## Ограничения

- Сигнал 2,4 ГГц не проходит через два бетонных перекрытия: координатор ставится на одном этаже с роутером.
- Примерно 3 % запросов завершаются тайм-аутом и проходят со второй попытки — опросчик повторяет их сам.
