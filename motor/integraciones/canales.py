"""Estado de los canales de avisos: cuánto llevan fallando y con qué error.

Vive en `base/canales/<canal>.json`, escrito por `drenar` y leído por `motor director ver` y por la señal a
Kuma. Leerlo no toca el canal: un canal caído no puede esconder que está caído.
"""

from __future__ import annotations
from motor.io import escribir_texto, leer_json

import json
import re
import time
from datetime import datetime, timezone
from pathlib import Path

UMBRAL_CAIDO_S = 600
TOPE_ERROR = 200

_SECRETOS = (
    (re.compile(r"https?://\S+"), "[oculto]"),
    (re.compile(r"(?i)\b(bearer|basic)\s+[A-Za-z0-9._~+/=-]{8,}"), r"\1 [oculto]"),
    (
        re.compile(
            r"(?i)\b(token|api[_-]?key|apikey|secret|password|passwd|clave|authorization|cookie)\b(\s*[=:]\s*)\S+"
        ),
        r"\1\2[oculto]",
    ),
    (re.compile(r"\b\w{6,}:[A-Za-z0-9_-]{20,}\b"), "[oculto]"),
    (re.compile(r"[A-Za-z0-9_\-+/=]{32,}"), "[oculto]"),
)


def redactar(texto: str, tope: int = TOPE_ERROR) -> str:
    """Un error de transporte sin direcciones, tokens, claves ni cadenas opacas largas, en una línea y acotado."""
    t = str(texto or "")
    for patron, cambio in _SECRETOS:
        t = patron.sub(cambio, t)
    t = re.sub(r"\s+", " ", t).strip()
    return t if len(t) <= tope else t[: tope - 1].rstrip() + "…"


def _entero(valor) -> int:
    try:
        return int(valor)
    except (TypeError, ValueError):
        return 0


def _iso(epoch: float) -> str:
    return datetime.fromtimestamp(epoch, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _epoch(hora: str) -> float | None:
    try:
        return datetime.fromisoformat(str(hora).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def _ruta(base: Path, canal: str) -> Path:
    return Path(base) / "canales" / f"{canal}.json"


def _leer(path: Path) -> dict:
    try:
        doc = leer_json(path)
    except (OSError, ValueError):
        return {}
    return doc if isinstance(doc, dict) else {}


def _guardar(path: Path, doc: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporal = path.with_suffix(".tmp")
    escribir_texto(temporal, json.dumps(doc, ensure_ascii=False, indent=2) + "\n")
    temporal.replace(path)


def registrar_pasada(base: Path, ahora: float, fallaron: dict[str, str], respondieron: set[str]) -> None:
    """Anota el resultado de una pasada de avisos: canales cuyo transporte falló (con su error) y canales que respondieron."""
    for canal, error in fallaron.items():
        path = _ruta(base, canal)
        doc = _leer(path) or {"canal": canal}
        doc.setdefault("fallando_desde", _iso(ahora))
        doc["fallos"] = _entero(doc.get("fallos")) + 1
        doc["ultimo_intento"] = _iso(ahora)
        doc["ultimo_error"] = redactar(error)
        _guardar(path, doc)
    for canal in respondieron - set(fallaron):
        path = _ruta(base, canal)
        if "fallando_desde" in _leer(path):
            _guardar(path, {"canal": canal, "ultimo_exito": _iso(ahora)})


def canales_en_fallo(base: Path) -> list[str]:
    """Los canales con un fallo registrado, sin esperar el umbral de la señal."""
    nombres: list[str] = []
    try:
        rutas = sorted((Path(base) / "canales").glob("*.json"))
    except OSError:
        return nombres
    for path in rutas:
        doc = _leer(path)
        if doc.get("fallando_desde"):
            nombres.append(str(doc.get("canal") or path.stem))
    return nombres


def sondeo_toca(base: Path, canal: str, ahora: float) -> bool:
    """Si el canal caído ya admite un sondeo: la primera vez y, después, tras su espera creciente."""
    proximo = _epoch(_leer(_ruta(base, canal)).get("proximo_sondeo") or "")
    return proximo is None or ahora >= proximo


def sondeos(base: Path, canal: str) -> int:
    return _entero(_leer(_ruta(base, canal)).get("sondeos"))


def anotar_sondeo(base: Path, canal: str, ahora: float, intervalo_s: int, error: str = "") -> None:
    """Anota un sondeo que no respondió y cuándo toca el siguiente."""
    path = _ruta(base, canal)
    doc = _leer(path) or {"canal": canal}
    doc["canal"] = doc.get("canal") or canal
    doc["sondeos"] = _entero(doc.get("sondeos")) + 1
    doc["ultimo_sondeo"] = _iso(ahora)
    doc["ultimo_intento"] = _iso(ahora)
    doc["proximo_sondeo"] = _iso(ahora + intervalo_s)
    if error:
        doc["ultimo_error"] = redactar(error)
    _guardar(path, doc)


def canales_caidos(base: Path, ahora: float | None = None, umbral_s: float = UMBRAL_CAIDO_S) -> list[dict]:
    """Los canales que llevan fallando al menos `umbral_s`, con desde cuándo y su último error."""
    ahora = time.time() if ahora is None else ahora
    caidos: list[dict] = []
    try:
        rutas = sorted((Path(base) / "canales").glob("*.json"))
    except OSError:
        return caidos
    for path in rutas:
        doc = _leer(path)
        desde = _epoch(doc.get("fallando_desde") or "")
        if desde is None or ahora - desde < umbral_s:
            continue
        caidos.append(
            {
                "canal": doc.get("canal") or path.stem,
                "fallando_desde": doc["fallando_desde"],
                "ultimo_intento": doc.get("ultimo_intento", ""),
                "fallos": _entero(doc.get("fallos")),
                "ultimo_error": redactar(doc.get("ultimo_error", "")),
            }
        )
    return caidos
