"""Compara escrituras nuevas sobre prefijos del corpus con dos reductores aislados.

Uso:
    python tools/escritura_diferencial.py --base origin/main --corpus DIR
        [--kinds comprobacion,solicitud] [--max-casos 100000]

El tope limita el trabajo local; la salida nunca incluye datos ni mensajes del
registro. Los resultados completos viven solo en archivos temporales privados.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import subprocess
import sys
import tarfile
import tempfile
from collections import Counter
from pathlib import Path
from typing import Any


RAIZ = Path(__file__).resolve().parents[1]
if str(RAIZ) not in sys.path:
    sys.path.insert(0, str(RAIZ))


def _muestras(corpus: Path, kinds: set[str]) -> dict[str, list[dict]]:
    from tools.plegado import leer_encargos, ultimo_evento

    muestras: dict[str, list[dict]] = {kind: [] for kind in kinds}
    formas: dict[str, set[tuple]] = {kind: set() for kind in kinds}
    for ruta in sorted((corpus / "registros").glob("*.sqlite")):
        for _, eventos in leer_encargos(ruta, ultimo_evento(ruta)):
            for evento in eventos:
                kind, data = evento["kind"], evento["data"]
                if kind not in muestras or not isinstance(data, dict):
                    continue
                forma = (tuple(sorted(data)), str(data.get("clase") or ""),
                         str(data.get("operacion") or ""))
                if len(muestras[kind]) < 2 and forma not in formas[kind]:
                    muestras[kind].append(data)
                    formas[kind].add(forma)
    return muestras


def _adaptar(datos: dict, estado: Any) -> dict:
    ajustados = dict(datos)
    if estado is None:
        return ajustados
    for campo, valor in (("fase", estado.fase_activa), ("intento", estado.intento)):
        if campo in ajustados:
            ajustados[campo] = valor
    if "ajustes_vistos" in ajustados:
        ajustados["ajustes_vistos"] = len(estado.ajustes)
    return ajustados


def _variantes(kind: str, datos: dict, estado: Any):
    yield "real", datos
    yield "vacio", {}
    for campo, valor in sorted(datos.items()):
        yield f"falta:{campo}", {k: v for k, v in datos.items() if k != campo}
        yield f"vacio:{campo}", {**datos, campo: ""}
        malo = {} if not isinstance(valor, dict) else 17
        yield f"tipo:{campo}", {**datos, campo: malo}
    for campo, valor in (("fase", "otra_fase"), ("intento", "otro_intento")):
        yield f"{campo}:ajeno", {**datos, campo: valor}
    for campo in ("solicitudes", "efectos"):
        existentes = getattr(estado, campo, ()) if estado is not None else ()
        if existentes:
            yield "id:repetido", {**datos, "id": existentes[0].id}
            break
    if kind == "comprobacion" or "ajustes_vistos" in datos:
        n = len(estado.ajustes)
        for nombre, valor in (("menos", n - 1), ("mas", n + 1), ("texto", "no numerico")):
            yield f"ajustes_vistos:{nombre}", {**datos, "ajustes_vistos": valor}


def _escribir_caso(reductor: Any, estado: Any, kind: str, variante: str,
                  carga: dict, legado: bool) -> None:
    from tools.plegado import campos_de

    clave = hashlib.sha256(
        json.dumps(carga, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()
    try:
        nuevo = reductor.reducir(estado, {"kind": kind, "data": carga}, nuevo=True)
        valor = campos_de(nuevo) if legado else nuevo
        salida = ["acepta", repr(valor)]
    except Exception as exc:
        salida = ["rechaza", type(exc).__name__, str(exc)]
    print(json.dumps([kind, variante, clave, *salida], ensure_ascii=False))


def _evaluar(corpus: Path, raiz: Path, kinds: list[str], max_casos: int, paso: int,
             legado: bool) -> int:
    from tools.plegado import cargar_reductor, leer_encargos, ultimo_evento

    reductor = cargar_reductor(raiz)
    muestras = _muestras(corpus, set(kinds))
    if faltan := sorted(k for k, datos in muestras.items() if not datos):
        raise ValueError(f"kinds sin cargas reales: {', '.join(faltan)}")
    cuenta = 0
    prefijo = 0
    if "entrega" in muestras:
        for muestra in muestras["entrega"]:
            for variante, carga in _variantes("entrega", muestra, None):
                _escribir_caso(reductor, None, "entrega", variante, carga, legado)
                cuenta += 1
                if cuenta >= max_casos:
                    return 0
    for ruta in sorted((corpus / "registros").glob("*.sqlite")):
        for _, eventos in leer_encargos(ruta, ultimo_evento(ruta)):
            estado = None
            for evento in eventos:
                estado = reductor.aplicar(estado, evento)
                prefijo += 1
                if estado is None or (prefijo - 1) % paso:
                    continue
                for kind in kinds:
                    for muestra in muestras[kind]:
                        datos = _adaptar(muestra, estado)
                        for variante, carga in _variantes(kind, datos, estado):
                            _escribir_caso(reductor, estado, kind, variante, carga, legado)
                            cuenta += 1
                            if cuenta >= max_casos:
                                return 0
    return 0


def _clase(base: list, arbol: list, kind: str, permitidos: set[str]) -> str:
    if base[:3] != arbol[:3]:
        return "carga_diferente"
    if base[3] == "acepta":
        if arbol[3] != "acepta":
            return "base_acepta_arbol_rechaza"
        return "estado" if base[4] != arbol[4] else ""
    if arbol[3] == "acepta":
        return ""
    if base[4] == "TransicionIlegal":
        if arbol[4] != base[4]:
            return "tipo_transicion"
        return "mensaje_transicion" if base[5] != arbol[5] else ""
    if base[4] != arbol[4] and kind not in permitidos:
        return "tipo_rechazo"
    return ""


def _comparar(base: Path, arbol: Path, kinds: list[str], permitidos: set[str]) -> int:
    diferencias: Counter[tuple[str, str]] = Counter()
    total: Counter[str] = Counter()
    with base.open(encoding="utf-8") as izquierda, arbol.open(encoding="utf-8") as derecha:
        while True:
            lbase, larbol = izquierda.readline(), derecha.readline()
            if not lbase and not larbol:
                break
            if not lbase or not larbol:
                diferencias["total", "casos_distintos"] += 1
                break
            a, b = json.loads(lbase), json.loads(larbol)
            kind = a[0]
            total[kind] += 1
            if diferencia := _clase(a, b, kind, permitidos):
                diferencias[kind, diferencia] += 1
    for kind in kinds:
        suyas = [(clase, n) for (k, clase), n in sorted(diferencias.items()) if k == kind]
        print(f"{kind}: {total[kind]} casos" +
              "".join(f", {clase}={n}" for clase, n in suyas))
    print(f"diferencias: {sum(diferencias.values())}")
    return int(bool(diferencias) or any(total[kind] == 0 for kind in kinds))


def _archivo_privado(ruta: Path):
    return os.fdopen(os.open(ruta, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600),
                     "w", encoding="utf-8")


def _ejecutar_lado(corpus: Path, raiz: Path, destino: Path, kinds: list[str],
                  max_casos: int, paso: int, legado: bool) -> None:
    orden = [
        sys.executable, "-I", str(Path(__file__).resolve()), "_evaluar",
        "--corpus", str(corpus), "--arbol", str(raiz), "--kinds", ",".join(kinds),
        "--max-casos", str(max_casos), "--paso", str(paso),
    ]
    if legado:
        orden.append("--estado-legado")
    with _archivo_privado(destino) as archivo:
        hecho = subprocess.run(orden, stdout=archivo, stderr=subprocess.PIPE,
                               text=True, check=False)
    if hecho.returncode:
        raise RuntimeError(f"el reductor no evaluó los casos: salida {hecho.returncode}")


def comparar(base: str, corpus: Path, arbol: Path, kinds: list[str],
             max_casos: int, permitidos: set[str], legado: bool) -> int:
    with (corpus / "meta.json").open(encoding="utf-8") as archivo:
        eventos = json.load(archivo)["eventos"]
    muestras = _muestras(corpus, set(kinds))
    por_prefijo = sum(3 * len(d) + 8 for k in kinds for d in muestras[k])
    paso = max(1, (eventos * por_prefijo + max_casos - 1) // max_casos)
    sha = subprocess.check_output(
        ["git", "-C", str(RAIZ), "rev-parse", "--verify", base],
        text=True,
    ).strip()
    archivo = subprocess.run(
        ["git", "-C", str(RAIZ), "archive", "--format=tar", sha, "motor"],
        capture_output=True, check=True,
    )
    with tempfile.TemporaryDirectory(prefix="escritura-diferencial-") as tmp:
        temporal = Path(tmp)
        with tarfile.open(fileobj=io.BytesIO(archivo.stdout)) as tar:
            tar.extractall(temporal / "base", filter="data")
        izquierda, derecha = temporal / "base.jsonl", temporal / "arbol.jsonl"
        _ejecutar_lado(corpus, temporal / "base", izquierda, kinds, max_casos, paso, legado)
        _ejecutar_lado(corpus, arbol, derecha, kinds, max_casos, paso, legado)
        return _comparar(izquierda, derecha, kinds, permitidos)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", required=False)
    parser.add_argument("--corpus", type=Path, required=True)
    parser.add_argument("--arbol", type=Path, default=RAIZ)
    parser.add_argument("--kinds", help="lista de kinds separados por coma")
    parser.add_argument("--max-casos", type=int, default=100_000)
    parser.add_argument("--permitir-cambio-tipo", default="")
    parser.add_argument("--estado-legado", action="store_true")
    parser.add_argument("--paso", type=int, default=1, help=argparse.SUPPRESS)
    parser.add_argument("orden", nargs="?", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.max_casos <= 0:
        parser.error("--max-casos debe ser positivo")
    if args.orden == "_evaluar":
        if not args.kinds:
            parser.error("_evaluar necesita --kinds")
        return _evaluar(args.corpus, args.arbol, args.kinds.split(","),
                        args.max_casos, args.paso, args.estado_legado)
    if args.orden is not None or not args.base:
        parser.error("necesita --base")
    from tools.corpus_plegado import _kinds

    kinds = sorted(set(args.kinds.split(",")) if args.kinds else _kinds(args.arbol))
    if not kinds or any(k not in _kinds(args.arbol) for k in kinds):
        parser.error("--kinds contiene un kind desconocido")
    if args.max_casos < len(kinds):
        parser.error("--max-casos menor que el número de kinds")
    try:
        return comparar(args.base, args.corpus, args.arbol, kinds,
                        args.max_casos, set(args.permitir_cambio_tipo.split(",")),
                        args.estado_legado)
    except (OSError, ValueError, subprocess.CalledProcessError, RuntimeError) as exc:
        print(f"escritura diferencial: no se pudo comparar ({type(exc).__name__})",
              file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
