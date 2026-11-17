"""
launcher.py
Punto de entrada del ejecutable de A.X.I.S. (tambien sirve en desarrollo: python launcher.py).

Que hace al ejecutarlo (doble clic en AXIS.exe):
  1. Si A.X.I.S. ya esta corriendo en el puerto, solo abre el navegador y termina (no duplica).
  2. Si el puerto lo usa OTRO programa, avisa con un mensaje claro y termina.
  3. Levanta FastAPI + Uvicorn en http://127.0.0.1:8000 (solo local, no se expone a la red).
  4. Cuando el servidor esta listo, abre el navegador predeterminado en esa direccion.
  5. Se detiene limpiamente con Ctrl+C, al cerrar la consola o por SIGTERM: apaga la telemetria
     y cierra SQLite antes de salir (via el lifespan de main.py).

Variables de entorno (todas opcionales):
  AXIS_PORT=8000              puerto
  AXIS_HOST=127.0.0.1         0.0.0.0 para abrirlo a la red local (p. ej. tablet por WiFi): usalo con cuidado
  AXIS_MODO=mock              equipo simulado en vez de un equipo real
  AXIS_NO_NAVEGADOR=1         no abrir el navegador
  AXIS_AUTOCIERRE_MIN=10      cerrar tras N minutos sin actividad (por defecto 0 = nunca; 10 si no hay consola)
  AXIS_DATA_DIR=...           donde guardar axis.db / axis.log (ver rutas.py)

Opciones:  --autotest   verifica que el paquete trae todo lo necesario y termina (codigo 0 = OK)
"""

import json
import multiprocessing
import os
import shutil
import signal
import socket
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
import webbrowser

HOST = os.environ.get("AXIS_HOST", "127.0.0.1")
try:
    PUERTO = int(os.environ.get("AXIS_PORT", "8000"))
except ValueError:
    PUERTO = 8000


def _host_navegador(host: str = HOST) -> str:
    return "127.0.0.1" if host in ("0.0.0.0", "", "::") else host


def url_base(puerto: int = PUERTO) -> str:
    return f"http://{_host_navegador()}:{puerto}"


# ----------------------------------------------------------------------------- consola y avisos
def preparar_salida():
    """
    Un .exe sin consola (--noconsole) arranca con sys.stdout/sys.stderr = None y uvicorn falla al
    configurar el logging. En ese caso se redirigen a axis.log (tambien sirve para diagnosticar).
    Retorna el archivo de log si hubo que redirigir, o None si hay consola.
    """
    if sys.stdout is not None and sys.stderr is not None:
        return None
    import rutas

    carpeta = rutas.directorio_datos()
    carpeta.mkdir(parents=True, exist_ok=True)
    log = open(carpeta / "axis.log", "a", encoding="utf-8", buffering=1)
    if sys.stdout is None:
        sys.stdout = log
    if sys.stderr is None:
        sys.stderr = log
    return log


def avisar(mensaje: str, sin_consola: bool) -> None:
    """Muestra un error al usuario: cuadro de dialogo si no hay consola; pausa si la consola se cerraria sola."""
    print(mensaje)
    if sin_consola and sys.platform == "win32":
        try:
            import ctypes
            ctypes.windll.user32.MessageBoxW(0, mensaje, "A.X.I.S.", 0x10)
        except Exception:
            pass
    elif sys.stdin is not None and sys.stdin.isatty():
        try:
            input("Presione Enter para salir...")
        except EOFError:
            pass


# ----------------------------------------------------------------------------- deteccion de instancias
def instancia_activa(puerto: int = PUERTO) -> bool:
    """True si en ese puerto ya responde un motor A.X.I.S. (GET /api/estado)."""
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{puerto}/api/estado", timeout=1.5) as r:
            return json.load(r).get("estado") == "ok"
    except (urllib.error.URLError, OSError, ValueError):
        return False


def puerto_ocupado(puerto: int = PUERTO) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(1.0)
        return s.connect_ex(("127.0.0.1", puerto)) == 0


# ----------------------------------------------------------------------------- actividad y autocierre
class SeguimientoActividad:
    """Middleware ASGI minimo: recuerda cuando llego la ultima peticion HTTP (para el autocierre)."""

    def __init__(self, app):
        self.app = app
        self.ultima = time.monotonic()

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            self.ultima = time.monotonic()
        await self.app(scope, receive, send)


