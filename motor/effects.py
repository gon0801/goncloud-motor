"""Un efecto por acción, idempotente: antes de actuar mira si el efecto ya existe.

Separado por conocimiento: `Workspace` sabe de git local, `GitHubPort` de PRs y checks,
`plataforma` de procesos, `Notifier` de Telegram. Aquí solo se orquestan.
"""

from __future__ import annotations

import json
import os
import shutil
import string
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

from . import machine as m
from .plataforma.windows import argv_en_wsl
from . import plataforma, relevo
from .conf import Conf, Maquina, RepoConf
from .fallas import PROVEEDORES
from .github import GitHubPort, MergeRechazado
from .notify import Notifier, render
from .plan import HUMAN_KINDS, Plan
from .store import Store
from .workspace import Workspace, git

RAIZ = Path(__file__).resolve().parent.parent


@dataclass
class Contexto:
    home: Path
    maquina: Maquina
    conf: Conf
    policy: m.Policy
    store: Store
    ws: Workspace
    gh: GitHubPort
    notifier: Notifier
    checks_cada_s: float = 60.0


RECETA = (
    "El motor corre cada script de la receta con `bash <script>`, sin argumentos, desde una copia "
    "del commit mergeado; MOTOR_SHA trae ese commit."
)


def _cola(p: subprocess.CompletedProcess) -> str:
    return (p.stdout + p.stderr)[-500:].strip()


def _run_tope(
    cmd: str, cwd: Path | None, env: dict, timeout: float
) -> subprocess.CompletedProcess:
    """Como _run, pero pasarse del tope es una salida 124 (falla), no una excepción: MOTOR 01, bloque I,
    la revisión del despliegue se pasaba de 15 min y el motor la reintentaba para siempre."""
    try:
        return _run(cmd, cwd, env, timeout)
    except subprocess.TimeoutExpired:
        return subprocess.CompletedProcess(
            cmd, 124, "", f"se pasó del tope de {int(timeout)} s"
        )


def _run(
    cmd: str, cwd: Path | None, env: dict, timeout: float = 900
) -> subprocess.CompletedProcess:
    """Corre `cmd` en su propio grupo; al pasarse del tope mata el grupo entero, no solo el shell.
    Shopify-token-01: el tope cortaba `bash desplegar.sh` y el `crontab -` que había abierto se
    quedaba colgado en la Mini, esperando un permiso que nadie contestaba."""
    p = subprocess.Popen(
        cmd,
        cwd=cwd,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        errors="replace",
        stdin=subprocess.DEVNULL,
        shell=True,
        executable="/bin/bash" if os.name != "nt" else None,
        **plataforma.kwargs_grupo(),
    )
    try:
        out, err = p.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        plataforma.matar_grupo(p)
        p.communicate()
        raise
    return subprocess.CompletedProcess(cmd, p.returncode, out, err)


def es_del_agente(motivo: str) -> bool:
    """Un merge rechazado es tarea del agente solo si su rama choca con la principal o se movió."""
    m_ = motivo.lower()
    return any(
        x in m_
        for x in (
            "cannot be cleanly created",
            "conflict",
            "does not match",
            "head commit",
        )
    )


SONDA_CADA_S = 120
SONDA_TOPE_S = 900


