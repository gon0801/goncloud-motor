"""Pliega registros de encargos con el reductor y dice qué se rompe o cambia.

Uso, siempre en solo lectura (nunca escribe en un registro):

    python3 tools/plegado.py comprobar <registro.sqlite>...
    python3 tools/plegado.py comparar --base <ref-de-git> <registro.sqlite>...

`comprobar` pliega cada encargo con el reductor del árbol de trabajo. Sale con 1
si algún plegado lanza o termina fuera de `ESTADOS`. Además revisa las
invariantes de `INVARIANTES` en cada prefijo y las reporta aparte, sin cambiar
el código de salida.

`comparar` pliega cada encargo con el reductor de `<ref-de-git>` y con el del
árbol de trabajo y compara los estados finales, también dentro de las fases,
los hallazgos y las demás listas de objetos. Separa dos clases de diferencia:

- de forma: una clave que existe en un solo lado. Se lista una vez por
  registro, con su ruta, el lado donde existe y en cuántos encargos aparece.
- de valor: una clave de los dos lados con valor distinto, o una lista que
  cambió de largo. Se lista por encargo, con la ruta del campo.

Sale con 1 si hay diferencias de valor; las de forma solas salen con 0, salvo
con `--estricto`. El reductor de la base corre en otro proceso, sacado con
`git archive`, para no mezclar dos versiones del mismo módulo.

`--json` cambia la salida en español por un documento para un guion. Si no
puede leer un registro o sacar el reductor de la base, sale con 2.
"""

from __future__ import annotations

import argparse
import dataclasses
import io
import json
import socket
import sqlite3
import subprocess
import sys
import tarfile
import tempfile
from collections import Counter
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, NoReturn

RAIZ = Path(__file__).resolve().parents[1]

Evento = dict[str, Any]
Campos = dict[str, Any]


@dataclass(frozen=True)
class Fallo:
    """El evento donde el plegado de un encargo se rompió."""

    evento: int
    tipo: str
    error: str


@dataclass(frozen=True)
class Plegado:
    """Un encargo plegado: sus campos finales, o el evento que lo rompió."""

    encargo: str
    eventos: int
    final: Campos | Fallo


@dataclass(frozen=True)
class Violacion:
    """El primer prefijo de un encargo donde una invariante no se sostiene."""

    invariante: str
    encargo: str
    evento: int
    tipo: str
    detalle: str


Lado = Literal["base", "arbol"]


@dataclass(frozen=True)
class Diferencia:
    """Las rutas que existen en los dos lados y quedan con distinto valor en un encargo."""

    encargo: str
    campos: tuple[str, ...]
    detalle: str = ""


@dataclass(frozen=True)
class DiferenciaDeForma:
    """Una ruta que existe en un solo lado, contada sobre todo el registro."""

    ruta: str
    solo_en: Lado
    encargos: int


@dataclass(frozen=True)
class Comprobacion:
    ruta: str
    plegados: tuple[Plegado, ...]
    violaciones: tuple[Violacion, ...]

    @property
    def fallos(self) -> tuple[tuple[str, Fallo], ...]:
        return tuple((p.encargo, p.final) for p in self.plegados if isinstance(p.final, Fallo))


@dataclass(frozen=True)
class Comparacion:
    ruta: str
    encargos: int
    diferencias: tuple[Diferencia, ...]
    forma: tuple[DiferenciaDeForma, ...]


@dataclass(frozen=True)
class Reductor:
    """El reductor de un árbol: `reducir` y su conjunto de estados válidos."""

    reducir: Callable[..., Any]
    estados: tuple[str, ...]

    def aplicar(self, estado: Any, evento: Evento) -> Any:
        resultado = self.reducir(estado, evento, nuevo=False)
        if resultado.estado not in self.estados:
            raise ValueError(f"estado fuera de ESTADOS: {resultado.estado}")
        return resultado


@dataclass
class Traza:
    """Los estados de cada prefijo que pliega y, si se rompió, dónde."""

    estados: list[Any] = field(default_factory=list)
    fallo: Fallo | None = None


