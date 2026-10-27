"""Pedido, procedimiento, intento y efecto son registros distintos.

El reducer no llama a parse_plan ni a machine.estado. Un texto del agente
no es un resultado comprobado.
"""

from __future__ import annotations

import hashlib
import socket
from dataclasses import dataclass, replace

ESTADOS = (
    "recibido",
    "ejecutando",
    "esperando_reintento",
    "conciliando",
    "necesita_usuario",
    "deteniendo",
    "detenido",
    "cancelado",
    "terminado",
)

_CERRADOS = ("deteniendo", "detenido", "cancelado", "terminado")


class TransicionIlegal(RuntimeError):
    pass


@dataclass(frozen=True)
class Proc:
    pid: int
    inicio: str
    boot_id: str
    job: str = ""
    host: str = ""

    def como_dict(self) -> dict:
        return {
            "pid": self.pid,
            "inicio": self.inicio,
            "boot_id": self.boot_id,
            "job": self.job,
            "host": self.host,
        }


@dataclass(frozen=True)
class Efecto:
    id: str
    destino: str
    operacion: str
    clave: str
    estado: str
    recibo: str = ""
    observacion: str = ""
    cuerpo: str = ""


@dataclass(frozen=True)
class PausaAgente:
    agente: str
    hasta: str
    causa: str
    relevo: bool = True


@dataclass(frozen=True)
class Encargo:
    id: str
    clave: str
    huella: str
    director: str
    texto: str
    adjuntos: tuple[str, ...]
    restricciones: tuple[str, ...]
    canal: str
    ajustes: tuple[str, ...] = ()
    procedimiento: tuple[str, ...] = ()
    estado: str = "recibido"
    ultimo_hecho: str = ""
    hora: str = ""
    motivo: str = ""
    siguiente: str = ""
    preguntas: tuple[str, ...] = ()
    pendientes: tuple[str, ...] = ()
    intento: str = ""
    proc: Proc | None = None
    herramienta: str = ""
    causa: str = ""
    estrategia: str = "investigar"
    firma_fallo: str = ""
    repeticiones: int = 0
    reanuda_en: str = ""
    escrituras: bool = True
    intentos_nuevos: bool = True
    evidencia: tuple[str, ...] = ()
    efectos: tuple[Efecto, ...] = ()
    autoridad: bool = True
    sesion: str = ""
    herramientas_observables: bool = True
    reserva: bool = False
    rol: str = ""
    generacion: int = 0
    conductor: str = ""
    candidato: str = ""
    host: str = ""
    agentes: tuple[str, ...] = ()
    agente: str = ""
    pausas: tuple[PausaAgente, ...] = ()
    via: str = "durable"


def huella_entrega(
    texto: str, adjuntos: tuple[str, ...], restricciones: tuple[str, ...]
) -> str:
    h = hashlib.sha256()
    h.update(texto.encode())
    h.update(b"\0")
    for parte in adjuntos + restricciones:
        h.update(parte.encode())
        h.update(b"\0")
    return h.hexdigest()


def id_encargo(clave: str, huella: str) -> str:
    return "enc_" + hashlib.sha256(f"{clave}:{huella}".encode()).hexdigest()[:16]


def recibo_de(clave: str) -> str:
    return "rec_" + hashlib.sha256(clave.encode()).hexdigest()[:12]


def _efecto(e: Encargo, nuevo: Efecto) -> tuple[Efecto, ...]:
    return tuple(x for x in e.efectos if x.id != nuevo.id) + (nuevo,)


def efecto_de(e: Encargo, efecto_id: str) -> Efecto | None:
    for x in e.efectos:
        if x.id == efecto_id:
            return x
    return None


