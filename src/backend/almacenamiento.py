"""
almacenamiento.py
Almacenamiento local versionado de A.X.I.S. (SQLite, solo libreria estandar).

Guarda dos cosas, siempre ligadas por IP:
  - configs:    running-config SANITIZADA, con version por equipo, sha256 y timestamp (UTC).
  - auditorias: resultado completo de cada ciclo de auditoria, vinculado a la config
                (config_id + sha256) que existia en ese momento.

Decisiones de diseno pensadas para la Raspberry Pi 4 (SD, cortes de energia):
  - SQLite en modo WAL + synchronous=NORMAL: tolera cortes de energia sin corromper
    y reduce escrituras sobre la SD.
  - Versionado por contenido: si el sha256 es igual al de la ultima version del equipo
    NO se guarda otra copia (la auditoria apunta a la misma version). Una config
    estable no crece en disco.
  - Retencion acotada: ultimas 30 versiones de config y 100 auditorias por equipo.
  - Defensa en profundidad: aunque llegue una config sin sanitizar, se redacta de nuevo
    antes de escribirla. Jamas se persisten secretos.
  - Ubicacion: AXIS_DATA_DIR (por defecto ./data junto al backend). Agrega esa carpeta a
    .gitignore: contiene configuraciones de equipos reales.
"""

import json
import os
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional

from extraccion_config import redactar_secretos

MAX_CONFIGS_POR_IP = 30
MAX_AUDITORIAS_POR_IP = 100

_ESCRITURA = threading.Lock()  # serializa "calcular version + insertar" (evita versiones duplicadas)

_ESQUEMA = """
CREATE TABLE IF NOT EXISTS configs (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    ip             TEXT    NOT NULL,
    hostname       TEXT    NOT NULL,
    version        INTEGER NOT NULL,
    sha256         TEXT    NOT NULL,
    timestamp      TEXT    NOT NULL,
    lineas         INTEGER NOT NULL,
    running_config TEXT    NOT NULL,
    UNIQUE (ip, version)
);
CREATE TABLE IF NOT EXISTS auditorias (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    ip              TEXT    NOT NULL,
    hostname        TEXT    NOT NULL,
    timestamp       TEXT    NOT NULL,
    estado_general  TEXT    NOT NULL,
    score_seguridad INTEGER,
    config_id       INTEGER REFERENCES configs(id) ON DELETE SET NULL,
    config_sha256   TEXT,
    resultado_json  TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_aud_ip ON auditorias (ip, id DESC);
CREATE INDEX IF NOT EXISTS ix_cfg_ip ON configs (ip, version DESC);
"""


class ErrorAlmacenamiento(Exception):
    """Falla de disco/DB. 'mensaje_usuario' es apto para la pantalla tactil."""

    def __init__(self, mensaje_usuario: str, detalle_tecnico: str = ""):
        self.mensaje_usuario = mensaje_usuario
        self.detalle_tecnico = detalle_tecnico
        super().__init__(mensaje_usuario)


def _ruta_db() -> Path:
    base = Path(os.environ.get("AXIS_DATA_DIR") or (Path(__file__).parent / "data"))
    base.mkdir(parents=True, exist_ok=True)
    return base / "axis.db"


@contextmanager
def _conexion() -> Iterator[sqlite3.Connection]:
    try:
        con = sqlite3.connect(_ruta_db(), timeout=5)
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA journal_mode=WAL")
        con.execute("PRAGMA synchronous=NORMAL")
        con.execute("PRAGMA foreign_keys=ON")
        con.executescript(_ESQUEMA)
    except (sqlite3.Error, OSError) as error:
        raise ErrorAlmacenamiento(
            "Error: No se pudo abrir el almacenamiento local. Verifique el espacio en disco y los permisos.",
            detalle_tecnico=str(error),
        )
    try:
        yield con
        con.commit()
    except sqlite3.Error as error:
        con.rollback()
        raise ErrorAlmacenamiento(
            "Error: Fallo al leer o escribir el almacenamiento local.", detalle_tecnico=str(error)
        )
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()


def _ahora() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# --------------------------------------------------------------------------- escritura
def guardar_config(ip: str, hostname: str, running: str, sha256: str, lineas: int = 0,
                   ahora: Optional[str] = None) -> Dict[str, Any]:
    """
    Guarda la running-config (sanitizada) como nueva version del equipo, salvo que el
    sha256 sea igual al de la ultima version (entonces reutiliza esa).
    Retorna {config_id, version, sha256, nuevo}.
    """
    running = redactar_secretos(running)
    with _ESCRITURA, _conexion() as con:
        ultima = con.execute(
            "SELECT id, version, sha256 FROM configs WHERE ip = ? ORDER BY version DESC LIMIT 1", (ip,)
        ).fetchone()
        if ultima and ultima["sha256"] == sha256:
            return {"config_id": ultima["id"], "version": ultima["version"], "sha256": sha256, "nuevo": False}

        version = (ultima["version"] + 1) if ultima else 1
        cursor = con.execute(
            "INSERT INTO configs (ip, hostname, version, sha256, timestamp, lineas, running_config) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (ip, hostname, version, sha256, ahora or _ahora(), lineas, running),
        )
        con.execute("DELETE FROM configs WHERE ip = ? AND version <= ?", (ip, version - MAX_CONFIGS_POR_IP))
        return {"config_id": cursor.lastrowid, "version": version, "sha256": sha256, "nuevo": True}


