"""Aplicación total y validación de los hechos nuevos de entrega."""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING, TypeGuard

from ..encargo import evolucionar
from ..entrega.tipos import (
    EstadoPR, PrAbierto, PrBorrador, PrCerrado, PrDeFase, PrMergeado, PrSin,
)

if TYPE_CHECKING:
    from ..dominio import Encargo, Fase


RECURSOS = frozenset((
    "rama", "pr", "espacio", "ejecucion", "runtime", "credenciales", "despliegue",
))


def _texto(d: dict, clave: str) -> str | None:
    valor = d.get(clave)
    return valor if isinstance(valor, str) and valor.strip() else None


def _numero(valor: object) -> TypeGuard[int]:
    return type(valor) is int and valor > 0


def _fase(e: Encargo, d: dict) -> Fase | None:
    ident = _texto(d, "fase")
    return next((f for f in e.fases if f.id == ident), None)


def _rechazar(motivo: str) -> None:
    from ..dominio import TransicionIlegal

    raise TransicionIlegal(motivo)


def _leer_pr(e: Encargo, d: dict) -> tuple[str, EstadoPR] | None:
    fase = _fase(e, d)
    if fase is None or _texto(d, "hora") is None:
        return None
    estado = d.get("estado")
    numero = d.get("numero")
    if estado == "sin_pr" and numero is None:
        return fase.id, PrSin()
    if not _numero(numero) or not all(
        isinstance(d.get(clave), str) for clave in ("head", "base", "merge_commit")
    ):
        return None
    head, base, commit = d["head"], d["base"], d["merge_commit"]
    if estado == "borrador":
        return fase.id, PrBorrador(numero, head, base)
    if estado == "abierto":
        return fase.id, PrAbierto(numero, head, base)
    if estado == "mergeado":
        return fase.id, PrMergeado(numero, head, base, commit)
    if estado == "cerrado":
        return fase.id, PrCerrado(numero, head, base)
    return None


def validar_pr_observado(e: Encargo, d: dict) -> None:
    if not isinstance(d, dict) or _leer_pr(e, d) is None:
        _rechazar("observación de PR ilegible")


def pr_observado(e: Encargo, d: dict, hora: str, nuevo: bool) -> Encargo:
    datos = _leer_pr(e, d) if isinstance(d, dict) else None
    if datos is None:
        return e
    fase, pr = datos
    vigente = next((x for x in e.prs if x.fase == fase), None)
    observado = PrDeFase(fase, pr, vigente.aceptada_en if vigente else "")
    return replace(
        e, prs=tuple(x for x in e.prs if x.fase != fase) + (observado,),
    )


def _leer_cancelacion(e: Encargo, d: dict) -> tuple[Fase, str, str] | None:
    fase, causa = _fase(e, d), d.get("causa")
    if (fase is None or fase.id in e.canceladas
        or e.estado in ("cancelado", "detenido", "deteniendo")
        or e.proc is not None or e.reserva or _texto(d, "hora") is None):
        return None
    numero, origen = d.get("numero"), d.get("origen")
    if causa == "pr_cerrado" and _numero(numero) and origen == "":
        return fase, f"pr-cerrado:{numero}", f"David cerró el PR #{numero}"
    if causa == "depende" and numero is None and _texto(d, "origen") in e.canceladas:
        return (fase, f"depende:{origen}",
                f"depende de la fase {origen}, que se canceló")
    return None


def validar_fase_cancelada(e: Encargo, d: dict) -> None:
    if not isinstance(d, dict) or _leer_cancelacion(e, d) is None:
        _rechazar("cancelación de fase ilegible")


