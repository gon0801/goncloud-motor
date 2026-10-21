"""C2: el motor corre poteto-mode; cada CLI corre un paso como delegado con las hojas pegadas.

Módulos nuevos y cambios, en el estilo del motor (Python 3.12, dataclasses congeladas, lo puro en
`poteto.py` y `machine.py`, los efectos en `pstack.py` y `effects.py`). Todo cuerpo es
`raise NotImplementedError`; los `# TODO` son la lógica que falta.

Flujo de una ronda `feature` (lane gate):

  decide(ARREGLAR) -> poteto.siguiente(jugada, bv) -> [Lanzamiento(fundar, 0)]
     start_attempt -> brief = poteto.brief(...)  (reglas + adaptador del CLI + hojas verbatim + encargo)
     collect -> poteto.comprueba(Documento how.md) -> paso_hecho
  decide -> [Lanzamiento(disenar, 0), Lanzamiento(disenar, 1)]      dos candidatos en paralelo
  decide -> [Lanzamiento(implementar, 0)]  recibe los design.md válidos, nombra su base
  decide -> [Lanzamiento(pulir, 0)]        último paso que escribe -> `entregado` -> VERIFICAR (igual que hoy)
"""

from __future__ import annotations

import enum
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

# =============================================================================
# conf.py (cambios)
# =============================================================================


@dataclass(frozen=True)
class PstackPin:
    """`pstack.toml` de la conf compartida: la versión de pstack fijada para las tres máquinas.
    Subir de versión es un PR en goncloud-motor-conf, nada en las máquinas."""

    origen: str  # repo público de GitHub, p. ej. "cursor/plugins"
    commit: str  # 7 a 40 hex; content-addressed en ~/.motor/pstack/<commit>/
    raiz: str  # carpeta dentro del tarball que trae skills/, agents/ y docs/ ("pstack")


# Conf gana:  pstack: PstackPin
#             pasos: dict[str, str]        # id de paso -> texto de reglas/pasos/<id>.md
#             adaptadores: dict[str, str]  # cli -> texto de reglas/adaptadores/<cli>.md ("" si no hay)
#
# load_conf exige pstack.toml completo y, por cada Paso de poteto.JUGADAS, su reglas/pasos/<id>.md.
# Un adaptador faltante no es error (Claude no necesita tabla); uno sobrante para un cli sin ruta sí.


def valida_pstack(raw: dict, pasos_requeridos: tuple[str, ...], pasos_presentes: tuple[str, ...]) -> list[str]:
    """Errores de `pstack.toml` y de `reglas/pasos/`. Lo usa `motor conf valida` y `load_conf`."""
    raise NotImplementedError


# =============================================================================
# plan.py (cambio)
# =============================================================================

PLAYBOOKS = ("feature", "docs", "bug-fix")

# Block gana:  playbook: str = ""   # "" = el de su lane (fast -> docs, gate/release -> feature)
# parse_plan valida `playbook:` contra PLAYBOOKS y lo guarda; to_dict/from_dict lo llevan.


# =============================================================================
# poteto.py (nuevo, puro): la tabla de playbooks, el siguiente paso, el brief y la comprobación
# =============================================================================


class Por(enum.StrEnum):
    BLOQUE = "bloque"  # una vez por bloque: las rondas de arreglo no lo repiten (fundar, disenar)
    RONDA = "ronda"  # cada ronda del papel (implementar, pulir, revisar)


@dataclass(frozen=True)
class Documento:
    """El paso deja un archivo en la carpeta del intento con la forma de su template."""

    archivo: str  # "how.md" | "design.md"
    secciones: tuple[str, ...]  # encabezados que deben estar (los del template de la hoja)
    cita_rutas: bool  # toda ruta entre acentos graves debe existir en el repo
    min_lineas: int


@dataclass(frozen=True)
class Commits:
    """El paso entrega commits: el SHA del result es la punta de la rama. `puede_no_mover` para pulir."""

    puede_no_mover: bool = False


