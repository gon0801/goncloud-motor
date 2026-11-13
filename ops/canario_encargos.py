"""Juzga un encargo ya terminado contra los puntos de David y lo esperado.

Uso, siempre en solo lectura:

    python3 ops/canario_encargos.py <encargo> [--base ~/.motor-encargos]
        [--esperado ops/canario_encargos.esperado.json] [--arbol <ruta>]
        [--sha-desplegado <sha>] [--archivo-desplegado <ruta> --marca <texto>]

Tres capas separadas:

- `observar` lee el registro (abierto con `mode=ro`), GitHub (`gh pr view` y
  `git ls-remote`) y el disco de la base. Devuelve una `Observacion`.
- `juzgar` es pura: de la observación y lo esperado saca una `Fila` por cada
  entrada de `TABLA`, con `pasa`, `falla` o `no medido`.
- El archivo de lo esperado dice qué resultado se espera en cada fila y los
  topes. Una fila que pasaba y falla es regresión; una que empieza a pasar
  obliga a actualizar el archivo.

Sale con 0 solo si cada fila da lo esperado, con 1 si alguna no, y con 2 si no
puede juzgar: el encargo no está en el registro o el archivo de lo esperado no
tiene la forma.

La salida trae ids, ramas y números de PR: no va a un log público.
"""

from __future__ import annotations

import argparse
import dataclasses
import io
import json
import sqlite3
import subprocess
import sys
from collections import Counter
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Literal, Protocol, cast, get_args

RAIZ = Path(__file__).resolve().parents[1]
if str(RAIZ) not in sys.path:
    sys.path.insert(0, str(RAIZ))

from motor import plataforma  # noqa: E402
from motor.encargos.dominio import Encargo, nivel_objetivo  # noqa: E402
from motor.encargos.politica import CERRADOS_VENTANA_S  # noqa: E402
from motor.github import GhGitHub, PrView  # noqa: E402
from motor.io import leer_json, leer_texto  # noqa: E402
from motor.repositorios import resolver_repositorio  # noqa: E402
from tools import plegado  # noqa: E402

ESPERADO = RAIZ / "ops" / "canario_encargos.esperado.json"

Resultado = Literal["pasa", "falla", "no medido"]
NombreFila = Literal[
    "termina", "entregado", "limpio", "no pierde", "no repite",
    "cuesta poco", "se detiene", "versión", "legible",
]

# --------------------------------------------------------------------------- observación


@dataclass(frozen=True)
class PR:
    """El PR de una fase según GitHub. `numero` 0: la fase no abrió PR."""

    fase: str
    numero: int
    estado: str | None
    """abierto, mergeado o cerrado; None si GitHub no respondió."""
    base: str = ""
    merge_commit: str = ""


@dataclass(frozen=True)
class Recibo:
    """Un `despliegues/<clave>/recibo.json` del encargo."""

    fase: str
    estado: str
    sha: str


TipoResto = Literal["rama", "runtime", "proceso", "espacio", "ejecucion"]


@dataclass(frozen=True)
class Resto:
    """Algo que el encargo creó y su regla de limpieza manda borrar."""

    tipo: TipoResto
    nombre: str
    queda: bool | None
    """None: no se pudo mirar."""


@dataclass(frozen=True)
class Pliegue:
    """Lo que el registro dice del encargo, plegado con el reductor del árbol."""

    estado: str
    nivel_autorizado: str
    nivel_efectivo: str
    nivel_alcanzado: str
    base: str
    recibido: str
    cierre: str
    """Vacío mientras el encargo no cierre."""
    desplegables: tuple[str, ...]
    """Las fases que deben dejar recibo de despliegue."""
    turnos: tuple[tuple[str, int], ...]
    rondas: int
    """Revisiones independientes."""
    violaciones: tuple[str, ...]
    """Invariantes de `tools/plegado.py` que no se sostienen."""


@dataclass(frozen=True)
class NoPliega:
    evento: int
    tipo: str
    excepcion: str


@dataclass(frozen=True)
class Version:
    """Vacío: no hay de dónde leerlo."""

    desplegado: str
    arbol: str
    pasada: str


@dataclass(frozen=True)
class ArchivoDesplegado:
    ruta: str
    marca: str
    contiene: bool | None
    """None: el archivo no existe."""