class Effects:
    def __init__(self, cx: Contexto):
        self.cx = cx
        self._ultimo_poll: dict[str, float] = {}
        self._sondas: dict[
            str, tuple[subprocess.Popen, float]
        ] = {}  # sonda humana corriendo aparte

    # ------------------------------------------------------------------ entorno
    def env_base(self, extra: dict | None = None) -> dict:
        mq = self.cx.maquina
        env = {
            **os.environ,
            **dict(mq.env),
            **dict(mq.secretos),
            "MOTOR_RAIZ": str(RAIZ),
            **(extra or {}),
        }
        if mq.path_extra:
            env["PATH"] = os.pathsep.join([*mq.path_extra, env.get("PATH", "")])
        return env

    def repo_conf(self, snap: m.Snapshot, repo: str) -> RepoConf:
        return RepoConf.parse(snap.repos.get(repo, {}).get("texto", ""))

    def conf_base(self, repo: str) -> tuple[str, str]:
        """(sha, texto) del `.motor.toml` de la rama principal del repo; si no tiene, uno mínimo."""
        d = self.cx.ws.espejo(repo)
        base = (
            git("symbolic-ref", "--short", "refs/remotes/origin/HEAD", cwd=d)
            .stdout.strip()
            .removeprefix("origin/")
            or "main"
        )
        sha, texto = self.cx.ws.leer_en_base(repo, base, ".motor.toml")
        principal = RepoConf.parse(texto).principal if texto else base
        if principal != base:
            sha, texto = self.cx.ws.leer_en_base(repo, principal, ".motor.toml")
        if not texto:
            texto = f'[repo]\nprincipal = "{principal}"\n'
        return sha, texto

    def pin_repos(self, plan: Plan) -> None:
        """Lee `.motor.toml` de la rama base de cada repo UNA vez y lo fija en el log."""
        for repo in dict.fromkeys(b.repo for b in plan.blocks):
            if any(
                e.kind == "RepoPinned" and e.data["repo"] == repo
                for e in self.cx.store.events(plan.id)
            ):
                continue
            sha, texto = self.conf_base(repo)
            self.cx.store.append(
                plan.id,
                "",
                "RepoPinned",
                {
                    "repo": repo,
                    "sha": sha,
                    "texto": texto,
                    "privado": self.cx.gh.es_privado(repo),
                },
                ukey=f"pin:{plan.id}:{repo}",
            )

    def attempt_dir(self, plan: str, block: str, role: str, n: int) -> Path:
        return self.cx.home / "intentos" / plan / f"{block}-{role}-{n}"

    # ------------------------------------------------------------------ intentos
    def start_attempt(self, plan: Plan, snap: m.Snapshot, a: m.StartAttempt) -> None:
        d = self.attempt_dir(plan.id, a.block, a.role, a.n)
        if (d / "started").exists():
            # se lanzó y el daemon cayó antes de anotar: no se duplica; se anota con la marca, así
            # cuenta para los topes y su resultado se recoge (revisión de Grok a MOTOR 01)
            datos = json.loads((d / "started").read_text(encoding="utf-8"))
            if "n" in datos:
                self.cx.store.append(
                    plan.id,
                    a.block,
                    "AttemptStarted",
                    datos,
                    ukey=f"start:{plan.id}:{a.block}:{a.role}:{a.n}",
                )
            return
        d.mkdir(parents=True, exist_ok=True)
        block = plan.block(a.block)
        rc = self.repo_conf(snap, block.repo)
        ruta = self.cx.conf.rutas[a.modelo]
        if a.role == "implementar":
            cwd = self.cx.ws.prepara(block.repo, plan.id, block.id, rc.principal)
            brief = self._brief_implementar(plan, block, a, cwd, rc)
            wip = [
                "--wip-rama",
                self.cx.ws.rama_wip(plan.id, block.id),
                "--wip-cada",
                str(self.cx.policy.wip_cada_s),
            ]
            if not snap.repos.get(block.repo, {}).get("privado", False):
                wip.append("--wip-local")  # repo público: lo de a medias no se publica
        else:
            cwd = self.cx.ws.copia(block.repo, a.sha, d / "revision")
            brief = self._brief_revisar(plan, block, a, rc)
            wip = []
        (d / "brief.md").write_text(brief, encoding="utf-8")
        result = d / "result.json"
        prompt = f"Lee {d / 'brief.md'} y ejecútalo completo sin preguntar nada."
        argv = [
            x.format(brief=str(d / "brief.md"), prompt=prompt, result=str(result))
            for x in ruta.argv
        ]
        if (
            ruta.cli in self.cx.maquina.wsl
        ):  # Muse en la PC: dentro de WSL, con su token pasado por WSLENV
            argv = argv_en_wsl(argv)
        base = self.env_base()
        de_ruta = {k: string.Template(v).safe_substitute(base) for k, v in ruta.env}
        env = self.env_base(
            {
                **de_ruta,
                "MOTOR_RESULT": str(result),
                "MOTOR_MODEL": a.modelo,
                "MOTOR_BLOCK": a.block,
                "MOTOR_ROLE": a.role,
                "WSLENV": "META_API_KEY/u:MOTOR_RESULT/p",
                "PYTHONPATH": str(RAIZ),
            }
        )
        # el `gh` de los agentes no mergea, ni cierra, ni marca listo: eso solo lo hace el motor
        env["PATH"] = os.pathsep.join(
            [str(RAIZ / "motor" / "shims"), env.get("PATH", "")]
        )
        cmd = [
            sys.executable,
            "-m",
            "motor.runwrap",
            "--dir",
            str(d),
            "--cwd",
            str(cwd),
            "--timeout",
            str(self.cx.policy.timeout_s),
            "--sin-avance",
            str(self.cx.policy.sin_avance_s),
            *wip,
            "--",
            *argv,
        ]
        proc = plataforma.lanzar_separado(cmd, cwd, env, d / "runwrap.log")
        datos = {
            "n": a.n,
            "role": a.role,
            "modelo": a.modelo,
            "cuenta": ruta.cuenta,
            "proc": proc,
            "dir": str(d),
            "sha": a.sha,
            "conf_sha": self.cx.conf.sha,
            "feedback": list(a.feedback),
        }
        (d / "started").write_text(json.dumps(datos), encoding="utf-8")
        self.cx.store.append(
            plan.id,
            a.block,
            "AttemptStarted",
            datos,
            ukey=f"start:{plan.id}:{a.block}:{a.role}:{a.n}",
        )

    def _brief_implementar(
        self, plan: Plan, block, a: m.StartAttempt, cwd: Path, rc: RepoConf
    ) -> str:
        filas = "\n".join(
            f"- {r.id}: {r.contenido}"
            + (f"\n  Acepta: `{r.acepta}`" if r.acepta else "")
            for r in block.rows
        )
        fb = "\n".join(f"- {x}" for x in a.feedback) or "- (primer intento)"
        rama = self.cx.ws.rama(plan.id, block.id)
        pendientes = git("status", "--short", cwd=cwd).stdout.strip()
        return f"""{self.cx.conf.reglas}
# Encargo {plan.id} · bloque {block.id} · intento {a.n}

Trabajas solo en esta carpeta, en la rama `{rama}`. Nadie lee tu pantalla: no preguntes nada.
Si algo no te deja seguir, intenta otra forma; si de verdad solo una persona puede hacerlo
({", ".join(HUMAN_KINDS)}), dilo en el resultado.

## Qué entregar
{block.titulo}

{filas}

## Qué pasó antes (corrígelo primero)
{fb}

## Lo que quedó sin commitear de un intento anterior
{pendientes or "(nada)"}

## Cómo se entrega
1. Un commit por fila, con el id de la fila al inicio del mensaje. Nunca `--no-verify`.
2. `git push origin HEAD:refs/heads/{rama}` (nunca a `{rc.principal}`).
   PROHIBIDO mergear, cerrar, aprobar o marcar listo un PR (`gh pr merge`, la API, la web): eso
   solo lo hace el motor. Si te dicen que el merge falló por conflicto, trae `{rc.principal}` a tu
   rama (merge o rebase), resuelve y vuelve a entregar.
3. Escribe en `$MOTOR_RESULT` un JSON: {{"sha": "<git rev-parse HEAD>", "resumen": "..."}}.
   Lo que digas no cuenta como prueba: el motor verifica el SHA y corre cada `Acepta`.
"""

    def _brief_revisar(self, plan: Plan, block, a: m.StartAttempt, rc: RepoConf) -> str:
        filas = "\n".join(
            f"- {r.id}: {r.contenido}"
            + (f" | Acepta: `{r.acepta}`" if r.acepta else "")
            for r in block.rows
        )
        return f"""{self.cx.conf.reglas_revisor}
# Revisión {plan.id} · bloque {block.id} · SHA {a.sha}

Estás en una copia de solo lectura de ese SHA (diferencia contra `{rc.principal}`). Revisa contra lo pedido:

{filas}

Un hallazgo bloquea solo si trae el comando que lo reproduce (`repro`). Lo demás va en `notas`.
Escribe en `$MOTOR_RESULT`:
{{"sha": "{a.sha}", "veredicto": "APROBADO" | "CAMBIOS",
  "bloqueantes": [{{"texto": "...", "repro": "comando", "arreglo": "..."}}], "notas": ["..."]}}
"""

    def _proveedor(self, modelo: str) -> str:
        """Quién habla: la cuenta si es un proveedor conocido (DeepSeek usa el CLI de Claude), si no el CLI."""
        r = self.cx.conf.rutas.get(modelo)
        if r is None:
            return ""
        return r.cuenta if r.cuenta in PROVEEDORES else r.cli

    def collect(
        self, plan: Plan, snap: m.Snapshot, block_id: str, att: m.Attempt
    ) -> dict | None:
        d = Path(att.dir)
        oc = d / "outcome.json"
        if not oc.exists():
            st = plataforma.estado_proc(att.proc)
            if st == "vivo":
                return None
            if st == "corte":
                return {"kind": "corte", "detalle": "la máquina se apagó o reinició"}
            return {
                "kind": "perdido",
                "detalle": "el proceso desapareció sin dejar resultado",
            }
        o = json.loads(oc.read_text(encoding="utf-8"))
        c = m.classify(
            o["exit"],
            o["timed_out"],
            o["no_progress"],
            o["tail"],
            self._proveedor(att.modelo),
        )
        if c["kind"] in ("cuota", "rate_limit"):
            if c["kind"] == "cuota":
                hasta = (
                    _iso_a_epoch(c.get("reset_at"))
                    or time.time() + self.cx.policy.cooldown_default_s
                )
            else:  # freno corto: la cuenta vuelve en minutos, no en la ventana de cuota (relevo.py)
                hasta = time.time() + relevo.pausa_cuenta_s(
                    relevo.Falla.RATE_LIMIT, c.get("retry_after_s")
                )
            cuenta = self.cx.policy.cuenta.get(att.modelo, att.modelo)
            self.cx.store.append(
                plan.id,
                "",
                "Cooldown",
                {"cuenta": cuenta, "hasta": hasta, "modelo": att.modelo},
                ukey=f"cooldown:{att.dir}",
            )
            return c
        if c["kind"] != "terminado":
            return c
        try:
            res = json.loads((d / "result.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {
                "kind": "sin_resultado",
                "detalle": "terminó sin escribir el resultado",
            }
        block = plan.block(block_id)
        if att.role == "revisar":
            if res.get("sha") != att.sha or res.get("veredicto") not in (
                "APROBADO",
                "CAMBIOS",
            ):
                return {
                    "kind": "veredicto_invalido",
                    "detalle": "el veredicto no cita el SHA revisado",
                }
            self.cx.store.append(
                plan.id,
                block_id,
                "Reviewed",
                {
                    "sha": att.sha,
                    "veredicto": res["veredicto"],
                    "modelo": att.modelo,
                    "bloqueantes": res.get("bloqueantes", []),
                    "notas": res.get("notas", []),
                },
                ukey=f"review:{att.dir}",
            )
            return {"kind": "veredicto"}
        sha = str(res.get("sha", ""))
        punta = self.cx.ws.punta(block.repo, self.cx.ws.rama(plan.id, block_id))
        if not sha or len(sha) < 7 or not punta.startswith(sha):
            return {
                "kind": "mintio",
                "detalle": f"dijo haber entregado {sha[:12]!r} y no es la punta de su rama",
            }
        if att.sha and punta == att.sha:
            return {
                "kind": "sin_cambios",
                "detalle": "volvió a entregar lo mismo que ya había fallado",
            }
        return {
            "kind": "entregado",
            "sha": punta,
            "resumen": str(res.get("resumen", ""))[:500],
        }

    # ------------------------------------------------------------------ pasos
    def verify(self, plan: Plan, snap: m.Snapshot, a: m.Verify) -> None:
        block = plan.block(a.block)
        rc = self.repo_conf(snap, block.repo)
        w = self.cx.ws.copia(
            block.repo, a.sha, self.cx.home / "tmp" / f"verifica-{plan.id}-{a.block}"
        )
        fallas = []
        # Pasarse del tope es una falla de la fila con su motivo, no un tropiezo que se reintenta para
        # siempre (PSTACK 01 en la PC: la batería e2e tardaba más de 15 min y se reintentó horas).
        tope = float(self.cx.conf.tiempos.get("verifica_tope_s", 1800))
        for r in block.rows:
            if r.acepta:
                p = _run_tope(r.acepta, w, self.env_base(dict(rc.prueba_env)), tope)
                if p.returncode != 0:
                    fallas.append(
                        f"{r.id}: `{r.acepta}` salió {p.returncode}: {(p.stdout + p.stderr)[-300:]}"
                    )
        self.cx.store.append(
            plan.id,
            a.block,
            "Verified",
            {"sha": a.sha, "ok": not fallas, "detalle": "\n".join(fallas)},
            ukey=f"verified:{plan.id}:{a.block}:{a.sha}",
        )

    def open_pr(self, plan: Plan, snap: m.Snapshot, a: m.OpenPr) -> None:
        block = plan.block(a.block)
        rc = self.repo_conf(snap, block.repo)
        d = self.cx.ws.espejo(block.repo)
        if (
            git(
                "merge-base", "--is-ancestor", a.sha, f"origin/{rc.principal}", cwd=d
            ).returncode
            == 0
        ):
            # Lo entregado ya está en la rama principal (otro plan lo mergeó antes; MOTOR 01, canario en
            # la Mini): no hay nada que mergear y GitHub no deja abrir un PR vacío. Se da por mergeado
            # y sigue a la prueba real, que es la que dice si de verdad funciona.
            base = git("rev-parse", f"origin/{rc.principal}", cwd=d).stdout.strip()
            self.cx.store.append(
                plan.id,
                a.block,
                "Merged",
                {"sha": a.sha, "commit": base, "ya_en_base": True},
                ukey=f"merged:{plan.id}:{a.block}:{a.sha}",
            )
            return
        pr = self.cx.gh.abrir_pr(
            block.repo,
            self.cx.ws.rama(plan.id, a.block),
            rc.principal,
            f"{plan.id} {a.block}: {block.titulo}",
            f"Plan {plan.id}, bloque {a.block}. Lo lleva el motor de la {self.cx.maquina.nombre}.",
        )
        self.cx.store.append(
            plan.id,
            a.block,
            "PrOpened",
            {"numero": pr.numero, "rama": pr.rama},
            ukey=f"pr:{plan.id}:{a.block}:{pr.numero}",  # tras un merge, el arreglo abre otro PR
        )

    def mark_ready(self, plan: Plan, snap: m.Snapshot, a: m.MarkReady) -> None:
        bv = snap.blocks[a.block]
        self.cx.gh.marcar_listo(plan.block(a.block).repo, int(bv.pr["numero"]))
        self.cx.store.append(
            plan.id,
            a.block,
            "MarkedReady",
            {"sha": a.sha},
            ukey=f"ready:{plan.id}:{a.block}:{a.sha}",
        )

    def _ya_mergeado(
        self, plan: Plan, snap: m.Snapshot, block_id: str, sha: str
    ) -> bool:
        """Si el PR ya está mergeado (alguien lo hizo fuera del motor), se adopta ese merge para que
        el plan siga, se anota quién no debió hacerlo y no se vuelve a esperar pruebas."""
        block = plan.block(block_id)
        numero = int(snap.blocks[block_id].pr["numero"])
        v = self.cx.gh.ver(block.repo, numero)
        if v.estado == "abierto":
            return False
        if v.estado != "mergeado" or v.head != sha:
            # cerrado, o mergeado con OTRO contenido (MOTOR 01, bloque F: se adoptaba el merge viejo y
            # se desplegaba otra vez el código con el error): ese PR ya no sirve, se abre uno nuevo
            self.cx.store.append(
                plan.id,
                block_id,
                "PrCerrado",
                {"numero": numero, "estado": v.estado},
                ukey=f"prcerrado:{plan.id}:{block_id}:{numero}",
            )
            return True
        self.cx.store.append(
            plan.id,
            block_id,
            "Merged",
            {"sha": sha, "commit": v.merge_commit, "externo": True},
            ukey=f"merged:{plan.id}:{block_id}:{sha}",
        )
        return True

    def poll_checks(self, plan: Plan, snap: m.Snapshot, a: m.PollChecks) -> None:
        k = f"{plan.id}:{a.block}:{a.sha}"
        if time.time() - self._ultimo_poll.get(k, 0) < self.cx.checks_cada_s:
            return
        self._ultimo_poll[k] = time.time()
        if self._ya_mergeado(plan, snap, a.block, a.sha):
            return
        block = plan.block(a.block)
        rc = self.repo_conf(snap, block.repo)
        v = self.cx.gh.checks(
            block.repo,
            int(snap.blocks[a.block].pr["numero"]),
            a.sha,
            tuple(dict.fromkeys([*rc.checks_extra, *block.checks])),
        )
        if not v.terminal:
            # Qué falta y si GitHub ya terminó: se anota solo cuando cambia (los sondeos no ensucian
            # el log). Con esto el aviso dice qué prueba se espera, y el motor nota la incoherencia
            # de esperar algo que GitHub ya no va a correr (INGESTA 01 esperó horas en silencio).
            self.cx.store.append(
                plan.id,
                a.block,
                "ChecksEsperando",
                {
                    "sha": a.sha,
                    "faltan": list(v.faltan),
                    "github_termino": v.github_termino,
                },
                ukey=f"espera:{k}:{','.join(v.faltan)}:{v.github_termino}",
            )
        if (
            v.terminal
        ):  # solo se anota el resultado final: los sondeos no ensucian el log
            self.cx.store.append(
                plan.id,
                a.block,
                "ChecksResult",
                {
                    "sha": a.sha,
                    "green": v.green,
                    "detalle": f"fallaron: {list(v.fallas)}" if v.fallas else "",
                },
                ukey=f"checks:{k}",
            )

    def merge(self, plan: Plan, snap: m.Snapshot, a: m.Merge) -> None:
        t = a.ticket
        if not isinstance(t, m.MergeTicket):
            raise TypeError("merge sin MergeTicket")
        block = plan.block(t.block)
        rc = self.repo_conf(snap, block.repo)
        if self._ya_mergeado(plan, snap, t.block, t.sha):
            return
        try:
            pr = self.cx.gh.merge(
                block.repo,
                t.pr,
                t.sha,
                rc.merge,
                f"{plan.id} {t.block}: {block.titulo}",
            )
        except MergeRechazado as e:
            if "head branch was modified" in str(e).lower():
                if self.cx.gh.ver(block.repo, t.pr).head == t.sha:
                    # GitHub todavía asienta el PR recién abierto (MOTOR 01, PR #9): misma punta, se reintenta
                    raise RuntimeError(
                        f"GitHub pidió reintentar el merge: {str(e)[:200]}"
                    ) from None
            elif not es_del_agente(str(e)):
                # GitHub todavía no lo deja (pruebas requeridas pendientes, política de la rama): no es
                # culpa del agente. Se reintenta en 5 minutos y, si dura una hora, se avisa.
                raise RuntimeError(
                    f"GitHub aún no deja mergear: {str(e)[:300]}"
                ) from None
            self.cx.store.append(
                plan.id,
                t.block,
                "ChecksResult",
                {
                    "sha": t.sha,
                    "green": False,
                    "detalle": f"el merge falló por conflicto con {rc.principal}: trae {rc.principal} a tu rama, "
                    f"resuelve y vuelve a entregar (no mergees tú). GitHub dijo: {str(e)[:300]}",
                },
                ukey=f"rechazo:{plan.id}:{t.block}:{t.sha}:{int(time.time())}",
            )
            return
        self.cx.store.append(
            plan.id,
            t.block,
            "Merged",
            {"sha": t.sha, "commit": pr.merge_commit},
            ukey=f"merged:{plan.id}:{t.block}:{t.sha}",
        )

    def _receta(self, snap: m.Snapshot, block) -> tuple[str, str, str]:
        if block.release:
            return (
                block.release.desplegar,
                block.release.checklist,
                block.release.rollback,
            )
        rc = self.repo_conf(snap, block.repo)
        return rc.desplegar, rc.checklist, rc.rollback

    def deploy(self, plan: Plan, snap: m.Snapshot, a: m.Deploy) -> None:
        bv = snap.blocks[a.block]
        block = bv.block
        rc = self.repo_conf(snap, block.repo)
        desplegar, checklist, rollback = self._receta(snap, block)
        commit = bv.merged[-1]["commit"]
        w = self.cx.ws.copia(
            block.repo, commit, self.cx.home / "tmp" / f"despliega-{plan.id}-{a.block}"
        )
        env = self.env_base(dict(rc.prueba_env))
        # La instalación no tiene .git; la copia (worktree) sí. Sin esto,
        # instala-todo.sh hace `git -C "$MOTOR_RAIZ"` y sale 3.
        env["MOTOR_RAIZ"] = str(w)
        env["MOTOR_SHA"] = commit
        tope = float(self.cx.conf.tiempos.get("despliegue_tope_s", 900))
        if not desplegar:
            ok, detalle = False, "el bloque es de despliegue y el repo no trae receta"
        elif (
            a.sha in bv.deploy_started
            and _run_tope(f"bash {checklist}", w, env, tope).returncode == 0
        ):
            ok, detalle = True, "ya estaba desplegado (se comprobó antes de repetir)"
        else:
            self.cx.store.append(
                plan.id,
                a.block,
                "DeployStarted",
                {"sha": a.sha},
                ukey=f"deploy-inicio:{plan.id}:{a.block}:{a.sha}",
            )
            pasos, ok = [], True
            for paso in (desplegar, checklist):
                p = _run_tope(f"bash {paso}", w, env, tope)
                pasos.append(f"{paso}: salida {p.returncode}")
                if p.returncode != 0:
                    # Sin la salida el agente solo veía "salida 1" y no sabía qué arreglar
                    # (INGESTA 01: desplegar.sh pedía un SHA como argumento que el motor no da).
                    ok = False
                    pasos.append(_cola(p))
                    rb = _run_tope(f"bash {rollback}", w, env, tope)
                    pasos.append(f"rollback: salida {rb.returncode}")
                    if rb.returncode != 0:
                        pasos.append(_cola(rb))
                    pasos.append(RECETA)
                    break
            detalle = "\n".join(pasos)
        self.cx.store.append(
            plan.id,
            a.block,
            "Deployed",
            {"sha": a.sha, "ok": ok, "detalle": detalle},
            ukey=f"deployed:{plan.id}:{a.block}:{a.sha}",
        )

    def prove(self, plan: Plan, snap: m.Snapshot, a: m.Prove) -> None:
        bv = snap.blocks[a.block]
        block = bv.block
        if block.prueba_real:
            rc = self.repo_conf(snap, block.repo)
            w = self.cx.ws.copia(
                block.repo,
                bv.merged[-1]["commit"],
                self.cx.home / "tmp" / f"prueba-{plan.id}-{a.block}",
            )
            env = self.env_base(dict(rc.prueba_env))
            env["MOTOR_RAIZ"] = str(w)
            tope = float(self.cx.conf.tiempos.get("verifica_tope_s", 1800))
            p = _run_tope(block.prueba_real, w, env, tope)
            # La salida puede ir a un archivo (la sonda de INGESTA 01): sin esto el agente veía "".
            ok, detalle = (
                p.returncode == 0,
                _cola(p) or f"salió {p.returncode} sin imprimir nada",
            )
        else:
            ok, detalle = True, "el bloque no declara prueba real"
        self.cx.store.append(
            plan.id,
            a.block,
            "Proved",
            {"sha": a.sha, "ok": ok, "detalle": detalle},
            ukey=f"proved:{plan.id}:{a.block}:{a.sha}",
        )

    def probe_human(self, plan: Plan, snap: m.Snapshot, a: m.ProbeHuman) -> None:
        """La sonda corre aparte y sin tope de un minuto: una revisión de tres máquinas tarda varios
        (MOTOR 01, bloque E: con 60 s nunca pasaba). Una a la vez por bloque y como mucho cada 2 min;
        el tick no la espera."""
        h = plan.block(a.block).human
        if not (h and h.sonda):
            return
        clave, ahora = f"{plan.id}:{a.block}", time.time()
        corriendo = self._sondas.get(clave)
        if corriendo:
            p, desde = corriendo
            if p.poll() is None:
                if ahora - desde > SONDA_TOPE_S:
                    p.kill()
                    p.wait()
                    self._sondas.pop(clave)
                return
            self._sondas.pop(clave)
            if p.returncode == 0:
                self.cx.store.append(
                    plan.id,
                    a.block,
                    "HumanDone",
                    {"por": "sonda"},
                    ukey=f"humano:{plan.id}:{a.block}",
                )
            self._ultimo_poll[f"sonda:{clave}"] = ahora
            return
        if ahora - self._ultimo_poll.get(f"sonda:{clave}", 0) < SONDA_CADA_S:
            return
        p = subprocess.Popen(
            h.sonda,
            shell=True,
            env=self.env_base({"MOTOR_HOME": str(self.cx.home)}),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            executable="/bin/bash" if os.name != "nt" else None,
        )
        self._sondas[clave] = (p, ahora)
        try:
            p.wait(
                timeout=5
            )  # una sonda rápida (un archivo) se resuelve en este mismo paso: sin aviso de más
        except subprocess.TimeoutExpired:
            return
        self.probe_human(plan, snap, a)

    def limpia(self, plan: Plan, snap: m.Snapshot, a: m.Limpia) -> None:
        """Plan hecho, probado y avisado: borra sus ramas (la del bloque y la de fotos) en GitHub, sus
        copias de trabajo y de revisión en esta máquina. Los registros de cada intento se quedan
        `registros_dias` (14) y los borra purga_registros. Las copias base de cada repo no se tocan."""
        borradas: list[str] = []
        espejos: set[Path] = set()
        for bid, bv in snap.blocks.items():
            if bv.block.human and not bv.block.rows:
                continue
            d = self.cx.ws.espejo(bv.block.repo)
            espejos.add(d)
            for rama in (
                self.cx.ws.rama(plan.id, bid),
                self.cx.ws.rama_wip(plan.id, bid),
            ):
                if git("ls-remote", "--heads", "origin", rama, cwd=d).stdout.strip():
                    if (
                        git("push", "-q", "origin", "--delete", rama, cwd=d).returncode
                        == 0
                    ):
                        borradas.append(f"{bv.block.repo}:{rama}")
                git("update-ref", "-d", f"refs/{rama}", cwd=d)
            w = self.cx.home / "wt" / plan.id / bid
            if w.exists():
                git("worktree", "remove", "--force", str(w), cwd=d)
            git("branch", "-q", "-D", self.cx.ws.rama(plan.id, bid), cwd=d)
        shutil.rmtree(self.cx.home / "wt" / plan.id, ignore_errors=True)
        for x in (self.cx.home / "tmp").glob(f"*-{plan.id}-*"):
            shutil.rmtree(x, ignore_errors=True)
        for x in (self.cx.home / "intentos" / plan.id).glob("*/revision"):
            shutil.rmtree(x, ignore_errors=True)
        for d in espejos:
            git("worktree", "prune", cwd=d)
        self.cx.store.append(
            plan.id, "", "Limpiado", {"ramas": borradas}, ukey=f"limpiado:{plan.id}"
        )

    def purga_registros(self, planes: list[Plan], now: float) -> None:
        """Borra los registros de intentos de los planes limpiados hace más de `registros_dias`."""
        dias = float(self.cx.conf.tiempos.get("registros_dias", 14))
        for p in planes:
            limpio = next(
                (e for e in self.cx.store.events(p.id) if e.kind == "Limpiado"), None
            )
            carpeta = self.cx.home / "intentos" / p.id
            if limpio and now - limpio.ts > dias * 86400 and carpeta.exists():
                shutil.rmtree(carpeta, ignore_errors=True)

    def notify(self, plan: Plan, snap: m.Snapshot, a: m.Notify) -> None:
        if a.template == "humano" and snap.blocks[a.block].human_done:
            return  # la sonda ya lo vio hecho en este mismo paso: no se molesta a David
        texto = render(
            a.template, dict(a.campos), self.cx.maquina.nombre, plan.titulo or plan.id
        )
        self.cx.notifier.encola(f"{plan.id}:{a.key}", texto)
        self.cx.store.append(
            plan.id,
            a.block,
            "NoticeQueued",
            {"key": a.key, "texto": texto},
            ukey=f"notice:{plan.id}:{a.key}",
        )


def _iso_a_epoch(iso: str | None) -> float:
    if not iso:
        return 0.0
    import datetime as dt

    try:
        return dt.datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return 0.0
