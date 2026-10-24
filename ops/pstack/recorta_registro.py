#!/usr/bin/env python3
"""Recorta un registro real de un CLI a solo las llamadas a herramientas del agente principal.

    recorta_registro.py <registro> <cli> <salida>
    recorta_registro.py --comprueba <dir>

Del registro quedan el nombre de cada herramienta y estas claves de su entrada: `subagent_type`,
`description`, `model`, `items` (una lista con un `{}` por elemento), `file_path` y `command`
(cortado a 200 caracteres). Toda forma de secreto de `SECRETOS` sale como `<secreto>` en cada
campo que se guarda. Salen los prompts, los mensajes del usuario, las respuestas de las herramientas y el
texto y el razonamiento del agente. La salida conserva el formato del CLI, para que `huella` la
lea igual que el original.

Con `--comprueba <dir>` recorta de nuevo cada original que nombra `<dir>/fuentes.json`
(`[{"salida", "cli", "origen"}]`, con `~` en lugar del home) y sale 1 si el recorte no tiene los
mismos subagentes que su original, uno por uno (herramienta y descripción), o si falta un original.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from motor import pstack as ps  # noqa: E402

CLAVES = ("subagent_type", "description", "model", "file_path", "command")
TOPE_COMANDO = 200
SECRETO = "<secreto>"

# Cada forma de secreto con un ejemplo que la cumple (armado por partes: este archivo no puede
# traer un token). La limpieza, `--comprueba` y tests/test_recorta.py usan esta misma lista.
SECRETOS = (
    (re.compile(r"ghp_[A-Za-z0-9_]*"), "ghp" + "_" + "a1B2" * 9),
    (re.compile(r"github_pat_[A-Za-z0-9_]*"), "github" + "_pat_" + "a1B2" * 9),
    (re.compile(r"[0-9]{8,10}:[A-Za-z0-9_-]{35}"), "123456789" + ":" + "aB3_-" * 7),
    (re.compile(r"(?<![A-Za-z0-9])sk-[A-Za-z0-9_-]{16,}"), "sk" + "-" + "aB3dE6" * 4),
    (re.compile(r"AKIA[0-9A-Z]{16}"), "AKIA" + "ABCD1234" * 2),
    (re.compile(r"-----BEGIN"), "-----" + "BEGIN PRIVATE KEY"),
)


def limpia(v):
    """Cambia por `<secreto>` toda forma de `SECRETOS` en un valor que se guarda (str, lista o dict).
    Los agentes pegan regexes de detección y credenciales en sus comandos."""
    if isinstance(v, str):
        previo = None
        # una clave pegada a otra: la segunda pasada limpia lo que la primera destapó
        while previo != v:
            previo = v
            for patron, _ in SECRETOS:
                v = patron.sub(SECRETO, v)
        return v
    if isinstance(v, list):
        return [limpia(x) for x in v]
    if isinstance(v, dict):
        return {k: limpia(x) for k, x in v.items()}
    return v


def reduce_args(args: dict) -> dict:
    """Solo las claves que identifican la llamada, limpias; `items` queda como una lista con un
    `{}` por elemento: cuántos son, sin su contenido."""
    out = {}
    for k in CLAVES:
        if k not in args or args[k] in (None, ""):
            continue
        v = limpia(args[k])
        out[k] = v[:TOPE_COMANDO] if k == "command" and isinstance(v, str) else v
    items = args.get("items")
    if isinstance(items, list):
        out["items"] = [{} for _ in items]
    return out


def _evento(cli: str, llamadas: list[tuple[str, dict]], ident: str) -> dict:
    """Las llamadas de un objeto del registro, en el formato del CLI: un solo evento, para que la
    huella las deduplique por id igual que en el original."""
    if cli == "kimi":
        o = {
            "role": "assistant",
            "tool_calls": [
                {"function": {"name": n, "arguments": json.dumps(a)}}
                for n, a in llamadas
            ],
        }
    elif cli == "opencode":
        nombre, args = llamadas[0]  # opencode: una parte de herramienta por objeto
        o = {"type": "tool_use", "part": {"tool": nombre, "state": {"input": args}}}
    elif cli == "grok":
        nombre, args = llamadas[0]  # grok: una línea por herramienta
        o = {"tool_name": nombre, "args": args}
    elif cli == "muse":
        o = {
            "type": "assistant_tool_calls_committed",
            "calls": [{"name": n, "args": json.dumps(a)} for n, a in llamadas],
        }
    else:  # claude y qwen
        o = {
            "type": "assistant",
            "message": {
                "content": [
                    {"type": "tool_use", "name": n, "input": a} for n, a in llamadas
                ]
            },
        }
    # el CLI repite la misma llamada con otro estado: la huella la cuenta una vez
    if ident:
        o["tool_call_id"] = ident
    return o


def recorta(texto: str, cli: str) -> str:
    """Una línea JSON por objeto del registro que llama herramientas, en el formato del CLI."""
    f = ps.FORMATOS[cli]
    out = []
    for i, o in enumerate(ps._objetos(texto)):
        llamadas, _ = f.extrae(o)
        if not llamadas:
            continue
        llamadas = [(n, reduce_args(a)) for n, a in llamadas]
        ident = str(o.get("tool_call_id") or o.get("callID") or f"recorte-{i}")
        if cli in ("opencode", "grok"):  # un evento por llamada: su formato es así
            for par in llamadas:
                out.append(json.dumps(_evento(cli, [par], ident), ensure_ascii=False))
            continue
        out.append(json.dumps(_evento(cli, llamadas, ident), ensure_ascii=False))
    return "\n".join(out) + ("\n" if out else "")


def subagentes(texto: str, cli: str, limpiar: bool = False) -> list[tuple[str, str]]:
    """(herramienta, descripción) de cada subagente del registro, en orden y con las mismas reglas
    de conteo que `huella` (mismo generador de llamadas). `limpiar` aplica la limpieza a la
    descripción de un original."""
    f = ps.FORMATOS[cli]
    out = []
    for herramienta, args in ps._llamadas(texto, cli)[0]:
        n = sum(a.tipo == "subagente" for a in ps._clasifica(f, herramienta, args))
        desc = args.get("description") or ""
        out += [(herramienta, limpia(desc) if limpiar else desc)] * n
    return out


def comprueba(d: Path) -> int:
    """Re-recorta cada original de `fuentes.json` (rutas con `~`): el recorte guardado y el
    re-recorte deben tener los mismos subagentes que el original, uno por uno. Sin original no
    hay nada comprobado: se nombra y sale 1."""
    fuentes = json.loads((d / "fuentes.json").read_text(encoding="utf-8"))
    mal = 0
    for f in fuentes:
        salida, cli = d / f["salida"], f["cli"]
        origen = Path(f["origen"]).expanduser()
        try:
            texto = origen.read_text(encoding="utf-8", errors="replace")
        except OSError as e:
            print(f"{f['salida']}: falta el original {origen}: {e}")
            mal += 1
            continue
        esperado = subagentes(texto, cli, limpiar=True)
        try:
            guardado = subagentes(salida.read_text(encoding="utf-8"), cli)
        except OSError as e:
            print(f"{f['salida']}: falta el recorte {salida}: {e}")
            mal += 1
            continue
        de_nuevo = subagentes(recorta(texto, cli), cli)
        if not (esperado == guardado == de_nuevo):
            print(
                f"{f['salida']}: original {len(esperado)} subagente(s), "
                f"recorte {len(guardado)}, re-recorte {len(de_nuevo)}"
                + (
                    ""
                    if len(esperado) != len(guardado)
                    else "; difieren en herramienta o descripción"
                )
            )
            mal += 1
    if mal:
        print(f"{mal} registro(s) no cuadran")
        return 1
    print(f"{len(fuentes)} registros: el recorte conserva los subagentes")
    return 0


def main(argv: list[str]) -> int:
    if argv[:1] == ["--comprueba"]:
        if len(argv) != 2:
            print(__doc__.strip().splitlines()[3], file=sys.stderr)
            return 2
        return comprueba(Path(argv[1]))
    if len(argv) != 3:
        print(__doc__.strip().splitlines()[2], file=sys.stderr)
        return 2
    registro, cli, salida = argv
    if cli not in ps.FORMATOS:
        print(f"cli desconocido: {cli} (hay {', '.join(ps.FORMATOS)})", file=sys.stderr)
        return 2
    texto = Path(registro).read_text(encoding="utf-8", errors="replace")
    Path(salida).write_text(recorta(texto, cli), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
