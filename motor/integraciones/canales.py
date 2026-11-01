"""Estado de los canales de avisos: cuánto llevan fallando y con qué error.

Vive en `base/canales/<canal>.json`, escrito por `drenar` y leído por `motor director ver` y por la señal a
Kuma. Leerlo no toca el canal: un canal caído no puede esconder que está caído.
"""

from __future__ import annotations

import json
import re
import time
from datetime import datetime, timezone
from pathlib import Path

UMBRAL_CAIDO_S = 600
TOPE_ERROR = 200

_SECRETO = re.compile(r"https?://\S+|\b\d{6,}:[A-Za-z0-9_-]{20,}\b")


def redactar(texto: str, tope: int = TOPE_ERROR) -> str:
    """Un error de transporte sin direcciones ni tokens, en una línea y acotado."""
    t = _SECRETO.sub("[oculto]", str(texto or ""))
    t = re.sub(r"\s+", " ", t).strip()
    return t if len(t) <= tope else t[: tope - 1].rstrip() + "…"


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
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return doc if isinstance(doc, dict) else {}


def _guardar(path: Path, doc: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporal = path.with_suffix(".tmp")
    temporal.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporal.replace(path)


def registrar_pasada(base: Path, ahora: float, fallaron: dict[str, str], respondieron: set[str]) -> None:
    """Anota el resultado de una pasada de avisos: canales cuyo transporte falló (con su error) y canales que respondieron."""
    for canal, error in fallaron.items():
        path = _ruta(base, canal)
        doc = _leer(path) or {"canal": canal}
        doc.setdefault("fallando_desde", _iso(ahora))
        doc["fallos"] = int(doc.get("fallos") or 0) + 1
        doc["ultimo_intento"] = _iso(ahora)
        doc["ultimo_error"] = redactar(error)
        _guardar(path, doc)
    for canal in respondieron - set(fallaron):
        path = _ruta(base, canal)
        if "fallando_desde" in _leer(path):
            _guardar(path, {"canal": canal, "ultimo_exito": _iso(ahora)})


def canales_caidos(base: Path, ahora: float | None = None, umbral_s: float = UMBRAL_CAIDO_S) -> list[dict]:
    """Los canales que llevan fallando al menos `umbral_s`, con desde cuándo y su último error."""
    ahora = time.time() if ahora is None else ahora
    caidos = []
    for path in sorted((Path(base) / "canales").glob("*.json")):
        doc = _leer(path)
        desde = _epoch(doc.get("fallando_desde") or "")
        if desde is None or ahora - desde < umbral_s:
            continue
        caidos.append(
            {
                "canal": doc.get("canal") or path.stem,
                "fallando_desde": doc["fallando_desde"],
                "ultimo_intento": doc.get("ultimo_intento", ""),
                "fallos": int(doc.get("fallos") or 0),
                "ultimo_error": redactar(doc.get("ultimo_error", "")),
            }
        )
    return caidos
