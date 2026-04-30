@echo off
setlocal
cd /d "%~dp0"
py -3.12 runpod_auto_pod_watcher.py %*
endlocal
