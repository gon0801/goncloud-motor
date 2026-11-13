"""Las decisiones de David en una sola tabla.

Cada fila lleva la regla, su valor, la decisión del propietario con su fecha y
el comportamiento que la prueba en los límites. El motor lee sus topes de aquí
y la tabla de `AGENTS.md` se genera desde las mismas filas: un valor que viva en
otro sitio vuelve a tener dos dueños.
"""

from __future__ import annotations

from dataclasses import dataclass

# Valores de la política. Los lee el motor; no se repiten como constantes en
# `ciclo.py` ni en ningún otro módulo.
TOPE_DEEPSEEK_MIN = 30
CERRADOS_VENTANA_S = 7 * 24 * 60 * 60
CERRADOS_POR_PASADA = 5
# Cadencia del sondeo de cerrados: rápido la primera hora, espaciado después y
# una gracia para la pausa del supervisor entre el cierre y su primera pasada.
CERRADOS_SONDEO_RAPIDO_S = 60 * 60
CERRADOS_GRACIA_S = 30 * 60
CERRADOS_RELECTURA_S = 30 * 60
SIN_TOPE_RONDAS = True
CI_BLOQUEA_MERGE = False
# Critical, High y Medium bloquean mientras estén dentro del alcance del pedido.
DEEPSEEK_BLOQUEANTES = ("critical", "high", "medium")
MEDIUM_EN_MISMO_PR = True
# Si la sesión del modelo de revisión de Opus no se puede leer, revisa Astra
# mientras tanto; cuando Opus vuelve, la siguiente revisión es suya.
RELEVO_POR_SESION = {
    "revisor": (
        ("opus", "openai/gpt-6-astra"),
    ),
}


@dataclass(frozen=True)
class Fila:
    id: str
    regla: str
    valor: str
    decision: str
    comportamiento: str


FILAS: tuple[Fila, ...] = (
    Fila(
        id="deepseek_tope",
        regla="DeepSeek: 30 minutos y se sigue con omisión explícita.",
        valor=f"{TOPE_DEEPSEEK_MIN} min",
        decision="David, 6-nov",
        comportamiento="a los 29 min espera; a los 31 min omite con la causa escrita",
    ),
    Fila(
        id="cerrados",
        regla="Un encargo cerrado se sondea 7 días desde el cierre, con 5 consultas por pasada.",
        valor=f"{CERRADOS_VENTANA_S // 86400} días; {CERRADOS_POR_PASADA} por pasada",
        decision="David, 6-nov",
        comportamiento="a los 6 días sondea; a los 8 no; 5 consultas entran y la 6.ª queda para la pasada siguiente",
    ),
    Fila(
        id="ci_no_compuerta",
        regla="El CI no autoriza el merge, pero no frena publicar, revisar ni corregir.",
        valor="no es compuerta",
        decision="David, 3-nov",
        comportamiento="un CI pendiente o verde no detiene la entrega; un error de lectura nunca cuenta como verde",
    ),
    Fila(
        id="sin_tope_rondas",
        regla="Sin tope de rondas.",
        valor="sin tope",
        decision="David, 1-nov",
        comportamiento="el freno no escala por contar vueltas",
    ),
    Fila(
        id="deepseek_bloquea",
        regla="DeepSeek bloquea con Critical, High o Medium dentro del alcance.",
        valor="Critical, High o Medium",
        decision="David, sin fecha registrada en el pedido",
        comportamiento="un Medium abierto en alcance bloquea; uno fuera de alcance no",
    ),
    Fila(
        id="medium_mismo_pr",
        regla="Los Medium se corrigen en el mismo PR.",
        valor="mismo PR",
        decision="David, 30-oct",
        comportamiento="un Medium abierto en alcance reabre la fase y vuelve al responsable; no espera otro encargo",
    ),
    Fila(
        id="relevo_sesion",
        regla="Si Opus no está disponible para revisar, revisa Astra mientras tanto.",
        valor="openai/gpt-6-astra",
        decision="David, 6-nov",
        comportamiento="una sesión de Opus ilegible no espera a una persona: el rol relega a Astra; cuando Opus vuelve, la siguiente revisión es suya",
    ),
    Fila(
        id="nada_espera_persona",
        regla="Nada espera a un humano salvo el merge.",
        valor="sin espera",
        decision="David, 3-nov",
        comportamiento="el ciclo sigue solo; la prueba del motor sin personas",
    ),
    Fila(
        id="arreglos_raiz",
        regla="Arreglos de raíz.",
        valor="causa, no síntoma",
        decision="David, 1-nov",
        comportamiento="la revisión independiente y las pruebas del flujo de revisiones",
    ),
    Fila(
        id="opencode_deepseek",
        regla="OpenCode Go solo DeepSeek.",
        valor="opencode-go/deepseek-v4.1-flash",
        decision="David, 1-nov",
        comportamiento="el catálogo rechaza otro modelo de OpenCode Go",
    ),
    Fila(
        id="home_temporal",
        regla="Las pruebas corren con el HOME en un temporal.",
        valor="HOME temporal",
        decision="David, sin fecha registrada en el pedido",
        comportamiento="la guarda de HOME del banco de pruebas",
    ),
    Fila(
        id="repo_publico",
        regla="Repo público, sin secretos ni runners propios.",
        valor="público y sin secretos",
        decision="David, 31-oct",
        comportamiento="gitleaks en el job de calidad",
    ),
    Fila(
        id="un_dato_un_dueno",
        regla="Un dato, un dueño: derivar en vez de guardar copias.",
        valor="derivar",
        decision="David, sin fecha registrada en el pedido",
        comportamiento="la tabla de política y la de AGENTS.md se generan de una sola fuente",
    ),
)


def valor(fila_id: str) -> Fila:
    for fila in FILAS:
        if fila.id == fila_id:
            return fila
    raise KeyError(fila_id)


def tabla_markdown() -> str:
    """La tabla de decisiones tal como se publica en AGENTS.md."""
    lineas = [
        "| Regla | Valor | Decisión de David | Qué la hace cumplir |",
        "| --- | --- | --- | --- |",
    ]
    for fila in FILAS:
        lineas.append(
            f"| {fila.regla} | {fila.valor} | {fila.decision} | {fila.comportamiento} |"
        )
    return "\n".join(lineas)


def tabla_de_agents(texto: str) -> str:
    """La tabla de decisiones que hoy publica un texto, como una sola cadena."""
    lineas = texto.splitlines()
    for indice, linea in enumerate(lineas):
        if linea.startswith("| Regla |"):
            bloque = []
            for actual in lineas[indice:]:
                if not actual.startswith("|"):
                    break
                bloque.append(actual)
            return "\n".join(bloque)
    return ""


def deepseek_bloquea(gravedad: str, en_alcance: bool) -> bool:
    return en_alcance and str(gravedad or "").lower() in DEEPSEEK_BLOQUEANTES


def relevo_de_sesion(rol: str, modelo: str) -> str:
    """El modelo autorizado para cubrir una sesión de revisión ilegible."""
    identidad = str(modelo or "").partition("/")[2].lower()
    if not identidad:
        return ""
    for clave, sustituto in RELEVO_POR_SESION.get(rol, ()):
        if clave in identidad:
            return sustituto
    return ""
