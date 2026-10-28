"""Pedido, procedimiento, intento y efecto son registros distintos.

El reducer no llama a parse_plan ni a machine.estado. Un texto del agente
no es un resultado comprobado.
"""

from __future__ import annotations

import hashlib
import socket
from dataclasses import dataclass, field, replace

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


CRITERIOS = ("seguridad", "datos", "comportamiento", "prueba_exigida", "operativo")
NIVELES = ("terminado", "pr", "integrado", "desplegado")
_ORDEN_NIVEL = {
    "ninguno": 0,
    "terminado": 1,
    "pr_abierto": 2,
    "pr": 3,
    "integrado": 4,
    "desplegado": 5,
}


@dataclass(frozen=True)
class VersionProducto:
    firma: str
    manifiesto: tuple[tuple[str, str], ...]
    sha_git: str = ""

    def como_dict(self) -> dict:
        return {
            "firma": self.firma,
            "manifiesto": [list(x) for x in self.manifiesto],
            "sha_git": self.sha_git,
        }

    @classmethod
    def desde(cls, firma: str, manifiesto, sha_git: str = "") -> "VersionProducto":
        return cls(
            firma=str(firma),
            manifiesto=tuple(
                (str(x[0]), str(x[1])) for x in (manifiesto or ()) if len(x) >= 2
            ),
            sha_git=str(sha_git or ""),
        )


def id_hallazgo(origen: str, ancla: str, titulo: str) -> str:
    normal = " ".join(str(titulo).lower().split())
    return "h_" + hashlib.sha256(f"{origen}|{ancla}|{normal}".encode()).hexdigest()[:8]


@dataclass(frozen=True)
class Hallazgo:
    id: str
    gravedad: str
    criterio: str
    titulo: str
    reproduccion: str = ""
    ancla: str = ""
    origen: str = "independiente"
    estado: str = "abierto"

    def como_dict(self) -> dict:
        return {
            "id": self.id,
            "gravedad": self.gravedad,
            "criterio": self.criterio,
            "titulo": self.titulo,
            "reproduccion": self.reproduccion,
            "ancla": self.ancla,
            "origen": self.origen,
            "estado": self.estado,
        }

    @classmethod
    def desde(cls, d: dict) -> "Hallazgo":
        return cls(
            id=str(d.get("id") or ""),
            gravedad=str(d.get("gravedad") or "observacion"),
            criterio=str(d.get("criterio") or ""),
            titulo=str(d.get("titulo") or ""),
            reproduccion=str(d.get("reproduccion") or ""),
            ancla=str(d.get("ancla") or ""),
            origen=str(d.get("origen") or "independiente"),
            estado=str(d.get("estado") or "abierto"),
        )


@dataclass(frozen=True)
class RefRevision:
    revision_id: str
    version: str
    base_version: str
    alcance: str
    veredicto: str
    revisor: str
    origen: str
    cubre: tuple[str, ...] = ()


@dataclass(frozen=True)
class EntregaAutorizada:
    nivel: str = "terminado"
    repo: str = ""
    rama: str = ""
    base: str = ""


@dataclass(frozen=True)
class AlcanceRevision:
    clase: str
    version: str
    base_version: str
    abiertos: tuple[Hallazgo, ...] = ()
    tocadas: tuple[str, ...] = ()
    conservadas: tuple[str, ...] = ()
    observacion: dict | None = None
    manifiesto: tuple[tuple[str, str], ...] = ()


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
    entrega: EntregaAutorizada = field(default_factory=EntregaAutorizada)
    nivel_alcanzado: str = "ninguno"
    pr_numero: int = 0
    pr_sha: str = ""
    deepseek_sha: str = ""
    producto: VersionProducto | None = None
    revisado: VersionProducto | None = None
    hallazgos: tuple[Hallazgo, ...] = ()
    aprobadas: tuple[tuple[str, str], ...] = ()
    revision_vigente: RefRevision | None = None
    deepseek_vigente: RefRevision | None = None
    modelos: tuple[tuple[str, str, str], ...] = ()
    sesion_responsable: str = ""
    sesion_revisor: str = ""
    epoca_responsable: int = 0
    epoca_revisor: int = 0
    viva_responsable: bool = False
    viva_revisor: bool = False
    sesion_modo: str = ""
    poteto_digest: str = ""
    bloqueo_huella: str = ""
    sin_progreso: int = 0
    observada_version: str = ""


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


