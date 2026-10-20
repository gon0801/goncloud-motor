"""Un efecto por acción, idempotente: antes de actuar mira si el efecto ya existe.

Separado por conocimiento: `Workspace` sabe de git local, `GitHubPort` de PRs y checks,
`plataforma` de procesos, `Notifier` de Telegram. Aquí solo se orquestan.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

from . import machine as m
from . import plataforma
from .conf import Conf, Maquina, RepoConf
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


def _run(cmd: str, cwd: Path | None, env: dict, timeout: float = 900) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, cwd=cwd, env=env, capture_output=True, text=True, stdin=subprocess.DEVNULL,
                          timeout=timeout, shell=True, executable="/bin/bash" if os.name != "nt" else None)


class Effects:
    def __init__(self, cx: Contexto):
        self.cx = cx
        self._ultimo_poll: dict[str, float] = {}

    # ------------------------------------------------------------------ entorno
    def env_base(self, extra: dict | None = None) -> dict:
        mq = self.cx.maquina
        env = {**os.environ, **dict(mq.env), **dict(mq.secretos), **(extra or {})}
        if mq.path_extra:
            env["PATH"] = os.pathsep.join([*mq.path_extra, env.get("PATH", "")])
        return env

    def repo_conf(self, snap: m.Snapshot, repo: str) -> RepoConf:
        return RepoConf.parse(snap.repos.get(repo, {}).get("texto", ""))

    def pin_repos(self, plan: Plan) -> None:
        """Lee `.motor.toml` de la rama base de cada repo UNA vez y lo fija en el log."""
        for repo in dict.fromkeys(b.repo for b in plan.blocks):
            if any(e.kind == "RepoPinned" and e.data["repo"] == repo for e in self.cx.store.events(plan.id)):
                continue
            d = self.cx.ws.espejo(repo)
            base = git("symbolic-ref", "--short", "refs/remotes/origin/HEAD", cwd=d).stdout.strip().removeprefix("origin/") or "main"
            sha, texto = self.cx.ws.leer_en_base(repo, base, ".motor.toml")
            principal = RepoConf.parse(texto).principal if texto else base
            if principal != base:
                sha, texto = self.cx.ws.leer_en_base(repo, principal, ".motor.toml")
            if not texto:
                texto = f'[repo]\nprincipal = "{principal}"\n'
            self.cx.store.append(plan.id, "", "RepoPinned", {"repo": repo, "sha": sha, "texto": texto,
                                                             "privado": self.cx.gh.es_privado(repo)},
                                 ukey=f"pin:{plan.id}:{repo}")

    def attempt_dir(self, plan: str, block: str, role: str, n: int) -> Path:
        return self.cx.home / "intentos" / plan / f"{block}-{role}-{n}"

    # ------------------------------------------------------------------ intentos
    def start_attempt(self, plan: Plan, snap: m.Snapshot, a: m.StartAttempt) -> None:
        d = self.attempt_dir(plan.id, a.block, a.role, a.n)
        if (d / "started").exists():
            return  # se lanzó y el daemon cayó antes de anotar: no se duplica
        d.mkdir(parents=True, exist_ok=True)
        block = plan.block(a.block)
        rc = self.repo_conf(snap, block.repo)
        ruta = self.cx.conf.rutas[a.modelo]
        if a.role == "implementar":
            cwd = self.cx.ws.prepara(block.repo, plan.id, block.id, rc.principal)
            brief = self._brief_implementar(plan, block, a, cwd, rc)
            wip = ["--wip-rama", self.cx.ws.rama_wip(plan.id, block.id), "--wip-cada", str(self.cx.policy.wip_cada_s)]
            if not snap.repos.get(block.repo, {}).get("privado", False):
                wip.append("--wip-local")   # repo público: lo de a medias no se publica
        else:
            cwd = self.cx.ws.copia(block.repo, a.sha, d / "revision")
            brief = self._brief_revisar(plan, block, a, rc)
            wip = []
        (d / "brief.md").write_text(brief)
        result = d / "result.json"
        prompt = f"Lee {d / 'brief.md'} y ejecútalo completo sin preguntar nada."
        argv = [x.format(brief=str(d / "brief.md"), prompt=prompt, result=str(result)) for x in ruta.argv]
        if ruta.cli in self.cx.maquina.wsl:   # Muse en la PC: dentro de WSL, con su token pasado por WSLENV
            argv = ["wsl.exe", "-e", *argv]
        env = self.env_base({"MOTOR_RESULT": str(result), "MOTOR_MODEL": a.modelo, "MOTOR_BLOCK": a.block,
                             "MOTOR_ROLE": a.role, "WSLENV": "META_API_KEY/u:MOTOR_RESULT/p",
                             "PYTHONPATH": str(RAIZ)})
        cmd = [sys.executable, "-m", "motor.runwrap", "--dir", str(d), "--cwd", str(cwd),
               "--timeout", str(self.cx.policy.timeout_s), "--sin-avance", str(self.cx.policy.sin_avance_s),
               *wip, "--", *argv]
        proc = plataforma.lanzar_separado(cmd, cwd, env, d / "runwrap.log")
        (d / "started").write_text(json.dumps(proc))
        self.cx.store.append(plan.id, a.block, "AttemptStarted",
                             {"n": a.n, "role": a.role, "modelo": a.modelo, "cuenta": ruta.cuenta, "proc": proc,
                              "dir": str(d), "sha": a.sha, "conf_sha": self.cx.conf.sha, "feedback": list(a.feedback)},
                             ukey=f"start:{plan.id}:{a.block}:{a.role}:{a.n}")

    def _brief_implementar(self, plan: Plan, block, a: m.StartAttempt, cwd: Path, rc: RepoConf) -> str:
        filas = "\n".join(f"- {r.id}: {r.contenido}" + (f"\n  Acepta: `{r.acepta}`" if r.acepta else "")
                          for r in block.rows)
        fb = "\n".join(f"- {x}" for x in a.feedback) or "- (primer intento)"
        rama = self.cx.ws.rama(plan.id, block.id)
        pendientes = git("status", "--short", cwd=cwd).stdout.strip()
        return f"""{self.cx.conf.reglas}
