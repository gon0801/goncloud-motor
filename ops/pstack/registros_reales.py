"""Datos de prueba sacados de registros reales de pstack, sin prompts.

recorta <carpeta_intento> <carpeta_salida>
juzga <carpeta_plan> <intento>=<cumple|falta>...
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

RAIZ_REPO = Path(__file__).resolve().parents[2]
if str(RAIZ_REPO) not in sys.path:
    sys.path.insert(0, str(RAIZ_REPO))

from motor import pstack as ps  # noqa: E402

_CAMPO = {"Read": "file_path", "Skill": "skill", "Bash": "command"}
_META = ("intento", "cli", "lane", "role", "raiz", "exigencia")


def reduce_input(nombre: str, args: dict, raiz: str) -> dict:
    if nombre in ("Agent", "Task"):
        return (
            {"subagent_type": args["subagent_type"]} if "subagent_type" in args else {}
        )
    if nombre == "Bash":
        cmd = str(args.get("command", ""))
        return {"command": ps.sin_heredocs(cmd) if raiz and raiz in cmd else "true"}
    campo = _CAMPO.get(nombre)
    return {campo: args[campo]} if campo and campo in args else {}


def recorta_registro(lineas, raiz: str) -> list[dict]:
    salida = []
    for linea in lineas:
        try:
            o = json.loads(linea)
        except ValueError:
            continue
        if (
            not isinstance(o, dict)
            or o.get("type") != "assistant"
            or o.get("parent_tool_use_id")
        ):
            continue
        contenido = (o.get("message") or {}).get("content") or []
        usos = [
            {
                "type": "tool_use",
                "name": b.get("name", ""),
                "input": reduce_input(b.get("name", ""), b.get("input") or {}, raiz),
            }
            for b in contenido
            if isinstance(b, dict) and b.get("type") == "tool_use"
        ]
        if usos:
            salida.append({"type": "assistant", "message": {"content": usos}})
    return salida


def primer_registro(info: dict) -> Path | None:
    return next((Path(p) for p in info.get("registros", []) if Path(p).is_file()), None)


def recorta(carpeta_intento: Path, carpeta_salida: Path) -> None:
    info = json.loads((carpeta_intento / "pstack.json").read_text(encoding="utf-8"))
    fuente = primer_registro(info)
    if fuente is None:
        raise SystemExit(f"{carpeta_intento}: ningún archivo de `registros` existe")
    with fuente.open(encoding="utf-8") as f:
        registro = recorta_registro(f, info["raiz"])
    carpeta_salida.mkdir(parents=True, exist_ok=True)
    (carpeta_salida / "registro.jsonl").write_text(
        "".join(json.dumps(o, ensure_ascii=False) + "\n" for o in registro),
        encoding="utf-8",
    )
    meta = {k: info.get(k) for k in _META}
    meta["intento"] = meta["intento"] or carpeta_intento.name
    (carpeta_salida / "meta.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=1) + "\n", encoding="utf-8"
    )


def juzga(carpeta_plan: Path, esperados: list[str]) -> int:
    mal = 0
    for par in esperados:
        intento, _, esperado = par.partition("=")
        info = json.loads(
            (carpeta_plan / intento / "pstack.json").read_text(encoding="utf-8")
        )
        fuente = primer_registro(info)
        if fuente is None:
            raise SystemExit(f"{intento}: ningún archivo de `registros` existe")
        h = ps.huella(fuente.read_text(encoding="utf-8"), info["cli"], info.get("raiz"))
        v = ps.juzga(h, ps.Exigencia.from_dict(info.get("exigencia") or {}))
        salio = "cumple" if isinstance(v, ps.Cumple) else "falta"
        print(
            f"{intento} cumple"
            if salio == "cumple"
            else f"{intento} falta: {v.texto()}"
        )
        mal += salio != esperado
    return 1 if mal else 0


def main(argv: list[str]) -> int:
    if len(argv) == 3 and argv[0] == "recorta":
        recorta(Path(argv[1]), Path(argv[2]))
        return 0
    if len(argv) >= 3 and argv[0] == "juzga":
        return juzga(Path(argv[1]), argv[2:])
    print(
        "uso: registros_reales.py recorta <carpeta_intento> <carpeta_salida>\n"
        "     registros_reales.py juzga <carpeta_plan> <intento>=<cumple|falta>...",
        file=sys.stderr,
    )
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
