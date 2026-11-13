"""Corre el canario de encargos en esta máquina y termina con su tabla.

Uso, con el intérprete del servicio de la máquina (los procesos que lanza usan
el mismo):

    <python del servicio> ops/canario_lanzador.py --repo <repo del canario>
        [--arbol <árbol del motor>] [--base ~/.motor-canario]
        [--base-viva ~/.motor-encargos] [--despliegue ~/motor-canario-despliegue]
        [--corrida <nombre>] [--maquina <nombre>] [--tope-min <n>] [--intervalo-s <n>]
        [--esperado <árbol>/ops/canario_encargos.esperado.json]

Una corrida da estos pasos, en este orden (`Paso`):

1. preparar: deja la base aparte con enlaces a los archivos de la máquina en la
   base viva (`ENLACES`) y sin `PROHIBIDOS`.
2. encargar: recibe un encargo de nivel desplegado contra el repo del canario,
   con la clave canario-<versión>-<máquina>-<corrida>. La misma corrida retoma
   el mismo encargo.
3. lanzar: un supervisor propio con `--agente ensayo` desde el árbol, con
   `MOTOR_CANARIO_ARBOL`, que es de donde el `trabajo.sh` del repo del canario
   toma el agente de ensayo.
4. esperar: `motor ver` hasta que el encargo cierra, vence el tope o el
   supervisor muere. No da órdenes ni escribe eventos.
5. detener: mata el grupo de su supervisor y mira que no quede nadie en él.
6. medir: `canario_encargos.observar` y `juzgar`.
7. limpiar: para su encargo si no cerró y borra su rama remota, espacio/<id>,
   ejecucion/<id> y trabajos/<id>.json de la flota. Nada que no lleve su id.

Por stderr van los avances y la tabla. Por stdout, un solo objeto JSON: el
`Informe`, o `{"paso", "error", "codigo": 2}` si no pudo correr (`paso` vacío ante
un error que ningún paso esperaba). Sale con 0 si
cada fila da lo esperado, el supervisor no dejó procesos y la limpieza terminó;
con 1 si no; con 2 si no pudo correr.

La salida trae ids, ramas y el repo: no va a un log público.
"""

from __future__ import annotations

import argparse
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import tomllib
import traceback
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal, cast

RAIZ = Path(__file__).resolve().parents[1]
if str(RAIZ) not in sys.path:
    sys.path.insert(0, str(RAIZ))

from motor import plataforma  # noqa: E402
from motor.encargos.registro import Registro  # noqa: E402
from motor.github import GhGitHub  # noqa: E402
from motor.io import leer_texto  # noqa: E402
from motor.repositorios import resolver_repositorio  # noqa: E402
from ops import canario_encargos as canario  # noqa: E402

Paso = Literal["preparar", "encargar", "lanzar", "esperar", "detener", "medir", "limpiar"]
Fin = Literal["terminado", "detenido", "cancelado", "tope", "supervisor_muerto"]
CERRADOS: dict[str, Fin] = {"terminado": "terminado", "detenido": "detenido", "cancelado": "cancelado"}
Presencia = Literal["ausente", "vivo", "incierto"]
"""Lo que queda del grupo del supervisor, con las palabras de `plataforma.presencia_grupo`."""

ENLACES = ("maquina.toml", "github.json", "capacidades.json", "flota-home.json")
PROHIBIDOS = ("secretos.env", "canal.json")
"""secretos.env trae la URL de Kuma y canal.json el canal: con ellos el supervisor
del canario hablaría en nombre del servicio vivo."""
TEXTO = "Canario: escribe el id de esta corrida en corridas/<máquina>.txt."
ID = re.compile(r"enc_[0-9a-f]+")
CORRIDA = re.compile(r"[A-Za-z0-9._-]+")


class NoSePuede(Exception):
    paso: Paso

    def __init__(self, paso: Paso, causa: str):
        super().__init__(causa)
        self.paso = paso
        self.causa = causa


