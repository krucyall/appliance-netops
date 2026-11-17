"""Pruebas offline de las reglas avanzadas de A.X.I.S. (puertos, running-config, MTU)."""
import json
import threading
from contextlib import contextmanager
from ipaddress import IPv4Address
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import mtu_stress
from extraccion_config import ErrorExtraccionConfig, obtener_running_config, redactar_secretos
from mtu_stress import ErrorPruebaMTU, construir_comando, ejecutar_prueba_mtu, interpretar_salida
from reglas_puertos import evaluar_puertos_mareados

AQUI = Path(__file__).parent


# ------------------------------------------------------------------ puertos mareados
def _iface(enabled, up, flap=-1.0):
    return {"is_enabled": enabled, "is_up": up, "last_flapped": flap}


def test_puertos_con_mock_completo():
    datos = json.loads((AQUI / "mock_interfaces.json").read_text())
    r = evaluar_puertos_mareados(datos)
    # Hay un inconsistente (Gi1/0) -> rojo por encima de las advertencias
    assert r["estado"] == "rojo"
    txt = " | ".join(r["detalle"])
    assert "GigabitEthernet0/1: habilitado" in txt          # mareado
    assert "GigabitEthernet1/0: apagado por configuracion" in txt  # inconsistente
    assert "GigabitEthernet0/3" in txt and "inestable" in txt      # flapping
    assert "Loopback0" not in txt and "Vlan1" not in txt           # virtuales ignoradas
    assert "GigabitEthernet0/2" not in txt                         # shutdown a proposito: OK


def test_puertos_sanos_dan_verde():
    r = evaluar_puertos_mareados({"interfaces": {"Gi0/0": _iface(True, True, 9999), "Gi0/1": _iface(False, False)}})
    assert r["estado"] == "verde" and r["detalle"] == []


def test_solo_mareado_es_advertencia():
    r = evaluar_puertos_mareados({"interfaces": {"Gi0/0": _iface(True, False)}})
    assert r["estado"] == "amarillo"


def test_flap_desconocido_no_se_acusa():
    # last_flapped = -1 significa "el driver no lo informa": no es evidencia de flapping
    r = evaluar_puertos_mareados({"interfaces": {"Gi0/0": _iface(True, True, -1.0)}})
    assert r["estado"] == "verde"


@pytest.mark.parametrize("datos", [{}, {"interfaces": {}}, {"interfaces": None}, {"interfaces": {"Loopback0": _iface(True, True)}}])
def test_puertos_sin_datos_evaluables_son_rojo(datos):
    assert evaluar_puertos_mareados(datos)["estado"] == "rojo"


# ------------------------------------------------------------------ running-config
CONFIG_IOS = """Building configuration...

Current configuration : 1234 bytes
!
hostname R1
enable secret 5 $1$abcd$HASHHASHHASH
username admin privilege 15 secret 9 $9$xyz
snmp-server community PUBLICA RO
interface GigabitEthernet0/0
 ip ospf message-digest-key 1 md5 7 0822455D0A16
line vty 0 4
 password 7 060506324F41
"""


class DispositivoFalso:
    def __init__(self, config=CONFIG_IOS, acepta_sanitized=True, falla=None):
        self.config, self.acepta_sanitized, self.falla = config, acepta_sanitized, falla
        self.llamadas = []

    def get_config(self, retrieve="all", full=False, sanitized=False):
        if not self.acepta_sanitized and sanitized:
            raise TypeError("unexpected keyword 'sanitized'")
        if self.falla:
            raise self.falla
        self.llamadas.append((retrieve, sanitized))
        return {"running": self.config, "startup": "", "candidate": ""}


def test_running_config_redacta_todos_los_secretos():
    r = obtener_running_config(DispositivoFalso())
    for secreto in ("$1$abcd$HASHHASHHASH", "$9$xyz", "PUBLICA", "0822455D0A16", "060506324F41"):
        assert secreto not in r["running"], secreto
    assert r["running"].count("<removed>") == 5
    assert r["sanitizada"] is True and r["lineas"] > 5
    assert "hostname R1" in r["running"]  # lo no secreto se conserva


