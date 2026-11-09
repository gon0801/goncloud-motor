"""Candados que impiden debilitar en silencio la publicacion y los secretos.

Uso, igual en local y en el job `gate`:

    python3 tools/candados.py cuerpo
    python3 tools/candados.py secretos

`cuerpo` falla si otro camino edita el cuerpo de un PR fuera de
`_escribir_cuerpo_pr`. `secretos` falla si el cambio agrega una entrada a la
allowlist de gitleaks, quita un hook de `.pre-commit-config.yaml`, permite
saltar la bateria por el mensaje del commit o quita la regla de secretos del
prompt del responsable.

La excepcion se escribe en la misma linea, con razon y fecha (AAAA-MM-DD), y
David la aprueba en el PR. Un `--raiz` distinto permite reproducir un cambio en
un temporal sin tocar el arbol de trabajo.
"""

from __future__ import annotations

import argparse
import ast
import re
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]

# Lo que hoy tiene el repo. Una entrada nueva exige su excepcion anotada.
ALLOWLIST_GITLEAKS = frozenset(
    {
        r"docs/evidence/correcciones-3-4/componentes-final-mini\.json$",
        r"docs/evidence/correcciones-3-4/componentes-final-pc\.json$",
        r"docs/evidence/correcciones-3-4/final-directores\.json$",
        r"tests/test_pstack\.py$",
    }
)
HOOKS = frozenset(
    {
        "gitleaks",
        "check-merge-conflict",
        "calidad-tipos",
        "calidad-lint",
        "calidad-io",
    }
)
REGLA_SECRETOS = "Nunca subas credenciales ni llaves a un repo"
_FECHA = re.compile(r"\b\d{4}-\d{2}-\d{2}\b")
_EXCEPCION = re.compile(r"#\s*excepcion:", re.IGNORECASE)
_CADENA = re.compile(r"'''([^']*)'''|\"([^\"]*)\"|'([^']*)'")
# El mensaje del commit solo puede sumar jobs. Un literal que hable de saltar
# la bateria es la forma en que #344 la apagaba.
_SALTOS = ("skip", "saltar", "no-ci", "sin-ci", "bateria=false", "bateria:false")


def _leer(raiz: Path, relativa: str) -> str:
    return (raiz / relativa).read_text(encoding="utf-8")


def _anotada(linea: str) -> bool:
    return bool(_EXCEPCION.search(linea)) and bool(_FECHA.search(linea))


def _entradas_de_lista(lineas: list[str], clave: str) -> list[tuple[str, int, str]]:
    """Las cadenas de una lista TOML simple, con su linea y su texto original."""
    entradas: list[tuple[str, int, str]] = []
    dentro = False
    for numero, linea in enumerate(lineas, start=1):
        if not dentro:
            if re.match(rf"\s*{clave}\s*=\s*\[", linea):
                dentro = True
            else:
                continue
        for encontrada in _CADENA.finditer(linea):
            valor = next(g for g in encontrada.groups() if g is not None)
            entradas.append((valor, numero, linea))
        if "]" in linea.split("#", 1)[0]:
            dentro = False
    return entradas


def _revisar_gitleaks(raiz: Path) -> list[str]:
    try:
        lineas = _leer(raiz, ".gitleaks.toml").splitlines()
    except OSError as exc:
        return [f".gitleaks.toml: no se puede leer ({exc})"]
    problemas = []
    for valor, numero, linea in _entradas_de_lista(lineas, "paths"):
        if valor in ALLOWLIST_GITLEAKS or _anotada(linea):
            continue
        problemas.append(
            f".gitleaks.toml:{numero}: entrada nueva en la allowlist "
            f"({valor}); anota la excepcion en la misma linea con razon y fecha"
        )
    for valor, numero, linea in _entradas_de_lista(lineas, "regexes"):
        if _anotada(linea):
            continue
        problemas.append(
            f".gitleaks.toml:{numero}: regex nuevo en la allowlist "
            f"({valor}); anota la excepcion en la misma linea con razon y fecha"
        )
    return problemas


