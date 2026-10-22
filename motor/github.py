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
import time
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
    estado: str  # abierto | mergeado | cerrado
    merge_commit: str = ""


@dataclass(frozen=True)
class ChecksVerdict:
    sha: str
    terminal: bool  # todos los requeridos terminaron
    green: bool
    faltan: tuple[str, ...]
    fallas: tuple[str, ...]
    github_termino: bool = (
        False  # GitHub ya no tiene nada corriendo ni en cola para este SHA
    )
    corridas: tuple = ()  # {"nombre", "run_id", "url", "status", "conclusion"} de cada check run
    corridas_fallidas: tuple = ()  # las corridas terminadas en rojo, con su url y run_id


def _run_id(r: dict) -> str:
    for k in ("details_url", "html_url", "url"):
        partes = str(r.get(k) or "").split("/actions/runs/")
        if len(partes) == 2 and partes[1].split("/")[0].isdigit():
            return partes[1].split("/")[0]
    rid = r.get("run_id") or r.get("id")
    return str(rid) if rid else ""


def _corrida(r: dict) -> dict:
    return {
        "nombre": r.get("name", ""),
        "run_id": _run_id(r),
        "url": r.get("html_url") or r.get("details_url") or "",
        "status": r.get("status", ""),
        "conclusion": r.get("conclusion"),
    }


def veredicto(
    sha: str,
    requeridos: tuple[str, ...],
    runs: list[dict],
    listo_desde: str | None = None,
    estados: list[dict] | None = None,
    relanzadas: tuple[str, ...] = (),
) -> ChecksVerdict:
    """Un check requerido "completa" cubre también una matriz "completa (1, 6)" … "completa (6, 6)":
    está terminado cuando todas sus partes terminaron, y verde cuando todas salieron bien.

    Un check saltado cuenta solo si se saltó después de que el PR dejó el borrador (`listo_desde`,
    hora ISO de GitHub; "" si nunca fue borrador; None si sigue en borrador). El salto del borrador
    no prueba nada (MOTOR 01, PR #484); el salto a propósito de un PR listo sí (Orbit salta
    `completa` si no hay código: INGESTA 01 esperó horas). Una corrida cuyo `run_id` ya se
    relanzó por saltada (`relanzadas`) siempre cuenta como salto del borrador: `gh run rerun`
    repite el evento original, con el PR en borrador, así que GitHub la vuelve a saltar aunque
    su `completed_at` quede después de `listo_desde`."""
    estados = estados or []
    relanzadas_set = {str(r) for r in relanzadas}
    corridas = tuple(_corrida(r) for r in runs)
    fallidas = tuple(
        c
        for c, r in zip(corridas, runs)
        if r.get("status") == "completed"
        and r.get("conclusion") not in ("success", "skipped", "neutral", None)
    )
    estados_malos = tuple(
        e.get("context", "") for e in estados if e.get("state") in ("failure", "error")
    )
    if not requeridos:
        efectivas = [
            r for r in runs if not _salto_del_borrador(r, listo_desde, relanzadas_set)
        ]
        corriendo = any(r.get("status") != "completed" for r in efectivas) or any(
            e.get("state") == "pending" for e in estados
        )
        hay = bool(efectivas or estados)
        fallas_sin = tuple(
            dict.fromkeys(
                [c["nombre"] for c in fallidas if c["nombre"]] + list(estados_malos)
            )
        )
        return ChecksVerdict(
            sha,
            terminal=hay and not corriendo,
            green=hay and not corriendo and not fallas_sin,
            faltan=(),
            fallas=fallas_sin,
            github_termino=hay and not corriendo,
            corridas=corridas,
            corridas_fallidas=fallidas,
        )
    faltan, fallas = [], []
    for c in requeridos:
        partes = [
            r
            for r in runs
            if (r["name"] == c or r["name"].startswith(c + " ("))
            and not _salto_del_borrador(r, listo_desde, relanzadas_set)
        ]
        if not partes:
            sts = [e for e in estados if e.get("context") == c]
            if sts and all(e.get("state") == "success" for e in sts):
                continue
            if sts and any(e.get("state") in ("failure", "error") for e in sts):
                fallas.append(c)
            else:
                faltan.append(c)
        elif any(r["status"] != "completed" for r in partes):
            faltan.append(c)
        elif any(
            r["conclusion"] not in ("success", "skipped", "neutral") for r in partes
        ):
            fallas.append(c)
    return ChecksVerdict(
        sha,
        terminal=not faltan or bool(fallas),
        green=not faltan and not fallas,
        faltan=tuple(faltan),
        fallas=tuple(fallas),
        github_termino=bool(runs or estados)
        and all(r["status"] == "completed" for r in runs)
        and all(e.get("state") != "pending" for e in estados),
        corridas=corridas,
        corridas_fallidas=fallidas,
    )


