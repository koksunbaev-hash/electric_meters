@echo off
chcp 65001 >nul
rem Опрос счётчика Saiman через ZigBee и отправка в OpenEgiz (MQTT 192.168.0.199:30511).
rem Порт координатора определяется автоматически. Лог пишется в zb_poller.log рядом со скриптом.
cd /d "%~dp0"
set PYTHONIOENCODING=utf-8
:loop
python -u zb_poller.py --port auto --interval 30
echo Опросчик остановился, перезапуск через 10 с...
timeout /t 10 /nobreak >nul
goto loop