def _revisar_precommit(raiz: Path) -> list[str]:
    try:
        texto = _leer(raiz, ".pre-commit-config.yaml")
    except OSError as exc:
        return [f".pre-commit-config.yaml: no se puede leer ({exc})"]
    presentes = set(re.findall(r"^\s*-\s*id:\s*([A-Za-z0-9_.-]+)", texto, re.M))
    faltan = HOOKS - presentes
    problemas = []
    for hook in sorted(faltan):
        excepcion = re.search(
            rf"#\s*excepcion:.*{re.escape(hook)}\b.*\d{{4}}-\d{{2}}-\d{{2}}",
            texto,
            re.IGNORECASE,
        )
        if excepcion:
            continue
        problemas.append(
            f".pre-commit-config.yaml: falta el hook {hook}; deja la "
            "excepcion anotada con razon y fecha"
        )
    return problemas


def _funcion(arbol: ast.AST, nombre: str) -> ast.FunctionDef | None:
    for nodo in ast.walk(arbol):
        if isinstance(nodo, (ast.FunctionDef, ast.AsyncFunctionDef)) and nodo.name == nombre:
            return nodo  # type: ignore[return-value]
    return None


def _quita_jobs(nodo: ast.AST) -> bool:
    """Solo la seleccion puede encoger: restar o descartar de `por_rutas`/`correr`."""
    for hijo in ast.walk(nodo):
        if isinstance(hijo, ast.BinOp) and isinstance(hijo.op, ast.Sub):
            if {"correr", "por_rutas"} & {
                x.id for x in ast.walk(hijo.left) if isinstance(x, ast.Name)
            }:
                return True
        if isinstance(hijo, ast.Call) and isinstance(hijo.func, ast.Attribute):
            valor = hijo.func.value
            if (
                isinstance(valor, ast.Name)
                and valor.id in ("correr", "por_rutas")
                and hijo.func.attr
                in ("remove", "discard", "difference", "symmetric_difference")
            ):
                return True
    return False


def _revisar_bateria(raiz: Path) -> list[str]:
    ruta = "motor/encargos/trabajos_ci.py"
    try:
        fuente = _leer(raiz, ruta)
        arbol = ast.parse(fuente)
    except (OSError, SyntaxError) as exc:
        return [f"{ruta}: no se puede leer ({exc})"]
    problemas = []
    extra = _funcion(arbol, "_jobs_extra")
    calcular = _funcion(arbol, "_calcular")
    if extra is None or calcular is None:
        return [f"{ruta}: falta la seleccion por diff (_jobs_extra/_calcular)"]
    for nombre, nodo in (("_jobs_extra", extra), ("_calcular", calcular)):
        if _quita_jobs(nodo):
            problemas.append(
                f"{ruta}: {nombre} puede quitar jobs por el mensaje del commit"
            )
    buscadores = [
        nodo
        for nodo in ast.walk(arbol)
        if isinstance(nodo, ast.Call)
        and isinstance(nodo.func, ast.Attribute)
        and isinstance(nodo.func.value, ast.Name)
        and nodo.func.value.id == "re"
        and nodo.func.attr in ("search", "match", "findall", "fullmatch")
    ]
    for nodo in buscadores:
        if _funcion_que_contiene(arbol, nodo) != "_jobs_extra":
            problemas.append(
                f"{ruta}:{nodo.lineno}: lee el mensaje del commit fuera de "
                "_jobs_extra; solo `jobs_extra=` puede sumar jobs"
            )
    for nodo in ast.walk(arbol):
        if isinstance(nodo, ast.Constant) and isinstance(nodo.value, str):
            bajo = nodo.value.lower()
            if any(salto in bajo for salto in _SALTOS):
                problemas.append(
                    f"{ruta}:{getattr(nodo, 'lineno', 0)}: literal que permite "
                    f"saltar la bateria ({nodo.value!r})"
                )
    return problemas