def _no_pude(motivo: str) -> NoReturn:
    print(f"plegado: {motivo}", file=sys.stderr)
    raise SystemExit(2)


def cargar_reductor(raiz: Path) -> Reductor:
    sys.path.insert(0, str(raiz))
    from motor.encargos import dominio

    origen = Path(dominio.__file__).resolve()
    if not origen.is_relative_to(raiz.resolve()):
        _no_pude(f"el reductor se cargó de {origen}, no de {raiz}")
    return Reductor(reducir=dominio.reducir, estados=tuple(dominio.ESTADOS))


def _uri_solo_lectura(ruta: Path) -> str:
    return f"{ruta.resolve().as_uri()}?mode=ro"


def leer_encargos(ruta: Path) -> Iterator[tuple[str, list[Evento]]]:
    if not ruta.is_file():
        _no_pude(f"no existe el registro {ruta}")
    db = sqlite3.connect(_uri_solo_lectura(ruta), uri=True)
    try:
        try:
            ids = [r[0] for r in db.execute("SELECT DISTINCT encargo FROM events ORDER BY encargo")]
        except sqlite3.DatabaseError as exc:
            _no_pude(f"no puedo leer {ruta}: {exc}")
        for encargo in ids:
            filas = db.execute(
                "SELECT id, ts, kind, data FROM events WHERE encargo = ? ORDER BY id",
                (encargo,),
            )
            yield encargo, [
                {"id": i, "ts": ts, "kind": kind, "data": json.loads(data)}
                for i, ts, kind, data in filas
            ]
    finally:
        db.close()


def trazar(reductor: Reductor, eventos: Sequence[Evento]) -> Traza:
    traza = Traza()
    estado = None
    for evento in eventos:
        try:
            estado = reductor.aplicar(estado, evento)
        except Exception as exc:
            traza.fallo = Fallo(evento["id"], evento["kind"], f"{type(exc).__name__}: {exc}")
            return traza
        traza.estados.append(estado)
    return traza


def _a_json(valor: object) -> object:
    if isinstance(valor, (set, frozenset)):
        return sorted(valor, key=str)
    return str(valor)


def campos_de(estado: Any) -> Campos:
    """Los campos del estado como JSON, iguales en cualquier proceso."""
    crudo = json.dumps(dataclasses.asdict(estado), default=_a_json, sort_keys=True)
    return json.loads(crudo)


def campos_distintos(a: Campos, b: Campos) -> tuple[str, ...]:
    return tuple(sorted(k for k in a.keys() | b.keys() if a.get(k, ...) != b.get(k, ...)))


def _primera_divergencia(
    eventos: Sequence[Evento], a: Traza, b: Traza
) -> tuple[Evento, str] | None:
    for i, (x, y) in enumerate(zip(a.estados, b.estados, strict=False)):
        if x != y:
            difieren = campos_distintos(campos_de(x), campos_de(y))
            return eventos[i], "difieren " + ", ".join(difieren)
    if len(a.estados) != len(b.estados):
        i = min(len(a.estados), len(b.estados))
        return eventos[i], "un plegado se rompe aquí y el otro no"
    return None


@contextmanager
def _otro_host() -> Iterator[None]:
    original = socket.gethostname
    otro = f"{original()}-otra-maquina"
    socket.gethostname = lambda: otro
    try:
        yield
    finally:
        socket.gethostname = original


def _cancelacion_definitiva(
    _reductor: Reductor, eventos: Sequence[Evento], traza: Traza
) -> tuple[Evento, str] | None:
    aceptada = cancelado = False
    for evento, e in zip(eventos, traza.estados, strict=False):
        if aceptada and e.orden_parada != "cancelar" and e.estado != "cancelado":
            return evento, f"la orden de cancelar se perdió; queda {e.estado}"
        if cancelado and e.estado != "cancelado":
            return evento, f"un encargo cancelado pasó a {e.estado}"
        aceptada = aceptada or e.orden_parada == "cancelar" or e.estado == "cancelado"
        cancelado = cancelado or e.estado == "cancelado"
    return None


