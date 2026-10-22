"""Avisos a David: plantillas cerradas en español llano. Ningún modelo redacta un aviso.

Un solo canal para las tres máquinas: un bot de Telegram propio ("Motor") que solo envía.
Cada aviso empieza con la máquina y el plan ("En la Mini · Reporte de ventas:") para que David
compare las dos marcas. `lint_plain` rechaza SHAs, rutas, jerga y más de 8 líneas.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import re
import time
import urllib.parse
import urllib.request
from pathlib import Path

DIAS = ("lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo")

PLANTILLAS = {
    "conf_mala": (
        "Alguien cambió la configuración compartida y trae un error; no la apliqué.\n"
        "Sigo trabajando con la anterior. Quien la cambió tiene que corregirla."
    ),
    "entregado": "Listo, parte {n} de {total}: {aviso}.\nLa probé con datos reales: {prueba}.",
    "humano": (
        "Necesito tu mano un momento: {que_hacer}.\n"
        "Cuando acabes, dile «ya quedó» a quien te dio el plan.\nLo demás sigue avanzando."
    ),
    "lento": (
        "Voy más lento: se acabó el cupo de todos los agentes de este paso.\n"
        "Sigo solo cuando se renueve, {hasta}. No tienes que hacer nada."
    ),
    "terminado": "Terminé el plan: todas sus partes quedaron hechas y probadas.\nNo queda nada pendiente.",
    "atorado": (
        "Me atoré en una parte: {bloque}.\n"
        "Motivo: {motivo}.\n"
        "Claude la va a revisar y te cuenta. Lo demás del plan sigue avanzando."
    ),
    "retome": "Se fue la luz o se reinició la máquina; ya retomé solo donde me quedé.",
    "rechazado": (
        "No pude tomar este plan: {motivo}\n"
        "Se lo dije a {por}; tiene que corregirlo y entregarlo otra vez."
    ),
    "avance": "{listas} de {total} partes listas.\n{ahora}\n{atoron}",
}

_HEX = re.compile(r"\b[0-9a-f]{7,40}\b")
_RUTA = re.compile(r"(^|\s)(~|\.{1,2}|/)[\w.~-]*/[\w./-]*")
_JERGA = re.compile(
    r"\b(sha|worktree|commit|rebase|traceback|stack|pid|stderr|stdout|exit=|json)\b",
    re.I,
)


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


FIJOS = {
    "conf_mala": PLANTILLAS["conf_mala"],
    "entregado": "Listo, terminó una parte del plan.\nLa probé con datos reales.",
    "humano": (
        "Necesito tu mano en un paso que solo tú puedes hacer.\n"
        "Pídele a quien te dio el plan los pasos exactos; lo demás sigue avanzando."
    ),
    "lento": (
        "Voy más lento: se acabó el cupo de todos los agentes de este paso.\n"
        "Sigo solo cuando se renueve. No tienes que hacer nada."
    ),
    "terminado": PLANTILLAS["terminado"],
    "atorado": (
        "Me atoré en una parte del plan.\n"
        "Claude la va a revisar y te cuenta. Lo demás del plan sigue avanzando."
    ),
    "retome": PLANTILLAS["retome"],
    "rechazado": (
        "No pude tomar este plan; trae un problema que hay que corregir.\n"
        "Pídele a quien te lo dio que revise el recibo y lo entregue otra vez."
    ),
    "avance": "Sigo trabajando en el plan.",
}
PARCIAL = {  # con el título del plan y solo los campos que casi siempre llegan
    "avance": "{listas} de {total} partes listas. Sigo trabajando.",
    "rechazado": (
        "No pude tomar este plan; trae un problema que hay que corregir.\n"
        "Pídele a {por} que revise el recibo y lo entregue otra vez."
    ),
    "atorado": (
        "Me atoré en una parte: {bloque}.\n"
        "Claude la va a revisar y te cuenta. Lo demás del plan sigue avanzando."
    ),
    "humano": FIJOS["humano"],
}
GENERICO = "Avancé en un plan; quien te lo dio te puede contar el detalle."


def render(template: str, campos: dict[str, str], maquina: str, titulo: str) -> str:
    """Aviso completo, ya revisado, y nunca lanza: si el texto con los datos del plan no pasa
    `lint_plain`, prueba con "un plan" por título, luego con el texto fijo de la plantilla sin
    ningún campo, y al final con el aviso genérico."""
    c = dict(campos)

    def completo(titulo: str) -> str:
        datos = dict(c)
        if "hasta" in datos:
            datos["hasta"] = _hora(datos["hasta"])
        return f"En la {maquina} · {titulo}:\n{PLANTILLAS[template].format(**datos)}"

    def parcial() -> str:
        return f"En la {maquina} · {titulo}:\n{PARCIAL[template].format(**{'por': 'quien te lo dio', 'total': '?', **c})}"

    intentos = [
        lambda: completo(str(titulo)),
        parcial,
        lambda: completo("un plan"),
        lambda: f"En la {maquina}:\n{FIJOS[template]}",
        lambda: f"En la {maquina}:\n{GENERICO}",
    ]
    for intento in intentos:
        try:
            return lint_plain(intento())
        except Exception:  # noqa: BLE001  un aviso nunca se pierde por un dato del plan
            continue
    return GENERICO


class Notifier:
    """Cola en disco y envío al-menos-una-vez: si la máquina se apaga entre enviar y mover el
    archivo, el aviso puede repetirse una vez; nunca se pierde."""

    def __init__(self, home: Path, base_url: str, token: str, chat: str):
        self.pend, self.env = (
            home / "avisos" / "pendientes",
            home / "avisos" / "enviados",
        )
        self.base_url, self.token, self.chat = base_url.rstrip("/"), token, chat

    def encola(self, key: str, texto: str) -> None:
        self.pend.mkdir(parents=True, exist_ok=True)
        nombre = re.sub(r"[^A-Za-z0-9_.-]", "_", key)
        tmp = self.pend / f".{nombre}.tmp"
        tmp.write_text(texto, encoding="utf-8")
        os.replace(tmp, self.pend / f"{int(time.time() * 1000)}-{nombre}.txt")

    def envia_pendientes(self) -> int:
        if not (self.token and self.chat and self.pend.exists()):
            return 0
        self.env.mkdir(parents=True, exist_ok=True)
        n = 0
        for f in sorted(self.pend.glob("*.txt")):
            data = urllib.parse.urlencode(
                {"chat_id": self.chat, "text": f.read_text(encoding="utf-8")}
            ).encode()
            try:
                with urllib.request.urlopen(
                    f"{self.base_url}/bot{self.token}/sendMessage",
                    data=data,
                    timeout=20,
                ) as r:
                    if not json.loads(r.read() or b"{}").get("ok", True):
                        return n
            except OSError:
                return n
            os.replace(f, self.env / f.name)
            n += 1
        return n