def reducir(estado: Encargo | None, evento: dict) -> Encargo:
    kind = evento["kind"]
    d = evento["data"]
    hora = str(d.get("hora") or "")
    if kind == "entrega":
        if estado is not None:
            raise TransicionIlegal("el encargo ya existe")
        via = d.get("via") or "durable"
        if via not in ("directa", "durable"):
            raise TransicionIlegal("via desconocida")
        return Encargo(
            id=d["id"],
            clave=d["clave"],
            huella=d["huella"],
            director=d.get("director") or "",
            texto=d["texto"],
            adjuntos=tuple(d.get("adjuntos") or ()),
            restricciones=tuple(d.get("restricciones") or ()),
            canal=d.get("canal") or "",
            ultimo_hecho="pedido recibido",
            hora=hora,
            siguiente=(
                "lo atienden los agentes del director"
                if via == "directa"
                else "investigar y comprobar el pedido"
            ),
            pendientes=tuple(d.get("pendientes") or ("comprobar el pedido",)),
            via=via,
        )
    if estado is None:
        raise TransicionIlegal("no hay encargo")
    e = estado
    if kind == "recibo":
        return e
    if kind == "ruta_agentes":
        return replace(e, agentes=tuple(d["agentes"]))
    if kind == "proveedor_fallo":
        pausa = PausaAgente(d["agente"], d["hasta"], d["causa"], d["relevo"])
        return replace(
            e,
            pausas=tuple(p for p in e.pausas if p.agente != pausa.agente) + (pausa,),
            causa=d["causa"],
        )
    if kind == "proveedor_disponible":
        return replace(e, pausas=tuple(p for p in e.pausas if p.agente != d["agente"]))
    if kind == "orden":
        return _orden(e, d, hora)
    if kind == "ajuste":
        if e.estado != "detenido":
            raise TransicionIlegal("el ajuste espera el estado detenido")
        return replace(
            e,
            ajustes=e.ajustes + (d["texto"],),
            hora=hora,
            ultimo_hecho="ajuste incorporado con el trabajo detenido",
            siguiente="seguir detenido hasta continuar",
        )
    if kind == "procedimiento":
        return replace(
            e,
            procedimiento=e.procedimiento + (d["nota"],),
            hora=hora or e.hora,
        )
    if kind == "reserva":
        if e.proc is not None or e.reserva:
            raise TransicionIlegal("ya hay un escritor")
        if (
            not e.intentos_nuevos
            or e.estado in _CERRADOS
            or e.estado == "esperando_reintento"
        ):
            raise TransicionIlegal("no hay autorización para otro intento")
        return replace(
            e,
            reserva=True,
            conductor=str(d.get("conductor") or e.conductor),
            candidato=str(d.get("candidato") or ""),
            host=str(d.get("host") or socket.gethostname()),
            rol=str(d.get("rol") or e.rol or "responsable"),
            generacion=int(d.get("generacion") or (e.generacion + 1)),
            estado="ejecutando" if e.estado == "recibido" else e.estado,
            ultimo_hecho=e.ultimo_hecho or "intento reservado",
            hora=hora or e.hora,
        )
    if kind == "reserva_cerrada":
        return replace(e, reserva=False, candidato="", hora=hora or e.hora)
    if kind == "conductor":
        return replace(
            e, conductor=d["token"], candidato="", host=d["host"], hora=hora or e.hora
        )
    if kind == "concesion":
        return replace(e, hora=hora or e.hora)
    if kind == "intento_abierto":
        if not e.intentos_nuevos or e.estado in _CERRADOS:
            raise TransicionIlegal("no hay autorización para otro intento")
        if e.proc is not None:
            raise TransicionIlegal("ya hay un escritor")
        return replace(
            e,
            reserva=False,
            rol=str(d.get("rol") or e.rol),
            agente=str(d.get("agente") or e.agente),
            intento=d["id"],
            sesion=d.get("sesion") or e.sesion,
            proc=Proc(
                pid=int(d["pid"]),
                inicio=str(d["inicio"]),
                boot_id=str(d.get("boot_id") or ""),
                job=str(d.get("job") or ""),
                host=str(d.get("host") or socket.gethostname()),
            ),
            herramientas_observables=bool(d.get("herramientas_observables", True)),
            hora=hora or e.hora,
            ultimo_hecho=e.ultimo_hecho or "intento abierto",
        )
    if kind == "actividad":
        if e.estado in _CERRADOS or e.estado == "esperando_reintento":
            return e
        if not d.get("observada"):
            return e
        return replace(
            e,
            estado="ejecutando",
            ultimo_hecho=d["hecho"],
            hora=hora or e.hora,
            herramienta=str(d.get("herramienta") or ""),
            motivo="",
        )
    if kind == "colgada":
        return replace(
            e,
            hora=hora or e.hora,
            ultimo_hecho=d["hecho"],
            motivo="sesión colgada",
            siguiente="cerrar el escritor y no abrir otro hasta verlo ausente",
            causa=d.get("causa") or e.causa,
        )
    if kind == "intento_cerrado":
        return replace(e, proc=None, herramienta="", reserva=False)
    if kind == "arbol":
        return _arbol(e, d, hora)
    if kind == "comprobacion":
        return _comprobacion(e, d, hora)
    if kind == "afirmacion":
        return replace(
            e, procedimiento=e.procedimiento + (f"afirmación: {d['texto']}",)
        )
    if kind == "efecto":
        return _efecto_evento(e, d, hora)
    if kind == "plazo":
        if e.estado != "esperando_reintento":
            return e
        return replace(
            e,
            estado="recibido",
            motivo="",
            reanuda_en="",
            siguiente="seguir después de la espera",
            ultimo_hecho=f"terminó la espera: {e.motivo}",
            hora=hora or e.hora,
        )
    if kind == "espera":
        if not d.get("motivo") or not d.get("reanuda_en"):
            raise TransicionIlegal("la espera necesita motivo y reanudación")
        return replace(
            e,
            estado="esperando_reintento",
            motivo=d["motivo"],
            reanuda_en=d["reanuda_en"],
            siguiente=f"reanudar en {d['reanuda_en']}",
            ultimo_hecho=d["motivo"],
            hora=hora or e.hora,
        )
    if kind == "pregunta":
        bloquea = bool(d.get("bloquea_todo"))
        return replace(
            e,
            preguntas=e.preguntas + (d["texto"],),
            estado="necesita_usuario" if bloquea else e.estado,
            motivo=d["texto"] if bloquea else e.motivo,
            siguiente="hace falta una decisión del usuario" if bloquea else e.siguiente,
            hora=hora or e.hora,
        )
    if kind == "estrategia":
        return replace(
            e,
            estrategia=d["estrategia"],
            rol=str(d["rol"]) if d.get("rol") else e.rol,
            motivo=d["motivo"],
            siguiente=d["siguiente"],
            hora=hora or e.hora,
            ultimo_hecho=d["motivo"],
        )
    if kind == "autoridad":
        alcanzable = bool(d["alcanzable"])
        puede = (
            alcanzable
            and e.intentos_nuevos
            and e.estado not in ("deteniendo", "detenido", "cancelado", "terminado")
        )
        return replace(e, autoridad=alcanzable, escrituras=puede)
    if kind == "via":
        via = d.get("via")
        if via not in ("directa", "durable"):
            raise TransicionIlegal("via desconocida")
        if e.estado in ("cancelado", "terminado"):
            raise TransicionIlegal("el encargo ya cerró")
        if e.proc is not None or e.reserva:
            raise TransicionIlegal("el ejecutor anterior sigue abierto")
        if via == e.via:
            return e
        return replace(
            e,
            via=via,
            hora=hora or e.hora,
            ultimo_hecho="via durable" if via == "durable" else "via directa",
            siguiente=(
                "el supervisor puede tomar el encargo"
                if via == "durable"
                else "lo atienden los agentes del director"
            ),
        )
    if kind == "resultado_directo":
        if e.via != "directa":
            raise TransicionIlegal("el resultado directo solo cierra la via directa")
        if e.proc is not None or e.reserva:
            raise TransicionIlegal("hay un escritor abierto")
        if e.estado in ("cancelado", "terminado"):
            raise TransicionIlegal("el encargo ya cerró")
        evidencia = str(d.get("evidencia") or "")
        if not evidencia:
            raise TransicionIlegal("terminado sin evidencia")
        return replace(
            e,
            estado="terminado",
            evidencia=e.evidencia + (evidencia,),
            intentos_nuevos=False,
            escrituras=False,
            ultimo_hecho="resultado directo comprobado",
            siguiente="publicado al canal de origen",
            hora=hora or e.hora,
            motivo="",
        )
    raise TransicionIlegal(f"evento desconocido: {kind}")