def _salto_del_borrador(
    run: dict,
    listo_desde: str | None,
    relanzadas: set[str] | frozenset[str] = frozenset(),
) -> bool:
    return run.get("conclusion") == "skipped" and (
        _run_id(run) in relanzadas
        or listo_desde is None
        or (listo_desde != "" and (run.get("completed_at") or "") <= listo_desde)
    )


class MergeRechazado(RuntimeError):
    pass


class PuntaMovida(MergeRechazado):
    """La punta de la rama del PR ya no es el SHA revisado."""


class Conflicto(MergeRechazado):
    """La rama choca con la base y no se puede crear el merge."""


class PoliticaBloquea(MergeRechazado):
    """La política de la rama (protección, revisiones, checks) no deja mergear."""


class Borrador(MergeRechazado):
    """El PR sigue en borrador."""


def clasificar_merge(error: BaseException) -> MergeRechazado:
    """El texto de GitHub se lee solo aquí, en la frontera: fuera solo viajan tipos."""
    if isinstance(error, (PuntaMovida, Conflicto, PoliticaBloquea, Borrador)):
        return error  # type: ignore[return-value]
    texto = str(error)
    bajo = texto.lower()
    if "draft" in bajo or "borrador" in bajo:
        return Borrador(texto)
    if any(
        x in bajo
        for x in (
            "head branch was modified",
            "does not match",
            "match-head",
            "expected head",
            "head commit",
        )
    ):
        return PuntaMovida(texto)
    if any(x in bajo for x in ("cannot be cleanly created", "conflict", "conflicto")):
        return Conflicto(texto)
    return PoliticaBloquea(texto)


class GitHubPort(Protocol):
    def abrir_pr(
        self, repo: str, rama: str, base: str, titulo: str, cuerpo: str
    ) -> PrView: ...
    def marcar_listo(self, repo: str, numero: int) -> None: ...
    def checks(
        self,
        repo: str,
        numero: int,
        sha: str,
        extra: tuple[str, ...],
        relanzadas: tuple[str, ...] = (),
    ) -> ChecksVerdict: ...
    def merge(
        self, repo: str, numero: int, sha: str, metodo: str, mensaje: str
    ) -> PrView: ...
    def ver(self, repo: str, numero: int) -> PrView: ...
    def es_privado(self, repo: str) -> bool: ...
    def relanzar_corrida(self, repo: str, run_id: str) -> None: ...
    def relanzar_fallidas(self, repo: str, run_id: str) -> None: ...
    def log_fallido(self, repo: str, run_id: str) -> str: ...


# --------------------------------------------------------------------------- real

AUN_NO_LLEGA = (
    "No commits between",
    "Head sha can't be blank",
    "not all refs are readable",
)
REINTENTOS_PR = 6
ESPERA_PR_S = 3.0


