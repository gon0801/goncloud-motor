"""Encargo: datos persistentes y situación tipada en un único dueño."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .dominio import (
        Decision, Efecto, EntregaAutorizada, Fase, Impedimento, LecturaDeepSeek,
        MarcasDeepSeekCerrado, PausaAgente, Proc, RefRevision, Solicitud,
        SondeoCI, VersionProducto,
    )
    from .situacion import Situacion
    from .entrega.tipos import PrDeFase


def _por_defecto_entrega() -> EntregaAutorizada:
    from .dominio import EntregaAutorizada

    return EntregaAutorizada()


def _por_defecto_lectura() -> LecturaDeepSeek:
    from .dominio import LecturaDeepSeek

    return LecturaDeepSeek()


def _por_defecto_cerrado() -> MarcasDeepSeekCerrado:
    from .dominio import MarcasDeepSeekCerrado

    return MarcasDeepSeekCerrado()


def _por_defecto_sondeo() -> SondeoCI:
    from .dominio import SondeoCI

    return SondeoCI()


def _por_defecto_situacion() -> Situacion:
    from .situacion import Recibido

    return Recibido()


CAMPOS_LEGADOS = (
    "estado", "orden_parada", "reanuda_en", "intento", "proc", "reserva",
    "intento_interrumpido", "cierre_forzado_fase", "cierre_forzado_huella",
)


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
    situacion: Situacion = field(default_factory=_por_defecto_situacion)
    ultimo_hecho: str = ""
    hora: str = ""
    motivo: str = ""
    siguiente: str = ""
    preguntas: tuple[str, ...] = ()
    pendientes: tuple[str, ...] = ()
    herramienta: str = ""
    causa: str = ""
    estrategia: str = "investigar"
    firma_fallo: str = ""
    repeticiones: int = 0
    escrituras: bool = True
    intentos_nuevos: bool = True
    evidencia: tuple[str, ...] = ()
    efectos: tuple[Efecto, ...] = ()
    autoridad: bool = True
    sesion: str = ""
    herramientas_observables: bool = True
    rol: str = ""
    generacion: int = 0
    conductor: str = ""
    candidato: str = ""
    host: str = ""
    agentes: tuple[str, ...] = ()
    agente: str = ""
    pausas: tuple[PausaAgente, ...] = ()
    via: str = "durable"
    entrega: EntregaAutorizada = field(default_factory=_por_defecto_entrega)
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
    lectura_deepseek: LecturaDeepSeek = field(default_factory=_por_defecto_lectura)
    deepseek_cerrado: MarcasDeepSeekCerrado = field(
        default_factory=_por_defecto_cerrado
    )
    sondeo_ci: SondeoCI = field(default_factory=_por_defecto_sondeo)
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
    # Excepciones del check de merge derivadas de eventos `excepcion`:
    # (tipo, sha, id, motivo, hora). Única fuente del marcador `motor:excepciones=`.
    excepciones: tuple[tuple[str, str, str, str, str], ...] = ()
    # Fuera de alcance heredado de un evento `hallazgo_fuera_de_alcance`:
    # (id, motivo, hora). Solo alimenta el marcador cuando el encargo no tiene
    # excepciones estructuradas. No incluye lo que agrega
    # `_fuera_de_alcance_por_restricciones`, que no se juzgó en un SHA.
    excepciones_legado: tuple[tuple[str, str, str], ...] = ()
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
    prs: tuple[PrDeFase, ...] = ()
    canceladas: tuple[str, ...] = ()
    otra_base_de: tuple[str, str] = ("", "")
    limpieza: tuple[tuple[str, str, str, int, str], ...] = ()
    limpio: str = ""
    hora_final: str = ""
    despliegue_mapa: tuple[tuple[str, str], ...] = ()
    despliegue_archivo: str = ""
    solicitudes: tuple[Solicitud, ...] = ()
    decisiones: tuple[Decision, ...] = ()
    autorizaciones: tuple[tuple[str, str, str, str, str, str], ...] = ()

    def __post_init__(self) -> None:
        from .situacion import materializar

        object.__setattr__(self, "situacion", materializar(self))

    @property
    def estado(self) -> str:
        return self.situacion.estado

    @property
    def orden_parada(self) -> str:
        return self.situacion.contexto.orden_parada

    @property
    def reanuda_en(self) -> str:
        return self.situacion.contexto.reanuda_en

    @property
    def intento(self) -> str:
        return self.situacion.contexto.intento

    @property
    def proc(self) -> Proc | None:
        return self.situacion.contexto.proc

    @property
    def reserva(self) -> bool:
        return self.situacion.contexto.reserva

    @property
    def intento_interrumpido(self) -> bool:
        return self.situacion.contexto.intento_interrumpido

    @property
    def cierre_forzado_fase(self) -> Fase | None:
        return self.situacion.contexto.cierre_forzado_fase

    @property
    def cierre_forzado_huella(self) -> str:
        return self.situacion.contexto.cierre_forzado_huella

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
        from .dominio import nivel_de

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


def evolucionar(e: Encargo, **cambios: Any) -> Encargo:
    from .situacion import base_de_estado

    estado = cambios.pop("estado", e.estado)
    contexto = {nombre: cambios.pop(nombre) for nombre in CAMPOS_LEGADOS[1:]
                if nombre in cambios}
    return replace(
        e, situacion=base_de_estado(estado, replace(e.situacion.contexto, **contexto)),
        **cambios,
    )