def test_running_config_hash_ignora_cabeceras_variables():
    a = obtener_running_config(DispositivoFalso(CONFIG_IOS))
    b = obtener_running_config(DispositivoFalso(CONFIG_IOS.replace("1234 bytes", "9999 bytes")))
    c = obtener_running_config(DispositivoFalso(CONFIG_IOS.replace("hostname R1", "hostname R2")))
    assert a["sha256"] == b["sha256"]      # mismo contenido real -> misma huella
    assert a["sha256"] != c["sha256"]      # cambio real -> huella distinta


def test_running_config_fallback_si_driver_no_acepta_sanitized():
    r = obtener_running_config(DispositivoFalso(acepta_sanitized=False))
    assert "PUBLICA" not in r["running"]  # la redaccion propia cubre el hueco


def test_running_config_vacia_o_con_error_levanta_error_de_usuario():
    with pytest.raises(ErrorExtraccionConfig) as e1:
        obtener_running_config(DispositivoFalso(config="   "))
    assert e1.value.mensaje_usuario.startswith("Error:")
    with pytest.raises(ErrorExtraccionConfig) as e2:
        obtener_running_config(DispositivoFalso(falla=RuntimeError("socket cerrado")))
    assert "socket cerrado" in e2.value.detalle_tecnico


def test_redactar_vyos():
    t = "set system login user vyos authentication encrypted-password '$6$abc'\n"
    assert "$6$abc" not in redactar_secretos(t)


# ------------------------------------------------------------------ MTU stress
DEST = IPv4Address("10.0.0.2")


def test_comandos_por_plataforma():
    assert construir_comando("ios", DEST, 1500, 3) == "ping 10.0.0.2 df-bit repeat 3 size 1500 timeout 2"
    # VyOS/Linux: size es payload -> 1500 - 28 = 1472
    assert construir_comando("vyos", DEST, 1500, 3) == "ping 10.0.0.2 count 3 size 1472 do-not-fragment"


def test_comando_rechaza_texto_libre_y_plataforma_desconocida():
    with pytest.raises(ErrorPruebaMTU):
        construir_comando("ios", "10.0.0.2; reload", 1500, 3)  # no es IPv4Address
    with pytest.raises(ErrorPruebaMTU):
        construir_comando("junos", DEST, 1500, 3)


def test_interpretar_salidas():
    assert interpretar_salida("ios", "!!!!!\nSuccess rate is 100 percent (5/5), round-trip", 5) == {"enviados": 5, "recibidos": 5}
    assert interpretar_salida("ios", "M.M.M\nSuccess rate is 0 percent (0/3)", 3) == {"enviados": 3, "recibidos": 0}
    assert interpretar_salida("vyos", "3 packets transmitted, 3 received, 0% packet loss", 3) == {"enviados": 3, "recibidos": 3}
    assert interpretar_salida("vyos", "ping: local error: message too long, mtu=1400", 3) == {"enviados": 3, "recibidos": 0}
    with pytest.raises(ErrorPruebaMTU):
        interpretar_salida("ios", "% Invalid input detected", 3)


class RouterFalso:
    """Simula un camino con 'mtu_camino': con DF, tamanos mayores no pasan."""
    def __init__(self, mtu_camino=1500, perdida_parcial_en=None):
        self.mtu_camino, self.parcial, self.comandos = mtu_camino, perdida_parcial_en, []

    def cli(self, comandos):
        salida = {}
        for c in comandos:
            self.comandos.append(c)
            tam = int(c.split("size ")[1].split()[0])
            if self.parcial == tam:
                salida[c] = "!.!\nSuccess rate is 66 percent (2/3)"
            elif tam <= self.mtu_camino:
                salida[c] = "!!!\nSuccess rate is 100 percent (3/3)"
            else:
                salida[c] = "M.M\nSuccess rate is 0 percent (0/3)"
        return salida


