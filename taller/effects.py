"""Shell delgado: un efecto por acción, idempotente. Antes de actuar mira si el efecto ya existe.

En el prototipo, "GitHub" es un repo bare local y "CI" es `ci` del config; el
contrato es el mismo que con Orbit (PR draft -> APROBADO -> ready -> completa ->
merge con el SHA exacto), que vive en `_ci_github` / `_merge_github` (por hacer).
"""

from __future__ import annotations

import json
import os
import shlex
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from . import machine as m
from .notify import AvisoInvalido, render
from .plan import HUMAN_KINDS, Plan
from .store import Store


@dataclass
class Config:
    home: Path
    origin: str
    principal: str
    ci: str
    prod_dir: Path
    policy: m.Policy
    telegram_token: str = ""
    telegram_chat: str = ""


def _run(cmd: list[str] | str, cwd: Path | None = None, env: dict | None = None,
         timeout: float = 600) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, cwd=cwd, env={**os.environ, **(env or {})}, capture_output=True,
                          text=True, stdin=subprocess.DEVNULL, timeout=timeout,
                          shell=isinstance(cmd, str), executable="/bin/bash" if isinstance(cmd, str) else None)


def git(*a: str, cwd: Path | None = None) -> subprocess.CompletedProcess:
    return _run(["git", *a], cwd=cwd)


