"""Corpus local de registros de las pruebas para comparar el plegado completo."""

from __future__ import annotations

import argparse
import ast
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
from dataclasses import asdict
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
FALLO_BASE = ("tests/test_rama_fase.py::UnFalloDeGitRepetidoPideAyuda"
              "::test_un_ajuste_del_propietario_vuelve_a_intentarlo")
MIN_ENCARGOS_REALES = 900
MIN_EVENTOS_REALES = 10000
if str(RAIZ) not in sys.path:
    sys.path.insert(0, str(RAIZ))


def _fallos_pytest(codigo: int, salida: str) -> list[str]:
    fallos = re.findall(r"^FAILED (\S+)", salida, re.MULTILINE)
    errores = re.findall(r"^ERROR (\S+)", salida, re.MULTILINE)
    if codigo == 0 and not fallos and not errores:
        return []
    if codigo == 1 and fallos == [FALLO_BASE] and not errores:
        return fallos
    raise RuntimeError(f"pytest no produjo el corpus esperado: salida {codigo}, "
                       f"fallos {fallos}, errores {errores}")


def _bateria_con_reintento(
    proceso: subprocess.CompletedProcess[str], arbol: Path, entorno: dict[str, str],
) -> tuple[list[str], list[str]]:
    salida = proceso.stdout + proceso.stderr
    fallidos = re.findall(r"^FAILED (\S+)", salida, re.MULTILINE)
    errores = re.findall(r"^ERROR (\S+)", salida, re.MULTILINE)
    extras = list(dict.fromkeys(fallo for fallo in fallidos if fallo != FALLO_BASE))
    if not extras or proceso.returncode != 1 or errores:
        return _fallos_pytest(proceso.returncode, salida), []
    entorno_reintento = {k: v for k, v in entorno.items()
                         if k not in {"MOTOR_CORPUS_DIR", "MOTOR_CORPUS_REPO"}}
    reintento = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", *extras],
        cwd=arbol, env=entorno_reintento, capture_output=True, text=True, check=False,
    )
    if reintento.returncode:
        raise RuntimeError(f"pytest sigue fallando tras reintentar {extras}: "
                           f"salida {reintento.returncode}")
    _fallos_pytest(reintento.returncode, reintento.stdout + reintento.stderr)
    return ([FALLO_BASE] if FALLO_BASE in fallidos else []), extras


def _comprobar_cobertura(encargos: int, eventos: int) -> None:
    if encargos < MIN_ENCARGOS_REALES or eventos < MIN_EVENTOS_REALES:
        raise RuntimeError(f"corpus insuficiente antes de sembrar: {encargos} encargos y "
                           f"{eventos} eventos; mínimo {MIN_ENCARGOS_REALES} encargos y "
                           f"{MIN_EVENTOS_REALES} eventos")


def _json(ruta: Path, valor: object) -> None:
    with os.fdopen(os.open(ruta, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600),
                   "w", encoding="utf-8") as archivo:
        json.dump(valor, archivo, ensure_ascii=False, sort_keys=True)


def _kinds(raiz: Path) -> set[str]:
    with (raiz / "motor" / "encargos" / "dominio.py").open(encoding="utf-8") as archivo:
        arbol = ast.parse(archivo.read())
    funcion = next(n for n in arbol.body if isinstance(n, ast.FunctionDef)
                   and n.name == "_reducir_evento")
    encontrados: set[str] = set()
    for nodo in ast.walk(funcion):
        if not isinstance(nodo, ast.Compare) or not isinstance(nodo.left, ast.Name):
            continue
        if nodo.left.id != "kind" or len(nodo.comparators) != 1:
            continue
        valor = nodo.comparators[0]
        if isinstance(valor, ast.Constant) and isinstance(valor.value, str):
            encontrados.add(valor.value)
        elif isinstance(valor, (ast.Tuple, ast.Set)):
            encontrados.update(x.value for x in valor.elts if isinstance(x, ast.Constant)
                                and isinstance(x.value, str))
        elif isinstance(valor, ast.Name) and valor.id == "DETENCIONES":
            with (raiz / "motor" / "encargos" / "detencion.py").open(encoding="utf-8") as archivo:
                detenciones = ast.parse(archivo.read())
            tabla = next(n.value for n in detenciones.body if isinstance(n, ast.AnnAssign)
                         and isinstance(n.target, ast.Name) and n.target.id == "DETENCIONES")
            encontrados.update(k.value for k in tabla.keys if isinstance(k, ast.Constant))
    if (raiz / "motor" / "encargos" / "reductor").is_dir():
        with (raiz / "motor" / "encargos" / "reductor" / "__init__.py").open(
            encoding="utf-8"
        ) as archivo:
            filas = ast.parse(archivo.read())
        tabla = next(
            n.value for n in filas.body
            if isinstance(n, ast.AnnAssign) and isinstance(n.target, ast.Name)
            and n.target.id == "TABLA"
            or isinstance(n, ast.Assign) and any(
                isinstance(t, ast.Name) and t.id == "TABLA" for t in n.targets
            )
        )
        if not isinstance(tabla, ast.Dict) or any(
            not isinstance(k, ast.Constant) or not isinstance(k.value, str)
            for k in tabla.keys
        ):
            raise ValueError("la tabla del reductor necesita claves literales")
        encontrados.update(k.value for k in tabla.keys)
    return encontrados