def _mismo_estado_al_replegar(
    reductor: Reductor, eventos: Sequence[Evento], traza: Traza
) -> tuple[Evento, str] | None:
    return _primera_divergencia(eventos, traza, trazar(reductor, eventos))


def _mismo_estado_con_otro_host(
    reductor: Reductor, eventos: Sequence[Evento], traza: Traza
) -> tuple[Evento, str] | None:
    with _otro_host():
        otra = trazar(reductor, eventos)
    return _primera_divergencia(eventos, traza, otra)


Invariante = Callable[[Reductor, Sequence[Evento], Traza], "tuple[Evento, str] | None"]

INVARIANTES: tuple[tuple[str, Invariante], ...] = (
    ("cancelacion_definitiva", _cancelacion_definitiva),
    ("mismo_estado_al_replegar", _mismo_estado_al_replegar),
    ("mismo_estado_con_otro_host", _mismo_estado_con_otro_host),
)


def _plegado(encargo: str, eventos: Sequence[Evento], traza: Traza) -> Plegado:
    final = traza.fallo or campos_de(traza.estados[-1])
    return Plegado(encargo, len(eventos), final)


def comprobar(reductor: Reductor, ruta: Path) -> Comprobacion:
    plegados: list[Plegado] = []
    violaciones: list[Violacion] = []
    for encargo, eventos in leer_encargos(ruta):
        traza = trazar(reductor, eventos)
        plegados.append(_plegado(encargo, eventos, traza))
        for nombre, invariante in INVARIANTES:
            hallado = invariante(reductor, eventos, traza)
            if hallado is not None:
                evento, detalle = hallado
                violaciones.append(
                    Violacion(nombre, encargo, evento["id"], evento["kind"], detalle)
                )
    return Comprobacion(str(ruta), tuple(plegados), tuple(violaciones))


def plegar(reductor: Reductor, ruta: Path) -> list[Plegado]:
    return [
        _plegado(encargo, eventos, trazar(reductor, eventos))
        for encargo, eventos in leer_encargos(ruta)
    ]


def _describe(final: Campos | Fallo) -> str:
    if isinstance(final, Fallo):
        return f"se rompe en el evento {final.evento} ({final.tipo}): {final.error}"
    return "pliega"


def _contrastar(
    a: object, b: object, ruta: str, forma: set[tuple[str, Lado]], valores: set[str] | None
) -> None:
    """Recorre los dos lados a la par; `valores` en None junta solo la forma.

    Una lista de otro largo ya es diferencia de valor y sus elementos no se
    pueden emparejar por posición, pero cada objeto de una lista sale del mismo
    tipo, así que sus claves sí dicen la forma.
    """
    if isinstance(a, dict) and isinstance(b, dict):
        for clave in sorted(a.keys() | b.keys()):
            sub = f"{ruta}.{clave}" if ruta else clave
            if clave not in b:
                forma.add((sub, "base"))
            elif clave not in a:
                forma.add((sub, "arbol"))
            else:
                _contrastar(a[clave], b[clave], sub, forma, valores)
    elif isinstance(a, list) and isinstance(b, list):
        if len(a) != len(b) and valores is not None:
            valores.add(ruta)
            valores = None
        for x, y in zip(a, b, strict=False):
            _contrastar(x, y, f"{ruta}[]", forma, valores)
    elif a != b and valores is not None:
        valores.add(ruta)


def diferencias(
    base: Sequence[Plegado], arbol: Sequence[Plegado]
) -> tuple[list[Diferencia], list[DiferenciaDeForma]]:
    de_base = {p.encargo: p.final for p in base}
    salida: list[Diferencia] = []
    cuenta: Counter[tuple[str, Lado]] = Counter()
    for p in arbol:
        if p.encargo not in de_base:
            salida.append(Diferencia(p.encargo, ("plegado",), "la base no leyó este encargo"))
            continue
        a, b = de_base[p.encargo], p.final
        if isinstance(a, dict) and isinstance(b, dict):
            forma: set[tuple[str, Lado]] = set()
            valores: set[str] = set()
            _contrastar(a, b, "", forma, valores)
            cuenta.update(forma)
            if valores:
                salida.append(Diferencia(p.encargo, tuple(sorted(valores))))
        elif a != b:
            detalle = f"base: {_describe(a)}; árbol: {_describe(b)}"
            salida.append(Diferencia(p.encargo, ("plegado",), detalle))
    return salida, [DiferenciaDeForma(r, lado, n) for (r, lado), n in sorted(cuenta.items())]


