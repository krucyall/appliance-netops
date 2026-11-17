@echo off
REM Compila AXIS.exe (ejecutable unico) en Windows y lo verifica con --autotest.
REM Doble clic, o desde la raiz del repo:  build_windows.bat
setlocal
cd /d "%~dp0"

where py >nul 2>nul
if errorlevel 1 (
    echo [ERROR] No se encontro Python ^(comando "py"^). Instale Python 3.9 o superior desde python.org.
    pause
    exit /b 1
)

if not exist ".venv-build\Scripts\activate.bat" (
    echo [1/4] Creando entorno virtual de compilacion...
    py -m venv .venv-build
    if errorlevel 1 goto :error
)
call ".venv-build\Scripts\activate.bat"

echo [2/4] Instalando dependencias ^(la primera vez tarda varios minutos^)...
python -m pip install --upgrade pip
pip install -r requirements-build.txt
if errorlevel 1 goto :error

echo [3/4] Compilando con PyInstaller ^(tarda varios minutos^)...
pyinstaller --noconfirm --clean axis.spec
if errorlevel 1 goto :error

echo [4/4] Verificando el ejecutable...
dist\AXIS.exe --autotest
if errorlevel 1 goto :error

echo.
echo ============================================================
echo  Listo: dist\AXIS.exe   ^(doble clic para ejecutar^)
echo ============================================================
pause
exit /b 0

:error
echo.
echo [ERROR] La compilacion fallo. Revise los mensajes de arriba.
pause
exit /b 1
