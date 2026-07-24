@echo off
REM Запуск десктоп-интерфейса SHIFRATOR одним двойным кликом.
REM Использует venv проекта; открывает браузер автоматически.
setlocal
cd /d "%~dp0.."
if not exist "venv\Scripts\python.exe" (
  echo [ОШИБКА] Не найден venv\Scripts\python.exe в %CD%
  echo Создайте окружение и установите зависимости: pip install -r requirements.txt
  pause
  exit /b 1
)
"venv\Scripts\python.exe" "app\launcher.py"
pause