def guardar_auditoria(ip: str, hostname: str, resultado: Dict[str, Any], config_id: Optional[int] = None,
                      config_sha256: Optional[str] = None, ahora: Optional[str] = None) -> Dict[str, Any]:
    """Guarda el resultado de una auditoria vinculado a su config. Retorna {auditoria_id, timestamp}."""
    marca = ahora or _ahora()
    score = (resultado.get("seguridad") or {}).get("score")
    with _ESCRITURA, _conexion() as con:
        cursor = con.execute(
            "INSERT INTO auditorias (ip, hostname, timestamp, estado_general, score_seguridad, "
            "config_id, config_sha256, resultado_json) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (ip, hostname, marca, resultado.get("estado_general", "desconocido"), score, config_id,
             config_sha256, json.dumps(resultado, ensure_ascii=False)),
        )
        con.execute(
            "DELETE FROM auditorias WHERE ip = ? AND id NOT IN "
            "(SELECT id FROM auditorias WHERE ip = ? ORDER BY id DESC LIMIT ?)",
            (ip, ip, MAX_AUDITORIAS_POR_IP),
        )
        return {"auditoria_id": cursor.lastrowid, "timestamp": marca}


# --------------------------------------------------------------------------- lectura
def listar_auditorias(ip: Optional[str] = None, limite: int = 50) -> List[Dict[str, Any]]:
    """Resumenes de auditorias, mas recientes primero (sin el JSON completo)."""
    limite = max(1, min(int(limite), 200))
    consulta = (
        "SELECT a.id, a.ip, a.hostname, a.timestamp, a.estado_general, a.score_seguridad, "
        "a.config_sha256, c.version AS config_version FROM auditorias a "
        "LEFT JOIN configs c ON c.id = a.config_id "
    )
    parametros: List[Any] = []
    if ip:
        consulta += "WHERE a.ip = ? "
        parametros.append(ip)
    consulta += "ORDER BY a.id DESC LIMIT ?"
    parametros.append(limite)
    with _conexion() as con:
        return [dict(f) for f in con.execute(consulta, parametros).fetchall()]


def obtener_auditoria(auditoria_id: int) -> Optional[Dict[str, Any]]:
    """Auditoria completa (metadatos + resultado) o None si no existe."""
    with _conexion() as con:
        f = con.execute(
            "SELECT a.*, c.version AS config_version FROM auditorias a "
            "LEFT JOIN configs c ON c.id = a.config_id WHERE a.id = ?", (auditoria_id,)
        ).fetchone()
    if f is None:
        return None
    return {
        "auditoria_id": f["id"], "ip": f["ip"], "hostname": f["hostname"], "timestamp": f["timestamp"],
        "estado_general": f["estado_general"], "score_seguridad": f["score_seguridad"],
        "config_id": f["config_id"], "config_version": f["config_version"],
        "config_sha256": f["config_sha256"], "resultado": json.loads(f["resultado_json"]),
    }


def listar_versiones(ip: str) -> List[Dict[str, Any]]:
    """Versiones guardadas de la running-config de un equipo, mas reciente primero."""
    with _conexion() as con:
        filas = con.execute(
            "SELECT id, ip, hostname, version, sha256, timestamp, lineas FROM configs "
            "WHERE ip = ? ORDER BY version DESC", (ip,)
        ).fetchall()
    return [dict(f) for f in filas]


def obtener_config(config_id: int) -> Optional[Dict[str, Any]]:
    with _conexion() as con:
        f = con.execute("SELECT * FROM configs WHERE id = ?", (config_id,)).fetchone()
    return dict(f) if f else None


def diff_versiones(config_id_base: int, config_id_nueva: int) -> Optional[Dict[str, Any]]:
    """
    Diff entre dos versiones guardadas (base -> nueva), con el mismo formato que
    comparar_configs() para reutilizar el Diff Viewer. 'agregada' = solo en la nueva.
    """
    from evaluador import comparar_configs  # import diferido: evita dependencia circular

    base, nueva = obtener_config(config_id_base), obtener_config(config_id_nueva)
    if base is None or nueva is None:
        return None
    resultado = comparar_configs(nueva["running_config"], base["running_config"])
    n = resultado["cantidad_diferencias"]
    resultado["mensaje"] = (
        f"Hay {n} linea(s) que difieren entre la version {base['version']} y la version {nueva['version']}."
        if n else f"Las versiones {base['version']} y {nueva['version']} son identicas."
    )
    resultado["version_base"], resultado["version_nueva"] = base["version"], nueva["version"]
    return resultado
