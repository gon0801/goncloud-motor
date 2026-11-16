"""Escritura de observaciones de PR sobre una fase, sin repetirlas."""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING, Callable

from .tipos import PrSin, estado_de

if TYPE_CHECKING:
    from motor.encargos.dominio import Encargo, Fase
    from motor.encargos.registro import Registro
    from motor.github import GitHubPort, PrView


def anotar_pr(registro: Registro, enc: Encargo, fase: str,
              vista: PrView, hora: str) -> Encargo:
    pr = estado_de(vista)
    anterior = next((x for x in enc.prs if x.fase == fase), None)
    if anterior is not None and anterior.pr == pr:
        return enc
    registro.anotar(
        enc.id,
        {"kind": "pr_observado", "data": {
            "fase": fase, "estado": pr.estado,
            "numero": None if isinstance(pr, PrSin) else pr.numero,
            "head": vista.head, "base": vista.base,
            "merge_commit": vista.merge_commit, "hora": hora,
        }},
        f"pr-observado:{enc.id}:{fase}:{uuid.uuid4().hex}",
    )
    return registro.fold(enc.id) or enc


def preparar_pr(gh: GitHubPort, enc: Encargo, base: str) -> PrView:
    vista = gh.ver(enc.entrega.repo, enc.pr_numero)
    if vista.base != base and vista.estado == "abierto":
        gh.cambiar_base(enc.entrega.repo, enc.pr_numero, base)
        vista = gh.ver(enc.entrega.repo, enc.pr_numero)
    if vista.borrador and vista.estado == "abierto":
        gh.marcar_listo(enc.entrega.repo, enc.pr_numero)
        vista = gh.ver(enc.entrega.repo, enc.pr_numero)
    return vista


def siguiente_fase(enc: Encargo, hora: str, intervalo: float,
                   minutos_desde: Callable[[str, str], float]) -> Fase | None:
    from motor.encargos.dominio import nivel_de

    candidatas = [
        fase for fase in enc.fases
        if fase.id != enc.fase_activa
        and fase.pr_numero
        and nivel_de(fase) == "pr"
        and (not fase.conciliado_en
             or minutos_desde(fase.conciliado_en, hora) >= intervalo)
    ]
    return min(candidatas, key=lambda fase: fase.conciliado_en) if candidatas else None


def anotar_integrado(registro: Registro, enc: Encargo, vista: PrView,
                     hora: str) -> Encargo:
    registro.anotar(
        enc.id,
        {"kind": "entrega_externa", "data": {
            "fase": enc.fase_activa, "intento": enc.intento,
            "paso": "integrado", "repo": enc.entrega.repo,
            "numero": enc.pr_numero, "sha": vista.merge_commit or vista.head,
            "recibo": vista.merge_commit or vista.head, "hora": hora,
        }},
        f"entrega:{enc.id}:{enc.fase_activa}:integrado:{enc.pr_sha}",
    )
    enc = registro.fold(enc.id) or enc
    return anotar_pr(registro, enc, enc.fase_activa, vista, hora)
