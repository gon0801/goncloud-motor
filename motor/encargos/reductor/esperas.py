"""Manejadores del reductor extraídos de dominio."""

from __future__ import annotations
from dataclasses import replace

from ..encargo import evolucionar
from ..politica import KINDS_DE_AVANCE

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..dominio import Encargo


def actualizar_marcas(anterior: Encargo | None, e: Encargo, evento: dict) -> Encargo:
    from ..dominio import impedimento_bloquea_trabajo
    from ..tabla_esperas import ESPERAS

    kind = evento["kind"]
    d = evento["data"]
    hora = str(d.get("hora") or "")
    cambios: dict = {}
    if anterior is None:
        cambios["primer_evento"] = hora
    avance = kind in KINDS_DE_AVANCE and anterior is not None
    if kind == "producto_listo" and avance and anterior is not None:
        fase_id = str(d.get("fase") or e.fase_activa)
        previa = next((f.producto for f in anterior.fases if f.id == fase_id), None)
        actual = next((f.producto for f in e.fases if f.id == fase_id), None)
        avance = actual is not None and (
            previa is None or (actual.firma, actual.sha_git) != (previa.firma, previa.sha_git)
        )
    if avance:
        cambios.update(ultimo_avance=hora, turnos_sin_avance=0,
                       espera_abierta="", racha_esperas_desde="")
    if kind == "intento_abierto":
        cambios["turnos_sin_avance"] = e.turnos_sin_avance + 1
    if kind == "orden" and d.get("accion") == "continuar":
        cambios.update(turnos_sin_avance=0, racha_esperas_desde="",
                       espera_clase="", espera_abierta="", espera_tope="")
    if kind in ("actividad", "reserva", "intento_abierto") or avance:
        cambios["reloj_turno"] = hora

    clase = ""
    if kind == "espera" and e.estado == "esperando_reintento":
        clase = e.espera_clase
    elif e.estado == "deteniendo" and (anterior is None or anterior.estado != "deteniendo"):
        clase = "deteniendo"
    elif kind == "efecto" and e.estado == "conciliando" and (
        anterior is None or anterior.estado != "conciliando"
    ):
        clase = "efecto_incierto"
    elif kind == "impedimento" and impedimento_bloquea_trabajo(e) and e.estado not in (
        "deteniendo", "detenido", "cancelado", "terminado"
    ):
        clase = "impedimento_trabajo"
    elif kind == "autoridad" and not e.autoridad and e.estado not in (
        "deteniendo", "detenido", "cancelado", "terminado"
    ):
        clase = "autoridad"
    if clase and clase != "legada":
        abierta = (
            e.espera_abierta if clase == e.espera_clase and e.espera_abierta
            and not avance else hora
        )
        cambios.update(espera_clase=clase, espera_abierta=abierta)
        if clase != e.espera_clase:
            cambios["espera_tope"] = ""
        if clase == "orden_espera":
            cambios["racha_esperas_desde"] = ""
        elif (clase in ESPERAS and ESPERAS[clase].conducta.al_tope == "detener"
              and not e.racha_esperas_desde):
            cambios["racha_esperas_desde"] = hora
    return replace(e, **cambios) if cambios else e


def incorporado(e: Encargo, d: dict, hora: str, nuevo: bool) -> Encargo:
    from ..dominio import replace
    from .validacion_esperas import leer_incorporado

    # El resultado de un intento ya se incorporó. La generación identifica
    # el intento; la marca sobrevive a la limpieza de su directorio.
    generacion = leer_incorporado(d)
    if generacion is None:
        return e
    marcas = e.incorporado if generacion in e.incorporado else (
        tuple(sorted(e.incorporado + (generacion,)))
    )
    return replace(e, incorporado=marcas)

def quietud_vigilada(e: Encargo, d: dict, hora: str, nuevo: bool) -> Encargo:
    from ..dominio import replace
    from .validacion_esperas import leer_quietud_vigilada

    # Una vuelta del supervisor observó el encargo quieto sobre el evento
    # `evento`. La cuenta vive en el registro; un evento nuevo la estrena.
    lectura = leer_quietud_vigilada(d)
    if lectura is None:
        return e
    return replace(e, quietud_vigilada=lectura)

