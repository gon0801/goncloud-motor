"""Por qué un bloque está como está: la única fuente del aviso de atasco, del resumen de cada hora,
de `motor estado` y del motivo que ve el agente en su encargo.

`diagnostico(snap, bid, policy, now)` mira el bloque y contesta UNA causa. Cada causa tiene su propia
clave de aviso (nunca se comparte entre causas); `atorado` dice si es un atasco que David debe saber.
Es pura: sin red, sin reloj propio, todo sale del log plegado en `snap`.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass

from . import avisos
from . import machine as m
from .machine import Estado, Policy, Snapshot

REPETIDA_CAMPOS = (
    ("verificacion", "verified", "ok", "falló la verificación de las filas"),
    ("prueba", "proved", "ok", "falló la prueba con datos reales"),
    ("despliegue", "deployed", "ok", "falló el despliegue"),
    ("checks", "checks", "green", "las pruebas de GitHub salieron en rojo"),
)
HUECO_EFECTO_S = (
    15 * 60
)  # entre dos fallas de un mismo paso; más que esto ya es otra racha
EFECTO_ATASCO_S = 3600
LOGIN = {  # cómo se vuelve a entrar en cada CLI; "deepseek" y "meta" son cuentas, no CLIs
    "claude": "claude y luego /login",
    "kimi": "kimi y luego /login",
    "opencode": "opencode auth login",
    "grok": "grok login",
    "muse": "muse login",
    "qwen": "qwen y elige el tipo de acceso",
    "deepseek": "revisa la llave de DeepSeek en los secretos de la máquina",
}


class Causa(enum.Enum):
    HECHO = "hecho"
    ESPERA_DEPENDENCIAS = "espera_dependencias"
    ESPERA_HUMANO = "espera_humano"
    TRABAJANDO = "trabajando"
    REVISANDO = "revisando"
    PASO_DEL_MOTOR = "paso_del_motor"
    ESPERA_CHECKS = "espera_checks"
    REHACER = "rehacer"
    POR_REVISAR = "por_revisar"
    TURNO = "turno"
    PAUSA_CUENTAS = "pausa_cuentas"
    SIN_CUPO = "sin_cupo"
    SIN_SESION = "sin_sesion"
    ESCALERA_AGOTADA = "escalera_agotada"
    CHECKS_INCOHERENTE = "checks_incoherente"
    CHECKS_LENTO = "checks_lento"
    FALTA_COMANDO = "falta_comando"
    FUERA_DE_CONTROL = "fuera_de_control"
    REPETIDA = "repetida"
    EFECTO_FALLANDO = "efecto_fallando"


@dataclass(frozen=True)
class Diagnostico:
    causa: Causa
    motivo: str  # la frase para David
    clave: str = ""  # la del aviso; vacía si esta causa no avisa
    atorado: bool = False
    detalle: str = ""  # texto técnico (no va a David)


def diagnostico(snap: Snapshot, bid: str, policy: Policy, now: float) -> Diagnostico:
    policy = policy.para(snap.plan)
    bv, e = snap.blocks[bid], m.estado(snap, bid)
    if e is Estado.HECHO:
        return Diagnostico(Causa.HECHO, "terminada")
    if e is Estado.ESPERANDO:
        return Diagnostico(
            Causa.ESPERA_DEPENDENCIAS,
            "espera a que terminen las partes de las que depende",
        )
    if e is Estado.HUMANO:
        return Diagnostico(Causa.ESPERA_HUMANO, "espera tu paso", f"humano:{bid}")
    if d := _atasco(bv, e, policy, now):
        return d
    if e is Estado.IMPLEMENTANDO:
        return Diagnostico(Causa.TRABAJANDO, _toca(bv), detalle=_tecnico(bv))
    if e is Estado.REVISANDO:
        return Diagnostico(Causa.REVISANDO, "la están revisando")
    if e in (Estado.ARREGLAR, Estado.REVISAR):
        return _sin_agente(snap, bid, e, policy, now)
    if e is Estado.CHECKS:
        head = bv.head()
        faltan = (bv.esperando.get(head) or {}).get("faltan")
        desde = bv.listo_desde.get(head, now)
        que = (
            avisos._lista(faltan) + " de GitHub" if faltan else "las pruebas de GitHub"
        )
        return Diagnostico(
            Causa.ESPERA_CHECKS, f"espero {que} desde hace {avisos._dura(now - desde)}"
        )
    return Diagnostico(
        Causa.PASO_DEL_MOTOR,
        {
            Estado.VERIFICAR: "la verifico",
            Estado.PR: "abro su propuesta de cambio en GitHub",
            Estado.LISTO: "la marco lista en GitHub",
            Estado.MERGE: "la integro",
            Estado.DESPLEGAR: "la despliego a producción",
            Estado.PROBAR: "la pruebo con datos reales",
        }.get(e, e.value),
    )


def _atasco(
    bv: m.BlockView, e: Estado, policy: Policy, now: float
) -> Diagnostico | None:
    """Atascos que no dependen de un agente. Se avisa una vez por causa y el motor sigue intentando:
    - GitHub ya terminó todas sus pruebas y falta una requerida (INGESTA 01 esperó horas así);
    - pruebas que no terminan nunca (un nombre que no existe);
    - la misma falla varias veces seguidas (la prueba real de una sonda que solo corre de noche);
    - un paso del motor que lleva una hora fallando (red, GitHub, disco)."""
    bid, head = bv.block.id, bv.head()
    if e is Estado.CHECKS:
        esp = bv.esperando.get(head) or {}
        if (
            esp.get("github_termino")
            and esp.get("faltan")
            and now - esp["desde"] > policy.aviso_incoherente_s
        ):
            return Diagnostico(
                Causa.CHECKS_INCOHERENTE,
                f"GitHub ya terminó sus pruebas, pero sigo esperando {avisos._lista(esp['faltan'])}, "
                f"que no llegó a correr ({avisos._dura(now - esp['desde'])})",
                f"atorado:{bid}:incoherente:{head[:12]}",
                True,
                f"faltan={list(esp['faltan'])}",
            )
        desde = bv.listo_desde.get(head)
        if desde is not None and now - desde > policy.aviso_lento_s:
            return Diagnostico(
                Causa.CHECKS_LENTO,
                f"las pruebas de GitHub llevan {avisos._dura(now - desde)} sin terminar",
                f"atorado:{bid}:checks:{head[:12]}",
                True,
            )
    falta = (bv.verified.get(head) or {}).get("falta_comando") if head else None
    if falta:
        # El agente no puede arreglar la máquina ni la Acepta del plan: rebotaría sin fin.
        # Un aviso por comando, no uno por ronda ni otro cuando se suma un comando nuevo.
        prefijo = f"atorado:{bid}:comando:"
        avisados = {
            c
            for k in bv.notices
            if k.startswith(prefijo)
            for c in k[len(prefijo) :].split(",")
        }
        nuevos = [c for c in falta if c not in avisados]
        clave = (
            prefijo + ",".join(nuevos)
            if nuevos
            else min(k for k in bv.notices if k.startswith(prefijo))
        )
        return Diagnostico(
            Causa.FALTA_COMANDO,
            f"la verificación del plan usa {', '.join(falta)}, que esta máquina no tiene; "
            "hay que corregir el plan o instalarlo en la máquina",
            clave,
            True,
            f"faltan={list(falta)}",
        )
    for tipo, campo, ok, que in REPETIDA_CAMPOS:
        d = getattr(bv, campo)
        n = _seguidas(d, ok)
        # Solo mientras no haya un arreglo nuevo: con otra entrega en camino ya no es atasco.
        if n >= policy.repeticiones_max and list(d)[-1] == head:
            antes = (
                len(d) - n
            )  # fija la clave mientras dura la racha: un aviso por racha
            return Diagnostico(
                Causa.REPETIDA,
                f"{que}, {n} veces seguidas",
                f"atorado:{bid}:repite:{tipo}:{antes}",
                True,
                str(list(d.values())[-1].get("detalle", ""))[:300],
            )
    for accion, (desde, ultima) in sorted(bv.rachas.items()):
        if now - ultima < HUECO_EFECTO_S and ultima - desde >= EFECTO_ATASCO_S:
            return Diagnostico(
                Causa.EFECTO_FALLANDO,
                "un paso del motor lleva una hora fallando (red o GitHub); sigo reintentando",
                f"atorado:{bid}:efecto:{accion}:{int(desde)}",
                True,
                f"{accion} falla desde hace {avisos._dura(now - desde)}",
            )
    return None


def _seguidas(d: dict[str, dict], campo: str) -> int:
    n = 0
    for v in reversed(list(d.values())):
        if v.get(campo):
            break
        n += 1
    return n


def _sin_agente(
    snap: Snapshot, bid: str, e: Estado, policy: Policy, now: float
) -> Diagnostico:
    """Bloque que toca a un agente. Con alguien libre es el turno del agente (o esperar el suyo); si
    nadie puede, la causa es lo que tienen en común: sin sesión, sin cupo, una pausa o la escalera gastada."""
    bv = snap.blocks[bid]
    role = "implementar" if e is Estado.ARREGLAR else "revisar"
    ladder = (
        policy.implementadores
        if role == "implementar"
        else m.revisores_para(bv, policy)
    )
    est = {mo: m.bloqueo(snap, policy, now, bv, role, mo, ladder) for mo in ladder}
    libres = [mo for mo, (c, _) in est.items() if not c]
    toca = _toca(bv) if role == "implementar" else "toca revisarla"
    causa = Causa.REHACER if role == "implementar" else Causa.POR_REVISAR
    if libres:
        ocupados = m._ocupados(snap, policy)
        lleno = bool(policy.agentes_por_maquina) and (
            sum(ocupados.values()) >= policy.agentes_por_maquina
        )
        if lleno or all(
            ocupados.get(policy.cuenta.get(mo, mo), 0)
            >= policy.max_parallel.get(policy.cuenta.get(mo, mo), 1)
            for mo in libres
        ):
            return Diagnostico(
                Causa.TURNO, f"{toca}; espera su turno, los agentes están ocupados"
            )
        return Diagnostico(causa, toca, detalle=_tecnico(bv))
    if all(c == "fuera" for c, _ in est.values()):
        tipo, que = _fuera(bv)
        return Diagnostico(
            Causa.FUERA_DE_CONTROL,
            f"{que}, y el agente ya intentó sin poder cambiar nada; espero un resultado nuevo o tu nota",
            f"atorado:{bid}:fuera:{tipo}:{(bv.merged_sha() or bv.head())[:12]}",
            True,
            f"{tipo}; el último intento terminó en sin_cambios",
        )
    vivos = {mo: v for mo, v in est.items() if v[0] != "agotado"}
    if not vivos:
        return Diagnostico(
            Causa.ESCALERA_AGOTADA,
            "ningún agente disponible pudo tomarla; todos agotaron sus intentos",
            f"atorado:{bid}:agotada:{len([a for a in bv.attempts if a.role == role])}",
            True,
            f"escalera={list(ladder)}",
        )
    if all(c == "sesion" for c, _ in vivos.values()):
        pendientes = sesiones_caidas(snap, bid, policy, now)
        faltan = ", ".join(f"{cli} ({login})" for _, cli, login, _ in pendientes)
        return Diagnostico(
            Causa.SIN_SESION,
            f"falta iniciar sesión: {faltan}",
            pendientes[0][0],
            True,
            f"cuentas={[p[3] for p in pendientes]}",
        )
    if all(c == "cupo" for c, _ in vivos.values()):
        primero = min(h for _, h in vivos.values())
        return Diagnostico(
            Causa.SIN_CUPO,
            f"espera a que se renueve el cupo de los agentes ({avisos._dura(primero - now)})",
            f"lento:{int(primero)}",
            False,
            f"vuelven a las {int(primero)}",
        )
    return Diagnostico(
        Causa.PAUSA_CUENTAS, f"{toca}; espera un momento a que vuelva un agente"
    )


def _fuera(bv: m.BlockView) -> tuple[str, str]:
    mg = bv.merged_sha()
    if (bv.verified.get(bv.head()) or {}).get("falta_comando"):
        return "comando", "falta un comando que pide la verificación del plan"
    if mg in bv.deployed and not bv.deployed[mg]["ok"]:
        return "despliegue", "falló el despliegue"
    return "prueba", "falló la prueba con datos reales"


def sesiones_caidas(
    snap: Snapshot, bid: str, policy: Policy, now: float
) -> list[tuple[str, str, str, str]]:
    """(clave, CLI, login, cuenta) por cada cuenta de la escalera sin sesión: un aviso por cuenta y
    por racha (la racha arranca con la primera falla de sesión desde el último éxito)."""
    bv = snap.blocks[bid]
    policy = policy.para(snap.plan)
    e = m.estado(snap, bid)
    role = "implementar" if e is Estado.ARREGLAR else "revisar"
    ladder = (
        policy.implementadores
        if role == "implementar"
        else m.revisores_para(bv, policy)
    )
    out, vistas = [], set()
    for mo in ladder:
        cuenta = policy.cuenta.get(mo, mo)
        c, racha = m.bloqueo(snap, policy, now, bv, role, mo, ladder)
        if c != "sesion" or cuenta in vistas:
            continue
        vistas.add(cuenta)
        cli = policy.cli.get(mo, cuenta)
        login = LOGIN.get(cuenta) or LOGIN.get(cli) or f"vuelve a entrar en {cli}"
        out.append((f"humano:auth:{cuenta}:{int(racha)}", cli, login, cuenta))
    return out


def _toca(bv: m.BlockView) -> str:
    falla = por_que(bv)
    return f"toca arreglarla porque {falla}" if falla else "toca hacerla"


def por_que(bv: m.BlockView) -> str:
    """Por qué el bloque volvió a un agente, en palabras para David (sin rutas ni detalles técnicos:
    el detalle completo va en el encargo del agente y en `motor estado`)."""
    head, mg = bv.head(), bv.merged_sha()
    if mg and mg == head:
        if mg in bv.proved and not bv.proved[mg]["ok"]:
            return "falló la prueba con datos reales"
        if mg in bv.deployed and not bv.deployed[mg]["ok"]:
            return "falló el despliegue y se revirtió"
    c = bv.checks.get(head)
    if c and not c["green"]:
        if str(c.get("detalle", "")).startswith("el merge falló"):
            return "GitHub no dejó integrarlo; hay que traer lo último de la rama principal"
        return "las pruebas automáticas de GitHub salieron en rojo"
    r = bv.review_for(head) if head else None
    if r and not m._aprobado(r):
        return "el revisor pidió cambios"
    v = bv.verified.get(head) if head else None
    if v and not v["ok"]:
        return "falló la verificación de las filas"
    last = bv.last_implement_outcome()
    if last and last.get("kind") == "corte":
        return "el intento anterior se cortó por un reinicio o apagón"
    if last and last.get("kind") != "entregado":
        return "el intento anterior terminó sin entregar"
    return ""


def _tecnico(bv: m.BlockView) -> str:
    return "\n".join(_lineas(bv))


def _lineas(bv: m.BlockView) -> list[str]:
    """Lo que el agente necesita saber para arreglar, un punto por hallazgo (con detalles técnicos)."""
    out: list[str] = []
    last = bv.last_implement_outcome()
    if last and last.get("kind") != "entregado":
        motivo = (
            "un corte de luz o reinicio"
            if last.get("kind") == "corte"
            else last.get("kind")
        )
        out.append(
            f"El intento anterior terminó por: {motivo} ({str(last.get('detalle', ''))[:300]}). "
            "Sigue desde lo que haya en la rama y lo que quedó sin commitear en esta carpeta."
        )
    head = bv.head()
    if head:
        v = bv.verified.get(head)
        if v and not v["ok"]:
            out.append(f"La verificación falló: {v.get('detalle', '')[:800]}")
        r = bv.review_for(head)
        if r and not m._aprobado(r):
            for blq in m._bloqueantes(r):
                out.append(
                    f"BLOQUEANTE: {blq['texto']} | repro: {blq['repro']}"
                    + (f" | arreglo: {blq['arreglo']}" if blq.get("arreglo") else "")
                )
        c = bv.checks.get(head)
        if c and not c["green"]:
            out.append(f"Pruebas automáticas en rojo: {c.get('detalle', '')[:800]}")
        mg = bv.merged_sha()
        if mg and mg == head:
            if mg in bv.deployed and not bv.deployed[mg]["ok"]:
                out.append(
                    f"El despliegue falló y se revirtió: {bv.deployed[mg].get('detalle', '')[:800]}"
                )
            if mg in bv.proved and not bv.proved[mg]["ok"]:
                out.append(
                    f"La prueba con datos reales falló: {bv.proved[mg].get('detalle', '')[:800]}"
                )
    return out


def encargo(snap: Snapshot, bid: str, policy: Policy, now: float) -> tuple[str, ...]:
    """Lo que se le dice al agente del intento siguiente: el motivo del diagnóstico, el detalle técnico,
    las notas del revisor que no bloquean y las del lead."""
    bv = snap.blocks[bid]
    tecnico = _lineas(bv)
    notas = [
        f"Nota del revisor (no bloquea): {n}"
        for r in (
            [bv.review_for(bv.head())] if bv.head() and bv.review_for(bv.head()) else []
        )
        for n in r.get("notas", [])
    ]
    out: list[str] = []
    if tecnico or bv.attempts:
        out.append(
            f"Por qué vuelve a ti: {diagnostico(snap, bid, policy, now).motivo}."
        )
    out.extend(tecnico)
    out.extend(notas)
    out.extend(f"Nota del lead: {n}" for n in bv.notes)
    return tuple(out)
