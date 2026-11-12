"""
Pruebas offline de la integracion A.X.I.S. (extractores, evaluador, almacenamiento,
ciclo de auditoria en UNA sesion, telemetria y API). No toca test_reglas_avanzadas.py.

Todo corre contra DispositivoMock (mock_equipo.json) y SQLite en una carpeta temporal:
sin GNS3, sin red y sin tocar tus datos reales.
"""
import asyncio
import json
import sys
import threading
import types
from contextlib import contextmanager

import pytest
from fastapi.testclient import TestClient

import almacenamiento
import api_auditoria
import auditoria
import main
import mtu_stress
import telemetria
from evaluador import calcular_score_seguridad, evaluar_dispositivo
from extraccion_config import ErrorExtraccionConfig, obtener_configs
from extractores import _canonica, extraer_interfaces_l2, parsear_vecinos_ospf_ios
from mock_dispositivo import DispositivoMock, sesion_mock
from telemetria import MonitorTelemetria, parsear_latencia_ms

# Se captura al importar (test_reglas_avanzadas reemplaza este atributo mas tarde, durante sus pruebas).
_ABRIR_SESION_ORIGINAL = mtu_stress._abrir_sesion

MOCK = DispositivoMock()
RUNNING = MOCK.get_config()["running"]
IP = "192.168.100.10"


