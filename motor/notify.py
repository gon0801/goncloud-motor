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
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

TOPE_TELEGRAM = 4000  # Telegram corta en 4096
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
        "Sigo intentando; lo demás del plan sigue avanzando."
    ),
    "retome": "Se fue la luz o se reinició la máquina; ya retomé solo donde me quedé.",
    "rechazado": (
        "No pude tomar este plan: {motivo}\n"
        "Se lo dije a {por}; tiene que corregirlo y entregarlo otra vez."
    ),
    "avance": "{listas} de {total} partes listas.\n{ahora}\n{atoron}",
    "externo": "Alguien integró el bloque «{bloque}» por fuera; sigo con eso.",
    "version": (
        "Falta instalar algo: esta máquina necesita la versión {version} del motor.\n"
        "Sigo trabajando con la que tengo; quien administra el motor tiene que instalarla."
    ),
    "incierto": (
        "Una parte quedó a medias y no voy a repetirla a ciegas.\n"
        "Quien administra el motor tiene que mirar qué pasó antes de seguir con esa parte."
    ),
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
        "Sigo intentando; lo demás del plan sigue avanzando."
    ),
    "retome": PLANTILLAS["retome"],
    "rechazado": (
        "No pude tomar este plan; trae un problema que hay que corregir.\n"
        "Pídele a quien te lo dio que revise el recibo y lo entregue otra vez."
    ),
    "avance": "Sigo trabajando en el plan.",
    "externo": "Alguien integró una parte del plan por fuera; sigo con eso.",
    "version": (
        "Falta instalar algo: esta máquina necesita otra versión del motor.\n"
        "Sigo trabajando con la que tengo; quien administra el motor tiene que instalarla."
    ),
    "incierto": (
        "Una parte quedó a medias y no voy a repetirla a ciegas.\n"
        "Quien administra el motor tiene que mirar qué pasó antes de seguir con esa parte."
    ),
}
PARCIAL = {  # con el título del plan y solo los campos que casi siempre llegan
    "avance": "{listas} de {total} partes listas. Sigo trabajando.",
    "rechazado": (
        "No pude tomar este plan; trae un problema que hay que corregir.\n"
        "Pídele a {por} que revise el recibo y lo entregue otra vez."
    ),
    "atorado": (
        "Me atoré en una parte: {bloque}.\n"
        "Sigo intentando; lo demás del plan sigue avanzando."
    ),
    "humano": FIJOS["humano"],
    "externo": FIJOS["externo"],
    "version": FIJOS["version"],
    "incierto": FIJOS["incierto"],
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
            return lint_plain(intento())[:TOPE_TELEGRAM]
        except Exception:  # noqa: BLE001  un aviso nunca se pierde por un dato del plan
            continue
    return GENERICO


def _sano(ident: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]", "_", ident).strip("._")


_SIGUE = set("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_")


def _coincide(nombre: str, sano: str) -> bool:
    """El id va entero: `cmd_1` no es `cmd_10`. Un archivo viejo puede llevar hora o un guion delante."""
    if not sano or nombre.startswith(".") or nombre.endswith(".tmp"):
        return False
    inicio = 0
    while True:
        i = nombre.find(sano, inicio)
        if i < 0:
            return False
        antes = nombre[i - 1] if i else ""
        j = i + len(sano)
        despues = nombre[j] if j < len(nombre) else ""
        if antes not in _SIGUE and despues not in _SIGUE:
            return True
        inicio = i + 1


def _clasifica(status: int, cuerpo: bytes) -> str:
    """`entregado` solo con HTTP 200 y `ok` exactamente verdadero. El resto no se da por enviado."""
    if status == 429 or status >= 500 or status <= 0:
        return "pendiente"
    if status != 200:
        return "fallido" if 400 <= status < 500 else "pendiente"
    try:
        data = json.loads(cuerpo.decode("utf-8"))
    except (UnicodeError, ValueError):
        return "fallido"
    if isinstance(data, dict) and data.get("ok") is True:
        return "entregado"
    return "fallido"


def base_url_valida(url: str) -> bool:
    """Solo http o https con host. Una url sin esquema no llega a urlopen."""
    partes = urllib.parse.urlsplit((url or "").strip())
    return partes.scheme in {"http", "https"} and bool(partes.netloc)


class Notifier:
    """Cola en disco. La entrega es al menos una vez: si el proceso muere después de enviar
    y antes de mover el archivo, el mismo aviso puede salir otra vez."""

    def __init__(self, home: Path, base_url: str, token: str, chat: str):
        self.pend, self.env, self.malos = (
            home / "avisos" / "pendientes",
            home / "avisos" / "enviados",
            home / "avisos" / "fallidos",
        )
        self.base_url, self.token, self.chat = base_url.rstrip("/"), token, chat
        self.causa = ""

    def _carpetas(self) -> tuple[Path, Path, Path]:
        return self.pend, self.env, self.malos

    def _archivo(self, ident: str) -> Path | None:
        sano = _sano(ident)
        if not sano:
            return None
        for carpeta in self._carpetas():
            if not carpeta.is_dir():
                continue
            for path in carpeta.iterdir():
                if path.is_file() and _coincide(path.name, sano):
                    return path
        return None

    def materializa(self, notice_id: str, texto: str, key: str = "") -> None:
        """Crea el archivo pendiente si no hay uno, nuevo o histórico, para este aviso."""
        if self._archivo(notice_id) or (key and self._archivo(key)):
            return
        self.pend.mkdir(parents=True, exist_ok=True)
        nombre = _sano(notice_id) or "aviso"
        payload = {
            "notice_id": notice_id,
            "texto": texto,
            "intento": 0,
            "no_antes": 0,
            "key": key,
        }
        tmp = self.pend / f".{nombre}.tmp"
        tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, self.pend / f"{nombre}.json")

    def encola(self, key: str, texto: str) -> None:
        self.materializa(key, texto, key)

    def reintenta(self, notice_id: str) -> bool:
        """Vuelve a dejar pendiente el mismo id. No crea otro aviso."""
        nombre = (_sano(notice_id) or "aviso") + ".json"
        src = self.malos / nombre
        if not src.is_file():
            return False
        try:
            data = json.loads(src.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = {"notice_id": notice_id, "texto": src.read_text(encoding="utf-8")}
        if not isinstance(data, dict):
            data = {"notice_id": notice_id, "texto": str(data)}
        data["notice_id"] = notice_id
        data["no_antes"] = 0
        self.pend.mkdir(parents=True, exist_ok=True)
        tmp = self.pend / f".{nombre}.tmp"
        tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, self.pend / nombre)
        src.unlink()
        return True

    def _anota_causa(self, texto: str) -> None:
        self.causa = texto
        try:
            self.pend.parent.mkdir(parents=True, exist_ok=True)
            (self.pend.parent / "causa.txt").write_text(texto, encoding="utf-8")
        except OSError:
            pass

    def _limpia_causa(self) -> None:
        self.causa = ""
        try:
            (self.pend.parent / "causa.txt").unlink(missing_ok=True)
        except OSError:
            pass

    def _espera(self, path: Path) -> None:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        if not isinstance(data, dict) or "notice_id" not in data:
            return
        n = int(data.get("intento") or 0) + 1
        data["intento"] = n
        data["no_antes"] = time.time() + min(30 * (2 ** min(n, 5)), 900)
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, path)

    def _texto(self, path: Path) -> str:
        crudo = path.read_text(encoding="utf-8")
        if path.suffix != ".json":
            return crudo
        try:
            data = json.loads(crudo)
        except ValueError:
            return crudo
        if isinstance(data, dict) and isinstance(data.get("texto"), str):
            return data["texto"]
        return crudo

    def _listo(self, path: Path) -> bool:
        if path.suffix != ".json":
            return True
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return True
        if not isinstance(data, dict):
            return True
        return float(data.get("no_antes") or 0) <= time.time()

    def envia_pendientes(self) -> int:
        if not (self.token and self.chat):
            self._anota_causa("avisos: falta token o chat")
            return 0
        if not base_url_valida(self.base_url):
            self._anota_causa("avisos: url sin esquema")
            return 0
        if not self.pend.is_dir():
            return 0
        self.env.mkdir(parents=True, exist_ok=True)
        n = 0
        archivos = sorted(
            p
            for p in self.pend.iterdir()
            if p.is_file() and p.suffix in {".txt", ".json"} and not p.name.startswith(".")
        )
        for f in archivos:
            if not self._listo(f):
                continue
            cuerpo = urllib.parse.urlencode(
                {"chat_id": self.chat, "text": self._texto(f)}
            ).encode()
            try:
                with urllib.request.urlopen(
                    f"{self.base_url}/bot{self.token}/sendMessage",
                    data=cuerpo,
                    timeout=20,
                ) as r:
                    veredicto = _clasifica(getattr(r, "status", 200), r.read() or b"")
            except urllib.error.HTTPError as e:
                veredicto = _clasifica(e.code, e.read() or b"")
            except ValueError:
                self._anota_causa("avisos: url invalida")
                return n
            except OSError:
                self._anota_causa("transporte no contesto")
                self._espera(f)
                return n
            if veredicto == "pendiente":
                self._anota_causa("transporte ocupado o caido")
                self._espera(f)
                return n
            if veredicto != "entregado":
                self.malos.mkdir(parents=True, exist_ok=True)
                os.replace(f, self.malos / f.name)
                self._anota_causa("respuesta sin ok")
                continue
            os.replace(f, self.env / f.name)
            self._limpia_causa()
            n += 1
        return n