@dataclass(frozen=True)
class Observacion:
    hora: str
    pliegue: Pliegue | NoPliega | None
    """None: el encargo no está en el registro."""
    prs: tuple[PR, ...] = ()
    recibos: tuple[Recibo, ...] = ()
    restos: tuple[Resto, ...] = ()
    version: Version = Version("", "", "")
    archivo: ArchivoDesplegado | None = None
    """None: nadie pidió revisar un archivo desplegado."""


# --------------------------------------------------------------------------- esperado


@dataclass(frozen=True)
class Topes:
    minutos_termina: int
    minutos: int
    turnos_por_rol: int
    rondas: int


@dataclass(frozen=True)
class Esperado:
    filas: Mapping[NombreFila, Resultado]
    topes: Topes


@dataclass(frozen=True)
class Fila:
    nombre: NombreFila
    punto: str
    resultado: Resultado
    esperado: Resultado
    observado: str
    fuente: str

    @property
    def discrepancia(self) -> str:
        return DISCREPANCIAS.get((self.esperado, self.resultado), "")


DISCREPANCIAS: dict[tuple[Resultado, Resultado], str] = {
    ("pasa", "falla"): "regresión",
    ("pasa", "no medido"): "dejó de medirse",
    ("falla", "no medido"): "dejó de medirse",
    ("falla", "pasa"): "mejora: actualiza el esperado",
    ("no medido", "pasa"): "ya se mide: actualiza el esperado",
    ("no medido", "falla"): "ya se mide: actualiza el esperado",
}

# --------------------------------------------------------------------------- juicio

Juicio = tuple[Resultado, str]

# Regla de limpieza del modelo (docs/modelo-centro-encargos.md): la copia de
# trabajo vive 7 días desde el estado final y `ejecucion/` hasta que cierra la
# ventana de sondeo; lo demás debe desaparecer al cerrar.
PLAZO_S: dict[TipoResto, int] = {
    "rama": 0,
    "runtime": 0,
    "proceso": 0,
    "espacio": 7 * 24 * 60 * 60,
    "ejecucion": CERRADOS_VENTANA_S,
}


def _hora(iso: str) -> datetime:
    return datetime.fromisoformat(iso.replace("Z", "+00:00"))


def _minutos(desde: str, hasta: str) -> int:
    return round((_hora(hasta) - _hora(desde)).total_seconds() / 60)


def _sin_pliegue(obs: Observacion) -> Juicio | None:
    if obs.pliegue is None:
        return "no medido", "el encargo no está en el registro"
    if isinstance(obs.pliegue, NoPliega):
        return "no medido", "el registro no se puede plegar (ver la fila legible)"
    return None


def _termina(obs: Observacion, topes: Topes) -> Juicio:
    if (fuera := _sin_pliegue(obs)) is not None:
        return fuera
    p = cast(Pliegue, obs.pliegue)
    if p.estado != "terminado":
        return "falla", f"el encargo quedó {p.estado}, no terminado"
    if p.nivel_alcanzado != p.nivel_autorizado:
        bajo = (
            f"; el motor bajó el objetivo a {p.nivel_efectivo}"
            if p.nivel_efectivo != p.nivel_autorizado else ""
        )
        return "falla", (
            f"terminó en nivel {p.nivel_alcanzado} y se pidió {p.nivel_autorizado}{bajo}"
        )
    minutos = _minutos(p.recibido, p.cierre or obs.hora)
    if minutos > topes.minutos_termina:
        return "falla", (
            f"terminó en nivel {p.nivel_alcanzado}, pero tardó {minutos} min"
            f" y el tope es {topes.minutos_termina}"
        )
    return "pasa", (
        f"terminó solo en nivel {p.nivel_alcanzado} en {minutos} min"
        f" (tope {topes.minutos_termina})"
    )