@dataclass(frozen=True)
class Repro:
    """El paso entrega un comando que reproduce el bug: sale distinto de 0 en el SHA base. Ese
    comando queda como `Acepta` implícito del bloque (debe salir 0 en lo entregado)."""


@dataclass(frozen=True)
class Veredicto:
    """Como hoy (SHA y veredicto válidos) y además cada `repro` se corre: si sale 0 no reprodujo y
    el hallazgo baja a notas. Regla 4 de quality-kit en código."""


Entrega = Documento | Commits | Repro | Veredicto


@dataclass(frozen=True)
class Paso:
    id: str
    hojas: tuple[str, ...]  # rutas dentro de <pstack>/ que se pegan verbatim en el brief
    escribe: bool  # trabaja en la carpeta del bloque (True) o en una copia de solo lectura (False)
    entrega: Entrega
    por: Por = Por.RONDA
    candidatos: int = 1  # delegados en paralelo; el paso queda hecho con >= 1 válido cuando todos terminaron
    insumos: tuple[str, ...] = ()  # ids de pasos previos cuyas entregas válidas se le dan por ruta


@dataclass(frozen=True)
class Jugada:
    """Un playbook de poteto-mode llevado por el motor. `fuente` cita el archivo y los pasos."""

    nombre: str
    pasos: tuple[Paso, ...]
    fuente: str

    def paso(self, id_: str) -> Paso:
        raise NotImplementedError

    def ultimo_que_escribe(self) -> Paso:
        """El paso cuyo éxito es `entregado` (la punta que va a VERIFICAR)."""
        raise NotImplementedError


_HOW = ("skills/how/references/explainer-prompt.md",)
_ARCHITECT = (
    "skills/architect/references/runner-prompt.md",
    "skills/architect/references/rationale-template.md",
    "skills/architect/references/design-red-flags.md",
)
_IMPLEMENTA = (
    "skills/principle-model-the-domain/SKILL.md",
    "skills/principle-sequence-verifiable-units/SKILL.md",
    "skills/principle-prove-it-works/SKILL.md",
)
_PULE = (
    "skills/deslop/SKILL.md",
    "agents/comment-sicko.md",
    "skills/technical-writing/SKILL.md",
    "skills/unslop/SKILL.md",
)
_REVISA = (
    "skills/interrogate/references/reviewer-prompt.md",
    "skills/interrogate/references/rubric.md",
    "skills/interrogate/references/code-quality-review.md",
    "skills/interrogate/references/lead-judgment.md",
)

FUNDAR = Paso(
    "fundar",
    _HOW,
    escribe=False,
    por=Por.BLOQUE,
    entrega=Documento("how.md", ("Overview", "Key Concepts", "How It Works", "Where Things Live", "Gotchas"), True, 30),
)
DISENAR = Paso(
    "disenar",
    _ARCHITECT,
    escribe=False,
    por=Por.BLOQUE,
    candidatos=2,  # architect: "Require at least two structurally distinct candidates"
    insumos=("fundar",),
    entrega=Documento(
        "design.md",
        ("Problem", "Usage", "Shape", "Tradeoffs accepted", "Alternatives considered", "Open questions and risks", "Next implementation step"),
        True,
        40,
    ),
)
REPRODUCIR = Paso("reproducir", (), escribe=False, por=Por.BLOQUE, entrega=Repro())
IMPLEMENTAR = Paso("implementar", _IMPLEMENTA, escribe=True, insumos=("fundar", "disenar", "reproducir"), entrega=Commits())
IMPLEMENTAR_TDD = Paso("implementar", (*_IMPLEMENTA, "skills/tdd/SKILL.md"), escribe=True, insumos=("fundar", "reproducir"), entrega=Commits())
PULIR = Paso("pulir", _PULE, escribe=True, insumos=("disenar",), entrega=Commits(puede_no_mover=True))
REDACTAR = Paso("redactar", ("skills/unslop/SKILL.md", "skills/technical-writing/SKILL.md"), escribe=True, entrega=Commits())
REVISAR = Paso("revisar", _REVISA, escribe=False, insumos=("fundar", "disenar"), entrega=Veredicto())

