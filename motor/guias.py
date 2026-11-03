"""Las guías que lleva un encargo pstack, según la exigencia del (carril, papel) y el CLI.

Una función pura: la misma lista la usa `effects` para escribir el anexo del encargo y
`errores_pstack` (`motor conf valida`) para exigir que cada archivo exista en la copia fija.
"""

from __future__ import annotations

from .pstack import SKILL_BASE, Exigencia


def guias(exigencia: Exigencia, cli: str, correccion: bool) -> tuple[str, ...]:
    """Rutas relativas a la copia fija, en el orden en que van en el encargo.

    `correccion` lo calcula machine.py en StartAttempt: verdadero en un bloque de bug o tras una
    revisión no aprobada, y entonces el playbook es bug-fix; si no, feature. Los `references/` de
    una skill no van: su SKILL.md dice cuándo leerlos.
    """
    out: list[str] = []
    if exigencia.playbook:
        cual = "bug-fix" if correccion else "feature"
        out.append(f"pstack/skills/{SKILL_BASE}/playbooks/{cual}.md")
    out += [f"pstack/skills/{s}/SKILL.md" for s in exigencia.skills if s != SKILL_BASE]
    out.append(f"pstack/skills/{SKILL_BASE}/SKILL.md")
    if cli == "claude":
        out.append("pstack/docs/harness/claude-code.md")
    return tuple(out)
