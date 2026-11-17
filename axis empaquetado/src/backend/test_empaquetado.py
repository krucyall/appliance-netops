"""Pruebas del empaquetado: rutas (desarrollo vs .exe), Frontend servido por el backend y utilidades del lanzador."""
import io
import json
import sys
import threading
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import launcher
import main
import rutas


@pytest.fixture(autouse=True)
def limpio(tmp_path, monkeypatch):
    monkeypatch.setenv("AXIS_DATA_DIR", str(tmp_path / "datos"))
    monkeypatch.setenv("AXIS_MODO", "mock")


# ----------------------------------------------------------------------------- rutas
def test_en_desarrollo_los_recursos_estan_junto_al_codigo():
    assert not rutas.empaquetado()
    assert rutas.recurso("mock_facts.json").is_file()
    assert (rutas.ruta_frontend() / "index.html").is_file()


def test_empaquetado_usa_la_carpeta_temporal_de_pyinstaller(tmp_path, monkeypatch):
    bundle = tmp_path / "_MEI123"
    (bundle / "Frontend").mkdir(parents=True)
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(bundle), raising=False)
    assert rutas.empaquetado()
    assert rutas.recurso("mock_facts.json") == bundle / "mock_facts.json"
    assert rutas.ruta_frontend() == bundle / "Frontend"


def test_los_datos_nunca_van_a_la_carpeta_temporal_del_bundle(tmp_path, monkeypatch):
    """La DB en sys._MEIPASS se borraria al cerrar el programa: debe ir junto al .exe."""
    bundle, exe = tmp_path / "_MEI123", tmp_path / "carpeta_exe"
    bundle.mkdir(); exe.mkdir()
    monkeypatch.delenv("AXIS_DATA_DIR")
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(bundle), raising=False)
    monkeypatch.setattr(sys, "executable", str(exe / "AXIS.exe"))
    destino = rutas.directorio_datos()
    assert destino == exe / "axis_data" and not str(destino).startswith(str(bundle))
    assert destino.is_dir()


def test_si_no_se_puede_escribir_junto_al_exe_usa_los_datos_de_usuario(tmp_path, monkeypatch):
    monkeypatch.delenv("AXIS_DATA_DIR")
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(tmp_path / "AXIS.exe"))
    monkeypatch.setattr(rutas, "_escribible", lambda carpeta: False)
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))
    assert rutas.directorio_datos() == tmp_path / "local" / "AXIS"


def test_la_base_de_datos_usa_el_directorio_de_datos(tmp_path):
    import almacenamiento
    almacenamiento.guardar_config("10.0.0.1", "R1", "hostname R1\n", "h")
    assert (tmp_path / "datos" / "axis.db").is_file()


# ----------------------------------------------------------------------------- frontend servido por el backend
def test_el_backend_sirve_el_frontend_y_la_api_sigue_funcionando():
    c = TestClient(main.app)
    raiz = c.get("/")
    assert raiz.status_code == 200 and "A.X.I.S." in raiz.text
    assert c.get("/config.js").status_code == 200 and "API_BASE" in c.get("/config.js").text
    assert c.get("/style.css").headers["content-type"].startswith("text/css")
    assert c.get("/api/estado").json() == {"estado": "ok", "mensaje": "Motor A.X.I.S. operativo."}
    assert c.get("/api/no-existe").status_code == 404           # un typo en /api no devuelve el HTML del dashboard


def test_el_montaje_del_frontend_es_la_ultima_ruta():
    """Un mount en '/' tapa todo lo que se registre despues: debe ser SIEMPRE lo ultimo."""
    ultima = main.app.routes[-1]
    assert getattr(ultima, "name", None) == "frontend"


# ----------------------------------------------------------------------------- lanzador
def test_deteccion_de_instancia_y_puerto():
    libre = launcher._puerto_libre()
    assert launcher.instancia_activa(libre) is False and launcher.puerto_ocupado(libre) is False
    import http.server, socketserver
    servidor = socketserver.TCPServer(("127.0.0.1", libre), http.server.SimpleHTTPRequestHandler)
    threading.Thread(target=servidor.serve_forever, daemon=True).start()
    try:
        assert launcher.puerto_ocupado(libre) is True
        assert launcher.instancia_activa(libre) is False        # lo ocupa otro programa, no A.X.I.S.
    finally:
        servidor.shutdown(); servidor.server_close()


def test_sin_consola_la_salida_se_redirige_a_un_log(tmp_path, monkeypatch):
    """Un .exe --noconsole arranca con stdout/stderr = None y uvicorn se cae si no se redirigen."""
    monkeypatch.setattr(sys, "stdout", None)
    monkeypatch.setattr(sys, "stderr", None)
    log = launcher.preparar_salida()
    try:
        print("hola desde un exe sin consola")
        assert sys.stdout is log and sys.stderr is log
    finally:
        log.close()
    assert "hola desde un exe sin consola" in (tmp_path / "datos" / "axis.log").read_text(encoding="utf-8")


def test_con_consola_no_se_toca_la_salida(monkeypatch):
    monkeypatch.setattr(sys, "stdout", io.StringIO())
    monkeypatch.setattr(sys, "stderr", io.StringIO())
    assert launcher.preparar_salida() is None


def test_seguimiento_de_actividad_y_autocierre():
    class Servidor:
        should_exit = False

    llamadas = []

    async def app(scope, receive, send):
        llamadas.append(scope["type"])

    seg = launcher.SeguimientoActividad(app)
    antes = seg.ultima
    time.sleep(0.02)
    import asyncio
    asyncio.run(seg({"type": "lifespan"}, None, None))
    assert seg.ultima == antes                                   # lifespan no cuenta como actividad
    asyncio.run(seg({"type": "http"}, None, None))
    assert seg.ultima > antes and llamadas == ["lifespan", "http"]

    servidor = Servidor()
    seg.ultima = time.monotonic() - 10                           # 10 s sin peticiones
    hilo = threading.Thread(target=launcher.vigilar_inactividad, args=(servidor, seg, 0.05), daemon=True)  # limite 3 s
    hilo.start(); hilo.join(timeout=6)
    assert servidor.should_exit is True


def test_url_base_ignora_0_0_0_0(monkeypatch):
    assert launcher._host_navegador("0.0.0.0") == "127.0.0.1" and launcher._host_navegador("10.0.0.5") == "10.0.0.5"


def test_autotest_completo_en_desarrollo(capsys):
    assert launcher.autotest() == 0
    salida = capsys.readouterr().out
    assert "AUTOTEST OK" in salida and "[FALLO]" not in salida
