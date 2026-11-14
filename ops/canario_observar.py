"""La observación del canario de encargos, siempre en solo lectura.

`observar` lee el registro (abierto con `mode=ro`), GitHub (`gh pr view` y
`git ls-remote`), el disco de la base, el clon del servicio y los procesos.
Juzga cada fase y cada proceso con las funciones del motor. Devuelve una
`Observacion`.
"""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
from collections import Counter
from collections.abc import Iterable
from dataclasses import replace
from pathlib import Path
from typing import Protocol

RAIZ = Path(__file__).resolve().parents[1]
if str(RAIZ) not in sys.path:
    sys.path.insert(0, str(RAIZ))

from motor.encargos import sesion  # noqa: E402
from motor.encargos.despliegue import comprobar_recibo  # noqa: E402
from motor.encargos.dominio import (  # noqa: E402
    Encargo,
    Fase,
    con_fase,
    nivel_cumplido,
    nivel_de,
    nivel_objetivo,
)
from motor.github import PrView  # noqa: E402
from motor.io import leer_json, leer_texto  # noqa: E402
from motor.repositorios import resolver_repositorio  # noqa: E402
from ops.canario_observacion import (  # noqa: E402
    PR,
    ArchivoDesplegado,
    Despliegue,
    Detencion,
    FaseVista,
    NoPliega,
    Observacion,
    Pliegue,
    Recibo,
    Resto,
    Version,
)
from tools import plegado  # noqa: E402

ESPERA_GIT_S = 30


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


def _exige_despliegue(e: Encargo, f: Fase) -> bool:
    """Si la fase se quedara en integrado, `nivel_cumplido` no la daría por cumplida."""
    integrada = con_fase(e, replace(f, ultimo_paso_entrega="integrado"))
    return not nivel_cumplido(replace(integrada, fase_activa=f.id))


def vista(e: Encargo, eventos: list[dict], violaciones: tuple[str, ...] = ()) -> Pliegue:
    """El encargo plegado, juzgado con las funciones del motor y no con copias."""
    turnos = Counter(
        str(ev["data"].get("rol") or "") for ev in eventos if ev["kind"] == "intento_abierto"
    )
    activa = e.fase_actual
    return Pliegue(
        estado=e.estado,
        nivel_autorizado=e.entrega.nivel,
        nivel_efectivo=nivel_objetivo(e),
        motivo_efectivo=e.nivel_efectivo_motivo,
        nivel_alcanzado=e.nivel_alcanzado,
        fase_activa=activa.id if activa else "",
        base=e.entrega.base,
        recibido=eventos[0]["ts"],
        cierre=e.hora_cierre,
        completo=e.objetivo_completo(),
        fases=tuple(
            FaseVista(
                id=f.id,
                estado=f.estado,
                nivel=nivel_de(f),
                cumplido=nivel_cumplido(replace(e, fase_activa=f.id)),
                desplegable=_exige_despliegue(e, f),
                causa=f.bloqueo_causa,
            )
            for f in e.fases
        ),
        turnos=tuple(sorted(turnos.items())),
        # La misma lectura que el reductor: una revisión sin origen es independiente.
        rondas=sum(
            1 for ev in eventos
            if ev["kind"] == "revision"
            and str(ev["data"].get("origen") or "independiente") == "independiente"
        ),
        violaciones=violaciones,
        ordenes=tuple(str(ev["data"].get("accion") or "") for ev in eventos if ev["kind"] == "orden"),
        causas=tuple(dict.fromkeys(
            c for c in (e.motivo, *(i.causa for i in e.impedimentos)) if c.strip()
        )),
    )


def _pr(gh: LectorPR, repo: str, fase: str, numero: int) -> PR:
    if not numero:
        return PR(fase, 0, None)
    try:
        v = gh.ver(repo, numero)
    except Exception:  # un GitHub caído es un dato: la fila queda sin medir
        return PR(fase, numero, None)
    return PR(fase, numero, v.estado, v.base, v.merge_commit)


