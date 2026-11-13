"""GitHub detrás de una interfaz de 5 métodos. Nadie fuera de aquí ve JSON de GitHub.

- `GhGitHub`: el real, con `gh`. Los checks requeridos parten de la protección de la rama
  base ∪ los extra del `.motor.toml` del repo, y el veredicto los amplía con toda corrida
  y todo contexto de estado que corra en el SHA. Merge con `--match-head-commit`: si la
  punta se movió, GitHub lo rechaza.
- `FakeGitHub`: igual de estricto, sobre repos locales, para probar sin red.

Flujo que el motor usa en cualquier repo: PR en borrador -> (revisión) -> listo -> esperar los
checks del SHA exacto -> merge con ese SHA.
"""

from __future__ import annotations
from motor.io import escribir_texto, leer_json

import json
import os
import re
import subprocess
import tempfile
import time
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable, Protocol

from .repositorios import resolver_repositorio
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
    cuerpo: str = ""


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
    # Requeridos que faltan y están en marcha o en cola: una corrida sin completar o un
    # estado legacy «pending». Uno que nadie corre está en `faltan` pero no aquí.
    en_marcha: tuple[str, ...] = ()



@dataclass(frozen=True)
class EstadoMerge:
    """Si el PR se puede mezclar con su base y si quedó atrás.

    `estado` es «limpio», «atras», «choca» o «desconocido» cuando GitHub aún
    no calculó el resultado. Los dos nombres crudos se conservan para la
    evidencia; nadie fuera de esta frontera lee los valores de GitHub.
    """

    estado: str
    mergeable: str = ""
    merge_state_status: str = ""


_REINTENTOS_MERGEABLE = 6
_ESPERA_MERGEABLE_S = 3.0


def _estado_merge_desde(mergeable: str, merge_state_status: str) -> EstadoMerge:
    mergeable = str(mergeable or "").upper()
    merge_state_status = str(merge_state_status or "").upper()
    if mergeable == "CONFLICTING" or merge_state_status in ("DIRTY", "CONFLICTING"):
        return EstadoMerge("choca", mergeable, merge_state_status)
    if merge_state_status == "BEHIND":
        return EstadoMerge("atras", mergeable, merge_state_status)
    if mergeable == "UNKNOWN" or merge_state_status == "UNKNOWN" or not mergeable:
        return EstadoMerge("desconocido", mergeable, merge_state_status)
    return EstadoMerge("limpio", mergeable, merge_state_status)


@dataclass(frozen=True)
class CheckRequerido:
    nombre: str
    app: str | None = None

@dataclass(frozen=True)
class PoliticaConocida:
    requeridos: tuple


def _contextos(raw) -> list[str]:
    if isinstance(raw, list):
        nombres = []
        for c in raw:
            if isinstance(c, str) and c:
                nombres.append(c)
            elif isinstance(c, dict) and c.get("context"):
                nombres.append(str(c["context"]))
        return nombres
    if isinstance(raw, dict):
        if isinstance(raw.get("contexts"), list):
            return _contextos(raw["contexts"])
        return _contextos(list(raw.get("checks") or []))
    return []


_MARCA_SHA = re.compile(r"ai-review:sha=([0-9a-fA-F]{7,64})")


def _run_id(r: dict) -> str:
    for k in ("details_url", "html_url", "url"):
        partes = str(r.get(k) or "").split("/actions/runs/")
        if len(partes) == 2 and partes[1].split("/")[0].isdigit():
            return partes[1].split("/")[0]
    rid = r.get("run_id")
    return str(rid) if rid else ""


_CONCLUSIONES_ROJAS = ("failure", "timed_out", "startup_failure")
_CONCLUSIONES_SIN_VEREDICTO = ("cancelled", "action_required", "stale")


