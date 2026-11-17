"""Limpieza verificable: el registro guarda cada recurso y cada intento."""

from __future__ import annotations

import errno
import re
import subprocess
import weakref
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Callable, Literal, TypeVar

from motor import plataforma

from .. import despliegue, politica
from ..copiar import borrar_arbol
from ..dominio import _fallo_transitorio, reducir
from ..frontera import leer_parada_local
from motor.io import leer_json
from .observacion import anotar_pr

if TYPE_CHECKING:
    from ..dominio import Encargo
    from ..registro import Registro
    from motor.github import GitHubPort, PrView

Tipo = Literal["pr", "rama", "despliegue", "runtime", "espacio",
               "ejecucion", "credenciales"]
Cumplimiento = Literal["borrado", "conservado", "pendiente"]
_CLAVE_DESPLIEGUE = re.compile(r"[0-9a-f]{64}")
_CONSERVED = frozenset(("recibo.json", "pedido.json"))
_MAX_INTENTOS_LIMPIEZA_LEGADA = 2
# El sondeo solo dosifica lecturas: reiniciar adelanta una consulta, nunca acredita un borrado.
_SONDEOS_COMPARTIDOS: weakref.WeakKeyDictionary[Registro, dict[tuple[str, str], datetime]] = (
    weakref.WeakKeyDictionary()
)
_CUOTAS_GH: weakref.WeakKeyDictionary[Registro, tuple[float, int]] = weakref.WeakKeyDictionary()
T = TypeVar("T")


class ConsultaPospuesta(Exception):
    """No se pudo consultar GitHub en esta ventana; no se gastan intentos."""


def _transitorio_gh(exc: BaseException) -> bool:
    if isinstance(exc, (subprocess.TimeoutExpired, TimeoutError)):
        return True
    if not isinstance(exc, RuntimeError):
        return False
    texto = str(exc).lower()
    return (_fallo_transitorio(texto) or "rate limit" in texto
            or "too many requests" in texto
            or "abuse detection" in texto or "http 429" in texto
            or any(marca in texto for marca in (
                "i/o timeout", "tls handshake timeout", "context deadline exceeded",
                "error connecting to api.github.com", "may be the result of a timeout",
            ))
            or re.search(r"\b(?:http|status|error)\s*5\d\d\b", texto) is not None)


def fallos_legados(registro: Registro, encargo_id: str) -> tuple[int, str]:
    """La limpieza anterior usa sus notas como contador, no memoria del proceso."""
    fila = registro.db.execute(
        "SELECT id, ukey FROM events WHERE encargo = ? "
        "AND (ukey LIKE 'limpieza-restaurada:%' "
        "OR ukey LIKE 'limpieza-fallida:%') ORDER BY id DESC LIMIT 1",
        (encargo_id,),
    ).fetchone()
    if fila is not None:
        ukey = str(fila[1])
        if ukey.startswith(f"limpieza-restaurada:{encargo_id}:"):
            return 0, str(fila[0])
        if ukey.startswith(f"limpieza-fallida:{encargo_id}:"):
            partes = ukey.split(":")
            try:
                return int(partes[-1]), partes[-2] if len(partes) == 4 else ""
            except ValueError:
                pass
    return 0, ""


def limpiar_rutas_legadas(base: Path, encargo_id: str,
                         borrar: Callable[[Path], None]) -> tuple[bool, list[tuple[Path, OSError]]]:
    borrado = False
    fallos: list[tuple[Path, OSError]] = []

    def quitar(ruta: Path) -> None:
        nonlocal borrado
        if not (ruta.exists() or ruta.is_symlink()):
            return
        try:
            borrar(ruta)
            borrado = True
        except OSError as exc:
            fallos.append((ruta, exc))

    quitar(base / "runtime" / encargo_id)
    ejecucion = base / "ejecucion" / encargo_id
    try:
        intentos = sorted(
            (ruta for ruta in ejecucion.iterdir() if ruta.name.startswith("intento-")),
            key=lambda ruta: ruta.name,
        )
    except FileNotFoundError:
        intentos = []
    except OSError as exc:
        fallos.append((ejecucion, exc))
        intentos = []
    for intento in intentos:
        quitar(intento)
    quitar(ejecucion / "actual")
    return borrado, fallos