@dataclass(frozen=True)
class Pedido:
    repo: str
    arbol: Path
    base: Path
    base_viva: Path
    despliegue: Path
    """La carpeta donde la receta del repo del canario deja `corridas/<máquina>.txt`."""
    corrida: str
    maquina: str
    """Vacío: el nombre de maquina.toml."""
    tope_s: float | None
    """None: los minutos de `termina` en lo esperado."""
    intervalo_s: float
    esperado: Path


@dataclass(frozen=True)
class Borrado:
    resto: str
    resultado: Literal["borrado", "no estaba", "falló"]
    causa: str = ""


@dataclass(frozen=True)
class Informe:
    maquina: str
    version: str
    corrida: str
    clave: str
    encargo: str
    fin: Fin
    estado: str
    """El estado del encargo al terminar la espera, según `motor ver`."""
    segundos: int
    """Desde que se pidió el encargo hasta que terminó la espera."""
    supervisor: Presencia
    filas: tuple[canario.Fila, ...]
    parada: str
    """Vacío si el encargo ya había cerrado; si no, el estado tras pararlo."""
    limpieza: tuple[Borrado, ...]

    @property
    def coincide(self) -> bool:
        return not any(f.discrepancia for f in self.filas)

    @property
    def codigo(self) -> int:
        limpio = self.supervisor == "ausente" and all(b.resultado != "falló" for b in self.limpieza)
        return 0 if self.coincide and limpio else 1

    def json(self) -> dict:
        return {
            **{k: v for k, v in asdict(self).items() if k not in ("filas", "limpieza")},
            "filas": [
                {
                    "nombre": f.nombre, "resultado": f.resultado, "esperado": f.esperado,
                    "discrepancia": f.discrepancia, "observado": f.observado,
                }
                for f in self.filas
            ],
            "limpieza": [asdict(b) for b in self.limpieza],
            "coincide": self.coincide,
            "codigo": self.codigo,
            "tabla": canario.tabla(self.filas),
        }


def _avance(paso: Paso, texto: str) -> None:
    print(f"canario [{paso}] {texto}", file=sys.stderr, flush=True)


def _entorno(arbol: Path) -> dict[str, str]:
    # Sin .pyc: el árbol puede ser el del servicio vivo y el canario no escribe en él.
    return {**os.environ, "PYTHONDONTWRITEBYTECODE": "1", "MOTOR_CANARIO_ARBOL": str(arbol)}


