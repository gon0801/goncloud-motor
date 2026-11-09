"""Pedido, procedimiento, intento y efecto son registros distintos.

El reducer no llama a parse_plan ni a machine.estado. Un texto del agente
no es un resultado comprobado.
"""

from __future__ import annotations

import hashlib
import json
import re
import socket
from dataclasses import dataclass, field, replace
from pathlib import Path

from .catalogo_agentes import identidad_modelo

ESTADOS = (
    "recibido",
    "ejecutando",
    "esperando_reintento",
    "conciliando",
    "deteniendo",
    "detenido",
    "cancelado",
    "terminado",
)

_CERRADOS = ("deteniendo", "detenido", "cancelado", "terminado")

# Los marcadores son observaciones del motor, no hechos del encargo: no cortan
# las series de reintentos ni cuentan como movimiento del encargo.
_EVENTOS_DE_MARCADOR = (
    "incorporado",
    "quietud_vigilada",
    "deepseek_lectura",
    "deepseek_cerrado",
    "ci_sondeo",
)

# Eventos que pueden caer entre un reintento y el siguiente sin ser un cambio.
_ENTRE_REINTENTOS = (
    "plazo",
    "espera",
    "instrumento_fallo",
    "reserva",
    "reserva_cerrada",
    "contexto_entrega",
)

_NOTA_COPIA_SESION = "copia de sesión "

# Un fallo de red o de servicio no es una causa reproducible: sigue reintentando.
_FALLOS_TRANSITORIOS = (
    "could not resolve host", "temporary failure in name resolution",
    "network is unreachable", "connection timed out", "connection reset",
    "connection refused", "connection closed", "operation timed out",
    "remote end hung up unexpectedly", "the remote end hung up",
    "early eof", "broken pipe",
    "resource temporarily unavailable", "http 429", "http 500", "http 502",
    "http 503", "http 504", "returned error: 429", "returned error: 500",
    "returned error: 502", "returned error: 503", "returned error: 504",
)

_FALLOS_PERMANENTES = (
    "http 400", "http 401", "http 403", "http 404", "http 413", "http 422",
    "returned error: 400", "returned error: 401", "returned error: 403",
    "returned error: 404", "returned error: 413", "returned error: 422",
)


def _fallo_transitorio(causa: str) -> bool:
    texto = str(causa or "").lower()
    if any(marca in texto for marca in _FALLOS_PERMANENTES):
        return False
    if "rpc failed" in texto:
        return True
    return any(marca in texto for marca in _FALLOS_TRANSITORIOS)


def _clase_de_fallo(causa: str) -> str:
    """La operación que falló, sin el detalle que cambia entre reintentos."""
    operacion = str(causa).split(": ", 1)[0]
    return re.sub(r"[0-9a-f]{7,40}", "#", operacion).strip().lower()


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
    # Modelo concreto que falló. Vacío pausa el agente entero (compatibilidad).
    modelo: str = ""


def _con_pausa(
    previas: tuple[PausaAgente, ...], nueva: PausaAgente
) -> tuple[PausaAgente, ...]:
    """Reemplaza solo la pausa del mismo modelo, no las de los demás modelos del
    agente: una cuota agotada en un modelo no borra la pausa de otro."""
    if not nueva.modelo:
        quedan = tuple(p for p in previas if p.agente != nueva.agente)
    else:
        identidad = identidad_modelo(nueva.modelo)
        quedan = tuple(
            p
            for p in previas
            if not (
                p.agente == nueva.agente
                and p.modelo
                and identidad_modelo(p.modelo) == identidad
            )
        )
    return quedan + (nueva,)


CRITERIOS = ("seguridad", "datos", "comportamiento", "prueba_exigida", "operativo")
NIVELES = ("terminado", "pr", "integrado", "desplegado")
# Marca una fase que siguió sin la revisión externa tras el tope: no hay
# comentario leído, pero el SHA ya no se vuelve a esperar. Una relectura
# posterior con comentarios reales sustituye esta huella.
HUELLA_DEEPSEEK_SIN_REVISION = "sin-revision"

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
    # Evidencia con la que el propietario lo declaró cerrado. Un hallazgo con
    # esta marca no se reabre sin una reproducción nueva.
    cierre_evidencia: str = ""

    def como_dict(self) -> dict:
        datos = {
            "id": self.id,
            "gravedad": self.gravedad,
            "criterio": self.criterio,
            "titulo": self.titulo,
            "reproduccion": self.reproduccion,
            "ancla": self.ancla,
            "origen": self.origen,
            "estado": self.estado,
        }
        if self.cierre_evidencia:
            datos["cierre_evidencia"] = self.cierre_evidencia
        return datos

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
            cierre_evidencia=str(d.get("cierre_evidencia") or ""),
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
    lector_version: int = 0


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


def _epochs(valor) -> tuple[tuple[str, float], ...]:
    if not isinstance(valor, dict):
        return ()
    salida = []
    for sha, epoch in valor.items():
        try:
            salida.append((str(sha), float(epoch)))
        except (TypeError, ValueError):
            continue
    return tuple(sorted(salida))


def _epochs_dict(valor: tuple[tuple[str, float], ...]) -> dict[str, float]:
    return dict(valor)


@dataclass(frozen=True)
class LecturaDeepSeek:
    """La última lectura de DeepSeek de un SHA: el espaciado y el error viven
    en el registro, no en `deepseek_lectura.json`."""

    sha: str = ""
    epoch: float = 0.0
    error: str = ""
    aviso: float = 0.0

    @classmethod
    def de_dict(cls, d: dict) -> "LecturaDeepSeek":
        try:
            epoch = float(d.get("epoch") or 0)
        except (TypeError, ValueError):
            epoch = 0.0
        try:
            aviso = float(d.get("aviso") or 0)
        except (TypeError, ValueError):
            aviso = 0.0
        return cls(
            sha=str(d.get("sha") or ""),
            epoch=epoch,
            error=str(d.get("error") or ""),
            aviso=aviso,
        )

    def como_dict(self) -> dict:
        return {
            "sha": self.sha,
            "epoch": self.epoch,
            "error": self.error,
            "aviso": self.aviso,
        }


@dataclass(frozen=True)
class MarcasDeepSeekCerrado:
    """Los SHA ya leídos de un encargo cerrado y desde cuándo se sondea."""

    shas: tuple[tuple[str, float], ...] = ()
    inicios: tuple[tuple[str, float], ...] = ()
    desde: float = 0.0

    @classmethod
    def de_dict(cls, d: dict) -> "MarcasDeepSeekCerrado":
        try:
            desde = float(d.get("desde") or 0)
        except (TypeError, ValueError):
            desde = 0.0
        shas = _epochs(d.get("shas"))
        inicios = _epochs(d.get("inicios"))
        # Formato anterior, de una sola fase: se conserva al actualizar.
        sha = str(d.get("sha") or "")
        try:
            epoch = float(d.get("epoch") or 0)
        except (TypeError, ValueError):
            epoch = 0.0
        if sha and epoch:
            if sha not in dict(shas):
                shas = tuple(sorted(shas + ((sha, epoch),)))
            if sha not in dict(inicios):
                inicios = tuple(sorted(inicios + ((sha, epoch),)))
        return cls(shas=shas, inicios=inicios, desde=desde)

    def como_dict(self) -> dict:
        return {
            "shas": _epochs_dict(self.shas),
            "inicios": _epochs_dict(self.inicios),
            "desde": self.desde,
        }


@dataclass(frozen=True)
class SondeoCI:
    """El avance del sondeo del CI de un SHA. Reemplaza `ci-sondeo.json`."""

    sha: str = ""
    primero: str = ""
    ultimo: str = ""
    sondeos: int = 0
    errores_limite: int = 0

    @classmethod
    def de_dict(cls, d: dict) -> "SondeoCI":
        return cls(
            sha=str(d.get("sha") or ""),
            primero=str(d.get("primero") or ""),
            ultimo=str(d.get("ultimo") or ""),
            sondeos=int(d.get("sondeos") or 0),
            errores_limite=int(d.get("errores_limite") or 0),
        )

    def como_dict(self) -> dict:
        return {
            "sha": self.sha,
            "primero": self.primero,
            "ultimo": self.ultimo,
            "sondeos": self.sondeos,
            "errores_limite": self.errores_limite,
        }


FASE_IMPLICITA = "fase_unica"
# Revisiones no_concluyente seguidas, sobre la misma versión y pidiendo lo mismo,
# tras las cuales el encargo pide ayuda al propietario en vez de otro turno.
TOPE_NO_CONCLUYENTES = 3
# `bloqueada` es terminal: la fase agotó sus recuperaciones con un impedimento
# sin resolver y no habilita a sus dependientes.
ESTADOS_FASE = ("pendiente", "trabajo", "revision", "entrega", "preparada", "bloqueada")


