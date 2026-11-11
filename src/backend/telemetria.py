"""
telemetria.py
Monitoreo continuo de latencia (ping ICMP desde el appliance) hacia los equipos auditados.

Es una tarea de fondo de asyncio, sin dependencias externas ni acceso a los equipos por SSH:
solo mide alcanzabilidad y latencia. Pensada para la Raspberry Pi 4:
  - Memoria acotada: buffer circular por equipo (240 muestras) y maximo 16 objetivos.
  - Cero escrituras a disco (la telemetria vive en RAM; no desgasta la SD).
  - Un ciclo que falla nunca mata el bucle: el error se guarda y se reintenta.
  - El ping se ejecuta en un hilo (asyncio.to_thread) con 'subprocess' SIN shell y con IPs
    validadas como IPv4Address: funciona igual en Linux (Pi/Ubuntu) y Windows (desarrollo)
    y no permite inyeccion de comandos.
  - Intervalo minimo de 5 s (AXIS_TELEMETRIA_INTERVALO, por defecto 15 s).

Estado por equipo:  rojo = sin respuesta en las ultimas muestras;
                    amarillo = perdida reciente o latencia promedio >= 100 ms;
                    verde = respondiendo normal.
"""

import asyncio
import contextlib
import os
import re
import shutil
import subprocess
import sys
import threading
from collections import deque
from datetime import datetime, timezone
from ipaddress import IPv4Address
from typing import Any, Callable, Deque, Dict, List, Optional

INTERVALO_MINIMO_SEG = 5
INTERVALO_DEFECTO_SEG = 15
MAX_PUNTOS = 240
MAX_OBJETIVOS = 16
UMBRAL_LATENCIA_AMARILLA_MS = 100.0
VENTANA_PERDIDA = 20

# Cubre Linux ("time=0.045 ms"), Windows ("time=1ms", "time<1ms") y Windows en espanol ("tiempo=1ms").
_RE_TIEMPO = re.compile(r"(?:time|tiempo)\s*([=<])\s*([\d.,]+)\s*ms", re.IGNORECASE)


class SondaNoDisponible(Exception):
    """El comando 'ping' no existe en este sistema (p. ej. falta iputils-ping)."""


def parsear_latencia_ms(salida: str) -> Optional[float]:
    """Extrae la latencia en ms de la salida de ping; None si no hubo respuesta."""
    m = _RE_TIEMPO.search(salida or "")
    if not m:
        return None
    try:
        return float(m.group(2).replace(",", "."))
    except ValueError:
        return None


def _comando_ping(ip: str, timeout: int) -> List[str]:
    if sys.platform.startswith("win"):
        return ["ping", "-n", "1", "-w", str(timeout * 1000), ip]
    return ["ping", "-c", "1", "-W", str(timeout), ip]