def _entregado(obs: Observacion, _topes: Topes) -> Juicio:
    if (fuera := _sin_pliegue(obs)) is not None:
        return fuera
    p = cast(Pliegue, obs.pliegue)
    if p.nivel_autorizado not in ("integrado", "desplegado"):
        return "no medido", f"se pidió nivel {p.nivel_autorizado}: no hay merge que comprobar"
    fallas: list[str] = []
    sin_leer: list[str] = []
    commits: dict[str, str] = {}
    for pr in obs.prs:
        if pr.numero == 0:
            fallas.append(f"la fase {pr.fase} no abrió PR")
        elif pr.estado is None:
            sin_leer.append(f"PR #{pr.numero}")
        elif pr.estado != "mergeado":
            fallas.append(f"el PR #{pr.numero} está {pr.estado}")
        elif pr.base != p.base:
            fallas.append(f"el PR #{pr.numero} se mergeó en {pr.base} y se pidió {p.base}")
        else:
            commits[pr.fase] = pr.merge_commit
    if not obs.prs:
        fallas.append("ninguna fase tiene PR")
    desplegadas = 0
    if p.nivel_autorizado == "desplegado":
        for fase in p.desplegables:
            if fase not in commits:
                continue
            if any(
                r.fase == fase and r.estado == "aplicado" and r.sha == commits[fase]
                for r in obs.recibos
            ):
                desplegadas += 1
            else:
                fallas.append(f"la fase {fase} no tiene recibo aplicado de su commit mergeado")
    if obs.archivo is not None:
        if obs.archivo.contiene is None:
            fallas.append(f"no existe el archivo desplegado {obs.archivo.ruta}")
        elif not obs.archivo.contiene:
            fallas.append(f"el archivo desplegado {obs.archivo.ruta} no trae la marca")
    if fallas:
        return "falla", "; ".join(fallas)
    if sin_leer:
        return "no medido", "GitHub no respondió por " + ", ".join(sin_leer)
    partes = [f"PR mergeados en {p.base}: {len(commits)} de {len(obs.prs)}"]
    if p.nivel_autorizado == "desplegado":
        partes.append(
            f"fases con recibo de despliegue aplicado de su commit mergeado:"
            f" {desplegadas} de {len(p.desplegables)}"
        )
    partes.append(
        "el archivo desplegado trae la marca" if obs.archivo is not None
        else "sin archivo desplegado que revisar"
    )
    return "pasa", "; ".join(partes)


def _limpio(obs: Observacion, _topes: Topes) -> Juicio:
    if (fuera := _sin_pliegue(obs)) is not None:
        return fuera
    p = cast(Pliegue, obs.pliegue)
    if not p.cierre:
        return "no medido", f"el encargo sigue {p.estado}: la limpieza empieza al cerrar"
    quedan: list[str] = []
    en_plazo: list[str] = []
    sin_mirar: list[str] = []
    for resto in obs.restos:
        plazo_s = PLAZO_S[resto.tipo]
        vence = _hora(p.cierre) + timedelta(seconds=plazo_s)
        if resto.queda is None:
            sin_mirar.append(resto.nombre)
        elif not resto.queda:
            continue
        elif plazo_s == 0 or _hora(obs.hora) >= vence:
            quedan.append(resto.nombre)
        else:
            en_plazo.append(f"{resto.nombre} hasta {vence:%Y-%m-%d}")
    plazo = f"; dentro de su plazo: {', '.join(en_plazo)}" if en_plazo else ""
    if quedan:
        return "falla", "quedan " + ", ".join(quedan) + plazo
    if sin_mirar:
        return "no medido", "no pude mirar " + ", ".join(sin_mirar) + plazo
    return "pasa", (
        f"nada vencido sin borrar entre {len(obs.restos)} restos revisados"
        f" (ramas remotas, runtime/, espacio/, ejecucion/ y procesos){plazo}"
    )


def _cuesta_poco(obs: Observacion, topes: Topes) -> Juicio:
    if (fuera := _sin_pliegue(obs)) is not None:
        return fuera
    p = cast(Pliegue, obs.pliegue)
    minutos = _minutos(p.recibido, p.cierre or obs.hora)
    turnos = ", ".join(f"{rol} {n}" for rol, n in p.turnos) or "ninguno"
    texto = (
        f"turnos {turnos} (tope {topes.turnos_por_rol} por rol);"
        f" rondas de revisión {p.rondas} (tope {topes.rondas});"
        f" {minutos} min (tope {topes.minutos})"
    )
    caro = (
        any(n > topes.turnos_por_rol for _, n in p.turnos)
        or p.rondas > topes.rondas
        or minutos > topes.minutos
    )
    return ("falla" if caro else "pasa"), texto


def _version(obs: Observacion, _topes: Topes) -> Juicio:
    v = obs.version
    faltan = [
        nombre for nombre, valor in (
            ("el SHA desplegado (instalacion.json)", v.desplegado),
            ("el SHA del árbol del servicio", v.arbol),
            ("el SHA de la última pasada del supervisor", v.pasada),
        ) if not valor
    ]
    if faltan:
        return "no medido", "no pude leer " + ", ".join(faltan)
    if v.arbol == v.desplegado and v.pasada == v.desplegado:
        return "pasa", f"árbol y última pasada del supervisor en {v.desplegado[:12]}, el desplegado"
    return "falla", (
        f"desplegado {v.desplegado[:12]}, árbol {v.arbol[:12]},"
        f" última pasada {v.pasada[:12]}"
    )