def _registros(salida: Path) -> list[Path]:
    return sorted((salida / "registros").glob("*.sqlite"))


def _limpiar_worktrees() -> None:
    listado = subprocess.run(
        ["git", "-C", str(RAIZ), "worktree", "list", "--porcelain"],
        check=True, capture_output=True, text=True,
    )
    for bloque in listado.stdout.split("\n\n"):
        lineas = bloque.splitlines()
        if not lineas or not lineas[0].startswith("worktree "):
            continue
        arbol = Path(lineas[0][9:])
        if (arbol.name == "arbol" and arbol.parent.name.startswith("corpus-ref-")
            and not arbol.exists()
            and any(linea.startswith("prunable ") for linea in lineas)):
            subprocess.run(
                ["git", "-C", str(RAIZ), "worktree", "remove", "--force", str(arbol)],
                check=True, capture_output=True,
            )


def _referencia(
    salida: Path, raiz: Path, ref: str, descartados_previos: int = 0
) -> tuple[int, int, int, set[str]]:
    from tools.plegado import cargar_reductor, campos_de, leer_encargos, trazar, ultimo_evento

    reductor = cargar_reductor(raiz)
    (salida / "estado-base").mkdir(mode=0o700, exist_ok=True)
    encargos, descartados, eventos = 0, descartados_previos, 0
    previos = []
    if descartados_previos:
        with (salida / "meta.json").open(encoding="utf-8") as archivo:
            previos = json.load(archivo)["descartados_detalle"]
    presentes: set[str] = set()
    for archivo in _registros(salida):
        estados = {}
        for encargo, secuencia in leer_encargos(archivo, ultimo_evento(archivo)):
            presente = {e["kind"] for e in secuencia}
            traza = trazar(reductor, secuencia)
            if traza.fallo is not None:
                descartados += 1
                previos.append({
                    "archivo": archivo.name, "encargo": encargo,
                    "eventos": secuencia, "fallo": asdict(traza.fallo),
                })
                continue
            estados[encargo] = campos_de(traza.estados[-1])
            presentes.update(presente)
            encargos += 1
            eventos += len(secuencia)
        _json(salida / "estado-base" / f"{archivo.stem}.json", estados)
    rechazos = sum(1 for _ in (salida / "rechazos.jsonl").open(encoding="utf-8"))
    _json(salida / "meta.json", {
        "ref": ref, "archivos": len(_registros(salida)), "encargos": encargos,
        "descartados": descartados, "eventos": eventos, "rechazos": rechazos,
        "kinds": sorted(presentes), "descartados_detalle": previos,
    })
    return encargos, descartados, eventos, presentes


def _descartar(salida: Path) -> None:
    rechazos = salida / "rechazos.jsonl"
    temporal = salida / "rechazos.tmp"
    with rechazos.open(encoding="utf-8") as entrada, os.fdopen(
        os.open(temporal, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600),
        "w", encoding="utf-8",
    ) as salida_rechazos:
        for linea in entrada:
            intento = json.loads(linea)
            with sqlite3.connect(salida / "registros" / intento["archivo"]) as db:
                intento["prefijo"] = db.execute(
                    "SELECT id, ts, encargo, kind, data, ukey FROM events"
                    " ORDER BY id LIMIT ?", (intento["conteo"],),
                ).fetchall()
            salida_rechazos.write(json.dumps(intento, ensure_ascii=False) + "\n")
    temporal.replace(rechazos)
    for registro in _registros(salida):
        with (salida / "estado-base" / f"{registro.stem}.json").open(encoding="utf-8") as archivo:
            validos = json.load(archivo)
        with sqlite3.connect(registro) as db:
            invalidos = [
                encargo for (encargo,) in db.execute("SELECT DISTINCT encargo FROM events")
                if encargo not in validos
            ]
            db.executemany("DELETE FROM events WHERE encargo = ?", ((e,) for e in invalidos))


