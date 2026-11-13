"""Candados de calidad con línea base: frenan lo nuevo, no lo que ya estaba.

Uso, igual en local y en CI:

    python3 tools/calidad.py tipos
    python3 tools/calidad.py lint
    python3 tools/calidad.py io
    python3 tools/calidad.py tamano --base origin/main
    python3 tools/calidad.py tamano --base <commit> --cabeza <commit>

`tipos` y `lint` comparan contra `.github/calidad/baseline.json` y fallan solo
si el cambio agrega un error. `io` es estricto: cualquier uso fuera de
`motor/io.py` falla salvo los archivos exentos por una prohibición expresa.
`--actualizar` regenera `tipos` y `lint` (se hace desde la rama base, no desde el
PR que se está revisando).

`tamano` mide `motor/` contra la rama base, leída de git: un archivo de más de
1,000 líneas o una función de más de 100 no puede medir más que en la base, ni en
líneas ni en tokens de código, y lo que en la base no existe o cabe no puede
pasar del límite. No guarda nada. Sin `--cabeza` mide el árbol de trabajo; con
`--cabeza` mide ese commit, leído de git igual que la base, y el árbol no cuenta:
así juzga un PR quien no ejecuta nada suyo.
"""

from __future__ import annotations

import argparse
import ast
import collections
import io
import itertools
import json
import shutil
import subprocess
import sys
import tokenize
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
if str(RAIZ) not in sys.path:
    sys.path.insert(0, str(RAIZ))
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
    "_disputas_pendientes": 1,
    "_fase_de_resultado": 1,
    "_marcador_autoridad_final": 1,
    "_mirar_autoridad": 2,
    "_nuevo_cierre_tras_marcador": 1,
    "_observacion_de_la_version": 1,
    "_paso_entrega_paso": 1,
    "_recibir_resultado_nuevo": 1,
    "_resultado_desde_fold": 1,
    "_soltar_conductor": 1,
}
# Literales `.json` que son entradas del agente o del protocolo, no marcadores
# del motor. Un `.json` nuevo en `ciclo.py` no entra sin pasar por esta lista.
# Excepción declarada: `relectura-deepseek.json` es estado local del supervisor
# (el cursor de la relectura de cerrados); no decide nada del encargo y su
# pérdida solo cuesta una relectura, así que no es un hecho ni un marcador.
MARCADORES_JSON = {
    "arranque.json",
    "auth.json",
    "comprobaciones.json",
    "corregir id, operacion, clave y cuerpo en .interno/pedido-efecto.json",
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
    "relectura-deepseek.json",
    "resultado.json",
    "revision.json",
    "wrapper.json",
}
# Los cinco marcadores retirados y el `incorporado` sin extensión: no pueden
# volver a decidir fuera del registro. Solo la migración los nombra.
MARCADORES_RETIRADOS = {
    "incorporado",
    "quietud.json",
    "deepseek_lectura.json",
    "deepseek_cerrado.json",
    "ci-sondeo.json",
    "cierre.json",
}
MIGRACION = {"_migrar_un_marcador", "migrar_marcadores"}
# La inspección recorre archivos `.json` del espacio: es una lectura, no un
# marcador que decida.
JSON_EXCEPCIONES = {("_texto_inspeccion_resuelta", ".json")}
MENSAJE_MARCADORES = "anótalo como evento y derívalo en dominio"
LIMITES_DE_TAMANO = {"archivo": 1000, "funcion": 100}
# Lo que pasa del límite de líneas no puede superar a la base en ninguna de estas
# medidas. Las líneas solas dejan cambiar un comentario o una línea en blanco por
# código; los tokens no cambian al borrar un comentario y sí suben con una lista
# larga escrita en una sola línea.
MEDIDAS_DE_TAMANO = ("líneas", "tokens de código")
NO_ES_CODIGO = frozenset(
    {
        tokenize.COMMENT,
        tokenize.NL,
        tokenize.NEWLINE,
        tokenize.INDENT,
        tokenize.DEDENT,
        tokenize.ENDMARKER,
    }
)
MENSAJE_TAMANO = (
    "Saca código de ahí a un módulo propio: nada de motor/ pasa del límite ni "
    "crece por encima de lo que mide en la base."
)
# Modos de git que sacan código de la medida: el enlace apunta fuera y el
# submódulo vive en otro repo, pero Python importa los dos igual.
AJENOS_DE_MOTOR = {b"120000": "es un enlace simbólico", b"160000": "es un submódulo"}
MENSAJE_SIN_MEDIR = (
    "Bajo motor/ solo van archivos propios del repo, en UTF-8 y que Python analice; "
    "lo demás no se puede medir."
)


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
                cuenta[("json", pila[-1] if pila else "<module>", nodo.value)] += 1

        def visit_BinOp(self, nodo: ast.BinOp) -> None:
            if isinstance(nodo.op, ast.Div):
                for lado in (nodo.left, nodo.right):
                    if isinstance(lado, ast.Constant) and isinstance(lado.value, str):
                        if lado.value in MARCADORES_RETIRADOS:
                            cuenta[
                                ("path", pila[-1] if pila else "<module>", lado.value)
                            ] += 1
            self.generic_visit(nodo)

    Visita().visit(arbol)
    return cuenta


