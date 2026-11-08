"""Candados de calidad con línea base: frenan lo nuevo, no lo que ya estaba.

Uso, igual en local y en CI:

    python3 tools/calidad.py tipos
    python3 tools/calidad.py lint
    python3 tools/calidad.py io

`tipos` y `lint` comparan contra `.github/calidad/baseline.json` y fallan solo
si el cambio agrega un error. `io` es estricto: cualquier uso fuera de
`motor/io.py` falla salvo los archivos exentos por una prohibición expresa.
`--actualizar` regenera `tipos` y `lint` (se hace desde la rama base, no desde el
PR que se está revisando).
"""

from __future__ import annotations

import argparse
import ast
import collections
import json
import shutil
import subprocess
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
BASE = RAIZ / ".github" / "calidad" / "baseline.json"
IO_PERMITIDO = RAIZ / "motor" / "io.py"
MENSAJE_IO = "usa motor.io.leer_json / escribir_texto"
IO_EXENTOS = {
    "motor/daemon.py": "modo planes: prohibición expresa de tocarlo",
}

# Los recorridos crudos de eventos y los archivos `.json` que hoy viven en
# `ciclo.py` son deuda conocida: se congelan aquí para que uno nuevo no entre
# sin pasar por el registro. `marcadores` falla con cualquiera que no esté.
MARCADORES_EVENTOS = {
    "_alcance_para_revision": 1,
    "_anotar_lectura_deepseek": 1,
    "_apariciones": 1,
    "_barrer_encargos": 1,
    "_cierre_epoch": 1,
    "_contener_fallo_de_barrido": 1,
    "_corte_de_instrumento": 1,
    "_cuerpo_ya_publicado": 1,
    "_disputas_pendientes": 1,
    "_evidencia_revision": 1,
    "_fallos_de_entrega_seguidos": 1,
    "_fallos_de_repo_seguidos": 1,
    "_fase_de_resultado": 1,
    "_impedimento_de_instrumento": 1,
    "_incorporar": 1,
    "_incorporar_responsable": 1,
    "_juicio_reparado": 1,
    "_marcador_autoridad_final": 1,
    "_mirar_autoridad": 2,
    "_nuevo_cierre_tras_marcador": 1,
    "_observacion_de_la_version": 1,
    "_paso_entrega_paso": 1,
    "_preguntas_pedidas_al_revisor": 1,
    "_recibir_resultado_nuevo": 1,
    "_resultado_desde_fold": 1,
    "_soltar_conductor": 1,
}
MARCADORES_JSON = {
    ".json",
    "arranque.json",
    "auth.json",
    "ci-sondeo.json",
    "cierre.json",
    "comprobaciones.json",
    "corregir id, operacion, clave y cuerpo en .interno/pedido-efecto.json",
    "deepseek_cerrado.json",
    "deepseek_lectura.json",
    "disputa.json",
    "fases.json",
    "flota-home.json",
    "generacion.json",
    "herramienta.json",
    "intento-*/observaciones*.json",
    "intento-*/observaciones.json",
    "observaciones-revision.json",
    "observaciones.json",
    "outcome.json",
    "pedido-efecto.json",
    "proc.json",
    "quietud.json",
    "resultado.json",
    "revision.json",
    "wrapper.json",
}
MENSAJE_MARCADORES = "anótalo como evento y derívalo en dominio"


VERSIONES = {"pyright": "1.1.414", "ruff": "0.17.4"}


def _ejecutable(nombre: str, modulo: str) -> list[str]:
    ruta = shutil.which(nombre)
    if ruta:
        return [ruta]
    if shutil.which("uv"):
        return ["uv", "tool", "run", "--from", f"{nombre}=={VERSIONES[nombre]}", nombre]
    return [sys.executable, "-m", modulo]


def _relativa(ruta: str) -> str:
    try:
        return str(Path(ruta).resolve().relative_to(RAIZ))
    except ValueError:
        return ruta


def _json_de_la_herramienta(nombre: str, ejecucion) -> object:
    """Solo acepta una corrida que produjo un resultado utilizable.

    Pyright y ruff salen con 1 cuando encontraron diagnósticos: eso es una
    corrida válida. Cualquier otro código, la salida vacía o un JSON que no
    parsea significan que la herramienta no analizó el código, y entonces el
    candado no puede decir «sin errores nuevos».
    """
    detalle = (ejecucion.stderr or ejecucion.stdout or "")[-2000:]
    if ejecucion.returncode not in (0, 1):
        raise SystemExit(
            f"{nombre} no pudo analizar (código {ejecucion.returncode}):\n{detalle}"
        )
    if not ejecucion.stdout.strip():
        raise SystemExit(f"{nombre} no devolvió resultado:\n{detalle}")
    try:
        return json.loads(ejecucion.stdout)
    except ValueError as exc:
        raise SystemExit(f"{nombre} no devolvió JSON: {exc}\n{detalle}") from exc


