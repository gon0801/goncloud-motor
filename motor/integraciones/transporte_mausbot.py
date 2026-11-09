"""Adaptador del canal MausBot para la PC.

Sólo usa la biblioteca estándar para que corra como script suelto en el equipo
donde vive OpenMausBot, sin depender del paquete `motor`.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from html.parser import HTMLParser
from pathlib import Path

EXE = r"C:\Users\ehven\AppData\Local\Programs\openmausbot\OpenMausBot.exe"
JS = (
    r"C:\Users\ehven\AppData\Local\Programs\openmausbot\resources\server"
    r"\openmausbot.js"
)
TIMEOUT_S = 30.0
CAMPOS_DE_ID = ("id", "messageId", "turnId", "thread")


class _SinEtiquetas(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.partes: list[str] = []

    def handle_data(self, data: str) -> None:
        self.partes.append(data)


def _exe() -> str:
    return os.environ.get("MOTOR_MAUSBOT_EXE") or EXE


def _js() -> str:
    return os.environ.get("MOTOR_MAUSBOT_JS", JS)


def argv_base() -> list[str]:
    argv = [_exe()]
    js = _js()
    if js:
        argv.append(js)
    return argv


def _timeout() -> float:
    try:
        valor = float(os.environ.get("MOTOR_MAUSBOT_TIMEOUT", str(TIMEOUT_S)))
    except ValueError:
        return TIMEOUT_S
    return valor if valor > 0 else TIMEOUT_S


def entorno() -> dict[str, str]:
    """El CLI de OpenMausBot sólo corre como Node si ELECTRON_RUN_AS_NODE está puesto."""
    env = os.environ.copy()
    env["ELECTRON_RUN_AS_NODE"] = "1"
    return env


def _bot() -> str:
    canal = ""
    sesion = ""
    base = os.environ.get("MOTOR_AVISO_BASE", "")
    encargo = os.environ.get("MOTOR_AVISO_ENCARGO", "")
    if base and encargo:
        try:
            ruta = Path(base) / "canal-identidad" / f"{encargo}.json"
            doc = json.loads(ruta.read_bytes().decode("utf-8-sig"))
            if isinstance(doc, dict):
                canal = str(doc.get("canal") or "")
                sesion = str(doc.get("sesion") or "")
        except (OSError, ValueError):
            canal = sesion = ""
    bot = sesion.strip() or canal
    if not bot or bot.lower() == "mausbot":
        return "MausBot"
    return bot


def _quitar_html(texto: str) -> str:
    if os.environ.get("MOTOR_AVISO_FORMATO") != "html":
        return texto
    parser = _SinEtiquetas()
    parser.feed(texto)
    parser.close()
    return "".join(parser.partes).strip()


def _detalle(hecho: subprocess.CompletedProcess[bytes]) -> str:
    crudo = hecho.stderr or hecho.stdout
    return crudo.decode("utf-8", errors="replace").strip()


def _send() -> int:
    cuerpo = sys.stdin.read()
    aviso = os.environ.get("MOTOR_AVISO_ID", "")
    bot = _bot()
    texto = _quitar_html(cuerpo).strip() + "\n\n[motor-aviso:" + aviso + "]"
    argv = argv_base() + ["send", bot, texto]
    try:
        hecho = subprocess.run(
            argv, capture_output=True, env=entorno(), timeout=_timeout()
        )
    except subprocess.TimeoutExpired:
        print("mausbot: el envio no respondio a tiempo", file=sys.stderr)
        return 1
    except OSError as exc:
        print(f"mausbot: no se pudo ejecutar el envio: {exc}", file=sys.stderr)
        return 1
    if hecho.returncode != 0:
        print(
            f"mausbot: el envio salio {hecho.returncode}: {_detalle(hecho)}",
            file=sys.stderr,
        )
        return hecho.returncode or 1
    print(json.dumps({"id": aviso}))
    return 0


def _id_del_dict(doc: dict, heredado: str) -> str:
    for campo in CAMPOS_DE_ID:
        valor = doc.get(campo)
        if valor not in (None, ""):
            return str(valor)
    return heredado


def _buscar(dato: object, aviso: str, heredado: str | None = None) -> str | None:
    if isinstance(dato, dict):
        propio = _id_del_dict(dato, heredado or aviso)
        for valor in dato.values():
            if isinstance(valor, str) and aviso and aviso in valor:
                return propio
        for valor in dato.values():
            hallado = _buscar(valor, aviso, propio)
            if hallado is not None:
                return hallado
        return None
    if isinstance(dato, list):
        for valor in dato:
            hallado = _buscar(valor, aviso, heredado)
            if hallado is not None:
                return hallado
    return None


def _check(argv: list[str]) -> int:
    aviso = os.environ.get("MOTOR_AVISO_ID", "")
    if not aviso and argv:
        aviso = argv[-1]
    bot = _bot()
    comando = argv_base() + ["messages", bot, "--json"]
    try:
        hecho = subprocess.run(
            comando, capture_output=True, env=entorno(), timeout=_timeout()
        )
    except subprocess.TimeoutExpired:
        print("mausbot: la consulta no respondio a tiempo", file=sys.stderr)
        return 1
    except OSError as exc:
        print(f"mausbot: no se pudo consultar: {exc}", file=sys.stderr)
        return 1
    if hecho.returncode != 0:
        print(
            f"mausbot: la consulta salio {hecho.returncode}: {_detalle(hecho)}",
            file=sys.stderr,
        )
        return 1
    try:
        doc = json.loads(hecho.stdout.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        print(f"mausbot: la consulta no devolvio JSON: {exc}", file=sys.stderr)
        return 1
    ident = _buscar(doc, aviso)
    if ident is None:
        print(json.dumps({"ausente": True}))
        return 0
    print(json.dumps({"id": ident}))
    return 0


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if not args or args[0] not in ("send", "check"):
        print("uso: transporte_mausbot.py send|check [aviso]", file=sys.stderr)
        return 2
    if args[0] == "send":
        return _send()
    return _check(args[1:])


if __name__ == "__main__":
    raise SystemExit(main())
