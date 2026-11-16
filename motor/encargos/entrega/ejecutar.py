"""Ejecuta las celdas nuevas sin duplicar el flujo de revisión existente."""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING
from weakref import WeakKeyDictionary

from .decision import (
    Accion, AnotarMergeado, CancelarFase, ContextoEntrega,
    Desplegar, DetenerOtraBase, MarcarListo, decidir,
)
from .observacion import anotar_pr
from .tipos import PrSin, estado_de

if TYPE_CHECKING:
    from pathlib import Path

    from motor.encargos.dominio import Encargo, Fase
    from motor.encargos.registro import Registro
    from motor.github import GitHubPort, PrView


_SONDEOS: WeakKeyDictionary[Registro, dict[tuple[str, str, int, int], float]] = WeakKeyDictionary()


def ids_por_sondeo(registro: Registro) -> list[str]:
    from .. import politica

    ids = registro.ids()
    if not politica.ENTREGA_V4:
        return ids
    sondeos = _SONDEOS.get(registro, {})
    return sorted(
        ids,
        key=lambda ident: sondeos.get((ident, "", 0, 0), float("-inf")),
    )


def _efectos_pendientes(registro: Registro, enc: Encargo) -> bool:
    from ..ciclo import _espacio, _pedidos_efecto
    from ..cierre import _recibos
    from ..dominio import nivel_objetivo

    try:
        pedidos = _pedidos_efecto(_espacio(registro.base, enc.id))
        if nivel_objetivo(enc) == "desplegado":
            pedidos = [p for p in pedidos if p["operacion"] != "desplegar"]
        _recibos(pedidos, enc)
    except (ValueError, TypeError, OSError):
        return True
    return any(efecto.estado != "aplicado" for efecto in enc.efectos)


def contexto_de(enc: Encargo, fase: str, vista: PrView) -> ContextoEntrega:
    from ..ciclo import _rama_de_fase, _revision_aprobada_vigente
    from ..dominio import nivel_de, nivel_objetivo

    observadas = {x.fase: x for x in enc.prs}
    otras = tuple(
        (_rama_de_fase(enc, f), observadas[f.id].pr if f.id in observadas else PrSin(),
         observadas[f.id].aceptada_en if f.id in observadas else "")
        for f in enc.fases if f.id != fase
    )
    actual = observadas.get(fase)
    return ContextoEntrega(
        fase=fase, nivel=nivel_objetivo(enc), pr=estado_de(vista),
        base_pedida=enc.entrega.base, otras_ramas=otras,
        aceptada_en=actual.aceptada_en if actual else "",
        nivel_alcanzado=nivel_de(next(f for f in enc.fases if f.id == fase)),
        final=enc.estado in ("terminado", "cancelado"),
        cancelado=enc.estado == "cancelado", canceladas=enc.canceladas,
        proceso_vivo=enc.proc is not None, reserva=bool(enc.reserva),
        revision_lista=_revision_aprobada_vigente(enc),
        otra_base_de=enc.otra_base_de,
    )


def base_objetivo_del_pr(enc: Encargo, gh: GitHubPort | None = None) -> str:
    from ..ciclo import _base_fase
    from ..dominio import nivel_objetivo

    if nivel_objetivo(enc) in ("integrado", "desplegado"):
        return enc.entrega.base
    base = _base_fase(enc)
    if gh is not None and base.startswith("encargo/"):
        try:
            if not gh.rama_existe(enc.entrega.repo, base):
                return enc.entrega.base
        except (RuntimeError, OSError):
            pass
    return base


def esperar_sin_gh(registro: Registro, enc: Encargo, hora: str):
    from ..ciclo import _anotar_instrumento, _con_espera, _reanuda_en, marcar_espera

    causa = (
        "nivel de entrega " + enc.entrega.nivel
        + " sin puerto GitHub configurado en este supervisor"
    )
    _anotar_instrumento(
        registro, enc, "github", causa,
        "configurar el puerto GitHub del supervisor y reintentar",
        hora, "sin-puerto",
    )
    marcar_espera(registro, enc.id, causa, _reanuda_en(300), hora)
    return _con_espera(registro.fold(enc.id) or enc)