def _legible(obs: Observacion, _topes: Topes) -> Juicio:
    p = obs.pliegue
    if p is None:
        return "no medido", "el encargo no está en el registro"
    if isinstance(p, NoPliega):
        return "falla", f"el plegado se rompe en el evento {p.evento} ({p.tipo}): {p.excepcion}"
    if p.violaciones:
        return "falla", "pliega, pero no sostiene " + ", ".join(p.violaciones)
    return "pasa", f"pliega y sostiene las {len(plegado.INVARIANTES)} invariantes de tools/plegado.py"


def _sin_medicion(motivo: str) -> Callable[[Observacion, Topes], Juicio]:
    return lambda _obs, _topes: ("no medido", motivo)


Juez = Callable[[Observacion, Topes], Juicio]

TABLA: tuple[tuple[NombreFila, str, str, Juez], ...] = (
    ("termina", "1", "registro: estado, nivel y horas", _termina),
    ("entregado", "2", "GitHub (gh pr view) y despliegues/*/recibo.json", _entregado),
    ("limpio", "2", "GitHub (git ls-remote), runtime/, espacio/, ejecucion/ y procesos",
     _limpio),
    ("no pierde", "3", "sin fuente todavía",
     _sin_medicion("necesita los cortes de energía del canario, que aún no existen")),
    ("no repite", "3", "sin fuente todavía",
     _sin_medicion("necesita contar PR, merges, recetas e invocaciones tras un corte")),
    ("cuesta poco", "4", "registro: turnos, revisiones y horas", _cuesta_poco),
    ("se detiene", "5", "sin fuente todavía",
     _sin_medicion("necesita un encargo con un checklist que falla a propósito")),
    ("versión", "6", "instalacion.json, supervisor-pasada.json y HEAD del árbol", _version),
    ("legible", "todos", "registro: plegado e invariantes de tools/plegado.py", _legible),
)


def juzgar(obs: Observacion, esperado: Esperado) -> tuple[Fila, ...]:
    filas = []
    for nombre, punto, fuente, juez in TABLA:
        resultado, observado = juez(obs, esperado.topes)
        filas.append(
            Fila(nombre, punto, resultado, esperado.filas[nombre], observado, fuente)
        )
    return tuple(filas)


def leer_esperado(ruta: Path) -> Esperado:
    datos = leer_json(ruta)
    nombres = {nombre for nombre, *_ in TABLA}
    filas = datos.get("filas") if isinstance(datos, dict) else None
    topes = datos.get("topes") if isinstance(datos, dict) else None
    campos = {f.name for f in dataclasses.fields(Topes)}
    if (
        not isinstance(filas, dict)
        or set(filas) != nombres
        or any(v not in get_args(Resultado) for v in filas.values())
    ):
        raise ValueError(
            f"{ruta}: 'filas' debe dar uno de {list(get_args(Resultado))}"
            f" a cada fila: {sorted(nombres)}"
        )
    if (
        not isinstance(topes, dict)
        or set(topes) != campos
        or any(type(v) is not int for v in topes.values())
    ):
        raise ValueError(f"{ruta}: 'topes' debe traer enteros para {sorted(campos)}")
    return Esperado(filas=cast(Mapping[NombreFila, Resultado], filas), topes=Topes(**topes))


# --------------------------------------------------------------------------- observar


class LectorPR(Protocol):
    def ver(self, repo: str, numero: int) -> PrView: ...


def _eventos(registro: Path, encargo: str) -> list[dict]:
    db = sqlite3.connect(f"{registro.resolve().as_uri()}?mode=ro", uri=True)
    try:
        filas = db.execute(
            "SELECT id, ts, kind, data FROM events WHERE encargo = ? ORDER BY id", (encargo,)
        ).fetchall()
    finally:
        db.close()
    return [
        {"id": i, "ts": ts, "kind": kind, "data": json.loads(data)}
        for i, ts, kind, data in filas
    ]


