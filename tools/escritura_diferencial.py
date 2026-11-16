"""Compara escrituras nuevas sobre prefijos del corpus con dos reductores aislados.

Uso:
    python tools/escritura_diferencial.py --base origin/main --corpus DIR
        [--kinds comprobacion,solicitud] [--max-casos N]

Por omisión se cubre cada firma por cada kind y se rotan variantes con el
presupuesto restante. Un tope explícito menor que esa cobertura es inválido.
La salida nunca incluye datos ni mensajes del registro. Los resultados
completos viven solo en archivos temporales privados.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import itertools
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


class TopeInsuficiente(ValueError):
    pass


def _muestras(corpus: Path, kinds: set[str]) -> dict[str, list[dict]]:
    from tools.plegado import leer_encargos, ultimo_evento

    muestras: dict[str, list[dict]] = {kind: [] for kind in kinds}
    formas: dict[str, set[tuple]] = {kind: set() for kind in kinds}
    grupos: dict[str, Counter[tuple]] = {kind: Counter() for kind in kinds}
    for ruta in sorted((corpus / "registros").glob("*.sqlite")):
        for _, eventos in leer_encargos(ruta, ultimo_evento(ruta)):
            for evento in eventos:
                kind, data = evento["kind"], evento["data"]
                if kind not in muestras or not isinstance(data, dict):
                    continue
                grupo = tuple(str(data.get(campo) or "")
                              for campo in ("paso", "clase", "operacion", "estado"))
                forma = (grupo, tuple(sorted(data)))
                if grupos[kind][grupo] < 2 and forma not in formas[kind]:
                    muestras[kind].append(data)
                    formas[kind].add(forma)
                    grupos[kind][grupo] += 1
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
    if kind == "comprobacion" and "evidencia" in datos:
        for campo, valor in (("fase", "otra_fase"), ("intento", "otro_intento")):
            yield f"evidencia_y_{campo}", {**datos, "evidencia": "", campo: valor}
    numericos = {campo for campo, valor in datos.items()
                 if isinstance(valor, (int, float)) and not isinstance(valor, bool)}
    numericos.update(set(datos) & {
        "generacion", "intentos", "ordenes", "numero", "sondeos", "fallas",
        "ajustes_vistos", "cierra",
    })
    if kind == "solicitud":
        numericos.add("generacion")
    for campo in sorted(numericos):
        for nombre, valor in (
            ("infinito", float("inf")), ("menos_infinito", float("-inf")),
            ("nan", float("nan")), ("negativo", -1),
            ("lista", []), ("texto", "no numerico"),
        ):
            yield f"{campo}:{nombre}", {**datos, campo: valor}
    for campo, valor in sorted(datos.items()):
        yield f"falta:{campo}", {k: v for k, v in datos.items() if k != campo}
        yield f"vacio:{campo}", {**datos, campo: ""}
        malo = {} if not isinstance(valor, dict) else 17
        yield f"tipo:{campo}", {**datos, campo: malo}
    for primero, segundo in itertools.combinations(sorted(datos), 2):
        yield f"par:{primero}:{segundo}", {**datos, primero: "", segundo: ""}
    for campo, valor in (("fase", "otra_fase"), ("intento", "otro_intento")):
        yield f"{campo}:ajeno", {**datos, campo: valor}
    for campo in ("solicitudes", "efectos"):
        existentes = getattr(estado, campo, ()) if estado is not None else ()
        for indice, existente in enumerate(existentes):
            yield f"id:repetido:{campo}:{indice}", {**datos, "id": existente.id}
    if kind == "comprobacion" or "ajustes_vistos" in datos:
        n = len(estado.ajustes) if estado is not None else 0
        for nombre, valor in (("menos", n - 1), ("mas", n + 1), ("texto", "no numerico")):
            if nombre == "texto" and "ajustes_vistos" in numericos:
                continue
            yield f"ajustes_vistos:{nombre}", {**datos, "ajustes_vistos": valor}


def _firma_estado(e: Any) -> tuple:
    return (
        e.id, e.estado, e.fase_activa, bool(e.intento),
        tuple((f.id, f.estado) for f in e.fases),
        len(e.solicitudes), len(e.efectos), len(e.ajustes),
    )


def _casos_por_estado(kinds: list[str], muestras: dict[str, list[dict]],
                      estado: Any, indice: int, inicio: int):
    grupos = []
    for desplazamiento in range(len(kinds)):
        kind = kinds[(inicio + desplazamiento) % len(kinds)]
        muestra = muestras[kind][(indice // len(kinds)) % len(muestras[kind])]
        datos = _adaptar(muestra, estado)
        grupos.append((kind, list(_variantes(kind, datos, estado))))
    for vuelta in range(max(len(variantes) for _, variantes in grupos)):
        for kind, variantes in grupos:
            if vuelta < len(variantes):
                variante, carga = variantes[(indice + vuelta) % len(variantes)]
                yield kind, variante, carga


def _claves_de_referencia(corpus: Path) -> dict[str, None] | None:
    for ruta in sorted((corpus / "estado-base").glob("*.json")):
        with ruta.open(encoding="utf-8") as archivo:
            estados = json.load(archivo)
        if estados:
            return dict.fromkeys(next(iter(estados.values())))
    return None


def _escribir_caso(reductor: Any, estado: Any, kind: str, variante: str,
                  carga: dict, legado: bool, referencia: dict[str, None] | None = None) -> None:
    from tools.plegado import campos_de, sin_claves_nuevas

    clave = hashlib.sha256(
        json.dumps(carga, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()
    try:
        nuevo = reductor.reducir(estado, {"kind": kind, "data": carga}, nuevo=True)
        valor = campos_de(nuevo) if legado else nuevo
        if legado and referencia is not None:
            valor = sin_claves_nuevas(valor, referencia)
        salida = ["acepta", repr(valor)]
    except Exception as exc:
        salida = ["rechaza", type(exc).__name__, str(exc)]
    print(json.dumps([kind, variante, clave, *salida]))


def _evaluar(corpus: Path, raiz: Path, kinds: list[str], max_casos: int | None,
             legado: bool) -> int:
    from tools.plegado import cargar_reductor, leer_encargos, ultimo_evento

    reductor = cargar_reductor(raiz)
    referencia = _claves_de_referencia(corpus) if legado else None
    muestras = _muestras(corpus, set(kinds))
    if faltan := sorted(k for k, datos in muestras.items() if not datos):
        raise ValueError(f"kinds sin cargas reales: {', '.join(faltan)}")
    prefijos: dict[tuple, tuple[int, Any]] = {}
    prefijo = 0
    for ruta in sorted((corpus / "registros").glob("*.sqlite")):
        for _, eventos in leer_encargos(ruta, ultimo_evento(ruta)):
            estado = None
            for evento in eventos:
                estado = reductor.aplicar(estado, evento)
                prefijo += 1
                if estado is not None:
                    prefijos.setdefault(_firma_estado(estado), (prefijo, estado))
    if not prefijos:
        raise ValueError("sin estados previos legibles")
    entregas = [
        (indice, variante, carga)
        for indice, muestra in enumerate(muestras.get("entrega", []))
        for variante, carga in _variantes("entrega", muestra, None)
    ]
    pares = len(prefijos) * len(kinds)
    if max_casos is None:
        repetidos = (
            sum(len(estado.solicitudes) for _, estado in prefijos.values())
            if "solicitud" in kinds else 0
        )
        max_casos = (len(entregas) + pares + repetidos
                     + min(100_000, len(prefijos) * 32))
    print(json.dumps(["_firmas", len(prefijos)]))
    if max_casos < pares + len(entregas):
        return 3
    cuenta = 0
    if "entrega" in muestras:
        for indice, variante, carga in entregas:
            _escribir_caso(reductor, None, "entrega",
                           f"0:{indice}:{variante}", carga, legado, referencia)
            cuenta += 1
    for numero, estado in prefijos.values():
        for kind in kinds:
            muestra = muestras[kind][(cuenta // len(kinds)) % len(muestras[kind])]
            carga = _adaptar(muestra, estado)
            _escribir_caso(reductor, estado, kind, f"{numero}:base", carga, legado,
                           referencia)
            cuenta += 1
    if "solicitud" in kinds:
        for numero, estado in prefijos.values():
            if cuenta >= max_casos:
                break
            muestra = _adaptar(muestras["solicitud"][0], estado)
            for variante, carga in _variantes("solicitud", muestra, estado):
                if not variante.startswith("id:repetido:solicitudes:"):
                    continue
                if cuenta >= max_casos:
                    break
                _escribir_caso(reductor, estado, "solicitud",
                               f"{numero}:{variante}", carga, legado, referencia)
                cuenta += 1
    disponibles = max_casos - cuenta
    cuota, resto = divmod(disponibles, len(prefijos))
    ordinal = 0
    for indice, (numero, estado) in enumerate(prefijos.values()):
        for kind, variante, carga in itertools.islice(
            ((kind, variante, carga) for kind, variante, carga in
             _casos_por_estado(kinds, muestras, estado, indice, ordinal)
             if variante != "real" and not (
                 kind == "solicitud" and variante.startswith("id:repetido:solicitudes:")
             )),
            cuota + (indice < resto),
        ):
            _escribir_caso(reductor, estado, kind, f"{numero}:{variante}", carga, legado,
                           referencia)
            ordinal += 1
    return 0


def _clase(base: list, arbol: list, kind: str, permitidos: set[str]) -> str:
    if base[:3] != arbol[:3]:
        return "carga_diferente"
    if base[3] == "acepta":
        if arbol[3] != "acepta":
            return "base_acepta_arbol_rechaza"
        return "estado" if base[4] != arbol[4] else ""
    if arbol[3] == "acepta":
        return "base_rechaza_arbol_acepta"
    if base[4] == "TransicionIlegal":
        if arbol[4] != base[4]:
            return "tipo_transicion"
        return "mensaje_transicion" if base[5] != arbol[5] else ""
    if base[4] != arbol[4]:
        if kind in permitidos and arbol[4] == "TransicionIlegal":
            return ""
        return "tipo_rechazo"
    return "mensaje_rechazo" if base[5] != arbol[5] else ""


def _indice_casos(archivo) -> dict[tuple[str, str], int]:
    posiciones: dict[tuple[str, str], int] = {}
    while True:
        posicion = archivo.tell()
        linea = archivo.readline()
        if not linea:
            return posiciones
        caso = json.loads(linea)
        clave = caso[0], caso[1]
        if clave in posiciones:
            raise ValueError("identidad de caso repetida")
        posiciones[clave] = posicion


def _pares_emitidos(posiciones: dict[tuple[str, str], int]) -> set[tuple[str, str]]:
    pares = set()
    for kind, variante in posiciones:
        prefijo, separador, nombre = variante.partition(":")
        if separador and prefijo.isdecimal() and nombre == "base":
            pares.add((kind, prefijo))
    return pares


def _comparar(base: Path, arbol: Path, kinds: list[str], permitidos: set[str]) -> int:
    diferencias: Counter[tuple[str, str]] = Counter()
    total: Counter[str] = Counter()
    with base.open(encoding="utf-8") as izquierda, arbol.open(encoding="utf-8") as derecha:
        firmas_base = json.loads(izquierda.readline())
        firmas_arbol = json.loads(derecha.readline())
        if firmas_base[0] != "_firmas" or firmas_arbol[0] != "_firmas":
            raise ValueError("sin conteo de firmas")
        if firmas_base[1] != firmas_arbol[1]:
            print(f"firmas_estado_distintas={abs(firmas_base[1] - firmas_arbol[1])}")
            print("diferencias: 1")
            return 1
        posiciones_base = _indice_casos(izquierda)
        posiciones_arbol = _indice_casos(derecha)
        esperados = firmas_base[1] * len(kinds)
        pares_base = _pares_emitidos(posiciones_base)
        pares_arbol = _pares_emitidos(posiciones_arbol)
        print(f"cubiertos {len(pares_base)} de {esperados} pares firma×kind")
        if len(pares_base) != esperados or len(pares_arbol) != esperados:
            diferencias["total", "cobertura_incompleta"] = max(
                esperados - len(pares_base), esperados - len(pares_arbol), 1,
            )
        for kind, variante in posiciones_base.keys() & posiciones_arbol.keys():
            izquierda.seek(posiciones_base[kind, variante])
            derecha.seek(posiciones_arbol[kind, variante])
            a, b = json.loads(izquierda.readline()), json.loads(derecha.readline())
            kind = a[0]
            total[kind] += 1
            if diferencia := _clase(a, b, kind, permitidos):
                diferencias[kind, diferencia] += 1
        diferentes = len(posiciones_base.keys() ^ posiciones_arbol.keys())
        if diferentes:
            diferencias["total", "casos_distintos"] = diferentes
    for kind in kinds:
        suyas = [(clase, n) for (k, clase), n in sorted(diferencias.items()) if k == kind]
        print(f"{kind}: {total[kind]} casos" +
              "".join(f", {clase}={n}" for clase, n in suyas))
    if diferentes:
        print(f"casos_distintos={diferentes}")
    if diferencias["total", "cobertura_incompleta"]:
        print(f"cobertura_incompleta={diferencias['total', 'cobertura_incompleta']}")
    print(f"diferencias: {sum(diferencias.values())}")
    return int(bool(diferencias) or any(total[kind] == 0 for kind in kinds))


def _archivo_privado(ruta: Path):
    return os.fdopen(os.open(ruta, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600),
                     "w", encoding="utf-8")


def _ejecutar_lado(corpus: Path, raiz: Path, destino: Path, kinds: list[str],
                  max_casos: int | None, legado: bool) -> int:
    orden = [
        sys.executable, "-I", str(Path(__file__).resolve()), "_evaluar",
        "--corpus", str(corpus), "--arbol", str(raiz), "--kinds", ",".join(kinds),
    ]
    if max_casos is not None:
        orden.extend(("--max-casos", str(max_casos)))
    if legado:
        orden.append("--estado-legado")
    with _archivo_privado(destino) as archivo:
        hecho = subprocess.run(orden, stdout=archivo, stderr=subprocess.PIPE,
                               text=True, check=False)
    if hecho.returncode not in (0, 3):
        raise RuntimeError(f"el reductor no evaluó los casos: salida {hecho.returncode}")
    return hecho.returncode


def comparar(base: str, corpus: Path, arbol: Path, kinds: list[str],
             max_casos: int | None, permitidos: set[str], legado: bool) -> int:
    with (corpus / "meta.json").open(encoding="utf-8") as archivo:
        meta = json.load(archivo)
    if not isinstance(meta, dict) or "eventos" not in meta:
        raise ValueError("meta sin eventos")
    muestras = _muestras(corpus, set(kinds))
    entregas = sum(1 for datos in muestras.get("entrega", [])
                   for _ in _variantes("entrega", datos, None))
    if max_casos is not None and max_casos < len(kinds) + entregas:
        raise TopeInsuficiente("tope de casos menor que los kinds seleccionados")
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
        codigos = (
            _ejecutar_lado(corpus, temporal / "base", izquierda, kinds, max_casos, legado),
            _ejecutar_lado(corpus, arbol, derecha, kinds, max_casos, legado),
        )
        with izquierda.open(encoding="utf-8") as archivo:
            firmas_base = json.loads(archivo.readline())
        with derecha.open(encoding="utf-8") as archivo:
            firmas_arbol = json.loads(archivo.readline())
        if firmas_base[1] == firmas_arbol[1] and 3 in codigos:
            raise TopeInsuficiente("tope de casos menor que el número de firmas de estado")
        return _comparar(izquierda, derecha, kinds, permitidos)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", required=False)
    parser.add_argument("--corpus", type=Path, required=True)
    parser.add_argument("--arbol", type=Path, default=RAIZ)
    parser.add_argument("--kinds", help="lista de kinds separados por coma")
    parser.add_argument("--max-casos", type=int)
    parser.add_argument("--permitir-cambio-tipo", default="")
    parser.add_argument("--estado-legado", action="store_true")
    parser.add_argument("orden", nargs="?", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.max_casos is not None and args.max_casos <= 0:
        parser.error("--max-casos debe ser positivo")
    if args.orden == "_evaluar":
        if not args.kinds:
            parser.error("_evaluar necesita --kinds")
        return _evaluar(args.corpus, args.arbol, args.kinds.split(","),
                        args.max_casos, args.estado_legado)
    if args.orden is not None or not args.base:
        parser.error("necesita --base")
    from tools.corpus_plegado import _kinds

    kinds = sorted(set(args.kinds.split(",")) if args.kinds else _kinds(args.arbol))
    if not kinds or any(k not in _kinds(args.arbol) for k in kinds):
        parser.error("--kinds contiene un kind desconocido")
    if args.max_casos is not None and args.max_casos < len(kinds):
        parser.error("--max-casos menor que el número de kinds")
    try:
        return comparar(args.base, args.corpus, args.arbol, kinds,
                        args.max_casos, set(args.permitir_cambio_tipo.split(",")),
                        args.estado_legado)
    except (OSError, ValueError, subprocess.CalledProcessError, RuntimeError) as exc:
        detalle = ("tope de casos insuficiente" if isinstance(exc, TopeInsuficiente)
                   else type(exc).__name__)
        print(f"escritura diferencial: no se pudo comparar ({detalle})",
              file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