def limpiar_cerrado_legado(registro: Registro, enc: Encargo, hora: str,
                          borrar: Callable[[Path], None],
                          cerrar: Callable[[Registro, Encargo], int]) -> bool:
    fallos_previos, ciclo = fallos_legados(registro, enc.id)
    if fallos_previos >= _MAX_INTENTOS_LIMPIEZA_LEGADA:
        return False
    cerrar(registro, enc)
    borrado, fallos = limpiar_rutas_legadas(Path(registro.base), enc.id, borrar)
    if fallos:
        ruta, exc = fallos[0]
        nota = (
            f"limpieza({ruta.name}): {type(exc).__name__}: {exc}; "
            "no se reintenta en cada barrido"
        )[:2000]
        registro.anotar(
            enc.id, {"kind": "procedimiento", "data": {"nota": nota, "hora": hora}},
            f"limpieza-fallida:{enc.id}:{ciclo + ':' if ciclo else ''}{fallos_previos + 1}",
        )
    elif fallos_previos:
        registro.anotar(
            enc.id, {"kind": "procedimiento",
                     "data": {"nota": "limpieza restablecida", "hora": hora}},
            f"limpieza-restaurada:{enc.id}:{registro.ultimo_evento(enc.id)}",
        )
    return borrado


@dataclass(frozen=True)
class Recurso:
    tipo: Tipo
    clave: str
    fase: str = ""


def _fecha(hora: str) -> datetime | None:
    try:
        fecha = datetime.fromisoformat(hora.replace("Z", "+00:00"))
        return fecha if fecha.tzinfo is not None else None
    except (AttributeError, ValueError):
        return None


def _vence(enc: Encargo, hora: str) -> bool:
    final, actual = _fecha(enc.hora_final), _fecha(hora)
    if final is None or actual is None:
        return False
    return (actual - final).total_seconds() >= politica.CERRADOS_VENTANA_S


def _sondeo_vigente(enc: Encargo, hora: str) -> bool:
    cierre, actual = _fecha(enc.hora_cierre), _fecha(hora)
    return (cierre is not None and actual is not None
            and (actual - cierre).total_seconds() < politica.CERRADOS_VENTANA_S)


def _ruta(registro: Registro, recurso: Recurso) -> Path:
    base = Path(registro.base)
    if recurso.tipo == "credenciales":
        encargo, sep, token = recurso.clave.partition("/")
        if (sep != "/" or not re.fullmatch(r"[\w-]+", encargo, re.ASCII)
            or not re.fullmatch(r"[\w-]+", token, re.ASCII)):
            raise ValueError("credencial del turno inválida")
        return base / "runtime" / encargo / token
    if recurso.tipo == "despliegue":
        if re.fullmatch(r"[\w-]+\.actual", recurso.clave, re.ASCII):
            return base / "despliegues" / recurso.clave
        if not _CLAVE_DESPLIEGUE.fullmatch(recurso.clave):
            raise ValueError("clave de despliegue inválida")
        return base / "despliegues" / recurso.clave
    if (recurso.tipo == "ejecucion" and
        re.fullmatch(r"[\w-]+/actual", recurso.clave, re.ASCII)):
        return base / "ejecucion" / recurso.clave
    if recurso.tipo not in ("runtime", "espacio", "ejecucion"):
        raise ValueError("recurso sin ruta")
    if not re.fullmatch(r"[\w-]+", recurso.clave, re.ASCII):
        raise ValueError("id de recurso inválido")
    return base / recurso.tipo / recurso.clave


def _claves_de_recibos(registro: Registro, enc: Encargo) -> set[str]:
    raiz = Path(registro.base) / "despliegues"
    claves: set[str] = set()
    for fase in enc.fases:
        recibo = Path(fase.desplegado_recibo) if fase.desplegado_recibo else None
        if (recibo is not None and recibo.name == "recibo.json"
            and recibo.parent.parent == raiz
            and _CLAVE_DESPLIEGUE.fullmatch(recibo.parent.name)):
            claves.add(recibo.parent.name)
    return claves


def _despliegues(registro: Registro, enc: Encargo) -> tuple[str, ...]:
    claves = _claves_de_recibos(registro, enc)
    claves.update(_indice_despliegues(registro).get(enc.id, ()))
    return tuple(sorted(claves))