def diagnosticos_tipos() -> collections.Counter:
    salida = subprocess.run(
        [*_ejecutable("pyright", "pyright"), "--outputjson"],
        cwd=RAIZ,
        capture_output=True,
        text=True,
        check=False,
    )
    datos = _json_de_la_herramienta("pyright", salida)
    if not isinstance(datos, dict) or "generalDiagnostics" not in datos:
        raise SystemExit(
            "pyright no devolvió su lista de diagnósticos:\n"
            + (salida.stderr or salida.stdout or "")[-2000:]
        )
    cuenta: collections.Counter = collections.Counter()
    for d in datos.get("generalDiagnostics") or []:
        if d.get("severity") != "error":
            continue
        cuenta[
            (
                _relativa(str(d.get("file") or "")),
                str(d.get("rule") or ""),
                str(d.get("message") or ""),
            )
        ] += 1
    return cuenta


def diagnosticos_lint() -> collections.Counter:
    salida = subprocess.run(
        [*_ejecutable("ruff", "ruff"), "check", ".", "--output-format", "json"],
        cwd=RAIZ,
        capture_output=True,
        text=True,
        check=False,
    )
    datos = _json_de_la_herramienta("ruff", salida)
    if not isinstance(datos, list):
        raise SystemExit(
            "ruff no devolvió su lista de diagnósticos:\n"
            + (salida.stderr or salida.stdout or "")[-2000:]
        )
    cuenta: collections.Counter = collections.Counter()
    for d in datos:
        cuenta[
            (
                _relativa(str(d.get("filename") or "")),
                str(d.get("code") or ""),
                str(d.get("message") or ""),
            )
        ] += 1
    return cuenta


def _llamadas_io(arbol: ast.AST) -> list[tuple[str, int]]:
    encontradas = []
    for nodo in ast.walk(arbol):
        if not isinstance(nodo, ast.Call):
            continue
        funcion = nodo.func
        if isinstance(funcion, ast.Attribute) and funcion.attr in (
            "read_text",
            "write_text",
        ):
            encontradas.append((funcion.attr, nodo.lineno))
        if (
            isinstance(funcion, ast.Attribute)
            and isinstance(funcion.value, ast.Name)
            and funcion.value.id == "json"
            and funcion.attr == "loads"
            and any(
                isinstance(x, ast.Call)
                and isinstance(x.func, ast.Attribute)
                and x.func.attr == "read_text"
                for x in ast.walk(nodo)
            )
        ):
            encontradas.append(("json.loads(read_text)", nodo.lineno))
    return encontradas


def usos_io() -> collections.Counter:
    cuenta: collections.Counter = collections.Counter()
    for archivo in sorted((RAIZ / "motor").rglob("*.py")):
        if archivo.resolve() == IO_PERMITIDO.resolve():
            continue
        try:
            arbol = ast.parse(archivo.read_text(encoding="utf-8"))
        except SyntaxError as exc:
            raise SystemExit(f"no se puede leer {archivo}: {exc}")
        for patron, _linea in _llamadas_io(arbol):
            cuenta[(str(archivo.relative_to(RAIZ)), patron)] += 1
    return cuenta


def _recorridos_de_marcadores(arbol: ast.AST) -> collections.Counter:
    cuenta: collections.Counter = collections.Counter()
    pila: list[str] = []

    class Visita(ast.NodeVisitor):
        def visit_FunctionDef(self, nodo: ast.FunctionDef) -> None:
            pila.append(nodo.name)
            self.generic_visit(nodo)
            pila.pop()

        visit_AsyncFunctionDef = visit_FunctionDef

        def visit_Call(self, nodo: ast.Call) -> None:
            funcion = nodo.func
            if isinstance(funcion, ast.Attribute) and funcion.attr == "eventos":
                cuenta[("eventos", pila[-1] if pila else "<module>")] += 1
            self.generic_visit(nodo)

        def visit_Constant(self, nodo: ast.Constant) -> None:
            if isinstance(nodo.value, str) and nodo.value.endswith(".json"):
                cuenta[("json", nodo.value)] += 1

    Visita().visit(arbol)
    return cuenta