def _git(*argv: str, espera: float) -> str | None:
    """La salida de `git`; None si no se pudo correr o no terminó bien."""
    try:
        hecho = subprocess.run(
            ["git", *argv], capture_output=True, text=True, stdin=subprocess.DEVNULL,
            timeout=espera, check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    return hecho.stdout.strip() if hecho.returncode == 0 else None


def _rama_existe(repo: str, rama: str) -> bool | None:
    try:
        remoto = resolver_repositorio(repo).remoto
    except (OSError, ValueError, subprocess.SubprocessError):
        return None
    salida = _git("ls-remote", "--", remoto, f"refs/heads/{rama}", espera=2 * ESPERA_GIT_S)
    return None if salida is None else bool(salida)


def _referencia_vencida(servicio: Path | None, rama: str, remota: bool | None) -> bool | None:
    """La rama ya no existe en el remoto y el clon del servicio aún guarda su referencia."""
    if servicio is None:
        return None
    salida = _git(
        "-C", str(servicio), "for-each-ref", "--format=%(refname)",
        f"refs/remotes/origin/{rama}", espera=ESPERA_GIT_S,
    )
    if salida is None:
        return None
    if not salida:
        return False
    return None if remota is None else not remota


def _tri(valores: Iterable[bool | None]) -> bool | None:
    """True si alguno queda, None si alguno no se pudo mirar, False si ninguno."""
    vistos = list(valores)
    if True in vistos:
        return True
    return None if None in vistos else False


def _existe(ruta: Path) -> bool | None:
    try:
        os.lstat(ruta)
    except FileNotFoundError:
        return False
    except OSError:
        return None
    return True


def _vivo(directorio: Path, proc: dict) -> bool | None:
    """Lo que dice `sesion.observar` sin escribir: el grupo del lanzador, el comprobador
    en curso y los descendientes de `arbol.json`. None: no se pudo mirar."""
    try:
        estado = sesion.observar(directorio, proc, escribir=False)
    except (OSError, KeyError, TypeError, ValueError):
        return None
    if estado == "ausente" and not proc:
        return None  # una identidad ilegible no demuestra que no quede nadie
    return {"vivo": True, "ausente": False}.get(estado)


def _json_o_vacio(ruta: Path) -> dict:
    try:
        datos = leer_json(ruta)
    except (OSError, ValueError):
        return {}
    return datos if isinstance(datos, dict) else {}


def _procesos_de_turnos(base: Path, encargo: str, eventos: list[dict]) -> Resto:
    """Los mismos candidatos que cierra el motor al limpiar (`proc.json` o
    `wrapper.json` de `ejecucion/<id>` y de cada intento), más la identidad de cada
    `intento_abierto`, que sobrevive al borrado del directorio. El proceso vigente
    del encargo sale de esos mismos eventos, así que ya está entre ellos."""
    raiz = base / "ejecucion" / encargo
    try:
        carpetas = [raiz, *sorted(raiz.glob("intento-*"))] if raiz.is_dir() else []
    except OSError:
        return Resto("proceso", f"los procesos de sus turnos en {raiz.name}", None)
    candidatos: list[tuple[Path, dict]] = []
    for carpeta in carpetas:
        for nombre in ("proc.json", "wrapper.json"):
            if _existe(carpeta / nombre):
                candidatos.append((carpeta, _json_o_vacio(carpeta / nombre)))
                break
    for ev in eventos:
        if ev["kind"] == "intento_abierto":
            token = str(ev["data"].get("id") or "")
            intentos = sorted(raiz.glob(f"intento-*-{token}")) if token else []
            candidatos += [(carpeta, ev["data"]) for carpeta in (raiz, *intentos)]
    # Cada carpeta guarda su comprobador y su arbol.json: la misma identidad se mira en todas.
    unicos = {
        (str(c), str(p.get("pid")), str(p.get("inicio"))): (c, p) for c, p in candidatos
    }
    turnos = sum(1 for ev in eventos if ev["kind"] == "intento_abierto")
    return Resto(
        "proceso", f"algún proceso de sus {turnos} turnos",
        _tri(_vivo(c, p) for c, p in unicos.values()),
    )


def _recibo(e: Encargo, fase: str) -> Recibo:
    try:
        datos = comprobar_recibo(replace(e, fase_activa=fase))
    except ValueError as exc:
        return Recibo(fase, str(exc), "")
    return Recibo(fase, "aplicado", str(datos.get("sha") or ""))


def _claves(base: Path) -> list[Path]:
    """Las carpetas de `despliegues/`, una por despliegue; OSError si no se pudo listar."""
    raiz = base / "despliegues"
    return sorted(d for d in raiz.iterdir() if d.is_dir()) if raiz.is_dir() else []


def _es_del_encargo(clave: Path, encargo: str) -> bool:
    recibo = _json_o_vacio(clave / "recibo.json")
    identidad = _json_o_vacio(clave / "pedido.json").get("identidad")
    dueno = identidad.get("encargo") if isinstance(identidad, dict) else None
    return encargo in (dueno, recibo.get("encargo"))


def _objeto(ruta: Path) -> dict | None:
    """El objeto JSON de `ruta`: vacío si no existe; None si existe y no se pudo leer."""
    try:
        datos = leer_json(ruta)
    except FileNotFoundError:
        return {}
    except (OSError, ValueError):
        return None
    return datos if isinstance(datos, dict) else None


def _dueno(pedido: dict | None, recibo: dict | None) -> str:
    """El encargo del despliegue: el de la identidad de su pedido o, si no, el de su recibo."""
    identidad = (pedido or {}).get("identidad")
    del_pedido = identidad.get("encargo") if isinstance(identidad, dict) else None
    for dueno in (del_pedido, (recibo or {}).get("encargo")):
        if isinstance(dueno, str) and dueno:
            return dueno
    return ""


def _pasos_lanzados(recibo: dict) -> tuple[tuple[str, int], ...] | None:
    """Los pasos que el trabajador lanzó, con su salida; None si alguno no dice si se lanzó."""
    pasos = recibo.get("pasos")
    if not isinstance(pasos, list):
        return None
    lanzados = []
    for paso in pasos:
        if not isinstance(paso, dict) or not isinstance(paso.get("paso"), str):
            return None
        if type(paso.get("enviado")) is not bool or (paso["enviado"] and type(paso.get("codigo")) is not int):
            return None
        if paso["enviado"]:
            lanzados.append((paso["paso"], paso["codigo"]))
    return tuple(lanzados)


def _recibos_de_despliegue(base: Path, encargo: str) -> tuple[Despliegue, ...] | None:
    """Los recibos de despliegue del encargo. None si alguno que puede ser suyo no se pudo
    leer o no dice qué pasos lanzó: un recibo ilegible no es un despliegue que no corrió."""
    try:
        claves = _claves(base)
    except OSError:
        return None
    despliegues = []
    for clave in claves:
        pedido, recibo = _objeto(clave / "pedido.json"), _objeto(clave / "recibo.json")
        dueno = _dueno(pedido, recibo)
        if not dueno and (pedido is None or recibo is None):
            return None
        if dueno != encargo:
            continue
        if recibo is None or (pasos := _pasos_lanzados(recibo)) is None:
            return None
        despliegues.append(Despliegue(str(recibo.get("fase") or ""), str(recibo.get("estado") or ""), pasos))
    return tuple(despliegues)


def _despliegues(base: Path, encargo: str) -> tuple[Resto, ...]:
    """Lo que quedó junto a los recibos del encargo y sus trabajadores."""
    raiz = base / "despliegues"
    try:
        claves = _claves(base)
    except OSError:
        return (
            Resto("despliegue", "despliegues/", None),
            Resto("proceso", "algún trabajador de despliegue", None),
        )
    restos: list[Resto] = []
    vivos: list[bool | None] = []
    for clave in claves:
        if not _es_del_encargo(clave, encargo):
            continue
        try:
            demas = sorted(x.name for x in clave.iterdir() if x.name != "recibo.json")
        except OSError:
            restos.append(Resto("despliegue", f"despliegues/{clave.name[:12]}", None))
        else:
            restos.append(Resto(
                "despliegue", f"despliegues/{clave.name[:12]} ({', '.join(demas)})", bool(demas),
            ))
        for nombre in ("proceso.json", "comando.json"):
            ident = _json_o_vacio(clave / nombre)
            if _existe(clave / nombre) and ident.get("estado") != "no_enviado":
                vivos.append(_vivo(clave, ident))
    puntero = raiz / f"{encargo}.actual"
    if (hay := _existe(puntero)) is not False:
        restos.append(Resto("despliegue", f"despliegues/{puntero.name}", hay))
    if not restos:
        restos.append(Resto("despliegue", "ningún despliegue del encargo", False))
    restos.append(Resto("proceso", "algún trabajador de despliegue", _tri(vivos)))
    return tuple(restos)


def _git_head(arbol: Path) -> str:
    return _git("-C", str(arbol), "rev-parse", "HEAD", espera=ESPERA_GIT_S) or ""


def _version_de(
    pasada: dict, instalacion: dict, servicio: Path | None, sha_desplegado: str,
) -> Version:
    destino = instalacion.get("destino")
    desplegado = sha_desplegado or str(
        (destino.get("codigo") if isinstance(destino, dict) else "") or ""
    )
    return Version(
        desplegado=desplegado,
        arbol=_git_head(servicio) if servicio else "",
        pasada=str(pasada.get("sha") or ""),
    )


def _archivo(ruta: Path, marca: str) -> ArchivoDesplegado:
    try:
        texto = leer_texto(ruta, errors="replace")
    except FileNotFoundError:
        return ArchivoDesplegado(str(ruta), marca, "no existe")
    except OSError:
        return ArchivoDesplegado(str(ruta), marca, "ilegible")
    return ArchivoDesplegado(str(ruta), marca, "con la marca" if marca in texto else "sin la marca")


def _restos(
    base: Path, e: Encargo, eventos: list[dict], prs: tuple[PR, ...],
    servicio: Path | None, despliegue: tuple[Resto, ...],
) -> tuple[Resto, ...]:
    repo = e.entrega.repo
    ramas = sorted({f.rama for f in e.fases if f.rama}) if repo else []
    remotas = {rama: _rama_existe(repo, rama) for rama in ramas}
    if e.estado == "cancelado":
        abiertos = [
            Resto("pr", f"el PR #{pr.numero} abierto", None if pr.estado is None else pr.estado == "abierto")
            for pr in prs if pr.numero
        ] or [Resto("pr", "ningún PR que cerrar", False)]
    else:
        abiertos = [Resto("pr", f"ningún PR que cerrar: el encargo quedó {e.estado}", False)]
    return (
        *(Resto("rama", f"la rama remota {rama}", queda) for rama, queda in remotas.items()),
        *([] if ramas else [Resto("rama", "ninguna rama remota registrada", False)]),
        *abiertos,
        *(
            Resto(tipo, f"{tipo}/{e.id}", _existe(base / tipo / e.id))
            for tipo in ("espacio", "ejecucion", "runtime")
        ),
        *despliegue,
        *(
            Resto(
                "referencia", f"la referencia origin/{rama} en el clon del servicio",
                _referencia_vencida(servicio, rama, remotas[rama]),
            )
            for rama in ramas
        ),
        *([] if ramas else [Resto("referencia", "ninguna rama que buscar en el clon del servicio", False)]),
        _procesos_de_turnos(base, e.id, eventos),
    )


def procesos(base: Path, encargo: str) -> tuple[Resto, ...]:
    """Los procesos del encargo que la fila limpio pide muertos, mirados con las mismas
    identidades que `observar`: los de sus turnos y sus trabajadores de despliegue."""
    eventos = _eventos(base / "registro.sqlite", encargo)
    return (
        _procesos_de_turnos(base, encargo, eventos),
        *(r for r in _despliegues(base, encargo) if r.tipo == "proceso"),
    )


def _plegar(eventos: list[dict]) -> tuple[Pliegue, Encargo] | NoPliega:
    """El encargo plegado con el reductor del árbol y su vista, o dónde se rompe."""
    reductor = plegado.cargar_reductor(RAIZ)
    traza = plegado.trazar(reductor, eventos)
    if traza.fallo is not None:
        return NoPliega(traza.fallo.evento, traza.fallo.tipo, traza.fallo.excepcion)
    e: Encargo = traza.estados[-1]
    violaciones = tuple(
        nombre for nombre, invariante in plegado.INVARIANTES
        if invariante(reductor, eventos, traza) is not None
    )
    return vista(e, eventos, violaciones), e


def _detencion(base: Path, encargo: str, archivo: Path | None) -> Detencion:
    desplegado = _archivo(archivo, encargo) if archivo else None
    eventos = _eventos(base / "registro.sqlite", encargo)
    if not eventos:
        return Detencion(encargo, None, archivo=desplegado)
    plegada = _plegar(eventos)
    if isinstance(plegada, NoPliega):
        return Detencion(encargo, plegada, archivo=desplegado)
    return Detencion(encargo, plegada[0], _recibos_de_despliegue(base, encargo), desplegado)


def observar(
    base: Path,
    encargo: str,
    gh: LectorPR,
    *,
    hora: str,
    arbol: Path | None = None,
    sha_desplegado: str = "",
    archivo: tuple[Path, str] | None = None,
    detencion: str = "",
    archivo_detencion: Path | None = None,
) -> Observacion:
    eventos = _eventos(base / "registro.sqlite", encargo)
    pasada = _json_o_vacio(base / "supervisor-pasada.json")
    instalacion = _json_o_vacio(base / "instalacion.json")
    servicio = arbol or (Path(str(pasada["ruta"])) if pasada.get("ruta") else None)
    comunes = Observacion(
        hora, None,
        version=_version_de(pasada, instalacion, servicio, sha_desplegado),
        actualizado=str(instalacion.get("hora") or ""),
        archivo=_archivo(*archivo) if archivo else None,
        detencion=_detencion(base, detencion, archivo_detencion) if detencion else None,
    )
    if not eventos:
        return comunes
    plegada = _plegar(eventos)
    if isinstance(plegada, NoPliega):
        return replace(comunes, pliegue=plegada)
    pliegue, e = plegada
    repo = e.entrega.repo
    prs = tuple(_pr(gh, repo, f.id, f.pr_numero) for f in e.fases) if repo else ()
    return replace(
        comunes,
        pliegue=pliegue,
        prs=prs,
        recibos=tuple(_recibo(e, f.id) for f in pliegue.fases if f.desplegable),
        restos=_restos(base, e, eventos, prs, servicio, _despliegues(base, encargo)),
    )
