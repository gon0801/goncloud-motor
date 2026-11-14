"""Corpus local de registros de las pruebas para comparar el plegado completo."""

from __future__ import annotations

import argparse
import ast
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
if str(RAIZ) not in sys.path:
    sys.path.insert(0, str(RAIZ))


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
        from motor.encargos.reductor import TABLA

        encontrados.update(TABLA)
    return encontrados


def _registros(salida: Path) -> list[Path]:
    return sorted((salida / "registros").glob("*.sqlite"))


def _referencia(
    salida: Path, raiz: Path, ref: str, descartados_previos: int = 0
) -> tuple[int, int, int, set[str]]:
    from tools.plegado import cargar_reductor, campos_de, leer_encargos, trazar, ultimo_evento

    reductor = cargar_reductor(raiz)
    (salida / "estado-base").mkdir(mode=0o700, exist_ok=True)
    encargos, descartados, eventos = 0, descartados_previos, 0
    presentes: set[str] = set()
    for archivo in _registros(salida):
        estados = {}
        for encargo, secuencia in leer_encargos(archivo, ultimo_evento(archivo)):
            presente = {e["kind"] for e in secuencia}
            traza = trazar(reductor, secuencia)
            if traza.fallo is not None:
                descartados += 1
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
        "kinds": sorted(presentes),
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
            proceso = subprocess.run(
                [sys.executable, "-m", "pytest", "-q", "-n", "8", "-p",
                 "tools.corpus_pytest", "tests"],
                cwd=arbol, env={
                    **os.environ, "MOTOR_CORPUS_DIR": str(salida.resolve()),
                    "MOTOR_CORPUS_REPO": str(arbol.resolve()),
                    "PYTHONPATH": str(arbol) + os.pathsep + os.environ.get("PYTHONPATH", ""),
                },
                capture_output=True, text=True, check=False,
            )
            with os.fdopen(os.open(salida / "pytest-base.log", os.O_WRONLY | os.O_CREAT, 0o600),
                           "w", encoding="utf-8") as archivo:
                archivo.write(proceso.stdout)
                archivo.write(proceso.stderr)
            if proceso.returncode not in (0, 1):
                raise RuntimeError(f"pytest no produjo un corpus: {proceso.stderr[-1200:]}")
            if proceso.returncode:
                print("La batería en la base reportó fallas; el corpus incluye lo que sí escribió.",
                      file=sys.stderr)
            _, descartados, _, presentes = _referencia(salida, arbol, ref)
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
                raise RuntimeError(f"sin cubrir: {sorted(total - presentes)}")
            with (salida / "meta.json").open(encoding="utf-8") as archivo:
                meta = json.load(archivo)
            _json(salida / "meta.json", {**meta, "sha": sha, "pytest_exit": proceso.returncode})
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
    iguales = total = aceptados = 0
    for registro in _registros(salida):
        with (salida / "estado-base" / f"{registro.stem}.json").open(encoding="utf-8") as archivo:
            esperados = json.load(archivo)
        presentes = {}
        for encargo, secuencia in leer_encargos(registro, ultimo_evento(registro)):
            if encargo not in esperados:
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
    rechazos = 0
    from motor.encargos.registro import ClaveOcupada, Evento, EventoIlegal, Registro
    with (salida / "rechazos.jsonl").open(encoding="utf-8") as archivo:
        for linea in archivo:
            intento = json.loads(linea)
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
                    valido = (isinstance(exc, (EventoIlegal, ClaveOcupada))
                              and type(exc).__name__ == intento["tipo"]
                              and exc.indice == intento["indice"])
                else:
                    valido = False
                despues = registro.db.execute("SELECT COUNT(*) FROM events").fetchone()[0]
                registro.close()
                if valido and antes == despues == intento["conteo"]:
                    rechazos += 1
                else:
                    print(f"{intento['archivo']}: cambió el rechazo {intento['tipo']}")
    print(f"acepta {aceptados} de {meta.get('eventos', aceptados)}; "
          f"rechaza {rechazos} de {meta.get('rechazos', rechazos)}; "
          f"estado igual en {iguales} de {total}")
    return int(iguales != total or aceptados != meta.get("eventos", aceptados)
               or rechazos != meta.get("rechazos", rechazos))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    comandos = parser.add_subparsers(dest="comando", required=True)
    generar_args = comandos.add_parser("generar")
    generar_args.add_argument("--ref", required=True)
    generar_args.add_argument("--salida", required=True, type=Path)
    verificar_args = comandos.add_parser("verificar")
    verificar_args.add_argument("corpus", type=Path)
    verificar_args.add_argument("--raiz", default=RAIZ, type=Path)
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
    return verificar(args.corpus, args.raiz)


if __name__ == "__main__":
    raise SystemExit(main())