def anotar_pendiente(registro: Registro, enc: Encargo,
                    texto: str, hora: str) -> Encargo:
    if enc.pendientes_entrega:
        return enc
    registro.anotar(
        enc.id, {"kind": "pendiente_entrega", "data": {"texto": texto, "hora": hora}},
        f"pendiente-entrega:{enc.id}:{enc.fase_activa}",
    )
    return registro.fold(enc.id) or enc


def _cancelar_cascada(registro: Registro, enc: Encargo, accion: CancelarFase,
                     hora: str) -> Encargo:
    from ..registro import Evento

    nuevas = {accion.fase}
    hechos = [Evento("fase_cancelada", {
        "fase": accion.fase, "numero": accion.numero,
        "causa": "pr_cerrado", "origen": "", "hora": hora,
    }, f"fase-cancelada:{enc.id}:{accion.fase}:{accion.numero}")]
    while True:
        siguientes = [
            (fase, next((dep for dep, _ in fase.requiere if dep in nuevas), ""))
            for fase in enc.fases if fase.id not in nuevas
            and fase.id not in enc.canceladas and fase.estado != "preparada"
            and any(dep in nuevas for dep, _ in fase.requiere)
        ]
        if not siguientes:
            break
        for fase, origen in siguientes:
            hechos.append(Evento("fase_cancelada", {
                "fase": fase.id, "numero": None, "causa": "depende",
                "origen": origen, "hora": hora,
            }, f"fase-cancelada:{enc.id}:{fase.id}:{origen}"))
            nuevas.add(fase.id)
    registro.append_many(enc.id, hechos)
    return registro.fold(enc.id) or enc


def _integrar_david(registro: Registro, enc: Encargo, fase: str,
                   vista: PrView, hora: str) -> Encargo:
    from ..cierre import componer_por_merge
    from ..dominio import nivel_cumplido, nivel_objetivo

    if enc.estado == "terminado":
        return enc
    recibo = vista.merge_commit or vista.head
    if not recibo:
        return enc
    if enc.nivel_alcanzado not in ("integrado", "desplegado"):
        tipo = "entrega_externa" if fase == enc.fase_activa else "conciliacion_fase"
        datos = {
            "fase": fase, "paso": "integrado", "numero": vista.numero,
            "base": vista.base, "sha": recibo, "recibo": recibo, "hora": hora,
        }
        if tipo == "entrega_externa":
            datos["repo"] = enc.entrega.repo
        registro.anotar(
            enc.id, {"kind": tipo, "data": datos},
            f"integrado-david:{enc.id}:{fase}:{recibo}",
        )
        enc = registro.fold(enc.id) or enc
    if fase != enc.fase_activa or nivel_objetivo(enc) == "desplegado" and not nivel_cumplido(enc):
        return enc
    if (nivel_cumplido(enc) and enc.fase_actual is not None
            and enc.fase_actual.estado != "preparada"
            and not _efectos_pendientes(registro, enc)):
        registro.anotar(
            enc.id, componer_por_merge(enc, vista.numero, vista.base, hora),
            f"cierre-david:{enc.id}:{fase}:{vista.numero}:{recibo}",
        )
    return registro.fold(enc.id) or enc


def ejecutar(registro: Registro, enc: Encargo, accion: Accion, vista: PrView,
             fase: str, hora: str, gh: GitHubPort) -> tuple[Encargo, bool]:
    from ..ciclo import _espacio, _paso_despliegue
    from ..dominio import nivel_objetivo

    if isinstance(accion, MarcarListo):
        gh.marcar_listo(enc.entrega.repo, vista.numero)
        return anotar_pr(registro, enc, fase, gh.ver(enc.entrega.repo, vista.numero), hora), True
    if isinstance(accion, CancelarFase):
        return _cancelar_cascada(registro, enc, accion, hora), True
    if isinstance(accion, DetenerOtraBase):
        registro.anotar(
            enc.id, {"kind": "pr_otra_base", "data": {
                "fase": accion.fase, "numero": accion.numero,
                "base": accion.base, "hora": hora,
            }},
            f"pr-otra-base:{enc.id}:{accion.fase}:{accion.numero}:{accion.base}",
        )
        return registro.fold(enc.id) or enc, True
    if isinstance(accion, (AnotarMergeado, Desplegar)):
        enc = _integrar_david(registro, enc, fase, vista, hora)
        if isinstance(accion, Desplegar) or (
            enc.nivel_alcanzado == "integrado" and nivel_objetivo(enc) == "desplegado"
        ):
            enc, _ = _paso_despliegue(registro, enc, _espacio(registro.base, enc.id), hora)
            enc = _integrar_david(registro, enc, fase, vista, hora)
        return enc, not _efectos_pendientes(registro, enc)
    return enc, False