def _indice_despliegues(registro: Registro) -> dict[str, tuple[str, ...]]:
    raiz = Path(registro.base) / "despliegues"
    indice: dict[str, set[str]] = {}
    for ruta in raiz.glob("*/pedido.json"):
        if (not _CLAVE_DESPLIEGUE.fullmatch(ruta.parent.name)
            or ruta.parent.is_symlink() or ruta.is_symlink()):
            continue
        try:
            pedido = leer_json(ruta)
        except (OSError, ValueError, TypeError):
            continue
        identidad = pedido.get("identidad") if isinstance(pedido, dict) else None
        encargo = identidad.get("encargo") if isinstance(identidad, dict) else None
        if isinstance(encargo, str):
            indice.setdefault(encargo, set()).add(ruta.parent.name)
    return {ident: tuple(sorted(claves)) for ident, claves in indice.items()}


class PresupuestoLimpieza(list[int]):
    def __init__(self, registro: Registro, epoch: float | None = None) -> None:
        super().__init__([politica.LIMPIEZA.por_pasada])
        self.registro = registro
        self.epoch = epoch
        self.consultas = politica.LIMPIEZA.consultas_gh
        if epoch is not None and (anterior := _CUOTAS_GH.get(registro)) is not None:
            inicio, restantes = anterior
            if 0 <= epoch - inicio < politica.LIMPIEZA.ventana_gh_s:
                self.consultas = restantes
            else:
                _CUOTAS_GH[registro] = (epoch, self.consultas)
        elif epoch is not None:
            _CUOTAS_GH[registro] = (epoch, self.consultas)
        self.bloqueada = False
        self._indice: dict[str, tuple[str, ...]] | None = None
        self._vistas_pr: dict[tuple[str, int], PrView] = {}

    def consultar(self, funcion: Callable[..., T], *args) -> T:
        if self.bloqueada or self.consultas <= 0:
            raise ConsultaPospuesta()
        self.consultas -= 1
        if self.epoch is not None:
            inicio, _ = _CUOTAS_GH[self.registro]
            _CUOTAS_GH[self.registro] = (inicio, self.consultas)
        try:
            return funcion(*args)
        except (RuntimeError, subprocess.TimeoutExpired, TimeoutError) as exc:
            if not _transitorio_gh(exc):
                raise
            self.bloqueada = True
            if self.epoch is not None:
                inicio, _ = _CUOTAS_GH[self.registro]
                _CUOTAS_GH[self.registro] = (inicio, 0)
            raise ConsultaPospuesta() from exc

    def despliegues(self, encargo: str) -> tuple[str, ...]:
        if self._indice is None:
            self._indice = _indice_despliegues(self.registro)
        return self._indice.get(encargo, ())

    def ver_pr(self, gh: GitHubPort, repo: str, numero: int) -> PrView:
        clave = (repo, numero)
        if clave not in self._vistas_pr:
            self._vistas_pr[clave] = self.consultar(gh.ver, repo, numero)
        return self._vistas_pr[clave]

    def recordar_pr(self, repo: str, vista: PrView) -> None:
        self._vistas_pr[(repo, vista.numero)] = vista


def _consulta_gh(presupuesto: list[int], funcion: Callable[..., T], *args) -> T:
    if isinstance(presupuesto, PresupuestoLimpieza):
        return presupuesto.consultar(funcion, *args)
    try:
        return funcion(*args)
    except (RuntimeError, subprocess.TimeoutExpired, TimeoutError) as exc:
        if _transitorio_gh(exc):
            raise ConsultaPospuesta() from exc
        raise


def _ver_pr(presupuesto: list[int], gh: GitHubPort, repo: str, numero: int) -> PrView:
    if isinstance(presupuesto, PresupuestoLimpieza):
        return presupuesto.ver_pr(gh, repo, numero)
    return _consulta_gh(presupuesto, gh.ver, repo, numero)