# Encargo {plan.id} · bloque {block.id} · intento {a.n}

Trabajas solo en esta carpeta, en la rama `{rama}`. Nadie lee tu pantalla: no preguntes nada.
Si algo no te deja seguir, intenta otra forma; si de verdad solo una persona puede hacerlo
({', '.join(HUMAN_KINDS)}), dilo en el resultado.

## Qué entregar
{block.titulo}

{filas}

## Qué pasó antes (corrígelo primero)
{fb}

## Lo que quedó sin commitear de un intento anterior
{pendientes or '(nada)'}

## Cómo se entrega
1. Un commit por fila, con el id de la fila al inicio del mensaje. Nunca `--no-verify`.
2. `git push origin HEAD:refs/heads/{rama}` (nunca a `{rc.principal}`).
3. Escribe en `$MOTOR_RESULT` un JSON: {{"sha": "<git rev-parse HEAD>", "resumen": "..."}}.
   Lo que digas no cuenta como prueba: el motor verifica el SHA y corre cada `Acepta`.
"""

    def _brief_revisar(self, plan: Plan, block, a: m.StartAttempt, rc: RepoConf) -> str:
        filas = "\n".join(f"- {r.id}: {r.contenido}" + (f" | Acepta: `{r.acepta}`" if r.acepta else "")
                          for r in block.rows)
        return f"""{self.cx.conf.reglas_revisor}
# Revisión {plan.id} · bloque {block.id} · SHA {a.sha}

Estás en una copia de solo lectura de ese SHA (diferencia contra `{rc.principal}`). Revisa contra lo pedido:

{filas}

Un hallazgo bloquea solo si trae el comando que lo reproduce (`repro`). Lo demás va en `notas`.
Escribe en `$MOTOR_RESULT`:
{{"sha": "{a.sha}", "veredicto": "APROBADO" | "CAMBIOS",
  "bloqueantes": [{{"texto": "...", "repro": "comando", "arreglo": "..."}}], "notas": ["..."]}}