def conducir_pr(registro: Registro, enc: Encargo, gh: GitHubPort,
                hora: str, fase: str = "", epoch: float | None = None) -> tuple[Encargo, bool]:
    if enc.estado == "cancelado":
        return enc, False
    ident = fase or enc.fase_activa
    actual = next((f for f in enc.fases if f.id == ident), None)
    if actual is None or not actual.pr_numero:
        return enc, False
    if epoch is not None:
        clave = (enc.id, ident, actual.pr_numero, enc.generacion)
        sondeos = _SONDEOS.setdefault(registro, {})
        previo = sondeos.get(clave)
        if not fase and previo is not None and 0 <= epoch - previo < 120:
            return enc, False
        sondeos[clave] = epoch
        if fase:
            sondeos[(enc.id, "", 0, 0)] = epoch
    try:
        vista = gh.ver(enc.entrega.repo, actual.pr_numero)
    except Exception:
        return enc, False
    enc = anotar_pr(registro, enc, ident, vista, hora)
    return ejecutar(registro, enc, decidir(contexto_de(enc, ident, vista)),
                    vista, ident, hora, gh)


def fase_final_por_sondear(enc: Encargo) -> str:
    from ..dominio import nivel_objetivo

    if enc.estado != "terminado" or nivel_objetivo(enc) != "pr":
        return ""
    for fase in enc.fases:
        if not fase.pr_numero or fase.id in enc.canceladas:
            continue
        observado = next((x for x in enc.prs if x.fase == fase.id), None)
        if observado is None or observado.pr.estado in ("abierto", "borrador"):
            return fase.id
    return ""


def conciliar_otra_vista(registro: Registro, enc: Encargo, gh: GitHubPort,
                        fase: Fase, vista: PrView, hora: str,
                        espacio: Path, activa_despliega: bool) -> Encargo:
    from .. import politica_despliegue
    from ..ciclo import (
        _anotar_conciliacion, _asegurar_commit_local, _paso_despliegue_fase,
    )
    from ..dominio import nivel_de, nivel_objetivo
    accion = decidir(contexto_de(enc, fase.id, vista))
    if isinstance(accion, (CancelarFase, DetenerOtraBase)):
        enc, _ = ejecutar(registro, enc, accion, vista, fase.id, hora, gh)
        return enc
    if vista.estado != "mergeado" or not isinstance(accion, AnotarMergeado):
        return _anotar_conciliacion(
            registro, enc, fase, "consulta", hora,
            resultado=vista.estado, base=str(vista.base),
        )
    if not (vista.merge_commit or vista.head):
        return _anotar_conciliacion(
            registro, enc, fase, "consulta", hora,
            resultado="mergeado", base=str(vista.base),
        )
    recibo = vista.merge_commit or vista.head
    if nivel_de(fase) == "integrado":
        return enc
    if nivel_objetivo(enc) == "pr":
        return _anotar_conciliacion(
            registro, enc, fase, "integrado", hora,
            sha=recibo, recibo=recibo, numero=fase.pr_numero,
            base=vista.base, resultado="mergeado",
        )
    _asegurar_commit_local(espacio, recibo, enc.entrega.base)
    integrada = replace(fase, integrado_sha=recibo, ultimo_paso_entrega="integrado")
    politica = politica_despliegue.resolver(espacio, enc, integrada)
    extra = {
        "sha": recibo, "recibo": recibo, "numero": fase.pr_numero,
        "base": vista.base, "resultado": "mergeado",
    }
    if politica.requiere_despliegue is not None:
        extra.update(politica.datos())
    enc = _anotar_conciliacion(registro, enc, fase, "integrado", hora, **extra)
    actual = next((f for f in enc.fases if f.id == fase.id), None)
    if (actual is not None and enc.proc is None and not enc.reserva
        and not activa_despliega and nivel_de(actual) == "integrado"
        and actual.estado != "preparada"):
        enc = _paso_despliegue_fase(registro, enc, actual, espacio, hora)
    return enc
