"""Git local: un espejo por repo, una carpeta de trabajo por bloque y fotos del trabajo a medias.

Ramas:
  motor/<maquina>/<plan>/<bloque>       la rama del bloque (la del PR)
  motor-wip/<maquina>/<plan>/<bloque>   fotos del trabajo sin commitear (no son del PR)

Las fotos se hacen con un índice temporal (`GIT_INDEX_FILE`) y `commit-tree`: no tocan el
índice ni la rama del agente, no corren hooks (no es un commit del repo) y nunca usan
`--no-verify`. Antes de empujar una foto se busca algo con forma de secreto; si aparece, esa
foto no se sube.
"""

from __future__ import annotations

import hashlib
import os
import re
import subprocess
import tempfile
from pathlib import Path


def git(*a: str, cwd: Path | None = None, env: dict | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *a], cwd=cwd, capture_output=True, text=True, stdin=subprocess.DEVNULL,
                          env={**os.environ, **(env or {})})


SECRETO = re.compile(
    r"(ghp_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|sk-[A-Za-z0-9-]{20,}|AKIA[0-9A-Z]{16}|"
    r"xox[abp]-[A-Za-z0-9-]{10,}|-----BEGIN [A-Z ]*PRIVATE KEY-----|"
    r"(?i:(api[_-]?key|token|secret|password|contraseña)\s*[:=]\s*['\"]?[A-Za-z0-9/+_.-]{16,}))")


def foto_wip(worktree: Path, rama_wip: str) -> str:
    """Sube una foto del estado actual de la carpeta. Devuelve 'subida', 'igual', 'secreto' o 'error: ...'."""
    head = git("rev-parse", "HEAD", cwd=worktree).stdout.strip()
    if not head:
        return "error: sin HEAD"
    with tempfile.TemporaryDirectory() as tmp:
        env = {"GIT_INDEX_FILE": str(Path(tmp) / "index")}
        git("read-tree", "HEAD", cwd=worktree, env=env)
        git("add", "-A", cwd=worktree, env=env)
        tree = git("write-tree", cwd=worktree, env=env).stdout.strip()
        if tree == git("rev-parse", "HEAD^{tree}", cwd=worktree).stdout.strip():
            return "igual"
        previa = git("ls-remote", "origin", f"refs/heads/{rama_wip}", cwd=worktree).stdout.split()
        if previa and git("rev-parse", "-q", "--verify", f"{previa[0]}^{{tree}}", cwd=worktree).stdout.strip() == tree \
                and git("rev-parse", "-q", "--verify", f"{previa[0]}^", cwd=worktree).stdout.strip() == head:
            return "igual"   # la foto anterior ya tiene exactamente esto
        diff = git("diff", "--cached", "HEAD", cwd=worktree, env=env).stdout
        if SECRETO.search(diff):
            return "secreto"
        c = git("-c", "user.name=motor", "-c", "user.email=motor@local", "commit-tree", tree, "-p", head,
                "-m", "foto del trabajo a medias (motor)", cwd=worktree, env=env).stdout.strip()
    p = git("push", "-q", "--force", "origin", f"{c}:refs/heads/{rama_wip}", cwd=worktree)
    return "subida" if p.returncode == 0 else f"error: {p.stderr[-200:]}"


class Workspace:
    def __init__(self, home: Path, maquina: str):
        self.home = home
        self.maquina = re.sub(r"[^a-z0-9]+", "-", maquina.lower()).strip("-")   # "Mac (prueba)" -> "mac-prueba"

    @staticmethod
    def slug(repo: str) -> str:
        nombre = re.sub(r"[^A-Za-z0-9]+", "-", repo.rstrip("/").removesuffix(".git").split("/")[-1].split(":")[-1])
        return f"{nombre}-{hashlib.sha1(repo.encode()).hexdigest()[:6]}"

    def rama(self, plan: str, block: str) -> str:
        return f"motor/{self.maquina}/{plan}/{block}"

    def rama_wip(self, plan: str, block: str) -> str:
        return f"motor-wip/{self.maquina}/{plan}/{block}"

    def espejo(self, repo: str) -> Path:
        d = self.home / "repos" / self.slug(repo)
        if not (d / ".git").exists():
            d.parent.mkdir(parents=True, exist_ok=True)
            r = git("clone", "-q", repo, str(d))
            if r.returncode != 0:
                raise RuntimeError(f"no se pudo clonar {repo}: {r.stderr[-300:]}")
        git("fetch", "-q", "--prune", "origin", "+refs/heads/*:refs/remotes/origin/*", cwd=d)
        return d

    def punta(self, repo: str, rama: str) -> str:
        r = git("ls-remote", repo, f"refs/heads/{rama}")
        return r.stdout.split()[0] if r.returncode == 0 and r.stdout.strip() else ""

    def leer_en_base(self, repo: str, base: str, ruta: str) -> tuple[str, str]:
        """(SHA de la base, contenido del archivo en ese SHA). Vacío si no existe."""
        d = self.espejo(repo)
        sha = git("rev-parse", f"origin/{base}", cwd=d).stdout.strip()
        r = git("show", f"{sha}:{ruta}", cwd=d)
        return sha, (r.stdout if r.returncode == 0 else "")

    def prepara(self, repo: str, plan: str, block: str, base: str) -> Path:
        """Carpeta del bloque en la punta de su rama (o de la base); restaura la foto wip si
        corresponde a esa punta. Idempotente: llamarla de nuevo deja el mismo resultado."""
        d = self.espejo(repo)
        rama, wip = self.rama(plan, block), self.rama_wip(plan, block)
        w = self.home / "wt" / plan / block
        remota = git("rev-parse", "--verify", "-q", f"origin/{rama}", cwd=d).stdout.strip()
        punto = f"origin/{rama}" if remota else f"origin/{base}"
        if not w.exists():
            w.parent.mkdir(parents=True, exist_ok=True)
            git("worktree", "prune", cwd=d)
            r = git("worktree", "add", "-q", "-B", rama, str(w), punto, cwd=d)
            if r.returncode != 0:
                raise RuntimeError(r.stderr)
        else:
            git("checkout", "-q", "-B", rama, punto, cwd=w)
            git("reset", "-q", "--hard", punto, cwd=w)
            git("clean", "-qfd", cwd=w)
        foto = git("rev-parse", "--verify", "-q", f"origin/{wip}", cwd=d).stdout.strip()
        if foto and git("rev-parse", f"{foto}^", cwd=d).stdout.strip() == git("rev-parse", "HEAD", cwd=w).stdout.strip():
            git("checkout", foto, "--", ".", cwd=w)        # lo que quedó a medias vuelve a la carpeta
            git("reset", "-q", cwd=w)                       # sin commitear, como lo dejó el agente
        return w

    def copia(self, repo: str, sha: str, donde: Path) -> Path:
        """Copia limpia y desconectada de un SHA (verificar, revisar, desplegar)."""
        d = self.espejo(repo)
        if donde.exists():
            git("worktree", "remove", "--force", str(donde), cwd=d)
        git("worktree", "prune", cwd=d)
        donde.parent.mkdir(parents=True, exist_ok=True)
        r = git("worktree", "add", "-q", "--detach", str(donde), sha, cwd=d)
        if r.returncode != 0:
            raise RuntimeError(r.stderr)
        return donde
