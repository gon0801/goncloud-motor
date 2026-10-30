"""Pedido, procedimiento, intento y efecto son registros distintos.

El reducer no llama a parse_plan ni a machine.estado. Un texto del agente
no es un resultado comprobado.
"""

from __future__ import annotations

import hashlib
import socket
from dataclasses import dataclass, field, replace
from pathlib import Path

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


FASE_IMPLICITA = "fase_unica"
ESTADOS_FASE = ("pendiente", "trabajo", "revision", "entrega", "preparada")


@dataclass(frozen=True)
class Fase:
    """Una fase conserva su propio resultado, revisiones y entrega.

    El estado avanza producto -> revision -> entrega -> preparada. Una fase
    preparada ya no se vuelve a ejecutar; sus datos son los que consume la
    siguiente fase.
    """

    id: str
    resultado: str
    estado: str = "trabajo"
    requiere: tuple[tuple[str, str], ...] = ()
    producto: VersionProducto | None = None
    revisado: VersionProducto | None = None
    hallazgos: tuple[Hallazgo, ...] = ()
    aprobadas: tuple[tuple[str, str], ...] = ()
    revision_vigente: RefRevision | None = None
    deepseek_vigente: RefRevision | None = None
    nivel_alcanzado: str = "ninguno"
    pr_numero: int = 0
    pr_sha: str = ""
    deepseek_sha: str = ""
    integrado_sha: str = ""
    rama: str = ""
    base: str = ""
    evidencia: str = ""
    inspeccion_pendiente: str = ""
    inspeccion_solicitud: str = ""
    inspeccion_entregada: str = ""
    inspecciones_consumidas: tuple[str, ...] = ()
    entrega_bloqueada_sha: str = ""

    def __post_init__(self) -> None:
        if not str(self.id or "").strip():
            raise TransicionIlegal("fase sin id")
        if self.estado not in ESTADOS_FASE:
            raise TransicionIlegal(f"estado de fase desconocido: {self.estado}")
        if self.estado == "preparada" and not (self.evidencia or self.revision_vigente):
            raise TransicionIlegal(f"la fase {self.id} no se prepara sin resultado comprobado")

    def como_dict(self) -> dict:
        return {
            "id": self.id,
            "resultado": self.resultado,
            "estado": self.estado,
            "requiere": [list(x) for x in self.requiere],
            "producto": self.producto.como_dict() if self.producto else None,
            "revision": {
                "version": self.revision_vigente.version,
                "veredicto": self.revision_vigente.veredicto,
                "alcance": self.revision_vigente.alcance,
                "id": self.revision_vigente.revision_id,
            }
            if self.revision_vigente
            else None,
            "nivel_alcanzado": self.nivel_alcanzado,
            "pr": {"numero": self.pr_numero, "sha": self.pr_sha},
            "deepseek": {"sha": self.deepseek_sha, "leida": bool(self.deepseek_sha)},
            "rama": self.rama,
            "base": self.base,
            "evidencia": self.evidencia,
            "inspeccion_pendiente": self.inspeccion_pendiente,
            "inspeccion_solicitud": self.inspeccion_solicitud,
            "inspeccion_entregada": self.inspeccion_entregada,
            "inspecciones_consumidas": list(self.inspecciones_consumidas),
        }



@dataclass(frozen=True)
class Decision:
    """Elección técnica con motivo y evidencia. Una decisión no autoriza nada."""

    id: str
    eleccion: str
    motivo: str
    evidencia: str = ""
    fases: tuple[str, ...] = ()
    hora: str = ""

    def como_dict(self) -> dict:
        return {
            "id": self.id,
            "eleccion": self.eleccion,
            "motivo": self.motivo,
            "evidencia": self.evidencia,
            "fases": list(self.fases),
            "hora": self.hora,
        }


ESTADOS_SOLICITUD = (
    "pendiente",
    "pregunta",
    "respuesta",
    "resuelta",
    "error",
    "denegada",
    "detenida",
)


@dataclass(frozen=True)
class Solicitud:
    """Una necesidad del trabajador, con la identidad que le concedió el host.

    `estado` avanza pendiente -> resuelta, o se detiene en pregunta, error,
    denegada o detenida. La fase y el intento son los de origen: una resolución
    de otra fase no se aplica.
    """

    id: str
    operacion: str
    recurso: str
    fase: str
    intento: str
    generacion: int
    conductor: str
    motivo: str = ""
    parametros: tuple[tuple[str, str], ...] = ()
    estado: str = "pendiente"
    evidencia: str = ""
    pregunta: str = ""
    respuesta: str = ""
    detalle: str = ""

    def __post_init__(self) -> None:
        if not str(self.id or "").strip():
            raise TransicionIlegal("solicitud sin id")
        if not str(self.operacion or "").strip() and self.estado == "pendiente":
            raise TransicionIlegal("solicitud sin operación")
        if self.estado not in ESTADOS_SOLICITUD:
            raise TransicionIlegal(f"estado de solicitud desconocido: {self.estado}")

    def como_dict(self) -> dict:
        return {
            "id": self.id,
            "operacion": self.operacion,
            "recurso": self.recurso,
            "fase": self.fase,
            "intento": self.intento,
            "generacion": self.generacion,
            "motivo": self.motivo,
            "parametros": [list(x) for x in self.parametros],
            "estado": self.estado,
            "evidencia": self.evidencia,
            "pregunta": self.pregunta,
            "respuesta": self.respuesta,
            "detalle": self.detalle,
        }


