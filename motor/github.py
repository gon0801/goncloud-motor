"""GitHub detrás de una interfaz de 5 métodos. Nadie fuera de aquí ve JSON de GitHub.

- `GhGitHub`: el real, con `gh`. Checks requeridos = protección de la rama base ∪ los extra del
  `.motor.toml` del repo. Merge con `--match-head-commit`: si la punta se movió, GitHub lo rechaza.
- `FakeGitHub`: igual de estricto, sobre repos locales, para probar sin red.

Flujo que el motor usa en cualquier repo: PR en borrador -> (revisión) -> listo -> esperar los
checks del SHA exacto -> merge con ese SHA.
"""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Protocol

from .workspace import git


@dataclass(frozen=True)
class PrView:
    numero: int
    rama: str
    base: str
    borrador: bool
    head: str
    estado: str            # abierto | mergeado | cerrado
    merge_commit: str = ""


@dataclass(frozen=True)
class ChecksVerdict:
    sha: str
    terminal: bool         # todos los requeridos terminaron
    green: bool
    faltan: tuple[str, ...]
    fallas: tuple[str, ...]


class MergeRechazado(RuntimeError):
    pass


class GitHubPort(Protocol):
    def abrir_pr(self, repo: str, rama: str, base: str, titulo: str, cuerpo: str) -> PrView: ...
    def marcar_listo(self, repo: str, numero: int) -> None: ...
    def checks(self, repo: str, numero: int, sha: str, extra: tuple[str, ...]) -> ChecksVerdict: ...
    def merge(self, repo: str, numero: int, sha: str, metodo: str, mensaje: str) -> PrView: ...
    def ver(self, repo: str, numero: int) -> PrView: ...


# --------------------------------------------------------------------------- real

class GhGitHub:
    """Contra GitHub de verdad. Se prueba con la suite de contrato solo si existe
    MOTOR_GH_SANDBOX=<dueño/repo> (un repo desechable)."""

    def __init__(self, env: dict | None = None):
        self.env = {**os.environ, **(env or {})}

    def _gh(self, *a: str) -> str:
        r = subprocess.run(["gh", *a], capture_output=True, text=True, env=self.env, stdin=subprocess.DEVNULL)
        if r.returncode != 0:
            raise RuntimeError(f"gh {' '.join(a[:3])}: {r.stderr[-300:]}")
        return r.stdout

    @staticmethod
    def nombre(repo: str) -> str:
        r = repo.removesuffix(".git")
        return r.split("github.com/")[-1] if "github.com/" in r else r.split(":")[-1]

    def _vista(self, repo: str, numero: int) -> PrView:
        d = json.loads(self._gh("pr", "view", str(numero), "-R", self.nombre(repo), "--json",
                                "number,headRefName,baseRefName,isDraft,headRefOid,state,mergeCommit"))
        estado = {"OPEN": "abierto", "MERGED": "mergeado"}.get(d["state"], "cerrado")
        return PrView(d["number"], d["headRefName"], d["baseRefName"], d["isDraft"], d["headRefOid"], estado,
                      (d.get("mergeCommit") or {}).get("oid", ""))

    def abrir_pr(self, repo, rama, base, titulo, cuerpo):
        n = self.nombre(repo)
        existentes = json.loads(self._gh("pr", "list", "-R", n, "--head", rama, "--state", "all", "--json", "number"))
        if existentes:
            return self._vista(repo, existentes[0]["number"])
        url = self._gh("pr", "create", "-R", n, "--draft", "--head", rama, "--base", base,
                       "--title", titulo, "--body", cuerpo).strip()
        return self._vista(repo, int(url.rstrip("/").split("/")[-1]))

    def marcar_listo(self, repo, numero):
        if self._vista(repo, numero).borrador:
            self._gh("pr", "ready", str(numero), "-R", self.nombre(repo))

    def checks(self, repo, numero, sha, extra):
        n = self.nombre(repo)
        pr = self._vista(repo, numero)
        try:
            req = json.loads(self._gh("api", f"repos/{n}/branches/{pr.base}/protection/required_status_checks",
                                      "--jq", ".contexts"))
        except RuntimeError:
            req = []
        requeridos = tuple(dict.fromkeys([*req, *extra]))
        runs = json.loads(self._gh("api", f"repos/{n}/commits/{sha}/check-runs", "--jq",
                                   "[.check_runs[]|{name, status, conclusion}]"))
        por_nombre = {r["name"]: r for r in runs}
        faltan = tuple(c for c in requeridos if c not in por_nombre or por_nombre[c]["status"] != "completed")
        fallas = tuple(c for c in requeridos if c in por_nombre and por_nombre[c]["status"] == "completed"
                       and por_nombre[c]["conclusion"] not in ("success", "skipped", "neutral"))
        return ChecksVerdict(sha, terminal=not faltan or bool(fallas), green=not faltan and not fallas,
                             faltan=faltan, fallas=fallas)

    def merge(self, repo, numero, sha, metodo, mensaje):
        try:
            self._gh("pr", "merge", str(numero), "-R", self.nombre(repo), f"--{metodo}",
                     "--match-head-commit", sha, "--subject", mensaje.splitlines()[0])
        except RuntimeError as e:
            raise MergeRechazado(str(e)) from None
        v = self._vista(repo, numero)
        if v.estado != "mergeado":
            raise MergeRechazado("GitHub no lo marcó como mergeado")
        return v

    def ver(self, repo, numero):
        return self._vista(repo, numero)


# --------------------------------------------------------------------------- falso