def _sembrar(salida: Path, raiz: Path, faltan: set[str]) -> None:
    sys.path.insert(0, str(raiz))
    from motor.encargos.registro import Evento, Registro

    archivo = salida / "registros" / "semilla.sqlite"
    with tempfile.TemporaryDirectory(prefix="corpus-semilla-") as temporal:
        reg = Registro(Path(temporal))
        for n, kind in enumerate(sorted(faltan)):
            encargo = f"semilla_{n}"
            reg.append_many(encargo, [Evento("entrega", {
                "id": encargo, "clave": encargo, "huella": "h", "texto": "semilla",
            }, f"entrega:{encargo}")])
            datos: dict[str, object] = {}
            if kind == "contexto_entrega":
                datos = {"base": "main"}
            elif kind == "proveedor_disponible":
                datos = {"agente": "responsable"}
            elif kind == "colgada":
                datos = {"hecho": "sesión colgada"}
            elif kind == "afirmacion":
                datos = {"texto": "confirmado"}
            elif kind == "autorizacion":
                datos = {"recurso": "repo", "operacion": "leer"}
            elif kind == "entrada_incorporada":
                datos = {"dep": "otra", "version": "v1"}
            elif kind == "fase_reactivada":
                reg.append_many(encargo, [
                    Evento("fases", {"fases": [
                        {"id": "X", "resultado": "base"},
                        {"id": "F", "resultado": "primera"},
                        {"id": "G", "resultado": "segunda", "requiere": ["X"]},
                    ]}, f"fases:{encargo}"),
                    Evento("conciliacion_fase", {"fase": "X", "paso": "preparada"},
                           f"preparada:{encargo}"),
                    Evento("bloqueo_fase", {"evidencia": "pendiente"}, f"bloqueo:{encargo}"),
                ])
                datos = {"fase": "F"}
            reg.append_many(encargo, [Evento(kind, datos, f"{kind}:{encargo}")])
        reg.close()
        with sqlite3.connect(archivo) as destino:
            destino.execute(
                "CREATE TABLE events (id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT NOT NULL,"
                " encargo TEXT NOT NULL, kind TEXT NOT NULL, data TEXT NOT NULL, ukey TEXT UNIQUE)"
            )
            with sqlite3.connect(Path(temporal) / "registro.sqlite") as origen:
                destino.executemany(
                    "INSERT INTO events (id, ts, encargo, kind, data, ukey) VALUES (?,?,?,?,?,?)",
                    origen.execute("SELECT id, ts, encargo, kind, data, ukey FROM events ORDER BY id"),
                )