@dataclass(frozen=True)
class Impedimento:
    """Una causa pendiente con su identidad. `clase` nombra la operación que
    falla, no el texto del mensaje. `verificable` dice si el host puede
    comprobar por su cuenta que dejó de fallar."""

    clase: str
    causa: str
    condicion: str
    verificable: bool = True


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
    modelos: tuple[tuple[str, str, str], ...] = ()
    modelos_solicitados: tuple[tuple[str, str, str], ...] = ()
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
    # Las causas pendientes del entorno o del instrumento. Mientras haya una,
    # ningún proceso nuevo: cada una se levanta con su propia comprobación.
    impedimento: str = ""
    impedimento_causa: str = ""
    impedimento_condicion: str = ""
    impedimento_fuente: str = ""
    impedimentos: tuple[Impedimento, ...] = ()
    observada_version: str = ""
    fases: tuple[Fase, ...] = ()
    fase_activa: str = ""
    solicitudes: tuple[Solicitud, ...] = ()
    decisiones: tuple[Decision, ...] = ()
    autorizaciones: tuple[tuple[str, str, str, str, str, str], ...] = ()

    # La fase conserva el producto, la revisión y la entrega. Estas vistas
    # existen para los consumidores del encargo que trabajan la fase actual.
    @property
    def fase_actual(self) -> Fase | None:
        for fase in self.fases:
            if fase.id == self.fase_activa:
                return fase
        return self.fases[-1] if self.fases else None

    @property
    def producto(self) -> VersionProducto | None:
        fase = self.fase_actual
        return fase.producto if fase else None

    @property
    def revisado(self) -> VersionProducto | None:
        fase = self.fase_actual
        return fase.revisado if fase else None

    @property
    def hallazgos(self) -> tuple[Hallazgo, ...]:
        fase = self.fase_actual
        return fase.hallazgos if fase else ()

    @property
    def aprobadas(self) -> tuple[tuple[str, str], ...]:
        fase = self.fase_actual
        return fase.aprobadas if fase else ()

    @property
    def revision_vigente(self) -> RefRevision | None:
        fase = self.fase_actual
        return fase.revision_vigente if fase else None

    @property
    def deepseek_vigente(self) -> RefRevision | None:
        fase = self.fase_actual
        return fase.deepseek_vigente if fase else None

    @property
    def nivel_alcanzado(self) -> str:
        fase = self.fase_actual
        return fase.nivel_alcanzado if fase else "ninguno"

    @property
    def pr_numero(self) -> int:
        fase = self.fase_actual
        return fase.pr_numero if fase else 0

    @property
    def pr_sha(self) -> str:
        fase = self.fase_actual
        return fase.pr_sha if fase else ""

    @property
    def deepseek_sha(self) -> str:
        fase = self.fase_actual
        return fase.deepseek_sha if fase else ""

    @property
    def rama_entrega(self) -> str:
        fase = self.fase_actual
        if fase is not None and fase.rama:
            return fase.rama
        return self.entrega.rama

    @property
    def inspeccion_pendiente(self) -> str:
        fase = self.fase_actual
        return fase.inspeccion_pendiente if fase else ""

    def fases_preparadas(self) -> tuple[Fase, ...]:
        return tuple(f for f in self.fases if f.estado == "preparada")

    def objetivo_completo(self) -> bool:
        return bool(self.fases) and all(
            f.estado == "preparada" for f in self.fases
        )


def con_fase(e: Encargo, fase: Fase) -> Encargo:
    fases = tuple(fase if x.id == fase.id else x for x in e.fases)
    return replace(e, fases=fases, fase_activa=e.fase_activa or fase.id)


def fase_implicita(e: Encargo) -> Fase:
    return Fase(
        id=FASE_IMPLICITA,
        resultado="Resultado completo del pedido original",
        estado="trabajo",
    )


def asegurar_fase(e: Encargo) -> Encargo:
    if e.fases:
        return e
    fase = fase_implicita(e)
    return replace(e, fases=(fase,), fase_activa=fase.id)


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


def fuentes_autorizadas(e: Encargo) -> tuple[Path, ...]:
    """Rutas cuya lectura concede el pedido. Sólo los adjuntos declarados al
    recibir el encargo: una ruta escrita en el texto no autoriza nada.

    Devuelve también las que ya no existen: la sonda tiene que intentar leerlas
    y fallar, no dar por bueno lo que no comprobó.
    """
    fuentes = []
    for crudo in e.adjuntos:
        ruta = Path(str(crudo)).expanduser()
        if not ruta.is_absolute():
            ruta = (Path.cwd() / ruta).resolve()
        fuentes.append(ruta)
    return tuple(fuentes)


def huella_bloqueo(e: Encargo) -> str:
    ids = sorted(h.id for h in bloqueantes_abiertos(e))
    firma = e.producto.firma if e.producto else ""
    return hashlib.sha256(("|".join(ids) + "|" + firma).encode()).hexdigest()


def _con_progreso(e: Encargo) -> Encargo:
    huella = huella_bloqueo(e)
    if huella != e.bloqueo_huella:
        return replace(e, bloqueo_huella=huella, sin_progreso=0)
    return e


def fuente_de_impedimento(clase: str) -> str:
    return "entorno" if clase == "entorno" else "instrumento"


def _con_impedimentos(
    e: Encargo, impedimentos: tuple[Impedimento, ...], hora: str = ""
) -> Encargo:
    """Recompone la causa vigente a partir del conjunto. La primera por clase
    alimenta los campos que ya leen el estado y la consulta."""
    ordenados = tuple(sorted(impedimentos, key=lambda i: i.clase))
    if not ordenados:
        return replace(
            e,
            impedimentos=(),
            impedimento="",
            impedimento_causa="",
            impedimento_condicion="",
            impedimento_fuente="",
            hora=hora or e.hora,
        )
    primero = ordenados[0]
    return replace(
        e,
        impedimentos=ordenados,
        impedimento=primero.clase,
        impedimento_causa=primero.causa,
        impedimento_condicion=primero.condicion,
        impedimento_fuente=fuente_de_impedimento(primero.clase),
        hora=hora or e.hora,
    )


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


def revision_independiente_cubre_producto(e: Encargo) -> bool:
    ref, producto = e.revision_vigente, e.producto
    if ref is None or ref.veredicto != "aprobado" or producto is None:
        return False
    if ref.version == producto.firma:
        return True
    ds = e.deepseek_vigente
    actual = dict(producto.manifiesto)
    return bool(
        e.entrega.nivel != "terminado"
        and e.rol != "revisor"
        and e.revisado is not None
        and ref.version == e.revisado.firma
        and ds is not None
        and ds.veredicto == "aprobado"
        and ds.version == producto.firma
        and producto.sha_git
        and producto.sha_git == e.pr_sha == e.deepseek_sha
        and all(actual.get(ruta) == firma for ruta, firma in e.aprobadas)
    )


def nivel_cumplido(e: Encargo) -> bool:
    if e.nivel_alcanzado == "sin_cambios":
        # Sin cambios publicables sólo satisface niveles sin efectos externos;
        # integrado y desplegado exigen el nivel real alcanzado.
        return e.revisado is not None and e.entrega.nivel in ("terminado", "pr")
    return _ORDEN_NIVEL.get(e.nivel_alcanzado, 0) >= _ORDEN_NIVEL.get(
        e.entrega.nivel, 1
    )


def inspeccion_bloquea(e: Encargo) -> bool:
    """La inspección pendiente no consume otro turno sin información nueva.

    La información nueva es la respuesta del propietario o la resolución
    terminal de la solicitud ligada, y abre el gate una sola vez: cuando el
    resultado ya se entregó al revisor, la marca de la fase cierra hasta que
    llegue una respuesta o resolución distinta.
    """
    fase = e.fase_actual
    if fase is None or not fase.inspeccion_pendiente:
        return False
    ident = fase.inspeccion_solicitud
    if not ident:
        return True
    sol = next((s for s in e.solicitudes if s.id == ident), None)
    if sol is None:
        return True
    if sol.estado in ("pendiente", "pregunta"):
        return True
    return respuesta_consumida(fase, marca_inspeccion_entregada(sol))