def bloqueantes_abiertos(e: Encargo, origen: str = "") -> tuple[Hallazgo, ...]:
    return tuple(
        h
        for h in e.hallazgos
        if h.gravedad == "bloqueante"
        and h.estado == "abierto"
        and (not origen or h.origen == origen)
    )


def _fusionar(
    actuales: tuple[Hallazgo, ...], nuevos: tuple[Hallazgo, ...]
) -> tuple[Hallazgo, ...]:
    por_id = {h.id: h for h in actuales}
    orden = [h.id for h in actuales]
    for h in nuevos:
        if h.id not in por_id:
            orden.append(h.id)
            por_id[h.id] = h
            continue
        previo = por_id[h.id]
        if (
            previo.gravedad == "bloqueante"
            and previo.estado == "abierto"
            and h.gravedad == "observacion"
            and h.estado != "resuelto"
        ):
            continue
        por_id[h.id] = h
    return tuple(por_id[i] for i in orden)


def huella_bloqueo(e: Encargo) -> str:
    ids = sorted(h.id for h in bloqueantes_abiertos(e))
    firma = e.producto.firma if e.producto else ""
    return hashlib.sha256(("|".join(ids) + "|" + firma).encode()).hexdigest()


def _con_progreso(e: Encargo) -> Encargo:
    huella = huella_bloqueo(e)
    if huella != e.bloqueo_huella:
        return replace(e, bloqueo_huella=huella, sin_progreso=0)
    return e


def alcance_de(e: Encargo) -> AlcanceRevision:
    version = e.producto.firma if e.producto else ""
    manifiesto = e.producto.manifiesto if e.producto else ()
    if e.revision_vigente is None or e.revisado is None or e.producto is None:
        return AlcanceRevision(
            clase="completa",
            version=version,
            base_version="",
            abiertos=bloqueantes_abiertos(e, "independiente"),
            manifiesto=manifiesto,
        )
    base = dict(e.revisado.manifiesto)
    actual = dict(manifiesto)
    tocadas = tuple(
        sorted(r for r in set(base) | set(actual) if base.get(r) != actual.get(r))
    )
    conservadas = tuple(r for r, h in e.aprobadas if actual.get(r) == h)
    return AlcanceRevision(
        clase="correccion",
        version=version,
        base_version=e.revisado.firma,
        abiertos=bloqueantes_abiertos(e, "independiente"),
        tocadas=tocadas,
        conservadas=conservadas,
        manifiesto=manifiesto,
    )


def nivel_cumplido(e: Encargo) -> bool:
    return _ORDEN_NIVEL.get(e.nivel_alcanzado, 0) >= _ORDEN_NIVEL.get(
        e.entrega.nivel, 1
    )