def _funcion_que_contiene(arbol: ast.AST, objetivo: ast.AST) -> str:
    """El nombre de la funcion mas interna que contiene la linea del nodo."""
    linea = getattr(objetivo, "lineno", 0)
    mejor: ast.FunctionDef | ast.AsyncFunctionDef | None = None
    for nodo in ast.walk(arbol):
        if not isinstance(nodo, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if nodo.lineno <= linea <= (nodo.end_lineno or nodo.lineno):
            if mejor is None or nodo.lineno > mejor.lineno:
                mejor = nodo
    return mejor.name if mejor is not None else "<module>"


def _revisar_prompt(raiz: Path) -> list[str]:
    ruta = "motor/encargos/comprobacion.py"
    try:
        texto = _leer(raiz, ruta)
    except OSError as exc:
        return [f"{ruta}: no se puede leer ({exc})"]
    if REGLA_SECRETOS not in texto:
        return [
            f"{ruta}: falta la regla de secretos del responsable "
            f"({REGLA_SECRETOS!r})"
        ]
    return []


def revisar_secretos(raiz: Path = RAIZ) -> list[str]:
    return [
        *_revisar_gitleaks(raiz),
        *_revisar_precommit(raiz),
        *_revisar_bateria(raiz),
        *_revisar_prompt(raiz),
    ]


def revisar_cuerpo(raiz: Path = RAIZ) -> list[str]:
    ruta = "motor/encargos/ciclo.py"
    try:
        arbol = ast.parse(_leer(raiz, ruta))
    except (OSError, SyntaxError) as exc:
        return [f"{ruta}: no se puede leer ({exc})"]
    problemas = []
    for nodo in ast.walk(arbol):
        if (
            isinstance(nodo, ast.Call)
            and isinstance(nodo.func, ast.Attribute)
            and nodo.func.attr == "actualizar_cuerpo"
        ):
            if _funcion_que_contiene(arbol, nodo) != "_escribir_cuerpo_pr":
                problemas.append(
                    f"{ruta}:{nodo.lineno}: edita el cuerpo del PR fuera de "
                    "`_escribir_cuerpo_pr`"
                )
    permitidos = {
        "_LIMITE_CUERPO_PR": {"_leer_cuerpo_del_espacio", "_cuerpo_con_limite"},
        "_NOTA_CUERPO_RECORTADO": {"_cuerpo_con_limite"},
    }
    for nodo in ast.walk(arbol):
        if (
            isinstance(nodo, ast.Name)
            and isinstance(nodo.ctx, ast.Load)
            and nodo.id in permitidos
        ):
            funciones = permitidos[nodo.id]
            if _funcion_que_contiene(arbol, nodo) not in funciones:
                problemas.append(
                    f"{ruta}:{nodo.lineno}: usa {nodo.id} fuera de la puerta "
                    "del limite `_cuerpo_con_limite`"
                )
    if _funcion(arbol, "_escribir_cuerpo_pr") is None:
        problemas.append(f"{ruta}: falta la puerta unica `_escribir_cuerpo_pr`")
    if _funcion(arbol, "_cuerpo_con_limite") is None:
        problemas.append(f"{ruta}: falta la puerta del limite `_cuerpo_con_limite`")
    return problemas


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="candados")
    ap.add_argument("modo", choices=("cuerpo", "secretos"))
    ap.add_argument("--raiz", default=str(RAIZ))
    args = ap.parse_args(argv)
    raiz = Path(args.raiz)
    problemas = (
        revisar_cuerpo(raiz) if args.modo == "cuerpo" else revisar_secretos(raiz)
    )
    if not problemas:
        print(f"{args.modo}: sin debilitamientos")
        return 0
    print(f"{args.modo}: {len(problemas)} problema(s)", file=sys.stderr)
    for problema in problemas:
        print(f"  {problema}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
