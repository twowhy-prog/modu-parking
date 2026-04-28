@echo off
chcp 65001 > nul
cd /d "%~dp0"

:: GPU가 있으면 --device cuda 로 바꾸세요
python server.py --port 7860 --device cpu

pause
