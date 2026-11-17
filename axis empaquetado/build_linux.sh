#!/usr/bin/env bash
# Compila AXIS (ejecutable unico) en Linux/macOS y lo verifica con --autotest.
set -euo pipefail
cd "$(dirname "$0")"
python3 -m venv .venv-build
source .venv-build/bin/activate
python -m pip install --upgrade pip
pip install -r requirements-build.txt
pyinstaller --noconfirm --clean axis.spec
echo "--- Verificando el ejecutable ---"
./dist/AXIS --autotest
echo "Listo: dist/AXIS"