def _orden(e: Encargo, d: dict, hora: str) -> Encargo:
    accion = d["accion"]
    if accion == "parar":
        if e.estado in ("cancelado", "terminado"):
            raise TransicionIlegal(f"no se para un encargo {e.estado}")
        return replace(
            e,
            estado="deteniendo",
            escrituras=False,
            intentos_nuevos=False,
            motivo="parada aceptada",
            siguiente="cerrar procesos y no despachar escrituras",
            ultimo_hecho="se aceptó parar",
            hora=hora or e.hora,
        )
    if accion == "cancelar":
        if e.estado == "terminado":
            raise TransicionIlegal("el encargo ya terminó")
        return replace(
            e,
            estado="deteniendo",
            escrituras=False,
            intentos_nuevos=False,
            motivo="cancelado",
            siguiente="cerrar procesos; no deshace efectos ya aplicados",
            ultimo_hecho="se aceptó cancelar",
            hora=hora or e.hora,
        )
    if accion == "continuar":
        if e.estado != "detenido":
            raise TransicionIlegal("continuar espera el estado detenido")
        return replace(
            e,
            estado="recibido",
            intentos_nuevos=True,
            escrituras=e.autoridad,
            motivo="",
            siguiente="reanudar el mismo encargo",
            ultimo_hecho="se continuó el mismo encargo",
            hora=hora or e.hora,
            proc=None,
            herramienta="",
        )
    raise TransicionIlegal(f"orden desconocida: {accion}")


