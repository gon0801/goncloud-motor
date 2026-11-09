"""Las decisiones de David en una sola tabla.

Cada fila lleva la regla, su valor, la decisión del propietario con su fecha y
el comportamiento que la prueba en los límites. El motor lee sus topes de aquí
y la tabla de `AGENTS.md` se genera desde las mismas filas: un valor que viva en
otro sitio vuelve a tener dos dueños.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# Valores de la política. Los lee el motor; no se repiten como constantes en
# `ciclo.py` ni en ningún otro módulo.
TOPE_DEEPSEEK_MIN = 30
CERRADOS_VENTANA_S = 7 * 24 * 60 * 60
CERRADOS_POR_PASADA = 5
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
    claves: tuple[str, ...] = ()
    valor_numerico: int | None = None


FILAS: tuple[Fila, ...] = (
    Fila(
        id="deepseek_tope",
        regla="DeepSeek: 30 minutos y se sigue con omisión explícita.",
        valor=f"{TOPE_DEEPSEEK_MIN} min",
        decision="David, 6-nov",
        comportamiento="a los 29 min espera; a los 31 min omite con la causa escrita",
        claves=("deepseek", "tope", "revisión externa", "revision externa"),
        valor_numerico=TOPE_DEEPSEEK_MIN,
    ),
    Fila(
        id="cerrados",
        regla="Un encargo cerrado se sondea 7 días desde el cierre, con 5 consultas por pasada.",
        valor=f"{CERRADOS_VENTANA_S // 86400} días; {CERRADOS_POR_PASADA} por pasada",
        decision="David, 6-nov",
        comportamiento="a los 6 días sondea; a los 8 no; 5 consultas entran y la 6.ª queda para la pasada siguiente",
        claves=("cerrado", "sondeo", "días", "dias"),
        valor_numerico=CERRADOS_VENTANA_S // 86400,
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
        claves=("medium", "critical", "high", "severidad"),
    ),
    Fila(
        id="medium_mismo_pr",
        regla="Los Medium se corrigen en el mismo PR.",
        valor="mismo PR",
        decision="David, 30-oct",
        comportamiento="un Medium abierto en alcance reabre la fase y vuelve al responsable; no espera otro encargo",
        claves=("medium", "mismo pr", "mismo pull request"),
    ),
    Fila(
        id="relevo_sesion",
        regla="Si Opus no está disponible para revisar, revisa Astra mientras tanto.",
        valor="openai/gpt-6-astra",
        decision="David, 6-nov",
        comportamiento="una sesión de Opus ilegible no espera a una persona: el rol relega a Astra; cuando Opus vuelve, la siguiente revisión es suya",
        claves=("opus", "astra", "sesión", "sesion"),
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


_SOLICITA_CAMBIO = (
    "cambiar", "cambia", "cambie", "subir", "sube", "suban", "bajar", "baja",
    "aumentar", "aumenta", "reducir", "reduce", "ampliar", "amplía", "amplia",
    "debería ser", "deberia ser", "deberían ser", "deberian ser", "hay que",
    "should be", "should increase", "should decrease", "increase", "decrease",
    "lower", "raise", "change", "instead of",
)
_INCUMPLE = (
    "incumple", "no cumple", "no respeta", "no se respeta", "no aplica",
    "no se aplica", "no implementa", "no está implementado", "no esta implementado",
    "difiere de", "falta", "faltan", "ignora", "bug", "does not", "doesn't",
    "not applied", "not implemented", "ignores",
)


_PROPUESTAS = (
    # «de 30 a 15 minutos» propone dos valores; el primero es el vigente.
    re.compile(r"\bde\s+(\d{1,6})\s*(?:minutos?|min|d[ií]as?)?\s*a\s+(\d{1,6})"),
    re.compile(
        r"\b(?:deber[ií]a|deber[ií]an|debe|deben|should(?:\s+be)?|hay\s+que)"
        r"\s+(?:ser|be|usar|use)?\s*(\d{1,6})"
    ),
    re.compile(
        r"\b(?:subir|sube|suban|bajar|baja|bajan|aumentar|aumenta|reducir|"
        r"reduce|ampliar|ampl[ií]a|amplia|cambiar|change|lower|raise|increase|"
        r"decrease)\b[^\d]{0,30}(\d{1,6})"
    ),
)


def _propuestas(texto: str) -> list[int]:
    """Solo los números que el hallazgo propone como valor nuevo."""
    vistos: list[int] = []
    for patron in _PROPUESTAS:
        for grupos in patron.findall(texto):
            valores = grupos if isinstance(grupos, tuple) else (grupos,)
            vistos.extend(int(v) for v in valores if v)
    return vistos


def hallazgo_contra_politica(hallazgo) -> str:
    """Razón por la que un hallazgo pide cambiar una regla de David.

    Solo cuenta una petición de cambio de valor. Un hallazgo que dice que el
    código incumple una regla sigue siendo un hallazgo normal: ahí el defecto
    está en el código, no en la decisión del propietario.
    """
    texto = " ".join(
        str(x or "")
        for x in (
            getattr(hallazgo, "titulo", ""),
            getattr(hallazgo, "reproduccion", ""),
        )
    ).lower()
    if not texto.strip() or any(marca in texto for marca in _INCUMPLE):
        return ""
    if not any(marca in texto for marca in _SOLICITA_CAMBIO):
        return ""
    propuestas = _propuestas(texto)
    for fila in FILAS:
        if fila.valor_numerico is None:
            continue
        if not any(clave in texto for clave in fila.claves):
            continue
        if any(n != fila.valor_numerico for n in propuestas):
            return (
                "el hallazgo pide cambiar el valor de una regla del propietario: "
                f"«{fila.regla}» = {fila.valor} ({fila.decision}); una decisión "
                "de política no se resuelve en este PR"
            )
    return ""