def _credenciales(registro: Registro, enc: Encargo) -> tuple[Recurso, ...]:
    raiz = Path(registro.base) / "ejecucion" / enc.id
    claves = set()
    for intento in raiz.glob("intento-*"):
        generacion = re.fullmatch(r"intento-(\d+)-[\w-]+", intento.name, re.ASCII)
        if (generacion is None or int(generacion[1]) not in enc.incorporado
            or intento.is_symlink() or not (intento / "outcome.json").is_file()):
            continue
        try:
            spec = leer_json(intento / "arranque.json")
        except (OSError, ValueError, TypeError):
            continue
        if not isinstance(spec, dict):
            continue
        token, rol = spec.get("token"), spec.get("rol") or "responsable"
        if (not isinstance(token, str) or not re.fullmatch(r"[\w-]+", token, re.ASCII)
            or not isinstance(rol, str) or not re.fullmatch(r"[\w-]+", rol, re.ASCII)):
            continue
        sesion = Path(registro.base) / "runtime" / enc.id / f"sesion-{rol}"
        if sesion.is_dir() and not (sesion / ".copia-incompleta").exists():
            claves.add(f"{enc.id}/{token}")
    return tuple(Recurso("credenciales", clave) for clave in sorted(claves))


def _ramas(enc: Encargo) -> tuple[Recurso, ...]:
    from ..ciclo import _rama_de_fase

    nombres = {enc.entrega.rama, *(_rama_de_fase(enc, f) for f in enc.fases)}
    return tuple(Recurso("rama", rama) for rama in sorted(nombres)
                 if rama.startswith("encargo/"))


def candidatos(registro: Registro, enc: Encargo,
               presupuesto: list[int] | None = None) -> tuple[Recurso, ...]:
    prs = tuple(Recurso("pr", str(f.pr_numero), f.id)
                for f in enc.fases if f.pr_numero)
    ramas = _ramas(enc)
    credenciales = _credenciales(registro, enc)
    if enc.estado not in ("terminado", "cancelado"):
        return credenciales + prs + ramas
    puntero = Path(registro.base) / "despliegues" / (enc.id + ".actual")
    despliegues = ((Recurso("despliegue", puntero.name),)
                  if puntero.exists() or puntero.is_symlink() else ())
    claves = set(presupuesto.despliegues(enc.id)
                if isinstance(presupuesto, PresupuestoLimpieza)
                else _despliegues(registro, enc))
    claves.update(_claves_de_recibos(registro, enc))
    despliegues += tuple(Recurso("despliegue", k) for k in sorted(claves))
    actual = Path(registro.base) / "ejecucion" / enc.id / "actual"
    puntero_turno = ((Recurso("ejecucion", f"{enc.id}/actual"),)
                    if actual.exists() or actual.is_symlink() else ())
    disco = tuple(Recurso(tipo, enc.id)
                  for tipo in ("runtime", "espacio", "ejecucion"))
    return credenciales + prs + ramas + despliegues + puntero_turno + disco


def _ultimo(enc: Encargo, recurso: Recurso) -> tuple[str, int, str]:
    return next(
        ((resultado, intento, hora) for tipo, clave, resultado, intento, hora
         in enc.limpieza if tipo == recurso.tipo and clave == recurso.clave),
        ("", 0, ""),
    )


def _borrados_anteriores_al_cierre(
    registro: Registro, enc: Encargo,
) -> tuple[int, set[tuple[str, str]]]:
    if enc.estado not in ("terminado", "cancelado") or not any(
        tipo in ("rama", "pr") and resultado == "borrado"
        for tipo, _, resultado, _, _ in enc.limpieza
    ):
        return 0, set()
    estado = None
    cierre = 0
    borrados: dict[tuple[str, str], int] = {}
    for evento in registro.eventos(enc.id):
        anterior = estado.estado if estado is not None else ""
        estado = reducir(estado, evento, nuevo=False)
        if estado is not None and estado.estado in ("terminado", "cancelado") and anterior not in (
            "terminado", "cancelado"
        ):
            cierre = evento["id"]
        if evento["kind"] == "limpieza":
            datos = evento["data"]
            recurso = (datos.get("recurso"), datos.get("clave"))
            if recurso[0] in ("rama", "pr"):
                if datos.get("resultado") == "borrado":
                    borrados[recurso] = evento["id"]
                else:
                    borrados.pop(recurso, None)
    return cierre, {recurso for recurso, evento in borrados.items() if evento < cierre}


