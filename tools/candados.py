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
import importlib.util
import re
import sys
import tomllib
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
ALLOWLIST_CLAVES = frozenset({"description", "targetRules", "paths", "regexes"})
TARGET_RULES = frozenset(
    {"generic-api-key", "sentry-access-token", "discord-api-token"}
)
REGLA_SECRETOS = "Nunca subas credenciales ni llaves a un repo"
_FECHA = re.compile(r"\b\d{4}-\d{2}-\d{2}\b")
_EXCEPCION = re.compile(r"#\s*excepcion:", re.IGNORECASE)
_CADENA = re.compile(r"'''([^']*)'''|\"([^\"]*)\"|'([^']*)'")


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


def _claves_de_allowlist(lineas: list[str]) -> list[tuple[str, int, str]]:
    """Cada clave de cada bloque `[[allowlists]]`, con su línea."""
    claves: list[tuple[str, int, str]] = []
    dentro = False
    for numero, linea in enumerate(lineas, start=1):
        limpia = linea.split("#", 1)[0].strip()
        if limpia.startswith("[["):
            dentro = limpia == "[[allowlists]]"
            continue
        if limpia.startswith("["):
            dentro = False
            continue
        if not dentro:
            continue
        encontrada = re.match(r"([A-Za-z0-9_.-]+)\s*=", limpia)
        if encontrada:
            claves.append((encontrada.group(1), numero, linea))
    return claves


def _linea_anotada(lineas: list[str], clave: str) -> bool:
    return any(clave in linea and _anotada(linea) for linea in lineas)


