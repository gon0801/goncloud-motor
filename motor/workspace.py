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


def git(
    *a: str, cwd: Path | None = None, env: dict | None = None
) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *a],
        cwd=cwd,
        capture_output=True,
        text=True,
        errors="replace",
        stdin=subprocess.DEVNULL,
        env={**os.environ, **(env or {})},
    )


SECRETO = re.compile(
    r"(ghp_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|sk-[A-Za-z0-9-]{20,}|AKIA[0-9A-Z]{16}|"
    r"xox[abp]-[A-Za-z0-9-]{10,}|-----BEGIN [A-Z ]*PRIVATE KEY-----|"
    r"(?i:(api[_-]?key|token|secret|password|contraseña)\s*[:=]\s*['\"]?[A-Za-z0-9/+_.-]{16,}))"
)


def foto_wip(worktree: Path, rama_wip: str, local: bool = False) -> str:
    """Sube una foto del estado actual de la carpeta. Devuelve 'subida', 'guardada', 'igual', 'secreto'
    o 'error: ...'. Con `local` (repos públicos) la foto queda solo en el disco de esta máquina, en
    `refs/<rama_wip>`: sobrevive un apagón y nunca queda visible en GitHub."""
    head = git("rev-parse", "HEAD", cwd=worktree).stdout.strip()
    if not head:
        return "error: sin HEAD"
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        env = {"GIT_INDEX_FILE": str(Path(tmp) / "index")}
        git("read-tree", "HEAD", cwd=worktree, env=env)
        git("add", "-A", cwd=worktree, env=env)
        tree = git("write-tree", cwd=worktree, env=env).stdout.strip()
        if local:
            previa = git(
                "rev-parse", "-q", "--verify", f"refs/{rama_wip}", cwd=worktree
            ).stdout.split()
        else:
            previa = git(
                "ls-remote", "origin", f"refs/heads/{rama_wip}", cwd=worktree
            ).stdout.split()
        if (
            previa
            and git(
                "rev-parse", "-q", "--verify", f"{previa[0]}^{{tree}}", cwd=worktree
            ).stdout.strip()
            == tree
            and git(
                "rev-parse", "-q", "--verify", f"{previa[0]}^", cwd=worktree
            ).stdout.strip()
            == head
        ):
            return "igual"  # la foto anterior ya tiene exactamente esto
        diff = (
            ""
            if local
            else git("diff", "--cached", "HEAD", cwd=worktree, env=env).stdout
        )
        if SECRETO.search(diff):
            return "secreto"
        c = git(
            "-c",
            "user.name=motor",
            "-c",
            "user.email=motor@local",
            "commit-tree",
            tree,
            "-p",
            head,
            "-m",
            "foto del trabajo a medias (motor)",
            cwd=worktree,
            env=env,
        ).stdout.strip()
    if local:
        p = git("update-ref", f"refs/{rama_wip}", c, cwd=worktree)
        return "guardada" if p.returncode == 0 else f"error: {p.stderr[-200:]}"
    p = git(
        "push", "-q", "--force", "origin", f"{c}:refs/heads/{rama_wip}", cwd=worktree
    )
    return "subida" if p.returncode == 0 else f"error: {p.stderr[-200:]}"