def _puede_intentar(enc: Encargo, recurso: Recurso, hora: str) -> bool:
    resultado, intento, anterior = _ultimo(enc, recurso)
    if resultado == "agotado":
        return False
    if resultado != "fallo":
        return True
    antes, ahora = _fecha(anterior), _fecha(hora)
    return (intento < politica.LIMPIEZA.intentos and antes is not None
            and ahora is not None
            and (ahora - antes).total_seconds() >= politica.LIMPIEZA.reintento_s)


def _anotar(registro: Registro, enc: Encargo, recurso: Recurso,
           resultado: str, hora: str, error: Exception | None = None) -> Encargo:
    anterior, intento, _ = _ultimo(enc, recurso)
    siguiente = intento + 1 if anterior != "borrado" else 1
    if resultado == "fallo" and siguiente >= politica.LIMPIEZA.intentos:
        resultado = "agotado"
    datos = {
        "recurso": recurso.tipo, "clave": recurso.clave, "resultado": resultado,
        "intento": siguiente, "hora": hora,
    }
    if error is not None:
        if isinstance(error, PermissionError):
            mensaje = "permiso denegado"
        elif isinstance(error, OSError):
            mensaje = {
                errno.ENOENT: "archivo ausente", errno.ENOSPC: "sin espacio en disco",
                errno.EBUSY: "recurso ocupado",
            }.get(error.errno or -1, "error de disco")
        elif isinstance(error, ValueError):
            mensaje = "metadatos de limpieza no verificables"
        else:
            mensaje = "operación de limpieza rechazada"
        datos.update(error_clase=type(error).__name__,
                     error_mensaje=f"{recurso.tipo}: {mensaje}")
    clave_evento = f"limpieza:{enc.id}:{recurso.tipo}:{recurso.clave}:{siguiente}:{hora}"
    if anterior == "borrado" and recurso.tipo in ("rama", "pr"):
        clave_evento += f":{registro.ultimo_evento(enc.id)}"
    registro.anotar(
        enc.id, {"kind": "limpieza", "data": datos},
        clave_evento,
    )
    return registro.fold(enc.id) or enc


def reintentar_agotados(registro: Registro, ident: str, clave: str, hora: str) -> dict:
    """Rearma solo los recursos agotados; no acredita un borrado que no comprobó."""
    from ..registro import Evento

    enc = registro.fold(ident)
    if enc is None or enc.estado not in ("terminado", "cancelado"):
        return {"encargo": ident, "rechazada": "el encargo no está cerrado"}
    agotados = sorted(
        (tipo, recurso) for tipo, recurso, resultado, _, _ in enc.limpieza
        if resultado == "agotado"
    )
    eventos = tuple(
        Evento("limpieza", {
            "recurso": tipo, "clave": recurso, "resultado": "pendiente",
            "intento": 0, "hora": hora,
        }, f"limpieza-reintentar:{ident}:{clave}:{tipo}:{recurso}")
        for tipo, recurso in agotados
    )
    if any(registro.por_ukey(evento.ukey) is not None for evento in eventos):
        return {"encargo": ident, "rechazada": "la clave de reintento ya se usó"}
    resultados = registro.append_many(ident, eventos)
    nuevos = sum(resultado == "nuevo" for resultado in resultados)
    if nuevos != len(eventos):
        return {"encargo": ident, "reiniciados": nuevos,
                "rechazada": "la clave de reintento ya se usó"}
    return {"encargo": ident, "reiniciados": nuevos}


def _pr(registro: Registro, enc: Encargo, gh: GitHubPort | None,
        recurso: Recurso, hora: str, presupuesto: list[int],
        epoch: float | None = None) -> tuple[Encargo, bool]:
    from .ejecutar import recordar_sondeo, sondeo_pendiente

    if gh is None or not enc.entrega.repo:
        return enc, False
    numero = int(recurso.clave)
    if epoch is not None and not sondeo_pendiente(registro, enc, recurso.fase, epoch):
        observado = next((p.pr for p in enc.prs if p.fase == recurso.fase), None)
        return enc, observado is not None and observado.estado in ("cerrado", "mergeado")
    vista = _ver_pr(presupuesto, gh, enc.entrega.repo, numero)
    if epoch is not None:
        recordar_sondeo(registro, enc, recurso.fase, epoch)
    if vista.estado == "abierto":
        if enc.estado != "cancelado" and recurso.fase not in enc.canceladas:
            return enc, False
        if not presupuesto[0]:
            return enc, False
        vista = _consulta_gh(presupuesto, gh.cerrar_pr, enc.entrega.repo, numero)
        presupuesto[0] -= 1
        if isinstance(presupuesto, PresupuestoLimpieza):
            presupuesto.recordar_pr(enc.entrega.repo, vista)
    enc = anotar_pr(registro, enc, recurso.fase, vista, hora)
    return enc, vista.estado in ("cerrado", "mergeado")