def marca_inspeccion_entregada(sol: Solicitud) -> str:
    return f"{sol.id}:{sol.estado}"


def inspeccion_consumida(e: Encargo) -> Solicitud | None:
    fase = e.fase_actual
    if fase is None or not fase.inspeccion_pendiente:
        return None
    sol = next((s for s in e.solicitudes if s.id == fase.inspeccion_solicitud), None)
    if sol is None or sol.estado in ("pendiente", "pregunta"):
        return None
    return sol if respuesta_consumida(fase, marca_inspeccion_entregada(sol)) else None


def espera_inspeccion_consumida(e: Encargo) -> dict | None:
    sol = inspeccion_consumida(e)
    if sol is None:
        return None
    return {
        "clase": "inspeccion:" + sol.id,
        "causa": (
            f"la revisión repitió la inspección {sol.id} ya consumida; "
            f"el resultado está en .interno/buzon/respuestas/{sol.id}.json"
        ),
        "condicion": (
            "un juicio corregido que use el resultado disponible o una consulta "
            "distinta; se conserva el producto y no se repite la herramienta"
        ),
        "verificable": True,
    }


def respuesta_consumida(fase: Fase, marca: str) -> bool:
    """La respuesta ya se entregó, aunque después se haya ligado otra consulta."""
    return bool(marca) and (
        marca == fase.inspeccion_entregada or marca in fase.inspecciones_consumidas
    )