JUGADAS: dict[str, Jugada] = {
    "docs": Jugada("docs", (REDACTAR,), "poteto-mode: Non-negotiables (prosa -> unslop, docs -> technical-writing)"),
    "feature": Jugada("feature", (FUNDAR, DISENAR, IMPLEMENTAR, PULIR), "poteto-mode/playbooks/feature.md pasos 1, 2, 4, 6 y Opening a PR (deslop, no-comments)"),
    "bug-fix": Jugada("bug-fix", (REPRODUCIR, FUNDAR, IMPLEMENTAR_TDD, PULIR), "poteto-mode/playbooks/bug-fix.md pasos 1, 2, 3, 5"),
    "revision": Jugada("revision", (REVISAR,), "interrogate pasos 3 y 5, un revisor que también es lead"),
}

# Toda hoja que nombra la tabla, más lo que `Pstack.materializa` exige para dar la carpeta por buena.
HOJAS_REQUERIDAS: tuple[str, ...] = tuple(sorted({h for j in JUGADAS.values() for p in j.pasos for h in p.hojas}))


def jugada_para(block, role: str, revisores_panel: int = 1) -> Jugada:
    """revisar -> `revision` (con `candidatos` = revisores_panel). implementar -> block.playbook o,
    sin él, el de su lane: fast -> docs; gate y release -> feature."""
    raise NotImplementedError


@dataclass(frozen=True)
class Lanzamiento:
    paso: str
    cand: int  # 0..candidatos-1


def hechos(bv, paso: Paso, role: str) -> list:
    """Intentos de este papel y paso que terminaron válidos (`paso_hecho` o `entregado`). Para
    `Por.BLOQUE` cuenta toda la historia del bloque; para `Por.RONDA`, solo desde el último
    `entregado` (o `veredicto`) del papel."""
    raise NotImplementedError


def siguiente(jugada: Jugada, bv, role: str) -> tuple[Lanzamiento, ...]:
    """Qué lanzar ahora para este papel: vacío si algo corre o la jugada ya terminó.

    Recorre `jugada.pasos` en orden. El primer paso sin ningún válido:
      - si tiene candidatos sin lanzar en esta ronda -> esos Lanzamientos (todos de una vez);
      - si todos sus candidatos terminaron sin ninguno válido -> se relanza el paso completo
        (es lo que `_elige` castiga con `fallas_seguidas_max` y el relevo).
    Un paso con >= 1 válido pero candidatos aún corriendo no abre el siguiente: espera.
    """
    raise NotImplementedError


def ladder_para(lanz: Lanzamiento, bv, ladder: tuple[str, ...], role: str) -> tuple[str, ...]:
    """El modelo que ya trabajó esta ronda va primero (menos autores, mismo estilo), la escalera
    después. Para `disenar` con 2 candidatos, el segundo prefiere un modelo distinto del primero."""
    raise NotImplementedError


def autores(bv) -> set[str]:
    """Reemplaza BlockView.autores(): todo modelo de un paso del papel implementar que no sea
    `fundar`. Quien diseñó o escribió no revisa; quien solo explicó el código sí puede."""
    raise NotImplementedError


@dataclass(frozen=True)
class Insumo:
    paso: str
    cand: int
    ruta: Path  # <dir del intento>/<archivo>
    modelo: str


def insumos(bv, paso: Paso, role: str) -> tuple[Insumo, ...]:
    """Las entregas válidas de los pasos que `paso.insumos` nombra, por ruta (no se pegan: guard-the-
    context-window). Para `disenar` son todos los candidatos válidos: el implementador elige base."""
    raise NotImplementedError