def ping_icmp(ip: str, timeout: int = 2) -> Optional[float]:
    """Un ping ICMP. Retorna latencia en ms o None (sin respuesta). Bloqueante: usar en un hilo."""
    if shutil.which("ping") is None:
        raise SondaNoDisponible("El comando 'ping' no esta instalado en el sistema.")
    try:
        r = subprocess.run(
            _comando_ping(str(IPv4Address(ip)), timeout),
            capture_output=True, text=True, timeout=timeout + 2, shell=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return parsear_latencia_ms(r.stdout) if r.returncode == 0 else None


def _estado(muestras: List[Dict[str, Any]]) -> str:
    if not muestras:
        return "desconocido"
    ultimas = muestras[-3:]
    sin_respuesta = all(m["ms"] is None for m in ultimas)
    if sin_respuesta and len(ultimas) >= 2:
        return "rojo"
    ventana = muestras[-VENTANA_PERDIDA:]
    con_respuesta = [m["ms"] for m in ventana if m["ms"] is not None]
    perdida = len(ventana) - len(con_respuesta)
    promedio = sum(con_respuesta) / len(con_respuesta) if con_respuesta else 0.0
    if sin_respuesta or perdida > 0 or promedio >= UMBRAL_LATENCIA_AMARILLA_MS:
        return "amarillo"
    return "verde"


class MonitorTelemetria:
    def __init__(self, intervalo: Optional[float] = None,
                 sonda: Callable[[str], Optional[float]] = ping_icmp,
                 concurrencia: int = 4):
        if intervalo is None:
            try:
                intervalo = float(os.environ.get("AXIS_TELEMETRIA_INTERVALO", INTERVALO_DEFECTO_SEG))
            except ValueError:
                intervalo = INTERVALO_DEFECTO_SEG
        self.intervalo = max(float(intervalo), 0.01 if sonda is not ping_icmp else INTERVALO_MINIMO_SEG)
        self._sonda = sonda
        self._concurrencia = concurrencia
        self._objetivos: Dict[str, Deque[Dict[str, Any]]] = {}
        self._lock = threading.Lock()
        self._tarea: Optional["asyncio.Task[None]"] = None
        self.ultimo_error: Optional[str] = None

    # ------------------------------------------------------------- objetivos
    def agregar_objetivo(self, ip: str) -> bool:
        """Registra un equipo a monitorear. True si es nuevo. ValueError si la IP es invalida o hay tope."""
        ip = str(IPv4Address(ip))
        with self._lock:
            if ip in self._objetivos:
                return False
            if len(self._objetivos) >= MAX_OBJETIVOS:
                raise ValueError(f"Se alcanzo el maximo de {MAX_OBJETIVOS} equipos monitoreados.")
            self._objetivos[ip] = deque(maxlen=MAX_PUNTOS)
            return True

    def quitar_objetivo(self, ip: str) -> bool:
        with self._lock:
            return self._objetivos.pop(str(IPv4Address(ip)), None) is not None

    # ------------------------------------------------------------- ciclo
    async def ejecutar_ciclo(self) -> None:
        """Mide todos los objetivos una vez (concurrencia acotada)."""
        with self._lock:
            ips = list(self._objetivos)
        semaforo = asyncio.Semaphore(self._concurrencia)

        async def medir(ip: str) -> None:
            async with semaforo:
                try:
                    ms = await asyncio.to_thread(self._sonda, ip)
                    self.ultimo_error = None
                except SondaNoDisponible as error:
                    self.ultimo_error, ms = str(error), None
                except Exception as error:  # una sonda defectuosa cuenta como "sin respuesta", no mata el ciclo
                    self.ultimo_error, ms = f"Error al medir {ip}: {error}", None
                muestra = {"t": datetime.now(timezone.utc).isoformat(timespec="seconds"), "ms": ms}
                with self._lock:
                    if ip in self._objetivos:  # pudo ser quitado mientras se medía
                        self._objetivos[ip].append(muestra)

        await asyncio.gather(*(medir(ip) for ip in ips), return_exceptions=True)

    async def _bucle(self) -> None:
        while True:
            try:
                await self.ejecutar_ciclo()
            except asyncio.CancelledError:
                raise
            except Exception as error:  # el bucle nunca debe morir
                self.ultimo_error = f"Error en ciclo de telemetria: {error}"
            await asyncio.sleep(self.intervalo)

    def iniciar(self) -> None:
        if self._tarea is None or self._tarea.done():
            self._tarea = asyncio.get_running_loop().create_task(self._bucle())

    async def detener(self) -> None:
        if self._tarea is not None:
            self._tarea.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._tarea
            self._tarea = None

    @property
    def activo(self) -> bool:
        return self._tarea is not None and not self._tarea.done()

    # ------------------------------------------------------------- lectura
    def resumen(self, ip: str, puntos: int = 60) -> Optional[Dict[str, Any]]:
        with self._lock:
            cola = self._objetivos.get(str(IPv4Address(ip)))
            muestras = list(cola) if cola is not None else None
        if muestras is None:
            return None
        con_respuesta = [m["ms"] for m in muestras if m["ms"] is not None]
        ventana = muestras[-VENTANA_PERDIDA:]
        perdida = (100 * sum(1 for m in ventana if m["ms"] is None) / len(ventana)) if ventana else 0.0
        ultimo = muestras[-1]["ms"] if muestras else None
        return {
            "ip": str(IPv4Address(ip)),
            "estado": _estado(muestras),
            "ultimo_ms": ultimo,
            "promedio_ms": round(sum(con_respuesta) / len(con_respuesta), 1) if con_respuesta else None,
            "min_ms": round(min(con_respuesta), 1) if con_respuesta else None,
            "max_ms": round(max(con_respuesta), 1) if con_respuesta else None,
            "perdida_pct": round(perdida, 1),
            "muestras": len(muestras),
            "serie": muestras[-puntos:],
        }

    def resumen_todos(self) -> Dict[str, Any]:
        with self._lock:
            ips = list(self._objetivos)
        return {
            "activo": self.activo,
            "intervalo_seg": self.intervalo,
            "error": self.ultimo_error,
            "objetivos": [r for r in (self.resumen(ip) for ip in ips) if r is not None],
        }


monitor = MonitorTelemetria()  # instancia unica de la app (la inicia el lifespan de main.py)