def _pliegue(e: Encargo, eventos: list[dict], violaciones: tuple[str, ...]) -> Pliegue:
    turnos = Counter(
        str(ev["data"].get("rol") or "") for ev in eventos if ev["kind"] == "intento_abierto"
    )
    return Pliegue(
        estado=e.estado,
        nivel_autorizado=e.entrega.nivel,
        nivel_efectivo=nivel_objetivo(e),
        nivel_alcanzado=e.nivel_alcanzado,
        base=e.entrega.base,
        recibido=eventos[0]["ts"],
        cierre=e.hora_cierre,
        desplegables=tuple(f.id for f in e.fases if f.requiere_despliegue is not False),
        turnos=tuple(sorted(turnos.items())),
        rondas=sum(
            1 for ev in eventos
            if ev["kind"] == "revision" and ev["data"].get("origen") == "independiente"
        ),
        violaciones=violaciones,
    )


def _pr(gh: LectorPR, repo: str, fase: str, numero: int) -> PR:
    if not numero:
        return PR(fase, 0, None)
    try:
        v = gh.ver(repo, numero)
    except Exception:  # un GitHub caído es un dato: la fila queda sin medir
        return PR(fase, numero, None)
    return PR(fase, numero, v.estado, v.base, v.merge_commit)


def _rama_existe(repo: str, rama: str) -> bool | None:
    try:
        remoto = resolver_repositorio(repo).remoto
        hecho = subprocess.run(
            ["git", "ls-remote", "--", remoto, f"refs/heads/{rama}"],
            capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=60,
            check=False,
        )
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return None
    if hecho.returncode != 0:
        return None
    return bool(hecho.stdout.strip())


def _vivo(marca: dict) -> bool:
    try:
        return plataforma.estado_proc(marca) == "vivo"
    except (KeyError, TypeError, ValueError):
        return False


def _json_o_vacio(ruta: Path) -> dict:
    try:
        datos = leer_json(ruta)
    except (OSError, ValueError):
        return {}
    return datos if isinstance(datos, dict) else {}


def _despliegues(base: Path, encargo: str) -> tuple[tuple[Recibo, ...], bool]:
    """Los recibos del encargo y si alguno de sus trabajadores sigue vivo."""
    recibos: list[Recibo] = []
    vivo = False
    for pedido in sorted((base / "despliegues").glob("*/pedido.json")):
        identidad = _json_o_vacio(pedido).get("identidad") or {}
        if not isinstance(identidad, dict) or identidad.get("encargo") != encargo:
            continue
        recibo = _json_o_vacio(pedido.parent / "recibo.json")
        if recibo:
            recibos.append(Recibo(
                str(recibo.get("fase") or ""), str(recibo.get("estado") or ""),
                str(recibo.get("sha") or ""),
            ))
        proceso = _json_o_vacio(pedido.parent / "proceso.json")
        vivo = vivo or (bool(proceso) and _vivo(proceso))
    return tuple(recibos), vivo


def _git_head(arbol: Path) -> str:
    try:
        hecho = subprocess.run(
            ["git", "-C", str(arbol), "rev-parse", "HEAD"],
            capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=30, check=False,
        )
    except OSError:
        return ""
    return hecho.stdout.strip() if hecho.returncode == 0 else ""


def _version_de(base: Path, arbol: Path | None, sha_desplegado: str) -> Version:
    pasada = _json_o_vacio(base / "supervisor-pasada.json")
    instalacion = _json_o_vacio(base / "instalacion.json")
    destino = instalacion.get("destino")
    desplegado = sha_desplegado or str(
        (destino.get("codigo") if isinstance(destino, dict) else "") or ""
    )
    ruta = arbol or (Path(str(pasada["ruta"])) if pasada.get("ruta") else None)
    return Version(
        desplegado=desplegado,
        arbol=_git_head(ruta) if ruta else "",
        pasada=str(pasada.get("sha") or ""),
    )


def _archivo(ruta: Path, marca: str) -> ArchivoDesplegado:
    try:
        contiene: bool | None = marca in leer_texto(ruta, errors="replace")
    except OSError:
        contiene = None
    return ArchivoDesplegado(str(ruta), marca, contiene)


