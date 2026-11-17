"""
rutas.py
Donde viven los archivos de A.X.I.S., tanto en desarrollo como dentro de un ejecutable PyInstaller.

Hay DOS tipos de archivos y NO se pueden mezclar:

  RECURSOS (solo lectura: mocks JSON, carpeta Frontend)
      Desarrollo : junto al codigo (src/backend, src/Frontend).
      Ejecutable : carpeta temporal sys._MEIPASS donde PyInstaller extrae el bundle.
                   Se BORRA al cerrar el programa.

  DATOS (escritura persistente: axis.db, axis.log)
      Nunca pueden vivir en sys._MEIPASS: se perderian en cada ejecucion.
      Prioridad:  1) variable AXIS_DATA_DIR
                  2) ejecutable: carpeta 'axis_data' junto al .exe (programa portatil);
                     si no se puede escribir ahi (p. ej. Program Files) -> %LOCALAPPDATA%\\AXIS
                  3) desarrollo: src/backend/data
"""

import os
import sys
import tempfile
from pathlib import Path

_BACKEND = Path(__file__).resolve().parent


def empaquetado() -> bool:
    """True si corre dentro de un ejecutable PyInstaller."""
    return bool(getattr(sys, "frozen", False))


def base_recursos() -> Path:
    if empaquetado():
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).resolve().parent))
    return _BACKEND


def recurso(nombre: str) -> Path:
    """Ruta a un recurso de solo lectura que vive en src/backend (p. ej. 'mock_facts.json')."""
    return base_recursos() / nombre


def ruta_frontend() -> Path:
    """Carpeta con index.html y los modulos JS del dashboard."""
    return base_recursos() / "Frontend" if empaquetado() else _BACKEND.parent / "Frontend"


def directorio_ejecutable() -> Path:
    return Path(sys.executable).resolve().parent if empaquetado() else _BACKEND


def _escribible(carpeta: Path) -> bool:
    try:
        carpeta.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=carpeta):
            pass
        return True
    except OSError:
        return False


def _datos_de_usuario() -> Path:
    if sys.platform == "win32":
        return Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local") / "AXIS"
    return Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share") / "axis"


def directorio_datos() -> Path:
    """Carpeta persistente para axis.db y axis.log. Con AXIS_DATA_DIR no se crea ni se prueba aqui."""
    manual = os.environ.get("AXIS_DATA_DIR")
    if manual:
        return Path(manual)
    if empaquetado():
        junto_al_exe = directorio_ejecutable() / "axis_data"
        return junto_al_exe if _escribible(junto_al_exe) else _datos_de_usuario()
    return _BACKEND / "data"