def marcadores_nuevos() -> collections.Counter:
    ruta = RAIZ / "motor" / "encargos" / "ciclo.py"
    try:
        arbol = ast.parse(ruta.read_text(encoding="utf-8"))
    except SyntaxError as exc:
        raise SystemExit(f"no se puede leer {ruta}: {exc}")
    nuevos: collections.Counter = collections.Counter()
    for (tipo, clave), n in _recorridos_de_marcadores(arbol).items():
        if tipo == "eventos":
            permitido = MARCADORES_EVENTOS.get(clave, 0)
        else:
            permitido = n if clave in MARCADORES_JSON else 0
        if n > permitido:
            nuevos[(tipo, clave)] = n - permitido
    return nuevos


def _cargar() -> dict:
    if not BASE.is_file():
        return {}
    return json.loads(BASE.read_text(encoding="utf-8"))


def _lista(cuenta: collections.Counter) -> list[list[str]]:
    return sorted([list(clave), str(n)] for clave, n in cuenta.items())


def _cuenta(lista: list[list[str]]) -> collections.Counter:
    return collections.Counter({tuple(clave): int(n) for clave, n in lista})


def _comparar(cuenta: collections.Counter, linea_base: dict, modo: str) -> int:
    cuenta = collections.Counter(cuenta)
    previos = _cuenta(linea_base.get(modo) or [])
    nuevos = cuenta - previos
    if not nuevos:
        print(f"{modo}: sin errores nuevos (hoy: {sum(cuenta.values())}; base: {sum(previos.values())})")
        return 0
    print(f"{modo}: {sum(nuevos.values())} error(es) nuevo(s)", file=sys.stderr)
    for clave, n in sorted(nuevos.items()):
        for _ in range(n):
            if modo == "io":
                print(f"  {clave[0]}: {clave[1]}; {MENSAJE_IO}", file=sys.stderr)
            else:
                print(f"  {clave[0]}: {clave[1]}: {clave[2]}", file=sys.stderr)
    return 1


def _comparar_io(cuenta: collections.Counter) -> int:
    """Estricto: un uso fuera de `motor/io.py` falla aunque el archivo ya tuviera
    usos históricos. Restar créditos por archivo dejaba reintroducir una lectura
    en un archivo que la migración había limpiado."""
    fuera = {
        clave: n
        for clave, n in cuenta.items()
        if clave[0] not in IO_EXENTOS
    }
    if not fuera:
        exentos = sum(cuenta.values())
        detalle = ", ".join(sorted(IO_EXENTOS))
        print(
            f"io: sin usos fuera de motor/io.py "
            f"(exentos: {exentos} en {detalle or 'ninguno'})"
        )
        return 0
    print(f"io: {sum(fuera.values())} error(es) nuevo(s)", file=sys.stderr)
    for clave, n in sorted(fuera.items()):
        for _ in range(n):
            print(f"  {clave[0]}: {clave[1]}; {MENSAJE_IO}", file=sys.stderr)
    return 1


def _comparar_marcadores(nuevos: collections.Counter) -> int:
    if not nuevos:
        print("marcadores: sin recorridos crudos ni .json nuevos en ciclo.py")
        return 0
    print(f"marcadores: {sum(nuevos.values())} nuevo(s)", file=sys.stderr)
    for (tipo, clave), n in sorted(nuevos.items()):
        for _ in range(n):
            print(
                f"  motor/encargos/ciclo.py: {tipo} {clave}; {MENSAJE_MARCADORES}",
                file=sys.stderr,
            )
    return 1


def _actualizar(linea_base: dict) -> None:
    linea_base["tipos"] = _lista(diagnosticos_tipos())
    linea_base["lint"] = _lista(diagnosticos_lint())
    linea_base.pop("io", None)
    BASE.parent.mkdir(parents=True, exist_ok=True)
    BASE.write_text(json.dumps(linea_base, ensure_ascii=False, indent=1) + "\n")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="calidad")
    ap.add_argument("modo", choices=("tipos", "lint", "io", "marcadores"))
    ap.add_argument("--actualizar", action="store_true")
    args = ap.parse_args(argv)
    linea_base = _cargar()
    if args.actualizar:
        _actualizar(linea_base)
        return 0
    if args.modo == "tipos":
        return _comparar(diagnosticos_tipos(), linea_base, "tipos")
    if args.modo == "lint":
        return _comparar(diagnosticos_lint(), linea_base, "lint")
    if args.modo == "marcadores":
        return _comparar_marcadores(marcadores_nuevos())
    return _comparar_io(usos_io())


if __name__ == "__main__":
    raise SystemExit(main())