def estado_aviso(home: Path, identes: list[str]) -> str | None:
    """`pendiente`, `entregado` o `fallido` si hay un archivo de alguno de esos ids. Si no, None."""
    sanos = [s for s in (_sano(x) for x in identes) if s]
    if not sanos:
        return None
    for estado, nombre in (
        ("pendiente", "pendientes"),
        ("entregado", "enviados"),
        ("fallido", "fallidos"),
    ):
        carpeta = home / "avisos" / nombre
        if not carpeta.is_dir():
            continue
        for path in carpeta.iterdir():
            if path.is_file() and any(_coincide(path.name, s) for s in sanos):
                return estado
    return None


def salud(home: Path, eventos=None) -> dict:
    """No trata una cola vacía como entrega. Un evento sin archivo queda desconocido."""
    base = home / "avisos"

    def lista(nombre: str) -> list[Path]:
        carpeta = base / nombre
        if not carpeta.is_dir():
            return []
        return [
            p
            for p in carpeta.iterdir()
            if p.is_file() and not p.name.startswith(".") and p.suffix in {".txt", ".json"}
        ]

    pend, enviados, malos = lista("pendientes"), lista("enviados"), lista("fallidos")
    nombres = [p.name for p in (*pend, *enviados, *malos)]
    desconocidos: list[str] = []
    for e in eventos or []:
        if getattr(e, "kind", "") not in ("NoticeQueued", "NoticeRequested"):
            continue
        data = getattr(e, "data", {}) or {}
        identes = [str(data.get("key") or ""), str(data.get("notice_id") or "")]
        sanos = [_sano(x) for x in identes if x]
        if not any(s and any(_coincide(nombre, s) for nombre in nombres) for s in sanos):
            desconocidos.append(str(data.get("notice_id") or data.get("key") or ""))
    causa = ""
    marca = base / "causa.txt"
    if marca.is_file():
        try:
            causa = marca.read_text(encoding="utf-8").strip().splitlines()[0][:200]
        except OSError:
            causa = ""
    if malos:
        entrega = "fallido"
    elif pend:
        entrega = "pendiente"
    elif desconocidos:
        entrega = "desconocido"
    elif enviados:
        entrega = "entregado"
    else:
        entrega = "sin_avisos"
    return {
        "pendientes": len(pend),
        "entregados": len(enviados),
        "fallidos": len(malos),
        "desconocidos": desconocidos,
        "causa": causa,
        "entrega": entrega,
    }