def _entrega_de(d: dict) -> EntregaAutorizada:
    nivel = str(d.get("entrega_nivel") or "terminado")
    if nivel not in NIVELES:
        raise TransicionIlegal(f"nivel de entrega desconocido: {nivel}")
    repo = str(d.get("repo") or "")
    base = str(d.get("base") or "")
    rama = str(d.get("rama") or "")
    if nivel != "terminado":
        if not repo or not base:
            raise TransicionIlegal(
                "el nivel de entrega exige repo y rama base desde el encargo"
            )
        if not rama:
            rama = f"encargo/{d.get('id') or ''}"
    return EntregaAutorizada(nivel=nivel, repo=repo, rama=rama, base=base)


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
        if via == "directa" and (d.get("entrega_nivel") or "terminado") != "terminado":
            raise TransicionIlegal(
                "la via directa no entrega niveles externos; usa la via durable"
            )
        if d.get("detenido"):
            return Encargo(
                id=d["id"],
                clave=d["clave"],
                huella=d["huella"],
                director=d.get("director") or "",
                texto=d["texto"],
                adjuntos=tuple(d.get("adjuntos") or ()),
                restricciones=tuple(d.get("restricciones") or ()),
                canal=d.get("canal") or "",
                estado="detenido",
                ultimo_hecho="importado detenido sin escrituras",
                hora=hora,
                siguiente="detenido hasta continuar tras conciliar los efectos",
                pendientes=tuple(d.get("pendientes") or ("comprobar el pedido",)),
                escrituras=False,
                intentos_nuevos=False,
                via=via,
                entrega=_entrega_de(d),
            )
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
            entrega=_entrega_de(d),
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
    if kind == "migracion":
        return replace(
            e,
            procedimiento=e.procedimiento + (str(d.get("nota") or "migración"),),
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
        rol_intento = str(d.get("rol") or e.rol or "responsable")
        poteto = str(d.get("poteto_digest") or "")
        if "poteto_digest" in d and rol_intento == "responsable" and not poteto:
            raise TransicionIlegal("intento del responsable sin poteto verificado")
        modelo = str(d.get("modelo") or "")
        modelos = e.modelos
        if modelo:
            registro_modelo = (rol_intento, str(d.get("agente") or e.agente), modelo)
            if not modelos or modelos[-1] != registro_modelo:
                modelos = modelos + (registro_modelo,)
        viva = (
            {"viva_revisor": True}
            if rol_intento == "revisor"
            else {"viva_responsable": True}
        )
        return replace(
            e,
            reserva=False,
            rol=rol_intento,
            agente=str(d.get("agente") or e.agente),
            modelos=modelos,
            sesion_modo=str(d.get("sesion_modo") or e.sesion_modo),
            poteto_digest=poteto or e.poteto_digest,
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
            **viva,
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
        sesion_runtime = str(d.get("sesion_runtime") or "")
        cambios = {}
        if sesion_runtime:
            if e.rol == "revisor":
                cambios["sesion_revisor"] = sesion_runtime
            else:
                cambios["sesion_responsable"] = sesion_runtime
        return replace(e, proc=None, herramienta="", reserva=False, **cambios)
    if kind == "sesion_perdida":
        if (d.get("rol") or e.rol) == "revisor":
            return replace(
                e,
                sesion_revisor="",
                viva_revisor=False,
                epoca_revisor=e.epoca_revisor + 1,
            )
        return replace(
            e,
            sesion_responsable="",
            viva_responsable=False,
            epoca_responsable=e.epoca_responsable + 1,
        )
    if kind == "producto_listo":
        version = VersionProducto.desde(
            d.get("version") or "", d.get("manifiesto"), d.get("sha_git") or ""
        )
        return _con_progreso(
            replace(
                e,
                producto=version,
                ultimo_hecho="producto listo para revisar",
                hora=hora or e.hora,
            )
        )
    if kind == "observacion_supervisor":
        return replace(
            e,
            observada_version=str(d.get("version") or e.observada_version),
            causa=str(d.get("error_instrumento") or e.causa),
            ultimo_hecho="comprobaciones observadas por el supervisor",
            hora=hora or e.hora,
        )
    if kind == "revision":
        return _revision(e, d, hora)
    if kind == "instrumento_fallo":
        return replace(
            e,
            causa=str(d.get("causa") or e.causa),
            motivo=f"instrumento: {d.get('fuente') or 'desconocido'}",
            siguiente=str(d.get("siguiente") or "reintentar el mismo paso"),
            ultimo_hecho=str(d.get("causa") or e.ultimo_hecho),
            hora=hora or e.hora,
        )
    if kind == "entrega_externa":
        return _entrega_externa(e, d, hora)
    if kind == "sin_progreso":
        huella = str(d.get("huella") or "")
        veces = e.sin_progreso + 1 if huella and huella == e.bloqueo_huella else 1
        diagnostico = str(d.get("diagnostico") or d.get("causa") or "")
        if d.get("escalar"):
            return replace(
                e,
                estado="necesita_usuario",
                bloqueo_huella=huella or e.bloqueo_huella,
                sin_progreso=veces,
                preguntas=e.preguntas + ((diagnostico,) if diagnostico else ()),
                motivo=diagnostico or "el mismo bloqueo sin progreso",
                siguiente="hace falta una decisión del usuario",
                ultimo_hecho=diagnostico or "el mismo bloqueo sin progreso",
                hora=hora or e.hora,
            )
        return replace(
            e,
            bloqueo_huella=huella or e.bloqueo_huella,
            sin_progreso=veces,
            motivo="la corrección no resolvió el bloqueo",
            siguiente="diagnosticar por qué la corrección no resolvió el bloqueo",
            ultimo_hecho="sin progreso: el mismo bloqueo y la misma firma",
            hora=hora or e.hora,
        )
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
        if e.entrega.nivel != "terminado":
            raise TransicionIlegal(
                "el resultado directo no cumple niveles externos de entrega"
            )
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


def _revision(e: Encargo, d: dict, hora: str) -> Encargo:
    origen = str(d.get("origen") or "independiente")
    nuevos = tuple(
        Hallazgo.desde(h) for h in (d.get("hallazgos") or ()) if isinstance(h, dict)
    )
    hallazgos = _fusionar(e.hallazgos, nuevos)
    veredicto = str(d.get("veredicto") or "no_concluyente")
    provisional = replace(e, hallazgos=hallazgos)
    if bloqueantes_abiertos(provisional, origen):
        veredicto = "cambios"
    ref = RefRevision(
        revision_id=str(d.get("revision_id") or ""),
        version=str(d.get("version") or ""),
        base_version=str(d.get("base_version") or ""),
        alcance=str(d.get("alcance") or "completa"),
        veredicto=veredicto,
        revisor=str(d.get("revisor") or ""),
        origen=origen,
        cubre=tuple(str(x) for x in (d.get("cubre") or ())),
    )
    aprobadas = e.aprobadas
    if d.get("aprobadas") is not None:
        aprobadas = tuple(
            (str(x[0]), str(x[1]))
            for x in (d.get("aprobadas") or ())
            if isinstance(x, (list, tuple)) and len(x) >= 2
        )
    revisado = e.revisado
    if (
        origen == "independiente"
        and e.producto is not None
        and ref.version == e.producto.firma
    ):
        revisado = e.producto
    cambios = {
        "hallazgos": hallazgos,
        "aprobadas": aprobadas,
        "revisado": revisado,
        "ultimo_hecho": f"revisión {origen}: {veredicto}",
        "hora": hora or e.hora,
    }
    if veredicto == "no_concluyente" and d.get("evidencia"):
        cambios["causa"] = str(d["evidencia"])[:1000]
    if origen == "deepseek":
        cambios["deepseek_vigente"] = ref
    else:
        cambios["revision_vigente"] = ref
    return _con_progreso(replace(e, **cambios))


def _entrega_externa(e: Encargo, d: dict, hora: str) -> Encargo:
    paso = str(d.get("paso") or "")
    sha = str(d.get("sha") or "")
    candidato = e.nivel_alcanzado
    if paso in ("pr_abierto", "pr_listo"):
        candidato = "pr_abierto"
    elif paso == "deepseek_leido":
        if not bloqueantes_abiertos(e, "deepseek"):
            candidato = "pr"
    elif paso == "integrado":
        candidato = "integrado"
    elif paso == "desplegado":
        candidato = "desplegado"
    if _ORDEN_NIVEL.get(candidato, 0) < _ORDEN_NIVEL.get(e.nivel_alcanzado, 0):
        candidato = e.nivel_alcanzado
    producto = e.producto
    if paso == "commit" and producto is not None and sha:
        producto = replace(producto, sha_git=sha)
    return replace(
        e,
        nivel_alcanzado=candidato,
        pr_numero=int(d.get("numero") or e.pr_numero or 0),
        pr_sha=sha
        if paso in ("commit", "push", "pr_abierto", "pr_listo") and sha
        else e.pr_sha,
        deepseek_sha=sha if paso == "deepseek_leido" and sha else e.deepseek_sha,
        producto=producto,
        ultimo_hecho=f"entrega externa: {paso}",
        hora=hora or e.hora,
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
        if e.entrega.nivel != "terminado" and not nivel_cumplido(e):
            return replace(
                e,
                evidencia=evidencias,
                hora=hora or e.hora,
                ultimo_hecho=evidencia,
                siguiente="completar la entrega externa autorizada",
            )
        return replace(
            e,
            estado="terminado",
            nivel_alcanzado=e.nivel_alcanzado
            if e.nivel_alcanzado != "ninguno"
            else "terminado",
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
        firma = str(d.get("firma") or "")
        rep = e.repeticiones + 1 if firma == e.firma_fallo else 1
        causa = str(d.get("causa") or "instrumento defectuoso")
        return replace(
            e,
            procedimiento=e.procedimiento + (str(d.get("reparacion") or causa),),
            causa=causa,
            firma_fallo=firma,
            repeticiones=rep,
            ultimo_hecho=causa,
            hora=hora or e.hora,
            motivo="instrumento defectuoso",
            siguiente="reparar la comprobación y volver a mirar el producto",
        )
    if clase == "defecto":
        firma = str(d.get("firma") or "")
        rep = e.repeticiones + 1 if firma == e.firma_fallo else 1
        causa = str(d.get("causa") or "defecto del producto")
        return replace(
            e,
            causa=causa,
            firma_fallo=firma,
            repeticiones=rep,
            ultimo_hecho=causa,
            hora=hora or e.hora,
            motivo="defecto del producto",
            siguiente="corregir el producto y repetir la comprobación",
        )
    if clase == "no_concluyente":
        motivo = str(
            d.get("motivo") or d.get("causa") or "evidencia insuficiente"
        )
        return replace(
            e,
            motivo=motivo,
            ultimo_hecho=motivo,
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
    def _modelo(rol: str) -> str:
        for r, _agente, modelo in reversed(e.modelos):
            if r == rol:
                return modelo
        return ""

    ref = e.revision_vigente
    return {
        "encargo": e.id,
        "estado": e.estado,
        "agente": e.agente,
        "agentes_autorizados": list(e.agentes),
        "modelos": {
            "implementador": _modelo("responsable"),
            "revisor": _modelo("revisor"),
        },
        "producto": {
            "archivos": [r for r, _ in e.producto.manifiesto] if e.producto else [],
            "firma": e.producto.firma if e.producto else "",
        },
        "revision": {
            "version": ref.version if ref else "",
            "veredicto": ref.veredicto if ref else "",
            "bloqueantes_abiertos": [
                h.como_dict() for h in bloqueantes_abiertos(e)
            ],
            "observaciones": [
                h.titulo
                for h in e.hallazgos
                if h.gravedad == "observacion" and h.estado == "abierto"
            ],
        },
        "entrega": {
            "nivel_autorizado": e.entrega.nivel,
            "nivel_alcanzado": e.nivel_alcanzado,
            "repo": e.entrega.repo,
            "rama": e.entrega.rama,
            "base": e.entrega.base,
            "pr": {"numero": e.pr_numero, "sha": e.pr_sha},
            "deepseek": {"sha": e.deepseek_sha, "leida": bool(e.deepseek_sha)},
        },
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


def _bloques_comunes(e: Encargo) -> list[str]:
    lineas = ["El pedido original no se reescribe.", f"Pedido:\n{e.texto}"]
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
    return lineas


def prompt_responsable(e: Encargo) -> str:
    lineas = [
        (
            "Eres el responsable de este encargo. Sigue poteto-mode en este espacio. "
            "Carga la skill empaquetada del runtime y limita las herramientas al espacio concedido."
        )
    ]
    lineas += _bloques_comunes(e)
    abiertos = bloqueantes_abiertos(e)
    if abiertos:
        detalle = []
        for h in abiertos:
            detalle.append(
                f"{h.id} [{h.criterio}] {h.titulo}\n"
                f"  ancla: {h.ancla or 'sin ancla'}\n"
                f"  reproduccion: {h.reproduccion}"
            )
        ref = e.revision_vigente.version if e.revision_vigente else ""
        lineas.append(
            "Hallazgos bloqueantes abiertos de la revisión"
            + (f" de la versión {ref}" if ref else "")
            + ". Corrige solo estos y sus efectos; lo demás aprobado se conserva:\n"
            + "\n".join(detalle)
        )
    if e.aprobadas:
        lineas.append(
            "Rutas ya aprobadas; no las reescribas sin necesidad:\n"
            + "\n".join(r for r, _ in e.aprobadas)
        )
    if e.causa or e.motivo:
        lineas.append(
            f"Última observación: {e.causa or e.motivo}\nSiguiente trabajo concreto: {e.siguiente}"
        )
    lineas.append(
        "Comprueba el comportamiento pedido y deja la evidencia en .interno/evidencia.txt. "
        "No declares el encargo terminado: el supervisor mira el destino. "
        "Una escritura fuera de este espacio se pide en .interno/pedido-efecto.json. "
        "No repitas un efecto que ya tenga recibo."
    )
    return "\n\n".join(lineas)


def prompt_revisor(e: Encargo, alcance: AlcanceRevision) -> str:
    lineas = [
        (
            "Eres el revisor técnico independiente de este encargo. No eres el "
            "responsable y no sigues poteto-mode. El espacio del producto es de "
            "solo lectura para ti."
        )
    ]
    lineas += _bloques_comunes(e)
    if alcance.clase == "correccion":
        lineas.append(
            f"Alcance de esta revisión: correccion sobre la versión {alcance.version}"
            f" (base {alcance.base_version}). Comprueba los hallazgos abiertos y los "
            "efectos de las correcciones. Lo aprobado que no cambió conserva su aprobación."
        )
        if alcance.abiertos:
            lineas.append(
                "Hallazgos que esta vuelta cubre:\n"
                + "\n".join(
                    f"{h.id} [{h.criterio}] {h.titulo} (ancla: {h.ancla})"
                    for h in alcance.abiertos
                )
            )
        if alcance.tocadas:
            lineas.append(
                "Rutas cuyo contenido cambió desde la base:\n"
                + "\n".join(alcance.tocadas)
            )
        if alcance.conservadas:
            lineas.append(
                "Rutas aprobadas con el contenido intacto. No las vuelvas a revisar:\n"
                + "\n".join(alcance.conservadas)
            )
    else:
        lineas.append(
            f"Alcance de esta revisión: completa, contra todo el pedido, "
            f"sobre la versión {alcance.version}."
        )
    lineas.append(
        "Solo bloquea por seguridad, datos, comportamiento solicitado roto, una prueba "
        "exigida en los casos autorizados que falte, o un error operativo demostrable "
        "en un documento. Todo bloqueante necesita cómo reproducirlo. Lo demás es "
        "observación y no impide aprobar."
    )
    return "\n\n".join(lineas)


def prompt_de(e: Encargo) -> str:
    return prompt_responsable(e)
