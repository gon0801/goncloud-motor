"""Identidad y oportunidad de los avisos de la entrega v4."""

from __future__ import annotations

import hashlib
import re
from typing import TYPE_CHECKING, Protocol

from ..dominio import dependencia_bloqueada
from ..reporte import Reporte, reporte

if TYPE_CHECKING:
    from ..dominio import Encargo
    from ..registro import Registro


class AvisosGuardados(Protocol):
    def get(self, ident: str) -> dict | None: ...
    def archivado(self, ident: str) -> dict | None: ...


def id_aviso(encargo: str, evidencia: str) -> str:
    return "av_" + hashlib.sha256(f"{encargo}:{evidencia}".encode()).hexdigest()[:16]


def aviso_de(enc: Encargo) -> dict:
    if enc.estado == "cancelado":
        evidencia = "cierre:cancelado"
    else:
        evidencia = enc.evidencia[-1] if enc.evidencia else f"cierre:{enc.estado}"
    return {
        "id": id_aviso(enc.id, evidencia),
        "encargo": enc.id,
        "canal": enc.canal,
        "evidencia": evidencia,
        "estado": "pendiente",
        "recibo": "",
        "entrega": {
            "nivel_autorizado": enc.entrega.nivel,
            "nivel_alcanzado": enc.nivel_alcanzado,
            "repo": enc.entrega.repo,
            "pr_numero": enc.pr_numero,
            "pr_sha": enc.pr_sha,
        },
    }


def titulo_del_pedido(texto: str, clave: str) -> str:
    for linea in str(texto or "").splitlines():
        encontrado = re.match(r"#{1,6}\s+(.+?)\s*$", linea)
        if encontrado:
            return encontrado.group(1).strip()
    return clave


def entrega_parcial_llana(enc: Encargo, bloqueadas: list) -> str:
    fases = enc.fases
    listas = [f for f in fases if f.estado == "preparada"]
    partes = [f"Entrega parcial: {len(listas)} fases listas"]
    for fase in bloqueadas:
        causa = " ".join(str(fase.bloqueo_causa or "").split())
        causa = causa.split(". ")[0] if causa else "sin causa registrada"
        partes.append(f"la fase {fase.id} quedó bloqueada: {causa[:160]}")
    for fase in fases:
        if fase.estado in ("preparada", "bloqueada"):
            continue
        condena = dependencia_bloqueada(fases, fase)
        partes.append(
            f"la fase {fase.id} no se ejecutó: dependía de la fase {condena}, "
            "que quedó bloqueada"
        )
    return ". ".join(partes) + "."


def _detencion_nueva(registro: Registro, enc: Encargo, corte: int) -> int:
    if enc.estado != "detenido":
        return 0
    try:
        for evento in reversed(registro.eventos(enc.id)):
            if int(evento["id"]) <= corte:
                break
            if evento["kind"] in ("pr_otra_base", "fallo_interno"):
                return int(evento["id"])
            if (evento["kind"] == "orden" and isinstance(evento["data"], dict)
                and evento["data"].get("accion") == "parar"):
                return int(evento["id"])
    except (OSError, KeyError, TypeError, ValueError):
        return 0
    return 0


def trabajos_nuevos(registro: Registro, enc: Encargo, corte: int,
                    vista: AvisosGuardados, limpieza_activa: bool
                    ) -> tuple[tuple[dict, Reporte], ...]:
    al_corte = registro.fold(enc.id, hasta=corte)
    rep = reporte(enc, limpieza_activa)
    if rep is None:
        return ()
    detencion = _detencion_nueva(registro, enc, corte)
    if detencion:
        aviso = aviso_de(enc)
        aviso.update(
            id=id_aviso(enc.id, f"detenido:{detencion}"),
            tipo="detenido", palabra=rep.palabra,
        )
        return ((aviso, rep),)
    if enc.estado not in ("terminado", "cancelado") or (
        al_corte is not None and al_corte.estado in ("terminado", "cancelado")
    ):
        return ()
    cierre_id = id_aviso(enc.id, "cierre:palabras-v4")
    anterior = vista.get(cierre_id) or vista.archivado(cierre_id)
    agotado = any(resultado == "agotado" for _, _, resultado, _, _ in enc.limpieza)
    if anterior is None:
        cierre_anterior = aviso_de(enc)["id"]
        if vista.get(cierre_anterior) or vista.archivado(cierre_anterior):
            return ()
        if limpieza_activa:
            if rep.palabra == "falta limpiar" and not agotado:
                return ()
            if not (enc.limpio or agotado or rep.palabra == "listo para tu merge"):
                return ()
        aviso = aviso_de(enc)
        aviso.update(id=cierre_id, tipo="cierre-v4", palabra=rep.palabra)
        return ((aviso, rep),)
    if anterior.get("estado") in ("local-pendiente", "pendiente"):
        aviso = aviso_de(enc)
        aviso.update(
            id=cierre_id, tipo="cierre-v4",
            palabra=rep.palabra if anterior.get("estado") == "local-pendiente"
            else anterior.get("palabra") or rep.palabra,
        )
        return ((aviso, rep),)
    if (anterior.get("estado") != "publicado" or not anterior.get("recibo")
        or anterior.get("palabra") == rep.palabra
        or rep.palabra == "falta limpiar" and not agotado):
        return ()
    aviso = aviso_de(enc)
    aviso.update(
        id=id_aviso(enc.id, f"palabra:{rep.palabra}"),
        tipo="palabra-v4", palabra=rep.palabra,
    )
    return ((aviso, rep),)