def _entrega_de(d: dict) -> EntregaAutorizada:
    nivel = str(d.get("entrega_nivel") or "terminado")
    if nivel not in NIVELES:
        raise TransicionIlegal(f"nivel de entrega desconocido: {nivel}")
    repo = str(d.get("repo") or "")
    base = str(d.get("base") or "")
    rama = str(d.get("rama") or "")
    if nivel != "terminado":
        if not repo:
            raise TransicionIlegal(
                "el nivel de entrega exige un repositorio desde el encargo"
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
                modelos_solicitados=tuple(tuple(x) for x in d.get("modelos_solicitados", ())),
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
            modelos_solicitados=tuple(tuple(x) for x in d.get("modelos_solicitados", ())),
        )
    if estado is None:
        raise TransicionIlegal("no hay encargo")
    e = estado
    if kind == "recibo":
        return e
    if kind == "ruta_agentes":
        return replace(e, agentes=tuple(d["agentes"]))
    if kind == "contexto_entrega":
        base = str(d.get("base") or "").strip()
        if not base or (e.entrega.base and e.entrega.base != base):
            raise TransicionIlegal("el contexto no puede cambiar una base autorizada")
        return replace(
            e, entrega=replace(e.entrega, base=base),
            causa="", motivo="", siguiente="seguir el mismo encargo", hora=hora or e.hora,
        )
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
        # Un ajuste trae información nueva del operador. Sólo levanta las causas
        # que el host no puede comprobar: las demás siguen hasta verificarse.
        e = _con_impedimentos(
            e, tuple(i for i in e.impedimentos if i.verificable), hora
        )
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
    if kind == "conductor_fase":
        fase = str(d.get("fase") or "")
        token = str(d.get("token") or "")
        if not fase:
            raise TransicionIlegal("asociación sin fase")
        if e.conductor and token != e.conductor:
            raise TransicionIlegal("la asociación no corresponde al conductor")
        if e.fases and fase != FASE_IMPLICITA and all(f.id != fase for f in e.fases):
            raise TransicionIlegal("asociación a una fase inexistente")
        return e
    if kind == "fases":
        return _adoptar_fases(e, d, hora)
    if kind == "producto_listo":
        e = _fase_de_evento(e, d)
        fase = e.fase_actual
        if fase is None:
            raise TransicionIlegal("no hay fase para el producto")
        version = VersionProducto.desde(
            d.get("version") or "", d.get("manifiesto"), d.get("sha_git") or ""
        )
        nueva = replace(fase, producto=version, estado="revision")
        return _con_progreso(
            replace(
                con_fase(e, nueva),
                ultimo_hecho=f"producto de {fase.id} listo para revisar",
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
    if kind == "impedimento":
        clase = str(d.get("clase") or d.get("huella") or "")
        causa = str(d.get("causa") or "")
        condicion = str(d.get("condicion") or "")
        if not clase or not causa or not condicion:
            raise TransicionIlegal("el impedimento necesita clase, causa y condicion")
        pendientes = {i.clase: i for i in e.impedimentos}
        pendientes[clase] = Impedimento(
            clase, causa, condicion, bool(d.get("verificable", True))
        )
        e = _con_impedimentos(e, tuple(pendientes.values()), hora)
        return replace(
            e,
            motivo=e.impedimento_causa,
            siguiente="esperar a que se cumpla: " + e.impedimento_condicion,
            ultimo_hecho=e.impedimento_causa,
        )
    if kind == "impedimento_resuelto":
        clase = str(d.get("clase") or d.get("huella") or "")
        evidencia = str(d.get("evidencia") or "")
        if not clase or not evidencia:
            raise TransicionIlegal("el impedimento no se resuelve sin clase y evidencia")
        restantes = tuple(i for i in e.impedimentos if i.clase != clase)
        if len(restantes) == len(e.impedimentos):
            return e
        e = _con_impedimentos(e, restantes, hora)
        return replace(
            e,
            motivo=e.impedimento_causa,
            siguiente=(
                "esperar a que se cumpla: " + e.impedimento_condicion
                if e.impedimentos
                else "seguir el mismo encargo"
            ),
            ultimo_hecho="impedimento resuelto: " + evidencia,
        )
    if kind == "entrega_externa":
        return _entrega_externa(e, d, hora)
    if kind == "entrega_bloqueada":
        e = _fase_de_evento(e, d)
        fase = e.fase_actual
        if fase is None:
            raise TransicionIlegal("no hay fase para la entrega bloqueada")
        return replace(
            con_fase(e, replace(fase, entrega_bloqueada_sha=str(d.get("sha") or ""))),
            ultimo_hecho=str(d.get("causa") or "entrega bloqueada por checks en rojo"),
            hora=hora or e.hora,
        )
    if kind == "entrada_incorporada":
        e = _fase_de_evento(e, d)
        fase = e.fase_actual
        dep = str(d.get("dep") or "").strip()
        version = str(d.get("version") or "").strip()
        if fase is None or not dep or not version:
            raise TransicionIlegal("la entrada incorporada necesita fase, dep y versión")
        if not any(d_ == dep for d_, _v in fase.requiere):
            raise TransicionIlegal(f"la fase {fase.id} no consume a {dep}")
        return replace(
            con_fase(
                e,
                replace(
                    fase,
                    requiere=tuple(
                        (d_, version if d_ == dep else v) for d_, v in fase.requiere
                    ),
                ),
            ),
            ultimo_hecho=f"entrada de {dep} incorporada: {version[:12]}",
            hora=hora or e.hora,
        )
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
        if e.estado in _CERRADOS:
            return e
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
        e = asegurar_fase(e)
        fase = e.fase_actual
        fases = e.fases
        if fase is not None:
            fases = tuple(
                replace(
                    fase,
                    estado="preparada",
                    nivel_alcanzado=fase.nivel_alcanzado
                    if fase.nivel_alcanzado != "ninguno"
                    else "terminado",
                    evidencia=evidencia,
                )
                if f.id == fase.id
                else f
                for f in e.fases
            )
        return replace(
            e,
            fases=fases,
            estado="terminado",
            evidencia=e.evidencia + (evidencia,),
            intentos_nuevos=False,
            escrituras=False,
            ultimo_hecho="resultado directo comprobado",
            siguiente="publicado al canal de origen",
            hora=hora or e.hora,
            motivo="",
        )

    if kind == "solicitud":
        return _solicitud(e, d, hora)
    if kind == "inspeccion_solicitud":
        e = _fase_de_evento(e, d)
        fase = e.fase_actual
        if fase is None:
            raise TransicionIlegal("no hay fase para la inspección")
        ident = str(d.get("solicitud") or "").strip()
        if not ident:
            raise TransicionIlegal("la inspección necesita su solicitud")
        return replace(
            con_fase(e, replace(fase, inspeccion_solicitud=ident)),
            hora=hora or e.hora,
        )
    if kind == "inspeccion_entregada":
        e = _fase_de_evento(e, d)
        fase = e.fase_actual
        if fase is None:
            raise TransicionIlegal("no hay fase para la entrega de la inspección")
        marca = str(d.get("marca") or "").strip()
        if not marca:
            raise TransicionIlegal("la entrega de la inspección necesita su marca")
        consumidas = fase.inspecciones_consumidas
        if marca not in consumidas:
            consumidas = consumidas + (marca,)
        return replace(
            con_fase(
                e,
                replace(
                    fase,
                    inspeccion_entregada=marca,
                    inspecciones_consumidas=consumidas,
                ),
            ),
            hora=hora or e.hora,
        )
    if kind == "resolucion":
        return _resolucion(e, d, hora)
    if kind == "respuesta":
        return _respuesta(e, d, hora)
    if kind == "autorizacion":
        return _autorizacion(e, d, hora)
    if kind == "decision":
        return _decision(e, d, hora)
    if kind == "solicitud_tardia":
        return replace(
            e,
            procedimiento=e.procedimiento
            + (
                "solicitud sin efecto: "
                + str(d.get("id") or "")
                + " ("
                + str(d.get("motivo") or "fuera de la fase o del intento")
                + ")",
            ),
            hora=hora or e.hora,
        )

    raise TransicionIlegal(f"evento desconocido: {kind}")


def _solicitud(e: Encargo, d: dict, hora: str) -> Encargo:
    """Una necesidad entra con la fase y el intento de origen, no con los vigentes."""
    if e.estado in _CERRADOS or e.estado == "detenido":
        raise TransicionIlegal("el encargo no admite solicitudes en este estado")
    ident = str(d.get("id") or "").strip()
    if not ident:
        raise TransicionIlegal("la solicitud necesita id")
    if any(s.id == ident for s in e.solicitudes):
        raise TransicionIlegal(f"la solicitud {ident} ya existe")
    if str(d.get("fase") or "") != e.fase_activa:
        raise TransicionIlegal("la solicitud no pertenece a la fase activa")
    intento = str(d.get("intento") or "")
    if not intento or (e.intento and intento != e.intento):
        raise TransicionIlegal("la solicitud no pertenece al intento vigente")
    nace = str(d.get("estado") or "pendiente")
    if nace not in ("pendiente", "pregunta"):
        raise TransicionIlegal("una solicitud nace pendiente o a la espera del propietario")
    sol = Solicitud(
        id=ident,
        operacion=str(d["operacion"]),
        recurso=str(d.get("recurso") or ""),
        fase=str(d.get("fase") or ""),
        intento=intento,
        generacion=int(d.get("generacion") or 0),
        conductor=str(d.get("conductor") or ""),
        motivo=str(d.get("motivo") or ""),
        parametros=tuple((str(k), str(v)) for k, v in (d.get("parametros") or ())),
        estado=nace,
        pregunta=str(d.get("pregunta") or "") if nace == "pregunta" else "",
    )
    return replace(e, solicitudes=e.solicitudes + (sol,), hora=hora or e.hora)


def _reanudar_inspeccion(e: Encargo, ident: str) -> Encargo:
    fase = e.fase_actual
    if (
        e.estado == "necesita_usuario"
        and fase is not None
        and fase.inspeccion_solicitud == ident
        and not inspeccion_bloquea(e)
    ):
        return replace(
            e,
            estado="recibido",
            motivo="",
            siguiente="continuar la revisión con la inspección resuelta",
        )
    return e


def _resolucion(e: Encargo, d: dict, hora: str) -> Encargo:
    ident = str(d.get("id") or "")
    sol = next((s for s in e.solicitudes if s.id == ident), None)
    if sol is None:
        raise TransicionIlegal("la resolución no tiene solicitud")
    if str(d.get("fase") or "") != sol.fase or str(d.get("intento") or "") != sol.intento:
        raise TransicionIlegal("la resolución no corresponde a la fase e intento de origen")
    if sol.estado in ("resuelta", "denegada"):
        raise TransicionIlegal("la solicitud ya tiene resolución definitiva")
    estado = str(d.get("estado") or "")
    if estado not in ESTADOS_SOLICITUD or estado == "pendiente":
        raise TransicionIlegal("estado de resolución inválido")
    nueva = replace(
        sol,
        estado=estado,
        evidencia=str(d.get("evidencia") or ""),
        pregunta=str(d.get("pregunta") or ""),
        detalle=str(d.get("detalle") or ""),
    )
    e = replace(
        e,
        solicitudes=tuple(nueva if s.id == ident else s for s in e.solicitudes),
        hora=hora or e.hora,
    )
    if estado in ("resuelta", "error", "denegada", "detenida"):
        e = _reanudar_inspeccion(e, ident)
    return e


def solicitud_vigente(e: Encargo, fase: str, intento: str) -> bool:
    """La solicitud sigue siendo el trabajo que espera la respuesta."""
    if fase and fase != e.fase_activa:
        return False
    if e.intento and intento and intento != e.intento:
        return False
    return True


def _respuesta(e: Encargo, d: dict, hora: str) -> Encargo:
    """Una respuesta tardía se conserva y no cambia el trabajo que ya no la espera."""
    ident = str(d.get("solicitud") or "")
    texto = str(d.get("texto") or "")
    sol = next((s for s in e.solicitudes if s.id == ident), None)
    if (
        sol is None
        or sol.estado != "pregunta"
        or not solicitud_vigente(e, sol.fase, sol.intento)
    ):
        return replace(
            e,
            procedimiento=e.procedimiento
            + (f"respuesta sin pregunta vigente ({ident}): {texto}",),
            hora=hora or e.hora,
        )
    nueva = replace(sol, estado="respuesta", respuesta=texto)
    return _reanudar_inspeccion(
        replace(
            e,
            solicitudes=tuple(nueva if s.id == ident else s for s in e.solicitudes),
            hora=hora or e.hora,
        ),
        ident,
    )


def _autorizacion(e: Encargo, d: dict, hora: str) -> Encargo:
    """Una respuesta autoriza la operación explicada, no todo el recurso."""
    recurso = str(d.get("recurso") or "").strip()
    operacion = str(d.get("operacion") or "").strip()
    texto = str(d.get("texto") or "")
    if not recurso:
        raise TransicionIlegal("la autorización necesita recurso")
    if not operacion:
        raise TransicionIlegal("la autorización necesita la operación explicada")
    fase = str(d.get("fase") or "")
    intento = str(d.get("intento") or "")
    tupla = (recurso, operacion, fase, intento, texto, hora)
    if any(x[:5] == tupla[:5] for x in e.autorizaciones):
        return e
    return replace(e, autorizaciones=e.autorizaciones + (tupla,), hora=hora or e.hora)


def _decision(e: Encargo, d: dict, hora: str) -> Encargo:
    ident = str(d.get("id") or "").strip()
    if not ident:
        raise TransicionIlegal("la decisión necesita id")
    if any(x.id == ident for x in e.decisiones):
        raise TransicionIlegal(f"la decisión {ident} ya existe")
    decision = Decision(
        id=ident,
        eleccion=str(d.get("eleccion") or ""),
        motivo=str(d.get("motivo") or ""),
        evidencia=str(d.get("evidencia") or ""),
        fases=tuple(str(x) for x in d.get("fases") or ()),
        hora=hora or e.hora,
    )
    if not decision.eleccion or not decision.motivo:
        raise TransicionIlegal("la decisión necesita elección y motivo")
    return replace(e, decisiones=e.decisiones + (decision,), hora=hora or e.hora)


def _requiere_de(item: dict) -> tuple[tuple[str, str], ...]:
    crudo = item.get("requiere") or ()
    if not isinstance(crudo, (list, tuple)):
        raise TransicionIlegal("requiere debe ser una lista")
    salida = []
    for dep in crudo:
        if isinstance(dep, dict):
            fid = str(dep.get("fase") or "").strip()
            version = str(dep.get("version") or "").strip()
        else:
            fid, version = str(dep or "").strip(), ""
        if not fid:
            raise TransicionIlegal("dependencia de fase vacía")
        salida.append((fid, version))
    return tuple(salida)


def _fusionar_requiere(
    previas: tuple[tuple[str, str], ...], nuevas: tuple[tuple[str, str], ...]
) -> tuple[tuple[str, str], ...]:
    """Una propuesta sin versión no borra una entrada ya fijada."""
    fijadas = {fase: version for fase, version in previas if version}
    return tuple(
        (fase, version or fijadas.get(fase, "")) for fase, version in nuevas
    )


def _fase_disponible(fases: tuple[Fase, ...]) -> Fase | None:
    """Primera fase no preparada cuya dependencia ya está preparada."""
    preparadas = {f.id for f in fases if f.estado == "preparada"}
    for fase in fases:
        if fase.estado == "preparada":
            continue
        if all(dep in preparadas for dep, _version in fase.requiere):
            return fase
    return None


def _sin_ciclo(fases: tuple[Fase, ...]) -> None:
    por_id = {f.id: f for f in fases}
    visto: set[str] = set()
    camino: set[str] = set()

    def visitar(fid: str) -> None:
        if fid in camino:
            raise TransicionIlegal(f"dependencia circular en la fase {fid}")
        if fid in visto:
            return
        camino.add(fid)
        for dep, _version in por_id[fid].requiere:
            visitar(dep)
        camino.discard(fid)
        visto.add(fid)

    for fase in fases:
        visitar(fase.id)


def _adoptar_fases(e: Encargo, d: dict, hora: str) -> Encargo:
    """La propuesta de fases se valida aquí y no borra trabajo ya obtenido.

    El formato lo escribe el implementador, nunca el usuario del encargo.
    """
    crudo = d.get("fases")
    if isinstance(crudo, dict):
        crudo = crudo.get("fases")
    if not isinstance(crudo, (list, tuple)) or not crudo:
        raise TransicionIlegal("la propuesta de fases está vacía")
    existentes = {f.id: f for f in e.fases}
    nuevas: list[Fase] = []
    vistos: set[str] = set()
    for item in crudo:
        if not isinstance(item, dict):
            raise TransicionIlegal("cada fase debe ser un objeto")
        fid = str(item.get("id") or "").strip()
        if not fid or not all(c.isalnum() or c in "-_" for c in fid):
            raise TransicionIlegal(f"id de fase inválido: {fid!r}")
        if fid in vistos:
            raise TransicionIlegal(f"fase repetida: {fid}")
        vistos.add(fid)
        resultado = str(item.get("resultado") or "").strip()
        requiere = _requiere_de(item)
        previa = existentes.get(fid)
        if previa is None:
            if not resultado:
                raise TransicionIlegal(f"la fase {fid} no declara resultado")
            nuevas.append(
                Fase(
                    id=fid,
                    resultado=resultado,
                    estado="pendiente",
                    requiere=requiere,
                )
            )
            continue
        if previa.estado != "pendiente" and resultado and resultado != previa.resultado:
            raise TransicionIlegal(f"no se reescribe el resultado de la fase {fid}")
        if previa.estado != "pendiente":
            fusionada = _fusionar_requiere(previa.requiere, requiere)
            if fusionada != previa.requiere:
                raise TransicionIlegal(
                    f"no se reescriben las dependencias de la fase {fid} ya trabajada"
                )
            requiere_final = previa.requiere
        else:
            requiere_final = _fusionar_requiere(previa.requiere, requiere)
        nuevas.append(
            replace(
                previa,
                resultado=resultado or previa.resultado,
                requiere=requiere_final,
            )
        )
    for fid in existentes:
        if fid not in vistos:
            raise TransicionIlegal(f"la propuesta omite la fase existente {fid}")
    por_id = {f.id: f for f in nuevas}
    for fase in nuevas:
        for dep, _version in fase.requiere:
            if dep not in por_id:
                raise TransicionIlegal(f"la fase {fase.id} requiere una fase inexistente: {dep}")
            if dep == fase.id:
                raise TransicionIlegal(f"la fase {fase.id} se requiere a sí misma")
    _sin_ciclo(tuple(nuevas))
    actual = next((f for f in nuevas if f.id == e.fase_activa), None)
    preparadas = {f.id for f in nuevas if f.estado == "preparada"}
    disponible = _fase_disponible(tuple(nuevas))
    if (
        actual is not None
        and actual.estado != "preparada"
        and all(dep in preparadas for dep, _version in actual.requiere)
    ):
        activa = actual.id
    elif disponible is not None:
        activa = disponible.id
    else:
        activa = e.fase_activa if any(f.id == e.fase_activa for f in nuevas) else nuevas[0].id
    return replace(
        e,
        fases=tuple(nuevas),
        fase_activa=activa,
        hora=hora or e.hora,
        ultimo_hecho=f"fases organizadas: {len(nuevas)}",
    )


def defecto_al_adoptar_fases(e: Encargo, fases) -> str:
    """El defecto que impediría adoptar la propuesta sobre el estado vigente,
    sin adoptarla. Es la misma validación que aplica el evento `fases`: que el
    archivo tenga forma de propuesta no basta si deja un ciclo o reescribe una
    fase ya trabajada."""
    try:
        _adoptar_fases(e, {"fases": list(fases)}, e.hora)
    except TransicionIlegal as exc:
        return str(exc)
    return ""


def _rol_de_continuacion(e: Encargo, fase: Fase) -> tuple[str, str]:
    """Conserva el paso de una fase iniciada; activa al implementador solo
    cuando hay que empezar una fase o corregir un defecto."""
    if fase.estado in ("revision", "entrega"):
        if bloqueantes_abiertos(e) or e.estrategia == "reparar_instrumento":
            return "responsable", e.estrategia
        rol = "revisor" if fase.estado == "revision" else (e.rol or "revisor")
        return rol, e.estrategia
    return "responsable", "investigar"


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
        # Continuar retoma el encargo y no repara nada: las causas pendientes
        # siguen vigentes, cada una con su propia comprobación.
        disponible = _fase_disponible(e.fases)
        if disponible is not None:
            rol, estrategia = _rol_de_continuacion(e, disponible)
            return replace(
                e,
                estado="recibido",
                fase_activa=disponible.id,
                rol=rol,
                estrategia=estrategia,
                intentos_nuevos=True,
                escrituras=e.autoridad,
                motivo="",
                siguiente=f"continuar la fase {disponible.id}",
                ultimo_hecho="se continuó el encargo",
                hora=hora or e.hora,
                proc=None,
                herramienta="",
            )
        if e.fases and all(f.estado == "preparada" for f in e.fases):
            return replace(
                e,
                estado="recibido",
                rol="revisor",
                intentos_nuevos=True,
                escrituras=e.autoridad,
                motivo="",
                siguiente="completar el cierre global",
                ultimo_hecho="se continuó; todas las fases están preparadas",
                hora=hora or e.hora,
                proc=None,
                herramienta="",
            )
        return replace(
            e,
            estado="recibido",
            rol="",
            intentos_nuevos=True,
            escrituras=e.autoridad,
            motivo="",
            siguiente=(
                "esperar a que una dependencia se prepare"
                if e.fases
                else "reanudar el mismo encargo"
            ),
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


def _fase_de_evento(e: Encargo, d: dict) -> Encargo:
    """Todo evento con fase se aplica a la fase y al intento que lo produjeron.

    Una respuesta tardía nombra una fase anterior o un intento que ya no está
    vigente; se rechaza en el registro en vez de aprobar otro trabajo.
    """
    e = asegurar_fase(e)
    pedida = str(d.get("fase") or "")
    if pedida and pedida != e.fase_activa:
        raise TransicionIlegal("el evento pertenece a otra fase")
    intento = str(d.get("intento") or "")
    if intento and e.intento and intento != e.intento:
        raise TransicionIlegal("el evento pertenece a otro intento")
    return e


def _revision(e: Encargo, d: dict, hora: str) -> Encargo:
    e = _fase_de_evento(e, d)
    fase = e.fase_actual
    if fase is None:
        raise TransicionIlegal("no hay fase para la revisión")
    origen = str(d.get("origen") or "independiente")
    nuevos = tuple(
        Hallazgo.desde(h) for h in (d.get("hallazgos") or ()) if isinstance(h, dict)
    )
    hallazgos = _fusionar(fase.hallazgos, nuevos)
    veredicto = str(d.get("veredicto") or "no_concluyente")
    alcance = str(d.get("alcance") or "completa")
    if alcance == "correccion" and fase.revisado is None:
        veredicto = "no_concluyente"
    provisional = con_fase(e, replace(fase, hallazgos=hallazgos))
    if veredicto != "no_concluyente" and bloqueantes_abiertos(provisional, origen):
        veredicto = "cambios"
    ref = RefRevision(
        revision_id=str(d.get("revision_id") or ""),
        version=str(d.get("version") or ""),
        base_version=str(d.get("base_version") or ""),
        alcance=alcance,
        veredicto=veredicto,
        revisor=str(d.get("revisor") or ""),
        origen=origen,
        cubre=tuple(str(x) for x in (d.get("cubre") or ())),
    )
    aprobadas = fase.aprobadas
    if veredicto != "no_concluyente" and d.get("aprobadas") is not None:
        aprobadas = tuple(
            (str(x[0]), str(x[1]))
            for x in (d.get("aprobadas") or ())
            if isinstance(x, (list, tuple)) and len(x) >= 2
        )
    revisado = fase.revisado
    if (
        origen == "independiente"
        and veredicto != "no_concluyente"
        and fase.producto is not None
        and ref.version == fase.producto.firma
    ):
        revisado = fase.producto
    estado = fase.estado
    if veredicto == "aprobado" and fase.estado in ("revision", "entrega"):
        estado = "entrega" if e.entrega.nivel != "terminado" else "revision"
    nueva = replace(
        fase,
        hallazgos=hallazgos,
        aprobadas=aprobadas,
        revisado=revisado,
        revision_vigente=ref if origen != "deepseek" else fase.revision_vigente,
        deepseek_vigente=ref if origen == "deepseek" else fase.deepseek_vigente,
        estado=estado,
        inspeccion_pendiente=(
            str(d.get("inspeccion_faltante") or "").strip()
            if veredicto == "no_concluyente"
            else ""
        ),
    )
    cambios = {
        "ultimo_hecho": f"revisión {origen}: {veredicto}",
        "hora": hora or e.hora,
    }
    if veredicto == "no_concluyente" and d.get("evidencia"):
        cambios["causa"] = str(d["evidencia"])[:1000]
    return _con_progreso(replace(con_fase(e, nueva), **cambios))


def _entrega_externa(e: Encargo, d: dict, hora: str) -> Encargo:
    e = _fase_de_evento(e, d)
    fase = e.fase_actual
    if fase is None:
        raise TransicionIlegal("no hay fase para la entrega")
    paso = str(d.get("paso") or "")
    sha = str(d.get("sha") or "")
    candidato = fase.nivel_alcanzado
    if paso in ("pr_abierto", "pr_listo"):
        candidato = "pr_abierto"
    elif paso == "deepseek_leido":
        if not bloqueantes_abiertos(e, "deepseek"):
            candidato = "pr"
    elif paso == "integrado":
        candidato = "integrado"
    elif paso == "desplegado":
        candidato = "desplegado"
    elif paso == "sin_cambios":
        candidato = "sin_cambios"
    if paso != "sin_cambios" and _ORDEN_NIVEL.get(candidato, 0) < _ORDEN_NIVEL.get(fase.nivel_alcanzado, 0):
        candidato = fase.nivel_alcanzado
    producto = fase.producto
    if paso == "commit" and producto is not None and sha:
        producto = replace(producto, sha_git=sha)
    nueva = replace(
        fase,
        nivel_alcanzado=candidato,
        pr_numero=int(d.get("numero") or fase.pr_numero or 0),
        pr_sha=sha
        if paso in ("commit", "push", "pr_abierto", "pr_listo") and sha
        else fase.pr_sha,
        deepseek_sha=sha if paso == "deepseek_leido" and sha else fase.deepseek_sha,
        integrado_sha=(
            str(d.get("recibo") or sha)
            if paso == "integrado" and (d.get("recibo") or sha)
            else fase.integrado_sha
        ),
        rama=str(d.get("rama") or fase.rama),
        base=str(d.get("base") or fase.base),
        evidencia=(
            str(d.get("evidencia") or fase.evidencia)
            if paso == "sin_cambios"
            else fase.evidencia
        ),
        producto=producto,
        estado="entrega" if fase.estado in ("revision", "entrega") else fase.estado,
    )
    return replace(
        con_fase(e, nueva),
        ultimo_hecho=f"entrega externa: {paso}",
        hora=hora or e.hora,
    )


def _preparar_con_entrada(
    fases: tuple[Fase, ...], preparada: Fase, firma: str
) -> tuple[Fase, ...]:
    """Fija la versión exacta de la fase preparada en quienes la consumen."""
    salida = []
    for fase in fases:
        if fase.id == preparada.id:
            salida.append(preparada)
            continue
        if firma and any(dep == preparada.id for dep, _version in fase.requiere):
            salida.append(
                replace(
                    fase,
                    requiere=tuple(
                        (dep, version or firma) if dep == preparada.id else (dep, version)
                        for dep, version in fase.requiere
                    ),
                )
            )
            continue
        salida.append(fase)
    return tuple(salida)


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
        e = asegurar_fase(e)
        pedida = str(d.get("fase") or "")
        if pedida and pedida != e.fase_activa:
            raise TransicionIlegal("el cierre pertenece a otra fase")
        intento = str(d.get("intento") or "")
        if intento and e.intento and intento != e.intento:
            raise TransicionIlegal("el cierre pertenece a otro intento")
        fase = e.fase_actual
        if fase is None:
            raise TransicionIlegal("no hay fase que cerrar")
        if e.entrega.nivel != "terminado" and not nivel_cumplido(e):
            return replace(
                e,
                evidencia=evidencias,
                hora=hora or e.hora,
                ultimo_hecho=evidencia,
                siguiente="completar la entrega externa autorizada",
            )
        nivel = e.nivel_alcanzado if e.nivel_alcanzado != "ninguno" else "terminado"
        preparada = replace(
            fase, estado="preparada", nivel_alcanzado=nivel, evidencia=evidencia
        )
        firma = fase.producto.firma if fase.producto else ""
        fases = _preparar_con_entrada(e.fases, preparada, firma)
        disponible = _fase_disponible(fases)
        siguiente = disponible.id if disponible is not None else ""
        pendientes = [f for f in fases if f.estado != "preparada"]
        if e.estado in ("cancelado", "deteniendo", "detenido"):
            activa = siguiente or (pendientes[0].id if pendientes else fase.id)
            return replace(
                e,
                fases=fases,
                fase_activa=activa,
                evidencia=evidencias,
                hora=hora or e.hora,
                ultimo_hecho=evidencia,
            )
        if disponible is not None:
            return replace(
                e,
                fases=fases,
                estado="recibido",
                fase_activa=siguiente,
                rol="responsable",
                estrategia="investigar",
                motivo="",
                siguiente=f"trabajar la fase {siguiente}",
                evidencia=evidencias,
                intentos_nuevos=True,
                escrituras=e.autoridad,
                ultimo_hecho=f"fase {fase.id} preparada; continúa la fase {siguiente}",
                hora=hora or e.hora,
            )
        if pendientes:
            return replace(
                e,
                fases=fases,
                estado="recibido",
                fase_activa=pendientes[0].id,
                rol="",
                estrategia="investigar",
                motivo="",
                siguiente="esperar a que una dependencia se prepare",
                evidencia=evidencias,
                intentos_nuevos=True,
                escrituras=e.autoridad,
                ultimo_hecho=f"fase {fase.id} preparada; quedan dependencias sin preparar",
                hora=hora or e.hora,
            )
        return replace(
            e,
            fases=fases,
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


def bitacora_legible(e: Encargo) -> list[dict]:
    """Vista del registro de decisiones. No es otra fuente ni otro juez."""
    fases = {f.id: f for f in e.fases}
    filas = []
    for decision in e.decisiones:
        resultado = ""
        for fase_id in decision.fases:
            fase = fases.get(fase_id)
            if fase is not None and fase.resultado:
                resultado = fase.resultado
                break
        if not resultado and e.fase_actual is not None:
            resultado = e.fase_actual.resultado
        filas.append(
            {
                "decision": decision.eleccion,
                "motivo": decision.motivo,
                "evidencia": decision.evidencia,
                "resultado": resultado or "registrada",
                "fases": list(decision.fases),
                "hora": decision.hora,
            }
        )
    return filas


def seguimiento_de(e: Encargo) -> dict:
    fase = e.fase_actual
    necesidad = next(
        (
            s.como_dict()
            for s in e.solicitudes
            if s.estado in ("pendiente", "pregunta", "respuesta")
        ),
        None,
    )
    ref = e.revision_vigente
    espera = None
    if e.estado == "esperando_reintento" or e.impedimento:
        espera = {
            "causa": e.impedimento_causa or e.motivo,
            "clase": e.impedimento or "servicio",
            "reanuda_en": e.reanuda_en,
            "condicion": e.impedimento_condicion,
        }
    return {
        "fase_actual": fase.id if fase else "",
        "estado_fase": fase.estado if fase else "",
        "resultado_alcanzado": (fase.resultado if fase else "") or "",
        "necesidad_pendiente": necesidad,
        "decision": e.decisiones[-1].como_dict() if e.decisiones else None,
        "revision": (
            {
                "id": ref.revision_id,
                "veredicto": ref.veredicto,
                "version": ref.version,
            }
            if ref
            else None
        ),
        "pr": {"numero": e.pr_numero, "sha": e.pr_sha} if e.pr_numero else None,
        "espera": espera,
    }


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
        "agentes_autorizados": list(dict.fromkeys((
            *e.agentes, *(agente for _rol, agente, _modelo in e.modelos_solicitados)
        ))),
        "modelos_solicitados": {rol: modelo for rol, _agente, modelo in e.modelos_solicitados},
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
            "rama": e.rama_entrega,
            "base": e.entrega.base,
            "pr": {"numero": e.pr_numero, "sha": e.pr_sha},
            "deepseek": {"sha": e.deepseek_sha, "leida": bool(e.deepseek_sha)},
        },
        "fases": [f.como_dict() for f in e.fases],
        "fase_activa": e.fase_activa,
        "solicitudes": [s.como_dict() for s in e.solicitudes],
        "necesidad_actual": next(
            (
                s.como_dict()
                for s in e.solicitudes
                if s.estado in ("pendiente", "pregunta", "respuesta")
            ),
            None,
        ),
        "decisiones": [d.como_dict() for d in e.decisiones],
        "bitacora": bitacora_legible(e),
        "seguimiento": seguimiento_de(e),
        "autorizaciones": [
            {
                "recurso": a[0],
                "operacion": a[1],
                "fase": a[2],
                "intento": a[3],
                "texto": a[4],
                "hora": a[5],
            }
            for a in e.autorizaciones
        ],
        "objetivo": {
            "fases": len(e.fases),
            "preparadas": len(e.fases_preparadas()),
            "completo": e.objetivo_completo(),
            "resultado_actual": e.fase_actual.resultado if e.fase_actual else "",
        },

        "ultimo_hecho": e.ultimo_hecho,
        "hora": e.hora,
        "motivo": e.motivo,
        "siguiente": e.siguiente,
        "preguntas": list(e.preguntas),
        "impedimento": (
            {
                "clase": e.impedimento,
                "fuente": e.impedimento_fuente,
                "causa": e.impedimento_causa,
                "condicion": e.impedimento_condicion,
            }
            if e.impedimento
            else None
        ),
        "impedimentos": [
            {
                "clase": i.clase,
                "fuente": fuente_de_impedimento(i.clase),
                "causa": i.causa,
                "condicion": i.condicion,
                "verificable": i.verificable,
            }
            for i in e.impedimentos
        ],
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
    # Con un impedimento del entorno pendiente, otro proceso repetiría el mismo
    # trabajo sin información nueva. La huella sólo se limpia con evidencia.
    if e.impedimento:
        return {"matar": False, "lanzar": False, "eventos": []}
    espera = espera_inspeccion_consumida(e)
    if espera is not None:
        return {
            "matar": False, "lanzar": False,
            "eventos": [{"kind": "impedimento", "data": {**espera, "hora": hora}}],
        }
    if inspeccion_bloquea(e):
        return {"matar": False, "lanzar": False, "eventos": []}
    if e.fases and all(f.estado == "preparada" for f in e.fases):
        return {"matar": False, "lanzar": False, "eventos": []}
    if e.fases and _fase_disponible(e.fases) is None:
        return {"matar": False, "lanzar": False, "eventos": []}
    if e.estado == "necesita_usuario" and not e.pendientes:
        return {"matar": False, "lanzar": False, "eventos": []}
    if arbol == "incierto":
        return {"matar": False, "lanzar": False, "eventos": []}
    if arbol == "vivo":
        if herramienta_viva:
            return {"matar": False, "lanzar": False, "eventos": []}
        if herramienta_viva is None:
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
        lineas.append(
            "Prohibiciones expresas del propietario. No las trates como hipótesis del plan:\n"
            + "\n".join(e.restricciones)
        )
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


def _bloque_fases(e: Encargo) -> list[str]:
    if not e.fases:
        return []
    lineas = ["Fases del encargo:"]
    for indice, fase in enumerate(e.fases, 1):
        marca = " (activa)" if fase.id == e.fase_activa else ""
        firma = f" producto={fase.producto.firma[:12]}" if fase.producto else ""
        lineas.append(
            f"{indice}. {fase.id} [{fase.estado}] {fase.resultado}{firma}{marca}"
        )
    fase = e.fase_actual
    if fase is not None:
        lineas.append(
            f"Trabaja solo la fase activa ({fase.id}): {fase.resultado}. "
            "No ejecutes fases posteriores: cada una se organiza y se revisa por separado."
        )
    return lineas


def prompt_responsable(e: Encargo) -> str:
    lineas = [
        (
            "Eres el responsable de este encargo. Sigue poteto-mode en este espacio. "
            "Carga la skill empaquetada del runtime y limita las herramientas al espacio concedido. "
            "Sharpen, Architect, Arena y Figure-it-out se usan cuando el trabajo los necesita; "
            "no son una cadena obligatoria. Una hipótesis del plan no es una prohibición: "
            "las decisiones técnicas dentro del objetivo y los permisos concedidos las tomas tú "
            "y las registras con decision.registrar. Solo una prohibición expresa del propietario, "
            "o una decisión que el pedido reserva al usuario, necesita su respuesta. "
            "No devuelvas el plan por un detalle técnico ordinario ni por redacción menor."
        )
    ]
    lineas += _bloques_comunes(e)
    if not e.fases:
        lineas.append(
            "Este pedido todavía no tiene fases. Antes de implementar, organiza el trabajo "
            "en fases ordenadas y escribe .interno/fases.json. Cada fase declara un "
            "resultado observable, no una lista de archivos ni comandos. El usuario no "
            "escribe este formato. Ejemplo:\n"
            '{"fases": [{"id": "datos", "resultado": "consultar y conservar las metricas diarias"},'
            ' {"id": "interfaz", "resultado": "mostrar las ganancias diarias", "requiere": ["datos"]}]}\n'
            "Después trabaja solo la primera fase. Un formato inválido se te devuelve para corregirlo."
        )
    lineas += _bloque_fases(e)
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
    fase = e.fase_actual
    if fase is not None:
        anteriores = [f for f in e.fases if f.id != fase.id]
        if anteriores and all(f.estado == "preparada" for f in anteriores):
            lineas.append(
                f"Revisa la fase final {fase.id}: {fase.resultado}. Además del resultado "
                "de esta fase, comprueba que los resultados preparados en conjunto cumplen "
                "el pedido original. No exijas que esta fase implemente todo el plan."
            )
        else:
            lineas.append(
                f"Esta revisión cubre solo la fase {fase.id}: {fase.resultado}. "
                "Las fases anteriores ya están preparadas: no las vuelvas a revisar salvo "
                "una relación concreta con lo corregido."
            )
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