def _rama(enc: Encargo, gh: GitHubPort | None,
          recurso: Recurso, presupuesto: list[int]) -> Cumplimiento:
    from ..ciclo import _rama_de_fase

    if gh is None or not enc.entrega.repo:
        return "pendiente"
    if enc.estado not in ("terminado", "cancelado") and not any(
        fase.pr_numero and _rama_de_fase(enc, fase) == recurso.clave
        for fase in enc.fases
    ):
        return "pendiente"
    if any(
        fase.pr_numero and _ultimo(enc, Recurso("pr", str(fase.pr_numero)))[0] == "agotado"
        for fase in enc.fases
    ):
        return "pendiente"
    if any(p.fase == fase.id and p.pr.estado in ("abierto", "borrador")
           and _rama_de_fase(enc, fase) == recurso.clave
           for fase in enc.fases for p in enc.prs):
        return "pendiente"
    propias_borradas = {
        _rama_de_fase(enc, fase) for fase in enc.fases if fase.pr_numero
        and _ultimo(enc, Recurso("pr", str(fase.pr_numero)))[0] == "borrado"
    }
    propios = tuple(
        vista for fase in enc.fases if fase.pr_numero
        and _ultimo(enc, Recurso("pr", str(fase.pr_numero)))[0] not in ("borrado", "agotado")
        for vista in (_ver_pr(presupuesto, gh, enc.entrega.repo, fase.pr_numero),)
        if vista.rama == recurso.clave
    )
    if any(vista.estado == "abierto" for vista in propios):
        return "pendiente"
    if not _consulta_gh(presupuesto, gh.rama_existe, enc.entrega.repo, recurso.clave):
        return "borrado"
    if _consulta_gh(presupuesto, gh.prs_con_base, enc.entrega.repo, recurso.clave):
        return "conservado"
    if enc.estado != "cancelado" and not propios and recurso.clave not in propias_borradas:
        return "conservado"
    if not presupuesto[0]:
        return "pendiente"
    _consulta_gh(presupuesto, gh.borrar_rama, enc.entrega.repo, recurso.clave)
    presupuesto[0] -= 1
    return ("borrado" if not _consulta_gh(
        presupuesto, gh.rama_existe, enc.entrega.repo, recurso.clave,
    ) else "pendiente")
def _despliegue(registro: Registro, enc: Encargo,
                ruta: Path, presupuesto: list[int]) -> bool:
    if ruta.is_symlink():
        return False
    if (_CLAVE_DESPLIEGUE.fullmatch(ruta.name) and ruta.is_dir()
        and not (ruta / "proceso.json").exists()
        and despliegue._observacion_en(registro, enc, ruta) == "incierto"):
        raise ValueError("trabajador de despliegue sin metadatos verificables")
    if any(despliegue.observacion(registro, enc, f) in ("vivo", "incierto")
           for f in enc.fases):
        return False
    if ruta.name == enc.id + ".actual":
        if despliegue.observacion(registro, enc) in ("vivo", "incierto"):
            return False
        if not presupuesto[0]:
            return False
        borrar_arbol(ruta)
        presupuesto[0] -= 1
        return not ruta.exists() and not ruta.is_symlink()
    puntero = Path(registro.base) / "despliegues" / (enc.id + ".actual")
    if puntero.exists() or puntero.is_symlink():
        return False
    if not ruta.exists():
        return True
    if not ruta.is_dir():
        return False
    try:
        pedido = leer_json(ruta / "pedido.json")
    except (OSError, ValueError, TypeError):
        return False
    identidad = pedido.get("identidad") if isinstance(pedido, dict) else None
    if not isinstance(identidad, dict) or identidad.get("encargo") != enc.id:
        return False
    if all(archivo.name in _CONSERVED for archivo in ruta.iterdir()):
        return True
    if despliegue._observacion_en(registro, enc, ruta) in ("vivo", "incierto"):
        return False
    for archivo in sorted(
        ruta.iterdir(),
        key=lambda p: (
            p.name in ("comando.json", "proceso.json"),
            p.name == "proceso.json", p.name,
        ),
    ):
        if archivo.name in _CONSERVED:
            continue
        if not presupuesto[0]:
            return False
        borrar_arbol(archivo)
        presupuesto[0] -= 1
    return (not puntero.exists() and not puntero.is_symlink()
            and (not ruta.exists() or
                 all(p.name in _CONSERVED for p in ruta.iterdir())))