def observar(
    base: Path,
    encargo: str,
    gh: LectorPR,
    *,
    hora: str,
    arbol: Path | None = None,
    sha_desplegado: str = "",
    archivo: tuple[Path, str] | None = None,
) -> Observacion:
    eventos = _eventos(base / "registro.sqlite", encargo)
    version = _version_de(base, arbol, sha_desplegado)
    desplegado = _archivo(*archivo) if archivo else None
    if not eventos:
        return Observacion(hora, None, version=version, archivo=desplegado)
    reductor = plegado.cargar_reductor(RAIZ)
    traza = plegado.trazar(reductor, eventos)
    if traza.fallo is not None:
        rotura = NoPliega(traza.fallo.evento, traza.fallo.tipo, traza.fallo.excepcion)
        return Observacion(hora, rotura, version=version, archivo=desplegado)
    e: Encargo = traza.estados[-1]
    violaciones = tuple(
        nombre for nombre, invariante in plegado.INVARIANTES
        if invariante(reductor, eventos, traza) is not None
    )
    repo = e.entrega.repo
    prs = tuple(_pr(gh, repo, f.id, f.pr_numero) for f in e.fases) if repo else ()
    recibos, despliegue_vivo = _despliegues(base, encargo)
    turnos = [ev["data"] for ev in eventos if ev["kind"] == "intento_abierto"]
    ramas = sorted({f.rama for f in e.fases if f.rama}) if repo else []
    restos = (
        *(Resto("rama", f"la rama remota {rama}", _rama_existe(repo, rama)) for rama in ramas),
        *(
            Resto(tipo, f"{tipo}/{encargo}", (base / tipo / encargo).exists())
            for tipo in ("runtime", "espacio", "ejecucion")
        ),
        Resto(
            "proceso", f"algún proceso de sus {len(turnos)} turnos",
            any(_vivo(t) for t in turnos),
        ),
        Resto("proceso", "algún trabajador de despliegue", despliegue_vivo),
    )
    return Observacion(
        hora=hora,
        pliegue=_pliegue(e, eventos, violaciones),
        prs=prs,
        recibos=recibos,
        restos=restos,
        version=version,
        archivo=desplegado,
    )


# --------------------------------------------------------------------------- salida


def tabla(filas: tuple[Fila, ...]) -> str:
    lineas = [
        "| Fila | Punto | Resultado | Esperado | Observado | Fuente |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    lineas += [
        f"| {f.nombre} | {f.punto} | {f.resultado} | {f.esperado} | {f.observado} | {f.fuente} |"
        for f in filas
    ]
    distintas = [f for f in filas if f.discrepancia]
    if distintas:
        lineas.append("")
        lineas.append("No coincide con lo esperado:")
        lineas += [f"- {f.nombre}: {f.discrepancia}" for f in distintas]
    else:
        lineas.append("")
        lineas.append("Coincide con lo esperado.")
    return "\n".join(lineas)


def _ahora() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def main(argv: list[str] | None = None, gh: LectorPR | None = None) -> int:
    for flujo in (sys.stdout, sys.stderr):
        if isinstance(flujo, io.TextIOWrapper):
            flujo.reconfigure(encoding="utf-8")
    p = argparse.ArgumentParser(
        description="Juzga un encargo terminado contra la tabla del canario.",
        epilog="La salida trae ids, ramas y números de PR: no la mandes a un log público.",
    )
    p.add_argument("encargo")
    p.add_argument("--base", type=Path, default=Path.home() / ".motor-encargos")
    p.add_argument("--esperado", type=Path, default=ESPERADO)
    p.add_argument("--arbol", type=Path, help="árbol del servicio; por omisión, el de la última pasada")
    p.add_argument("--sha-desplegado", default="", help="por omisión, el de instalacion.json")
    p.add_argument("--archivo-desplegado", type=Path)
    p.add_argument("--marca", default="")
    args = p.parse_args(argv)
    if (args.archivo_desplegado is None) != (not args.marca):
        p.error("--archivo-desplegado y --marca van juntos")
    try:
        esperado = leer_esperado(args.esperado)
    except (OSError, ValueError) as exc:
        print(f"canario: {exc}", file=sys.stderr)
        return 2
    if not (args.base / "registro.sqlite").is_file():
        print(f"canario: no existe {args.base / 'registro.sqlite'}", file=sys.stderr)
        return 2
    obs = observar(
        args.base, args.encargo, gh or GhGitHub(),
        hora=_ahora(),
        arbol=args.arbol,
        sha_desplegado=args.sha_desplegado,
        archivo=(args.archivo_desplegado, args.marca) if args.archivo_desplegado else None,
    )
    if obs.pliegue is None:
        print(f"canario: el encargo {args.encargo} no está en el registro", file=sys.stderr)
        return 2
    filas = juzgar(obs, esperado)
    print(f"Encargo {args.encargo}, observado {obs.hora}.\n")
    print(tabla(filas))
    return 1 if any(f.discrepancia for f in filas) else 0


if __name__ == "__main__":
    raise SystemExit(main())