def sin_progreso(e: Encargo, d: dict, hora: str, nuevo: bool) -> Encargo:
    from ..dominio import _clave_del_sin_progreso, con_escalon, escalon_gastado, replace

    # La escalera se lee de los ids: la clave sale del conjunto abierto (o
    # del pedido del revisor) y los pasos ya gastados se guardan sobre esa
    # clave. No hay contador global que resincronizar.
    rol = str(d.get("rol") or "responsable")
    clave = _clave_del_sin_progreso(e, d, rol)
    diagnostico = str(d.get("diagnostico") or d.get("causa") or "")
    e = replace(e, vuelta_contada=e.generacion)
    if d.get("nueva"):
        # La vuelta trajo bloqueantes nuevos: es información nueva, no «el
        # mismo bloqueo». La clave nueva estrena escalera sola.
        return replace(
            e,
            ultimo_hecho="la vuelta trajo bloqueantes nuevos: vuelven al responsable",
            hora=hora or e.hora,
        )
    if d.get("escalar"):
        # El mismo bloqueo sin información nueva ya no detiene el encargo.
        # Primero pasa el rol a otro modelo autorizado; si el relevo
        # tampoco avanza, se entrega el PR con los pendientes escritos.
        # La escalera es por rol y por clave: un relevo del responsable no
        # deja al revisor sin turno ni fuerza la entrega sin revisar.
        if escalon_gastado(e, rol, "relevo", clave) or escalon_gastado(
            e, rol, "pendientes", clave
        ):
            pendiente = diagnostico or (
                "el bloqueo sigue abierto tras el relevo: " + clave
            )
            return evolucionar(
                con_escalon(e, rol, clave, "pendientes"),
                estado=(
                    "recibido"
                    if e.estado in ("recibido", "ejecutando")
                    else e.estado
                ),
                pendientes_entrega=e.pendientes_entrega + (pendiente,),
                motivo=diagnostico or "el relevo tampoco avanzó en el mismo bloqueo",
                siguiente="entregar el PR con los pendientes abiertos",
                ultimo_hecho=(
                    "el relevo tampoco avanzó: se entrega con pendientes"
                ),
                hora=hora or e.hora,
            )
        return evolucionar(
            con_escalon(e, rol, clave, "relevo"),
            estado=(
                "recibido"
                if e.estado in ("recibido", "ejecutando")
                else e.estado
            ),
            motivo=diagnostico or "el mismo bloqueo sin progreso",
            siguiente="seguir con otro modelo autorizado del mismo rol",
            ultimo_hecho=diagnostico or "se releva a otro modelo del mismo rol",
            hora=hora or e.hora,
        )
    return replace(
        con_escalon(e, rol, clave, "diagnostico"),
        motivo="la corrección no resolvió el bloqueo",
        siguiente="diagnosticar por qué la corrección no resolvió el bloqueo",
        ultimo_hecho="sin progreso: ningun bloqueante cerrado en la vuelta",
        hora=hora or e.hora,
    )

def pendiente_entrega(e: Encargo, d: dict, hora: str, nuevo: bool) -> Encargo:
    from ..dominio import replace

    texto = str(d.get("texto") or "").strip()
    if texto and texto not in e.pendientes_entrega:
        return replace(
            e,
            pendientes_entrega=e.pendientes_entrega + (texto,),
            hora=hora or e.hora,
        )
    return e

def comprobacion(e: Encargo, d: dict, hora: str, nuevo: bool) -> Encargo:
    from ..dominio import _comprobacion

    return _comprobacion(e, d, hora, nuevo)

def afirmacion(e: Encargo, d: dict, hora: str, nuevo: bool) -> Encargo:
    from ..dominio import replace

    if "texto" not in d:
        return e
    return replace(
        e, procedimiento=e.procedimiento + (f"afirmación: {d['texto']}",)
    )