class GhGitHub:
    """Contra GitHub de verdad. Se prueba con la suite de contrato solo si existe
    MOTOR_GH_SANDBOX=<dueño/repo> (un repo desechable)."""

    def __init__(self, env: dict | None = None):
        self.env = {**os.environ, **(env or {})}
        self.dormir = time.sleep  # se cambia en las pruebas

    def _gh(self, *a: str) -> str:
        r = subprocess.run(
            ["gh", *a],
            check=False,
            capture_output=True,
            text=True,
            errors="replace",
            env={**self.env, "GIT_TERMINAL_PROMPT": "0"},
            stdin=subprocess.DEVNULL,
            timeout=180 if {"clone", "fetch", "push"} & set(a) else 60,
        )
        if r.returncode != 0:
            raise RuntimeError(f"gh {' '.join(a[:3])}: {r.stderr[-300:]}")
        return r.stdout

    @staticmethod
    def nombre(repo: str) -> str:
        r = repo.removesuffix(".git")
        return r.split("github.com/")[-1] if "github.com/" in r else r.split(":")[-1]

    def _vista(self, repo: str, numero: int) -> PrView:
        d = json.loads(
            self._gh(
                "pr",
                "view",
                str(numero),
                "-R",
                self.nombre(repo),
                "--json",
                "number,headRefName,baseRefName,isDraft,headRefOid,state,mergeCommit",
            )
        )
        estado = {"OPEN": "abierto", "MERGED": "mergeado"}.get(d["state"], "cerrado")
        return PrView(
            d["number"],
            d["headRefName"],
            d["baseRefName"],
            d["isDraft"],
            d["headRefOid"],
            estado,
            (d.get("mergeCommit") or {}).get("oid", ""),
        )

    def abrir_pr(self, repo, rama, base, titulo, cuerpo):
        n = self.nombre(repo)
        existentes = json.loads(
            self._gh(
                "pr",
                "list",
                "-R",
                n,
                "--head",
                rama,
                "--state",
                "open",  # un PR ya mergeado o cerrado no se reusa: el arreglo siguiente va en uno nuevo
                "--json",
                "number",
            )
        )
        if existentes:
            return self._vista(repo, existentes[0]["number"])
        for intento in range(REINTENTOS_PR):
            try:
                url = self._gh(
                    "pr",
                    "create",
                    "-R",
                    n,
                    "--draft",
                    "--head",
                    rama,
                    "--base",
                    base,
                    "--title",
                    titulo,
                    "--body",
                    cuerpo,
                ).strip()
                break
            except RuntimeError as e:
                # Recién empujada, la rama tarda unos segundos en verse del lado de GitHub.
                if intento == REINTENTOS_PR - 1 or not any(
                    x in str(e) for x in AUN_NO_LLEGA
                ):
                    raise
                self.dormir(ESPERA_PR_S)
        return self._vista(repo, int(url.rstrip("/").split("/")[-1]))

    def marcar_listo(self, repo, numero):
        if self._vista(repo, numero).borrador:
            self._gh("pr", "ready", str(numero), "-R", self.nombre(repo))

    @staticmethod
    def _paginas(texto: str) -> list:
        out, dec, i = [], json.JSONDecoder(), 0
        texto = texto.strip()
        while i < len(texto):
            while i < len(texto) and texto[i].isspace():
                i += 1
            if i >= len(texto):
                break
            obj, j = dec.raw_decode(texto, i)
            out.append(obj)
            i = j
        return out

    def _es_404(self, e: RuntimeError) -> bool:
        t = str(e)
        return "HTTP 404" in t or " 404" in t

    @staticmethod
    def _sin_proteccion(e: RuntimeError) -> bool:
        return "Branch not protected" in str(e)

    def checks(self, repo, numero, sha, extra, relanzadas=()):
        n = self.nombre(repo)
        pr = self._vista(repo, numero)
        try:
            req = json.loads(
                self._gh(
                    "api",
                    f"repos/{n}/branches/{pr.base}/protection/required_status_checks",
                    "--jq",
                    ".contexts",
                )
            )
        except RuntimeError as e:
            if not self._sin_proteccion(e):
                raise
            req = []
        try:
            reglas = self._paginas(
                self._gh(
                    "api",
                    f"repos/{n}/rules/branches/{pr.base}",
                    "--paginate",
                )
            )
        except RuntimeError as e:
            if not self._es_404(e):
                raise
            reglas = []
        for pagina in reglas:
            for regla in pagina if isinstance(pagina, list) else []:
                if regla.get("type") != "required_status_checks":
                    continue
                for c in (regla.get("parameters") or {}).get(
                    "required_status_checks", []
                ):
                    ctx = c.get("context") if isinstance(c, dict) else c
                    if ctx and ctx not in req:
                        req.append(ctx)
        requeridos = tuple(dict.fromkeys([*req, *extra]))
        runs: list[dict] = []
        for pagina in self._paginas(
            self._gh(
                "api",
                f"repos/{n}/commits/{sha}/check-runs?per_page=100",
                "--paginate",
                "--jq",
                "[.check_runs[]|{name, status, conclusion, completed_at, id, html_url, details_url}]",
            )
        ):
            runs.extend(pagina if isinstance(pagina, list) else [])
        estados: list[dict] = []
        for pagina in self._paginas(
            self._gh("api", f"repos/{n}/commits/{sha}/status", "--paginate")
        ):
            if isinstance(pagina, dict):
                estados.extend(pagina.get("statuses") or [])
        return veredicto(
            sha,
            requeridos,
            runs,
            None if pr.borrador else self._listo_desde(n, numero),
            estados,
            tuple(str(r) for r in relanzadas),
        )

    def relanzar_corrida(self, repo, run_id):
        if run_id:
            self._gh("run", "rerun", str(run_id), "-R", self.nombre(repo))

    def relanzar_fallidas(self, repo, run_id):
        if run_id:
            self._gh("run", "rerun", str(run_id), "--failed", "-R", self.nombre(repo))

    def log_fallido(self, repo, run_id) -> str:
        if not run_id:
            return ""
        try:
            texto = self._gh(
                "run", "view", str(run_id), "--log-failed", "-R", self.nombre(repo)
            )
        except RuntimeError:
            return ""
        return "\n".join(texto.splitlines()[-80:])

    def _listo_desde(self, n: str, numero: int) -> str:
        """Hora (de GitHub) en que el PR dejó el borrador por última vez; "" si nunca fue borrador."""
        horas = self._gh(
            "api",
            f"repos/{n}/issues/{numero}/events?per_page=100",
            "--paginate",
            "--jq",
            '.[]|select(.event=="ready_for_review")|.created_at',
        ).split()
        return horas[-1] if horas else ""

    def merge(self, repo, numero, sha, metodo, mensaje):
        try:
            self._gh(
                "pr",
                "merge",
                str(numero),
                "-R",
                self.nombre(repo),
                f"--{metodo}",
                "--match-head-commit",
                sha,
                "--subject",
                mensaje.splitlines()[0],
            )
        except RuntimeError as e:
            raise clasificar_merge(e) from None
        v = self._vista(repo, numero)
        if v.estado != "mergeado":
            raise PoliticaBloquea("GitHub no lo marcó como mergeado")
        return v

    def ver(self, repo, numero):
        return self._vista(repo, numero)

    def es_privado(self, repo):
        """Si no se puede saber, se trata como público: la foto se queda en la máquina."""
        try:
            return (
                json.loads(
                    self._gh("repo", "view", self.nombre(repo), "--json", "visibility")
                )["visibility"]
                != "PUBLIC"
            )
        except Exception:  # noqa: BLE001  cualquier fallo (red, gh, JSON) = no se sabe
            return False