class FakeGitHub:
    """Estado en `<dir>/github.json`. La "protección" y los checks salen de `<dir>/repos.json`:
    {"<repo>": {"requeridos": ["ci"], "solo_listo": ["completa"], "checks": {"ci": "./ci.sh"}}}.
    Los checks corren el comando en una copia limpia del SHA; los de `solo_listo` no corren en borrador."""

    def __init__(self, d: Path):
        self.d = d
        d.mkdir(parents=True, exist_ok=True)

    def _cargar(self) -> dict:
        p = self.d / "github.json"
        return json.loads(p.read_text()) if p.exists() else {"prs": {}, "checks": {}}

    def _guardar(self, s: dict) -> None:
        tmp = self.d / "github.json.tmp"
        tmp.write_text(json.dumps(s, indent=1))
        os.replace(tmp, self.d / "github.json")

    def _cfg(self, repo: str) -> dict:
        p = self.d / "repos.json"
        return json.loads(p.read_text()).get(repo, {}) if p.exists() else {}

    def _punta(self, repo: str, rama: str) -> str:
        r = git("ls-remote", repo, f"refs/heads/{rama}")
        return r.stdout.split()[0] if r.stdout.strip() else ""

    def _vista(self, repo: str, pr: dict) -> PrView:
        head = pr["head_merge"] if pr["estado"] == "mergeado" else self._punta(repo, pr["rama"])
        return PrView(pr["numero"], pr["rama"], pr["base"], pr["borrador"], head, pr["estado"], pr.get("merge_commit", ""))

    def abrir_pr(self, repo, rama, base, titulo, cuerpo):
        s = self._cargar()
        k = f"{repo}#{rama}"
        if k not in s["prs"]:
            s["prs"][k] = {"numero": len(s["prs"]) + 1, "rama": rama, "base": base, "borrador": True,
                           "estado": "abierto", "titulo": titulo}
            self._guardar(s)
        return self._vista(repo, s["prs"][k])

    def _pr(self, s: dict, repo: str, numero: int) -> dict:
        for k, pr in s["prs"].items():
            if k.startswith(repo + "#") and pr["numero"] == numero:
                return pr
        raise KeyError(numero)

    def marcar_listo(self, repo, numero):
        s = self._cargar()
        self._pr(s, repo, numero)["borrador"] = False
        self._guardar(s)

    def _correr(self, repo: str, sha: str, cmd: str) -> bool:
        with tempfile.TemporaryDirectory() as t:
            w = Path(t) / "w"
            git("clone", "-q", repo, str(w))
            git("checkout", "-q", sha, cwd=w)
            return subprocess.run(cmd, shell=True, cwd=w, capture_output=True, stdin=subprocess.DEVNULL).returncode == 0

    def checks(self, repo, numero, sha, extra):
        s = self._cargar()
        pr = self._pr(s, repo, numero)
        cfg = self._cfg(repo)
        requeridos = tuple(dict.fromkeys([*cfg.get("requeridos", []), *extra]))
        faltan, fallas = [], []
        for c in requeridos:
            if c in cfg.get("solo_listo", []) and pr["borrador"]:
                faltan.append(c)
                continue
            if c not in cfg.get("checks", {}):
                faltan.append(c)   # nadie lo corre: nunca termina (como en GitHub)
                continue
            k = f"{repo}@{sha}#{c}"
            if k not in s["checks"]:
                s["checks"][k] = self._correr(repo, sha, cfg["checks"][c])
            if not s["checks"][k]:
                fallas.append(c)
        self._guardar(s)
        return ChecksVerdict(sha, terminal=not faltan or bool(fallas), green=not faltan and not fallas,
                             faltan=tuple(faltan), fallas=tuple(fallas))

    def merge(self, repo, numero, sha, metodo, mensaje):
        s = self._cargar()
        pr = self._pr(s, repo, numero)
        if pr["estado"] == "mergeado":
            return self._vista(repo, pr)
        punta = self._punta(repo, pr["rama"])
        if punta != sha:
            raise MergeRechazado(f"la punta de la rama ({punta[:7]}) no es el SHA aprobado")
        if pr["borrador"]:
            raise MergeRechazado("el PR sigue en borrador")
        v = self.checks(repo, numero, sha, ())
        if not v.green:
            raise MergeRechazado(f"checks requeridos no están en verde: {v.faltan + v.fallas}")
        with tempfile.TemporaryDirectory() as t:
            w = Path(t) / "w"
            git("clone", "-q", "-b", pr["base"], repo, str(w))
            r = git("merge", "--squash", sha, cwd=w)
            if r.returncode != 0:
                raise MergeRechazado("conflicto con la rama base")
            git("-c", "user.name=github", "-c", "user.email=gh@local", "commit", "-q", "-m", mensaje, cwd=w)
            if git("push", "-q", "origin", pr["base"], cwd=w).returncode != 0:
                raise MergeRechazado("la rama base se movió; reintenta")
            commit = git("rev-parse", "HEAD", cwd=w).stdout.strip()
        s = self._cargar()
        pr = self._pr(s, repo, numero)
        pr.update(estado="mergeado", merge_commit=commit, head_merge=sha)
        self._guardar(s)
        return self._vista(repo, pr)

    def ver(self, repo, numero):
        s = self._cargar()
        return self._vista(repo, self._pr(s, repo, numero))


def para(maquina_github: str, env: dict | None = None) -> GitHubPort:
    if maquina_github.startswith("falso:"):
        return FakeGitHub(Path(maquina_github.split(":", 1)[1]).expanduser())
    return GhGitHub(env)


__all__ = ["PrView", "ChecksVerdict", "MergeRechazado", "GitHubPort", "GhGitHub", "FakeGitHub", "para", "asdict"]