def _arbol(e: Encargo, d: dict, hora: str) -> Encargo:
    if d.get("observacion") != "ausente" or e.estado != "deteniendo" or e.reserva:
        return e
    estado = "cancelado" if e.motivo == "cancelado" else "detenido"
    return replace(
        e,
        estado=estado,
        proc=None,
        herramienta="",
        hora=hora or e.hora,
        ultimo_hecho="el árbol de procesos quedó ausente",
        siguiente="ninguna" if estado == "cancelado" else "detenido hasta continuar",
        motivo="" if estado == "detenido" else "cancelado",
    )


def _comprobacion(e: Encargo, d: dict, hora: str) -> Encargo:
    clase = d["clase"]
    if clase == "producto_aprobado":
        return replace(
            e,
            ultimo_hecho="producto aprobado; falta comprobar efectos mediados",
            hora=hora or e.hora,
        )
    if clase in ("confirmado", "cierre_comprobado"):
        evidencia = str(d.get("evidencia") or "")
        if not evidencia:
            raise TransicionIlegal("confirmado sin evidencia")
        evidencias = e.evidencia + (evidencia,)
        if e.estado in ("cancelado", "deteniendo", "detenido"):
            return replace(
                e, evidencia=evidencias, hora=hora or e.hora, ultimo_hecho=evidencia
            )
        return replace(
            e,
            estado="terminado",
            evidencia=evidencias,
            intentos_nuevos=False,
            escrituras=False,
            ultimo_hecho=evidencia,
            hora=hora or e.hora,
            motivo="",
            siguiente="ninguna",
            firma_fallo="",
            repeticiones=0,
        )
    if clase == "instrumento":
        firma = d["firma"]
        rep = e.repeticiones + 1 if firma == e.firma_fallo else 1
        return replace(
            e,
            procedimiento=e.procedimiento + (d["reparacion"],),
            causa=d["causa"],
            firma_fallo=firma,
            repeticiones=rep,
            ultimo_hecho=d["causa"],
            hora=hora or e.hora,
            motivo="instrumento defectuoso",
            siguiente="reparar la comprobación y volver a mirar el producto",
        )
    if clase == "defecto":
        firma = d["firma"]
        rep = e.repeticiones + 1 if firma == e.firma_fallo else 1
        return replace(
            e,
            causa=d["causa"],
            firma_fallo=firma,
            repeticiones=rep,
            ultimo_hecho=d["causa"],
            hora=hora or e.hora,
            motivo="defecto del producto",
            siguiente="corregir el producto y repetir la comprobación",
        )
    if clase == "no_concluyente":
        return replace(
            e,
            motivo=d["motivo"],
            ultimo_hecho=d["motivo"],
            hora=hora or e.hora,
            siguiente="volver a observar; no declarar terminado",
        )
    raise TransicionIlegal(f"comprobación desconocida: {clase}")