def brief(
    *,
    jugada: Jugada,
    paso: Paso,
    cand: int,
    cli: str,
    reglas: str,
    adaptador: str,
    hojas: tuple[tuple[str, str], ...],  # (ruta en pstack, texto verbatim)
    encargo_paso: str,  # reglas/pasos/<id>.md de la conf
    insumos_: tuple[Insumo, ...],
    cuerpo: str,  # lo de hoy: filas, feedback, pendientes, cómo se entrega (según el paso)
) -> str:
    """El brief de un delegado. Secciones, en este orden:
    1. reglas de todo encargo (conf);
    2. encabezado: "Paso k de n: <id> · <plan> · <bloque> · candidato c de N. El motor lleva el
       playbook <nombre> de poteto-mode. Eres un delegado: no cargues skills ni abras subagentes;
       todo lo que necesitas está abajo. Nadie lee tu pantalla";
    3. "Tus herramientas (<cli>)": el adaptador, si lo hay;
    4. "Hojas de pstack": cada hoja con su ruta como encabezado, verbatim;
    5. "Insumos": rutas y quién las hizo;
    6. "Qué entregar": encargo_paso + cuerpo + el JSON de $MOTOR_RESULT que pide la Entrega.
    Puro: ni disco ni reloj."""
    raise NotImplementedError


def comprueba(
    paso: Paso,
    dir_intento: Path,
    res: dict,
    *,
    punta: str,
    sha_inicio: str,
    existe_en_repo: Callable[[str], bool],
    corre_repro: Callable[[str], int] | None = None,  # exit code en la copia; lo da effects
    bases_validas: tuple[str, ...] = (),  # "cand-0", "cand-1": a quién puede nombrar el implementador
) -> dict:
    """outcome del intento según `paso.entrega`. Agota la unión:
    Documento -> `paso_hecho` o `paso_invalido` ("design.md no trae la sección X" / "cita `a/b.py`, que no existe").
    Commits   -> `entregado` si es el último que escribe, si no `paso_hecho`; `mintio` si el SHA no es la punta;
                 `sin_cambios` si la punta no se movió (salvo puede_no_mover); `paso_invalido` si `base` no está en bases_validas.
    Repro     -> corre el comando: 0 = no reproduce -> `paso_invalido`; distinto de 0 -> `paso_hecho` con {"repro": cmd}.
    Veredicto -> como hoy; los bloqueantes cuyo repro sale 0 pasan a notas con "no reprodujo".
    Lee solo `dir_intento` (archivo de la Entrega); lo demás viene por parámetros."""
    raise NotImplementedError


def rutas_citadas(texto: str) -> tuple[str, ...]:
    """Rutas entre acentos graves con al menos un `/` o una extensión; lo que `cita_rutas` revisa."""
    raise NotImplementedError


def secciones_faltantes(texto: str, secciones: tuple[str, ...]) -> tuple[str, ...]:
    """Encabezados Markdown (`## X`) que faltan, sin distinguir mayúsculas."""
    raise NotImplementedError


# =============================================================================
# pstack.py (nuevo, efecto): materializar la versión fijada y leer hojas
# =============================================================================


class Pstack:
    """La copia de pstack fijada por la conf, en `~/.motor/pstack/<commit>/`. Content-addressed e
    idempotente: la misma conf en tres máquinas da la misma carpeta."""

    def __init__(self, home: Path, pin: PstackPin):
        raise NotImplementedError

    @property
    def dir(self) -> Path:
        raise NotImplementedError

    def materializa(self) -> Path:
        """Si la carpeta no existe: baja https://codeload.github.com/<origen>/tar.gz/<commit> con
        urllib (repo público, sin gh), extrae `<raiz>/` a una carpeta temporal, exige HOJAS_REQUERIDAS
        y hace rename atómico. Falla -> RuntimeError (el daemon lo anota como EffectFailed y no
        arranca intentos hasta tenerla). Nunca borra una versión anterior: `limpia` lo hace al cambiar el pin."""
        raise NotImplementedError

    def hoja(self, ruta: str) -> str:
        """Texto verbatim de `<dir>/<ruta>`. KeyError si no existe (la conf nombró una hoja que esta versión no trae)."""
        raise NotImplementedError

    def hojas(self, rutas: tuple[str, ...]) -> tuple[tuple[str, str], ...]:
        raise NotImplementedError

    def estado(self) -> str:
        """Para `doctor`: "pstack <commit[:12]> listo" o "falta materializar"."""
        raise NotImplementedError


