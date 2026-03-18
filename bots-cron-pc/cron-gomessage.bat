@echo off
chcp 65001 >nul
set LOGDIR=D:\workspace\mytools\bots-cron-pc\logs
if not exist "%LOGDIR%" mkdir "%LOGDIR%"

for /f "tokens=2 delims==" %%I in ('wmic os get localdatetime /value') do set DT=%%I
set LOGFILE=%LOGDIR%\gomessage-%DT:~0,8%.log

echo ========================================>> "%LOGFILE%"
echo [%date% %time%] start cron-gomessage>> "%LOGFILE%"
echo ========================================>> "%LOGFILE%"

echo [%time%] run goMessage --profile ai>> "%LOGFILE%"
python D:\workspace\clawbots\feishuMSG-xls\src\goMessage.py --profile ai >> "%LOGFILE%" 2>&1

echo [%time%] run goMessage --profile ot>> "%LOGFILE%"
python D:\workspace\clawbots\feishuMSG-xls\src\goMessage.py --profile ot >> "%LOGFILE%" 2>&1

echo [%time%] done>> "%LOGFILE%"