class Effects:
    def __init__(self, cfg: Config, store: Store):
        self.cfg, self.store = cfg, store
        self.home = cfg.home

    # ------------------------------------------------------------------ rutas
    def rama(self, plan: str, block: str) -> str:
        return f"taller/{plan}/{block}"

    def wt(self, plan: str, block: str) -> Path:
        return self.home / "wt" / plan / block

    def attempt_dir(self, plan: str, block: str, role: str, n: int) -> Path:
        return self.home / "intentos" / plan / f"{block}-{role}-{n}"

    def _mirror(self) -> Path:
        """Clon local de origin del que cuelgan todos los worktrees."""
        mirror = self.home / "repo"
        if not (mirror / ".git").exists():
            git("clone", "-q", self.cfg.origin, str(mirror))
        git("fetch", "-q", "--prune", "origin", cwd=mirror)
        return mirror

    def _detached(self, sha: str, donde: Path) -> Path:
        mirror = self._mirror()
        if donde.exists():
            git("worktree", "remove", "--force", str(donde), cwd=mirror)
        git("worktree", "prune", cwd=mirror)
        donde.parent.mkdir(parents=True, exist_ok=True)
        r = git("worktree", "add", "-q", "--detach", str(donde), sha, cwd=mirror)
        if r.returncode != 0:
            raise RuntimeError(r.stderr)
        return donde

    def sha_en_origin(self, sha: str, rama: str) -> str:
        """SHA completo si `sha` existe en origin y es la punta de `rama`; si no, vacío."""
        if not sha or len(sha) < 7:
            return ""
        full = git("rev-parse", "-q", "--verify", f"{sha}^{{commit}}", cwd=Path(self.cfg.origin)).stdout.strip()
        tip = git("rev-parse", "-q", "--verify", f"refs/heads/{rama}", cwd=Path(self.cfg.origin)).stdout.strip()
        return full if full and full == tip else ""

    # ------------------------------------------------------------------ intentos
    def start_attempt(self, plan: Plan, a: m.StartAttempt) -> None:
        ukey = f"start:{plan.id}:{a.block}:{a.role}:{a.n}"
        d = self.attempt_dir(plan.id, a.block, a.role, a.n)
        if d.exists() and (d / "started").exists():
            return  # ya se lanzó (caída entre lanzar y anotar): no se duplica
        d.mkdir(parents=True, exist_ok=True)
        block = plan.block(a.block)
        prov = self.cfg.policy.providers[a.provider]
        if a.role == "implementar":
            cwd = self._prepara_rama(plan.id, a.block)
            brief = self._brief_implementar(plan, block, a, cwd)
            wip = ["--wip-push", self.rama(plan.id, a.block)]
        else:
            cwd = self._detached(a.sha, d / "revision")
            brief = self._brief_revisar(plan, block, a)
            wip = []
        (d / "brief.md").write_text(brief)
        result = d / "result.json"
        prompt = f"Lee {d / 'brief.md'} y ejecútalo completo sin preguntar nada."
        argv = [x.format(brief=str(d / "brief.md"), prompt=prompt, result=str(result))
                for x in prov.argv]
        env = {**os.environ, **dict(prov.env), "TALLER_RESULT": str(result),
               "TALLER_PROVIDER": a.provider, "TALLER_BLOCK": a.block, "TALLER_ROLE": a.role}
        cmd = [sys.executable, "-m", "taller.runwrap", "--dir", str(d), "--cwd", str(cwd),
               "--timeout", str(self.cfg.policy.timeout_s), "--sin-avance",
               str(self.cfg.policy.sin_avance_s), *wip, "--", *argv]
        p = subprocess.Popen(cmd, cwd=cwd, env={**env, "PYTHONPATH": str(Path(__file__).parent.parent)},
                             stdin=subprocess.DEVNULL, stdout=open(d / "runwrap.log", "ab"),
                             stderr=subprocess.STDOUT, start_new_session=True)
        (d / "started").write_text(str(p.pid))
        self.store.append(plan.id, a.block, "AttemptStarted",
                          {"n": a.n, "role": a.role, "provider": a.provider, "pid": p.pid,
                           "dir": str(d), "sha": a.sha, "feedback": list(a.feedback)}, ukey=ukey)

    def _prepara_rama(self, plan: str, block: str) -> Path:
        mirror = self._mirror()
        w = self.wt(plan, block)
        rama = self.rama(plan, block)
        remota = git("rev-parse", "--verify", "-q", f"origin/{rama}", cwd=mirror).stdout.strip()
        if not w.exists():
            w.parent.mkdir(parents=True, exist_ok=True)
            base = f"origin/{rama}" if remota else f"origin/{self.cfg.principal}"
            git("worktree", "prune", cwd=mirror)
            r = git("worktree", "add", "-q", "-B", rama, str(w), base, cwd=mirror)
            if r.returncode != 0:
                raise RuntimeError(r.stderr)
        else:
            git("fetch", "-q", "origin", cwd=w)
            if remota:
                # el relevo sigue desde lo empujado (el runwrap anterior hizo wip-push)
                git("reset", "-q", "--hard", f"origin/{rama}", cwd=w)
        return w

    def _brief_implementar(self, plan: Plan, block, a: m.StartAttempt, cwd: Path) -> str:
        filas = "\n".join(f"- {r.id}: {r.contenido}" + (f"\n  Acepta: `{r.acepta}`" if r.acepta else "")
                          for r in block.rows)
        fb = "\n".join(f"- {x}" for x in a.feedback) or "- (primer intento)"
        diff = git("diff", "--stat", f"origin/{self.cfg.principal}...HEAD", cwd=cwd).stdout.strip()
        return f"""# Encargo {plan.id} · bloque {block.id} · intento {a.n}

Trabajas solo en este directorio, en la rama `{self.rama(plan.id, block.id)}`. Nadie lee tu
pantalla: no preguntes nada. Si algo no te deja seguir, intenta otra forma; si de verdad
solo una persona puede hacerlo ({', '.join(HUMAN_KINDS)}), dilo en el resultado.

## Qué entregar
{block.titulo}

{filas}

## Qué pasó antes (corrígelo primero)
{fb}

## Lo que ya hay en la rama
{diff or '(nada todavía)'}

## Cómo se entrega
1. Un commit por fila, con el id de la fila al inicio del mensaje.
2. `git push origin HEAD:refs/heads/{self.rama(plan.id, block.id)}` (nunca a la rama principal,
   nunca `--no-verify`).
3. Escribe en `$TALLER_RESULT` un JSON: {{"sha": "<git rev-parse HEAD>", "resumen": "..."}}.
   Lo que digas no cuenta como prueba: el sistema verifica el SHA y corre cada `Acepta`.
"""

    def _brief_revisar(self, plan: Plan, block, a: m.StartAttempt) -> str:
        filas = "\n".join(f"- {r.id}: {r.contenido}" + (f" | Acepta: `{r.acepta}`" if r.acepta else "")
                          for r in block.rows)
        return f"""# Revisión {plan.id} · bloque {block.id} · SHA {a.sha}

Estás en una copia de solo lectura de ese SHA. Revisa el cambio contra lo pedido:

{filas}

Regla de bloqueo: un hallazgo bloquea solo si trae el comando que lo reproduce (`repro`).
Lo demás va en `notas` y no reabre la ronda.

Escribe en `$TALLER_RESULT` un JSON:
{{"sha": "{a.sha}", "veredicto": "APROBADO" | "CAMBIOS",
  "bloqueantes": [{{"texto": "...", "repro": "comando", "arreglo": "..."}}], "notas": ["..."]}}
"""

    def collect(self, plan: Plan, block: str, att: m.Attempt) -> dict | None:
        """Si el intento terminó, devuelve su resultado clasificado (y anota lo que derive)."""
        d = Path(att.dir)
        oc = d / "outcome.json"
        if not oc.exists():
            if _vivo(att.pid):
                return None
            return {"kind": "perdido", "detalle": "el proceso desapareció sin dejar resultado"}
        o = json.loads(oc.read_text())
        c = m.classify(o["exit"], o["timed_out"], o["no_progress"], o["tail"])
        if c["kind"] == "cuota":
            hasta = _iso_a_epoch(c.get("reset_at")) or time.time() + self.cfg.policy.cooldown_default_s
            self.store.append(plan.id, "", "Cooldown",
                              {"provider": att.provider, "hasta": hasta},
                              ukey=f"cooldown:{att.provider}:{att.dir}")
            return c
        if c["kind"] != "terminado":
            return c
        res_path = d / "result.json"
        try:
            res = json.loads(res_path.read_text())
        except (OSError, ValueError):
            return {"kind": "sin_resultado", "detalle": "terminó sin escribir el resultado"}
        if att.role == "revisar":
            if res.get("sha") != att.sha or res.get("veredicto") not in ("APROBADO", "CAMBIOS"):
                return {"kind": "veredicto_invalido", "detalle": "el veredicto no cita el SHA revisado"}
            self.store.append(plan.id, block, "Reviewed",
                              {"sha": att.sha, "veredicto": res["veredicto"],
                               "bloqueantes": res.get("bloqueantes", []), "notas": res.get("notas", []),
                               "provider": att.provider}, ukey=f"review:{att.dir}")
            return {"kind": "veredicto"}
        sha = str(res.get("sha", ""))
        full = self.sha_en_origin(sha, self.rama(plan.id, block))
        if not full:
            return {"kind": "mintio", "detalle": f"dijo haber entregado {sha[:12]!r} y ese SHA no es la punta de su rama"}
        if att.sha and full == att.sha:
            return {"kind": "sin_cambios", "detalle": "volvió a entregar lo mismo que ya había fallado, sin corregir"}
        return {"kind": "entregado", "sha": full, "resumen": str(res.get("resumen", ""))[:500]}

    # ------------------------------------------------------------------ pasos síncronos
    def verify(self, plan: Plan, a: m.Verify) -> None:
        block = plan.block(a.block)
        w = self._detached(a.sha, self.home / "tmp" / f"verifica-{plan.id}-{a.block}")
        fallas = []
        for r in block.rows:
            if r.acepta:
                p = _run(r.acepta, cwd=w, timeout=900)
                if p.returncode != 0:
                    fallas.append(f"{r.id}: `{r.acepta}` salió {p.returncode}: {(p.stdout + p.stderr)[-300:]}")
        self.store.append(plan.id, a.block, "Verified",
                          {"sha": a.sha, "ok": not fallas, "detalle": "\n".join(fallas)},
                          ukey=f"verified:{plan.id}:{a.block}:{a.sha}")

    def run_ci(self, plan: Plan, a: m.RunCI) -> None:
        if self.cfg.ci == "github":
            raise NotImplementedError("Orbit: gh pr ready + esperar rapido/completa/gate + bot ai-review")
        w = self._detached(a.sha, self.home / "tmp" / f"ci-{plan.id}-{a.block}")
        p = _run(self.cfg.ci, cwd=w, timeout=3600)
        self.store.append(plan.id, a.block, "CiResult",
                          {"sha": a.sha, "green": p.returncode == 0, "detalle": (p.stdout + p.stderr)[-800:]},
                          ukey=f"ci:{plan.id}:{a.block}:{a.sha}")

    def merge(self, plan: Plan, a: m.Merge) -> None:
        t = a.ticket
        if not isinstance(t, m.MergeTicket):
            raise TypeError("merge sin MergeTicket")
        marca = f"taller-merge: {plan.id}/{t.block}/{t.sha}"
        mirror = self._mirror()
        ya = git("log", f"origin/{self.cfg.principal}", "--format=%H %B", "--grep", marca, cwd=mirror).stdout
        if not ya.strip():
            w = self.home / "tmp" / "merge"
            self._detached(f"origin/{self.cfg.principal}", w)
            r = git("merge", "--squash", t.sha, cwd=w)
            if r.returncode != 0:
                git("merge", "--abort", cwd=w)
                git("reset", "-q", "--hard", cwd=w)
                self.store.append(plan.id, t.block, "CiResult",
                                  {"sha": t.sha, "green": False,
                                   "detalle": "conflicto al mergear con la rama principal; actualiza la rama"},
                                  ukey=f"conflicto:{plan.id}:{t.block}:{t.sha}:{time.time()}")
                return
            git("-c", "user.name=taller", "-c", "user.email=taller@local", "commit", "-q",
                "-m", f"{plan.id} {t.block}: {plan.block(t.block).titulo}\n\n{marca}", cwd=w)
            p = git("push", "-q", "origin", f"HEAD:refs/heads/{self.cfg.principal}", cwd=w)
            if p.returncode != 0:
                return  # otro movió la principal: el siguiente tick reintenta
            git("fetch", "-q", "origin", cwd=mirror)
        tip = git("log", f"origin/{self.cfg.principal}", "--format=%H", "-1", "--grep", marca, cwd=mirror).stdout.strip()
        self.store.append(plan.id, t.block, "Merged", {"sha": t.sha, "principal": tip},
                          ukey=f"merged:{plan.id}:{t.block}:{t.sha}")

    def deploy(self, plan: Plan, a: m.Deploy) -> None:
        block = plan.block(a.block)
        rel = block.release
        marca = self.cfg.prod_dir / f".taller-desplegado-{plan.id}-{a.block}-{a.sha[:12]}"
        if marca.exists():
            ok, detalle = True, "ya estaba desplegado"
        else:
            mirror = self._mirror()
            tip = git("log", f"origin/{self.cfg.principal}", "--format=%H", "-1", "--grep",
                      f"taller-merge: {plan.id}/{a.block}/{a.sha}", cwd=mirror).stdout.strip()
            w = self._detached(tip, self.home / "tmp" / f"deploy-{plan.id}-{a.block}")
            env = {"PROD_DIR": str(self.cfg.prod_dir)}
            self.cfg.prod_dir.mkdir(parents=True, exist_ok=True)
            pasos = []
            ok = True
            for paso in (rel.desplegar, rel.checklist):
                p = _run(f"bash {shlex.quote(paso)}", cwd=w, env=env)
                pasos.append(f"{paso}: salida {p.returncode} {(p.stdout + p.stderr)[-200:]}")
                if p.returncode != 0:
                    ok = False
                    rb = _run(f"bash {shlex.quote(rel.rollback)}", cwd=w, env=env)
                    pasos.append(f"rollback: salida {rb.returncode}")
                    break
            if ok:
                marca.write_text(tip)
            detalle = "\n".join(pasos)
        self.store.append(plan.id, a.block, "Deployed", {"sha": a.sha, "ok": ok, "detalle": detalle},
                          ukey=f"deployed:{plan.id}:{a.block}:{a.sha}")

    def prove(self, plan: Plan, a: m.Prove) -> None:
        block = plan.block(a.block)
        if block.prueba_real:
            p = _run(block.prueba_real, env={"PROD_DIR": str(self.cfg.prod_dir)}, timeout=900)
            ok, detalle = p.returncode == 0, (p.stdout + p.stderr)[-500:]
        else:
            ok, detalle = True, "el bloque no declara prueba real"
        self.store.append(plan.id, a.block, "Proved", {"sha": a.sha, "ok": ok, "detalle": detalle},
                          ukey=f"proved:{plan.id}:{a.block}:{a.sha}")

    def probe_human(self, plan: Plan, a: m.ProbeHuman) -> None:
        h = plan.block(a.block).human
        if h and h.sonda and _run(h.sonda, env={"TALLER_HOME": str(self.home)}, timeout=60).returncode == 0:
            self.store.append(plan.id, a.block, "HumanDone", {"por": "sonda"}, ukey=f"humano:{plan.id}:{a.block}")

    # ------------------------------------------------------------------ avisos
    def notify(self, plan: Plan, a: m.Notify) -> None:
        campos = dict(a.campos)
        try:
            texto = render(a.template, campos)
        except (AvisoInvalido, KeyError):
            texto = "Avancé en el plan; Claude te puede contar el detalle."
        titulo = plan.titulo or plan.id
        texto = f"{titulo}\n{texto}" if a.template != "intrusion" else texto
        try:
            texto = render_final(texto)
        except AvisoInvalido:
            texto = "Avancé en el plan; Claude te puede contar el detalle."
        out = self.home / "avisos" / "pendientes"
        out.mkdir(parents=True, exist_ok=True)
        nombre = a.key.replace(":", "_").replace("/", "_")
        (out / f"{int(time.time() * 1000)}-{nombre}.txt").write_text(texto)
        self.store.append(plan.id, a.block, "NoticeQueued", {"key": a.key, "texto": texto},
                          ukey=f"notice:{plan.id}:{a.key}")

    def send_pending(self) -> None:
        """Manda los avisos pendientes por Telegram si hay credenciales; si falla, reintenta luego."""
        pend = self.home / "avisos" / "pendientes"
        enviados = self.home / "avisos" / "enviados"
        if not pend.exists() or not (self.cfg.telegram_token and self.cfg.telegram_chat):
            return
        enviados.mkdir(parents=True, exist_ok=True)
        for f in sorted(pend.glob("*.txt")):
            data = urllib.parse.urlencode({"chat_id": self.cfg.telegram_chat, "text": f.read_text()}).encode()
            url = f"https://api.telegram.org/bot{self.cfg.telegram_token}/sendMessage"
            try:
                urllib.request.urlopen(url, data=data, timeout=20)
                os.replace(f, enviados / f.name)
            except OSError:
                return


def render_final(texto: str) -> str:
    from .notify import lint_plain
    return lint_plain(texto)


def _vivo(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    # un hijo terminado sin recoger sigue "vivo" para kill(0): recógelo si es nuestro
    try:
        r, _ = os.waitpid(pid, os.WNOHANG)
        return r == 0
    except ChildProcessError:
        return True


def _iso_a_epoch(iso: str | None) -> float:
    if not iso:
        return 0.0
    import datetime as dt
    try:
        return dt.datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return 0.0