@dataclass(frozen=True)
class Fase:
    """Una fase conserva su propio resultado, revisiones y entrega.

    El estado avanza producto -> revision -> entrega -> preparada. Una fase
    preparada ya no se vuelve a ejecutar; sus datos son los que consume la
    siguiente fase. Una fase bloqueada tampoco se vuelve a ejecutar: agotó sus
    recuperaciones con un impedimento sin resolver.
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
    # Último paso de la entrega externa que cambia el nivel. Es un hecho del
    # registro, no el nivel: `nivel_de` lo combina con el PR, la lectura de
    # DeepSeek y los bloqueantes abiertos.
    ultimo_paso_entrega: str = ""
    pr_numero: int = 0
    pr_sha: str = ""
    deepseek_sha: str = ""
    # Huella del comentario de DeepSeek que se leyó para `deepseek_sha`: el
    # motor relee el SHA vigente cuando el comentario cambia, no solo el SHA.
    deepseek_huella: str = ""
    # Último fallo de la relectura de GitHub, hasta que una lectura vuelva a salir
    # bien. Es para quien mira (`ver`): ningún prompt lo lee.
    deepseek_error: str = ""
    integrado_sha: str = ""
    desplegado_recibo: str = ""
    requiere_despliegue: bool | None = None
    desplegar: str = ""
    checklist: str = ""
    rollback: str = ""
    despliegue_fuente: str = ""
    despliegue_huella: str = ""
    rama: str = ""
    base: str = ""
    evidencia: str = ""
    # Causa del bloqueo, en palabras del impedimento que agotó las
    # recuperaciones. Solo la usan las fases bloqueadas.
    bloqueo_causa: str = ""
    # Pendientes propios de la fase: se guardan aquí, no en el encargo, para
    # que el PR y el aviso de otra fase no los copie.
    pendientes: tuple[str, ...] = ()
    # Notas del motor producidas por esta fase (la falta de una revisión, una
    # publicación fallida): el PR de otra fase no las copia.
    notas: tuple[str, ...] = ()
    inspeccion_pendiente: str = ""
    inspeccion_solicitud: str = ""
    inspeccion_entregada: str = ""
    inspecciones_consumidas: tuple[str, ...] = ()
    entrega_bloqueada_sha: str = ""
    # Resultado del CI real del PR, leído una sola vez después de entregar.
    # `ci_pr_estado` es «reintentando» mientras corre el único reintento de un
    # rojo de tubería; `ci_pr_clase` y `ci_pr_reintento` alimentan el aviso llano.
    ci_pr_sha: str = ""
    ci_pr_estado: str = ""
    ci_pr_fallas: tuple[str, ...] = ()
    ci_pr_causa: str = ""
    ci_pr_clase: str = ""
    ci_pr_reintento: str = ""
    ci_pr_aviso: str = ""
    cerrados: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not str(self.id or "").strip():
            raise TransicionIlegal("fase sin id")
        if self.estado not in ESTADOS_FASE:
            raise TransicionIlegal(f"estado de fase desconocido: {self.estado}")
        if self.estado == "preparada" and not (self.evidencia or self.revision_vigente):
            raise TransicionIlegal(f"la fase {self.id} no se prepara sin resultado comprobado")
        if self.estado == "bloqueada" and not (self.evidencia and self.bloqueo_causa):
            raise TransicionIlegal(
                f"la fase {self.id} no se bloquea sin causa y evidencia"
            )

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
            "requiere_despliegue": self.requiere_despliegue,
            "despliegue": {
                "desplegar": self.desplegar,
                "checklist": self.checklist,
                "rollback": self.rollback,
                "fuente": self.despliegue_fuente,
                "huella": self.despliegue_huella,
                "recibo": self.desplegado_recibo,
            },
            "pr": {"numero": self.pr_numero, "sha": self.pr_sha},
            "deepseek": {
                "sha": self.deepseek_sha,
                "leida": bool(self.deepseek_sha)
                and self.deepseek_huella != HUELLA_DEEPSEEK_SIN_REVISION,
                "error": self.deepseek_error,
            },
            "rama": self.rama,
            "base": self.base,
            "evidencia": self.evidencia,
            "bloqueo_causa": self.bloqueo_causa,
            "pendientes": list(self.pendientes),
            "notas": list(self.notas),
            "inspeccion_pendiente": self.inspeccion_pendiente,
            "inspeccion_solicitud": self.inspeccion_solicitud,
            "inspeccion_entregada": self.inspeccion_entregada,
            "inspecciones_consumidas": list(self.inspecciones_consumidas),
            "ci_pr": {
                "sha": self.ci_pr_sha,
                "estado": self.ci_pr_estado,
                "fallas": list(self.ci_pr_fallas),
                "causa": self.ci_pr_causa,
                "clase": self.ci_pr_clase,
                "reintento": self.ci_pr_reintento,
                "aviso": self.ci_pr_aviso,
            },
        }

    @property
    def nivel_alcanzado(self) -> str:
        return nivel_de(self)


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
    # Respuestas del propietario que llegaron cuando la solicitud ya no estaba
    # en `pregunta`: no cambian su estado, pero son información nueva.
    tardias: tuple[str, ...] = ()
    # Carpeta del host donde su herramienta deja `resultado.json`. El intento
    # que la lanzó puede no ser el vigente cuando termina: la solicitud la recuerda.
    carpeta: str = ""
    # Firma del producto cuando se lanzó la herramienta.
    version: str = ""
    # Huella de la configuración efectiva del recurso con que se lanzó.
    configuracion: str = ""

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
            "tardias": list(self.tardias),
            "carpeta": self.carpeta,
            "version": self.version,
            "configuracion": self.configuracion,
        }

    @property
    def respuestas_propietario(self) -> tuple[str, ...]:
        """Todo lo que el propietario contestó, completo y en orden."""
        return ((self.respuesta,) if self.respuesta else ()) + self.tardias


@dataclass(frozen=True)
class Impedimento:
    """Una causa pendiente con su identidad. `clase` nombra la operación que
    falla, no el texto del mensaje. `verificable` dice si el host puede
    comprobar por su cuenta que dejó de fallar. `agente` es el que se iba a lanzar
    cuando falló, si la causa depende de él (su shell): se vuelve a probar con ese
    mismo agente y no se supone ninguno."""

    clase: str
    causa: str
    condicion: str
    verificable: bool = True
    agente: str = ""


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
    # Restricciones estructuradas desde la entrada: rutas que el pedido permite
    # o prohíbe tocar. Se comparan por ruta, no por prosa.
    permisos: tuple[str, ...] = ()
    prohibiciones: tuple[str, ...] = ()
    ajustes: tuple[str, ...] = ()
    # Cuántos ajustes llevaba el prompt del último intento que llegó a abrirse.
    # Un ajuste posterior no lo vio ningún intento: no se cierra ni se entrega.
    ajustes_vistos: int = 0
    # Cuántos ajustes lleva el prompt de la reserva en curso. Sólo pasa a
    # `ajustes_vistos` cuando el intento se abre; si la reserva se cierra sin
    # abrirlo, no cuenta nada.
    ajustes_reservados: int = 0
    # Cuántos ajustes quedaron dichos en la nota de cierre porque ningún intento
    # podía recibirlos. No son vistos: si un intento vuelve a poder lanzarse, la
    # corrección le llega.
    ajustes_anotados: int = 0
    procedimiento: tuple[str, ...] = ()
    estado: str = "recibido"
    ultimo_hecho: str = ""
    hora: str = ""
    motivo: str = ""
    siguiente: str = ""
    preguntas: tuple[str, ...] = ()
    pendientes: tuple[str, ...] = ()
    intento: str = ""
    intento_interrumpido: bool = False
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
    # Tope ya comprobado por una mirada de solo lectura del remoto o de la
    # máquina: "pr" (no se puede integrar) o "integrado" (no se puede
    # desplegar). Vacío = sin limitación conocida: el objetivo es el nivel
    # autorizado. Nunca supera a `entrega.nivel`; su motivo vive en el evento
    # `nivel_efectivo` que lo fijó.
    nivel_efectivo: str = ""
    # El motivo en palabras llanas de `nivel_efectivo`, para el aviso y `vista`.
    nivel_efectivo_motivo: str = ""
    modelos: tuple[tuple[str, str, str], ...] = ()
    modelos_fase: tuple[tuple[str, str, str], ...] = ()
    modelos_solicitados: tuple[tuple[str, str, str], ...] = ()
    sesion_responsable: str = ""
    sesion_revisor: str = ""
    epoca_responsable: int = 0
    epoca_revisor: int = 0
    viva_responsable: bool = False
    viva_revisor: bool = False
    sesion_modo: str = ""
    poteto_digest: str = ""
    # Los bloqueos (ids) abiertos cuando el trabajo volvió al responsable por última
    # vez: lo que el responsable ya sabe. Uno abierto que no está aquí es información
    # nueva, no «el mismo bloqueo».
    devuelto_con: tuple[str, ...] = ()
    # Generación de la última vuelta que el freno ya resolvió: una vuelta cuenta una
    # sola vez, entren las fuentes que entren en el barrido.
    vuelta_contada: int = 0
    # Revisiones no_concluyente seguidas sobre la misma versión que piden lo
    # mismo, y qué pidió la última (fase, versión, texto, recurso, objetivo, huella).
    no_concluyentes: int = 0
    no_concluyente_ref: tuple[str, ...] = ()
    # La escalera no se guarda como contador de vueltas: se guarda qué pasos ya
    # gastó cada rol sobre un conjunto de ids. La clave identifica ese conjunto
    # (los ids devueltos al responsable, o el pedido del revisor) y el número de
    # avances registrados; un avance o un hecho nuevo la estrena sin reiniciar
    # ningún campo.
    escalones: tuple[tuple[str, str, str], ...] = ()
    # Cuántos avances reales registró el encargo (bloqueante cerrado, primer
    # bloqueante, aprobación concluyente sin bloqueos). Es la época de la
    # escalera: crece sola con el hecho, no se reinicia a mano.
    avances: int = 0
    # Marcadores que antes vivían en archivos y decidían fuera del registro.
    # Cada uno es ahora un hecho: el reducer guarda su última marca y el ciclo
    # la lee sin recorrer eventos ni abrir un `.json`.
    incorporado: tuple[int, ...] = ()
    quietud_vigilada: tuple[int, int, float, float] = (0, 0, 0.0, 0.0)
    lectura_deepseek: LecturaDeepSeek = field(default_factory=LecturaDeepSeek)
    deepseek_cerrado: MarcasDeepSeekCerrado = field(
        default_factory=MarcasDeepSeekCerrado
    )
    sondeo_ci: SondeoCI = field(default_factory=SondeoCI)
    # Hechos derivados que antes se contaban recorriendo el log en `ciclo.py`.
    # El reducer los actualiza al plegar; los consumidores leen el fold.
    fallos_repo: tuple[str, int] = ("", 0)
    fallos_entrega: tuple[str, int] = ("", 0)
    fallos_instrumento: tuple[tuple[str, str, str, str], ...] = ()
    apariciones: tuple[tuple[str, int], ...] = ()
    fallos_proveedor: tuple[tuple[str, int], ...] = ()
    fallos_barrido: int = 0
    ultima_firma: str = ""
    artefactos_instrumento: tuple[tuple[str, str], ...] = ()
    lecturas_deepseek: tuple[tuple[str, str, int], ...] = ()
    cuerpos_publicados: tuple[tuple[str, str, str], ...] = ()
    preguntas_pedidas: tuple[str, ...] = ()
    hora_cierre: str = ""
    evidencias_revision: tuple[tuple[str, str], ...] = ()
    internos_producto: tuple[str, ...] = ()
    # Pendientes que se escriben en el cuerpo del PR cuando el encargo entrega
    # sin que el revisor haya concluido o sin la revisión externa.
    pendientes_entrega: tuple[str, ...] = ()
    cierre_forzado_fase: Fase | None = None
    cierre_forzado_huella: str = ""
    reaperturas: int = 0
    # Herramientas que fallaron igual tres veces y no se vuelven a usar en este
    # encargo. El trabajo sigue con la evidencia que haya.
    herramientas_no_disponibles: tuple[str, ...] = ()
    # Notas que acompañan al cuerpo del PR (disputas, preguntas contestadas,
    # herramientas caídas, tiempos agotados).
    notas_pr: tuple[str, ...] = ()
    # Hallazgos externos que una restricción expresa o el revisor dejaron fuera
    # de alcance: (id, título, razón). No bloquean; se escriben en el PR.
    fuera_de_alcance: tuple[tuple[str, str, str], ...] = ()
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
    despliegue_mapa: tuple[tuple[str, str], ...] = ()
    despliegue_archivo: str = ""
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
    def revision_vigente(self) -> RefRevision | None:
        fase = self.fase_actual
        return fase.revision_vigente if fase else None

    @property
    def nivel_alcanzado(self) -> str:
        return nivel_de(self.fase_actual)

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
    def deepseek_huella(self) -> str:
        fase = self.fase_actual
        return fase.deepseek_huella if fase else ""

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


def sesiones_de_fase_nueva(e: Encargo) -> Encargo:
    """Al empezar otra fase, ninguna sesión de la anterior continúa.

    Las dos épocas cambian para que el id de sesión de cada rol sea nuevo; los
    ids guardados se olvidan para que el lanzamiento no reanude un contexto que
    ya no corresponde. El resumen de lo anterior va en el prompt, armado desde
    el fold (pedido, ajustes, fases cerradas y hallazgos abiertos). La
    identidad del cierre vigente no se toca: sobrevive al avance de fase para
    que `reabrir` responda al cierre por fase que sigue vigente.
    """
    return replace(
        _sin_repeticiones(e),
        modelos_fase=(),
        firma_fallo="",
        repeticiones=0,
        vuelta_contada=0,
        devuelto_con=(),
        pendientes_entrega=(),
        avances=e.avances + 1,
        sesion_responsable="",
        sesion_revisor="",
        viva_responsable=False,
        viva_revisor=False,
        epoca_responsable=e.epoca_responsable + 1,
        epoca_revisor=e.epoca_revisor + 1,
        sesion_modo="nueva",
    )


def huella_entrega(
    texto: str,
    adjuntos: tuple[str, ...],
    restricciones: tuple[str, ...],
    permisos: tuple[str, ...] = (),
    prohibiciones: tuple[str, ...] = (),
) -> str:
    h = hashlib.sha256()
    h.update(texto.encode())
    h.update(b"\0")
    for parte in adjuntos + restricciones + permisos + prohibiciones:
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


def correcciones_sin_ver(e: Encargo) -> bool:
    return len(e.ajustes) > e.ajustes_vistos


def _ajustes_de_la_reserva(e: Encargo, d: dict) -> int:
    """Cuántos ajustes lleva el prompt de la reserva. Una reserva de intento
    de la versión anterior no anotaba el conteo, pero su prompt llevaba todos
    los ajustes registrados hasta entonces: leerla como cero dejaría los
    ajustes previos como no vistos y el cierre y la entrega volverían al
    implementador para siempre. La concesión del conductor tampoco abre un
    prompt, así que no cambia lo visto."""
    if "ajustes" in d:
        return int(d["ajustes"])
    if d.get("conductor"):
        return e.ajustes_vistos
    return len(e.ajustes)


def ajustes_cubiertos(e: Encargo) -> int:
    """Ajustes que un cierre puede dar por atendidos: los que vio un intento y
    los que la nota de cierre ya dijo que nadie pudo recibir."""
    return max(e.ajustes_vistos, e.ajustes_anotados)


def puede_recibir_intento(e: Encargo) -> bool:
    """Hay un intento que podría lanzarse: devolverle el trabajo sirve. Con
    todas las fases preparadas el cierre global no lanza a nadie."""
    if e.via == "directa" or not e.intentos_nuevos:
        return False
    return not e.fases or _fase_disponible(e.fases) is not None


def bloqueantes_abiertos(e: Encargo, origen: str = "") -> tuple[Hallazgo, ...]:
    fase = e.fase_actual
    return tuple(
        h
        for h in (fase.hallazgos if fase else ())
        if h.gravedad == "bloqueante"
        and h.estado == "abierto"
        and (not origen or h.origen == origen)
    )


_PASOS_QUE_DECIDEN_NIVEL = (
    "pr_abierto",
    "pr_listo",
    "commit",
    "push",
    "pr_con_pendientes",
    "deepseek_leido",
    "deepseek_sin_revision",
    "integrado",
    "desplegado",
    "sin_cambios",
)


def _bloqueantes_de_la_fase(fase: Fase, origen: str = "") -> tuple[Hallazgo, ...]:
    return tuple(
        h
        for h in fase.hallazgos
        if h.gravedad == "bloqueante"
        and h.estado == "abierto"
        and (not origen or h.origen == origen)
    )


def nivel_de(fase: Fase | None) -> str:
    """El nivel alcanzado sale de hechos registrados, no de un campo aparte.

    Contrato, una fila por caso:

    - sin PR: una fase preparada alcanzó `terminado`; el resto, `ninguno`;
    - PR con SHA: `pr_abierto` mientras la lectura de DeepSeek no cubra el SHA;
    - cambio de head (`commit`, `push`, `pr_abierto`, `pr_listo`): vuelve a
      `pr_abierto` aunque el SHA anterior estuviera leído;
    - lectura de DeepSeek del SHA vigente sin bloqueantes abiertos: `pr`;
    - `deepseek_sin_revision` (omisión tras el tope): `pr` si no quedan
      bloqueantes de DeepSeek abiertos;
    - un Critical, High o Medium de DeepSeek abierto en alcance: `pr_abierto`;
    - CI rojo de código sobre el SHA vigente: `pr_abierto` hasta corregirlo;
    - `pr_con_pendientes`: `pr` aunque la revisión no haya concluido;
    - `sin_cambios`: `sin_cambios`, solo satisface niveles sin efectos externos;
    - merge: `integrado`; despliegue: `desplegado`.

    La reapertura de la fase por una corrección vuelve a dejar
    `ultimo_paso_entrega` en `pr_abierto`; solo otra lectura de DeepSeek del
    SHA nuevo la sube. Así una relectura con bloqueantes baja el nivel sin una
    rama aparte y una corrección posterior no integra lo que no se revisó.
    """
    if fase is None:
        return "ninguno"
    paso = fase.ultimo_paso_entrega
    if paso == "desplegado":
        return "desplegado"
    if paso == "integrado":
        return "integrado"
    if paso == "sin_cambios":
        return "sin_cambios"
    if not fase.pr_numero:
        return "terminado" if fase.estado == "preparada" else "ninguno"
    if not fase.pr_sha:
        return "pr_abierto"
    if paso == "pr_con_pendientes":
        return "pr"
    if (
        fase.ci_pr_estado == "rojo"
        and fase.ci_pr_clase == "codigo"
        and fase.ci_pr_sha
        and fase.ci_pr_sha == fase.pr_sha
    ):
        return "pr_abierto"
    if (
        paso in ("deepseek_leido", "deepseek_sin_revision")
        and fase.deepseek_sha == fase.pr_sha
        and not _bloqueantes_de_la_fase(fase, "deepseek")
    ):
        return "pr"
    return "pr_abierto"


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
        # Un cierre del propietario no se reabre con la misma reproducción. Solo
        # una evidencia nueva (otra reproducción) vuelve a abrir el hallazgo.
        if previo.cierre_evidencia and h.estado == "abierto":
            nueva = " ".join(str(h.reproduccion or "").split())
            previa = " ".join(str(previo.reproduccion or "").split())
            cierre = " ".join(str(previo.cierre_evidencia or "").split())
            if not nueva or nueva in (previa, cierre):
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


def _bloqueos_abiertos(e: Encargo) -> set[str]:
    """Hallazgos bloqueantes de la fase."""
    return {h.id for h in bloqueantes_abiertos(e)}


def huella_bloqueo(e: Encargo) -> str:
    """Identidad de lo que sigue abierto. No incluye la firma del producto:
    cambiar archivos sin cerrar un bloqueante no es avance."""
    return hashlib.sha256("|".join(sorted(_bloqueos_abiertos(e))).encode()).hexdigest()


def _bloqueos_cerrados(e: Encargo) -> set[str]:
    fase = e.fase_actual
    return set(fase.cerrados) if fase is not None else set()


def bloqueos_nuevos(e: Encargo) -> set[str]:
    """Bloqueos abiertos que el responsable no ha visto: ni se le devolvieron ni se
    cerraron antes. Uno que se cerró y se reabrió no es nuevo."""
    return _bloqueos_abiertos(e) - set(e.devuelto_con) - _bloqueos_cerrados(e)


def revision_externa_pendiente(e: Encargo) -> bool:
    """Falta el veredicto de DeepSeek sobre el commit vigente. Mientras tanto una
    vuelta sin novedad no es una vuelta sin avance."""
    fase = e.fase_actual
    if fase is None or e.entrega.nivel == "terminado" or not fase.pr_numero:
        return False
    sha = fase.producto.sha_git if fase.producto else ""
    if not sha:
        return False
    return fase.deepseek_sha != sha


def hubo_avance(previo: Encargo, e: Encargo) -> bool:
    """Hay avance si entre `previo` y `e` se cerro un bloqueante o un check en
    rojo, o si aparece el primero (no hay con que compararlo). Uno nuevo junto
    a los que siguen abiertos no lo es. Cerrar de nuevo lo que ya se cerro una
    vez y se reabrio tampoco: un check que alterna rojo y verde, o un bloqueante
    que vuelve, no deja de ser el mismo bloqueo."""
    antes = _bloqueos_abiertos(previo)
    ahora = _bloqueos_abiertos(e)
    ya_cerrados = _bloqueos_cerrados(previo)
    return bool((antes - ahora) - ya_cerrados) or bool(
        ahora and not antes and ahora - ya_cerrados
    )


def _huella_operador(e: Encargo) -> str:
    """La información que el propietario aportó (ajustes y respuestas)."""
    partes = list(e.ajustes)
    for sol in e.solicitudes:
        partes.extend(sol.respuestas_propietario)
    return hashlib.sha256("\0".join(partes).encode()).hexdigest()


def _huella_escalera_revisor(e: Encargo) -> str:
    """El punto de la escalera del revisor, derivado de los hechos que la
    estrenaban en 874daed: un avance (ids o cerrados), un dato del propietario,
    una aprobación concluyente, una reapertura o un cambio de fase. No cambia
    con cada revisión no concluyente, que es lo que la escalera cuenta."""
    fase = e.fase_actual
    partes = [
        e.fase_activa,
        str(e.reaperturas),
        str(e.avances),
        _huella_operador(e),
    ]
    if fase is not None:
        partes += [
            huella_bloqueo(e),
            ",".join(sorted(fase.cerrados)),
            ",".join(sorted(f"{r}:{h}" for r, h in fase.aprobadas)),
            fase.revisado.firma if fase.revisado else "",
        ]
    return hashlib.sha256("\0".join(partes).encode()).hexdigest()


def clave_escalera(e: Encargo, rol: str) -> str:
    """El conjunto de hechos que define el escalón vigente de un rol.

    El del responsable son los ids abiertos y la información del propietario;
    el del revisor, ese mismo punto de avance más el pedido que repite. Un
    avance, un bloqueante nuevo, una aprobación concluyente o un dato del
    propietario cambia la clave y la escalera empieza sola: no hay contador que
    reiniciar.
    """
    rol = rol or "responsable"
    if rol == "revisor":
        ref = e.no_concluyente_ref[0] if e.no_concluyente_ref else ""
        return _prefijo_epoca(e, rol) + ref
    return _prefijo_epoca(e, rol) + huella_bloqueo(e)


def _prefijo_epoca(e: Encargo, rol: str) -> str:
    """Los hechos que reinician la escalera del rol, sin el punto de ids.

    Es la parte de la clave que comparten todos los puntos de la misma época;
    el punto vigente (los ids o el pedido) se agrega después. Una época nueva
    deja atrás los relevos de sus puntos anteriores.
    """
    rol = rol or "responsable"
    if rol == "revisor":
        return f"{_huella_escalera_revisor(e)}:revisor:"
    return (
        f"{e.fase_activa}:responsable:{e.avances}:{_huella_operador(e)}:"
    )


def _clave_del_sin_progreso(e: Encargo, d: dict, rol: str) -> str:
    """La identidad del punto que gasta un evento `sin_progreso`.

    Un corte de instrumento cuelga del punto vigente y conserva su causa
    (`instrumento:<clave>`): dos causas distintas no comparten escalón y la
    misma causa sí, aunque el producto y los bloqueantes sean los mismos. Los
    demás eventos se leen por los ids y los datos del propietario, como el
    resto de la escalera.
    """
    huella = str(d.get("huella") or "")
    if huella.startswith("instrumento:"):
        return clave_escalera(e, rol) + ":" + huella
    return clave_escalera(e, rol)


def pasos_escalera(
    e: Encargo, rol: str, clave: str | None = None
) -> tuple[str, ...]:
    rol = rol or "responsable"
    clave = clave_escalera(e, rol) if clave is None else clave
    return tuple(p for r, c, p in e.escalones if r == rol and c == clave)


def escalon_gastado(e: Encargo, rol: str, paso: str, clave: str | None = None) -> bool:
    return paso in pasos_escalera(e, rol, clave)


def siguiente_escalon(e: Encargo, rol: str, clave: str | None = None) -> str:
    """Qué toca en el punto vigente: diagnosticar, relevar o entregar."""
    pasos = pasos_escalera(e, rol, clave)
    if "relevo" in pasos or "pendientes" in pasos:
        return "pendientes"
    if "diagnostico" in pasos:
        return "relevo"
    return "diagnostico"


def con_escalon(e: Encargo, rol: str, clave: str, paso: str) -> Encargo:
    """Anota un paso de la escalera. El hecho queda en el registro y la clave
    dice a qué ids pertenece; no se guarda un contador aparte."""
    rol = rol or "responsable"
    if (rol, clave, paso) in e.escalones:
        return e
    return replace(e, escalones=e.escalones + ((rol, clave, paso),))


def relevos_de(e: Encargo, rol: str) -> int:
    """Cuántos relevos gastó el rol en el punto vigente.

    Cuenta el del punto de ids y los cortes de instrumento de ese mismo punto,
    que también rotan el modelo. Los relevos de un punto o una época anteriores
    no cuentan: la escalera ya los superó.
    """
    rol = rol or "responsable"
    punto = clave_escalera(e, rol)
    marca_instrumento = punto + ":instrumento:"
    return sum(
        1
        for r, clave, paso in e.escalones
        if r == rol
        and paso == "relevo"
        and (clave == punto or clave.startswith(marca_instrumento))
    )


def hubo_relevo(e: Encargo, rol: str = "") -> bool:
    return any(
        p == "relevo" and (not rol or r == rol) for r, _c, p in e.escalones
    )


def total_relevos(e: Encargo) -> int:
    return sum(1 for _r, _c, p in e.escalones if p == "relevo")


def ultima_huella_relevo(e: Encargo) -> str:
    for _r, clave, paso in reversed(e.escalones):
        if paso == "relevo":
            return clave
    return ""


def _con_progreso(previo: Encargo, e: Encargo, concluyente: bool = False) -> Encargo:
    """Reinicia la cuenta del freno al cerrar bloqueantes o resolver una
    revisión. Repetir una aprobación no resuelve lo que sigue bloqueando la
    entrega. Un avance real incrementa `avances`, la época que estrena la
    escalera de cada rol sin reiniciar campos."""
    aprobacion_nueva = concluyente and (
        previo.revision_vigente is None
        or previo.revision_vigente.veredicto != "aprobado"
    )
    avance = hubo_avance(previo, e) or (
        aprobacion_nueva and not _bloqueos_abiertos(e)
    )
    cerrados = _bloqueos_abiertos(previo) - _bloqueos_abiertos(e)
    fase = e.fase_actual
    if fase is not None and cerrados - set(fase.cerrados):
        e = con_fase(e, replace(fase, cerrados=tuple(sorted(set(fase.cerrados) | cerrados))))
    if not avance:
        return e
    # Un avance cierra la cuenta de revisiones no_concluyente repetidas y los
    # pendientes que dejó un relevo anterior. La época de la escalera crece y
    # los ids también cambian de clave: la escalera se estrena sola.
    return replace(
        _sin_repeticiones(e),
        avances=e.avances + 1,
        pendientes_entrega=(),
    )


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
    fase = e.fase_actual
    version = fase.producto.firma if fase and fase.producto else ""
    manifiesto = fase.producto.manifiesto if fase and fase.producto else ()
    if (
        fase is None
        or fase.revision_vigente is None
        or fase.revisado is None
        or fase.producto is None
    ):
        return AlcanceRevision(
            clase="completa",
            version=version,
            base_version="",
            abiertos=bloqueantes_abiertos(e, "independiente"),
            manifiesto=manifiesto,
        )
    base = dict(fase.revisado.manifiesto)
    actual = dict(manifiesto)
    tocadas = tuple(
        sorted(r for r in set(base) | set(actual) if base.get(r) != actual.get(r))
    )
    conservadas = tuple(r for r, h in fase.aprobadas if actual.get(r) == h)
    return AlcanceRevision(
        clase="correccion",
        version=version,
        base_version=fase.revisado.firma,
        abiertos=bloqueantes_abiertos(e, "independiente"),
        tocadas=tocadas,
        conservadas=conservadas,
        manifiesto=manifiesto,
    )


def revision_independiente_cubre_producto(e: Encargo) -> bool:
    """Solo la aprobación de Opus sobre la firma vigente cubre el producto.

    Una corrección que cambió el producto después de esa aprobación necesita
    su revisión final, aunque DeepSeek ya la haya aprobado: DeepSeek es una
    compuerta aparte del PR, no sustituye al revisor independiente.
    """
    ref, producto = e.revision_vigente, e.producto
    return bool(
        ref is not None
        and ref.veredicto == "aprobado"
        and producto is not None
        and ref.version == producto.firma
    )


def sin_nada_en_curso(e: Encargo) -> bool:
    """Encargo abierto que no explica por qué no avanza: no espera con hora, no
    pide al usuario, no está detenido, y ningún agente, reserva, impedimento
    registrado, inspección pendiente ni dependencia entre fases lo sostiene.
    Si además el barrido no lanza nada y el registro no se mueve, está quieto."""
    if (
        e.estado not in ("recibido", "ejecutando")
        or not e.intentos_nuevos
        or e.via == "directa"
        or e.proc is not None
        or e.reserva
        or e.impedimento
        or e.impedimentos
        or e.pendientes_entrega
        or (inspeccion_bloquea(e) and pregunta_de_inspeccion(e) is None)
        or any(
            s.estado in ("pendiente", "pregunta") and s is not pregunta_de_inspeccion(e)
            for s in e.solicitudes
        )
    ):
        return False
    # Una pregunta del propietario ya no es una espera: queda como aviso y el
    # barrido sigue. Un recibido sin pregunta tampoco se da por quieto.
    return not (
        e.fases
        and (
            all(f.estado == "preparada" for f in e.fases)
            or _fase_disponible(e.fases) is None
        )
    )


def nivel_objetivo(e: Encargo) -> str:
    """El nivel que el cierre exige de verdad: el autorizado, salvo la
    limitación ya comprobada por el sondeo de capacidad (nunca lo supera)."""
    return e.nivel_efectivo if e.nivel_efectivo else e.entrega.nivel


def limita_mas_que(nivel: str, vigente: str) -> bool:
    """True si `nivel` aprieta el tope `vigente` ("": sin limitación conocida).

    El nivel efectivo solo puede hacerse más restrictivo (h_6a425441): una
    determinación que no aprieta no relaja la que ya está comprobada."""
    if not nivel:
        return False
    if not vigente:
        return True
    return _ORDEN_NIVEL[nivel] < _ORDEN_NIVEL[vigente]


def nivel_cumplido(e: Encargo) -> bool:
    fase = e.fase_actual
    if e.nivel_alcanzado == "sin_cambios":
        # Sin cambios publicables sólo satisface niveles sin efectos externos;
        # integrado y desplegado exigen el nivel real alcanzado.
        return (
            fase is not None
            and fase.revisado is not None
            and nivel_objetivo(e) in ("terminado", "pr")
        )
    nivel = nivel_objetivo(e)
    if (
        nivel == "desplegado"
        and e.fase_actual is not None
        and e.fase_actual.requiere_despliegue is False
        and e.fase_actual.despliegue_fuente
    ):
        nivel = "integrado"
    return _ORDEN_NIVEL.get(e.nivel_alcanzado, 0) >= _ORDEN_NIVEL.get(nivel, 1)


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
    if ajuste_sin_ver_por_inspeccion(e):
        return False
    ident = fase.inspeccion_solicitud
    if not ident:
        return True
    sol = next((s for s in e.solicitudes if s.id == ident), None)
    if sol is None:
        # La liga apunta a una solicitud que nunca se registró (llegó tarde, con
        # el encargo detenido): nadie la va a resolver, no puede bloquear.
        return False
    if sol.estado == "pendiente":
        return True
    if sol.estado == "pregunta":
        # La consulta espera autorización, pero eso ya no detiene el motor: la
        # pregunta queda visible y la revisión sigue con la evidencia disponible.
        return False
    return respuesta_consumida(fase, marca_inspeccion_entregada(sol))


def ajuste_sin_ver_por_inspeccion(e: Encargo) -> bool:
    """El propietario agregó un ajuste que ningún intento ha visto todavía: es
    información nueva aunque la solicitud siga abierta. Abre el turno una sola
    vez, porque al abrirse el intento del revisor el ajuste queda visto."""
    fase = e.fase_actual
    return (
        fase is not None and bool(fase.inspeccion_pendiente) and correcciones_sin_ver(e)
    )


def pregunta_de_inspeccion(e: Encargo) -> Solicitud | None:
    """La solicitud ligada que espera al propietario y bloquea la fase."""
    fase = e.fase_actual
    if fase is None or not fase.inspeccion_pendiente or not inspeccion_bloquea(e):
        return None
    sol = next((s for s in e.solicitudes if s.id == fase.inspeccion_solicitud), None)
    return sol if sol is not None and sol.estado == "pregunta" else None


def marca_inspeccion_entregada(sol: Solicitud) -> str:
    marca = f"{sol.id}:{sol.estado}"
    return f"{marca}:r{len(sol.tardias)}" if sol.tardias else marca


def inspeccion_consumida(e: Encargo) -> Solicitud | None:
    fase = e.fase_actual
    if fase is None or not fase.inspeccion_pendiente or ajuste_sin_ver_por_inspeccion(e):
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


def reducir(estado: Encargo | None, evento: dict, nuevo: bool = True) -> Encargo:
    """Aplica un evento al estado y rechaza cualquier estado fuera de `ESTADOS`.

    `nuevo=True` es el camino de escritura: la propuesta se valida con las
    reglas de hoy. El registro plegado pasa `nuevo=False`, porque un evento
    guardado ya se aceptó y se aplica tal como entró.

    La validación del estado es del plegado, no de un campo aparte: un evento
    histórico que intente un estado retirado se rechaza como `TransicionIlegal`
    en vez de reabrir una espera que ya no existe."""
    resultado = _reducir(estado, evento, nuevo)
    if resultado.estado not in ESTADOS:
        raise TransicionIlegal(
            f"estado fuera del conjunto de la reducción: {resultado.estado}"
        )
    return resultado


def _reducir_evento(estado: Encargo | None, evento: dict, nuevo: bool = True) -> Encargo:
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
                permisos=tuple(d.get("permisos") or ()),
                prohibiciones=tuple(d.get("prohibiciones") or ()),
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
            permisos=tuple(d.get("permisos") or ()),
            prohibiciones=tuple(d.get("prohibiciones") or ()),
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
        pausa = PausaAgente(
            d["agente"], d["hasta"], d["causa"], d["relevo"],
            str(d.get("modelo") or ""),
        )
        return replace(
            e,
            pausas=_con_pausa(e.pausas, pausa),
            causa=d["causa"],
        )
    if kind == "proveedor_disponible":
        modelo = str(d.get("modelo") or "")
        if not modelo:
            return replace(
                e, pausas=tuple(p for p in e.pausas if p.agente != d["agente"])
            )
        return replace(
            e,
            pausas=tuple(
                p
                for p in e.pausas
                if not (
                    p.agente == d["agente"]
                    and p.modelo
                    and identidad_modelo(p.modelo) == identidad_modelo(modelo)
                )
            ),
        )
    if kind == "orden":
        return _orden(e, d, hora)
    if kind == "ajuste":
        if e.estado == "terminado":
            # Con el PR abierto la corrección vuelve al responsable en la misma
            # rama; integrado o desplegado ya no hay PR que corregir.
            if not e.pr_numero or e.nivel_alcanzado in ("integrado", "desplegado"):
                raise TransicionIlegal("el encargo ya terminó; no admite correcciones")
            e = _reabrir_en_el_pr(
                e,
                e.fase_actual,
                "corrección del propietario sobre el PR abierto",
                "aplicar la corrección en la misma rama y el mismo PR",
                hora,
            )
        if e.estado == "cancelado" or (
            e.estado == "deteniendo" and e.motivo == "cancelado"
        ):
            raise TransicionIlegal("el encargo está cancelado; no admite correcciones")
        # Un ajuste trae información nueva del operador. Sólo levanta las causas
        # que el host no puede comprobar: las demás siguen hasta verificarse.
        # Con el trabajo en curso no toca el reloj del último hecho: la
        # detección de una sesión colgada no se reinicia por una nota.
        en_curso = e.estado in ("recibido", "ejecutando", "conciliando")
        e = _con_impedimentos(
            e,
            tuple(i for i in e.impedimentos if i.verificable),
            "" if en_curso else hora,
        )
        fase = e.fase_actual
        abiertos = (
            [h for h in fase.hallazgos if h.estado == "abierto"]
            if fase is not None
            else []
        )
        ids = _resolver_cierres(d.get("cierra") or (), abiertos)
        if not ids:
            ids = _ids_cerrados_por_texto(d["texto"], abiertos)
        e, cerrados = _cerrar_hallazgos(e, ids, str(d.get("evidencia") or ""))
        e = replace(e, ajustes=e.ajustes + (d["texto"],))
        if cerrados:
            e = replace(
                e,
                ultimo_hecho=(
                    "el propietario declaro cerrados: " + ", ".join(cerrados)
                ),
            )
        e = _soltar_impedimento_de_inspeccion(_nueva_info_del_propietario(e))
        if e.estado == "detenido":
            return replace(
                e,
                hora=hora,
                ultimo_hecho="ajuste incorporado con el trabajo detenido",
                siguiente="seguir detenido hasta continuar",
            )
        return e
    if kind == "procedimiento":
        return replace(
            e,
            procedimiento=e.procedimiento + (d["nota"],),
            # La nota de la versión anterior traía este conteo como
            # "ajustes_vistos"; sólo la escribía la nota sin intento.
            ajustes_anotados=int(
                d.get("ajustes_anotados", d.get("ajustes_vistos", e.ajustes_anotados))
            ),
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
            ajustes_reservados=_ajustes_de_la_reserva(e, d),
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
        return replace(
            e,
            reserva=False,
            candidato="",
            ajustes_reservados=e.ajustes_vistos,
            hora=hora or e.hora,
        )
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
        modelos_fase = e.modelos_fase
        if modelo:
            registro_modelo = (rol_intento, str(d.get("agente") or e.agente), modelo)
            if not modelos or modelos[-1] != registro_modelo:
                modelos = modelos + (registro_modelo,)
            if not modelos_fase or modelos_fase[-1] != registro_modelo:
                modelos_fase = modelos_fase + (registro_modelo,)
        viva = (
            {"viva_revisor": True}
            if rol_intento == "revisor"
            else {"viva_responsable": True}
        )
        return replace(
            e,
            reserva=False,
            ajustes_vistos=max(e.ajustes_vistos, e.ajustes_reservados),
            rol=rol_intento,
            agente=str(d.get("agente") or e.agente),
            modelos=modelos,
            modelos_fase=modelos_fase,
            sesion_modo=str(d.get("sesion_modo") or e.sesion_modo),
            poteto_digest=poteto or e.poteto_digest,
            intento=d["id"],
            intento_interrumpido=False,
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
        return replace(
            e,
            proc=None,
            herramienta="",
            reserva=False,
            ajustes_reservados=e.ajustes_vistos,
            **cambios,
        )
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
        return _adoptar_fases(e, d, hora, nuevo)
    if kind == "nivel_efectivo":
        nivel = str(d.get("nivel") or "")
        if nivel not in ("", "pr", "integrado"):
            raise TransicionIlegal(f"nivel efectivo desconocido: {nivel}")
        if nivel and _ORDEN_NIVEL.get(nivel, 0) >= _ORDEN_NIVEL.get(
            e.entrega.nivel, 1
        ):
            raise TransicionIlegal("el nivel efectivo no supera al solicitado")
        if e.nivel_efectivo and _ORDEN_NIVEL.get(nivel, 99) > _ORDEN_NIVEL[
            e.nivel_efectivo
        ]:
            # El nivel efectivo es un tope que solo se aprieta: una fuente que
            # no vio la limitación (p. ej. el sondeo de máquina sin gh) no
            # relaja lo que otra ya comprobó (h_6a425441). El nivel igual es
            # un sin registro, no un error.
            raise TransicionIlegal("el nivel efectivo solo se limita más; no se relaja")
        if nivel == e.nivel_efectivo:
            return e
        motivo = str(d.get("motivo") or f"nivel efectivo limitado a {nivel}")
        return replace(
            e,
            nivel_efectivo=nivel,
            nivel_efectivo_motivo=motivo,
            ultimo_hecho=motivo,
            hora=hora or e.hora,
        )
    if kind == "politica_despliegue":
        e = _fase_de_evento(e, d)
        fase = e.fase_actual
        if fase is None or fase.estado == "preparada":
            raise TransicionIlegal("no se cambia la política de una fase cerrada")
        fuente, huella = str(d.get("fuente") or ""), str(d.get("huella") or "")
        if not fuente or not huella:
            raise TransicionIlegal("política de despliegue sin fuente comprobable")
        datos = datos_despliegue(d)
        if "requiere_despliegue" not in datos:
            raise TransicionIlegal("política sin declaración de despliegue")
        correspondencia = _correspondencia_despliegue(e, d)
        return replace(
            con_fase(e, replace(fase, **datos, despliegue_fuente=fuente, despliegue_huella=huella)),
            hora=hora or e.hora,
            **correspondencia,
        )
    if kind == "producto_listo":
        e = _fase_de_evento(e, d)
        fase = e.fase_actual
        if fase is None:
            raise TransicionIlegal("no hay fase para el producto")
        version = VersionProducto.desde(
            d.get("version") or "", d.get("manifiesto"), d.get("sha_git") or ""
        )
        nueva = replace(fase, producto=version, estado="revision")
        return replace(
            con_fase(e, nueva),
            ultimo_hecho=f"producto de {fase.id} listo para revisar",
            hora=hora or e.hora,
        )
    if kind == "ci_pr":
        return _ci_pr(e, d, hora)
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
            clase, causa, condicion, bool(d.get("verificable", True)),
            str(d.get("agente") or ""),
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
        return _con_progreso(
            e,
            replace(
                con_fase(
                    e,
                    replace(
                        fase,
                        entrega_bloqueada_sha=str(d.get("sha") or ""),
                    ),
                ),
                ultimo_hecho=str(
                    d.get("causa") or "entrega bloqueada por un conflicto con la base"
                ),
                hora=hora or e.hora,
            ),
        )
    if kind == "lectura_fallida":
        # Se ve en `ver` (deepseek.error) y no toca motivo, siguiente ni causa,
        # que son lo que lee el próximo turno del agente. Una causa vacía anota
        # que la lectura volvió a salir bien.
        fase = e.fase_actual
        if fase is None or str(d.get("fase") or fase.id) != fase.id:
            return e
        return con_fase(e, replace(fase, deepseek_error=str(d.get("causa") or "")))
    if kind == "incorporado":
        # El resultado de un intento ya se incorporó. La generación identifica
        # el intento; la marca sobrevive a la limpieza de su directorio.
        try:
            generacion = int(d.get("generacion") or 0)
        except (TypeError, ValueError):
            raise TransicionIlegal(
                "la incorporación necesita una generación"
            ) from None
        if generacion < 0:
            raise TransicionIlegal("la incorporación necesita una generación")
        marcas = e.incorporado if generacion in e.incorporado else (
            tuple(sorted(e.incorporado + (generacion,)))
        )
        return replace(e, incorporado=marcas)
    if kind == "quietud_vigilada":
        # Una vuelta del supervisor observó el encargo quieto sobre el evento
        # `evento`. La cuenta vive en el registro; un evento nuevo la estrena.
        try:
            evento_id = int(d.get("evento") or 0)
            vueltas = int(d.get("vueltas") or 0)
            desde = float(d.get("desde") or 0)
            visto = float(d.get("visto") or 0)
        except (TypeError, ValueError):
            raise TransicionIlegal(
                "la quietud vigilada necesita números"
            ) from None
        return replace(e, quietud_vigilada=(evento_id, vueltas, desde, visto))
    if kind == "deepseek_lectura":
        return replace(e, lectura_deepseek=LecturaDeepSeek.de_dict(d))
    if kind == "deepseek_cerrado":
        return replace(e, deepseek_cerrado=MarcasDeepSeekCerrado.de_dict(d))
    if kind == "ci_sondeo":
        return replace(e, sondeo_ci=SondeoCI.de_dict(d))
    if kind == "fase_reabierta":
        return _fase_reabierta(e, d, hora)
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
                return replace(
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
            return replace(
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
    if kind == "nota_pr":
        texto = str(d.get("texto") or "").strip()
        if texto and texto not in e.notas_pr:
            # La nota es de la fase que la produjo; el log del encargo se
            # conserva para vista y prompts. Sin fases declaradas no se crea
            # ninguna fase nueva por escribir una nota.
            fase = e.fase_actual
            if e.fases and fase is not None and texto not in fase.notas:
                e = con_fase(e, replace(fase, notas=fase.notas + (texto,)))
            return replace(e, notas_pr=e.notas_pr + (texto,), hora=hora or e.hora)
        return e
    if kind == "hallazgo_fuera_de_alcance":
        hid = str(d.get("id") or "").strip()
        titulo = str(d.get("titulo") or "").strip()
        razon = " ".join(str(d.get("razon") or "").split())
        if hid and razon:
            actuales = tuple(x for x in e.fuera_de_alcance if x[0] != hid)
            return replace(
                e,
                fuera_de_alcance=actuales + ((hid, titulo, razon),),
                hora=hora or e.hora,
            )
        return e
    if kind == "entrega_devuelta":
        # La devolución de la entrega se lee del registro: el producto y el
        # estado no cambian, solo deja de reintentarse hasta la próxima vuelta.
        return e
    if kind == "pendiente_entrega":
        texto = str(d.get("texto") or "").strip()
        if texto and texto not in e.pendientes_entrega:
            return replace(
                e,
                pendientes_entrega=e.pendientes_entrega + (texto,),
                hora=hora or e.hora,
            )
        return e
    if kind == "instrumento_no_disponible":
        clase = str(d.get("clase") or "").strip()
        if not clase:
            raise TransicionIlegal("instrumento_no_disponible sin clase")
        limpios = tuple(i for i in e.impedimentos if i.clase != clase)
        return _con_impedimentos(
            replace(
                e,
                herramientas_no_disponibles=tuple(
                    sorted(set(e.herramientas_no_disponibles) | {clase})
                ),
                hora=hora or e.hora,
            ),
            limpios,
            "",
        )
    if kind == "cierre_forzado":
        evidencia = str(d.get("evidencia") or "").strip()
        if not evidencia:
            raise TransicionIlegal("cierre forzado sin evidencia")
        if e.estado in _CERRADOS:
            raise TransicionIlegal("el encargo ya cerró")
        e = asegurar_fase(e)
        fase = e.fase_actual
        e = replace(e, cierre_forzado_fase=fase, cierre_forzado_huella=hashlib.sha256(
            json.dumps([e.id, fase.id, hora, evidencia, e.reaperturas]).encode()
        ).hexdigest())
        if fase is not None:
            e = con_fase(
                e,
                replace(fase, estado="preparada", evidencia=evidencia),
            )
        return replace(
            e,
            estado="terminado",
            evidencia=e.evidencia + (evidencia,),
            intentos_nuevos=False,
            escrituras=False,
            ultimo_hecho=evidencia,
            siguiente="ninguna",
            motivo="",
            hora=hora or e.hora,
        )
    if kind == "bloqueo_fase":
        return _bloqueo_fase(e, d, hora)
    if kind == "arbol":
        return _arbol(e, d, hora)
    if kind == "comprobacion":
        return _comprobacion(e, d, hora, nuevo)
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
        # Aviso informativo: una pregunta no cambia el estado ni detiene el
        # encargo. Los eventos históricos con `bloquea_todo` se pliegan igual,
        # sin reabrir una espera que el motor ya no tiene.
        texto = str(d.get("texto") or "")
        return replace(
            e,
            preguntas=e.preguntas if texto in e.preguntas else e.preguntas + (texto,),
            hora=hora or e.hora,
        )
    if kind == "quietud":
        # La quietud es un dato del diagnóstico, no una espera. Se convierte en
        # un pendiente visible que el barrido entrega con lo que haya.
        causa = str(d.get("causa") or "").strip()
        if not causa:
            raise TransicionIlegal("la quietud necesita causa")
        if e.estado not in ("recibido", "ejecutando"):
            return e
        return replace(
            e,
            pendientes_entrega=(
                e.pendientes_entrega
                if causa in e.pendientes_entrega
                else e.pendientes_entrega + (causa,)
            ),
            motivo=causa,
            siguiente="entregar el PR con los pendientes abiertos",
            ultimo_hecho=causa,
            hora=hora or e.hora,
        )
    if kind == "estrategia":
        if d["estrategia"] in ("investigar", "diagnosticar_bloqueo") and (
            d.get("rol") or "responsable"
        ) == "responsable":
            e = replace(e, devuelto_con=tuple(sorted(_bloqueos_abiertos(e))))
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
                replace(fase, estado="preparada", evidencia=evidencia)
                if f.id == fase.id
                else f
                for f in e.fases
            )
        return replace(
            e,
            fases=fases,
            estado="terminado",
            cierre_forzado_fase=None,
            cierre_forzado_huella="",
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
    if kind == "herramienta":
        return _herramienta(e, d, hora)
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

    if kind in (
        "ci_producto",
        "ci_revision_rojo",
        "ci_pr_sondeo",
        "respuesta_revisor_pedida",
        "autoridad_cerrada",
        "disputa",
    ):
        # Eventos sin efecto sobre el estado: los retirados de una versión
        # anterior, la marca de que una pregunta vencida se pasó al revisor y
        # la de que un cerrado ya no tiene nada que reintentar en la autoridad.
        return replace(e, hora=hora or e.hora)

    raise TransicionIlegal(f"evento desconocido: {kind}")



def _con_hechos_derivados(e: Encargo, evento: dict) -> Encargo:
    """Actualiza lo que antes se contaba recorriendo el log en `ciclo.py`.

    El reducer es el único que ve la secuencia completa, así que las series,
    los contadores y las últimas marcas salen del pliegue y no de un segundo
    recorrido de eventos en el ciclo.
    """
    kind = evento["kind"]
    d = evento["data"]
    cambios: dict = {}
    if d.get("firma"):
        cambios["ultima_firma"] = str(d["firma"])
    if kind in ("impedimento", "impedimento_resuelto"):
        clase = str(d.get("clase") or d.get("huella") or "")
        if clase:
            actual = dict(e.apariciones)
            actual[clase] = actual.get(clase, 0) + 1
            cambios["apariciones"] = tuple(sorted(actual.items()))
    if kind == "instrumento_fallo":
        clase = str(d.get("clase") or "")
        version = str(d.get("version") or "")
        causa = str(d.get("causa") or "")
        artefacto = str(d.get("artefacto") or "")
        cambios["fallos_instrumento"] = e.fallos_instrumento + (
            (version, clase, causa, artefacto),
        )
        if clase:
            arts = dict(e.artefactos_instrumento)
            arts[clase] = artefacto
            cambios["artefactos_instrumento"] = tuple(sorted(arts.items()))
        if str(d.get("fuente") or "") == "barrido":
            cambios["fallos_barrido"] = e.fallos_barrido + 1
    if kind == "proveedor_fallo":
        agente = str(d.get("agente") or "")
        if agente:
            actual = dict(e.fallos_proveedor)
            actual[agente] = actual.get(agente, 0) + 1
            cambios["fallos_proveedor"] = tuple(sorted(actual.items()))
    if kind == "proveedor_disponible":
        agente = str(d.get("agente") or "")
        if agente:
            actual = dict(e.fallos_proveedor)
            actual.pop(agente, None)
            cambios["fallos_proveedor"] = tuple(sorted(actual.items()))
    if kind == "entrega_externa":
        paso = str(d.get("paso") or "")
        fase = str(d.get("fase") or e.fase_activa)
        if paso == "deepseek_leido":
            sha = str(d.get("sha") or "")
            actual = {(f, s): n for f, s, n in e.lecturas_deepseek}
            actual[(fase, sha)] = actual.get((fase, sha), 0) + 1
            cambios["lecturas_deepseek"] = tuple(
                sorted((f, s, n) for (f, s), n in actual.items())
            )
        if paso == "pr_cuerpo":
            marca = (fase, str(d.get("sha") or ""), str(d.get("huella") or ""))
            if marca not in e.cuerpos_publicados:
                cambios["cuerpos_publicados"] = e.cuerpos_publicados + (marca,)
    if kind == "respuesta_revisor_pedida":
        ident = str(d.get("solicitud") or "")
        if ident and ident not in e.preguntas_pedidas:
            cambios["preguntas_pedidas"] = e.preguntas_pedidas + (ident,)
    if kind in ("cierre_forzado", "bloqueo_fase"):
        cambios["hora_cierre"] = str(d.get("hora") or evento.get("ts") or "")
    if kind == "comprobacion" and str(d.get("clase") or "") in (
        "cierre_comprobado",
        "confirmado",
    ):
        cambios["hora_cierre"] = str(d.get("hora") or evento.get("ts") or "")
    if kind == "revision" and str(d.get("origen") or "independiente") == "independiente":
        fase = str(d.get("fase") or e.fase_activa)
        if fase:
            actual = {f: ev for f, ev in e.evidencias_revision}
            actual[fase] = str(d.get("evidencia") or "")
            cambios["evidencias_revision"] = tuple(sorted(actual.items()))
    if kind == "producto_listo":
        cambios["internos_producto"] = e.internos_producto + (
            str(d.get("interno") or ""),
        )
    repo_clase, repo_n = e.fallos_repo
    ent_clase, ent_n = e.fallos_entrega
    if kind == "espera":
        motivo = str(d.get("motivo") or "")
        clase = _clase_de_fallo(motivo)
        if clase == repo_clase:
            repo_n += 1
        else:
            repo_clase, repo_n = clase, 1
        transitorio = _fallo_transitorio(motivo)
        if clase != ent_clase:
            # Una espera de otra clase corta la serie, aunque sea transitoria:
            # la serie de entrega se mide sobre el mismo fallo.
            ent_clase, ent_n = clase, 0 if transitorio else 1
        elif not transitorio:
            ent_n += 1
    elif kind == "procedimiento" and str(d.get("nota") or "").startswith(
        _NOTA_COPIA_SESION
    ):
        pass
    elif kind not in _ENTRE_REINTENTOS and kind not in _EVENTOS_DE_MARCADOR:
        repo_clase, repo_n = "", 0
        ent_clase, ent_n = "", 0
    if (repo_clase, repo_n) != e.fallos_repo:
        cambios["fallos_repo"] = (repo_clase, repo_n)
    if (ent_clase, ent_n) != e.fallos_entrega:
        cambios["fallos_entrega"] = (ent_clase, ent_n)
    return replace(e, **cambios) if cambios else e


def _reducir(estado: Encargo | None, evento: dict, nuevo: bool = True) -> Encargo:
    return _con_hechos_derivados(
        _reducir_evento(estado, evento, nuevo), evento
    )


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


def _herramienta(e: Encargo, d: dict, hora: str) -> Encargo:
    """La solicitud recuerda dónde dejará su resultado la herramienta que la atiende."""
    ident = str(d.get("id") or "")
    sol = next((s for s in e.solicitudes if s.id == ident), None)
    if sol is None:
        raise TransicionIlegal("la herramienta no tiene solicitud")
    carpeta = str(d.get("carpeta") or "")
    if not carpeta:
        raise TransicionIlegal("la herramienta necesita su carpeta")
    if sol.carpeta:
        return e
    return replace(
        e,
        solicitudes=tuple(
            replace(
                s,
                carpeta=carpeta,
                version=str(d.get("version") or ""),
                configuracion=str(d.get("configuracion") or ""),
            )
            if s.id == ident
            else s
            for s in e.solicitudes
        ),
        hora=hora or e.hora,
    )


def _soltar_impedimento_de_inspeccion(e: Encargo) -> Encargo:
    """Una respuesta o un ajuste del propietario es la salida del impedimento
    `inspeccion:<id>`: sin ella solo un juicio corregido lo levanta, y el
    revisor no corre mientras el impedimento siga."""
    restantes = tuple(i for i in e.impedimentos if not i.clase.startswith("inspeccion:"))
    if len(restantes) == len(e.impedimentos):
        return e
    e = _con_impedimentos(e, restantes)
    return replace(
        e,
        motivo=e.impedimento_causa,
        siguiente=(
            "esperar a que se cumpla: " + e.impedimento_condicion
            if e.impedimentos
            else "seguir el mismo encargo"
        ),
        ultimo_hecho="el propietario aportó información nueva; el revisor retoma la inspección",
    )


def _reanudar_inspeccion(e: Encargo, ident: str) -> Encargo:
    """La resolución o respuesta se consume en el flujo normal.

    Ya no hay un estado de espera humana que reabrir: el barrido vuelve a
    ofrecer el trabajo con la información nueva."""
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


def respuesta_vigente(e: Encargo, sol: Solicitud) -> bool:
    """La respuesta del propietario es información de la fase, no del intento.

    Cada vuelta del revisor abre su propio intento y `e.intento` sigue siendo el
    último, así que la solicitud de la vuelta anterior nunca coincide. El
    revisor siguiente es quien la consume, de modo que basta la fase.
    """
    return not sol.fase or sol.fase == e.fase_activa


def solicitud_ligada(e: Encargo, sol: Solicitud) -> bool:
    """La solicitud es la que la inspección pendiente de la fase espera."""
    fase = e.fase_actual
    return fase is not None and bool(fase.inspeccion_solicitud) and fase.inspeccion_solicitud == sol.id


def _respuesta(e: Encargo, d: dict, hora: str) -> Encargo:
    """Una respuesta tardía se conserva y no cambia el trabajo que ya no la espera."""
    ident = str(d.get("solicitud") or "")
    texto = str(d.get("texto") or "")
    sol = next((s for s in e.solicitudes if s.id == ident), None)
    if sol is None or not respuesta_vigente(e, sol):
        return replace(
            e,
            procedimiento=e.procedimiento
            + (f"respuesta sin pregunta vigente ({ident}): {texto}",),
            hora=hora or e.hora,
        )
    if sol.estado != "pregunta":
        # La solicitud ya tiene resultado (un error, una denegación, una
        # detención): la respuesta no lo reescribe pero tampoco se pierde.
        if texto in sol.respuestas_propietario:
            return e
        nueva = replace(sol, tardias=sol.tardias + (texto,))
    else:
        nueva = replace(sol, estado="respuesta", respuesta=texto)
    e = replace(
        e,
        solicitudes=tuple(nueva if s.id == ident else s for s in e.solicitudes),
        hora=hora or e.hora,
    )
    return _reanudar_inspeccion(
        _soltar_impedimento_de_inspeccion(_nueva_info_del_propietario(e)), ident
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


def dependencia_bloqueada(fases: tuple[Fase, ...], fase: Fase) -> str:
    """Id de la fase bloqueada que condena a `fase`, o vacío si no hay ninguna.

    La condena viaja por las dependencias declaradas, directas y transitivas.
    Una fase sin dependencias declaradas tiene una implícita: su antecesora en
    el orden del plan. Una lista vacía no acredita independencia, así que el
    orden secuencial se conserva hasta que el plan diga otra cosa.
    """
    por_id = {f.id: f for f in fases}
    orden = {f.id: i for i, f in enumerate(fases)}
    vistos: set[str] = set()
    pila = [fase.id]
    while pila:
        fid = pila.pop()
        if fid in vistos:
            continue
        vistos.add(fid)
        actual = por_id.get(fid)
        if actual is None:
            continue
        if actual.estado == "bloqueada":
            return actual.id
        deps = [dep for dep, _version in actual.requiere]
        if not deps and orden.get(fid, 0) > 0:
            deps = [fases[orden[fid] - 1].id]
        pila.extend(deps)
    return ""


def queda_trabajo(fases: tuple[Fase, ...]) -> bool:
    """Queda fase alcanzable: que no esté preparada ni bloqueada ni condenada
    por una bloqueada. Si no queda, el encargo termina como entrega parcial."""
    return any(
        f.estado not in ("preparada", "bloqueada")
        and not dependencia_bloqueada(fases, f)
        for f in fases
    )


def _fase_disponible(fases: tuple[Fase, ...]) -> Fase | None:
    """Primera fase ejecutable: no preparada ni bloqueada, con sus dependencias
    preparadas y sin una bloqueada en su cadena."""
    preparadas = {f.id for f in fases if f.estado == "preparada"}
    for fase in fases:
        if fase.estado in ("preparada", "bloqueada"):
            continue
        if not all(dep in preparadas for dep, _version in fase.requiere):
            continue
        if dependencia_bloqueada(fases, fase):
            continue
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


def _correspondencia_despliegue(e: Encargo, d: dict) -> dict:
    if "correspondencia" not in d:
        return {}
    crudo = d["correspondencia"]
    if not isinstance(crudo, dict):
        raise TransicionIlegal("correspondencia de despliegue debe ser un objeto")
    archivo, fases = crudo.get("archivo"), crudo.get("fases")
    if not isinstance(archivo, str) or not archivo.strip():
        raise TransicionIlegal("correspondencia de despliegue sin archivo")
    if not isinstance(fases, (list, tuple)) or not fases:
        raise TransicionIlegal("correspondencia de despliegue sin fases")
    if any(
        not isinstance(par, (list, tuple))
        or len(par) != 2
        or any(not isinstance(valor, str) or not valor.strip() for valor in par)
        for par in fases
    ):
        raise TransicionIlegal("correspondencia requiere pares de ids de fase y bloque")
    mapa = tuple((fase, bloque) for fase, bloque in fases)
    ids_fase = {fase for fase, _ in mapa}
    ids_bloque = {bloque for _, bloque in mapa}
    if len(ids_fase) != len(mapa) or ids_fase != {fase.id for fase in e.fases}:
        raise TransicionIlegal("correspondencia debe incluir cada fase exactamente una vez")
    if len(ids_bloque) != len(mapa):
        raise TransicionIlegal("correspondencia repite un bloque")
    if (e.despliegue_mapa or e.despliegue_archivo) and (
        mapa != e.despliegue_mapa or archivo != e.despliegue_archivo
    ):
        raise TransicionIlegal("no se reemplaza la correspondencia de despliegue adoptada")
    return {"despliegue_mapa": mapa, "despliegue_archivo": archivo}


def datos_despliegue(item: dict) -> dict:
    """Normaliza sólo lo declarado; omitir metadata nunca borra una política."""
    salida = {}
    if "requiere_despliegue" in item:
        requiere = item["requiere_despliegue"]
        if requiere is not None and type(requiere) is not bool:
            raise TransicionIlegal("requiere_despliegue debe ser booleano o null")
        salida["requiere_despliegue"] = requiere
    if "lane" in item:
        lane = item["lane"]
        if lane not in ("fast", "gate", "release"):
            raise TransicionIlegal("lane debe ser fast, gate o release")
        requiere = lane == "release"
        if salida.get("requiere_despliegue", requiere) is not requiere:
            raise TransicionIlegal("lane contradice requiere_despliegue")
        salida["requiere_despliegue"] = requiere
    if "despliegue" in item:
        receta = item["despliegue"]
        if not isinstance(receta, dict):
            raise TransicionIlegal("despliegue debe contener desplegar, checklist y rollback")
        for nombre in ("desplegar", "checklist", "rollback"):
            valor = receta.get(nombre, "")
            if not isinstance(valor, str):
                raise TransicionIlegal(f"despliegue.{nombre} debe ser una ruta")
            salida[nombre] = valor.strip()
        if any(salida[n] for n in ("desplegar", "checklist", "rollback")):
            if salida.get("requiere_despliegue") is False:
                raise TransicionIlegal("una fase sin despliegue no puede declarar receta")
            salida["requiere_despliegue"] = True
    return salida


_ACENTOS = str.maketrans("áéíóúüÁÉÍÓÚÜ", "aeiouuAEIOUU")
# Enumeración explícita de los ids de las fases dentro de un ajuste del
# director: «exactamente N con ids B, C, D». El ancla de conteo y la lista se
# capturan en la MISMA coincidencia, así que el ancla solo puede certificar
# la lista que sigue: ninguna otra frase del segmento la hereda. La frase es
# obligatoria y estructurada para que la prosa corriente no enumere por
# accidente; los separadores admiten coma y una conjunción y/e/o solo como
# cierre de la serie («…, H e I»). Es el único contraste mecánico entre la
# propuesta y los ajustes: una lista estructurada, nunca prosa suelta. El
# resto de un ajuste (una fase «ya integrada», un «no la repitas») no muta el
# estado de las fases: la prosa es ambigua y el orden seguro es el secuencial
# con sus compuertas normales.
_ENUMERACION_IDS = re.compile(
    r"\bexactamente\s+(\d+)\s+(?:fases\s+)?con\s+ids\s*:?\s*"
    r"([A-Za-z][\w-]*(?:\s*,\s*[A-Za-z][\w-]*)+(?:\s+[yeo]\s+[A-Za-z][\w-]*)?)"
)


def _normaliza(texto: str) -> str:
    return " ".join(str(texto or "").translate(_ACENTOS).lower().split())


def _fase_corresponde(fid: str, token: str) -> bool:
    """El id de la fase responde al token del ajuste: igualdad o su primer
    segmento («b-receta» responde a «B»)."""
    t = token.strip().lower()
    if not t:
        return False
    f = fid.strip().lower()
    return f == t or re.split(r"[-_]", f)[0] == t


def _tokens_con_forma_de_id(tokens: list[str]) -> bool:
    """Forma inequívoca de id para todos los tokens de una enumeración: una
    letra suelta («b»..«i», como en el ajuste real 12421) o un id compuesto
    con guion o guion bajo («b-receta», como los ids del evento 12521). Los
    adjetivos de una frase corriente («cortos», «legibles», «en») no la
    tienen: sin esta forma, la coincidencia es prosa, no enumeración."""
    return all(len(t) == 1 or "-" in t or "_" in t for t in tokens)


def _enumeracion_del_director(e: Encargo) -> tuple[str, list[str]] | None:
    """El último ajuste del director que enumera los ids de las fases con el
    ancla «exactamente N … con ids», con el ancla y la lista en la misma
    coincidencia y N igual al número de tokens.

    El ancla es la única forma que ata y no puede certificar otra lista: la
    prosa no lleva por accidente un compromiso numérico seguido de una lista
    con exactamente ese largo, y ninguna otra frase del segmento hereda el
    ancla. La enumeración fija la estructura del plan: una propuesta que
    declare fases nuevas fuera de ella no representa los ajustes vigentes y
    se rechaza para que el implementador corrija `.interno/fases.json`,
    aunque ninguna de sus fases responda a la enumeración (una propuesta
    totalmente fuera tampoco la representa).
    """
    for ajuste in reversed(e.ajustes):
        for segmento in _normaliza(ajuste).split(". "):
            if "fase" not in segmento:
                continue
            m = _ENUMERACION_IDS.search(segmento)
            if m:
                tokens = [
                    t.strip()
                    for t in re.split(
                        r"\s*,\s*|\s+[yeo]\s+", m.group(2)
                    )
                    if t.strip()
                ]
                if (
                    len(tokens) == int(m.group(1))
                    and len(tokens) >= 2
                    and _tokens_con_forma_de_id(tokens)
                ):
                    return ajuste, tokens
    return None


def _adoptar_fases(e: Encargo, d: dict, hora: str, nuevo: bool = False) -> Encargo:
    """La propuesta de fases se valida aquí y no borra trabajo ya obtenido.

    El formato lo escribe el implementador, nunca el usuario del encargo. Las
    reglas que juzgan la propuesta (formato, no reescribir, enumeración del
    director, condena) solo corren al escribir el evento. Un evento guardado ya
    fue aceptado con las reglas de su día; al plegar se aplica tal como entró,
    aunque una regla posterior lo habría rechazado. Las reglas que sostienen el
    grafo (dependencia inexistente, ciclo) siguen corriendo: una historia que
    las incumple es un registro incoherente, no una propuesta vieja.
    """
    crudo = d.get("fases")
    if isinstance(crudo, dict):
        crudo = crudo.get("fases")
    if not isinstance(crudo, (list, tuple)) or not crudo:
        if nuevo:
            raise TransicionIlegal("la propuesta de fases está vacía")
        return e
    existentes = {f.id: f for f in e.fases}
    nuevas: list[Fase] = []
    nuevos_ids: set[str] = set()
    vistos: set[str] = set()
    for item in crudo:
        if not isinstance(item, dict):
            if nuevo:
                raise TransicionIlegal("cada fase debe ser un objeto")
            continue
        fid = str(item.get("id") or "").strip()
        if not fid or not all(c.isalnum() or c in "-_" for c in fid):
            if nuevo:
                raise TransicionIlegal(f"id de fase inválido: {fid!r}")
            continue
        if fid in vistos:
            if nuevo:
                raise TransicionIlegal(f"fase repetida: {fid}")
            continue
        vistos.add(fid)
        resultado = str(item.get("resultado") or "").strip()
        requiere = _requiere_de(item)
        despliegue = datos_despliegue(item)
        previa = existentes.get(fid)
        if previa is None:
            if not resultado:
                if nuevo:
                    raise TransicionIlegal(f"la fase {fid} no declara resultado")
                continue
            nuevos_ids.add(fid)
            nuevas.append(
                Fase(
                    id=fid,
                    resultado=resultado,
                    estado="pendiente",
                    requiere=requiere,
                    **despliegue,
                )
            )
            continue
        if (
            nuevo
            and previa.estado != "pendiente"
            and resultado
            and resultado != previa.resultado
        ):
            raise TransicionIlegal(f"no se reescribe el resultado de la fase {fid}")
        if (nuevo and previa.estado != "pendiente") or previa.despliegue_fuente:
            if any(getattr(previa, k) != v for k, v in despliegue.items()):
                if nuevo:
                    raise TransicionIlegal(
                        f"no se reescribe el despliegue de la fase {fid} ya trabajada"
                    )
        if previa.estado != "pendiente":
            fusionada = _fusionar_requiere(previa.requiere, requiere)
            if nuevo and fusionada != previa.requiere:
                raise TransicionIlegal(
                    f"no se reescriben las dependencias de la fase {fid} ya trabajada"
                )
            requiere_final = fusionada if not nuevo else previa.requiere
        else:
            requiere_final = _fusionar_requiere(previa.requiere, requiere)
        nuevas.append(
            replace(
                previa,
                resultado=resultado or previa.resultado,
                requiere=requiere_final,
                **despliegue,
            )
        )
    for fid in existentes:
        if fid not in vistos and nuevo:
            raise TransicionIlegal(f"la propuesta omite la fase existente {fid}")
    enumeracion = _enumeracion_del_director(e)
    if nuevo and enumeracion is not None:
        ajuste_enum, tokens = enumeracion
        fuera = sorted(
            fid for fid in nuevos_ids
            if not any(_fase_corresponde(fid, t) for t in tokens)
        )
        if fuera:
            raise TransicionIlegal(
                "el ajuste del director enumera las fases ("
                + ", ".join(tokens)
                + "); la propuesta declara fases fuera de esa enumeración: "
                + ", ".join(fuera)
            )
    if not nuevas:
        return e
    por_id = {f.id: f for f in nuevas}
    for fase in nuevas:
        for dep, _version in fase.requiere:
            if dep not in por_id:
                raise TransicionIlegal(f"la fase {fase.id} requiere una fase inexistente: {dep}")
            if dep == fase.id:
                raise TransicionIlegal(f"la fase {fase.id} se requiere a sí misma")
    _sin_ciclo(tuple(nuevas))
    # El orden secuencial implícito vive en el orden de la lista: una
    # repropuesta puede deswirear la condena de una fase sin tocar su
    # `requiere`, reordenando o insertando fases. Se compara la condena de
    # todas las fases ejecutables entre el estado viejo y la propuesta
    # (con su orden y sus dependencias); perderla por propuesta no está
    # acreditado por nadie: el orden lo reconcilia el director. Apretarla o
    # conservarla sí se permite. Solo al escribir: una historia ya aceptada se
    # aplica tal como entró.
    propuestas = tuple(nuevas)
    for f in propuestas:
        if not nuevo or f.estado in ("preparada", "bloqueada"):
            continue
        condenada_vieja = next(
            (
                dependencia_bloqueada(e.fases, vieja)
                for vieja in e.fases
                if vieja.id == f.id
            ),
            "",
        )
        if condenada_vieja and not dependencia_bloqueada(propuestas, f):
            raise TransicionIlegal(
                f"la propuesta deja a la fase {f.id} de estar condenada por la "
                f"fase bloqueada {condenada_vieja} (reorden, inserción o "
                "dependencias nuevas); el orden lo reconcilia el director"
            )
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
    resultado = replace(
        e,
        fases=tuple(nuevas),
        fase_activa=activa,
        hora=hora or e.hora,
        ultimo_hecho=f"fases organizadas: {len(nuevas)}",
    )
    if activa != e.fase_activa:
        resultado = sesiones_de_fase_nueva(resultado)
    return resultado


def defecto_al_adoptar_fases(e: Encargo, fases) -> str:
    """El defecto que impediría adoptar la propuesta sobre el estado vigente,
    sin adoptarla. Es la misma validación que aplica el evento `fases`: que el
    archivo tenga forma de propuesta no basta si deja un ciclo o reescribe una
    fase ya trabajada."""
    try:
        _adoptar_fases(e, {"fases": list(fases)}, e.hora, nuevo=True)
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


def _fuera_de_alcance_por_restricciones(e: Encargo) -> Encargo:
    """Un bloqueante que una restricción expresa deja fuera no sostiene el encargo.

    No es un reinicio de campos: aplica la restricción al hallazgo guardado y
    deja el hecho en `fuera_de_alcance`, con su razón, para el PR. El filtro de
    política no vive aquí: se decide al anotar la lectura de DeepSeek
    (`ciclo._anotar_lectura_deepseek`), donde queda el evento que lo sostiene.
    """
    fase = e.fase_actual
    if fase is None:
        return e
    from .comprobacion import hallazgo_fuera_de_alcance_por_restriccion

    fuera = list(e.fuera_de_alcance)
    hallazgos = []
    for h in fase.hallazgos:
        if (
            h.origen == "deepseek"
            and h.gravedad == "bloqueante"
            and h.estado == "abierto"
        ):
            razon = hallazgo_fuera_de_alcance_por_restriccion(
                e.restricciones,
                h,
                permisos=e.permisos,
                prohibiciones=e.prohibiciones,
            )
            if razon:
                h = replace(
                    h,
                    gravedad="observacion",
                    estado="resuelto",
                    cierre_evidencia=razon,
                )
                fuera = [x for x in fuera if x[0] != h.id]
                fuera.append((h.id, h.titulo, razon))
        hallazgos.append(h)
    if tuple(hallazgos) == fase.hallazgos:
        return e
    return replace(
        con_fase(e, replace(fase, hallazgos=tuple(hallazgos))),
        fuera_de_alcance=tuple(fuera),
    )


def _orden(e: Encargo, d: dict, hora: str) -> Encargo:
    accion = d["accion"]
    if accion == "reabrir":
        fase = e.cierre_forzado_fase
        if e.estado != "terminado" or fase is None:
            raise TransicionIlegal("reabrir requiere un cierre forzado vigente")
        if d.get("fase") != fase.id or d.get("cierre") != e.cierre_forzado_huella:
            raise TransicionIlegal("la orden no corresponde al cierre forzado vigente")
        return replace(
            sesiones_de_fase_nueva(con_fase(e, fase)),
            estado="detenido", fase_activa=fase.id,
            cierre_forzado_fase=None, cierre_forzado_huella="",
            reaperturas=e.reaperturas + 1, generacion=e.generacion + 1,
            intento="", intento_interrumpido=True,
            proc=None, reserva=False, candidato="", conductor="", host="",
            herramienta="", sesion="", rol="responsable", estrategia="investigar",
            escrituras=False, intentos_nuevos=False, causa="", motivo="", reanuda_en="",
            ultimo_hecho=f"cierre forzado reabierto en la fase {fase.id}; sigue detenido",
            siguiente="detenido hasta continuar", hora=hora or e.hora,
        )
    if accion == "parar":
        if e.estado in ("cancelado", "terminado"):
            raise TransicionIlegal(f"no se para un encargo {e.estado}")
        return replace(
            e,
            estado="deteniendo",
            intento_interrumpido=e.intento_interrumpido or e.proc is not None or e.reserva,
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
        rearmar = (
            e.estado == "recibido"
            and e.proc is None
            and not e.reserva
            and e.intentos_nuevos
        )
        if e.estado != "detenido" and not rearmar:
            raise TransicionIlegal(
                "continuar espera el estado detenido o una pausa sin proceso"
            )
        # Continuar rearma el trabajo y quita lo que impedía lanzarlo, pero no
        # reinicia la escalera ni la cuenta del freno: esos hechos se derivan
        # del registro y solo cambian con información nueva. La restricción
        # expresa sí se aplica: un bloqueante que deja fuera no sostiene el
        # encargo.
        e = _retomar(_fuera_de_alcance_por_restricciones(e), hora)
        if e.no_concluyentes < TOPE_NO_CONCLUYENTES:
            e = replace(e, pendientes_entrega=())
        return replace(
            e,
            proc=None, herramienta="", conductor="", intento="",
            motivo="", causa="", reanuda_en="",
        )
    raise TransicionIlegal(f"orden desconocida: {accion}")


def _retomar(e: Encargo, hora: str) -> Encargo:
    """Vuelve a abrir el trabajo sin esperar a una persona.

    La inspección pendiente no lo deja en un estado mudo: el barrido la atiende
    con una capacidad concedida, la devuelve al revisor o la entrega como
    pendiente visible. Continuar no es información nueva: con el freno del
    revisor en el tope no se relanza la revisión, se entrega con el pendiente."""
    resultado = _retomar_trabajo(e, hora)
    if (
        e.no_concluyentes >= TOPE_NO_CONCLUYENTES
        and not resultado.pendientes_entrega
    ):
        motivo = (
            "El revisor no concluyó tras el tope de vueltas seguidas y se "
            "continuó sin un ajuste ni una respuesta nuevos: se entrega con "
            "los pendientes abiertos; un ajuste nuevo reabre la revisión."
        )
        resultado = replace(
            resultado,
            pendientes_entrega=resultado.pendientes_entrega + (motivo,),
            motivo=motivo,
            siguiente="entregar con los pendientes abiertos",
            ultimo_hecho=motivo,
        )
    return resultado


def _retomar_trabajo(e: Encargo, hora: str) -> Encargo:
    """Vuelve a abrir el trabajo. No repara nada: las causas pendientes siguen
    vigentes, cada una con su propia comprobación. No toca el proceso: quien
    retoma desde `detenido` ya sabe que no hay ninguno."""
    disponible = _fase_disponible(e.fases)
    if disponible is not None:
        rol, estrategia = _rol_de_continuacion(e, disponible)
        resultado = replace(
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
        )
        if disponible.id != e.fase_activa:
            return sesiones_de_fase_nueva(resultado)
        return resultado
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
    )


def _bloqueo_fase(e: Encargo, d: dict, hora: str) -> Encargo:
    """El pendiente de una fase la bloquea sin terminar el encargo completo.

    La fase queda `bloqueada` con causa y evidencia; sus pendientes se guardan
    en la fase, no en el encargo, para que el PR de otra fase no los copie. La
    identidad del cierre vigente queda fijada como en el cierre forzado, así
    que `reabrir` sirve igual para los cierres por fase. Si otra fase tiene
    dependencias válidas, el encargo sigue con ella; si no queda ninguna
    alcanzable, el encargo termina como entrega parcial.
    """
    evidencia = str(d.get("evidencia") or "").strip()
    if not evidencia:
        raise TransicionIlegal("bloqueo de fase sin evidencia")
    if e.estado in _CERRADOS:
        raise TransicionIlegal("el encargo ya cerró")
    e = asegurar_fase(e)
    fase = e.fase_actual
    if fase is None:
        raise TransicionIlegal("no hay fase que bloquear")
    if fase.estado == "bloqueada":
        raise TransicionIlegal(f"la fase {fase.id} ya está bloqueada")
    causa = " ".join(str(d.get("causa") or evidencia).split())
    e = replace(
        e,
        cierre_forzado_fase=fase,
        cierre_forzado_huella=hashlib.sha256(
            json.dumps([e.id, fase.id, hora, evidencia, e.reaperturas]).encode()
        ).hexdigest(),
    )
    e = con_fase(
        e,
        replace(
            fase,
            estado="bloqueada",
            bloqueo_causa=causa,
            evidencia=evidencia,
            pendientes=tuple(e.pendientes_entrega),
        ),
    )
    e = replace(
        e,
        pendientes_entrega=(),
        ultimo_hecho=evidencia,
        hora=hora or e.hora,
    )
    disponible = _fase_disponible(e.fases)
    if disponible is not None:
        resultado = replace(
            e,
            estado="recibido",
            fase_activa=disponible.id,
            rol="responsable",
            estrategia="investigar",
            motivo="",
            siguiente=f"trabajar la fase {disponible.id}",
            intentos_nuevos=True,
            escrituras=e.autoridad,
            devuelto_con=(),
        )
        return (
            sesiones_de_fase_nueva(resultado)
            if disponible.id != fase.id
            else resultado
        )
    if queda_trabajo(e.fases):
        return replace(
            e,
            estado="recibido",
            rol="",
            siguiente="esperar a que una dependencia se prepare",
        )
    return replace(
        e,
        estado="terminado",
        evidencia=e.evidencia + (evidencia,),
        intentos_nuevos=False,
        escrituras=False,
        motivo="",
        siguiente="ninguna",
    )


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


def _ci_pr(e: Encargo, d: dict, hora: str) -> Encargo:
    """El CI del PR real, leído una vez cuando termina.

    Un rojo de una prueba del producto reabre la fase para corregir en la misma
    rama y el mismo PR. Un rojo de infraestructura solo queda anotado: el
    encargo no vuelve al responsable. El estado «reintentando» marca el único
    reintento de un rojo de tubería, antes de leer el resultado de nuevo; el
    estado «sin_resultado» cierra el sondeo cuando el CI no terminó a tiempo.
    """
    e = _fase_de_evento(e, d)
    fase = e.fase_actual
    if fase is None:
        raise TransicionIlegal("no hay fase para el CI del PR")
    sha = str(d.get("sha") or "")
    estado = str(d.get("estado") or "")
    if estado not in ("verde", "rojo", "error", "reintentando", "sin_resultado"):
        raise TransicionIlegal("estado de CI del PR desconocido")
    causa = str(d.get("causa") or "")
    fallas = tuple(str(x) for x in (d.get("fallas") or ()) if str(x))
    nueva = replace(
        fase,
        ci_pr_sha=sha,
        ci_pr_estado=estado,
        ci_pr_fallas=fallas,
        ci_pr_causa=causa,
        ci_pr_clase=str(d.get("clase") or ""),
        ci_pr_reintento=str(d.get("reintento") or ""),
        ci_pr_aviso=str(d.get("aviso") or ""),
    )
    e = con_fase(e, nueva)
    if not d.get("reabrir"):
        return replace(e, hora=hora or e.hora)
    if e.estado != "terminado" or e.nivel_alcanzado in ("integrado", "desplegado"):
        # Un encargo ya integrado o desplegado no vuelve a pr_abierto: el CI
        # del PR solo deja su aviso. Reabrir ahí rebajaría el nivel alcanzado.
        return replace(e, hora=hora or e.hora)
    return _reabrir_en_el_pr(
        e,
        nueva,
        causa or "el CI del PR falló en una prueba del producto",
        "corregir el CI rojo en la misma rama y el mismo PR",
        hora,
    )


def _reabrir_en_el_pr(
    e: Encargo, fase: Fase, causa: str, siguiente: str, hora: str
) -> Encargo:
    """La fase vuelve al responsable en su misma rama y su mismo PR."""
    reabierta = replace(fase, estado="entrega", ultimo_paso_entrega="pr_abierto")
    return sesiones_de_fase_nueva(
        replace(
            con_fase(e, reabierta),
            estado="recibido",
            rol="responsable",
            estrategia="investigar",
            motivo=causa,
            siguiente=siguiente,
            intentos_nuevos=True,
            escrituras=e.autoridad,
            ultimo_hecho=causa,
            devuelto_con=(),
            hora=hora or e.hora,
        )
    )


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
        lector_version=int(d.get("lector_version") or 0),
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
    nuevo = _con_progreso(
        e,
        replace(con_fase(e, nueva), **cambios),
        concluyente=veredicto == "aprobado",
    )
    return _contar_no_concluyentes(nuevo, d, origen, veredicto)


def _sin_repeticiones(e: Encargo) -> Encargo:
    """Lo que el propietario aporta empieza otra cuenta de revisiones repetidas."""
    return replace(e, no_concluyentes=0, no_concluyente_ref=())


def _nueva_info_del_propietario(e: Encargo) -> Encargo:
    """Cierra la cuenta del freno y los pendientes de un relevo anterior.

    La escalera no se reinicia a mano: la clave del rol incluye los ajustes y
    las respuestas, así que el dato nuevo la estrena por sí solo.
    """
    return replace(_sin_repeticiones(e), pendientes_entrega=())


def _texto_normalizado(valor: str) -> str:
    import re
    import unicodedata

    sin_tildes = "".join(
        c
        for c in unicodedata.normalize("NFKD", str(valor or ""))
        if not unicodedata.combining(c)
    )
    return " ".join(re.split(r"[^a-z0-9]+", sin_tildes.lower())).strip()


def _coincide_hallazgo(valor: str, h: Hallazgo) -> bool:
    """Un id, un ancla o un título que el propietario nombró para cerrar.

    La comparación es por el nombre entero, no por sus palabras: cerrar por
    un token común cerraría hallazgos ajenos. Se acepta el título completo, el
    título seguido de la explicación del ajuste y su primera palabra (``F11``
    para ``F11 fuga en el log``)."""
    buscado = _texto_normalizado(valor)
    if not buscado:
        return False
    campos = (
        _texto_normalizado(h.id),
        _texto_normalizado(h.ancla),
        _texto_normalizado(h.titulo),
    )
    for campo in campos:
        if not campo:
            continue
        if buscado == campo or buscado.startswith(campo + " "):
            return True
    titulo = campos[2]
    primera = titulo.split(" ", 1)[0] if titulo else ""
    return bool(primera) and len(primera) >= 3 and buscado == primera


def _separar_nombres(texto: str) -> tuple[str, ...]:
    """Los nombres que el propietario encadena con comas, `y` o `e`."""
    import re

    return tuple(
        parte.strip()
        for parte in re.split(r"\s*(?:,|;|\by\b|\be\b|\band\b)\s*", str(texto or ""))
        if parte.strip()
    )


def _resolver_cierres(valores, abiertos) -> tuple[str, ...]:
    """Resuelve nombres a hallazgos abiertos; una coincidencia ambigua no cierra."""
    ids = []
    for valor in valores or ():
        texto = str(valor or "").strip()
        if not texto:
            continue
        nombres = (texto,)
        if not any(_coincide_hallazgo(texto, h) for h in abiertos):
            nombres = _separar_nombres(texto)
        for nombre in nombres:
            encontrados = [h.id for h in abiertos if _coincide_hallazgo(nombre, h)]
            if len(encontrados) == 1 and encontrados[0] not in ids:
                ids.append(encontrados[0])
    return tuple(ids)


def _ids_cerrados_por_texto(texto: str, abiertos) -> tuple[str, ...]:
    """Hallazgos que el propietario declara cerrados en el texto del ajuste.

    Solo se leen detrás de `cierra`/`cierro` para no cerrar por una mención
    casual. La vía explícita es `--cierra`, que acepta id, ancla o título."""
    import re

    m = re.search(r"(?i)\bcierr[ao]s?\b\s*:?\s*([^\n]+)", str(texto or ""))
    if not m:
        return ()
    previo = str(texto or "")[: m.start()].strip().lower().split()
    if previo and previo[-1] in ("no", "sin", "nunca"):
        return ()
    return _resolver_cierres([m.group(1)], abiertos)


def _cerrar_hallazgos(
    e: Encargo, ids, evidencia: str
) -> tuple[Encargo, tuple[str, ...]]:
    fase = e.fase_actual
    if fase is None:
        return e, ()
    abiertos = {h.id for h in fase.hallazgos if h.estado == "abierto"}
    pedidos = tuple(x for x in dict.fromkeys(ids) if x in abiertos)
    if not pedidos:
        return e, ()
    marca = str(evidencia or "").strip() or "cerrado por el propietario"
    hallazgos = tuple(
        replace(h, estado="resuelto", cierre_evidencia=marca)
        if h.id in pedidos
        else h
        for h in fase.hallazgos
    )
    nuevo = _con_progreso(e, con_fase(e, replace(fase, hallazgos=hallazgos)))
    return nuevo, pedidos


def _pedido_de_revision(fase_id: str, d: dict) -> tuple[str, ...]:
    pedido = d.get("pedido") or {}
    recurso = str(pedido.get("recurso") or "")
    operacion = str(pedido.get("operacion") or "")
    objetivo = json.dumps(
        sorted([str(k), str(v)] for k, v in (pedido.get("parametros") or ())),
        ensure_ascii=False,
    )
    return (
        fase_id,
        str(d.get("version") or ""),
        " ".join(str(d.get("inspeccion_faltante") or "").lower().split()),
        f"{operacion}@{recurso}" if recurso else "",
        objetivo if recurso else "",
        str(pedido.get("huella") or ""),
    )


def _contar_no_concluyentes(e: Encargo, d: dict, origen: str, veredicto: str) -> Encargo:
    """N revisiones no_concluyente seguidas del revisor independiente en la
    misma fase son vueltas sin revisión: la N-ésima releva el rol y, si el
    relevo tampoco concluye, deja el encargo entregando con la nota. La cuenta
    no se reinicia con otra versión ni con otro pedido del revisor: la
    reinicia un ajuste o una respuesta del propietario (`_sin_repeticiones`) o
    un veredicto distinto. La cuenta es propia (`no_concluyentes`): no pasa por
    `_sin_avance`, que es la escalera de diagnóstico de los bloqueos."""
    if origen != "independiente":
        return e
    if veredicto != "no_concluyente":
        return _sin_repeticiones(e)
    faltante = str(d.get("inspeccion_faltante") or "").strip()
    ref = _pedido_de_revision(e.fase_activa, d)
    repite = bool(e.no_concluyente_ref) and e.no_concluyente_ref[0] == ref[0]
    seguidas = e.no_concluyentes + 1 if repite else 1
    e = replace(e, no_concluyentes=seguidas, no_concluyente_ref=ref)
    if seguidas < TOPE_NO_CONCLUYENTES:
        return e
    fase = e.fase_actual
    version = ref[1][:12]
    causa = str(d.get("evidencia") or "").strip() or "sin evidencia nueva"
    pedido = (
        f"pidiendo lo mismo: «{faltante}»"
        if faltante
        else "sin evidencia que resuelva la duda"
    )
    motivo = (
        f"El revisor no concluyó {seguidas} veces seguidas sobre la versión {version} "
        f"{pedido}. Última causa: {causa[:400]}."
    )
    if fase is not None:
        e = con_fase(e, replace(fase, inspeccion_pendiente=""))
    if escalon_gastado(e, "revisor", "relevo") or escalon_gastado(
        e, "revisor", "pendientes"
    ):
        # El revisor de relevo tampoco concluyó: se entrega con la nota.
        return replace(
            con_escalon(e, "revisor", clave_escalera(e, "revisor"), "pendientes"),
            estado=(
                "recibido"
                if e.estado in ("recibido", "ejecutando")
                else e.estado
            ),
            pendientes_entrega=e.pendientes_entrega + (motivo,),
            motivo=motivo,
            siguiente="entregar el PR con la revisión sin veredicto",
            ultimo_hecho=motivo,
        )
    return replace(
        con_escalon(e, "revisor", clave_escalera(e, "revisor"), "relevo"),
        estado=(
            "recibido"
            if e.estado in ("recibido", "ejecutando")
            else e.estado
        ),
        motivo=motivo,
        siguiente="seguir con otro revisor autorizado sobre la misma versión",
        ultimo_hecho=motivo,
    )


def _entrega_externa(e: Encargo, d: dict, hora: str) -> Encargo:
    e = _fase_de_evento(e, d)
    fase = e.fase_actual
    if fase is None:
        raise TransicionIlegal("no hay fase para la entrega")
    paso = str(d.get("paso") or "")
    sha = str(d.get("sha") or "")
    producto = fase.producto
    if paso == "commit" and producto is not None and sha:
        producto = replace(producto, sha_git=sha)
    nueva = replace(
        fase,
        ultimo_paso_entrega=(
            paso if paso in _PASOS_QUE_DECIDEN_NIVEL else fase.ultimo_paso_entrega
        ),
        pr_numero=int(d.get("numero") or fase.pr_numero or 0),
        pr_sha=sha
        if paso in ("commit", "push", "pr_abierto", "pr_listo", "pr_con_pendientes")
        and sha
        else fase.pr_sha,
        deepseek_sha=(
            sha
            if paso in ("deepseek_leido", "deepseek_sin_revision") and sha
            else fase.deepseek_sha
        ),
        deepseek_huella=(
            str(d.get("huella") or "")
            if paso == "deepseek_leido"
            else (
                HUELLA_DEEPSEEK_SIN_REVISION
                if paso == "deepseek_sin_revision"
                else fase.deepseek_huella
            )
        ),
        integrado_sha=(
            str(d.get("recibo") or sha)
            if paso == "integrado" and (d.get("recibo") or sha)
            else fase.integrado_sha
        ),
        desplegado_recibo=(
            str(d.get("recibo") or "") if paso == "desplegado" else fase.desplegado_recibo
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
    resultado = replace(
        con_fase(e, nueva),
        ultimo_hecho=f"entrega externa: {paso}",
        hora=hora or e.hora,
    )
    return resultado


def _fase_reabierta(e: Encargo, d: dict, hora: str) -> Encargo:
    """DeepSeek dejó un High o un Medium abierto en el PR de una fase ya
    preparada, cuando el encargo iba a terminar. La fase vuelve al responsable
    en su misma rama y su mismo PR; las demás conservan lo que tienen."""
    pedida = str(d.get("fase") or "")
    fase = next((f for f in e.fases if f.id == pedida), None)
    if fase is None or fase.estado != "preparada":
        raise TransicionIlegal("solo se reabre una fase preparada")
    if e.estado in ("terminado", "cancelado", "deteniendo", "detenido"):
        raise TransicionIlegal("el encargo ya no admite reabrir una fase")
    causa = str(d.get("causa") or "DeepSeek dejó un hallazgo abierto")
    return sesiones_de_fase_nueva(
        replace(
            con_fase(e, replace(fase, estado="entrega", ultimo_paso_entrega="pr_abierto")),
            fase_activa=fase.id,
            estado="recibido",
            rol="responsable",
            estrategia="investigar",
            motivo=causa,
            siguiente=f"corregir la fase {fase.id}",
            intentos_nuevos=True,
            escrituras=e.autoridad,
            ultimo_hecho=causa,
            devuelto_con=(),
            hora=hora or e.hora,
        )
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


def _comprobacion(e: Encargo, d: dict, hora: str, nuevo: bool = False) -> Encargo:
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
        preparada = replace(fase, estado="preparada", evidencia=evidencia)
        firma = fase.producto.firma if fase.producto else ""
        fases = _preparar_con_entrada(e.fases, preparada, firma)
        disponible = _fase_disponible(fases)
        siguiente = disponible.id if disponible is not None else ""
        pendientes = [f for f in fases if f.estado != "preparada"]
        if e.estado in ("cancelado", "deteniendo", "detenido"):
            activa = siguiente or (pendientes[0].id if pendientes else fase.id)
            resultado = replace(
                e,
                fases=fases,
                fase_activa=activa,
                evidencia=evidencias,
                hora=hora or e.hora,
                ultimo_hecho=evidencia,
            )
            return (
                sesiones_de_fase_nueva(resultado)
                if activa != fase.id
                else resultado
            )
        if disponible is not None:
            return sesiones_de_fase_nueva(
                replace(
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
                    devuelto_con=(),
                    hora=hora or e.hora,
                )
            )
        if pendientes and queda_trabajo(fases):
            resultado = replace(
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
                devuelto_con=(),
                hora=hora or e.hora,
            )
            return (
                sesiones_de_fase_nueva(resultado)
                if pendientes[0].id != fase.id
                else resultado
            )
        # Quien cierra anota cuántas correcciones cubre (vistas o dichas en la
        # nota). Una que entró después no la atendió nadie: el registro rechaza
        # el cierre en la misma transacción en que lo escribe, haya o no un
        # intento que pueda recibirla, así que no hay carrera con corregir.
        # Sólo al escribir: un cierre ya registrado se relee tal como se
        # aceptó, también los que escribió la versión anterior.
        vistas = d.get("ajustes_vistos")
        if nuevo and vistas is not None and len(e.ajustes) > int(vistas):
            raise TransicionIlegal("llegó una corrección durante el cierre")
        # Con fases bloqueadas el cierre es una entrega parcial: la identidad
        # del cierre por fase se conserva para que `reabrir` la encuentre.
        completo = not pendientes
        return replace(
            e,
            fases=fases,
            estado="terminado",
            evidencia=evidencias,
            cierre_forzado_fase=None if completo else e.cierre_forzado_fase,
            cierre_forzado_huella="" if completo else e.cierre_forzado_huella,
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
    fase = e.fase_actual
    rama = fase.rama if fase is not None and fase.rama else e.entrega.rama
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
            "archivos": [r for r, _ in fase.producto.manifiesto]
            if fase is not None and fase.producto
            else [],
            "firma": fase.producto.firma
            if fase is not None and fase.producto
            else "",
        },
        "revision": {
            "version": ref.version if ref else "",
            "veredicto": ref.veredicto if ref else "",
            "bloqueantes_abiertos": [
                h.como_dict() for h in bloqueantes_abiertos(e)
            ],
            "observaciones": [
                h.titulo
                for h in (fase.hallazgos if fase is not None else ())
                if h.gravedad == "observacion" and h.estado == "abierto"
            ],
        },
        "entrega": {
            "nivel_autorizado": e.entrega.nivel,
            "nivel_efectivo": nivel_objetivo(e),
            "nivel_efectivo_motivo": e.nivel_efectivo_motivo,
            "nivel_alcanzado": e.nivel_alcanzado,
            "repo": e.entrega.repo,
            "rama": rama,
            "base": e.entrega.base,
            "pr": {"numero": e.pr_numero, "sha": e.pr_sha},
            "deepseek": {
                "sha": e.deepseek_sha,
                "leida": bool(e.deepseek_sha)
                and e.deepseek_huella != HUELLA_DEEPSEEK_SIN_REVISION,
                "error": fase.deepseek_error if fase is not None else "",
            },
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
            "bloqueadas": len(
                [f for f in e.fases if f.estado == "bloqueada"]
            ),
            "completo": e.objetivo_completo(),
            "resultado_actual": e.fase_actual.resultado if e.fase_actual else "",
        },

        "ultimo_hecho": e.ultimo_hecho,
        "hora": e.hora,
        "motivo": e.motivo,
        "relevo": {
            "huella": ultima_huella_relevo(e),
            "veces": total_relevos(e),
        },
        "pendientes_entrega": list(e.pendientes_entrega),
        "fuera_de_alcance": [
            {"id": hid, "titulo": titulo, "razon": razon}
            for hid, titulo, razon in e.fuera_de_alcance
        ],
        "herramientas_no_disponibles": list(e.herramientas_no_disponibles),
        "notas_pr": list(e.notas_pr),
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
    if e.pendientes_entrega:
        # Ya no se lanza otro turno: el supervisor entrega lo que hay, con los
        # pendientes escritos, sin esperar a ninguna persona.
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


def _bloque_ci_pr(e: Encargo) -> list[str]:
    """El rojo del CI real del PR, para que el responsable lo corrija."""
    fase = e.fase_actual
    if (
        e.rol != "responsable"
        or fase is None
        or fase.ci_pr_estado != "rojo"
        or not fase.ci_pr_fallas
    ):
        return []
    lineas = [
        "El CI del PR falló en: " + ", ".join(fase.ci_pr_fallas)
        + ". Corrige esa prueba en la misma rama y el mismo PR; lo aprobado que "
        "no cambió se conserva."
    ]
    motivo = str(fase.ci_pr_causa or "").strip()
    if len(motivo) > 4000:
        motivo = motivo[:4000] + "\n[...]"
    if motivo:
        lineas.append("Motivo literal:\n" + motivo)
    return lineas


def _bloque_cierres(e: Encargo) -> list[str]:
    fase = e.fase_actual
    if fase is None:
        return []
    cerrados = [h for h in fase.hallazgos if h.cierre_evidencia]
    if not cerrados:
        return []
    return [
        "El propietario declaró cerrados estos hallazgos. No los reabras sin "
        "una reproducción nueva:\n"
        + "\n".join(
            f"{h.id} [{h.criterio}] {h.titulo} (evidencia: {h.cierre_evidencia})"
            for h in cerrados
        )
    ]


def _bloques_comunes(e: Encargo) -> list[str]:
    lineas = ["El pedido original no se reescribe.", f"Pedido:\n{e.texto}"]
    if e.restricciones:
        lineas.append(
            "Prohibiciones expresas del propietario. No las trates como hipótesis del plan:\n"
            + "\n".join(e.restricciones)
        )
    if e.permisos:
        lineas.append(
            "Rutas permitidas por el pedido (globs; fuera de ellas no se toca):\n"
            + "\n".join(e.permisos)
        )
    if e.prohibiciones:
        lineas.append(
            "Rutas prohibidas por el pedido (globs):\n"
            + "\n".join(e.prohibiciones)
        )
    if e.ajustes:
        lineas.append(
            "Correcciones del propietario, incorporadas después del pedido:\n"
            + "\n".join(e.ajustes)
        )
    lineas += _bloque_cierres(e)
    lineas += _bloque_ci_pr(e)
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
    if e.herramientas_no_disponibles:
        lineas.append(
            "Herramientas no disponibles para este encargo (fallaron igual tres "
            "veces): " + ", ".join(e.herramientas_no_disponibles)
            + ". No las vuelvas a usar; sigue con la evidencia que haya y deja "
            "constancia de lo que no pudiste comprobar."
        )
    if e.pendientes_entrega:
        lineas.append(
            "Pendientes que acompañan a la entrega:\n"
            + "\n".join(e.pendientes_entrega)
        )
    if e.notas_pr:
        lineas.append("Notas para el PR:\n" + "\n".join(e.notas_pr))
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
            "Carga la skill empaquetada del runtime y trabaja como el usuario, con las "
            "herramientas y los accesos de su máquina. "
            "Sharpen, Architect, Arena y Figure-it-out se usan cuando el trabajo los necesita; "
            "no son una cadena obligatoria. Una hipótesis del plan no es una prohibición: "
            "las decisiones técnicas dentro del objetivo y los permisos concedidos las tomas tú "
            "y las registras con decision.registrar. Solo una prohibición expresa del propietario, "
            "o una decisión que el pedido reserva al usuario, necesita su respuesta. "
            "No devuelvas el plan por un detalle técnico ordinario ni por redacción menor."
        )
    ]
    lineas += _bloques_comunes(e)
    lineas.append(f"Nivel de entrega autorizado: {e.entrega.nivel}.")
    if e.nivel_efectivo:
        lineas.append(
            f"El nivel efectivo queda limitado a {e.nivel_efectivo}: "
            + (e.nivel_efectivo_motivo or "el sondeo de capacidad ya lo comprobó")
            + ". No intentes ir más allá del nivel efectivo."
        )
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
    fase = e.fase_actual
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
        if any(h.origen == "deepseek" for h in abiertos):
            lineas.append(
                "Si sostienes con evidencia que un hallazgo de DeepSeek no aplica, "
                "no lo descartes por tu cuenta: deja la evidencia en "
                ".interno/disputa.json con el id del hallazgo. El revisor "
                "independiente decide con esa evidencia si aplica y su decisión "
                "queda escrita en el PR."
            )
    if fase is not None and fase.aprobadas:
        lineas.append(
            "Rutas ya aprobadas; no las reescribas sin necesidad:\n"
            + "\n".join(r for r, _ in fase.aprobadas)
        )
    if e.causa or e.motivo:
        lineas.append(
            f"Última observación: {e.causa or e.motivo}\nSiguiente trabajo concreto: {e.siguiente}"
        )
    lineas.append(
        "Comprueba el comportamiento pedido y deja la evidencia en .interno/evidencia.txt. "
        "No declares el encargo terminado: el supervisor mira el destino. "
        "Si el nivel de entrega autorizado incluye desplegado, declara ese efecto en "
        ".interno/pedido-efecto.json y el supervisor lo despacha por el adaptador y "
        "sella el nivel con su recibo; para todo lo demás trabajas directamente. "
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
                "Rutas aprobadas con el contenido intacto. Conserva su evidencia y vuelve a ellas "
                "si un efecto del cambio lo justifica:\n"
                + "\n".join(alcance.conservadas)
            )
    else:
        lineas.append(
            f"Alcance de esta revisión: completa, contra todo el pedido, "
            f"sobre la versión {alcance.version}."
        )
    lineas.append(
        "Explica el impacto real y la evidencia de cada hallazgo. Un bloqueante identifica "
        "un incumplimiento del pedido o un riesgo concreto sin resolver y cómo reproducirlo. "
        "Elige pruebas y revisiones que aporten evidencia al comportamiento y al riesgo del "
        "cambio. Conserva la evidencia válida y amplía la revisión cuando exista un motivo."
    )
    externos = bloqueantes_abiertos(e, "deepseek")
    if externos:
        lineas.append(
            "Hallazgos externos abiertos de DeepSeek. Los decide esta revisión, no el "
            "responsable:\n"
            + "\n".join(
                f"{h.id} [{h.criterio}] {h.titulo} (ancla: {h.ancla})\n"
                f"  reproduccion: {h.reproduccion}"
                for h in externos
            )
            + "\nSi el hallazgo está fuera del alcance del pedido o de sus restricciones, "
            "inclúyelo en `fuera_de_alcance` como "
            '{"hallazgo": "<id>", "razon": "<por qué>"}; no bloquea y la razón queda '
            "en el PR. Si la disputa con evidencia es correcta, inclúyelo en `resueltos` "
            "y explica en `evidencia` por qué. Si no lo descartas, déjalo abierto y "
            "explica por qué; volverá al responsable para corregirlo."
        )
    return "\n\n".join(lineas)


def prompt_de(e: Encargo) -> str:
    return prompt_responsable(e)