def test_mtu_ok_es_verde():
    r = ejecutar_prueba_mtu(RouterFalso(1500), "ios", DEST, [1400], mtu_esperado=1500)
    assert r["estado"] == "verde" and r["mtu_maximo_exitoso"] == 1500
    assert [s["tamano_ip"] for s in r["sondas"]] == [1400, 1500]


def test_mtu_camino_menor_es_rojo_y_estima_maximo():
    r = ejecutar_prueba_mtu(RouterFalso(1400), "ios", DEST, [1280, 1400], mtu_esperado=1500)
    assert r["estado"] == "rojo" and r["mtu_maximo_exitoso"] == 1400
    assert "NO soporta MTU 1500" in r["mensaje"]


def test_mtu_perdida_parcial_es_amarillo():
    r = ejecutar_prueba_mtu(RouterFalso(1500, perdida_parcial_en=1400), "ios", DEST, [1400], mtu_esperado=1500)
    assert r["estado"] == "amarillo"


def test_mtu_siempre_prueba_el_mtu_esperado():
    r = ejecutar_prueba_mtu(RouterFalso(9000), "ios", DEST, [1400], mtu_esperado=9000)
    assert 9000 in [s["tamano_ip"] for s in r["sondas"]]


# ------------------------------------------------------------------ endpoint
def _app(router_falso):
    @contextmanager
    def sesion_falsa(_solicitud):
        yield router_falso

    mtu_stress._abrir_sesion = sesion_falsa
    app = FastAPI()
    app.include_router(mtu_stress.router)
    return TestClient(app)


BODY = {"ip_equipo": "192.168.100.1", "usuario": "admin", "password": "cisco",
        "driver": "ios", "destino": "10.0.0.2", "mtu_esperado": 1500}


def test_endpoint_flujo_feliz_no_filtra_password():
    client = _app(RouterFalso(1500))
    r = client.post("/api/mtu-stress", json=BODY)
    assert r.status_code == 200 and r.json()["estado"] == "verde"
    assert "cisco" not in r.text


@pytest.mark.parametrize("cambio", [
    {"destino": "8.8.8.8"},                    # publica: no se permite trafico a Internet
    {"destino": "224.0.0.5"},                  # multicast
    {"destino": "10.0.0.2; reload"},           # intento de inyeccion de comandos
    {"tamanos": [100]},                        # bajo el minimo
    {"tamanos": [1400] * 7},                   # demasiados tamanos
    {"cantidad": 500},                         # demasiadas repeticiones
    {"driver": "junos"},                       # plataforma fuera de la lista cerrada
])
def test_endpoint_rechaza_entradas_peligrosas(cambio):
    client = _app(RouterFalso(1500))
    r = client.post("/api/mtu-stress", json={**BODY, **cambio})
    assert r.status_code == 422


def test_endpoint_rechaza_prueba_simultanea():
    client = _app(RouterFalso(1500))
    mtu_stress._cerrojo_prueba.acquire()
    try:
        assert client.post("/api/mtu-stress", json=BODY).status_code == 409
    finally:
        mtu_stress._cerrojo_prueba.release()


def test_endpoint_traduce_errores_a_mensaje_de_usuario():
    class Roto:
        def cli(self, _):
            raise RuntimeError("boom")
    client = _app(Roto())
    r = client.post("/api/mtu-stress", json=BODY)
    assert r.status_code == 502 and r.json()["detail"].startswith("Error:")


def test_redactar_keychain_sin_tocar_el_id_de_clave():
    t = "key chain RUTAS\n key 1\n  key-string 7 0822455D0A16\n"
    r = redactar_secretos(t)
    assert "0822455D0A16" not in r
    assert " key 1" in r  # el identificador de clave no es secreto