def vigilar_inactividad(servidor, seguimiento: SeguimientoActividad, minutos: float) -> None:
    """Pide el apagado ordenado si pasan 'minutos' sin ninguna peticion (el navegador envia un latido cada 10 s)."""
    while not servidor.should_exit:
        time.sleep(1)
        if time.monotonic() - seguimiento.ultima > minutos * 60:
            print(f"Sin actividad durante {minutos:g} min: cerrando A.X.I.S.")
            servidor.should_exit = True


def abrir_navegador_cuando_este_listo(servidor, url: str) -> None:
    for _ in range(300):  # hasta ~60 s
        if servidor.started:
            print(f"Abriendo el navegador en {url}")
            webbrowser.open(url)
            return
        if servidor.should_exit:
            return
        time.sleep(0.2)


def instalar_cierre_de_consola_windows(servidor, terminado: threading.Event):
    """
    Windows: al cerrar la ventana de la consola (X), el sistema envia CTRL_CLOSE_EVENT y mata el proceso
    a los ~5 s. Este manejador pide el apagado ordenado y espera a que termine. (No aplica a Ctrl+C: eso
    ya lo maneja uvicorn.) Devuelve la referencia, que debe mantenerse viva mientras corra el servidor.
    """
    import ctypes
    from ctypes import wintypes

    tipo_manejador = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.DWORD)

    def manejador(evento):
        if evento in (2, 5, 6):  # CTRL_CLOSE_EVENT, CTRL_LOGOFF_EVENT, CTRL_SHUTDOWN_EVENT
            servidor.should_exit = True
            terminado.wait(timeout=4)
            return True
        return False

    referencia = tipo_manejador(manejador)
    ctypes.windll.kernel32.SetConsoleCtrlHandler(referencia, True)
    return referencia