def _resolver_ref(ref: str) -> str:
    hecho = subprocess.run(
        ["git", "-C", str(RAIZ), "rev-parse", "--verify", f"{ref}^{{commit}}"],
        capture_output=True, text=True, check=False,
    )
    if hecho.returncode != 0:
        _no_pude(f"no encuentro la ref {ref}: {hecho.stderr.strip()}")
    return hecho.stdout.strip()


def _plegar_en_la_base(sha: str, rutas: Sequence[Path]) -> dict[str, list[Plegado]]:
    archivo = subprocess.run(
        ["git", "-C", str(RAIZ), "archive", "--format=tar", sha, "motor"],
        capture_output=True, check=False,
    )
    if archivo.returncode != 0:
        _no_pude(f"git archive de {sha[:12]} falló: {archivo.stderr.decode().strip()}")
    with tempfile.TemporaryDirectory(prefix="plegado-base-") as tmp:
        with tarfile.open(fileobj=io.BytesIO(archivo.stdout)) as tar:
            tar.extractall(tmp, filter="data")
        hecho = subprocess.run(
            [sys.executable, "-I", str(Path(__file__).resolve()), "plegar",
             "--raiz", tmp, *map(str, rutas)],
            capture_output=True, text=True, check=False,
        )
    if hecho.returncode != 0:
        _no_pude(f"el reductor de {sha[:12]} no pudo plegar:\n{hecho.stderr}")
    crudo = json.loads(hecho.stdout)
    return {ruta: [_plegado_de_json(p) for p in ps] for ruta, ps in crudo.items()}


def _plegado_a_json(p: Plegado) -> dict:
    if isinstance(p.final, Fallo):
        return {"encargo": p.encargo, "eventos": p.eventos, "fallo": dataclasses.asdict(p.final)}
    return {"encargo": p.encargo, "eventos": p.eventos, "estado": p.final}


def _plegado_de_json(p: dict) -> Plegado:
    final = Fallo(**p["fallo"]) if "fallo" in p else p["estado"]
    return Plegado(p["encargo"], p["eventos"], final)


def comparar(sha: str, rutas: Sequence[Path]) -> list[Comparacion]:
    base = _plegar_en_la_base(sha, rutas)
    reductor = cargar_reductor(RAIZ)
    salida = []
    for ruta in rutas:
        arbol = plegar(reductor, ruta)
        valor, forma = diferencias(base[str(ruta)], arbol)
        salida.append(Comparacion(str(ruta), len(arbol), tuple(valor), tuple(forma)))
    return salida


def _texto_comprobacion(c: Comprobacion) -> list[str]:
    total, rotos = len(c.plegados), len(c.fallos)
    lineas = [c.ruta, f"  Leí {total} encargos; pliegan {total - rotos}."]
    if rotos:
        lineas.append("  No pliegan:")
        lineas.extend(
            f"    {encargo}: evento {f.evento} ({f.tipo}): {f.error}" for encargo, f in c.fallos
        )
    lineas.append("  Invariantes, revisadas en cada prefijo (no cambian la salida):")
    for nombre, _ in INVARIANTES:
        suyas = [v for v in c.violaciones if v.invariante == nombre]
        if not suyas:
            lineas.append(f"    {nombre}: sin violaciones.")
            continue
        lineas.append(f"    {nombre}: {len(suyas)} encargos la violan.")
        lineas.extend(
            f"      {v.encargo}: evento {v.evento} ({v.tipo}): {v.detalle}" for v in suyas
        )
    return lineas