def _efecto_evento(e: Encargo, d: dict, hora: str) -> Encargo:
    nuevo = Efecto(
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
    if nuevo.estado == "incierto" and e.estado not in _CERRADOS:
        estado = "conciliando"
        motivo = f"efecto {nuevo.id} sin resultado confirmado"
        siguiente = "consultar el destino antes de repetir"
    if nuevo.estado == "aplicado" and e.estado == "conciliando":
        estado = "recibido"
        motivo = ""
        siguiente = "seguir con el pedido"
    return replace(
        e,
        efectos=_efecto(e, nuevo),
        estado=estado,
        motivo=motivo,
        siguiente=siguiente,
        hora=hora or e.hora,
        ultimo_hecho=nuevo.observacion or e.ultimo_hecho,
    )


def vista(e: Encargo, recibo: str = "") -> dict:
    if e.estado == "esperando_reintento" and (not e.motivo or not e.reanuda_en):
        raise TransicionIlegal("espera incompleta")
    if e.estado == "necesita_usuario" and not e.preguntas:
        raise TransicionIlegal("falta la pregunta")
    if e.estado == "conciliando" and not any(x.estado == "incierto" for x in e.efectos):
        raise TransicionIlegal("conciliando sin efecto incierto")
    if e.estado == "terminado" and not e.evidencia:
        raise TransicionIlegal("terminado sin evidencia")
    if e.estado == "ejecutando" and not e.ultimo_hecho:
        raise TransicionIlegal("ejecutando sin hecho")
    return {
        "encargo": e.id,
        "estado": e.estado,
        "agente": e.agente,
        "agentes_autorizados": list(e.agentes),
        "ultimo_hecho": e.ultimo_hecho,
        "hora": e.hora,
        "motivo": e.motivo,
        "siguiente": e.siguiente,
        "preguntas": list(e.preguntas),
        "recibo": recibo,
        "reanuda_en": e.reanuda_en,
        "estrategia": e.estrategia,
        "escrituras": e.escrituras,
        "via": e.via,
        "director": e.director,
        "canal": e.canal,
        "texto": e.texto,
    }


def decision_fallo(e: Encargo) -> dict | None:
    """El mismo fallo, sin hechos nuevos, no se repite con la misma estrategia."""
    if e.repeticiones >= 2 and e.estrategia != "revision_tecnica":
        return {
            "kind": "estrategia",
            "data": {
                "estrategia": "revision_tecnica",
                "motivo": "el mismo fallo sin hechos nuevos",
                "siguiente": "revision tecnica acotada",
                "hora": e.hora,
            },
        }
    return None


def decision_proceso(
    e: Encargo,
    arbol: str,
    herramienta_viva: bool | None,
    sin_hecho_nuevo: bool,
    herramientas_observables: bool,
    hora: str,
) -> dict:
    """No mira el tamaño de un log. Un árbol incierto no autoriza otro escritor.

    `herramienta_viva is None` significa que no hubo observación. No es lo mismo
    que una herramienta vista y ya terminada.
    """
    if e.estado == "deteniendo":
        if arbol == "ausente":
            return {
                "matar": False,
                "lanzar": False,
                "eventos": [
                    {"kind": "arbol", "data": {"observacion": "ausente", "hora": hora}}
                ],
            }
        if arbol == "vivo":
            return {"matar": True, "lanzar": False, "eventos": []}
        return {"matar": False, "lanzar": False, "eventos": []}
    if e.estado in ("detenido", "cancelado", "terminado", "esperando_reintento"):
        return {"matar": False, "lanzar": False, "eventos": []}
    if e.estado == "necesita_usuario" and not e.pendientes:
        return {"matar": False, "lanzar": False, "eventos": []}
    if arbol == "incierto":
        return {"matar": False, "lanzar": False, "eventos": []}
    if arbol == "vivo":
        if herramienta_viva:
            return {"matar": False, "lanzar": False, "eventos": []}
        if herramienta_viva is None and not herramientas_observables:
            return {"matar": False, "lanzar": False, "eventos": []}
        if sin_hecho_nuevo:
            return {
                "matar": True,
                "lanzar": False,
                "eventos": [
                    {
                        "kind": "colgada",
                        "data": {
                            "hecho": "sin herramienta en curso y sin hecho nuevo",
                            "causa": "sesión sin actividad observable",
                            "hora": hora,
                        },
                    }
                ],
            }
        return {"matar": False, "lanzar": False, "eventos": []}
    eventos = []
    if e.proc is not None:
        eventos.append(
            {
                "kind": "intento_cerrado",
                "data": {"hora": hora, "pid": e.proc.pid, "inicio": e.proc.inicio},
            }
        )
    # Una parada quita intentos_nuevos. La caída de la autoridad quita escrituras
    # y deja seguir el trabajo aislado. La reserva ya comprometida no abre otro proceso.
    lanzar = (
        e.intentos_nuevos
        and e.estado in ("recibido", "ejecutando")
        and e.proc is None
        and not e.reserva
    )
    return {"matar": False, "lanzar": lanzar and not eventos, "eventos": eventos}


def prompt_de(e: Encargo) -> str:
    lineas = [
        (
            "Eres el responsable de este encargo. Sigue poteto-mode en este espacio. "
            "Carga la skill empaquetada del runtime y limita las herramientas al espacio concedido."
        ),
        "El pedido original no se reescribe.",
        f"Pedido:\n{e.texto}",
    ]
    if e.restricciones:
        lineas.append("Restricciones:\n" + "\n".join(e.restricciones))
    if e.ajustes:
        lineas.append(
            "Ajustes incorporados con el trabajo detenido:\n" + "\n".join(e.ajustes)
        )
    if e.efectos:
        hechos = [f"{x.id} {x.estado} {x.recibo}".strip() for x in e.efectos]
        lineas.append("Efectos ya registrados:\n" + "\n".join(hechos))
    if e.evidencia:
        lineas.append("Evidencia:\n" + "\n".join(e.evidencia))
    if e.procedimiento:
        lineas.append(
            "Notas de procedimiento. No sustituyen al pedido ni a los efectos.\n"
            + "\n".join(e.procedimiento)
        )
    if e.preguntas:
        lineas.append("Preguntas pendientes:\n" + "\n".join(e.preguntas))
    if e.causa or e.motivo:
        lineas.append(
            f"Última observación: {e.causa or e.motivo}\nSiguiente trabajo concreto: {e.siguiente}"
        )
    lineas.append(
        "Comprueba el comportamiento pedido y deja la salida en evidencia-comportamiento.txt. "
        "No declares el encargo terminado: el supervisor mira el destino. "
        "Una escritura fuera de este espacio se pide en pedido-efecto.json. "
        "No repitas un efecto que ya tenga recibo."
    )
    return "\n\n".join(lineas)