class Workspace:
    def __init__(self, home: Path, maquina: str):
        self.home = home
        self.maquina = re.sub(r"[^a-z0-9]+", "-", maquina.lower()).strip(
            "-"
        )  # "Mac (prueba)" -> "mac-prueba"

    @staticmethod
    def slug(repo: str) -> str:
        nombre = re.sub(
            r"[^A-Za-z0-9]+",
            "-",
            repo.rstrip("/").removesuffix(".git").split("/")[-1].split(":")[-1],
        )
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
        git(
            "fetch",
            "-q",
            "--prune",
            "origin",
            "+refs/heads/*:refs/remotes/origin/*",
            cwd=d,
        )
        return d

    def punta(self, repo: str, rama: str) -> str:
        """SHA de la punta remota de la rama; "" solo si la rama no existe. Si el remoto no contesta
        sube RuntimeError: un remoto caído no es una rama inexistente."""
        r = git("ls-remote", repo, f"refs/heads/{rama}")
        if r.returncode != 0:
            raise RuntimeError(
                f"no se pudo consultar {repo}: {r.stderr.strip()[-300:]}"
            )
        return r.stdout.split()[0] if r.stdout.strip() else ""

    def leer_en_base(self, repo: str, base: str, ruta: str) -> tuple[str, str]:
        """(SHA de la base, contenido del archivo en ese SHA). Vacío si no existe."""
        d = self.espejo(repo)
        sha = git("rev-parse", f"origin/{base}", cwd=d).stdout.strip()
        r = git("show", f"{sha}:{ruta}", cwd=d)
        return sha, (r.stdout if r.returncode == 0 else "")

    def prepara(self, repo: str, plan: str, block: str, base: str) -> Path:
        """Carpeta del bloque en la punta de su rama (o de la base). Si hay una foto wip cuyo padre
        desciende de ese punto, la rama arranca en el padre de la foto y encima queda, sin commitear,
        el árbol de la foto: lo commiteado en local y lo borrado o a medias vuelven como estaban.
        Idempotente: llamarla de nuevo deja el mismo resultado."""
        d = self.espejo(repo)
        rama, wip = self.rama(plan, block), self.rama_wip(plan, block)
        w = self.home / "wt" / plan / block
        remota = git(
            "rev-parse", "--verify", "-q", f"origin/{rama}", cwd=d
        ).stdout.strip()
        punto = f"origin/{rama}" if remota else f"origin/{base}"
        foto = (
            git("rev-parse", "--verify", "-q", f"origin/{wip}", cwd=d).stdout.strip()
            or git("rev-parse", "--verify", "-q", f"refs/{wip}", cwd=d).stdout.strip()
        )
        padre = (
            git("rev-parse", "-q", "--verify", f"{foto}^", cwd=d).stdout.strip()
            if foto
            else ""
        )
        if padre and (
            git("merge-base", "--is-ancestor", punto, padre, cwd=d).returncode != 0
            or git("rev-parse", f"{foto}^{{tree}}", cwd=d).stdout
            == git("rev-parse", f"{punto}^{{tree}}", cwd=d).stdout
        ):
            padre = ""  # foto vieja, de otra historia o vacía: no se restaura
        arranque = padre or punto
        # la decisión ya está tomada; solo ahora se toca la carpeta
        if not w.exists():
            w.parent.mkdir(parents=True, exist_ok=True)
            git("worktree", "prune", cwd=d)
            r = git("worktree", "add", "-q", "-B", rama, str(w), arranque, cwd=d)
            if r.returncode != 0:
                raise RuntimeError(r.stderr)
        else:
            git("checkout", "-q", "-B", rama, arranque, cwd=w)
            git("reset", "-q", "--hard", arranque, cwd=w)
            git("clean", "-qfd", cwd=w)
        if padre:
            # read-tree -m -u pasa de HEAD al árbol de la foto: lo que el agente borró sigue borrado
            r = git("read-tree", "-m", "-u", "HEAD", foto, cwd=w)
            if r.returncode != 0:
                raise RuntimeError(
                    f"no se pudo restaurar la foto {foto[:7]}: {r.stderr[-300:]}"
                )
            git("reset", "-q", cwd=w)  # sin commitear, como lo dejó el agente
        elif remota:
            # Rama ya existente: se trae la base. Tras un merge (squash) lo viejo ya está en la base y el
            # arreglo choca si no; MOTOR 01, bloque F. Si hay conflicto, se deja como estaba y el
            # agente lo resuelve con el motivo que le da el motor.
            r = git(
                "-c",
                "user.name=motor",
                "-c",
                "user.email=motor@local",
                "merge",
                "-q",
                "--no-edit",
                f"origin/{base}",
                cwd=w,
            )
            if r.returncode != 0:
                git("merge", "--abort", cwd=w)
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
