"""Clasificación de fallas por proveedor, de los adapters de Hermes (goncloud-hermes,
`plugins/goncloud-team/subscriptions/adapters/`): un patrón común más lo que cada CLI dice a su manera.

    clasifica(proveedor, salida, errores="") -> {"kind": ..., "detalle": ...}

`kind` es el vocabulario del motor (cuota, rate_limit, auth, modelo_no_disponible, transitorio, falla).
La prueba `tests/test_fallas.py` recorre `tests/datos/fallas/<proveedor>-<caso>.txt`: una salida real por caso.

Regla de Hermes que se conserva: en la salida del modelo (stdout) un número suelto como 401 o 429 es
un ancho, una cuenta o una "página 404", nunca un código; solo cuenta junto a una pista HTTP. En
stderr, que es el canal de error del proveedor, el número suelto sí vale.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

PROVEEDORES = ("claude", "kimi", "opencode", "grok", "muse", "qwen", "deepseek")

@dataclass(frozen=True)
class OrigenProveedor:
    proveedor: str
    cuenta: str = ""
    ruta: str = ""
    intento: str = ""
    secuencia: int = 0
    reset_at: str = ""
@dataclass(frozen=True)
class CuotaAgotada:
    origen: OrigenProveedor
@dataclass(frozen=True)
class FrenoCorto:
    origen: OrigenProveedor
@dataclass(frozen=True)
class SinCanalFiable:
    proveedor: str
    motivo: str
CANAL_EN_VIVO = {"kimi": SinCanalFiable("kimi", "el CLI solo entrega el resultado final; no hay senal de cuota en vivo")}

_ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")

_AUTH = re.compile(
    r"unauthorized|not authenticated|authentication|credential|api[ _-]?key|not logged in|login required|"
    r"sign in required|keychain is locked|forbidden|\bexpired\b|signed out|invalid token|token (is )?(invalid|expired|missing)",
    re.I,
)
# La ventana del plan se acabó ("usage limit", el límite semanal de kimi). Gana sobre palabras de freno.
_QUOTA = re.compile(
    r"\bquota\b|\bbalance\b|insufficient|usage[ _-]?limit|out of credit|\b(?:daily|weekly|monthly)\b[^.\n]{0,24}\blimit",
    re.I,
)
# En la salida del agente solo frases de cuota inequívocas: "insufficient permissions" o un
# "exceeded" de una prueba no son cuota.
_QUOTA_EN_SALIDA = re.compile(
    r"usage[ _-]?limit|insufficient (?:balance|credit|quota)|quota exceeded|exceeded[^.\n]{0,20}quota|out of credit|"
    r"\b(?:daily|weekly|monthly)\b[^.\n]{0,24}\blimit",
    re.I,
)
# Un freno que nombra el plan o un reinicio de horas es la ventana gastada con palabras de freno.
_VENTANA_GASTADA = re.compile(
    r"\bresets?\s+at\b|\bresets?\s+in\b(?!\s+\d)|billing (?:period|cycle)|for (?:your|this|the) (?:current )?plan|plan[ _-]?limit",
    re.I,
)
_RESETS_IN = re.compile(r"\bresets?\s+in\s+(\d+(?:\.\d+)?)\s*([a-z]*)", re.I)
FRENO_CORTO_S = 120  # más largo que esto ya es cuota, no un freno corto
_FRENO = re.compile(
    r"rate[ _-]?limit|too many requests|retry[ _-]?after|throttl|try again (?:later|in a (?:moment|bit|few))|slow down|overloaded",
    re.I,
)
_FRENO_EN_SALIDA = re.compile(
    r"rate[ _-]?limit(?:s)?[ _-](?:exceeded|reached)|rate_limit_error|too many requests",
    re.I,
)
# Frase de auth que ningún modelo escribe por casualidad: vale también en la salida mezclada.
_AUTH_EN_SALIDA = re.compile(r"keychain is locked", re.I)
_EXCEDIDO = re.compile(r"\bexceeded\b", re.I)
_RETRY_AFTER = re.compile(
    r"(?:retry[ _-]?after|try again in)[:\s]+(\d+(?:\.\d+)?)\s*([a-z]*)", re.I
)
_UNIDADES = {
    **dict.fromkeys(("", "s", "sec", "secs", "second", "seconds"), 1.0),
    **dict.fromkeys(("ms", "millisecond", "milliseconds"), 0.001),
    **dict.fromkeys(("m", "min", "mins", "minute", "minutes"), 60.0),
    **dict.fromkeys(("h", "hr", "hrs", "hour", "hours"), 3600.0),
}
_MODELO = re.compile(
    r"model (is )?not|unknown model|no such model|not available|does not exist|deprecat",
    re.I,
)
# El proveedor contestó sobre el modelo, no sobre la cuenta: su 401 no es de credenciales.
_MODELO_NIVEL = re.compile(
    r"model (is )?disabled|model (is )?not enabled|model (is )?not allowed|model not permitted",
    re.I,
)
_INVALIDA = re.compile(r"invalid request|bad request|malformed|schema", re.I)
_TRANSPORTE = re.compile(
    r"connection[ _-]?error|connection reset|reset by peer|disconnected[ _-]+before[ _-]+response|"
    r"dns(?: lookup| resolution)? (?:failure|failed|error)|name or service not known|getaddrinfo|"
    r"connection refused|econnreset|econnrefused|enotfound|etimedout|network is unreachable|socket hang up|"
    r"\b50[234]\b[^\n]{0,20}(?:bad gateway|unavailable|timeout)|temporarily unavailable|overloaded",
    re.I,
)
_CODIGOS = {
    "401": "auth",
    "403": "auth",
    "402": "cuota",
    "429": "rate_limit",
    "404": "modelo_no_disponible",
    "400": "falla",
}
_PISTA_HTTP = r"(?:https?|status(?:[ _-]?code)?|code|error|response|responded|returned|failed(?:\s+with)?|rejected)"
_RAZON = r"(?:unauthorized|forbidden|payment required|too many requests|not found|bad request|insufficient balance)"
_CODIGO_HTTP = re.compile(
    rf"{_PISTA_HTTP}[^0-9a-z\n]{{0,16}}(40[0-4]|429)\b|\b(40[0-4]|429)\b[^0-9a-z\n]{{0,4}}{_RAZON}",
    re.I,
)
_RESET_ISO = re.compile(
    r"reset[s]?\s*(?:at|on)?\s*(\d{4}-\d\d-\d\dT\d\d:\d\d(?::\d\d)?Z?)", re.I
)

# Lo que cada CLI dice a su manera y el patrón común no ve. Se prueba primero, en orden.
_PROPIOS: dict[str, tuple[tuple[re.Pattern, str], ...]] = {
    "claude": (
        (re.compile(r"not logged in|please run /login|invalid api key", re.I), "auth"),
        (
            re.compile(
                r"issue with the selected model|may not exist or you may not have access",
                re.I,
            ),
            "modelo_no_disponible",
        ),
        (re.compile(r"request rejected \((\d{3})\)", re.I), "http"),
    ),
    "deepseek": (  # el CLI de Claude contra la API de DeepSeek: 402 es saldo agotado
        (re.compile(r"api error: 402|insufficient balance", re.I), "cuota"),
        (
            re.compile(
                r"authentication fails|failed to authenticate|invalid api key", re.I
            ),
            "auth",
        ),
        (re.compile(r"request rejected \((\d{3})\)", re.I), "http"),
    ),
    "kimi": (  # sin sesión no dice "login required": manda a /login
        (
            re.compile(
                r"no model configured|use /login|run `kimi` and use /login", re.I
            ),
            "auth",
        ),
    ),
    "opencode": (
        (re.compile(r"model unavailable", re.I), "modelo_no_disponible"),
        (
            re.compile(
                r"managed service port .* already in use|server process exited", re.I
            ),
            "transitorio",
        ),
    ),
    "grok": ((re.compile(r"not signed in|grok login", re.I), "auth"),),
    "muse": ((re.compile(r"missing meta credentials|muse login", re.I), "auth"),),
    "qwen": ((re.compile(r"no auth type is selected|--auth-type", re.I), "auth"),),
    "cursor-agent": (
        (
            re.compile(r"ActionRequiredError: .*not included in your weekly usage", re.I),
            "modelo_no_disponible",
        ),
    ),
}


def retry_after_s(texto: str) -> float | None:
    """Los segundos que el proveedor pidió esperar ("retry after 30s"), o None si no dijo o no se lee."""
    m = _RETRY_AFTER.search(texto)
    if not m:
        return None
    escala = _UNIDADES.get(m.group(2).lower())
    return None if escala is None else float(m.group(1)) * escala


def _canal(texto: str, suelto: bool) -> str | None:
    """Clasifica un canal de salida, o None. `suelto`: confía en un número de estado y en cualquier
    palabra de freno (stderr); si no, solo en contexto HTTP y en el idioma de error del proveedor."""
    if _MODELO_NIVEL.search(texto):
        return "modelo_no_disponible"
    if (_QUOTA if suelto else _QUOTA_EN_SALIDA).search(texto):
        return "cuota"
    freno = _FRENO if suelto else _FRENO_EN_SALIDA
    if freno.search(texto):
        return "rate_limit"
    if suelto:
        for codigo, kind in _CODIGOS.items():
            if re.search(rf"\b{codigo}\b", texto):
                return kind
    else:
        m = _CODIGO_HTTP.search(texto)
        if m:
            return _CODIGOS[m.group(1) or m.group(2)]
    if _TRANSPORTE.search(texto):
        return "transitorio"
    if not suelto:
        return "auth" if _AUTH_EN_SALIDA.search(texto) else None
    if _AUTH.search(texto):
        return "auth"
    if _EXCEDIDO.search(texto):
        return "cuota"
    if _MODELO.search(texto):
        return "modelo_no_disponible"
    if _INVALIDA.search(texto):
        return "falla"
    return None


def _gastada_o(kind: str, texto: str) -> str:
    """Un freno cuyas palabras nombran el plan o un reinicio lejano es cuota."""
    if kind != "rate_limit":
        return kind
    if _QUOTA.search(texto) or _VENTANA_GASTADA.search(texto):
        return "cuota"
    m = _RESETS_IN.search(texto)
    if m:
        escala = _UNIDADES.get(m.group(2).lower())
        if escala is None or float(m.group(1)) * escala > FRENO_CORTO_S:
            return "cuota"
    return kind


def _propio(proveedor: str, texto: str) -> str | None:
    for patron, kind in _PROPIOS.get(proveedor, ()):
        m = patron.search(texto)
        if not m:
            continue
        if kind == "http":
            kind = _CODIGOS.get(m.group(1), "falla")
            return _gastada_o(kind, texto)
        return kind
    return None


def clasifica(proveedor: str, salida: str, errores: str = "") -> dict:
    """Qué le pasó a un CLI que salió mal. `salida` es lo que dijo el modelo o el stream (número suelto
    no vale); `errores` es su stderr (número suelto sí vale). Sin pistas: "falla", con el final de lo que dijo."""
    salida, errores = _ANSI.sub("", salida or ""), _ANSI.sub("", errores or "")
    todo = f"{errores}\n{salida}"
    kind = _propio(proveedor, todo)
    if kind is None:
        for texto, suelto in ((errores, True), (salida, False)):
            kind = _canal(texto.lower(), suelto)
            if kind:
                kind = _gastada_o(kind, texto)
                break
    out: dict = {"kind": kind or "falla", "detalle": todo.strip()[-300:]}
    if out["kind"] == "cuota":
        m = _RESET_ISO.search(todo)
        out["reset_at"] = m.group(1) if m else ""
    if out["kind"] in ("cuota", "rate_limit"):
        out["retry_after_s"] = retry_after_s(todo)
    return out