def generar(ref: str, salida: Path) -> int:
    if salida.exists() and any(salida.iterdir()):
        raise ValueError(f"el destino no está vacío: {salida}")
    _limpiar_worktrees()
    salida.mkdir(parents=True, exist_ok=True, mode=0o700)
    if salida.stat().st_mode & 0o077:
        raise ValueError(f"el destino debe ser privado: {salida}")
    (salida / "registros").mkdir(mode=0o700)
    with os.fdopen(os.open(salida / "rechazos.jsonl", os.O_WRONLY | os.O_CREAT, 0o600)):
        pass
    sha = subprocess.check_output(["git", "-C", str(RAIZ), "rev-parse", ref], text=True).strip()
    with tempfile.TemporaryDirectory(prefix="corpus-ref-") as temporal:
        arbol = Path(temporal) / "arbol"
        subprocess.run(["git", "-C", str(RAIZ), "worktree", "add", "--detach", str(arbol), sha],
                       check=True, capture_output=True)
        try:
            shutil.copy2(RAIZ / "tools" / "corpus_pytest.py", arbol / "tools" / "corpus_pytest.py")
            shutil.copy2(RAIZ / "tools" / "corpus_sitecustomize.py", arbol / "sitecustomize.py")
            entorno_pytest = {
                **os.environ, "TMPDIR": os.path.realpath(tempfile.gettempdir()),
                "MOTOR_CORPUS_DIR": str(salida.resolve()),
                "MOTOR_CORPUS_REPO": str(arbol.resolve()),
            }
            proceso = subprocess.run(
                [sys.executable, "-m", "pytest", "-q", "-n", "8", "-p",
                 "tools.corpus_pytest", "tests"],
                cwd=arbol, env=entorno_pytest,
                capture_output=True, text=True, check=False,
            )
            with os.fdopen(os.open(salida / "pytest-base.log", os.O_WRONLY | os.O_CREAT, 0o600),
                           "w", encoding="utf-8") as archivo:
                archivo.write(proceso.stdout)
                archivo.write(proceso.stderr)
            fallos, reintentados = _bateria_con_reintento(proceso, arbol, entorno_pytest)
            if fallos:
                print(f"La batería en la base falló solo en {fallos[0]}.",
                      file=sys.stderr)
            encargos_reales, descartados, eventos_reales, presentes = _referencia(
                salida, arbol, ref,
            )
            _comprobar_cobertura(encargos_reales, eventos_reales)
            _descartar(salida)
            faltan = _kinds(arbol) - presentes
            if faltan:
                semilla = subprocess.run(
                    [sys.executable, str(RAIZ / "tools" / "corpus_plegado.py"), "_sembrar",
                     "--salida", str(salida), "--raiz", str(arbol), *sorted(faltan)],
                    cwd=arbol, capture_output=True, text=True, check=False,
                )
                if semilla.returncode:
                    raise RuntimeError(f"no se pudo sembrar {sorted(faltan)}: {semilla.stderr[-1200:]}")
            encargos, descartados, eventos, presentes = _referencia(
                salida, arbol, ref, descartados,
            )
            total = _kinds(arbol)
            if presentes != total:
                raise RuntimeError(f"sin cubrir: {sorted(total - presentes)}; "
                                   f"sobran: {sorted(presentes - total)}")
            with (salida / "meta.json").open(encoding="utf-8") as archivo:
                meta = json.load(archivo)
            _json(salida / "meta.json", {
                **meta, "sha": sha, "pytest_exit": int(bool(fallos)),
                "pytest_fallos": fallos, "pytest_reintentos": reintentados,
            })
            rechazos = meta["rechazos"]
            print(f"{encargos} encargos ({descartados} descartados), {eventos} eventos, "
                  f"{rechazos} lotes rechazados, {len(presentes)} de {len(total)} kinds")
        finally:
            subprocess.run(["git", "-C", str(RAIZ), "worktree", "remove", "--force", str(arbol)],
                           check=True, capture_output=True)
    return 0