@pytest.fixture(autouse=True)
def entorno(tmp_path, monkeypatch):
    monkeypatch.setenv("AXIS_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("AXIS_MODO", "mock")
    monkeypatch.setattr(telemetria.monitor, "_objetivos", {})
    monkeypatch.setattr(telemetria.monitor, "_sonda", lambda ip: 12.3)
    monkeypatch.setattr(api_auditoria, "_ABRIR_SESION", None)


@pytest.fixture
def client():
    return TestClient(main.app)


# =============================================================== extractores
def _por_nombre(res):
    return {i["interfaz"]: i for i in res["interfaces_analizadas"]}


def test_l2_extrae_solo_puertos_de_acceso_activos():
    ifs = _por_nombre(extraer_interfaces_l2(RUNNING))
    # Omitidos: troncal (Gi0/0), shutdown (Gi1/0), Loopback0, Vlan10 (SVI)
    assert set(ifs) == {"GigabitEthernet0/1", "GigabitEthernet0/2", "GigabitEthernet0/3", "GigabitEthernet1/1"}
    assert ifs["GigabitEthernet0/1"] == {"interfaz": "GigabitEthernet0/1", "vlan_acceso": 10, "bpdu_guard": False, "portfast": False}
    assert ifs["GigabitEthernet0/2"]["vlan_acceso"] == 1          # sin 'access vlan' => VLAN 1 por defecto
    assert ifs["GigabitEthernet0/2"]["portfast"] and not ifs["GigabitEthernet0/2"]["bpdu_guard"]
    assert ifs["GigabitEthernet0/3"]["portfast"] and ifs["GigabitEthernet0/3"]["bpdu_guard"]
    assert ifs["GigabitEthernet1/1"]["vlan_acceso"] == 1          # puerto sin config en un switch


def test_l2_portfast_y_bpduguard_globales_y_excepciones_por_interfaz():
    base = ("spanning-tree mode rapid-pvst\nspanning-tree portfast default\n"
            "spanning-tree portfast bpduguard default\n")
    cfg = base + ("interface GigabitEthernet0/1\n switchport mode access\n!\n"
                  "interface GigabitEthernet0/2\n switchport mode access\n spanning-tree portfast disable\n!\n"
                  "interface GigabitEthernet0/3\n switchport mode access\n spanning-tree bpduguard disable\n!\nend\n")
    ifs = _por_nombre(extraer_interfaces_l2(cfg))
    assert ifs["GigabitEthernet0/1"]["portfast"] and ifs["GigabitEthernet0/1"]["bpdu_guard"]
    assert not ifs["GigabitEthernet0/2"]["portfast"] and not ifs["GigabitEthernet0/2"]["bpdu_guard"]
    assert ifs["GigabitEthernet0/3"]["portfast"] and not ifs["GigabitEthernet0/3"]["bpdu_guard"]


def test_l2_un_router_no_genera_puertos_de_acceso():
    router = "hostname R1\ninterface GigabitEthernet0/0\n ip address 10.0.0.1 255.255.255.0\n!\nrouter ospf 1\n!\nend\n"
    assert extraer_consulta(router) == []


def extraer_consulta(cfg):
    return extraer_interfaces_l2(cfg)["interfaces_analizadas"]


def test_ospf_parser_con_nombres_largos_y_cortos():
    neigh, brief = MOCK.cli(["show ip ospf neighbor", "show ip ospf interface brief"]).values()
    v = parsear_vecinos_ospf_ios(neigh, brief)["vecinos_ospf"]
    assert [x["estado"] for x in v] == ["FULL", "EXSTART"]          # 'FULL/DR' y 'EXSTART/BDR' sin el rol
    assert [x["area"] for x in v] == ["0", "0"]                      # 'Vlan10' (neighbor) cruza con 'Vl10' (brief)
    assert [x["tiempo_muerto_seg"] for x in v] == [38, 4]


def test_ospf_parser_casos_borde():
    assert parsear_vecinos_ospf_ios("") == {"vecinos_ospf": []}
    assert parsear_vecinos_ospf_ios("% OSPF: Router process not found") == {"vecinos_ospf": []}
    linea = "10.9.9.9          1   FULL/  -        00:00:31    10.9.9.9        Serial0/0"
    v = parsear_vecinos_ospf_ios(linea)["vecinos_ospf"][0]          # punto a punto, sin tabla de areas
    assert v["estado"] == "FULL" and v["area"] == "?"
    assert _canonica("Gi0/1") == _canonica("GigabitEthernet0/1") == "gigabitethernet0/1"


# =============================================================== evaluador
def test_evaluar_dispositivo_suma_puertos_mareados_y_semaforo_amarillo():
    datos = {**MOCK.get_facts(), "interfaces": MOCK.get_interfaces()}
    r = evaluar_dispositivo(datos)
    assert [c["check"] for c in r["checks"]] == ["firmware", "uptime", "puertos_mareados"]
    assert r["checks"][2]["estado"] == "amarillo" and r["estado_general"] == "amarillo"


def test_evaluar_dispositivo_sin_interfaces_conserva_el_comportamiento_original():
    r = evaluar_dispositivo({"hostname": "R1", "os_version": "12.4(24)T", "uptime": 300})
    assert [c["check"] for c in r["checks"]] == ["firmware", "uptime"]
    assert r["estado_general"] == "rojo"


def test_score_l2_con_datos_reales_extraidos():
    r = calcular_score_seguridad({"hostname": "SW", **extraer_interfaces_l2(RUNNING)})
    tipos = sorted((h["tipo"], h["severidad"]) for h in r["hallazgos"])
    assert tipos == [("sin_bpdu_guard", "media"), ("vlan1_en_acceso", "alta"), ("vlan1_en_acceso", "alta")]
    assert r["score"] == 100 - 15 - 15 - 10 and r["estado_color"] == "rojo"


# =============================================================== extraccion_config.obtener_configs
class _Espia(DispositivoMock):
    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        self.llamadas_get_config = []

    def get_config(self, retrieve="all", full=False, sanitized=False):
        self.llamadas_get_config.append(retrieve)
        return super().get_config(retrieve, full, sanitized)


def test_obtener_configs_una_llamada_y_sin_secretos():
    d = _Espia()
    r = obtener_configs(d)
    assert d.llamadas_get_config == ["all"]                          # running + startup en UN viaje
    for secreto in ("PUBLICA", "0822455D0A16", "060506324F41", "hx5rVt7rPNoS4wqbXKX7m0", "abcdefghijk"):
        assert secreto not in r["running"] and secreto not in r["startup"]
    assert len(r["sha256"]) == 64 and r["lineas"] > 20


def test_obtener_configs_startup_vacia_es_valida_pero_running_vacia_no():
    datos = json.loads(json.dumps(MOCK._d))
    datos["config"]["startup"] = ""
    assert obtener_configs(DispositivoMock(datos))["startup"] == ""
    datos["config"]["running"] = "  "
    with pytest.raises(ErrorExtraccionConfig):
        obtener_configs(DispositivoMock(datos))


# =============================================================== almacenamiento
def test_versionado_por_contenido_y_por_ip():
    a = almacenamiento.guardar_config("10.0.0.1", "R1", "hostname R1\n", "h1")
    b = almacenamiento.guardar_config("10.0.0.1", "R1", "hostname R1\n", "h1")   # mismo sha256
    c = almacenamiento.guardar_config("10.0.0.1", "R1", "hostname R1\nntp server 1.1.1.1\n", "h2")
    d = almacenamiento.guardar_config("10.0.0.2", "R2", "hostname R2\n", "h1")   # otro equipo
    assert (a["version"], a["nuevo"]) == (1, True)
    assert (b["version"], b["nuevo"], b["config_id"]) == (1, False, a["config_id"])
    assert (c["version"], c["nuevo"]) == (2, True)
    assert d["version"] == 1
    assert [v["version"] for v in almacenamiento.listar_versiones("10.0.0.1")] == [2, 1]


def test_nunca_persiste_secretos_aunque_llegue_sin_sanitizar():
    almacenamiento.guardar_config("10.0.0.1", "R1", "enable secret 5 $1$SECRETO$XYZ\nsnmp-server community PRIVADA RW\n", "h")
    guardada = almacenamiento.obtener_config(almacenamiento.listar_versiones("10.0.0.1")[0]["id"])
    assert "SECRETO" not in guardada["running_config"] and "PRIVADA" not in guardada["running_config"]


def test_auditoria_vinculada_a_su_config_y_filtros():
    cfg = almacenamiento.guardar_config("10.0.0.1", "R1", "hostname R1\n", "h1", lineas=1)
    a1 = almacenamiento.guardar_auditoria("10.0.0.1", "R1", {"estado_general": "rojo", "seguridad": {"score": 60}}, cfg["config_id"], "h1")
    almacenamiento.guardar_auditoria("10.0.0.2", "R2", {"estado_general": "verde"})
    reg = almacenamiento.obtener_auditoria(a1["auditoria_id"])
    assert (reg["ip"], reg["config_version"], reg["config_sha256"], reg["score_seguridad"]) == ("10.0.0.1", 1, "h1", 60)
    assert reg["resultado"]["estado_general"] == "rojo" and reg["timestamp"].endswith("+00:00")
    assert len(almacenamiento.listar_auditorias()) == 2
    assert [x["ip"] for x in almacenamiento.listar_auditorias("10.0.0.2")] == ["10.0.0.2"]
    assert almacenamiento.obtener_auditoria(9999) is None


def test_retencion_acotada_y_vinculos_huerfanos_quedan_en_null(monkeypatch):
    monkeypatch.setattr(almacenamiento, "MAX_CONFIGS_POR_IP", 3)
    monkeypatch.setattr(almacenamiento, "MAX_AUDITORIAS_POR_IP", 2)
    primera = almacenamiento.guardar_config("10.0.0.1", "R1", "v1\n", "h1")
    aud = almacenamiento.guardar_auditoria("10.0.0.1", "R1", {"estado_general": "verde"}, primera["config_id"], "h1")
    for n in range(2, 6):
        almacenamiento.guardar_config("10.0.0.1", "R1", f"v{n}\n", f"h{n}")
    assert [v["version"] for v in almacenamiento.listar_versiones("10.0.0.1")] == [5, 4, 3]
    assert almacenamiento.obtener_auditoria(aud["auditoria_id"])["config_id"] is None   # su version fue purgada
    for _ in range(4):
        almacenamiento.guardar_auditoria("10.0.0.1", "R1", {"estado_general": "verde"})
    assert len(almacenamiento.listar_auditorias("10.0.0.1")) == 2


def test_diff_entre_versiones_reutiliza_el_formato_del_diff_viewer():
    a = almacenamiento.guardar_config("10.0.0.1", "R1", "hostname R1\nline vty 0 4\n", "h1")
    b = almacenamiento.guardar_config("10.0.0.1", "R1", "hostname R1\nntp server 1.1.1.1\nline vty 0 4\n", "h2")
    d = almacenamiento.diff_versiones(a["config_id"], b["config_id"])
    assert d["cantidad_diferencias"] == 1 and {"tipo": "agregada", "linea": "ntp server 1.1.1.1"} in d["diff"]
    assert "version 1" in d["mensaje"] and "version 2" in d["mensaje"]
    assert almacenamiento.diff_versiones(a["config_id"], 9999) is None


def test_fallo_de_disco_se_traduce_a_mensaje_de_usuario(tmp_path, monkeypatch):
    archivo = tmp_path / "no_es_carpeta"
    archivo.write_text("x")
    monkeypatch.setenv("AXIS_DATA_DIR", str(archivo))                # una ruta que no puede ser directorio
    with pytest.raises(almacenamiento.ErrorAlmacenamiento) as e:
        almacenamiento.listar_auditorias()
    assert e.value.mensaje_usuario.startswith("Error:")


# =============================================================== ciclo de auditoria (UNA sesion)
class _SesionContada:
    """Fabrica de sesiones que cuenta aperturas/cierres y llamadas, para probar 'una sola sesion SSH'."""

    def __init__(self):
        self.aperturas = self.cierres = 0
        self.dispositivo = _Espia()

    def __call__(self, ip, usuario, password, driver):
        @contextmanager
        def cm():
            self.aperturas += 1
            try:
                yield self.dispositivo
            finally:
                self.cierres += 1
        return cm()


def test_ciclo_completo_evalua_todo_en_una_sola_sesion():
    sesion = _SesionContada()
    r = auditoria.ejecutar_ciclo_auditoria(IP, "admin", "x", abrir_sesion=sesion)
    assert (sesion.aperturas, sesion.cierres) == (1, 1)
    assert sesion.dispositivo.llamadas_get_config == ["all"]
    estados = {c["check"]: c["estado"] for c in r["checks"]}
    assert estados == {"firmware": "verde", "uptime": "verde", "puertos_mareados": "amarillo",
                       "seguridad_l2": "rojo", "ospf": "rojo", "config_sin_guardar": "amarillo"}
    assert r["estado_general"] == "rojo" and r["hostname"] == "SW-CORE-LAB"
    assert r["seguridad"]["score"] == 60 and len(r["ospf"]["vecinos"]) == 2
    assert r["configs"]["hay_cambios_sin_guardar"] is True
    ospf = next(c for c in r["checks"] if c["check"] == "ospf")
    assert len(ospf["detalle"]) == 1 and "EXSTART" in ospf["detalle"][0]


def test_ciclo_guarda_versiona_vincula_y_registra_telemetria():
    r1 = auditoria.ejecutar_ciclo_auditoria(IP, "admin", "x", abrir_sesion=_SesionContada())
    r2 = auditoria.ejecutar_ciclo_auditoria(IP, "admin", "x", abrir_sesion=_SesionContada())
    assert (r1["respaldo"]["version"], r1["respaldo"]["nuevo"]) == (1, True)
    assert (r2["respaldo"]["version"], r2["respaldo"]["nuevo"]) == (1, False)    # config sin cambios: misma version
    assert r2["auditoria_id"] == r1["auditoria_id"] + 1
    guardada = almacenamiento.obtener_auditoria(r1["auditoria_id"])
    assert guardada["config_sha256"] == r1["respaldo"]["sha256"] and guardada["ip"] == IP
    assert "diff" not in guardada["resultado"]["configs"]                        # el diff no se duplica en el historial
    assert telemetria.monitor.resumen(IP) is not None


def test_ningun_secreto_sale_en_el_resultado_ni_en_disco():
    r = auditoria.ejecutar_ciclo_auditoria(IP, "admin", "x", abrir_sesion=_SesionContada())
    guardada = almacenamiento.obtener_config(r["respaldo"]["config_id"])
    todo = json.dumps(r) + guardada["running_config"]
    for secreto in ("PUBLICA", "0822455D0A16", "060506324F41", "hx5rVt7rPNoS4wqbXKX7m0", "abcdefghijk"):
        assert secreto not in todo


def test_si_el_almacenamiento_falla_la_auditoria_igual_se_entrega(monkeypatch):
    def roto(*a, **k):
        raise almacenamiento.ErrorAlmacenamiento("Error: disco lleno.")
    monkeypatch.setattr(almacenamiento, "guardar_config", roto)
    r = auditoria.ejecutar_ciclo_auditoria(IP, "admin", "x", abrir_sesion=_SesionContada())
    assert r["auditoria_id"] is None and r["respaldo"] == {"error": "Error: disco lleno."}
    assert any(c["check"] == "almacenamiento" and c["estado"] == "amarillo" for c in r["checks"])


def test_equipo_sin_ospf_y_driver_no_ios_marcan_no_aplica():
    datos = json.loads(json.dumps(MOCK._d))
    datos["config"]["running"] = datos["config"]["running"].replace("router ospf 1\n network 10.0.0.0 0.0.0.255 area 0\n!\n", "")

    @contextmanager
    def sesion(*_):
        yield DispositivoMock(datos)

    r = auditoria.ejecutar_ciclo_auditoria(IP, "a", "b", abrir_sesion=sesion, guardar=False)
    assert next(c for c in r["checks"] if c["check"] == "ospf")["mensaje"].startswith("No aplica")
    assert r["ospf"] is None and r["respaldo"] == {}

    r2 = auditoria.ejecutar_ciclo_auditoria(IP, "a", "b", driver_name="vyos", abrir_sesion=sesion, guardar=False)
    assert next(c for c in r2["checks"] if c["check"] == "seguridad_l2")["mensaje"].startswith("No aplica")


def test_modo_mock_usa_el_equipo_simulado_por_defecto():
    r = auditoria.ejecutar_ciclo_auditoria(IP, "", "", guardar=False)    # sin abrir_sesion: AXIS_MODO=mock
    assert r["hostname"] == "SW-CORE-LAB"


# =============================================================== API
BODY = {"ip": IP, "usuario": "admin", "password": "ClaveSecreta1", "driver_name": "ios"}


def test_endpoint_auditoria_completa_y_contrato_compatible_con_el_dashboard(client):
    r = client.post("/api/auditoria/completa", json=BODY)
    assert r.status_code == 200
    j = r.json()
    assert {"hostname", "estado_general", "checks"} <= set(j)             # mismo contrato que /api/auditar
    assert j["estado_general"] == "rojo" and j["auditoria_id"] == 1 and j["respaldo"]["version"] == 1
    assert "ClaveSecreta1" not in r.text
    assert any(l["tipo"] == "agregada" for l in j["configs"]["diff"])


@pytest.mark.parametrize("cambio", [{"ip": "10.0.0.1; reload"}, {"ip": "no-es-ip"}, {"driver_name": "junos"}])
def test_endpoint_auditoria_rechaza_entradas_invalidas(client, cambio):
    assert client.post("/api/auditoria/completa", json={**BODY, **cambio}).status_code == 422


def test_endpoint_auditoria_rechaza_ciclos_simultaneos(client):
    api_auditoria._cerrojo_ciclo.acquire()
    try:
        assert client.post("/api/auditoria/completa", json=BODY).status_code == 409
    finally:
        api_auditoria._cerrojo_ciclo.release()


def test_endpoint_auditoria_traduce_error_de_conexion(client, monkeypatch):
    class ErrorConexionFalso(Exception):
        mensaje_usuario = "Error: Usuario o contrasena incorrectos. Verifique las credenciales."

    @contextmanager
    def sesion(*_):
        raise ErrorConexionFalso()
        yield

    monkeypatch.setattr(api_auditoria, "_ABRIR_SESION", sesion)
    r = client.post("/api/auditoria/completa", json=BODY)
    assert r.status_code == 502 and r.json()["detail"].startswith("Error: Usuario")


def test_historial_versiones_y_diff_por_api(client):
    client.post("/api/auditoria/completa", json=BODY)
    client.post("/api/auditoria/completa", json={**BODY, "ip": "192.168.100.11"})
    todos = client.get("/api/historial").json()
    assert [h["ip"] for h in todos] == ["192.168.100.11", IP]               # mas reciente primero
    assert [h["ip"] for h in client.get("/api/historial", params={"ip": IP}).json()] == [IP]
    assert client.get("/api/historial/1").json()["resultado"]["hostname"] == "SW-CORE-LAB"
    assert client.get("/api/historial/999").status_code == 404
    assert client.get("/api/historial", params={"limite": 0}).status_code == 422
    versiones = client.get("/api/configs/versiones", params={"ip": IP}).json()
    assert [v["version"] for v in versiones] == [1]
    assert client.get("/api/configs/diff", params={"base": 1, "nueva": 999}).status_code == 404
    assert client.get("/api/configs/diff", params={"base": 1, "nueva": 1}).json()["cantidad_diferencias"] == 0


def test_endpoints_existentes_siguen_con_su_contrato(client):
    assert client.get("/api/estado").json() == {"estado": "ok", "mensaje": "Motor M.E.O.W. operativo.", "modo": "mock"}
    inventario = client.get("/api/dispositivos").json()
    assert inventario and {"id", "nombre", "ip", "driver"} <= set(inventario[0])
    cred = {"ip": "192.168.1.10", "usuario": "u", "password": "p", "driver_name": "ios"}
    r = client.post("/api/auditar", json=cred)
    assert r.status_code == 200 and [c["check"] for c in r.json()["checks"]] == ["firmware", "uptime"]
    assert r.json()["modo"] == "mock"
    assert client.post("/api/auditar", json={"ip": "192.168.1.10"}).status_code == 200  # mock: credenciales opcionales
    assert client.post("/api/ospf", json=cred).status_code == 200
    assert client.post("/api/seguridad", json=cred).json()["score"] == 60
    assert client.post("/api/configs/comparar", json=cred).json()["hay_cambios_sin_guardar"] is True


def test_mtu_stress_en_modo_mock_pasa_por_la_ruta_real(client, monkeypatch):
    monkeypatch.setattr(mtu_stress, "_abrir_sesion", _ABRIR_SESION_ORIGINAL)
    cuerpo = {"ip_equipo": IP, "usuario": "a", "password": "b", "driver": "ios", "destino": "10.0.0.2", "mtu_esperado": 1500}
    assert client.post("/api/mtu-stress", json=cuerpo).json()["estado"] == "verde"
    assert client.post("/api/mtu-stress", json={**cuerpo, "mtu_esperado": 9000}).json()["estado"] == "rojo"


def test_mtu_abrir_sesion_usa_conectar_de_conexion(monkeypatch):
    llamadas = []
    falso = types.ModuleType("conexion")
    falso.conectar = lambda *a: llamadas.append(a) or "SESION"
    monkeypatch.setitem(sys.modules, "conexion", falso)
    monkeypatch.setenv("AXIS_MODO", "real")
    solicitud = mtu_stress.SolicitudMTU(ip_equipo="192.168.100.1", usuario="admin", password="pw",
                                        driver="ios", destino="10.0.0.2")
    assert _ABRIR_SESION_ORIGINAL(solicitud) == "SESION"
    assert llamadas == [("192.168.100.1", "admin", "pw", "ios")]


# =============================================================== telemetria
@pytest.mark.parametrize("salida,esperado", [
    ("64 bytes from 10.0.0.2: icmp_seq=1 ttl=64 time=0.045 ms", 0.045),
    ("Reply from 10.0.0.2: bytes=32 time=1ms TTL=64", 1.0),
    ("Reply from 10.0.0.2: bytes=32 time<1ms TTL=64", 1.0),
    ("Respuesta desde 10.0.0.2: bytes=32 tiempo=2ms TTL=64", 2.0),
    ("64 bytes from 10.0.0.2: icmp_seq=1 ttl=64 time=0,5 ms", 0.5),
    ("Request timed out.", None),
    ("Reply from 10.0.0.1: Destination host unreachable.", None),
    ("", None),
])
def test_parser_de_latencia(salida, esperado):
    assert parsear_latencia_ms(salida) == esperado


def _monitor(muestras, **kw):
    cola = iter(muestras)
    m = MonitorTelemetria(intervalo=0.01, sonda=lambda ip: next(cola), **kw)
    m.agregar_objetivo("10.0.0.2")
    return m


def _correr(m, ciclos):
    async def ir():
        for _ in range(ciclos):
            await m.ejecutar_ciclo()
    asyncio.run(ir())
    return m.resumen("10.0.0.2")


def test_estados_de_telemetria():
    assert _correr(_monitor([10.0, 12.0, 11.0]), 3)["estado"] == "verde"
    assert _correr(_monitor([None, None, None]), 3)["estado"] == "rojo"
    r = _correr(_monitor([10.0, None, 12.0]), 3)
    assert r["estado"] == "amarillo" and r["perdida_pct"] == pytest.approx(33.3, abs=0.1)
    assert _correr(_monitor([150.0, 160.0]), 2)["estado"] == "amarillo"        # latencia alta
    r = _correr(_monitor([10.0, 20.0]), 2)
    assert (r["min_ms"], r["max_ms"], r["promedio_ms"], r["ultimo_ms"]) == (10.0, 20.0, 15.0, 20.0)


def test_limites_de_memoria_y_validacion(monkeypatch):
    monkeypatch.setattr(telemetria, "MAX_PUNTOS", 5)
    monkeypatch.setattr(telemetria, "MAX_OBJETIVOS", 2)
    m = MonitorTelemetria(intervalo=0.01, sonda=lambda ip: 1.0)
    assert m.agregar_objetivo("10.0.0.2") is True and m.agregar_objetivo("10.0.0.2") is False
    m.agregar_objetivo("10.0.0.3")
    with pytest.raises(ValueError):
        m.agregar_objetivo("10.0.0.4")                                          # tope de objetivos
    with pytest.raises(ValueError):
        m.agregar_objetivo("10.0.0.2; rm -rf /")                                # no es una IPv4
    assert _correr(m, 12)["muestras"] == 5                                      # buffer circular acotado
    assert m.quitar_objetivo("10.0.0.3") is True and m.quitar_objetivo("10.0.0.3") is False


def test_una_sonda_defectuosa_no_mata_el_ciclo():
    def mala(ip):
        raise RuntimeError("boom")
    m = MonitorTelemetria(intervalo=0.01, sonda=mala)
    m.agregar_objetivo("10.0.0.2")
    r = _correr(m, 2)
    assert r["muestras"] == 2 and r["estado"] == "rojo" and "boom" in m.ultimo_error


def test_tarea_en_background_inicia_mide_y_se_detiene():
    m = MonitorTelemetria(intervalo=0.01, sonda=lambda ip: 5.0)
    m.agregar_objetivo("10.0.0.2")

    async def ir():
        m.iniciar()
        await asyncio.sleep(0.15)
        activo_durante = m.activo
        await m.detener()
        return activo_durante

    assert asyncio.run(ir()) is True and m.activo is False
    assert m.resumen("10.0.0.2")["muestras"] >= 3


def test_api_de_telemetria(client, monkeypatch):
    assert client.post("/api/telemetria/objetivos", json={"ip": "10.0.0.2"}).status_code == 201
    for ip in ("8.8.8.8", "127.0.0.1", "224.0.0.1", "10.0.0.2; reboot"):
        assert client.post("/api/telemetria/objetivos", json={"ip": ip}).status_code == 422
    telemetria.monitor._objetivos["10.0.0.2"].append({"t": "2026-01-01T00:00:00+00:00", "ms": 7.0})
    j = client.get("/api/telemetria").json()
    assert [o["ip"] for o in j["objetivos"]] == ["10.0.0.2"] and j["objetivos"][0]["ultimo_ms"] == 7.0
    assert client.get("/api/telemetria/10.0.0.2").json()["estado"] == "verde"
    assert client.get("/api/telemetria/10.0.0.9").status_code == 404
    monkeypatch.setattr(telemetria, "MAX_OBJETIVOS", 1)
    assert client.post("/api/telemetria/objetivos", json={"ip": "10.0.0.3"}).status_code == 409
    assert client.delete("/api/telemetria/objetivos/10.0.0.2").status_code == 204
    assert client.delete("/api/telemetria/objetivos/10.0.0.2").status_code == 404


def test_lifespan_arranca_y_detiene_la_telemetria():
    with TestClient(main.app):
        assert telemetria.monitor.activo is True
    assert telemetria.monitor.activo is False