def _corrida(r: dict) -> dict:
    # `corridas[i]["run_id"]` alimenta el relanzamiento de checks saltados en
    # borrador (`_relanza_saltada`). Las conclusiones completadas sin veredicto
    # (`cancelled`, `action_required`, `stale`) quedan en `faltan`, pero no son
    # saltadas del borrador: no deben exponer `run_id` a `_relanza_saltada`.
    # `run_id_corrida` conserva el id real en todos los casos: el encargo lo
    # usa para el único `rerun --failed` de un rojo de tubería, que también
    # incluye cancelaciones.
    return {
        "nombre": r.get("name", ""),
        "run_id": ""
        if r.get("conclusion") in _CONCLUSIONES_SIN_VEREDICTO
        else _run_id(r),
        "run_id_corrida": _run_id(r),
        # El ID del check-run de un job de Actions es el databaseId del job:
        # es lo que `gh run view --job` espera (h_a8968fb0).
        "job_id": str(r.get("id") or ""),
        "url": r.get("html_url") or r.get("details_url") or "",
        "status": r.get("status", ""),
        "conclusion": r.get("conclusion"),
        "completed_at": str(r.get("completed_at") or ""),
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

    No solo los requeridos iniciales cuentan: toda corrida que corre en el SHA entra al veredicto
    (enc_25d22b9bfce242f9 cerró terminado con `windows` en rojo porque nadie lo tenía
    como requerido), y también cada contexto de estado del SHA que no esté todo en
    `success` (h_f05e0491: un `external-ci` en failure/error/pending con corridas
    verdes sigue siendo rojo o pendiente). Corridas y estados del mismo nombre se
    evalúan los dos: un check-run en verde no esconde a un estado del mismo contexto
    en rojo o pendiente (h_f05e0491, segunda ronda). Un check se ignora por su
    conclusión, no por su nombre: salto del borrador, `skipped` o `neutral` no
    cuentan; lo que corre o falla sí. Solo `failure`, `timed_out` y
    `startup_failure` cuentan como rojo (`fallas`); otras conclusiones
    completadas no verdes (`cancelled`, `action_required`, `stale`) quedan en
    `faltan` sin abrir el cierre en verde.

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
        and r.get("conclusion") in _CONCLUSIONES_ROJAS
    )
    corren = []
    for r in runs:
        nombre = str(r.get("name") or "")
        if (
            not nombre
            or _salto_del_borrador(r, listo_desde, relanzadas_set)
            or (
                r.get("status") == "completed"
                and r.get("conclusion") in ("skipped", "neutral")
            )
            or any(nombre == c or nombre.startswith(c + " (") for c in requeridos)
        ):
            continue
        corren.append(nombre)
    for ctx in {str(e.get("context") or "") for e in estados}:
        if not ctx:
            continue
        sts = [e for e in estados if str(e.get("context") or "") == ctx]
        if all(e.get("state") == "success" for e in sts):
            continue
        corren.append(ctx)
    requeridos = tuple(dict.fromkeys([*requeridos, *sorted(set(corren))]))
    if not requeridos:
        return ChecksVerdict(
            sha,
            terminal=True,
            green=True,
            faltan=(),
            fallas=(),
            github_termino=True,
            corridas=corridas,
            corridas_fallidas=(),
        )
    faltan, fallas, en_marcha = [], [], []
    for c in requeridos:
        partes = [
            r
            for r in runs
            if (r["name"] == c or r["name"].startswith(c + " ("))
            and not _salto_del_borrador(r, listo_desde, relanzadas_set)
        ]
        sts = [e for e in estados if str(e.get("context") or "") == c]
        if partes:
            if any(
                r["status"] != "completed" or r.get("conclusion") is None
                for r in partes
            ):
                faltan.append(c)
                en_marcha.append(c)
                continue
            if any(
                r["conclusion"] in _CONCLUSIONES_ROJAS
                for r in partes
            ):
                fallas.append(c)
                continue
        # Aquí las corridas del nombre no están en marcha ni en rojo: un estado
        # del mismo contexto no queda escondido por ellas (h_f05e0491); los dos
        # resultados se evalúan y el peor manda. Una conclusión completada que
        # no sea verde ni roja (cancelled, action_required, stale) tampoco
        # cuenta como verde: queda en `faltan` sin marcar falla.
        if sts and any(e.get("state") in ("failure", "error") for e in sts):
            fallas.append(c)
        elif sts and not all(e.get("state") == "success" for e in sts):
            faltan.append(c)
            if any(e.get("state") == "pending" for e in sts):
                en_marcha.append(c)
        elif not partes and not sts:
            faltan.append(c)
        elif any(
            r["conclusion"] not in ("success", "skipped", "neutral")
            for r in partes
        ):
            faltan.append(c)
    faltan_set = set(faltan)
    vistos_faltan: set[str] = set()
    corridas_dedup = []
    for c in corridas:
        rid = c["run_id"]
        if rid and c["nombre"] in faltan_set:
            if rid in vistos_faltan or c.get("conclusion") != "skipped":
                c = {**c, "run_id": ""}
            else:
                vistos_faltan.add(rid)
        corridas_dedup.append(c)
    corridas = tuple(corridas_dedup)
    return ChecksVerdict(
        sha,
        terminal=not faltan or bool(fallas),
        green=not faltan and not fallas,
        faltan=tuple(faltan),
        fallas=tuple(fallas),
        github_termino=bool(runs or estados)
        and all(
            r["status"] == "completed"
            and r.get("conclusion") is not None
            and r.get("conclusion") != "action_required"
            for r in runs
        )
        and all(e.get("state") != "pending" for e in estados),
        corridas=corridas,
        corridas_fallidas=fallidas,
        en_marcha=tuple(en_marcha),
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


class BaseMovida(MergeRechazado):
    """La base cambió mientras GitHub mergeaba: el mismo merge se puede repetir."""


_RECHAZOS_DE_MERGE = (
    (Borrador, ("draft", "borrador")),
    (PuntaMovida, ("head branch was modified", "does not match", "match-head",
                   "expected head", "head commit")),
    (BaseMovida, ("base branch was modified",)),
    (Conflicto, ("cannot be cleanly created", "conflict", "conflicto")),
)


def clasificar_merge(error: BaseException) -> MergeRechazado:
    """El texto de GitHub se lee solo aquí, en la frontera: fuera solo viajan tipos."""
    if isinstance(error, MergeRechazado):
        return error
    texto = str(error)
    for tipo, marcas in _RECHAZOS_DE_MERGE:
        if any(marca in texto.lower() for marca in marcas):
            return tipo(texto)
    return PoliticaBloquea(texto)


def _causa_corta(e: BaseException) -> str:
    return " ".join(str(e).split())[:300]


@dataclass(frozen=True)
class CapacidadRemota:
    """Lo que una mirada de solo lectura al remoto ya deja comprobado.

    `integrar_bloqueado`: True solo con una regla fija y legible que impide
    mergear (revisiones humanas requeridas o el actor sin permiso de push);
    False cuando todas las fuentes consultadas salieron limpias; None cuando
    algo quedó sin leer (red, 403 ambiguo, respuesta ilegible). None nunca
    baja un nivel: produce espera o reintento en el flujo normal.
    """

    repo_ok: bool
    base_ok: bool | None
    integrar_bloqueado: bool | None
    detalle: str = ""


class GitHubPort(Protocol):
    def abrir_pr(
        self, repo: str, rama: str, base: str, titulo: str, cuerpo: str
    ) -> PrView: ...
    def marcar_listo(self, repo: str, numero: int) -> None: ...
    def cambiar_base(self, repo: str, numero: int, base: str) -> None: ...
    def actualizar_cuerpo(self, repo: str, numero: int, cuerpo: str) -> None: ...
    def checks(
        self,
        repo: str,
        numero: int,
        sha: str,
        extra: tuple[str, ...],
        relanzadas: tuple[str, ...] = (),
        *, base_politica: str = "",
    ) -> ChecksVerdict: ...
    def checks_requeridos(self, repo: str, numero: int, sha: str) -> ChecksVerdict: ...
    def estado_merge(self, repo: str, numero: int) -> EstadoMerge: ...
    def merge(
        self, repo: str, numero: int, sha: str, metodo: str, mensaje: str
    ) -> PrView: ...
    def ver(self, repo: str, numero: int) -> PrView: ...
    def es_privado(self, repo: str) -> bool: ...
    def relanzar_corrida(self, repo: str, run_id: str) -> None: ...
    def relanzar_fallidas(self, repo: str, run_id: str) -> None: ...
    def log_fallido(self, repo: str, run_id: str) -> str: ...
    def log_fallido_job(
        self, repo: str, run_id: str, job: str, job_id: str = ""
    ) -> str: ...
    def comentarios_deepseek(
        self, repo: str, numero: int, sha: str = ""
    ) -> list[dict]: ...
    def disparar_workflow(
        self, repo: str, workflow: str, inputs: dict
    ) -> None: ...
    def capacidad(self, repo: str, base: str) -> CapacidadRemota: ...


# --------------------------------------------------------------------------- real

AUN_NO_LLEGA = (
    "No commits between",
    "Head sha can't be blank",
    "not all refs are readable",
)
REINTENTOS_PR = 6
ESPERA_PR_S = 3.0


@contextmanager
def _cuerpo_en_archivo(cuerpo: str):
    """El cuerpo viaja a `gh` en un archivo, no como un argv gigante."""
    archivo = tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", newline="", suffix=".md", delete=False
    )
    try:
        with archivo:
            archivo.write(cuerpo)
        yield archivo.name
    finally:
        try:
            os.unlink(archivo.name)
        except OSError:
            pass


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
        nombre = resolver_repositorio(repo).github
        if nombre is None:
            raise ValueError("el repositorio local no tiene un remoto GitHub configurado")
        return nombre

    def _vista(self, repo: str, numero: int) -> PrView:
        d = json.loads(
            self._gh(
                "pr",
                "view",
                str(numero),
                "-R",
                self.nombre(repo),
                "--json",
                "number,headRefName,baseRefName,isDraft,headRefOid,state,mergeCommit,body",
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
            str(d.get("body") or ""),
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
            numero = existentes[0]["number"]
            vista = self._vista(repo, numero)
            if vista.base != base:
                self.cambiar_base(repo, numero, base)
                vista = self._vista(repo, numero)
            return vista
        with _cuerpo_en_archivo(cuerpo) as ruta_cuerpo:
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
                        "--body-file",
                        ruta_cuerpo,
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

    def cambiar_base(self, repo, numero, base):
        if self._vista(repo, numero).base != base:
            self._gh(
                "pr", "edit", str(numero), "--base", base,
                "-R", self.nombre(repo),
            )

    def actualizar_cuerpo(self, repo, numero, cuerpo):
        with _cuerpo_en_archivo(cuerpo) as ruta_cuerpo:
            self._gh(
                "pr", "edit", str(numero), "--body-file", ruta_cuerpo,
                "-R", self.nombre(repo),
            )

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

    @staticmethod
    def _sin_funcion_en_el_plan(e: RuntimeError) -> bool:
        # Repo privado en el plan gratis: GitHub no ofrece protección ni reglas (HTTP 403).
        return "Upgrade to GitHub Pro" in str(e)

    def checks(self, repo, numero, sha, extra, relanzadas=(), *, base_politica=""):
        n = self.nombre(repo)
        pr = self._vista(repo, numero)
        req = []
        for base in dict.fromkeys(b for b in (pr.base, base_politica) if b):
            try:
                protegidos = _contextos(json.loads(
                    self._gh(
                        "api",
                        f"repos/{n}/branches/{base}/protection/required_status_checks",
                        "--jq",
                        ".contexts",
                    )
                ))
            except RuntimeError as e:
                if not (self._sin_proteccion(e) or self._sin_funcion_en_el_plan(e)):
                    raise
                protegidos = []
            req.extend(c for c in protegidos if c not in req)
            try:
                reglas = self._paginas(
                    self._gh(
                        "api",
                        f"repos/{n}/rules/branches/{base}",
                        "--paginate",
                    )
                )
            except RuntimeError as e:
                if not self._sin_funcion_en_el_plan(e):
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
                "[.check_runs[]|{name, status, conclusion, completed_at, id, html_url, details_url, app_id: .app.id}]",
            )
        ):
            runs.extend(pagina if isinstance(pagina, list) else [])
        estados: list[dict] = []
        for pagina in self._paginas(
            self._gh("api", f"repos/{n}/commits/{sha}/status", "--paginate")
        ):
            if isinstance(pagina, dict):
                estados.extend(pagina.get("statuses") or [])
        politica = PoliticaConocida(tuple(CheckRequerido(nombre=n) for n in requeridos))
        return veredicto(
            sha,
            tuple(c.nombre for c in politica.requeridos),
            runs,
            None if pr.borrador else self._listo_desde(n, numero),
            estados,
            tuple(str(r) for r in relanzadas),
        )

    def checks_requeridos(self, repo, numero, sha):
        """Distingue CI en marcha de una política que requiere intervención.

        `checks` incluye opcionales; aquí manda la selección de requeridos de
        GitHub. Con `--json`, gh entrega los estados sin usar el exit code de CI.
        """
        n = self.nombre(repo)
        pr = json.loads(self._gh(
            "pr", "view", str(numero), "-R", n,
            "--json", "headRefOid,reviewDecision",
        ))
        if pr["headRefOid"] != sha:
            raise PuntaMovida("la punta del PR ya no es el SHA revisado")
        if pr.get("reviewDecision") in ("REVIEW_REQUIRED", "CHANGES_REQUESTED"):
            raise PoliticaBloquea("la política del PR requiere una revisión humana")
        try:
            checks = json.loads(self._gh(
                "pr", "checks", str(numero), "-R", n,
                "--required", "--json", "name,bucket,state",
            ))
        except RuntimeError as exc:
            if not any(t in str(exc) for t in (
                "no required checks reported", "no checks reported",
            )):
                raise
            checks = []
        faltan, fallas, en_marcha = [], [], []
        for check in checks:
            nombre, estado = check["name"], check["state"]
            if check["bucket"] in ("fail", "cancel"):
                fallas.append(nombre)
            elif check["bucket"] not in ("pass", "skipping"):
                faltan.append(nombre)
                if estado in ("IN_PROGRESS", "QUEUED", "PENDING", "REQUESTED", "WAITING"):
                    en_marcha.append(nombre)
        return ChecksVerdict(
            sha, terminal=not faltan or bool(fallas),
            green=not faltan and not fallas,
            faltan=tuple(faltan), fallas=tuple(fallas),
            en_marcha=tuple(en_marcha),
        )

    def estado_merge(self, repo, numero):
        """Lee si el PR choca con su base; GitHub tarda en calcularlo.

        `mergeable` nace en UNKNOWN tras abrir o mover el PR. Una respuesta
        todavía sin calcular se vuelve a pedir en el acto, como mucho
        `_REINTENTOS_MERGEABLE` veces, en vez de esperar al siguiente barrido.
        """
        n = self.nombre(repo)
        estado = EstadoMerge("desconocido")
        for intento in range(_REINTENTOS_MERGEABLE):
            datos = json.loads(
                self._gh(
                    "pr",
                    "view",
                    str(numero),
                    "-R",
                    n,
                    "--json",
                    "mergeable,mergeStateStatus",
                )
            )
            estado = _estado_merge_desde(
                str(datos.get("mergeable") or ""),
                str(datos.get("mergeStateStatus") or ""),
            )
            if estado.estado != "desconocido":
                return estado
            if intento < _REINTENTOS_MERGEABLE - 1:
                self.dormir(_ESPERA_MERGEABLE_S)
        return estado

    def relanzar_corrida(self, repo, run_id):
        if not run_id:
            return
        try:
            self._gh("run", "rerun", str(run_id), "-R", self.nombre(repo))
        except RuntimeError as e:
            if "already running" not in str(e).lower():
                raise

    def relanzar_fallidas(self, repo, run_id):
        if not run_id:
            return
        try:
            self._gh("run", "rerun", str(run_id), "--failed", "-R", self.nombre(repo))
        except RuntimeError as e:
            if "already running" not in str(e).lower():
                raise

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

    def log_fallido_job(self, repo, run_id, job, job_id="") -> str:
        """El log literal del job exacto de la corrida.
        `--job` pide el ID del job, no su nombre: con el nombre `gh` intenta
        GET /actions/jobs/<nombre> y falla (h_a8968fb0). El ID viene del
        check-run (`job_id`); si no lo hay, se resuelve con el nombre. El
        texto no se recorta: el literal completo es la evidencia del fallo y
        el recorte ciego de la cola puede borrar el error."""
        if not run_id or not job:
            return ""
        objetivo = str(job_id or "").strip()
        if not objetivo:
            objetivo = self._id_del_job(repo, run_id, job)
        if not objetivo:
            return ""
        try:
            return self._gh(
                "run", "view", str(run_id), "--job", objetivo, "--log",
                "-R", self.nombre(repo),
            )
        except RuntimeError:
            return ""

    def _id_del_job(self, repo, run_id, job) -> str:
        """Resuelve el ID del job por su nombre."""
        try:
            vista = json.loads(
                self._gh(
                    "run", "view", str(run_id), "--json", "jobs",
                    "-R", self.nombre(repo),
                )
            )
        except (RuntimeError, ValueError):
            return ""
        jobs = vista.get("jobs") if isinstance(vista, dict) else None
        if not isinstance(jobs, list):
            return ""
        elegido = next(
            (
                j
                for j in jobs
                if isinstance(j, dict) and str(j.get("name") or "") == job
            ),
            None,
        )
        return str(elegido.get("databaseId") or "") if elegido else ""

    def comentarios_deepseek(self, repo, numero, sha=""):
        """Comentarios de la integracion ai-review en ese PR y para ese SHA.

        Solo acredita origen el autor github-actions[bot] con la marca
        sticky de ai-review y el SHA pedido. El texto no acredita origen.
        """
        n = self.nombre(repo)
        out = []
        for ruta in (f"repos/{n}/issues/{numero}/comments", f"repos/{n}/pulls/{numero}/comments"):
            try:
                paginas = self._paginas(
                    self._gh("api", ruta + "?per_page=100", "--paginate")
                )
            except RuntimeError:
                continue
            for pagina in paginas:
                items = pagina if isinstance(pagina, list) else pagina.get("comments", []) if isinstance(pagina, dict) else []
                for c in items if isinstance(items, list) else []:
                    if not isinstance(c, dict):
                        continue
                    body = str(c.get("body") or "")
                    autor = str((c.get("user") or {}).get("login") or "")
                    if autor != "github-actions[bot]":
                        continue
                    if "<!-- ai-review:sticky -->" not in body:
                        continue
                    marca = _MARCA_SHA.search(body)
                    real = (
                        marca.group(1)
                        if marca
                        else str(c.get("commit_id") or c.get("original_commit_id") or "")
                    )
                    if not real or (sha and real != sha):
                        continue
                    out.append({"texto": body, "autor": autor, "sha": real})
        return out

    def disparar_workflow(self, repo, workflow, inputs):
        n = self.nombre(repo)
        args = ["workflow", "run", workflow, "-R", n]
        for k, v in inputs.items():
            args += ["-f", f"{k}={v}"]
        self._gh(*args)

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

    def capacidad(self, repo, base):
        """Mirada de solo lectura: acceso al repo, rama base y reglas fijas de
        merge. No abre PR ni mergea. Solo una regla fija y legible (revisiones
        humanas requeridas sobre la base, o el actor sin push) devuelve True;
        lo que no se pudo leer queda None y no baja ningún nivel."""
        n = self.nombre(repo)
        detalle: list[str] = []
        try:
            cuerpo = json.loads(self._gh("api", f"repos/{n}"))
        except (RuntimeError, ValueError) as e:
            return CapacidadRemota(
                False, None, None, f"repo no legible: {_causa_corta(e)}"
            )
        integrar = False
        perms = cuerpo.get("permissions") or {}
        if perms and not perms.get("push", True):
            integrar = True
            detalle.append("el actor no tiene permiso de push sobre el repo")
        try:
            self._gh("api", f"repos/{n}/branches/{base}")
        except RuntimeError as e:
            if self._es_404(e):
                detalle.append(f"la rama base {base} no está en el remoto")
                return CapacidadRemota(True, False, None, "; ".join(detalle))
            detalle.append(f"no se pudo leer la rama {base}: {_causa_corta(e)}")
            return CapacidadRemota(True, None, None, "; ".join(detalle))
        revisiones = 0
        try:
            prot = json.loads(self._gh("api", f"repos/{n}/branches/{base}/protection"))
            revisiones = max(
                revisiones,
                int(
                    (prot.get("required_pull_request_reviews") or {}).get(
                        "required_approving_review_count"
                    )
                    or 0
                ),
            )
        except RuntimeError as e:
            # "Branch not protected" y el 403 del plan gratis dicen que no hay
            # protección que leer: fuente limpia. Cualquier otra respuesta sin
            # leer deja la conclusión en desconocido.
            if not (self._sin_proteccion(e) or self._sin_funcion_en_el_plan(e)):
                detalle.append(f"protección de {base} sin leer: {_causa_corta(e)}")
                return CapacidadRemota(True, True, None, "; ".join(detalle))
        try:
            for pagina in self._paginas(
                self._gh("api", f"repos/{n}/rules/branches/{base}", "--paginate")
            ):
                for regla in pagina if isinstance(pagina, list) else []:
                    if isinstance(regla, dict) and regla.get("type") == "pull_request":
                        revisiones = max(
                            revisiones,
                            int(
                                (regla.get("parameters") or {}).get(
                                    "required_approving_review_count"
                                )
                                or 0
                            ),
                        )
        except RuntimeError as e:
            if not self._sin_funcion_en_el_plan(e):
                detalle.append(f"reglas de {base} sin leer: {_causa_corta(e)}")
                return CapacidadRemota(True, True, None, "; ".join(detalle))
        if revisiones >= 1:
            integrar = True
            detalle.append(
                f"{base} exige {revisiones} aprobación(es) que el motor no puede dejar"
            )
        if not detalle:
            detalle.append("sin reglas fijas de merge en el remoto")
        return CapacidadRemota(True, True, integrar, "; ".join(detalle))


