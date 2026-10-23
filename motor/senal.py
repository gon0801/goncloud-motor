"""Señal de vida a Kuma: mientras el daemon avance, un hilo aparte hace un GET cada minuto a la
dirección push de `MOTOR_KUMA_URL`. Si el daemon deja de avanzar (una vuelta colgada), la señal se
apaga y Kuma avisa.

La dirección es un secreto: nunca sale de aquí ni en el log, ni en un error, ni en `doctor`.
"""

from __future__ import annotations

import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from .conf import load_secretos

VARIABLE = "MOTOR_KUMA_URL"
CADA_S = 60.0
SILENCIO_MIN_S = 900.0
TOPE_GET_S = 5.0
LOG_CADA_S = 3600.0


class _SinRedireccion(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def url_configurada(home: Path) -> str:
    """La dirección en `secretos.env`, leída en este momento; "" si no hay. Puede lanzar ConfError."""
    return load_secretos(home).get(VARIABLE, "")


def silencio_s(tiempos: dict[str, float]) -> float:
    piso = float(tiempos.get("senal_silencio_min_s", SILENCIO_MIN_S))
    return max(piso, 2 * float(tiempos.get("repro_tope_s", 300)))


class Senal:
    def __init__(self, home: Path, tiempos, reloj=time.monotonic):
        self.home = home
        self._tiempos = tiempos
        self._reloj = reloj
        self._ultimo_avance = reloj()
        self._planes = 0
        self._ultimo_log = float("-inf")
        self._alto = threading.Event()
        self._hilo: threading.Thread | None = None

    def avanzo(self, planes: int | None = None) -> None:
        self._ultimo_avance = self._reloj()
        if planes is not None:
            self._planes = planes

    def arranca(self) -> None:
        if self._hilo is not None and self._hilo.is_alive():
            return
        self._alto.clear()
        self._ultimo_avance = self._reloj()
        self._hilo = threading.Thread(
            target=self._bucle, name="senal-kuma", daemon=True
        )
        self._hilo.start()

    def para(self) -> None:
        self._alto.set()

    def _bucle(self) -> None:
        while True:
            self.una_vez()
            if self._alto.wait(float(self._tiempos().get("senal_cada_s", CADA_S))):
                return

    def una_vez(self) -> bool:
        """Un intento de señal. Nunca lanza; devuelve si se mandó."""
        try:
            tiempos = self._tiempos()
            if self._reloj() - self._ultimo_avance >= silencio_s(tiempos):
                return False
            url = url_configurada(self.home)
            if not url:
                return False
            self._manda(url)
            return True
        except Exception as e:  # noqa: BLE001  la señal nunca detiene al motor ni filtra la dirección
            self._anota(type(e).__name__)
            return False

    def _manda(self, url: str) -> None:
        sep = "&" if "?" in url else "?"
        msg = urllib.parse.quote(f"{self._planes} planes")
        abre = urllib.request.build_opener(_SinRedireccion).open
        with abre(f"{url}{sep}status=up&msg={msg}", timeout=TOPE_GET_S) as r:
            r.read(1024)

    def _anota(self, tipo: str) -> None:
        ahora = self._reloj()
        if ahora - self._ultimo_log < LOG_CADA_S:
            return
        self._ultimo_log = ahora
        print(f"motor: la señal a Kuma falló ({tipo})", file=sys.stderr, flush=True)
