"""Avisos a David: plantillas cerradas en español llano. Ningún LLM redacta un aviso.

`lint_plain` rechaza lo que David no tiene por qué leer: SHAs, rutas, jerga y
más de 8 líneas. Una prueba recorre todas las plantillas.
"""

from __future__ import annotations

import datetime as dt
import re

DIAS = ("lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo")

PLANTILLAS = {
    "entregado": "Listo, parte {n} de {total}: {aviso}.\nLa probé con datos reales: {prueba}.",
    "humano": ("Necesito tu mano un momento: {que_hacer}.\n"
               "Cuando acabes, dile a Claude «ya quedó».\nLo demás sigue avanzando."),
    "lento": ("Voy más lento: se acabó el cupo de todos los agentes de este paso.\n"
              "Sigo solo cuando se renueve, {hasta}. No tienes que hacer nada."),
    "terminado": "Terminé el plan: {total} partes hechas y probadas.\nNo queda nada pendiente.",
    "atorado": ("Me atoré en una parte: {bloque}.\n"
                "Ningún agente pudo resolverla; Claude la va a revisar y te cuenta.\n"
                "Lo demás del plan sigue avanzando."),
    "intrusion": ("Alguien cambió la rama principal sin pasar por la revisión.\n"
                  "Detuve los merges de este plan hasta que Claude lo revise."),
}

_HEX = re.compile(r"\b[0-9a-f]{7,40}\b")
_RUTA = re.compile(r"(^|\s)(~|\.{1,2}|/)[\w.~-]*/[\w./-]*")
_JERGA = re.compile(r"\b(sha|worktree|commit|rebase|traceback|stack|pid|stderr|stdout|exit=|json)\b", re.I)


class AvisoInvalido(ValueError):
    pass


def lint_plain(texto: str) -> str:
    problemas = []
    if len(texto.splitlines()) > 8:
        problemas.append("más de 8 líneas")
    if _HEX.search(texto):
        problemas.append("trae un identificador hexadecimal")
    if _RUTA.search(texto):
        problemas.append("trae una ruta de archivo")
    m = _JERGA.search(texto)
    if m:
        problemas.append(f"trae jerga: {m.group(0)}")
    if problemas:
        raise AvisoInvalido("; ".join(problemas))
    return texto


def _hora(epoch: str) -> str:
    t = dt.datetime.fromtimestamp(float(epoch))
    return f"el {DIAS[t.weekday()]} a las {t.hour}:{t.minute:02d}"


def render(template: str, campos: dict[str, str]) -> str:
    c = dict(campos)
    if "hasta" in c:
        c["hasta"] = _hora(c["hasta"])
    return lint_plain(PLANTILLAS[template].format(**c))