def _disco(registro: Registro, enc: Encargo, recurso: Recurso,
           hora: str, presupuesto: list[int],
           gh: GitHubPort | None = None) -> Cumplimiento:
    ruta = _ruta(registro, recurso)
    if (recurso.tipo in ("espacio", "ejecucion")
        and not recurso.clave.endswith("/actual")
        and (not _vence(enc, hora) or _sondeo_vigente(enc, hora))):
        return "conservado"
    if not ruta.exists() and not ruta.is_symlink():
        return "borrado"
    if recurso.tipo == "espacio" and enc.entrega.repo and any(
        gh is None or _ver_pr(
            presupuesto, gh, enc.entrega.repo, fase.pr_numero,
        ).estado == "abierto"
        for fase in enc.fases if fase.pr_numero
    ):
        return "conservado"
    if not presupuesto[0]:
        return "pendiente"
    borrar_arbol(ruta)
    presupuesto[0] -= 1
    return "borrado" if not ruta.exists() and not ruta.is_symlink() else "pendiente"


def _recibos_presentes(registro: Registro, enc: Encargo) -> bool:
    raiz = Path(registro.base) / "despliegues"
    return all(
        not fase.desplegado_recibo or (
            (ruta := Path(fase.desplegado_recibo)).name == "recibo.json"
            and ruta.parent.parent == raiz
            and _CLAVE_DESPLIEGUE.fullmatch(ruta.parent.name) is not None
            and not ruta.parent.is_symlink() and not ruta.is_symlink()
            and ruta.is_file()
        )
        for fase in enc.fases
    )


def _restos_tras_limpio(registro: Registro, enc: Encargo, hora: str) -> bool:
    from ..ciclo import _rama_de_fase

    base = Path(registro.base)
    if any(pr.pr.estado in ("abierto", "borrador") for pr in enc.prs):
        return True
    ahora = _fecha(hora)
    if not _vence(enc, hora) and any(
        _ultimo(enc, rama)[0] not in ("borrado", "agotado")
        and (ahora is None
             or (ultimo := _SONDEOS_COMPARTIDOS.get(registro, {}).get(
                 (enc.id, rama.clave)
             )) is None
             or ahora < ultimo
             or (ahora - ultimo).total_seconds() >= politica.LIMPIEZA.reintento_s)
        for rama in _ramas(enc)
        if enc.estado == "cancelado"
        or any(f.pr_numero and _rama_de_fase(enc, f) == rama.clave for f in enc.fases)
    ):
        return True
    rutas = (
        base / "runtime" / enc.id,
        base / "ejecucion" / enc.id / "actual",
        base / "despliegues" / f"{enc.id}.actual",
    )
    if _vence(enc, hora) and not _sondeo_vigente(enc, hora):
        rutas += (base / "espacio" / enc.id, base / "ejecucion" / enc.id)
    if any(ruta.exists() or ruta.is_symlink() for ruta in rutas):
        return True
    try:
        return any(
            any(p.name not in _CONSERVED for p in Path(f.desplegado_recibo).parent.iterdir())
            for f in enc.fases
            if f.desplegado_recibo and Path(f.desplegado_recibo).parent.is_dir()
        )
    except OSError:
        return True


def _limpiar_recurso(registro: Registro, enc: Encargo, gh: GitHubPort | None,
                     recurso: Recurso, hora: str,
                     presupuesto: list[int],
                     epoch: float | None = None) -> tuple[Encargo, Cumplimiento]:
    if recurso.tipo == "pr":
        enc, listo = _pr(registro, enc, gh, recurso, hora, presupuesto, epoch)
        return enc, "borrado" if listo else "pendiente"
    if recurso.tipo == "rama":
        return enc, _rama(enc, gh, recurso, presupuesto)
    if recurso.tipo == "despliegue":
        listo = _despliegue(registro, enc, _ruta(registro, recurso), presupuesto)
        return enc, "borrado" if listo else "pendiente"
    return enc, _disco(registro, enc, recurso, hora, presupuesto, gh)


