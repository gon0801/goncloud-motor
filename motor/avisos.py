"""Del diagnóstico al aviso: qué plantilla y qué campos le tocan a cada causa, y el resumen de cada hora."""

from __future__ import annotations

from . import diagnostico as dg
from . import machine as m

NOMBRES = {
    "sonnet": "Sonnet",
    "opus": "Opus",
    "k3": "Kimi",
    "glm": "GLM",
    "grok": "Grok",
    "qwen": "Qwen",
    "deepseek": "DeepSeek",
    "muse": "Muse",
    "haiku": "Haiku",
    "fable": "Fable",
}
# El tope de Telegram es 4096; el aviso lleva además la máquina y el título del plan.
TOPE_AVISO = 4000
TOPE_AHORA, TOPE_ATORON = 2000, 1500


def _dura(seg: float) -> str:
    mi = int(seg // 60)
    if mi < 60:
        return f"{max(mi, 1)} min"
    h, mi = divmod(mi, 60)
    return f"{h} h" + (f" {mi} min" if mi else "")


def _lista(nombres) -> str:
    q = [f"la prueba «{n}»" for n in nombres]
    return q[0] if len(q) == 1 else ", ".join(q[:-1]) + " y " + q[-1]


def _recorta(texto: str, tope: int) -> str:
    return texto if len(texto) <= tope else texto[: tope - 1].rstrip() + "…"


def avisos_de(
    snap: m.Snapshot, bid: str, policy: m.Policy, now: float, d: dg.Diagnostico
) -> list[m.Notify]:
    """Los avisos que el diagnóstico pide (el motor descarta los que ya se mandaron)."""
    b = snap.blocks[bid].block
    if not d.clave:
        return []
    if d.causa is dg.Causa.ESPERA_HUMANO:
        return [m.Notify(d.clave, bid, "humano", (("que_hacer", b.human.que_hacer),))]
    if d.causa is dg.Causa.SIN_SESION:
        return [
            m.Notify(
                clave,
                "",  # la sesión es de la cuenta, no del bloque: un aviso para todo el plan
                "humano",
                (
                    (
                        "que_hacer",
                        f"volver a entrar a {NOMBRES.get(cli, cli.capitalize())}: "
                        f"abre la terminal y escribe {login}",
                    ),
                ),
            )
            for clave, cli, login, _ in dg.sesiones_caidas(snap, bid, policy, now)
        ]
    if d.causa is dg.Causa.SIN_CUPO:
        return [m.Notify(d.clave, "", "lento", (("hasta", d.clave.split(":")[-1]),))]
    if d.atorado:
        return [
            m.Notify(
                d.clave, bid, "atorado", (("bloque", b.aviso), ("motivo", d.motivo))
            )
        ]
    return []


def _resumen(
    snap: m.Snapshot, now: float, policy: m.Policy
) -> tuple[tuple[str, str], ...]:
    """El latido de cada hora: cuántas partes van, qué se está haciendo y si algo está atorado.
    Si deja de llegar, David sabe que el motor o la máquina se cayeron."""
    diag = {bid: dg.diagnostico(snap, bid, policy, now) for bid in snap.blocks}
    listas = sum(d.causa is dg.Causa.HECHO for d in diag.values())
    partes, atorados = [], []
    for bid, bv in snap.blocks.items():
        d, titulo = diag[bid], bv.block.titulo
        if d.causa in (dg.Causa.HECHO, dg.Causa.ESPERA_DEPENDENCIAS):
            continue
        if d.atorado:
            atorados.append(f"«{titulo}» ({d.motivo})")
            continue
        run = bv.running_productivo()
        asesor = next((a for a in bv.attempts if a.ended is None and a.role == "revisar"), None)
        if run:
            frase = (
                f"trabajo en «{titulo}» ({NOMBRES.get(run.modelo, run.modelo.capitalize())}, "
                f"{_dura(now - run.started)})"
            )
            falla = dg.por_que(bv)
            partes.append(frase + (f" porque {falla}" if falla else ""))
        elif asesor:
            partes.append(f"«{titulo}»: {d.motivo}. Un asesor la mira y eso no frena la entrega.")
        else:
            partes.append(f"«{titulo}»: {d.motivo}")
    ahora = (
        ("Ahora " + "; ".join(partes) + ".")
        if partes
        else "Ahora estoy entre un paso y otro."
    )
    atoron = ("Atorado: " + "; ".join(atorados) + ".") if atorados else "Nada atorado."
    return (
        ("listas", str(listas)),
        ("total", str(len(snap.blocks))),
        ("ahora", _recorta(ahora, TOPE_AHORA)),
        ("atoron", _recorta(atoron, TOPE_ATORON)),
    )