def _motor(p: Pedido, *argv: str) -> dict:
    """La salida JSON de `python -m motor <argv>` desde el árbol; RuntimeError si no la hay."""
    try:
        hecho = subprocess.run(
            [sys.executable, "-B", "-m", "motor", *argv], cwd=p.arbol, env=_entorno(p.arbol),
            capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=120, check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RuntimeError(f"motor {argv[0]}: {exc}") from exc
    try:
        datos = json.loads(hecho.stdout)
    except ValueError:
        datos = None
    if not isinstance(datos, dict):
        raise RuntimeError(f"motor {argv[0]} salió {hecho.returncode}: {hecho.stderr.strip()[-400:]}")
    return datos


def preparar(p: Pedido) -> tuple[str, canario.Esperado]:
    base, viva = p.base.expanduser().resolve(), p.base_viva.expanduser().resolve()
    if base == viva or viva in base.parents or base in viva.parents:
        raise NoSePuede("preparar", f"{p.base} no está aparte de la base viva {p.base_viva}")
    sobran = [n for n in PROHIBIDOS if os.path.lexists(base / n)]
    if sobran:
        raise NoSePuede("preparar", f"{p.base} trae {', '.join(sobran)}")
    base.mkdir(parents=True, exist_ok=True)
    for nombre in ENLACES:
        fuente, enlace = viva / nombre, base / nombre
        if not fuente.is_file():
            raise NoSePuede("preparar", f"falta {fuente}")
        if enlace.is_symlink() and os.readlink(enlace) == str(fuente):
            continue
        if os.path.lexists(enlace) and not enlace.is_symlink():
            raise NoSePuede("preparar", f"{enlace} existe y no es un enlace a {fuente}")
        try:
            enlace.unlink(missing_ok=True)
            enlace.symlink_to(fuente)
        except OSError as exc:
            raise NoSePuede("preparar", f"no pude enlazar {enlace}: {exc}") from exc
    try:
        nombre = str(tomllib.loads(leer_texto(base / "maquina.toml")).get("nombre") or "")
        esperado = canario.leer_esperado(p.esperado)
    except (OSError, ValueError) as exc:
        raise NoSePuede("preparar", str(exc)) from exc
    if not nombre:
        raise NoSePuede("preparar", "maquina.toml no trae nombre")
    if p.maquina and p.maquina != nombre:
        raise NoSePuede(
            "preparar",
            f"--maquina {p.maquina} no es {nombre}, el nombre de maquina.toml con el que"
            " el repo del canario nombra su archivo",
        )
    return nombre, esperado


def encargar(p: Pedido, clave: str) -> str:
    try:
        datos = _motor(
            p, "encargar", "--base", str(p.base), "--clave", clave, "--repo", p.repo,
            "--entrega", "desplegado", "--texto", TEXTO,
        )
    except RuntimeError as exc:
        raise NoSePuede("encargar", str(exc)) from exc
    if datos.get("conflicto"):
        raise NoSePuede("encargar", f"la clave {clave} ya existe con otro pedido")
    encargo = str(datos.get("encargo") or "")
    if not ID.fullmatch(encargo):
        raise NoSePuede("encargar", f"motor encargar no dio un id de encargo: {datos}")
    return encargo


def lanzar(p: Pedido, argv: Sequence[str]) -> dict:
    try:
        return plataforma.lanzar_separado(
            list(argv), p.arbol, _entorno(p.arbol), p.base / "canario-supervisor.log",
        )
    except OSError as exc:
        raise NoSePuede("lanzar", str(exc)) from exc


def esperar(p: Pedido, encargo: str, supervisor: dict, tope_s: float) -> tuple[Fin, str]:
    vence = time.monotonic() + tope_s
    estado = ""
    while True:
        try:
            visto = str(_motor(p, "ver", encargo, "--base", str(p.base)).get("estado") or "")
        except RuntimeError as exc:
            _avance("esperar", f"no pude leer el encargo; lo intento en la siguiente vuelta: {exc}")
        else:
            if visto != estado:
                _avance("esperar", f"{encargo} {visto}")
            estado = visto
        if estado in CERRADOS:
            return CERRADOS[estado], estado
        if plataforma.estado_proc(supervisor) != "vivo":
            return "supervisor_muerto", estado
        if time.monotonic() >= vence:
            return "tope", estado
        time.sleep(max(0.0, min(p.intervalo_s, vence - time.monotonic())))


def detener(supervisor: dict) -> Presencia:
    pid, inicio = int(supervisor["pid"]), str(supervisor.get("inicio") or "")
    job = str(supervisor.get("job") or "")
    plataforma.matar_proc(pid, inicio)
    plataforma.cerrar_huerfanos(pid, inicio, job)
    vence = time.monotonic() + 10
    while (presencia := plataforma.presencia_grupo(pid, inicio, job)) == "vivo":
        if time.monotonic() >= vence:
            break
        time.sleep(0.1)
    return cast(Presencia, presencia)


def _sha_desplegado(viva: Path) -> str:
    destino = canario._json_o_vacio(viva / "instalacion.json").get("destino")
    return str(destino.get("codigo") or "") if isinstance(destino, dict) else ""


def medir(
    p: Pedido, encargo: str, maquina: str, gh: canario.LectorPR, esperado: canario.Esperado,
) -> tuple[canario.Fila, ...]:
    obs = canario.observar(
        p.base, encargo, gh,
        hora=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        arbol=p.arbol,
        sha_desplegado=_sha_desplegado(p.base_viva.expanduser()),
        archivo=(p.despliegue / "corridas" / f"{maquina}.txt", encargo),
    )
    if obs.pliegue is None:
        raise NoSePuede("medir", f"el encargo {encargo} no está en el registro")
    return canario.juzgar(obs, esperado)


def _borrar_rama(repo: str, rama: str) -> Borrado:
    nombre = f"la rama remota {rama}"
    antes = canario._rama_existe(repo, rama)
    if antes is False:
        return Borrado(nombre, "no estaba")
    with tempfile.TemporaryDirectory() as vacio:
        # `git push` pide un repositorio; uno vacío no aporta nada al borrado.
        for argv in (
            ["git", "init", "-q", "--bare", vacio],
            ["git", "--git-dir", vacio, "push", "-q", resolver_repositorio(repo).remoto,
             "--delete", rama],
        ):
            hecho = subprocess.run(
                argv, capture_output=True, text=True, stdin=subprocess.DEVNULL,
                timeout=2 * canario.ESPERA_GIT_S, check=False,
            )
            if hecho.returncode:
                return Borrado(nombre, "falló", hecho.stderr.strip()[-300:])
    if canario._rama_existe(repo, rama) is not False:
        return Borrado(nombre, "falló", "git push --delete salió bien y la rama sigue")
    return Borrado(nombre, "borrado")


def _borrar_ruta(ruta: Path, nombre: str) -> Borrado:
    if not os.path.lexists(ruta):
        return Borrado(nombre, "no estaba")
    try:
        if ruta.is_dir() and not ruta.is_symlink():
            shutil.rmtree(ruta)
        else:
            ruta.unlink()
    except OSError as exc:
        return Borrado(nombre, "falló", str(exc))
    return Borrado(nombre, "borrado")


def _ramas(base: Path, encargo: str) -> list[str]:
    reg = Registro(base)
    try:
        e = reg.fold(encargo)
    finally:
        reg.close()
    return sorted({f.rama for f in e.fases if f.rama}) if e is not None else []


def limpiar(p: Pedido, encargo: str, estado: str) -> tuple[str, tuple[Borrado, ...]]:
    parada = ""
    if estado not in CERRADOS:
        try:
            parada = str(_motor(
                p, "parar", encargo, "--base", str(p.base), "--clave", f"{encargo}-cierre-canario",
            ).get("estado") or "")
        except RuntimeError as exc:
            parada = f"no se pudo parar: {exc}"
    borrados = [
        _borrar_rama(p.repo, rama) if encargo in rama
        else Borrado(f"la rama remota {rama}", "falló", "no lleva el id del encargo: no la borro")
        for rama in _ramas(p.base, encargo)
    ]
    borrados += [_borrar_ruta(p.base / d / encargo, f"{d}/{encargo}") for d in ("espacio", "ejecucion")]
    flota = str(canario._json_o_vacio(p.base / "flota-home.json").get("home") or "")
    trabajo = f"trabajos/{encargo}.json de la flota"
    borrados.append(
        _borrar_ruta(Path(flota) / "trabajos" / f"{encargo}.json", trabajo) if flota
        else Borrado(trabajo, "falló", "flota-home.json no dice dónde está la flota")
    )
    return parada, tuple(borrados)


def correr(p: Pedido, gh: canario.LectorPR, supervisor: Sequence[str] | None = None) -> Informe:
    maquina, esperado = preparar(p)
    version = canario._git_head(p.arbol)
    if not version:
        raise NoSePuede("encargar", f"no pude leer el SHA de {p.arbol}")
    clave = f"canario-{version[:12]}-{maquina}-{p.corrida}"
    _avance("preparar", f"base {p.base} lista; {maquina} en {version[:12]}")
    inicio = time.monotonic()
    encargo = encargar(p, clave)
    _avance("encargar", f"{encargo} con la clave {clave}")
    proceso = lanzar(p, supervisor or [
        sys.executable, "-B", "-m", "motor", "supervisor", "--base", str(p.base), "--agente", "ensayo",
    ])
    _avance("lanzar", f"supervisor {proceso['pid']}")
    tope_s = p.tope_s if p.tope_s is not None else esperado.topes.minutos_termina * 60.0
    try:
        fin, estado = esperar(p, encargo, proceso, tope_s)
    finally:
        presencia = detener(proceso)
    segundos = int(time.monotonic() - inicio)
    _avance("detener", f"la espera terminó por {fin} a los {segundos} s; su grupo quedó {presencia}")
    filas = medir(p, encargo, maquina, gh, esperado)
    parada, limpieza = limpiar(p, encargo, estado)
    for b in limpieza:
        _avance("limpiar", f"{b.resto}: {b.resultado}" + (f" ({b.causa})" if b.causa else ""))
    return Informe(
        maquina, version, p.corrida, clave, encargo, fin, estado, segundos, presencia,
        filas, parada, limpieza,
    )


def main(
    argv: list[str] | None = None, gh: canario.LectorPR | None = None,
    supervisor: Sequence[str] | None = None,
) -> int:
    for flujo in (sys.stdout, sys.stderr):
        if isinstance(flujo, io.TextIOWrapper):
            flujo.reconfigure(encoding="utf-8")
    casa = Path.home()
    a = argparse.ArgumentParser(
        description="Corre el canario de encargos en esta máquina y termina con su tabla.",
        epilog="La salida trae ids, ramas y el repo: no la mandes a un log público.",
    )
    a.add_argument("--repo", required=True, help="el repo del canario")
    a.add_argument("--arbol", type=Path, default=RAIZ, help="el árbol del motor que se prueba")
    a.add_argument("--base", type=Path, default=casa / ".motor-canario")
    a.add_argument("--base-viva", type=Path, default=casa / ".motor-encargos")
    a.add_argument("--despliegue", type=Path, default=casa / "motor-canario-despliegue")
    a.add_argument("--corrida", default=datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ"))
    a.add_argument("--maquina", default="")
    a.add_argument("--tope-min", type=float)
    a.add_argument("--intervalo-s", type=float, default=60.0)
    a.add_argument("--esperado", type=Path, help="por omisión, el del árbol")
    args = a.parse_args(argv)
    if not CORRIDA.fullmatch(args.corrida):
        a.error("--corrida solo lleva letras, dígitos, punto, guion y guion bajo")
    arbol = args.arbol.expanduser().resolve()
    p = Pedido(
        repo=args.repo, arbol=arbol, base=args.base.expanduser(),
        base_viva=args.base_viva.expanduser(), despliegue=args.despliegue.expanduser(),
        corrida=args.corrida, maquina=args.maquina,
        tope_s=None if args.tope_min is None else args.tope_min * 60.0,
        intervalo_s=args.intervalo_s,
        esperado=args.esperado or arbol / "ops" / "canario_encargos.esperado.json",
    )
    try:
        informe = correr(p, gh or GhGitHub(), supervisor)
    except NoSePuede as exc:
        _avance(exc.paso, f"no pude seguir: {exc.causa}")
        print(json.dumps({"paso": exc.paso, "error": exc.causa, "codigo": 2}, ensure_ascii=False))
        return 2
    except Exception as exc:  # el 1 queda para una tabla que no coincide, siempre con su informe
        traceback.print_exc()
        error = f"{type(exc).__name__}: {exc}"
        print(json.dumps({"paso": "", "error": error, "codigo": 2}, ensure_ascii=False))
        return 2
    print(canario.tabla(informe.filas), file=sys.stderr)
    print(json.dumps(informe.json(), ensure_ascii=False))
    return informe.codigo


if __name__ == "__main__":
    raise SystemExit(main())
