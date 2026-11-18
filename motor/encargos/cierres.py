"""Lecturas de cierre de las esperas por hecho, compartidas por el paso y el barrido.

Con `politica.ESPERAS_V5` encendido, una espera por hecho no se vuelve a mirar
cada pasada: el barrido pregunta aquí si toca leer, llama a la función de la
clase y solo cuando la lectura cierra escribe el evento de cierre. Mientras la
lectura diga «todavía no» o «no se pudo leer», no se escribe nada.

Cada función devuelve `True` si la espera cerró, `False` si todavía no y `None`
si no se pudo leer. El intervalo y el tope salen de `tabla_esperas`; el cuándo
volver a leer vive en memoria del proceso, como los sondeos de la limpieza. Al
reiniciar el supervisor no hay memoria y se lee enseguida.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Callable
from weakref import WeakKeyDictionary

from . import tabla_esperas
from .tabla_esperas import ESPERAS, ClaseEspera

LecturaCierre = Callable[["Contexto"], bool | None]

# (encargo, clase, apertura) -> epoch de la última lectura. El barrido y el paso
# comparten la misma tabla: una espera no se lee dos veces por la misma vuelta.
_SONDEOS: WeakKeyDictionary[object, dict[tuple[str, str, str], float]] = (
    WeakKeyDictionary()
)


@dataclass(frozen=True)
class Contexto:
    """Lo que una lectura de cierre necesita, sin importar el paso que la abre."""

    registro: object
    enc: object
    hora: str
    epoch: float
    gh: object = None
    espacio: object = None
    directorio: object = None
    arbol: str = ""


def olvidar(registro: object, encargo_id: str = "") -> None:
    """Olvida las lecturas de un encargo, o de toda la base, en este proceso."""
    if encargo_id:
        sondeos = _SONDEOS.get(registro, {})
        for clave in [c for c in sondeos if c[0] == encargo_id]:
            sondeos.pop(clave, None)
        return
    _SONDEOS.pop(registro, None)


def _epoch(hora: str) -> float:
    try:
        return datetime.fromisoformat(str(hora).replace("Z", "+00:00")).timestamp()
    except (TypeError, ValueError):
        return 0.0


def toca_mirar(
    registro: object,
    enc: object,
    clase: str,
    epoch: float,
    intervalo_s: int,
) -> bool:
    """¿Pasó el intervalo desde la última lectura de esta espera?

    `intervalo_s == 0` significa cada pasada. Sin memoria previa (arranque del
    supervisor) devuelve `True`: al reiniciar se lee enseguida.
    """
    clave = (str(getattr(enc, "id", "")), clase, str(getattr(enc, "espera_abierta", "")))
    sondeos = _SONDEOS.setdefault(registro, {})
    previo = sondeos.get(clave)
    if intervalo_s > 0 and previo is not None and 0 <= epoch - previo < intervalo_s:
        return False
    sondeos[clave] = epoch
    return True


def cerrar(ctx: Contexto) -> bool | None:
    """La lectura de cierre de la espera vigente.

    Una clase que no es por hecho no tiene lectura propia: el reloj la cierra
    como hoy y aquí se devuelve `True`. Una clase por hecho sin función de
    cierre también deja el camino de hoy.
    """
    clase = str(getattr(ctx.enc, "espera_clase", "") or "")
    fila = ESPERAS.get(clase) if clase in ESPERAS else None
    if fila is None or fila.tipo != "por_hecho":
        return True
    lectura = CIERRES.get(clase)
    if lectura is None:
        return True
    intentos = _intentos_de(ctx.enc)
    intervalo = tabla_esperas.intervalo_s(clase, intentos)
    if not toca_mirar(
        ctx.registro, ctx.enc, clase, ctx.epoch, intervalo
    ):
        return False
    return lectura(ctx)


def _intentos_de(enc: object) -> int:
    """El intento de la espera, para las escaleras: `espera_tope` lo dice."""
    tope = str(getattr(enc, "espera_tope", "") or "")
    if tope.endswith(" intentos"):
        try:
            return max(1, int(tope.split()[0]))
        except (TypeError, ValueError):
            return 1
    return 1


# --------------------------------------------------------------------------
# Una lectura por clase por hecho.
# --------------------------------------------------------------------------


def _revision_deepseek(ctx: Contexto) -> bool | None:
    """Cierra con un comentario de DeepSeek en el SHA, o con su sticky de timeout.

    Antes del tope hay una acción propia: el disparo del revisor a los dos
    minutos del primer envío. Se ejecuta aunque la lectura diga «todavía no», y
    escribe su evento una sola vez.
    """
    from .ciclo import _accion_deepseek_si_toca, _leer_deepseek
    from .lectura import Error as ErrorLectura, Pendiente as PendienteLectura

    enc, gh = ctx.enc, ctx.gh
    fase = getattr(enc, "fase_actual", None)
    if gh is None or fase is None or not fase.pr_sha:
        return None
    sha = str(fase.pr_sha)
    primera = next(
        (t for s, t in getattr(enc, "primer_envio_por_sha", ()) if s == sha),
        ctx.hora,
    )
    lectura = _leer_deepseek(gh, enc.entrega.repo, fase.pr_numero, sha, primera)
    if isinstance(lectura, ErrorLectura):
        return None
    if isinstance(lectura, PendienteLectura):
        _accion_deepseek_si_toca(
            ctx.registro, enc, gh, ctx.espacio, sha, primera, ctx.hora
        )
        return False
    _leidos, comentarios = lectura.datos if isinstance(lectura.datos, tuple) else ([], [])
    # Los avisos de timeout viajan en `leidos` y no en `comentarios`: el paso
    # los trata como ausencia y omite en la misma vuelta.
    return True if (comentarios or (_leidos and not comentarios)) else False


def _lectura_deepseek_fallida(ctx: Contexto) -> bool | None:
    """Cierra cuando la lectura de DeepSeek vuelve a salir, aunque sea vacía."""
    from .ciclo import _leer_deepseek
    from .lectura import Error as ErrorLectura

    enc, gh = ctx.enc, ctx.gh
    fase = getattr(enc, "fase_actual", None)
    if gh is None or fase is None or not fase.pr_sha:
        return None
    sha = str(fase.pr_sha)
    primera = next(
        (t for s, t in getattr(enc, "primer_envio_por_sha", ()) if s == sha),
        ctx.hora,
    )
    lectura = _leer_deepseek(gh, enc.entrega.repo, fase.pr_numero, sha, primera)
    return None if isinstance(lectura, ErrorLectura) else True


def _checks_merge(ctx: Contexto) -> bool | None:
    """Cierra cuando los checks requeridos dejan de estar en marcha."""
    enc, gh = ctx.enc, ctx.gh
    if gh is None or not getattr(enc, "pr_numero", 0) or not getattr(enc, "pr_sha", ""):
        return None
    try:
        checks = gh.checks_requeridos(enc.entrega.repo, enc.pr_numero, enc.pr_sha)
    except Exception:  # noqa: BLE001  una lectura ilegible no es un verde
        return None
    return not bool(getattr(checks, "en_marcha", ()))


def _despliegue_en_curso(ctx: Contexto) -> bool | None:
    """Cierra cuando el trabajador de la receta deja de estar vivo."""
    from . import despliegue

    try:
        return despliegue.observacion(ctx.registro, ctx.enc) != "vivo"
    except (OSError, ValueError, KeyError, TypeError):
        return None


def _despliegue_sin_receta(ctx: Contexto) -> bool | None:
    """Cierra cuando la fase ya declara si despliega y con qué receta."""
    from . import politica_despliegue

    if ctx.espacio is None:
        return None
    try:
        resuelta = politica_despliegue.resolver(ctx.espacio, ctx.enc)
    except (OSError, ValueError, KeyError, TypeError):
        return None
    return resuelta.requiere_despliegue is not None


def _autoridad(ctx: Contexto) -> bool | None:
    """Cierra cuando la autoridad vuelve. `_mirar_autoridad` ya la refrescó."""
    return bool(getattr(ctx.enc, "autoridad", True))


def _observacion_comprobador(ctx: Contexto) -> bool | None:
    """Cierra cuando llega la observación del comprobador para la versión."""
    from .ciclo import _observacion_de_la_version
    from .comprobacion import (
        huella_procedimiento, interno_de, leer_declaradas, version_producto,
    )

    if ctx.espacio is None or ctx.directorio is None:
        return None
    try:
        declaradas = leer_declaradas(interno_de(ctx.espacio))
        if declaradas is None:
            return None
        version = version_producto(ctx.espacio).firma
        procedimiento = huella_procedimiento(ctx.espacio, declaradas)
        return _observacion_de_la_version(
            ctx.registro, ctx.enc, version, declaradas, ctx.directorio,
            procedimiento, getattr(ctx.enc, "fase_activa", None),
        ) is not None
    except (OSError, ValueError, KeyError, TypeError):
        return None


def _efecto_incierto(ctx: Contexto) -> bool | None:
    """Cierra cuando el efecto incierto ya figura aplicado en el destino.

    Con autoridad configurada el destino no se consulta desde aquí: la
    conciliación la hace el paso cuando la espera se cierra por su tope. Se
    devuelve `None` para no inventar un cierre que no se pudo leer.
    """
    from .frontera import DestinoEnsayo
    from .transporte import ConfigAutoridadInvalida, cargar

    pendientes = [
        e for e in getattr(ctx.enc, "efectos", ()) if e.estado == "incierto"
    ]
    if not pendientes:
        return True
    try:
        if cargar(ctx.registro.base) is not None:
            return None
    except (ConfigAutoridadInvalida, OSError, ValueError):
        return None
    destino = DestinoEnsayo(ctx.registro.base / "destino")
    for efecto in pendientes:
        try:
            consulta = destino.consultar(efecto.clave)
        except (OSError, ValueError, KeyError, TypeError):
            return None
        if consulta.get("estado") != "aplicado":
            return False
    return True


def _sin_puerto_github(ctx: Contexto) -> bool | None:
    """Cierra cuando este supervisor ya tiene puerto de GitHub."""
    return ctx.gh is not None


def _cabeza_desviada(ctx: Contexto) -> bool | None:
    """Cierra cuando la cabeza del PR vuelve a ser el SHA entregado."""
    enc, gh = ctx.enc, ctx.gh
    if gh is None or not getattr(enc, "pr_numero", 0):
        return None
    try:
        vista = gh.ver(enc.entrega.repo, enc.pr_numero)
    except Exception:  # noqa: BLE001  una lectura ilegible no es una conciliación
        return None
    return str(getattr(vista, "head", "") or "") == str(getattr(enc, "pr_sha", "") or "")


CIERRES: dict[ClaseEspera, LecturaCierre] = {
    "revision_deepseek": _revision_deepseek,
    "lectura_deepseek_fallida": _lectura_deepseek_fallida,
    "checks_merge": _checks_merge,
    "despliegue_en_curso": _despliegue_en_curso,
    "despliegue_sin_receta": _despliegue_sin_receta,
    "autoridad": _autoridad,
    "observacion_comprobador": _observacion_comprobador,
    "efecto_incierto": _efecto_incierto,
    "sin_puerto_github": _sin_puerto_github,
    "cabeza_desviada": _cabeza_desviada,
}


def clases_por_hecho() -> tuple[str, ...]:
    """Las clases por hecho que tienen lectura de cierre propia."""
    return tuple(CIERRES)