def barrer_limpieza(registro: Registro, enc: Encargo, gh: GitHubPort | None,
                    hora: str, presupuesto: list[int],
                    epoch: float | None = None) -> Encargo:
    from ..ciclo import _cerrar_procesos_del_encargo

    if enc.estado in ("terminado", "cancelado"):
        _cerrar_procesos_del_encargo(registro, enc)
    if leer_parada_local(registro.base) is not None:
        return enc
    cierre, anteriores = _borrados_anteriores_al_cierre(registro, enc)
    marcas = {
        (tipo, clave): (enc.id, f"cierre:{cierre}:{tipo}:{clave}")
        for tipo, clave in anteriores
    }
    sondeos = _SONDEOS_COMPARTIDOS.setdefault(registro, {})
    ahora = _fecha(hora)
    debidos = {
        recurso for recurso, marca in marcas.items()
        if (ultimo := sondeos.get(marca)) is None or ahora is None or ahora < ultimo
        or (not _vence(enc, hora)
            and (ahora - ultimo).total_seconds() >= politica.LIMPIEZA.reintento_s)
    }
    if (enc.estado in ("terminado", "cancelado") and enc.limpio and not debidos
        and not any(tipo == "pr" for tipo, _ in anteriores)
        and not _restos_tras_limpio(registro, enc, hora)):
        return enc
    if enc.reserva:
        return enc
    if enc.proc is not None:
        try:
            if plataforma.estado_proc(enc.proc.como_dict()) not in ("muerto", "corte"):
                return enc
        except (OSError, TypeError, ValueError, KeyError):
            return enc
    recursos = candidatos(registro, enc, presupuesto)
    faltan = False
    for recurso in recursos:
        resultado, _, _ = _ultimo(enc, recurso)
        comprobar = (recurso.tipo, recurso.clave) in anteriores
        if resultado == "borrado" and recurso.tipo in ("rama", "pr") and not comprobar:
            continue
        if comprobar and (recurso.tipo, recurso.clave) not in debidos:
            if recurso.tipo == "pr":
                faltan = True
            continue
        if not _puede_intentar(enc, recurso, hora):
            faltan = True
            continue
        restantes = presupuesto[0]
        try:
            enc, estado = _limpiar_recurso(
                registro, enc, gh, recurso, hora, presupuesto,
                None if comprobar and recurso.tipo == "pr" else epoch,
            )
        except ConsultaPospuesta:
            faltan = True
            continue
        except (OSError, RuntimeError, ValueError, subprocess.TimeoutExpired) as exc:
            enc = _anotar(registro, enc, recurso, "fallo", hora, exc)
            faltan = True
            continue
        if recurso.tipo == "rama" and estado == "conservado" and (ahora := _fecha(hora)):
            sondeos[(enc.id, recurso.clave)] = ahora
            if comprobar:
                sondeos[marcas[(recurso.tipo, recurso.clave)]] = ahora
        if (recurso.tipo == "pr" and estado == "pendiente" and comprobar
            and gh is not None and enc.entrega.repo and (ahora := _fecha(hora))):
            sondeos[marcas[(recurso.tipo, recurso.clave)]] = ahora
        if estado == "pendiente":
            faltan = True
        elif estado == "borrado" and (
            comprobar or resultado != "borrado" or presupuesto[0] < restantes
        ):
            enc = _anotar(registro, enc, recurso, "borrado", hora)
    if (enc.estado in ("terminado", "cancelado") and not faltan and not enc.limpio
        and _recibos_presentes(registro, enc)
        and all(pr.pr.estado not in ("abierto", "borrador") for pr in enc.prs)):
        registro.anotar(enc.id, {"kind": "limpio", "data": {"hora": hora}},
                       f"limpio:{enc.id}:{enc.hora_final}:{registro.ultimo_evento(enc.id)}")
        enc = registro.fold(enc.id) or enc
    return enc