def efecto(e: Encargo, d: dict, hora: str, nuevo: bool) -> Encargo:
    from ..dominio import Efecto, _CERRADOS, _efecto

    if "id" not in d or "estado" not in d:
        return e
    nuevo_efecto = Efecto(
        id=d["id"],
        destino=d.get("destino") or "",
        operacion=d.get("operacion") or "",
        clave=d.get("clave") or "",
        estado=d["estado"],
        recibo=d.get("recibo") or "",
        observacion=d.get("observacion") or "",
        cuerpo=str(d.get("cuerpo") or ""),
    )
    estado = e.estado
    motivo = e.motivo
    siguiente = e.siguiente
    if nuevo_efecto.estado == "incierto" and e.estado not in _CERRADOS:
        estado = "conciliando"
        motivo = f"efecto {nuevo_efecto.id} sin resultado confirmado"
        siguiente = "consultar el destino antes de repetir"
    if nuevo_efecto.estado == "aplicado" and e.estado == "conciliando":
        estado = "recibido"
        motivo = ""
        siguiente = "seguir con el pedido"
    return evolucionar(
        e,
        efectos=_efecto(e, nuevo_efecto),
        estado=estado,
        motivo=motivo,
        siguiente=siguiente,
        hora=hora or e.hora,
        ultimo_hecho=nuevo_efecto.observacion or e.ultimo_hecho,
    )

def plazo(e: Encargo, d: dict, hora: str, nuevo: bool) -> Encargo:
    if e.estado != "esperando_reintento":
        return e
    return evolucionar(
        e,
        estado="recibido",
        motivo="",
        reanuda_en="",
        siguiente="seguir después de la espera",
        ultimo_hecho=f"terminó la espera: {e.motivo}",
        hora=hora or e.hora,
    )

def espera(e: Encargo, d: dict, hora: str, nuevo: bool) -> Encargo:
    from ..dominio import _CERRADOS
    from ..tabla_esperas import ESPERAS

    if not isinstance(d, dict) or e.estado in _CERRADOS:
        return e
    if not d.get("motivo") or not d.get("reanuda_en"):
        return e
    clase = d.get("clase")
    clase = clase if isinstance(clase, str) and clase in ESPERAS else "legada"
    abierta = hora if clase == "legada" else (
        e.espera_abierta if clase == e.espera_clase and e.espera_abierta
        else str(d.get("abierta") or hora)
    )
    return evolucionar(
        e,
        estado="esperando_reintento",
        espera_clase=clase,
        espera_abierta=abierta,
        espera_tope=str(d.get("tope") or "") if clase != "legada" else "",
        motivo=d["motivo"],
        reanuda_en=d["reanuda_en"],
        siguiente=f"reanudar en {d['reanuda_en']}",
        ultimo_hecho=d["motivo"],
        hora=hora or e.hora,
    )


def tope_espera(e: Encargo, d: dict, hora: str, nuevo: bool) -> Encargo:
    from ..detencion import detener_por
    from ..situacion import PorTope
    from .validacion_esperas import leer_tope

    fila = leer_tope(e, d)
    if fila is None:
        return e
    if fila.conducta.al_tope == "avisar":
        return e
    return detener_por(e, hora, PorTope(d["clase"], d["abierta"], d["tope"]))


def falta_de_avance(e: Encargo, d: dict, hora: str, nuevo: bool) -> Encargo:
    from ..detencion import detener_por
    from ..situacion import FaltaDeAvance
    from .validacion_esperas import leer_falta_de_avance

    if not leer_falta_de_avance(e, d):
        return e
    return detener_por(e, hora, FaltaDeAvance(d["regla"], d["desde"], d["turnos"]))

def excepcion(e: Encargo, d: dict, hora: str, nuevo: bool) -> Encargo:
    from ..dominio import replace

    # La excepción del check de merge se deriva del pliegue: tipo, SHA e id
    # con su motivo y hora. El último evento de una misma clave manda.
    tipo = str(d.get("tipo") or "descarte")
    sha = str(d.get("sha") or "")
    hid = str(d.get("id") or "")
    if sha and tipo in ("descarte", "fuera_alcance", "omision"):
        motivo = " ".join(str(d.get("motivo") or "").split())
        clave = (tipo, sha, hid)
        actuales = tuple(
            x for x in e.excepciones if (x[0], x[1], x[2]) != clave
        )
        return replace(
            e,
            excepciones=actuales
            + ((tipo, sha, hid, motivo, str(d.get("hora") or "")),),
            hora=hora or e.hora,
        )
    return replace(e, hora=hora or e.hora)
