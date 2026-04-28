@echo off
chcp 65001 > nul
cd /d "%~dp0"

echo VoiceStudio 시작 중...
echo.

if not exist .env (
    copy .env.example .env > nul
    echo .env 파일을 생성했습니다. 필요 시 수정하세요.
    echo.
)

python backend\main.py
pause
