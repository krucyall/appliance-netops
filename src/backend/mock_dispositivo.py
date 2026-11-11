"""
mock_dispositivo.py
"Super Mock" de un equipo completo para desarrollar SIN laboratorio (bus, sin GNS3).

DispositivoMock imita el subconjunto de la API de NAPALM que usa A.X.I.S. (get_facts,
get_interfaces, get_config, cli) leyendo mock_equipo.json. Los datos pasan por la logica REAL
de extractores.py, evaluador.py, almacenamiento.py, etc.: lo unico falso es el equipo.

Se activa con la variable de entorno  AXIS_MODO=mock  (cualquier otro valor = equipo real).
El mock trae secretos a proposito (enable secret, SNMP, claves OSPF) para comprobar que la
sanitizacion los elimina antes de guardar o devolver nada.
"""

import copy
import json
import os
import re
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional

RUTA_MOCK_EQUIPO = Path(__file__).parent / "mock_equipo.json"


def modo_mock() -> bool:
    return os.environ.get("AXIS_MODO", "real").strip().lower() == "mock"


class DispositivoMock:
    def __init__(self, datos: Optional[Dict[str, Any]] = None):
        if datos is None:
            with open(RUTA_MOCK_EQUIPO, "r", encoding="utf-8") as archivo:
                datos = json.load(archivo)
        self._d = datos

    def get_facts(self) -> Dict[str, Any]:
        return copy.deepcopy(self._d["facts"])

    def get_interfaces(self) -> Dict[str, Any]:
        return copy.deepcopy(self._d["interfaces"])

    def get_config(self, retrieve: str = "all", full: bool = False, sanitized: bool = False) -> Dict[str, str]:
        cfg = self._d["config"]
        quiere = lambda cual: retrieve in ("all", cual)
        return {
            "running": cfg["running"] if quiere("running") else "",
            "startup": cfg["startup"] if quiere("startup") else "",
            "candidate": "",
        }

    def cli(self, comandos: List[str]) -> Dict[str, str]:
        return {c: self._responder(c) for c in comandos}

    def _responder(self, comando: str) -> str:
        if comando.startswith("ping "):
            # Simula un camino con 'mtu_camino': con DF, tamanos mayores no pasan.
            tam = re.search(r"size (\d+)", comando)
            n = int(re.search(r"repeat (\d+)", comando).group(1)) if "repeat" in comando else 3
            pasa = tam is not None and int(tam.group(1)) <= self._d.get("mtu_camino", 1500)
            return f"Success rate is {100 if pasa else 0} percent ({n if pasa else 0}/{n})"
        return self._d.get("cli", {}).get(comando, "% Invalid input detected at '^' marker.")

    def open(self) -> None: ...
    def close(self) -> None: ...


@contextmanager
def sesion_mock(*_args: Any, **_kwargs: Any) -> Iterator[DispositivoMock]:
    """Mismo contrato que conexion.conectar(): entrega el 'dispositivo' y lo cierra al salir."""
    dispositivo = DispositivoMock()
    try:
        yield dispositivo
    finally:
        dispositivo.close()