def _revisar_gitleaks(raiz: Path) -> list[str]:
    try:
        texto = _leer(raiz, ".gitleaks.toml")
    except OSError as exc:
        return [f".gitleaks.toml: no se puede leer ({exc})"]
    lineas = texto.splitlines()
    problemas = []
    for clave, numero, linea in _claves_de_allowlist(lineas):
        if clave in ALLOWLIST_CLAVES or _anotada(linea):
            continue
        problemas.append(
            f".gitleaks.toml:{numero}: clave nueva en la allowlist "
            f"({clave}); anota la excepcion en la misma linea con razon y fecha"
        )
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
    try:
        config = tomllib.loads(texto)
    except tomllib.TOMLDecodeError as exc:
        problemas.append(f".gitleaks.toml: no se puede leer ({exc})")
        return problemas
    extend = config.get("extend") or {}
    if extend.get("useDefault") is not True and not _linea_anotada(
        lineas, "useDefault"
    ):
        problemas.append(
            ".gitleaks.toml: useDefault apagado; las reglas por defecto no "
            "corren. Anota la excepcion en la misma linea con razon y fecha"
        )
    for bloque in config.get("allowlists") or []:
        if not isinstance(bloque, dict):
            continue
        rutas = bloque.get("paths") or []
        reglas = bloque.get("targetRules")
        if rutas and not reglas and not _linea_anotada(lineas, "targetRules"):
            problemas.append(
                ".gitleaks.toml: la allowlist tiene rutas sin targetRules, "
                "asi que valen para todas las reglas; exceptua con razon y fecha"
            )
            continue
        extra = set(reglas or ()) - TARGET_RULES
        if extra and not _linea_anotada(lineas, "targetRules"):
            problemas.append(
                ".gitleaks.toml: targetRules suma reglas "
                f"({sorted(extra)}); la allowlist cubre mas de lo aceptado"
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


_RUTAS_DE_PRUEBA = (
    ("motor/encargos/ciclo.py",),
    ("tests/test_menores_repetidos.py",),
    ("ops/tests/correr.sh",),
    ("conf-ejemplo/x.json",),
    ("docs/diseno-flujo-revisiones.md",),
    ("docs/otro.md",),
)
_EXTRAS_DE_PRUEBA = (
    (),
    ("bateria",),
    ("macos",),
    ("windows",),
    ("ops-tests",),
    ("macos", "windows"),
)


def _cargar_modulo(raiz: Path, relativa: str):
    ruta = raiz / relativa
    if str(RAIZ) not in sys.path:
        sys.path.insert(0, str(RAIZ))
    nombre = "candados_" + re.sub(r"\W", "_", relativa)
    especificacion = importlib.util.spec_from_file_location(nombre, ruta)
    if especificacion is None or especificacion.loader is None:
        raise ImportError(f"no se puede cargar {ruta}")
    modulo = importlib.util.module_from_spec(especificacion)
    especificacion.loader.exec_module(modulo)
    return modulo


def _revisar_bateria(raiz: Path) -> list[str]:
    """La seleccion solo puede sumar jobs por el mensaje del commit.

    Se prueba el comportamiento: para las rutas de muestra, agregar cualquier
    `extra` no puede quitar un job que el diff ya activaba. Asi cualquier forma
    de recorte (resta, interseccion, un `or` que reemplaza) falla.
    """
    ruta = "motor/encargos/trabajos_ci.py"
    try:
        fuente = _leer(raiz, ruta)
        arbol = ast.parse(fuente)
        modulo = _cargar_modulo(raiz, ruta)
    except (OSError, SyntaxError, ImportError) as exc:
        return [f"{ruta}: no se puede leer ({exc})"]
    problemas = []
    for rutas in _RUTAS_DE_PRUEBA:
        esperados = set(modulo.trabajos_a_correr(rutas))
        if not esperados:
            continue
        for extra in _EXTRAS_DE_PRUEBA:
            valores, _ = modulo._calcular(rutas, extra)
            activos = {job for job, corre in valores.items() if corre}
            faltan = esperados - activos
            if faltan:
                problemas.append(
                    f"{ruta}: puede quitar jobs por el mensaje del commit "
                    f"(deja de correr {sorted(faltan)} con extra={extra})"
                )
                break
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
        arbol = ast.parse(texto)
    except (OSError, SyntaxError) as exc:
        return [f"{ruta}: no se puede leer ({exc})"]
    funcion = _funcion(arbol, "instrucciones_responsable")
    if funcion is not None:
        for nodo in ast.walk(funcion):
            if (
                isinstance(nodo, ast.Constant)
                and isinstance(nodo.value, str)
                and REGLA_SECRETOS in nodo.value
            ):
                return []
    return [
        f"{ruta}: falta la regla de secretos del responsable "
        f"({REGLA_SECRETOS!r}); un comentario no cuenta como prompt"
    ]


def revisar_secretos(raiz: Path = RAIZ) -> list[str]:
    return [
        *_revisar_gitleaks(raiz),
        *_revisar_precommit(raiz),
        *_revisar_bateria(raiz),
        *_revisar_prompt(raiz),
    ]


def _llama_a(nodo: ast.AST, nombre: str) -> bool:
    return (
        isinstance(nodo, ast.Call)
        and isinstance(nodo.func, ast.Name)
        and nodo.func.id == nombre
    )


def _argumento(call: ast.Call, indice: int, nombre: str) -> ast.expr | None:
    if len(call.args) > indice:
        return call.args[indice]
    for clave in call.keywords:
        if clave.arg == nombre:
            return clave.value
    return None


def _cuerpo_limitado(nodo: ast.expr | None) -> bool:
    return _llama_a(nodo, "_cuerpo_con_limite") or _llama_a(nodo, "_cuerpo_pr")


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
        if _llama_a(nodo, "_escribir_cuerpo_pr"):
            cierre = _argumento(nodo, 4, "cierre")
            vacio = (
                cierre is None
                or isinstance(cierre, ast.Constant)
                and (cierre.value is None or cierre.value == "")
            )
            if len(nodo.args) != 5 or nodo.keywords or vacio:
                problemas.append(
                    f"{ruta}:{nodo.lineno}: llama a `_escribir_cuerpo_pr` sin "
                    "el cierre del motor; la puerta recorta solo el texto"
                )
        if (
            isinstance(nodo, ast.Call)
            and isinstance(nodo.func, ast.Attribute)
            and nodo.func.attr == "abrir_pr"
        ):
            cuerpo = _argumento(nodo, 4, "cuerpo")
            if not _cuerpo_limitado(cuerpo):
                problemas.append(
                    f"{ruta}:{nodo.lineno}: abre un PR sin el limite del "
                    "cuerpo; usa `_cuerpo_pr` o `_cuerpo_con_limite`"
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
    puerta = _funcion(arbol, "_escribir_cuerpo_pr")
    if puerta is None:
        problemas.append(f"{ruta}: falta la puerta unica `_escribir_cuerpo_pr`")
    elif not any(_llama_a(nodo, "_cuerpo_con_limite") for nodo in ast.walk(puerta)):
        problemas.append(
            f"{ruta}: `_escribir_cuerpo_pr` no aplica el limite del cuerpo"
        )
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