# =============================================================================
# machine.py (cambios)
# =============================================================================

EXITOS = ("entregado", "veredicto", "paso_hecho")  # reemplaza los ("entregado", "veredicto") sueltos
NO_ES_FALLA = (*EXITOS, "corte")


@dataclass
class Attempt:
    """Igual que hoy más el paso y el candidato."""

    n: int
    role: str
    modelo: str
    started: float
    proc: dict
    dir: str
    paso: str = "implementar"  # intentos viejos sin paso se leen como el único paso de antes
    cand: int = 0
    sha: str = ""
    ended: dict | None = None
    ended_at: float = 0.0


@dataclass(frozen=True)
class StartAttempt:
    block: str
    role: str
    modelo: str
    n: int
    sha: str
    feedback: tuple[str, ...]
    paso: str  # obligatorio: no existe un intento sin paso
    cand: int = 0


# BlockView.head(): sin cambio (`entregado` solo lo emite el último paso que escribe).
# BlockView.autores(): delega en poteto.autores(self).
# BlockView.running(): pasa a devolver TODOS los que corren (candidatos en paralelo); `estado()` usa any().
#
# decide(), rama ARREGLAR / REVISAR, reemplaza el StartAttempt único por:
#
#   jugada = poteto.jugada_para(b, role, policy.revisores_panel)
#   for lanz in poteto.siguiente(jugada, bv, role):
#       ladder = poteto.ladder_para(lanz, bv, ladder_base, role)
#       mo = _elige(ladder, snap, policy, now, bv, role, reservas)       # topes de siempre: cada candidato ocupa un lugar
#       if mo is None: acciones.extend(_sin_agente(...)); break
#       reservas[cuenta(mo)] += 1
#       acciones.append(StartAttempt(bid, role, mo, n, bv.head(), fb, paso=lanz.paso, cand=lanz.cand))
#
# _feedback(): agrega, si el último intento del papel fue `paso_invalido`, su detalle con el paso:
#   "Tu design.md (paso disenar) no trae la sección Alternatives considered". Ya lo hace para cualquier kind.
#
# Policy gana: revisores_panel: int = 1, disenar_candidatos: int = 2   (de general.toml [poteto]).


# =============================================================================
# relevo.py (cambio)
# =============================================================================

# DE_KIND gana:
#   "paso_invalido": Falla.INVALID_TERMINAL,   # el proveedor contestó, pero no con la forma del contrato
#   "mintio": Falla.INVALID_TERMINAL,
#   "sin_cambios": Falla.TASK_FAILURE,
# (los dos últimos faltaban: hechos §6, "relevo.DE_KIND no conoce mintio ni sin_cambios").


# =============================================================================
# effects.py (cambios)
# =============================================================================


