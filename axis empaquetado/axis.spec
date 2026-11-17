# -*- mode: python ; coding: utf-8 -*-
# PyInstaller: un solo ejecutable (--onefile) de A.X.I.S.
#   Compilar:   pyinstaller --noconfirm --clean axis.spec
#   Sin consola (ventana oculta, se cierra sola por inactividad):   AXIS_SIN_CONSOLA=1 pyinstaller --noconfirm --clean axis.spec
import importlib.util
import os
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_submodules, copy_metadata

RAIZ = Path(SPECPATH).resolve()          # carpeta donde esta este .spec (raiz del repo)
BACKEND = RAIZ / "src" / "backend"
FRONTEND = RAIZ / "src" / "Frontend"
CON_CONSOLA = os.environ.get("AXIS_SIN_CONSOLA", "0") != "1"

# ---- Datos que viajan dentro del .exe (se extraen a sys._MEIPASS; ver src/backend/rutas.py)
datas = [(str(FRONTEND), "Frontend")]                                   # -> <bundle>/Frontend/...
datas += [(str(p), ".") for p in sorted(BACKEND.glob("mock_*.json"))]   # -> <bundle>/mock_*.json

# ---- NAPALM carga sus drivers con importlib (get_network_driver("ios")): PyInstaller no los ve solo.
# Se incluye el nucleo y el driver IOS (los demas arrastrarian junos-eznc, ncclient, pyeapi... = cientos de MB).
hiddenimports = (
    collect_submodules("uvicorn")
    + collect_submodules("napalm", filter=lambda n: n.startswith(("napalm.base", "napalm.ios")))
    + collect_submodules("netmiko")
    + ["conexion", "mock_dispositivo", "extraccion_config", "extractores", "reglas_puertos",
       "almacenamiento", "auditoria", "telemetria", "api_auditoria", "api_telemetria", "mtu_stress", "rutas"]
)
datas += collect_data_files("napalm") + collect_data_files("netmiko") \
    + collect_data_files("ntc_templates") + collect_data_files("textfsm")

# ---- Metadata de paquetes (*.dist-info): NAPALM/Netmiko preguntan sus versiones y las de sus dependencias con
# importlib.metadata.version("netutils") y similares. PyInstaller no la incluye sola y sin ella el .exe falla con
# "PackageNotFoundError: No package metadata was found for netutils" (en desarrollo no se nota).
for paquete in ("napalm", "netmiko"):
    datas += copy_metadata(paquete, recursive=True)

# ---- Driver comunitario VyOS: solo si esta instalado en el entorno de compilacion
if importlib.util.find_spec("napalm_vyos"):
    hiddenimports += collect_submodules("napalm_vyos")
    datas += collect_data_files("napalm_vyos") + copy_metadata("napalm_vyos")

a = Analysis(
    [str(BACKEND / "launcher.py")],
    pathex=[str(BACKEND)],               # los modulos del backend se importan "planos" (import evaluador)
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=["tkinter", "pytest", "_pytest", "matplotlib", "IPython"],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="AXIS",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,                            # UPX dispara falsos positivos de antivirus y ralentiza el arranque
    runtime_tmpdir=None,
    console=CON_CONSOLA,                  # True: ventana de consola (cerrarla detiene el programa)
    disable_windowed_traceback=False,
    icon=None,                            # icon="ruta\\axis.ico" si quieres un icono propio
)