# ----------------------------------------------------------------------------- autotest
def _puerto_libre() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _http(metodo: str, url: str, cuerpo=None):
    datos = json.dumps(cuerpo).encode() if cuerpo is not None else None
    peticion = urllib.request.Request(url, data=datos, method=metodo, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(peticion, timeout=30) as r:
        contenido = r.read().decode("utf-8")
        return r.status, contenido


def autotest() -> int:
    """Comprueba el paquete de punta a punta usando el equipo simulado. Retorna 0 si todo esta bien."""
    fallos = []
    temporal = tempfile.mkdtemp(prefix="axis_autotest_")
    os.environ.update(AXIS_DATA_DIR=temporal, AXIS_MODO="mock", AXIS_NO_NAVEGADOR="1")

    def comprobar(nombre, funcion):
        try:
            detalle = funcion()
            print(f"[OK]    {nombre}" + (f": {detalle}" if detalle else ""))
        except Exception as error:
            fallos.append(nombre)
            print(f"[FALLO] {nombre} -> {type(error).__name__}: {error}")

    import rutas

    print(f"A.X.I.S. autotest | empaquetado={rutas.empaquetado()} | recursos={rutas.base_recursos()}")

    def frontend():
        carpeta = rutas.ruta_frontend()
        assert (carpeta / "index.html").is_file(), f"falta index.html en {carpeta}"
        return f"{len(list(carpeta.glob('*.js')))} modulos JS + index.html + style.css"

    def mocks():
        nombres = ["mock_facts.json", "mock_configs.json", "mock_ospf.json", "mock_seguridad.json", "mock_equipo.json"]
        for nombre in nombres:
            json.loads(rutas.recurso(nombre).read_text(encoding="utf-8"))
        return f"{len(nombres)} archivos JSON validos"

    def drivers():
        import napalm
        from netmiko import ConnectHandler  # noqa: F401  (comprueba que netmiko y paramiko cargan)

        disponibles = [d for d in ("ios",) if napalm.get_network_driver(d)]
        try:
            napalm.get_network_driver("vyos")
            disponibles.append("vyos")
        except Exception:
            disponibles.append("vyos NO instalado (opcional: pip install napalm-vyos antes de compilar)")
        return "drivers NAPALM: " + ", ".join(disponibles)

    def servidor_y_api():
        import uvicorn
        import main as axis_api

        puerto = _puerto_libre()
        servidor = uvicorn.Server(uvicorn.Config(axis_api.app, host="127.0.0.1", port=puerto, log_level="warning",
                                                 loop="asyncio", http="h11", ws="none", lifespan="on"))
        hilo = threading.Thread(target=servidor.run, daemon=True)
        hilo.start()
        for _ in range(100):
            if servidor.started:
                break
            time.sleep(0.1)
        assert servidor.started, "el servidor no arranco"
        base = f"http://127.0.0.1:{puerto}"
        try:
            estado, html = _http("GET", base + "/")
            assert estado == 200 and "A.X.I.S." in html, "el backend no sirve el Frontend en /"
            estado, js = _http("GET", base + "/config.js")
            assert estado == 200 and "API_BASE" in js
            estado, cuerpo = _http("GET", base + "/api/estado")
            assert json.loads(cuerpo)["estado"] == "ok"
            estado, cuerpo = _http("POST", base + "/api/auditoria/completa",
                                   {"ip": "192.168.100.10", "usuario": "", "password": "", "driver_name": "ios"})
            r = json.loads(cuerpo)
            assert r["estado_general"] == "rojo" and r["seguridad"]["score"] == 60, "la auditoria no dio el resultado esperado"
            assert r["respaldo"]["version"] == 1 and r["auditoria_id"] == 1, "no se guardo en el historial"
            estado, cuerpo = _http("GET", base + "/api/historial")
            assert len(json.loads(cuerpo)) == 1
            assert (rutas.directorio_datos() / "axis.db").is_file(), "no se creo axis.db"
            return f"Frontend, API, auditoria completa y SQLite OK (puerto {puerto})"
        finally:
            servidor.should_exit = True
            hilo.join(timeout=10)

    comprobar("Frontend empaquetado", frontend)
    comprobar("Mocks JSON empaquetados", mocks)
    comprobar("Drivers de red (NAPALM / Netmiko)", drivers)
    comprobar("Servidor + auditoria + historial", servidor_y_api)

    shutil.rmtree(temporal, ignore_errors=True)
    print("\nAUTOTEST " + ("OK" if not fallos else "CON FALLOS: " + ", ".join(fallos)))
    return 0 if not fallos else 1


# ----------------------------------------------------------------------------- arranque
def main() -> int:
    multiprocessing.freeze_support()  # requerido en Windows para ejecutables empaquetados

    if "--autotest" in sys.argv:
        return autotest()

    for flujo in (sys.stdout, sys.stderr):  # mensajes visibles al instante aunque la salida se redirija a un archivo
        try:
            flujo.reconfigure(line_buffering=True)
        except Exception:
            pass
    log = preparar_salida()
    sin_consola = log is not None
    url = url_base()

    if instancia_activa():
        print(f"A.X.I.S. ya esta en ejecucion en {url}. Abriendo el navegador.")
        if not os.environ.get("AXIS_NO_NAVEGADOR"):
            webbrowser.open(url)
        return 0
    if puerto_ocupado():
        avisar(f"El puerto {PUERTO} esta ocupado por otro programa.\n"
               f"Cierrelo o defina otro puerto con la variable AXIS_PORT.", sin_consola)
        return 1

    import uvicorn
    import main as axis_api  # la API (FastAPI) y el Frontend montado en "/"

    minutos = float(os.environ.get("AXIS_AUTOCIERRE_MIN", 10 if sin_consola else 0))
    seguimiento = SeguimientoActividad(axis_api.app)
    # Se fijan loop/http/ws explicitamente: evita que uvicorn los busque de forma dinamica dentro del .exe.
    servidor = uvicorn.Server(uvicorn.Config(seguimiento, host=HOST, port=PUERTO, log_level="info", access_log=False,
                                             loop="asyncio", http="h11", ws="none", lifespan="on"))
    terminado = threading.Event()
    referencia = instalar_cierre_de_consola_windows(servidor, terminado) if sys.platform == "win32" and not sin_consola else None

    print("=" * 62)
    print(f" A.X.I.S. en {url}")
    print(" Para detenerlo: cierre esta ventana o presione Ctrl+C.")
    if minutos > 0:
        print(f" Se cerrara solo tras {minutos:g} min sin actividad.")
    print("=" * 62)

    if minutos > 0:
        threading.Thread(target=vigilar_inactividad, args=(servidor, seguimiento, minutos), daemon=True).start()
    if not os.environ.get("AXIS_NO_NAVEGADOR"):
        threading.Thread(target=abrir_navegador_cuando_este_listo, args=(servidor, url), daemon=True).start()

    def salir_con_exito(*_):
        raise SystemExit(0)

    # Uvicorn apaga todo en orden y luego RE-ENVIA la senal recibida al propio proceso. Con el manejador por
    # defecto, SIGTERM mataria el proceso (codigo 143) antes del mensaje final; asi sale con codigo 0.
    signal.signal(signal.SIGTERM, salir_con_exito)
    try:
        servidor.run()
    except KeyboardInterrupt:  # Ctrl+C: uvicorn ya apago ordenadamente
        pass
    finally:
        terminado.set()
        del referencia
        print("A.X.I.S. detenido.")
        if log:
            log.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