# --------------------------------------------------------------------------- falso


class FakeGitHub:
    """Estado en `<dir>/github.json`. La "protección" y los checks salen de `<dir>/repos.json`:
    {"<repo>": {"requeridos": ["ci"], "solo_listo": ["completa"], "checks": {"ci": "./ci.sh"}, "privado": true}}.
    Los checks corren el comando en una copia limpia del SHA; los de `solo_listo` no corren en borrador.
    Reglas fijas de merge: "revisiones_requeridas": N o "sin_push": true bloquean `merge` y `capacidad`;
    "capacidad": "desconocida" simula un remoto cuyas reglas no se pueden leer (no baja ningún nivel)."""

    def __init__(self, d: Path):
        self.d = d
        d.mkdir(parents=True, exist_ok=True)
        if not (d / "github.json").exists():
            self._guardar({"prs": {}, "checks": {}})
        # Configuración de las pruebas: un solo doble, muchas respuestas.
        self.responde_deepseek: Callable[[int, str], str | None] | None = None
        self.responde_ver: Callable[[str, int], PrView] | None = None
        self.responde_checks: Callable[..., ChecksVerdict] | None = None
        self.responde_estado_merge: Callable[[str, int], EstadoMerge] | None = None
        self.responde_merge: Callable[..., PrView] | None = None
        self.responde_marcar_listo: Callable[[str, int], None] | None = None
        self.textos: dict[object, object] | list[object] = {}
        self.titulos: list[str] = []
        self.sufijo: str = ""
        self.hallazgo: dict | None = None
        self.texto: str | None = None
        self.archivo: str = ""
        self.severidad: str = ""
        self.identificador: str = ""
        self.fijo: bool = False
        self.estado: str = "abierto"
        self.motivo: str = ""
        self.sha: str = ""
        self.listos: list[int] = []
        self.llamadas: list[tuple[int, str]] = []
        self.vistos: list[str] = []
        self.fallos = 0
        self.falla = False
        self.merges: list[tuple[int, str]] = []
        self.actualizaciones = 0
        self.fallar_cuerpo = False
        self.relanzadas: list[str] = []
        self.checks_llamadas = 0
        self.mergeables_llamadas: dict[str, int] = {}
        self.dormir = lambda _segundos: None
        self.bloquea = False
        self.intentos: list[str] = []
        self._reintentos: dict[str, int] = {}
        self._errores: dict[str, int] = {}

    def _cargar(self) -> dict:
        p = self.d / "github.json"
        return (
            leer_json(p)
            if p.exists()
            else {"prs": {}, "checks": {}}
        )

    def _guardar(self, s: dict) -> None:
        tmp = self.d / "github.json.tmp"
        escribir_texto(tmp, json.dumps(s, indent=1))
        os.replace(tmp, self.d / "github.json")

    def _cfg(self, repo: str) -> dict:
        p = self.d / "repos.json"
        return (
            leer_json(p).get(repo, {})
            if p.exists()
            else {}
        )

    def _punta(self, repo: str, rama: str) -> str:
        r = git("ls-remote", repo, f"refs/heads/{rama}")
        return r.stdout.split()[0] if r.stdout.strip() else ""

    def es_privado(self, repo):
        return bool(self._cfg(repo).get("privado", True))

    def capacidad(self, repo, base):
        """Solo lectura sobre el repo local y las reglas fijas de repos.json."""
        cfg = self._cfg(repo)
        if str(cfg.get("capacidad") or "") == "desconocida":
            return CapacidadRemota(True, None, None, "el remoto no deja leer sus reglas")
        if not git("ls-remote", repo, "HEAD").stdout.strip():
            return CapacidadRemota(False, None, None, "repo no legible")
        if not self._punta(repo, base):
            return CapacidadRemota(
                True, False, None, f"la rama base {base} no está en el remoto"
            )
        revisiones = int(cfg.get("revisiones_requeridas") or 0)
        if revisiones >= 1:
            return CapacidadRemota(
                True,
                True,
                True,
                f"{base} exige {revisiones} aprobación(es) que el motor no puede dejar",
            )
        if cfg.get("sin_push"):
            return CapacidadRemota(
                True, True, True, "el actor no tiene permiso de push sobre el repo"
            )
        return CapacidadRemota(True, True, False, "sin reglas fijas de merge en el remoto")

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
            str(pr.get("cuerpo") or ""),
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
                "cuerpo": cuerpo,
            }
            self._guardar(s)
        elif s["prs"][k]["base"] != base:
            s["prs"][k]["base"] = base
            self._guardar(s)
        return self._vista(repo, s["prs"][k])

    def _pr(self, s: dict, repo: str, numero: int) -> dict:
        for k, pr in s["prs"].items():
            if k.startswith(repo + "#") and pr["numero"] == numero:
                return pr
        raise KeyError(numero)

    def marcar_listo(self, repo, numero):
        if self.responde_marcar_listo is not None:
            self.responde_marcar_listo(repo, numero)
            return
        self.listos.append(numero)
        s = self._cargar()
        self._pr(s, repo, numero)["borrador"] = False
        self._guardar(s)

    def cambiar_base(self, repo, numero, base):
        s = self._cargar()
        pr = self._pr(s, repo, numero)
        if pr["base"] != base:
            pr["base"] = base
            self._guardar(s)

    def actualizar_cuerpo(self, repo, numero, cuerpo):
        self.actualizaciones += 1
        if self.fallar_cuerpo:
            raise RuntimeError("gh pr edit: fallo simulado")
        s = self._cargar()
        self._pr(s, repo, numero)["cuerpo"] = cuerpo
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

    def _checks_de_guion(self, repo, sha, extra, cfg) -> ChecksVerdict:
        """Corridas declaradas en `repos.json`, sin ejecutar comandos.

        Es la configuración del doble para probar la lectura de CI: estados,
        reintentos y corridas sin `run_id`.
        """
        self.checks_llamadas += 1
        if self._errores.get(str(repo), 0) > 0:
            self._errores[str(repo)] -= 1
            raise RuntimeError("GitHub no respondió")
        estados = {
            "success": ("completed", "success"),
            "failure": ("completed", "failure"),
            "timed_out": ("completed", "timed_out"),
            "startup_failure": ("completed", "startup_failure"),
            "in_progress": ("in_progress", None),
            "cancelled": ("completed", "cancelled"),
            "skipped": ("completed", "skipped"),
            "neutral": ("completed", "neutral"),
        }
        intento = self._reintentos.get(str(repo), 0)
        no_empezo = intento > 0 and bool(cfg.get("reintento_no_empezado"))
        if no_empezo:
            corridas = cfg.get("corridas") or {}
            intento_lectura = 0
        elif intento > 0 and "corridas_tras_reintento" in cfg:
            corridas = cfg["corridas_tras_reintento"]
            intento_lectura = intento
        else:
            corridas = (cfg.get("corridas_sha") or {}).get(
                sha, cfg.get("corridas") or {}
            )
            intento_lectura = intento
        completado = (
            "2026-11-03T21:46:00Z"
            if intento_lectura == 0
            else time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        )
        runs = []
        for nombre, estado in corridas.items():
            status, conclusion = estados[estado]
            rid = 40000 + intento_lectura * 1000 + len(runs)
            run = {
                "name": nombre,
                "status": status,
                "conclusion": conclusion,
                "completed_at": completado,
            }
            if not cfg.get("sin_run_id"):
                run.update(
                    {
                        "id": rid,
                        "html_url": f"https://github.com/o/r/actions/runs/{rid}/job/{rid}",
                        "details_url": f"https://github.com/o/r/actions/runs/{rid}/job/{rid}",
                    }
                )
            runs.append(run)
        return veredicto(sha, tuple(extra), runs, "")

    def checks(self, repo, numero, sha, extra, relanzadas=(), *, base_politica=""):
        if self.responde_checks is not None:
            return self.responde_checks(
                repo,
                numero,
                sha,
                extra,
                relanzadas,
                base_politica=base_politica,
            )
        cfg = self._cfg(repo)
        if any(
            clave in cfg
            for clave in ("corridas", "corridas_sha", "corridas_tras_reintento")
        ):
            return self._checks_de_guion(repo, sha, extra, cfg)
        s = self._cargar()
        pr = self._pr(s, repo, numero)
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

    def checks_requeridos(self, repo, numero, sha):
        if self.ver(repo, numero).head != sha:
            raise PuntaMovida("la punta del PR ya no es el SHA revisado")
        return self.checks(repo, numero, sha, ())

    def estado_merge(self, repo, numero):
        if self.responde_estado_merge is not None:
            return self.responde_estado_merge(repo, numero)
        cfg = self._cfg(repo)
        secuencia = cfg.get("mergeables")
        estado = EstadoMerge("desconocido")
        for _intento in range(_REINTENTOS_MERGEABLE):
            llamadas = self.mergeables_llamadas.get(str(repo), 0) + 1
            self.mergeables_llamadas[str(repo)] = llamadas
            if secuencia:
                item = secuencia[min(llamadas - 1, len(secuencia) - 1)]
                if isinstance(item, dict):
                    estado = _estado_merge_desde(
                        str(item.get("mergeable") or ""),
                        str(item.get("mergeStateStatus") or ""),
                    )
                else:
                    estado = _estado_merge_desde(str(item), "")
            else:
                estado = _estado_merge_desde(
                    str(cfg.get("mergeable") or "MERGEABLE"),
                    str(cfg.get("merge_state_status") or "CLEAN"),
                )
            if estado.estado != "desconocido":
                return estado
            self.dormir(_ESPERA_MERGEABLE_S)
        return estado

    def relanzar_corrida(self, repo, run_id):
        self.relanzadas.append(str(run_id))
        s = self._cargar()
        s.setdefault("relanzadas", []).append(
            {"repo": repo, "run_id": str(run_id), "fallidas": False}
        )
        self._guardar(s)

    def relanzar_fallidas(self, repo, run_id):
        self.relanzadas.append(str(run_id))
        self._reintentos[str(repo)] = self._reintentos.get(str(repo), 0) + 1
        s = self._cargar()
        s.setdefault("relanzadas", []).append(
            {"repo": repo, "run_id": str(run_id), "fallidas": True}
        )
        self._guardar(s)

    def fallar_checks(self, repo: str, veces: int = 1) -> None:
        """Las próximas `veces` lecturas de checks fallan, como un GitHub caído."""
        self._errores[str(repo)] = veces

    def log_fallido(self, repo, run_id) -> str:
        return ""

    def log_fallido_job(self, repo, run_id, job, job_id="") -> str:
        logs = self._cfg(repo).get("logs_job") or {}
        return str(logs.get(job, ""))

    def comentarios_deepseek(self, repo, numero, sha=""):
        self.llamadas.append((numero, sha))
        if sha not in self.vistos:
            self.vistos.append(sha)
        if self.falla:
            raise RuntimeError("DeepSeek no responde")
        if self.fallos > 0:
            self.fallos -= 1
            raise RuntimeError("gh api: 502")
        if callable(self.responde_deepseek):
            texto = self.responde_deepseek(numero, sha)
            return (
                [{"texto": texto, "autor": "DeepSeek", "sha": sha}] if texto else []
            )
        if self.textos:
            if isinstance(self.textos, list):
                indice = self.vistos.index(sha)
                texto = (
                    self.textos[indice] if indice < len(self.textos) else None
                )
            else:
                texto = self.textos.get(numero, self.textos.get(None))
            if callable(texto):
                texto = texto(len(self.llamadas))
            return (
                [{"texto": texto, "autor": "DeepSeek", "sha": sha}] if texto else []
            )
        s = self._cargar()
        pr = self._pr(s, repo, numero)
        out = []
        for clave in ("comentarios_deepseek", "comentarios", "deepseek"):
            for c in pr.get(clave, []) or []:
                if isinstance(c, str):
                    out.append({"texto": c, "autor": "DeepSeek", "sha": sha})
                    continue
                texto = str(c.get("texto") or c.get("body") or c.get("text") or "")
                if not texto:
                    continue
                csha = str(c.get("sha") or c.get("commit_id") or "")
                if sha and csha and csha != sha:
                    continue
                out.append(
                    {"texto": texto, "autor": str(c.get("autor") or "DeepSeek"), "sha": csha or sha}
                )
        return out


    def disparar_workflow(self, repo, workflow, inputs):
        s = self._cargar()
        s.setdefault("ai_review", []).append(
            {"repo": repo, "workflow": workflow, "inputs": dict(inputs)}
        )
        self._guardar(s)

    def merge(self, repo, numero, sha, metodo, mensaje):
        if self.responde_merge is not None:
            return self.responde_merge(repo, numero, sha, metodo, mensaje)
        if self.fijo:
            raise clasificar_merge(RuntimeError(self.motivo))
        self.intentos.append(sha)
        if self.bloquea:
            raise PoliticaBloquea("the base branch policy prohibits the merge")
        s = self._cargar()
        pr = self._pr(s, repo, numero)
        if pr["estado"] == "mergeado":
            return self._vista(repo, pr)
        cfg = self._cfg(repo)
        if int(cfg.get("revisiones_requeridas") or 0) >= 1 or cfg.get("sin_push"):
            raise PoliticaBloquea(
                "la política de la rama prohibe el merge de forma fija"
            )
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
                raise BaseMovida("la rama base se movió; reintenta")
            commit = git("rev-parse", "HEAD", cwd=w).stdout.strip()
        s = self._cargar()
        pr = self._pr(s, repo, numero)
        pr.update(estado="mergeado", merge_commit=commit, head_merge=sha)
        self.merges.append((numero, sha))
        self._guardar(s)
        return self._vista(repo, pr)

    def ver(self, repo, numero):
        if self.responde_ver is not None:
            return self.responde_ver(repo, numero)
        if self.fijo:
            return PrView(numero, "x", "main", False, self.sha, self.estado, "c" * 40)
        s = self._cargar()
        return self._vista(repo, self._pr(s, repo, numero))


def _cumple_el_puerto(gh: FakeGitHub) -> GitHubPort:
    """pyright verifica que el doble implementa todo `GitHubPort`."""
    return gh


def para(maquina_github: str, env: dict | None = None) -> GitHubPort:
    if maquina_github.startswith("falso:"):
        return FakeGitHub(Path(maquina_github.split(":", 1)[1]).expanduser())
    return GhGitHub(env)


__all__ = [
    "BaseMovida",
    "Borrador",
    "CapacidadRemota",
    "ChecksVerdict",
    "Conflicto",
    "EstadoMerge",
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