"""

    def collect(self, plan: Plan, snap: m.Snapshot, block_id: str, att: m.Attempt) -> dict | None:
        d = Path(att.dir)
        oc = d / "outcome.json"
        if not oc.exists():
            st = plataforma.estado_proc(att.proc)
            if st == "vivo":
                return None
            if st == "corte":
                return {"kind": "corte", "detalle": "la máquina se apagó o reinició"}
            return {"kind": "perdido", "detalle": "el proceso desapareció sin dejar resultado"}
        o = json.loads(oc.read_text())
        c = m.classify(o["exit"], o["timed_out"], o["no_progress"], o["tail"])
        if c["kind"] == "cuota":
            hasta = _iso_a_epoch(c.get("reset_at")) or time.time() + self.cx.policy.cooldown_default_s
            cuenta = self.cx.policy.cuenta.get(att.modelo, att.modelo)
            self.cx.store.append(plan.id, "", "Cooldown", {"cuenta": cuenta, "hasta": hasta, "modelo": att.modelo},
                                 ukey=f"cooldown:{att.dir}")
            return c
        if c["kind"] != "terminado":
            return c
        try:
            res = json.loads((d / "result.json").read_text())
        except (OSError, ValueError):
            return {"kind": "sin_resultado", "detalle": "terminó sin escribir el resultado"}
        block = plan.block(block_id)
        if att.role == "revisar":
            if res.get("sha") != att.sha or res.get("veredicto") not in ("APROBADO", "CAMBIOS"):
                return {"kind": "veredicto_invalido", "detalle": "el veredicto no cita el SHA revisado"}
            self.cx.store.append(plan.id, block_id, "Reviewed",
                                 {"sha": att.sha, "veredicto": res["veredicto"], "modelo": att.modelo,
                                  "bloqueantes": res.get("bloqueantes", []), "notas": res.get("notas", [])},
                                 ukey=f"review:{att.dir}")
            return {"kind": "veredicto"}
        sha = str(res.get("sha", ""))
        punta = self.cx.ws.punta(block.repo, self.cx.ws.rama(plan.id, block_id))
        if not sha or len(sha) < 7 or not punta.startswith(sha):
            return {"kind": "mintio", "detalle": f"dijo haber entregado {sha[:12]!r} y no es la punta de su rama"}
        if att.sha and punta == att.sha:
            return {"kind": "sin_cambios", "detalle": "volvió a entregar lo mismo que ya había fallado"}
        return {"kind": "entregado", "sha": punta, "resumen": str(res.get("resumen", ""))[:500]}

    # ------------------------------------------------------------------ pasos
    def verify(self, plan: Plan, snap: m.Snapshot, a: m.Verify) -> None:
        block = plan.block(a.block)
        rc = self.repo_conf(snap, block.repo)
        w = self.cx.ws.copia(block.repo, a.sha, self.cx.home / "tmp" / f"verifica-{plan.id}-{a.block}")
        fallas = []
        for r in block.rows:
            if r.acepta:
                p = _run(r.acepta, w, self.env_base(dict(rc.prueba_env)))
                if p.returncode != 0:
                    fallas.append(f"{r.id}: `{r.acepta}` salió {p.returncode}: {(p.stdout + p.stderr)[-300:]}")
        self.cx.store.append(plan.id, a.block, "Verified", {"sha": a.sha, "ok": not fallas, "detalle": "\n".join(fallas)},
                             ukey=f"verified:{plan.id}:{a.block}:{a.sha}")

    def open_pr(self, plan: Plan, snap: m.Snapshot, a: m.OpenPr) -> None:
        block = plan.block(a.block)
        rc = self.repo_conf(snap, block.repo)
        pr = self.cx.gh.abrir_pr(block.repo, self.cx.ws.rama(plan.id, a.block), rc.principal,
                                 f"{plan.id} {a.block}: {block.titulo}",
                                 f"Plan {plan.id}, bloque {a.block}. Lo lleva el motor de la {self.cx.maquina.nombre}.")
        self.cx.store.append(plan.id, a.block, "PrOpened", {"numero": pr.numero, "rama": pr.rama},
                             ukey=f"pr:{plan.id}:{a.block}")

    def mark_ready(self, plan: Plan, snap: m.Snapshot, a: m.MarkReady) -> None:
        bv = snap.blocks[a.block]
        self.cx.gh.marcar_listo(plan.block(a.block).repo, int(bv.pr["numero"]))
        self.cx.store.append(plan.id, a.block, "MarkedReady", {"sha": a.sha}, ukey=f"ready:{plan.id}:{a.block}:{a.sha}")

    def poll_checks(self, plan: Plan, snap: m.Snapshot, a: m.PollChecks) -> None:
        k = f"{plan.id}:{a.block}:{a.sha}"
        if time.time() - self._ultimo_poll.get(k, 0) < self.cx.checks_cada_s:
            return
        self._ultimo_poll[k] = time.time()
        block = plan.block(a.block)
        rc = self.repo_conf(snap, block.repo)
        v = self.cx.gh.checks(block.repo, int(snap.blocks[a.block].pr["numero"]), a.sha, rc.checks_extra)
        if v.terminal:   # solo se anota el resultado final: los sondeos no ensucian el log
            self.cx.store.append(plan.id, a.block, "ChecksResult",
                                 {"sha": a.sha, "green": v.green, "detalle": f"fallaron: {list(v.fallas)}" if v.fallas else ""},
                                 ukey=f"checks:{k}")

    def merge(self, plan: Plan, snap: m.Snapshot, a: m.Merge) -> None:
        t = a.ticket
        if not isinstance(t, m.MergeTicket):
            raise TypeError("merge sin MergeTicket")
        block = plan.block(t.block)
        rc = self.repo_conf(snap, block.repo)
        try:
            pr = self.cx.gh.merge(block.repo, t.pr, t.sha, rc.merge, f"{plan.id} {t.block}: {block.titulo}")
        except MergeRechazado as e:
            # la punta se movió o algo cambió: vuelve al implementador con el motivo
            self.cx.store.append(plan.id, t.block, "ChecksResult", {"sha": t.sha, "green": False, "detalle": str(e)},
                                 ukey=f"rechazo:{plan.id}:{t.block}:{t.sha}:{int(time.time())}")
            return
        self.cx.store.append(plan.id, t.block, "Merged", {"sha": t.sha, "commit": pr.merge_commit},
                             ukey=f"merged:{plan.id}:{t.block}:{t.sha}")

    def _receta(self, snap: m.Snapshot, block) -> tuple[str, str, str]:
        if block.release:
            return block.release.desplegar, block.release.checklist, block.release.rollback
        rc = self.repo_conf(snap, block.repo)
        return rc.desplegar, rc.checklist, rc.rollback

    def deploy(self, plan: Plan, snap: m.Snapshot, a: m.Deploy) -> None:
        bv = snap.blocks[a.block]
        block = bv.block
        rc = self.repo_conf(snap, block.repo)
        desplegar, checklist, rollback = self._receta(snap, block)
        commit = bv.merged[-1]["commit"]
        w = self.cx.ws.copia(block.repo, commit, self.cx.home / "tmp" / f"despliega-{plan.id}-{a.block}")
        env = self.env_base(dict(rc.prueba_env))
        if not desplegar:
            ok, detalle = False, "el bloque es de despliegue y el repo no trae receta"
        elif a.sha in bv.deploy_started and _run(f"bash {checklist}", w, env).returncode == 0:
            ok, detalle = True, "ya estaba desplegado (se comprobó antes de repetir)"
        else:
            self.cx.store.append(plan.id, a.block, "DeployStarted", {"sha": a.sha}, ukey=f"deploy-inicio:{plan.id}:{a.block}:{a.sha}")
            pasos, ok = [], True
            for paso in (desplegar, checklist):
                p = _run(f"bash {paso}", w, env)
                pasos.append(f"{paso}: salida {p.returncode}")
                if p.returncode != 0:
                    ok = False
                    rb = _run(f"bash {rollback}", w, env)
                    pasos.append(f"rollback: salida {rb.returncode}")
                    break
            detalle = "\n".join(pasos)
        self.cx.store.append(plan.id, a.block, "Deployed", {"sha": a.sha, "ok": ok, "detalle": detalle},
                             ukey=f"deployed:{plan.id}:{a.block}:{a.sha}")

    def prove(self, plan: Plan, snap: m.Snapshot, a: m.Prove) -> None:
        bv = snap.blocks[a.block]
        block = bv.block
        if block.prueba_real:
            rc = self.repo_conf(snap, block.repo)
            w = self.cx.ws.copia(block.repo, bv.merged[-1]["commit"], self.cx.home / "tmp" / f"prueba-{plan.id}-{a.block}")
            p = _run(block.prueba_real, w, self.env_base(dict(rc.prueba_env)))
            ok, detalle = p.returncode == 0, (p.stdout + p.stderr)[-500:]
        else:
            ok, detalle = True, "el bloque no declara prueba real"
        self.cx.store.append(plan.id, a.block, "Proved", {"sha": a.sha, "ok": ok, "detalle": detalle},
                             ukey=f"proved:{plan.id}:{a.block}:{a.sha}")

    def probe_human(self, plan: Plan, snap: m.Snapshot, a: m.ProbeHuman) -> None:
        h = plan.block(a.block).human
        if h and h.sonda and _run(h.sonda, None, self.env_base({"MOTOR_HOME": str(self.cx.home)}), 60).returncode == 0:
            self.cx.store.append(plan.id, a.block, "HumanDone", {"por": "sonda"}, ukey=f"humano:{plan.id}:{a.block}")

    def notify(self, plan: Plan, snap: m.Snapshot, a: m.Notify) -> None:
        if a.template == "humano" and snap.blocks[a.block].human_done:
            return   # la sonda ya lo vio hecho en este mismo paso: no se molesta a David
        texto = render(a.template, dict(a.campos), self.cx.maquina.nombre, plan.titulo or plan.id)
        self.cx.notifier.encola(f"{plan.id}:{a.key}", texto)
        self.cx.store.append(plan.id, a.block, "NoticeQueued", {"key": a.key, "texto": texto},
                             ukey=f"notice:{plan.id}:{a.key}")


def _iso_a_epoch(iso: str | None) -> float:
    if not iso:
        return 0.0
    import datetime as dt
    try:
        return dt.datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return 0.0