# --------------------------------------------------------------------------- falso


class FakeGitHub:
    """Estado en `<dir>/github.json`. La "protección" y los checks salen de `<dir>/repos.json`:
    {"<repo>": {"requeridos": ["ci"], "solo_listo": ["completa"], "checks": {"ci": "./ci.sh"}, "privado": true}}.
    Los checks corren el comando en una copia limpia del SHA; los de `solo_listo` no corren en borrador."""

    def __init__(self, d: Path):
        self.d = d
        d.mkdir(parents=True, exist_ok=True)

    def _cargar(self) -> dict:
        p = self.d / "github.json"
        return (
            json.loads(p.read_text(encoding="utf-8"))
            if p.exists()
            else {"prs": {}, "checks": {}}
        )

    def _guardar(self, s: dict) -> None:
        tmp = self.d / "github.json.tmp"
        tmp.write_text(json.dumps(s, indent=1), encoding="utf-8")
        os.replace(tmp, self.d / "github.json")

    def _cfg(self, repo: str) -> dict:
        p = self.d / "repos.json"
        return (
            json.loads(p.read_text(encoding="utf-8")).get(repo, {})
            if p.exists()
            else {}
        )

    def _punta(self, repo: str, rama: str) -> str:
        r = git("ls-remote", repo, f"refs/heads/{rama}")
        return r.stdout.split()[0] if r.stdout.strip() else ""

    def es_privado(self, repo):
        return bool(self._cfg(repo).get("privado", True))

    def _vista(self, repo: str, pr: dict) -> PrView:
        head = (
            pr["head_merge"]
            if pr["estado"] == "mergeado"
            else self._punta(repo, pr["rama"])
        )
        return PrView(
            pr["numero"],
            pr["rama"],
            pr["base"],
            pr["borrador"],
            head,
            pr["estado"],
            pr.get("merge_commit", ""),
        )

    def abrir_pr(self, repo, rama, base, titulo, cuerpo):
        s = self._cargar()
        abiertos = [
            k
            for k, pr in s["prs"].items()
            if k.startswith(f"{repo}#{rama}#") and pr["estado"] == "abierto"
        ]
        k = abiertos[0] if abiertos else f"{repo}#{rama}#{len(s['prs']) + 1}"
        if k not in s["prs"]:
            s["prs"][k] = {
                "numero": len(s["prs"]) + 1,
                "rama": rama,
                "base": base,
                "borrador": True,
                "estado": "abierto",
                "titulo": titulo,
            }
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
            return (
                subprocess.run(
                    cmd,
                    shell=True,
                    check=False,
                    cwd=w,
                    capture_output=True,
                    stdin=subprocess.DEVNULL,
                ).returncode
                == 0
            )

    def checks(self, repo, numero, sha, extra, relanzadas=()):
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
                faltan.append(c)  # nadie lo corre: nunca termina (como en GitHub)
                continue
            k = f"{repo}@{sha}#{c}"
            if k not in s["checks"]:
                s["checks"][k] = self._correr(repo, sha, cfg["checks"][c])
            if not s["checks"][k]:
                fallas.append(c)
        self._guardar(s)
        corridas = tuple(
            {
                "nombre": c,
                "run_id": "",
                "url": "",
                "status": "completed",
                "conclusion": "failure" if c in fallas else "success",
            }
            for c in requeridos
            if c not in faltan
        )
        return ChecksVerdict(
            sha,
            terminal=not faltan or bool(fallas),
            green=not faltan and not fallas,
            faltan=tuple(faltan),
            fallas=tuple(fallas),
            github_termino=True,  # el falso corre todo en el acto
            corridas=corridas,
            corridas_fallidas=tuple(
                c for c in corridas if c["conclusion"] == "failure"
            ),
        )

    def relanzar_corrida(self, repo, run_id):
        s = self._cargar()
        s.setdefault("relanzadas", []).append(
            {"repo": repo, "run_id": str(run_id), "fallidas": False}
        )
        self._guardar(s)

    def relanzar_fallidas(self, repo, run_id):
        s = self._cargar()
        s.setdefault("relanzadas", []).append(
            {"repo": repo, "run_id": str(run_id), "fallidas": True}
        )
        self._guardar(s)

    def log_fallido(self, repo, run_id) -> str:
        return ""

    def merge(self, repo, numero, sha, metodo, mensaje):
        s = self._cargar()
        pr = self._pr(s, repo, numero)
        if pr["estado"] == "mergeado":
            return self._vista(repo, pr)
        punta = self._punta(repo, pr["rama"])
        if punta != sha:
            raise PuntaMovida(
                f"la punta de la rama ({punta[:7]}) no es el SHA aprobado"
            )
        if pr["borrador"]:
            raise Borrador("el PR sigue en borrador")
        v = self.checks(repo, numero, sha, ())
        if not v.green:
            raise PoliticaBloquea(
                f"checks requeridos no están en verde: {v.faltan + v.fallas}"
            )
        with tempfile.TemporaryDirectory() as t:
            w = Path(t) / "w"
            git("clone", "-q", "-b", pr["base"], repo, str(w))
            r = git("merge", "--squash", sha, cwd=w)
            if r.returncode != 0:
                raise Conflicto("conflicto con la rama base")
            git(
                "-c",
                "user.name=github",
                "-c",
                "user.email=gh@local",
                "commit",
                "-q",
                "-m",
                mensaje,
                cwd=w,
            )
            if git("push", "-q", "origin", pr["base"], cwd=w).returncode != 0:
                raise PuntaMovida("la rama base se movió; reintenta")
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


__all__ = [
    "Borrador",
    "ChecksVerdict",
    "Conflicto",
    "FakeGitHub",
    "GhGitHub",
    "GitHubPort",
    "MergeRechazado",
    "PoliticaBloquea",
    "PrView",
    "PuntaMovida",
    "asdict",
    "clasificar_merge",
    "para",
]