def marcadores_nuevos() -> collections.Counter:
    ruta = RAIZ / "motor" / "encargos" / "ciclo.py"
    try:
        arbol = ast.parse(ruta.read_text(encoding="utf-8"))
    except SyntaxError as exc:
        raise SystemExit(f"no se puede leer {ruta}: {exc}")
    nuevos: collections.Counter = collections.Counter()
    for clave, n in _recorridos_de_marcadores(arbol).items():
        tipo = clave[0]
        if tipo == "eventos":
            permitido = MARCADORES_EVENTOS.get(clave[1], 0)
            if n > permitido:
                nuevos[clave] = n - permitido
        elif tipo == "json":
            funcion, valor = clave[1], clave[2]
            if (funcion, valor) in JSON_EXCEPCIONES:
                continue
            if funcion in MIGRACION and valor in MARCADORES_RETIRADOS:
                continue
            if valor not in MARCADORES_JSON:
                nuevos[clave] = n
        elif tipo == "path":
            funcion, valor = clave[1], clave[2]
            if funcion not in MIGRACION:
                nuevos[clave] = n
    return nuevos


def _funciones(nodo: ast.AST, prefijo: str = ""):
    """Cada función o método con su nombre calificado dentro del archivo.

    El nombre (`Clase.metodo`, `externa.interna`) y no la línea es la identidad:
    mover la función dentro del archivo no cambia su tamaño en la base, y
    renombrarla o llevarla a otro archivo la vuelve nueva.
    """
    for hijo in ast.iter_child_nodes(nodo):
        if isinstance(hijo, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            nombre = prefijo + hijo.name
            if not isinstance(hijo, ast.ClassDef):
                yield nombre, hijo.lineno, hijo.end_lineno or hijo.lineno
            yield from _funciones(hijo, nombre + ".")
        else:
            yield from _funciones(hijo, prefijo)


def _acumulado_por_linea(texto: str) -> dict[str, list[int]]:
    """Por medida, lo que suman las líneas físicas desde la primera hasta cada una."""
    tokens = [0] * (len(texto.splitlines()) + 1)
    for token in tokenize.generate_tokens(io.StringIO(texto).readline):
        if token.type not in NO_ES_CODIGO:
            tokens[token.start[0]] += 1
    lineas = [0] + [1] * (len(tokens) - 1)
    return {
        medida: list(itertools.accumulate(por_linea))
        for medida, por_linea in zip(MEDIDAS_DE_TAMANO, (lineas, tokens), strict=True)
    }


# Lo leído de `motor/`: el contenido de cada `.py` y, con su motivo, lo que no
# se puede medir.
Lectura = tuple[list[tuple[str, bytes]], list[tuple[str, str]]]


def _medir(lectura: Lectura) -> tuple[dict[tuple, collections.Counter], list[tuple[str, str]]]:
    """Cada medida por `("archivo", ruta)` y `("funcion", ruta, nombre)`, y lo que no se mide."""
    fuentes, sin_medir = lectura
    sin_medir = list(sin_medir)
    cuenta: dict[tuple, collections.Counter] = collections.defaultdict(collections.Counter)
    for ruta, contenido in fuentes:
        try:
            texto = contenido.decode("utf-8")
        except UnicodeDecodeError as exc:
            sin_medir.append((ruta, f"no es UTF-8 (byte {exc.start}: {exc.reason})"))
            continue
        try:
            arbol = ast.parse(texto)
            acumulado = _acumulado_por_linea(texto)
        except (SyntaxError, tokenize.TokenError) as exc:
            sin_medir.append((ruta, f"no se puede analizar: {exc}"))
            continue
        cuenta[("archivo", ruta)].update(_tramo(acumulado, 1, len(acumulado["líneas"]) - 1))
        for nombre, inicio, fin in _funciones(arbol):
            # Dos definiciones con el mismo nombre suman: una segunda no puede
            # colarse bajo el tamaño de la primera.
            cuenta[("funcion", ruta, nombre)].update(_tramo(acumulado, inicio, fin))
    return cuenta, sin_medir


def _tramo(acumulado: dict[str, list[int]], inicio: int, fin: int) -> dict[str, int]:
    return {medida: suma[fin] - suma[inicio - 1] for medida, suma in acumulado.items()}


def _medir_lo_juzgado(lectura: Lectura, donde: str) -> dict[tuple, collections.Counter]:
    medidas, sin_medir = _medir(lectura)
    if sin_medir:
        raise SystemExit(
            "\n".join(f"tamano: {ruta}{donde} {motivo}" for ruta, motivo in sin_medir)
            + f"\n{MENSAJE_SIN_MEDIR}"
        )
    return medidas


def _medir_la_base(lectura: Lectura, ref: str) -> dict[tuple, collections.Counter]:
    """La base solo pone el techo: lo que no mide no tumba el check, que con su
    propio `calidad.py` no podría arreglarlo ningún PR."""
    medidas, sin_medir = _medir(lectura)
    for ruta, motivo in sin_medir:
        print(
            f"tamano: {ruta} en {ref} {motivo}; no se mide ahí y se juzga como si no existiera",
            file=sys.stderr,
        )
    return medidas


def _fuentes_del_arbol() -> Lectura:
    motor = RAIZ / "motor"
    caminos = sorted([motor, *motor.rglob("*")])
    # El submódulo sale del índice: sin iniciarlo, como lo deja el checkout del
    # CI, en el disco es una carpeta vacía.
    submodulos = [
        (entrada.split(b"\t", 1)[1].decode(), AJENOS_DE_MOTOR[b"160000"])
        for entrada in _git("ls-files", "--stage", "-z", "--", "motor").split(b"\0")
        if entrada.startswith(b"160000 ")
    ]
    enlaces = [
        (camino.relative_to(RAIZ).as_posix(), AJENOS_DE_MOTOR[b"120000"])
        for camino in caminos
        if camino.is_symlink()
    ]
    fuentes = [
        (archivo.relative_to(RAIZ).as_posix(), archivo.read_bytes())
        for archivo in caminos
        if archivo.suffix == ".py" and archivo.is_file()
    ]
    return fuentes, sorted(enlaces + submodulos)


def _git(*args: str, entrada: bytes | None = None) -> bytes:
    return subprocess.run(
        ["git", *args], cwd=RAIZ, input=entrada, capture_output=True, check=True
    ).stdout


def _commit(ref: str, papel: str) -> str:
    try:
        return _git("rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}").decode().strip()
    except subprocess.CalledProcessError:
        raise SystemExit(
            f"tamano: la {papel} {ref} no existe en este clon; {_como_traer(ref)}"
        ) from None


def _como_traer(ref: str) -> str:
    if ref.startswith("origin/"):
        rama = ref.removeprefix("origin/")
        return f"tráela con `git fetch origin +refs/heads/{rama}:refs/remotes/{ref}`"
    if _git("rev-parse", "--is-shallow-repository").strip() == b"true":
        return "el clon es superficial; trae la historia con `git fetch --unshallow origin`"
    return "no es una rama de origin ni está en la historia del clon; revisa el nombre"


def _fuentes_de_git(commit: str) -> Lectura:
    """Los `.py` de `motor/` tal como están en `commit`, sin tocar el árbol."""
    blobs, ajenos = [], []
    for entrada in _git("ls-tree", "-r", "-z", commit, "--", "motor").split(b"\0"):
        if not entrada:
            continue
        meta, ruta = entrada.split(b"\t", 1)
        modo, tipo, sha = meta.split()
        if modo in AJENOS_DE_MOTOR:
            ajenos.append((ruta.decode(), AJENOS_DE_MOTOR[modo]))
        elif tipo == b"blob" and ruta.endswith(b".py"):
            blobs.append((ruta.decode(), sha))
    lote = _git("cat-file", "--batch", entrada=b"".join(sha + b"\n" for _, sha in blobs))
    fuentes, pos = [], 0
    for ruta, _ in blobs:
        fin_cabecera = lote.index(b"\n", pos)
        tamano = int(lote[pos:fin_cabecera].split()[2])
        inicio = fin_cabecera + 1
        fuentes.append((ruta, lote[inicio : inicio + tamano]))
        pos = inicio + tamano + 1
    return fuentes, ajenos


def _nombre_de_tamano(clave: tuple) -> str:
    return clave[1] if clave[0] == "archivo" else f"{clave[1]}::{clave[2]}"


def _excesos_de_tamano(
    hoy: dict[tuple, collections.Counter], base: dict[tuple, collections.Counter], ref: str
) -> list[str]:
    excesos = []
    for clave, medidas in sorted(hoy.items()):
        limite = LIMITES_DE_TAMANO[clave[0]]
        lineas = medidas["líneas"]
        if lineas <= limite:
            continue
        nombre = _nombre_de_tamano(clave)
        tipo = "archivo" if clave[0] == "archivo" else "función"
        antes = base.get(clave)
        if antes is None or antes["líneas"] <= limite:
            en_base = (
                f"no existe en {ref}" if antes is None else f"en {ref} mide {antes['líneas']}"
            )
            excesos.append(
                f"  {nombre}: {tipo} de {lineas} líneas; {en_base} y el límite es {limite}"
            )
            continue
        excesos.extend(
            f"  {nombre}: {tipo} de {medidas[medida]} {medida}; en {ref} mide {antes[medida]}"
            for medida in MEDIDAS_DE_TAMANO
            if medidas[medida] > antes[medida]
        )
    return excesos


def _comparar_tamano(ref: str, cabeza: str | None) -> int:
    commit = _commit(ref, "base")
    if cabeza is None:
        medido, lectura = "", _fuentes_del_arbol()
    else:
        medido, lectura = f" en {cabeza}", _fuentes_de_git(_commit(cabeza, "cabeza"))
    hoy = _medir_lo_juzgado(lectura, medido)
    excesos = _excesos_de_tamano(hoy, _medir_la_base(_fuentes_de_git(commit), ref), ref)
    if excesos:
        print(
            f"tamano: {len(excesos)} límite(s) superado(s){medido} contra {ref}", file=sys.stderr
        )
        print("\n".join(excesos), file=sys.stderr)
        print(MENSAJE_TAMANO, file=sys.stderr)
        return 1
    grandes = collections.Counter(
        clave[0] for clave, medidas in hoy.items()
        if medidas["líneas"] > LIMITES_DE_TAMANO[clave[0]]
    )
    print(
        f"tamano: nada crece{medido} contra {ref} ({commit[:12]}). Pasan del límite hoy "
        f"{grandes['archivo']} archivo(s) de más de {LIMITES_DE_TAMANO['archivo']} "
        f"líneas y {grandes['funcion']} función(es) de más de "
        f"{LIMITES_DE_TAMANO['funcion']}; en ellos:"
    )
    for medida in MEDIDAS_DE_TAMANO:
        print(f"  {medida}: ninguno supera lo que mide en {ref}")
    return 0


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
    for clave, n in sorted(nuevos.items()):
        tipo = clave[0]
        detalle = " ".join(str(x) for x in clave[1:])
        for _ in range(n):
            print(
                f"  motor/encargos/ciclo.py: {tipo} {detalle}; {MENSAJE_MARCADORES}",
                file=sys.stderr,
            )
    return 1


def politica_desactualizada() -> str:
    """La tabla de AGENTS.md se genera desde `motor.encargos.politica`."""
    from motor.encargos.politica import tabla_de_agents, tabla_markdown

    ruta = RAIZ / "AGENTS.md"
    actual = tabla_de_agents(ruta.read_text(encoding="utf-8"))
    esperado = tabla_markdown()
    if actual == esperado:
        return ""
    return (
        "AGENTS.md: la tabla de decisiones no coincide con "
        "motor/encargos/politica.py; regenérala con `python3 tools/calidad.py "
        "politica --actualizar`"
    )


def _regenerar_politica() -> None:
    from motor.encargos.politica import tabla_de_agents, tabla_markdown

    ruta = RAIZ / "AGENTS.md"
    texto = ruta.read_text(encoding="utf-8")
    actual = tabla_de_agents(texto)
    if not actual:
        raise SystemExit("AGENTS.md no tiene la tabla de decisiones")
    nuevo = texto.replace(actual, tabla_markdown())
    ruta.write_text(nuevo, encoding="utf-8")
    (RAIZ / "CLAUDE.md").write_text(nuevo, encoding="utf-8")


def _actualizar(linea_base: dict) -> None:
    linea_base["tipos"] = _lista(diagnosticos_tipos())
    linea_base["lint"] = _lista(diagnosticos_lint())
    linea_base.pop("io", None)
    BASE.parent.mkdir(parents=True, exist_ok=True)
    BASE.write_text(json.dumps(linea_base, ensure_ascii=False, indent=1) + "\n")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="calidad")
    ap.add_argument(
        "modo", choices=("tipos", "lint", "io", "marcadores", "politica", "tamano")
    )
    ap.add_argument("--actualizar", action="store_true")
    ap.add_argument(
        "--base", default="origin/main", help="solo tamano: la referencia de git a comparar"
    )
    ap.add_argument(
        "--cabeza", help="solo tamano: el commit a medir en vez del árbol de trabajo"
    )
    args = ap.parse_args(argv)
    if args.modo == "tamano":
        if args.actualizar:
            ap.error("tamano no guarda nada que actualizar: compara contra --base")
        return _comparar_tamano(args.base, args.cabeza)
    if args.modo == "politica":
        if args.actualizar:
            _regenerar_politica()
            return 0
        defecto = politica_desactualizada()
        if defecto:
            print(defecto, file=sys.stderr)
            return 1
        print("politica: la tabla de AGENTS.md se genera desde politica.py")
        return 0
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