def fase_cancelada(e: Encargo, d: dict, hora: str, nuevo: bool) -> Encargo:
    from ..dominio import _bloqueo_fase, con_fase

    datos = _leer_cancelacion(e, d) if isinstance(d, dict) else None
    if datos is None:
        return e
    fase, evidencia, causa = datos
    canceladas = e.canceladas + (fase.id,)
    if e.estado not in ("terminado", "cancelado") and len(canceladas) != len(e.fases):
        if e.fase_activa == fase.id:
            bloqueado = _bloqueo_fase(e, {"evidencia": evidencia, "causa": causa}, hora)
            return replace(bloqueado, canceladas=canceladas)
    marcada = con_fase(
        e, replace(fase, estado="bloqueada", evidencia=evidencia,
                   bloqueo_causa=causa, pendientes=tuple(e.pendientes_entrega)),
    )
    if len(canceladas) == len(e.fases):
        return evolucionar(
            marcada, estado="cancelado", canceladas=canceladas,
            intentos_nuevos=False, escrituras=False,
            motivo=causa, ultimo_hecho=evidencia, hora=hora or e.hora,
        )
    return replace(marcada, canceladas=canceladas, hora=hora or e.hora)


def _leer_otra_base(e: Encargo, d: dict) -> tuple[str, str] | None:
    fase, base = _fase(e, d), _texto(d, "base")
    if (fase is None or base is None or not _numero(d.get("numero"))
        or _texto(d, "hora") is None or e.proc is not None or e.reserva
        or e.estado in ("deteniendo", "detenido", "cancelado", "terminado")):
        return None
    return fase.id, base


def validar_pr_otra_base(e: Encargo, d: dict) -> None:
    if not isinstance(d, dict) or _leer_otra_base(e, d) is None:
        _rechazar("otra base de PR ilegible")


def pr_otra_base(e: Encargo, d: dict, hora: str, nuevo: bool) -> Encargo:
    from ..detencion import pr_otra_base as detener

    if not isinstance(d, dict) or _leer_otra_base(e, d) is None:
        return e
    return detener(e, d, hora)


def _leer_limpieza(d: dict) -> tuple[str, str, str, int, str] | None:
    recurso, clave, resultado = d.get("recurso"), _texto(d, "clave"), d.get("resultado")
    intento, hora = d.get("intento"), _texto(d, "hora")
    if (not isinstance(recurso, str) or recurso not in RECURSOS or clave is None
        or resultado not in ("borrado", "fallo", "agotado", "pendiente")
        or (not _numero(intento) and not (resultado == "pendiente"
                                          and type(intento) is int and intento == 0))
        or hora is None):
        return None
    if resultado == "pendiente" and intento != 0:
        return None
    return recurso, clave, resultado, intento, hora


def validar_limpieza(e: Encargo, d: dict) -> None:
    if not isinstance(d, dict) or _leer_limpieza(d) is None:
        _rechazar("limpieza ilegible")
    if d["resultado"] == "pendiente" and not any(
        tipo == d["recurso"] and clave == d["clave"] and resultado == "agotado"
        for tipo, clave, resultado, _, _ in e.limpieza
    ):
        _rechazar("solo un recurso agotado se puede reintentar")
    if "error_clase" in d or "error_mensaje" in d:
        clase, mensaje = d.get("error_clase"), d.get("error_mensaje")
        if (not isinstance(clase, str) or not clase.isidentifier() or len(clase) > 64
            or not isinstance(mensaje, str) or not mensaje.strip()
            or len(mensaje) > 128 or "\n" in mensaje):
            _rechazar("causa de limpieza ilegible")


def limpieza(e: Encargo, d: dict, hora: str, nuevo: bool) -> Encargo:
    datos = _leer_limpieza(d) if isinstance(d, dict) else None
    if datos is None:
        return e
    resultado = replace(
        e, limpieza=tuple(x for x in e.limpieza if x[:2] != datos[:2]) + (datos,),
    )
    return replace(resultado, limpio="") if datos[2] == "pendiente" else resultado


def validar_limpio(e: Encargo, d: dict) -> None:
    if (not isinstance(d, dict) or _texto(d, "hora") is None
        or e.estado not in ("terminado", "cancelado")):
        _rechazar("limpio requiere un encargo final y su hora")


def limpio(e: Encargo, d: dict, hora: str, nuevo: bool) -> Encargo:
    if (not isinstance(d, dict) or e.estado not in ("terminado", "cancelado")
        or _texto(d, "hora") is None):
        return e
    return replace(e, limpio=d["hora"])