_LADOS: dict[Lado, str] = {"base": "la base", "arbol": "el árbol"}


def _texto_comparacion(c: Comparacion, ref: str, sha: str, estricto: bool) -> list[str]:
    cabeza = f"  Comparé {c.encargos} encargos con el reductor de {ref} ({sha[:12]})"
    if not c.diferencias and not c.forma:
        return [c.ruta, cabeza + ": sin diferencias."]
    lineas = [c.ruta, cabeza + "."]
    if c.forma:
        efecto = "cuentan como fallo por --estricto" if estricto else "no cambian la salida"
        lineas.append(f"  Forma, claves que existen en un solo lado ({efecto}):")
        lineas.extend(
            f"    {f.ruta}: solo en {_LADOS[f.solo_en]}, en {f.encargos} encargos." for f in c.forma
        )
    if not c.diferencias:
        lineas.append("  Valor: sin diferencias.")
        return lineas
    lineas.append(f"  Valor: {len(c.diferencias)} encargos difieren.")
    for d in c.diferencias:
        linea = f"    {d.encargo}: difieren {', '.join(d.campos)}"
        lineas.append(linea + (f". {d.detalle}" if d.detalle else ""))
    return lineas


def _imprimir(documento: object) -> None:
    print(json.dumps(documento, ensure_ascii=False, indent=2, default=_a_json))


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Pliega registros de encargos con el reductor.")
    ordenes = parser.add_subparsers(dest="orden", required=True)
    p_comprobar = ordenes.add_parser("comprobar", help="pliega y revisa invariantes")
    p_comparar = ordenes.add_parser("comparar", help="compara con el reductor de otra ref")
    p_comparar.add_argument("--base", required=True)
    p_comparar.add_argument("--estricto", action="store_true",
                            help="las diferencias de forma también salen con 1")
    p_plegar = ordenes.add_parser("plegar", help="estados finales en JSON (lo usa comparar)")
    p_plegar.add_argument("--raiz", type=Path, default=RAIZ)
    for p in (p_comprobar, p_comparar):
        p.add_argument("--json", action="store_true")
    for p in (p_comprobar, p_comparar, p_plegar):
        p.add_argument("registros", nargs="+", type=Path)
    args = parser.parse_args(argv)

    if args.orden == "plegar":
        reductor = cargar_reductor(args.raiz)
        _imprimir({str(r): [_plegado_a_json(p) for p in plegar(reductor, r)]
                   for r in args.registros})
        return 0

    if args.orden == "comprobar":
        reductor = cargar_reductor(RAIZ)
        hechas = [comprobar(reductor, r) for r in args.registros]
        rotos = any(c.fallos for c in hechas)
        if args.json:
            _imprimir({"ok": not rotos, "registros": [
                {"ruta": c.ruta, "encargos": len(c.plegados),
                 "pliegan": len(c.plegados) - len(c.fallos),
                 "fallos": [{"encargo": encargo, **dataclasses.asdict(f)} for encargo, f in c.fallos],
                 "violaciones": [dataclasses.asdict(v) for v in c.violaciones]}
                for c in hechas]})
        else:
            print("\n".join(linea for c in hechas for linea in _texto_comprobacion(c)))
        return 1 if rotos else 0

    sha = _resolver_ref(args.base)
    hechas = comparar(sha, args.registros)
    distintos = any(c.diferencias or (args.estricto and c.forma) for c in hechas)
    if args.json:
        _imprimir({"ok": not distintos, "base": args.base, "sha": sha,
                   "estricto": args.estricto, "registros": [
            {"ruta": c.ruta, "encargos": c.encargos,
             "diferencias": [dataclasses.asdict(d) for d in c.diferencias],
             "forma": [dataclasses.asdict(f) for f in c.forma]}
            for c in hechas]})
    else:
        print("\n".join(linea for c in hechas
                        for linea in _texto_comparacion(c, args.base, sha, args.estricto)))
    return 1 if distintos else 0


if __name__ == "__main__":
    raise SystemExit(main())
