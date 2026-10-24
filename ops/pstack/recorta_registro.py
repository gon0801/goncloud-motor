#!/usr/bin/env python3
"""Recorta un registro real de un CLI a solo las llamadas a herramientas del agente principal.

    recorta_registro.py <registro> <cli> <salida>
    recorta_registro.py --comprueba <dir>

Del registro quedan el nombre de cada herramienta y estas claves de su entrada: `subagent_type`,
`description`, `model`, `items` (una lista con un `{}` por elemento), `file_path` y `command` (cortado a 200
caracteres). Salen los prompts, los mensajes del usuario, las respuestas de las herramientas y el
texto y el razonamiento del agente. La salida conserva el formato del CLI, para que `huella` la
lea igual que el original.

Con `--comprueba <dir>` recorta de nuevo cada original que nombra `<dir>/fuentes.json`
(`[{"salida", "cli", "origen"}]`) y sale 1 si la huella de un recorte no da los mismos subagentes
que la de su original.
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
TOKENS = re.compile(r"ghp_|github_pat_")


def _sanea(v):
    """Los agentes pegan regexes de detección de secretos en sus comandos; los prefijos de
    token no pueden quedar en el recorte o los linters de secretos lo marcan."""
    return TOKENS.sub("token-", v) if isinstance(v, str) else v


def reduce_args(args: dict) -> dict:
    """Solo las claves que identifican la llamada; `items` queda como un `{}` por elemento."""
    out = {}
    for k in CLAVES:
        if k not in args or args[k] in (None, ""):
            continue
        v = _sanea(args[k])
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
    if (
        ident
    ):  # el CLI repite la misma llamada con otro estado: la huella la cuenta una vez
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


def comprueba(d: Path) -> int:
    """Re-recorta cada original de `fuentes.json`: la huella del recorte y la del original deben
    dar los mismos subagentes."""
    fuentes = json.loads((d / "fuentes.json").read_text(encoding="utf-8"))
    mal = 0
    for f in fuentes:
        salida, cli, origen = d / f["salida"], f["cli"], Path(f["origen"])
        try:
            texto = Path(origen).read_text(encoding="utf-8", errors="replace")
        except OSError as e:
            print(f"{f['salida']}: no puedo leer el original: {e}")
            mal += 1
            continue
        o_original = len(ps.huella(texto, cli).subagentes)
        o_recorte = len(ps.huella(salida.read_text(encoding="utf-8"), cli).subagentes)
        de_nuevo = len(ps.huella(recorta(texto, cli), cli).subagentes)
        if not (o_original == o_recorte == de_nuevo):
            print(
                f"{f['salida']}: original {o_original} subagente(s), "
                f"recorte {o_recorte}, re-recorte {de_nuevo}"
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
            print(__doc__.strip().splitlines()[4], file=sys.stderr)
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