def verificar(salida: Path, raiz: Path) -> int:
    from tools.plegado import _igual, cargar_reductor, campos_de, leer_encargos, trazar, ultimo_evento

    reductor = cargar_reductor(raiz)
    with (salida / "meta.json").open(encoding="utf-8") as archivo:
        meta = json.load(archivo)
    faltan = {"pytest_exit", "pytest_fallos", "archivos", "encargos",
              "eventos", "rechazos", "descartados", "descartados_detalle"} - meta.keys()
    if faltan:
        print(f"faltan metadatos: {', '.join(sorted(faltan))}")
        return 1
    if (meta["pytest_exit"] not in (0, 1)
        or meta["pytest_exit"] == 1 and meta["pytest_fallos"] != [FALLO_BASE]
        or meta["pytest_exit"] == 0 and meta["pytest_fallos"]):
        print("el corpus proviene de una batería con fallos no aceptados")
        return 1
    descartes_cambiados = 0
    for descarte in meta["descartados_detalle"]:
        traza = trazar(reductor, descarte["eventos"])
        if traza.fallo is None or asdict(traza.fallo) != descarte["fallo"]:
            descartes_cambiados += 1
            print(f"{descarte['archivo']}/{descarte['encargo']}: cambió el descarte")
    iguales = total = aceptados = sin_referencia = 0
    registros = _registros(salida)
    for registro in registros:
        with (salida / "estado-base" / f"{registro.stem}.json").open(encoding="utf-8") as archivo:
            esperados = json.load(archivo)
        presentes = {}
        for encargo, secuencia in leer_encargos(registro, ultimo_evento(registro)):
            if encargo not in esperados:
                sin_referencia += 1
                print(f"{registro.name}/{encargo}: sin referencia")
                continue
            total += 1
            traza = trazar(reductor, secuencia)
            aceptados += len(traza.estados)
            if traza.fallo is not None:
                print(f"{registro.name}/{encargo}: {traza.fallo.tipo} no pliega")
                continue
            presentes[encargo] = campos_de(traza.estados[-1])
            if _igual(presentes[encargo], esperados[encargo]):
                iguales += 1
            else:
                distintos = sorted(
                    k for k in presentes[encargo].keys() | esperados[encargo].keys()
                    if k not in presentes[encargo] or k not in esperados[encargo]
                    or not _igual(presentes[encargo][k], esperados[encargo][k])
                )
                print(f"{registro.name}/{encargo}: difieren {', '.join(distintos)}")
    rechazos = rechazos_cambiados = 0
    from motor.encargos.registro import ClaveOcupada, Evento, EventoIlegal, Registro
    with (salida / "rechazos.jsonl").open(encoding="utf-8") as archivo:
        for linea in archivo:
            intento = json.loads(linea)
            esperado = ("tipo", "mensaje", "tipo_origen", "mensaje_origen")
            if any(campo not in intento for campo in esperado):
                rechazos_cambiados += 1
                print(f"{intento['archivo']}: cambió el rechazo: faltan tipo o mensaje")
                continue
            origen = salida / "registros" / intento["archivo"]
            with tempfile.TemporaryDirectory(prefix="corpus-rechazo-") as temporal:
                registro = Registro(Path(temporal))
                if "prefijo" in intento:
                    filas = intento["prefijo"]
                else:
                    with sqlite3.connect(f"{origen.resolve().as_uri()}?mode=ro", uri=True) as db:
                        filas = db.execute(
                            "SELECT id, ts, encargo, kind, data, ukey FROM events"
                            " ORDER BY id LIMIT ?", (intento["conteo"],),
                        ).fetchall()
                registro.db.executemany(
                    "INSERT INTO events (id, ts, encargo, kind, data, ukey) VALUES (?,?,?,?,?,?)",
                    filas,
                )
                antes = registro.db.execute("SELECT COUNT(*) FROM events").fetchone()[0]
                lote = [(Evento(e["kind"], e["data"], e["ukey"]), e["ts"])
                        for e in intento["lote"]]
                try:
                    registro._escribir(intento["encargo"], lote)
                except Exception as exc:
                    origen_error = exc.__cause__ or exc
                    valido = (isinstance(exc, (EventoIlegal, ClaveOcupada))
                              and type(exc).__name__ == intento["tipo"]
                              and exc.indice == intento["indice"]
                              and str(exc) == intento["mensaje"]
                              and type(origen_error).__name__ == intento["tipo_origen"]
                              and str(origen_error) == intento["mensaje_origen"])
                else:
                    valido = False
                despues = registro.db.execute("SELECT COUNT(*) FROM events").fetchone()[0]
                registro.close()
                if valido and antes == despues == intento["conteo"]:
                    rechazos += 1
                else:
                    rechazos_cambiados += 1
                    print(f"{intento['archivo']}: cambió el rechazo {intento['tipo']}")
    print(f"acepta {aceptados} de {meta['eventos']}; "
          f"rechaza {rechazos} de {meta['rechazos']}; "
          f"estado igual en {iguales} de {meta['encargos']}")
    return int(iguales != total or sin_referencia != 0
               or descartes_cambiados != 0
               or len(meta["descartados_detalle"]) != meta["descartados"]
               or len(registros) != meta["archivos"]
               or total != meta["encargos"]
               or aceptados != meta["eventos"]
               or rechazos_cambiados != 0 or rechazos != meta["rechazos"])


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    comandos = parser.add_subparsers(dest="comando", required=True)
    generar_args = comandos.add_parser("generar")
    generar_args.add_argument("--ref", required=True)
    generar_args.add_argument("--salida", required=True, type=Path)
    verificar_args = comandos.add_parser("verificar")
    verificar_args.add_argument("corpus", type=Path)
    verificar_args.add_argument("--raiz", default=RAIZ, type=Path)
    verificar_args.add_argument("--cobertura", action="store_true")
    semilla_args = comandos.add_parser("_sembrar")
    semilla_args.add_argument("--salida", required=True, type=Path)
    semilla_args.add_argument("--raiz", required=True, type=Path)
    semilla_args.add_argument("faltan", nargs="*")
    args = parser.parse_args()
    if args.comando == "generar":
        return generar(args.ref, args.salida.resolve())
    if args.comando == "_sembrar":
        _sembrar(args.salida, args.raiz, set(args.faltan))
        return 0
    if args.cobertura:
        from tools.cobertura_reductor import medir

        return medir(args.raiz, lambda: verificar(args.corpus, args.raiz))
    return verificar(args.corpus, args.raiz)


if __name__ == "__main__":
    raise SystemExit(main())