class EffectsPoteto:
    """Lo que cambia en Effects. `Contexto` gana `pstack: Pstack`."""

    def start_attempt(self, plan, snap, a: StartAttempt) -> None:
        """Como hoy hasta elegir cwd, y luego:
        - `paso = jugada.paso(a.paso)`; cwd = ws.prepara(...) si paso.escribe, si no ws.copia(sha, d/"copia")
          (fundar/disenar/revisar leen la punta actual del bloque, o la base si aún no hay entrega);
        - brief = poteto.brief(jugada, paso, a.cand, ruta.cli, conf.reglas, conf.adaptadores.get(cli, ""),
                               self.cx.pstack.hojas(paso.hojas), conf.pasos[paso.id],
                               poteto.insumos(bv, paso, a.role), cuerpo=self._cuerpo(paso, ...));
        - `datos` del AttemptStarted gana "paso", "cand" y "pstack": pin.commit (auditoría);
        - el resto (argv de la ruta, WSL, env, runwrap, shims) no cambia."""
        raise NotImplementedError

    def _cuerpo(self, paso: Paso, plan, block, a: StartAttempt, cwd: Path, rc) -> str:
        """Filas, feedback y pendientes de hoy para los pasos que escriben; solo filas y SHA para
        los de lectura. Para `implementar` añade: "Diseños válidos: cand-0 (Sonnet), cand-1 (Kimi).
        Nombra en result.json {"base": "cand-k"} cuál sigues y por qué"."""
        raise NotImplementedError

    def collect(self, plan, snap, block_id: str, att: Attempt) -> dict | None:
        """Como hoy hasta `terminado` y la lectura de result.json; después:
        return poteto.comprueba(paso, Path(att.dir), res,
                                punta=ws.punta(...), sha_inicio=att.sha,
                                existe_en_repo=lambda p: (espejo / p).exists(),   # en el SHA de la copia
                                corre_repro=lambda cmd: _run_tope(cmd, copia, env, tope).returncode,
                                bases_validas=tuple(f"cand-{i.cand}" for i in poteto.insumos(bv, paso, role) if i.paso == "disenar"))
        `Reviewed` se anota con los bloqueantes ya filtrados por repro. `Repro` válido se anota como
        evento `ReproFijado` {"cmd"} y `verify()` lo corre como un Acepta más (debe salir 0)."""
        raise NotImplementedError

    def verify(self, plan, snap, a) -> None:
        """Hoy más el repro fijado del bloque (bug-fix): exit 0 en el SHA entregado, si no falla la verificación."""
        raise NotImplementedError


# =============================================================================
# daemon.py / __main__.py (cambios chicos)
# =============================================================================

# daemon.recargar_conf: al cambiar el pin, `Pstack(home, conf.pstack).materializa()` antes de aplicar la
#   conf; si falla, la conf nueva se rechaza como hoy (ConfRechazada + aviso), y se sigue con la anterior.
# doctor: una línea con `pstack.estado()`.
# estado --json: cada intento trae "paso" y "cand" (claw y Hermi lo muestran en lenguaje llano:
#   "diseñando (2 propuestas)", "puliendo", "revisando").


# =============================================================================
# Pruebas que el sketch exige (tests/test_poteto.py, puras salvo la e2e)
# =============================================================================

# test_feature_corre_fundar_disenar_x2_implementar_pulir_en_orden
# test_disenar_abre_implementar_solo_cuando_los_dos_candidatos_terminaron_y_uno_es_valido
# test_design_sin_seccion_es_paso_invalido_y_el_feedback_la_nombra
# test_how_que_cita_ruta_inexistente_es_paso_invalido
# test_ronda_de_arreglo_no_repite_fundar_ni_disenar
# test_implementador_que_nombra_base_inexistente_es_paso_invalido
# test_autores_incluye_disenar_y_excluye_fundar
# test_bloqueante_cuyo_repro_sale_0_baja_a_notas
# test_reproducir_que_sale_0_en_la_base_es_paso_invalido_y_el_valido_se_vuelve_acepta
# test_docs_es_un_solo_paso
# test_paso_invalido_cuenta_para_el_relevo_como_invalid_terminal
# test_cada_candidato_ocupa_un_lugar_del_tope_de_la_maquina
# test_materializa_es_idempotente_y_exige_hojas_requeridas  (tarball falso en disco)
# test_conf_valida_exige_pstack_toml_y_un_md_por_paso
# e2e con GitHub falso: un delegado falso entrega design.md sin secciones dos veces -> entra otro modelo
