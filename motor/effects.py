"""Un efecto por acción, idempotente: antes de actuar mira si el efecto ya existe.

Separado por conocimiento: `Workspace` sabe de git local, `GitHubPort` de PRs y checks,
`plataforma` de procesos, `Notifier` de Telegram. Aquí solo se orquestan.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import re
import shutil
import string
import subprocess
import sys
import tempfile
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

from . import claves, huella
from . import machine as m
from .plataforma.windows import argv_en_wsl
from . import plataforma, relevo
from . import pstack as ps
from .conf import Conf, Maquina, RepoConf
from .fallas import PROVEEDORES
from .github import GitHubPort, MergeRechazado
from .notify import Notifier, render
from .plan import HUMAN_KINDS, Plan
from .store import Store
from .workspace import Workspace, foto_wip, git

RAIZ = Path(__file__).resolve().parent.parent
_FALTA = re.compile(
    r"(?:^|\s)([\w.+-]+): (?:command not found|orden no encontrada)"
    r"|'([^']+)' is not recognized as an internal or external command"
)


def con_python(env: dict) -> dict:
    """Los planes escritos en la PC dicen `python`; la Mac y la Mini solo traen `python3`
    (INGESTA 01, bloque C). Si falta `python` y hay `python3`, el motor pone uno que llama a python3."""
    ruta = env.get("PATH", "")
    if (
        os.name != "nt"
        and not shutil.which("python", path=ruta)
        and shutil.which("python3", path=ruta)
    ):
        env["PATH"] = os.pathsep.join([str(RAIZ / "motor" / "shims" / "python"), ruta])
    return env


def comandos_faltantes(salida: str) -> list[str]:
    """Comandos que la máquina no tiene, según lo que dijo el shell (bash o cmd de Windows)."""
    return sorted({a or b for a, b in _FALTA.findall(salida)})


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


def _posix(cmd: str) -> tuple:
    """Cómo correr un comando POSIX del motor (Acepta, sondas, pruebas reales) en cada plataforma:
    (args, shell, executable) para Popen. En Windows no hay /bin/bash y `shell=True` con executable
    sale mal (le pasa /c): se llama al bash/sh de Git por argv; sin bash, el shell del sistema."""
    if os.name != "nt":
        return cmd, True, "/bin/bash"
    sh = shutil.which("bash") or shutil.which("sh")
    return ([sh, "-c", cmd], False, None) if sh else (cmd, True, None)


RECETA = (
    "El motor corre cada script de la receta con `bash <script>`, sin argumentos, desde una copia "
    "del commit mergeado; MOTOR_SHA trae ese commit."
)


def _cola(p: subprocess.CompletedProcess) -> str:
    return (p.stdout + p.stderr)[-500:].strip()


def _run(
    cmd: str, cwd: Path | None, env: dict, timeout: float = 900
) -> subprocess.CompletedProcess:
    """Corre `cmd` en su propio grupo; al pasarse del tope mata el grupo entero, no solo el shell.
    Shopify-token-01: el tope cortaba `bash desplegar.sh` y el `crontab -` que había abierto se
    quedaba colgado en la Mini, esperando un permiso que nadie contestaba."""
    argv, shell, exe = _posix(cmd)
    with tempfile.TemporaryFile() as out_f, tempfile.TemporaryFile() as err_f:
        p = subprocess.Popen(
            argv,
            cwd=cwd,
            env=env,
            stdout=out_f,
            stderr=err_f,
            stdin=subprocess.DEVNULL,
            shell=shell,
            executable=exe,
            **plataforma.kwargs_grupo(),
        )
        try:
            p.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            plataforma.matar_grupo(p)
            try:
                p.wait(timeout=5)
            except Exception:  # noqa: BLE001
                pass
            out_f.seek(0)
            err_f.seek(0)
            out = out_f.read().decode("utf-8", errors="replace")
            err = err_f.read().decode("utf-8", errors="replace")
            return subprocess.CompletedProcess(
                cmd,
                124,
                out,
                err + f"se pasó del tope de {int(timeout)} s",
            )
        out_f.seek(0)
        err_f.seek(0)
        out = out_f.read().decode("utf-8", errors="replace")
        err = err_f.read().decode("utf-8", errors="replace")
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


def _modelo_arg(argv) -> str:
    """El valor de `--model` / `-m` en el argv de una ruta (así el shim reconoce a qué ruta llama el agente)."""
    for i, x in enumerate(argv[:-1]):
        if x in ("--model", "-m"):
            return argv[i + 1]
    return ""


def arbol_de(conf_dir: Path) -> str:
    """Id de la copia fija de pstack: hash de lo que hay en `pstack/` y `pstack-motor/` de la conf.
    Mismo contenido, mismo id en las tres máquinas; otro contenido, otra carpeta."""
    h = hashlib.sha256()
    for nombre in ("pstack", "pstack-motor"):
        base = conf_dir / nombre
        if not base.is_dir():
            continue
        for f in sorted(p for p in base.rglob("*") if p.is_file()):
            h.update(f.relative_to(conf_dir).as_posix().encode() + b"\0")
            h.update(f.read_bytes() + b"\0")
    return h.hexdigest()[:12]


def materializa_pstack(conf_dir: Path, raiz: Path) -> tuple[str, Path] | None:
    """Copia `pstack/` y `pstack-motor/` de la conf a `<raiz>/<id de árbol>/` con un rename atómico:
    nadie ve una copia a medias, y una copia ya hecha no se toca (no cambia mientras dura un intento)."""
    if not (conf_dir / "pstack").is_dir():
        return None
    id_ = arbol_de(conf_dir)
    destino = raiz / id_
    if destino.exists():
        os.utime(destino)  # en uso: la limpieza de 14 días no la cuenta como vieja
        return id_, destino
    raiz.mkdir(parents=True, exist_ok=True)
    tmp = raiz / f".tmp-{id_}-{os.getpid()}-{uuid.uuid4().hex[:6]}"
    try:
        for nombre in ("pstack", "pstack-motor"):
            if (conf_dir / nombre).is_dir():
                shutil.copytree(conf_dir / nombre, tmp / nombre)
        try:
            os.rename(tmp, destino)
        except OSError:
            if (
                not destino.exists()
            ):  # si otro proceso la dejó lista primero, es la misma
                raise
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return id_, destino


def errores_pstack(d: Path, conf) -> list[str]:
    """Para `motor conf valida`: pstack.toml tiene que coincidir con la copia fija que lleva la conf."""
    pc = conf.pstack
    if pc is None:
        return []
    copia = d / "pstack"
    if not (copia / "skills" / ps.SKILL_BASE / "SKILL.md").is_file():
        return [
            "pstack.toml existe pero falta pstack/skills/poteto-mode/SKILL.md en la conf"
        ]
    errores = []
    if not pc.commit:
        errores.append("pstack.toml [pstack] commit: falta el commit de la copia fija")
    marca = next(
        (
            (copia / n).read_text(encoding="utf-8", errors="replace")
            for n in ("ORIGEN", "COMMIT")
            if (copia / n).is_file()
        ),
        None,
    )
    if marca is None:
        if pc.exigir:
            errores.append(
                "pstack/ no dice de qué commit viene (falta pstack/ORIGEN): no puedo comprobar que "
                "pstack.toml coincide con la copia fija"
            )
    elif pc.commit and pc.commit[:7] not in marca:
        errores.append(
            f"pstack.toml dice el commit {pc.commit[:12]} y la copia fija viene de otro "
            f"({marca.strip().splitlines()[0][:80]})"
        )
    for papel in ps.PAPELES:
        for lane in ps.LANES:
            if (lane, papel) not in pc.exigencias:
                errores.append(f"pstack.toml: falta la exigencia de {papel} en {lane}")
    for (lane, papel), e in pc.exigencias.items():
        for sk in e.skills:
            if not (copia / "skills" / sk / "SKILL.md").is_file():
                errores.append(
                    f"pstack.toml: la exigencia de {papel} en {lane} pide la skill {sk}, "
                    "que no está en la copia"
                )
    for r in conf.rutas.values():
        if r.cli not in ps.FORMATOS:
            errores.append(f"ruta {r.modelo}: no sé leer el registro del CLI {r.cli}")
        elif not (d / "pstack-motor" / f"{r.cli}.md").is_file():
            errores.append(
                f"falta pstack-motor/{r.cli}.md (mapa del CLI de {r.modelo})"
            )
    if not (d / "pstack-motor" / "motor.md").is_file():
        errores.append("falta pstack-motor/motor.md")
    return errores


HOLGURA_VIVO_S = (
    900  # un intento "vivo" tanto después de su tope de tiempo se da por perdido
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
        self._pstack: tuple[str, tuple[str, Path] | None] | None = None

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
        return con_python(env)

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
                ukey=claves.repo_fijado(plan.id, repo),
            )

    def attempt_dir(self, plan: str, block: str, role: str, n: int) -> Path:
        return self.cx.home / "intentos" / plan / f"{block}-{role}-{n}"

    # ------------------------------------------------------------------ pstack
    def asegura_pstack(self) -> tuple[str, Path] | None:
        """La copia fija de pstack de la conf vigente, ya materializada en `<home>/pstack/<id>/`."""
        conf = self.cx.conf
        if conf.pstack is None or conf.dir is None:
            return None
        if self._pstack and self._pstack[0] == conf.sha != "sin-git":
            return self._pstack[1]
        copia = materializa_pstack(conf.dir, self.cx.home / "pstack")
        self._pstack = (conf.sha, copia)
        return copia

    def _prepara_pstack(
        self, d: Path, lane: str, role: str, cli: str, cwd: Path, sesion: str
    ) -> dict | None:
        """Fija lo que este intento exige y dónde se lee su registro; lo guarda en `pstack.json`.
        `collect` juzga contra esto, no contra la conf de entonces: la conf puede cambiar a medias."""
        pc, copia = self.cx.conf.pstack, self.asegura_pstack()
        if pc is None or copia is None:
            return None
        arbol, raiz = copia
        registros = []
        if cli == "claude":  # el transcript de la sesión que fijamos con --session-id
            base = Path(os.environ.get("CLAUDE_CONFIG_DIR") or Path.home() / ".claude")
            for c in dict.fromkeys([str(cwd), str(cwd.resolve())]):
                registros.append(
                    str(
                        base
                        / "projects"
                        / re.sub(r"[^A-Za-z0-9]", "-", c)
                        / f"{sesion}.jsonl"
                    )
                )
        info = {
            "cli": cli,
            "lane": lane,
            "role": role,
            "exigir": pc.exigir,
            "exigencia": pc.exigencia(lane, role).to_dict(),
            "arbol": arbol,
            "raiz": str(raiz),
            "sesion": sesion,
            "registros": [*registros, str(d / "log.txt")],
        }
        texto = json.dumps(info, ensure_ascii=False)
        (d / "pstack.json").write_text(texto, encoding="utf-8")
        return info

    def _seccion_pstack(self, info: dict | None) -> str:
        """Lo que el encargo lleva de pstack: motor.md, el mapa del CLI y "Cómo trabajas"."""
        if info is None:
            return ""
        raiz = Path(info["raiz"])
        partes = []
        for titulo, f in (
            ("Lo que manda el motor sobre pstack", raiz / "pstack-motor" / "motor.md"),
            (f"Mapa de {info['cli']}", raiz / "pstack-motor" / f"{info['cli']}.md"),
        ):
            if f.is_file():
                partes.append(f"## {titulo}\n{f.read_text(encoding='utf-8').strip()}\n")
        partes.append(
            ps.instrucciones(
                ps.Exigencia.from_dict(info["exigencia"]), info["cli"], str(raiz)
            )
        )
        return "\n".join(partes) + "\n"

    def _juzga_pstack(self, d: Path) -> dict | None:
        """Lee el registro real del intento, guarda `huella.json` y, si el motor exige pstack y falta
        algo, devuelve el resultado `sin_pstack`. Un registro ilegible deja pasar la entrega con aviso."""
        try:
            info = json.loads((d / "pstack.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        h = ps.Huella(legible=False)
        fuente = ""
        for r in self._registros_de(info):
            try:
                texto = Path(r).read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            h = ps.huella(texto, info["cli"], info["raiz"])
            fuente = r
            if h.legible:
                break
        veredicto = ps.juzga(h, ps.Exigencia.from_dict(info["exigencia"]))
        if isinstance(veredicto, ps.Falta):
            estado = "falta"
        else:
            estado = "cumple" if veredicto.verificable else "no_verificable"
        texto = json.dumps(
            {
                "cli": info["cli"],
                "fuente": fuente,
                "exigir": info["exigir"],
                "estado": estado,
                "faltas": list(veredicto.faltas) if estado == "falta" else [],
                "huella": h.to_dict(),
                "exigencia": info["exigencia"],
            },
            ensure_ascii=False,
        )
        (d / "huella.json").write_text(texto, encoding="utf-8")
        if not info["exigir"]:
            return None
        if estado == "no_verificable":
            self._avisa_no_verificable(info["cli"])
        if estado == "falta":
            return {"kind": "sin_pstack", "detalle": veredicto.texto()}
        return None

    @staticmethod
    def _registros_de(info: dict) -> list[str]:
        """Los registros donde buscar la huella. `grok --output-format json` deja en log.txt un solo
        objeto final sin herramientas: las acciones están en `~/.grok/sessions/<cwd>/<sessionId>/updates.jsonl`."""
        registros = list(info["registros"])
        if info["cli"] != "grok":
            return registros
        try:
            texto = Path(registros[-1]).read_text(encoding="utf-8", errors="replace")
        except OSError:
            return registros
        ids = []
        for o in ps._objetos(texto):
            sid = o.get("sessionId") or o.get("session_id")
            if isinstance(sid, str) and re.fullmatch(r"[A-Za-z0-9._-]+", sid):
                ids.append(sid)
        base = Path(os.environ.get("GROK_HOME") or Path.home() / ".grok") / "sessions"
        extra = [
            str(p)
            for sid in dict.fromkeys(ids)
            for p in sorted(base.glob(f"*/{sid}/updates.jsonl"))
        ]
        return [*extra, *registros]

    def _avisa_no_verificable(self, cli: str) -> None:
        """Una vez por CLI y por día: no pude leer el registro, la entrega pasó sin comprobarse."""
        dia = dt.date.today().isoformat()
        marca = self.cx.home / "avisos-pstack" / f"{cli}-{dia}"
        if marca.exists():
            return
        marca.parent.mkdir(parents=True, exist_ok=True)
        marca.write_text(dia, encoding="utf-8")
        self.cx.notifier.encola(
            f"pstack-ilegible:{cli}:{dia}",
            f"En {self.cx.maquina.nombre}: no pude leer el registro de {cli}, así que no comprobé que "
            "usara poteto-mode. Dejé pasar la entrega. Este aviso sale una vez al día por herramienta.",
        )

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
                    ukey=claves.intento_iniciado(plan.id, a.block, a.role, a.n),
                )
            return
        d.mkdir(parents=True, exist_ok=True)
        block = plan.block(a.block)
        rc = self.repo_conf(snap, block.repo)
        ruta = self.cx.conf.rutas[a.modelo]
        if a.role == "implementar":
            cwd = self.cx.ws.prepara(block.repo, plan.id, block.id, rc.principal)
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
            wip = []
        # un uuid por intento: el CLI lo usa como id de sesión y así el motor sabe qué registro leer
        marca_sesion = d / "sesion"
        if not marca_sesion.exists():
            marca_sesion.write_text(str(uuid.uuid4()), encoding="utf-8")
        sesion = marca_sesion.read_text(encoding="utf-8").strip()
        info_pstack = self._prepara_pstack(d, block.lane, a.role, ruta.cli, cwd, sesion)
        seccion = self._seccion_pstack(info_pstack)
        if a.role == "implementar":
            brief = self._brief_implementar(plan, block, a, cwd, rc, seccion)
        else:
            brief = self._brief_revisar(plan, block, a, rc, seccion)
        (d / "brief.md").write_text(brief, encoding="utf-8")
        result = d / "result.json"
        prompt = f"Lee {d / 'brief.md'} y ejecútalo completo sin preguntar nada."
        argv = [
            x.format(
                brief=str(d / "brief.md"),
                prompt=prompt,
                result=str(result),
                pstack=info_pstack["raiz"] if info_pstack else "",
                sesion=sesion,
            )
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
        # el shim de cada CLI de modelos lee de aquí qué cuenta es cada llamada y cuánto cabe
        cupos = json.dumps(
            {
                "home": str(self.cx.home),
                "rutas": {
                    mo: {
                        "cli": r.cli,
                        "cuenta": r.cuenta,
                        "modelo_arg": _modelo_arg(r.argv),
                    }
                    for mo, r in self.cx.conf.rutas.items()
                },
                "max_parallel": dict(self.cx.policy.max_parallel),
                "agentes_por_maquina": self.cx.policy.agentes_por_maquina,
            }
        )
        (d / "cupos.json").write_text(cupos, encoding="utf-8")
        puerta: list[str] = []
        if (
            info_pstack and info_pstack["exigir"]
        ):  # la puerta en vivo es parte de exigir
            puerta = [
                "--puerta",
                str(info_pstack["exigencia"]["puerta"]),
                "--cli",
                ruta.cli,
                "--raiz",
                info_pstack["raiz"],
                *[x for r in info_pstack["registros"] for x in ("--registro", r)],
            ]
        cmd = [
            sys.executable,
            "-m",
            "motor.runwrap",
            "--home",
            str(self.cx.home),
            "--cuenta",
            ruta.cuenta,
            *puerta,
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
        datos = {
            "n": a.n,
            "role": a.role,
            "modelo": a.modelo,
            "cuenta": ruta.cuenta,
            "proc": {},
            "dir": str(d),
            "sha": a.sha,
            "conf_sha": self.cx.conf.sha,
            "feedback": list(a.feedback),
        }
        # la marca va antes de lanzar: si el daemon cae entre las dos cosas, el intento no se duplica
        # (sin `proc` se da por perdido y se relanza); con el pid, la marca se completa
        _escribe_marca(d / "started", datos)
        datos["proc"] = plataforma.lanzar_separado(cmd, cwd, env, d / "runwrap.log")
        _escribe_marca(d / "started", datos)
        self.cx.store.append(
            plan.id,
            a.block,
            "AttemptStarted",
            datos,
            ukey=claves.intento_iniciado(plan.id, a.block, a.role, a.n),
        )

    def _brief_implementar(
        self,
        plan: Plan,
        block,
        a: m.StartAttempt,
        cwd: Path,
        rc: RepoConf,
        seccion_pstack: str = "",
    ) -> str:
        filas = "\n".join(
            f"- {r.id}: {r.contenido}"
            + (f"\n  Acepta: `{r.acepta}`" if r.acepta else "")
            for r in block.rows
        )
        fb = "\n".join(f"- {x}" for x in a.feedback) or "- (primer intento)"
        rama = self.cx.ws.rama(plan.id, block.id)
        pendientes = git("status", "--short", cwd=cwd).stdout.strip()
        repro_json = repro_texto = ""
        if block.es_bug:
            repro_json = ', "repro": "<comando>"'
            repro_texto = (
                "\n4. Este bloque arregla un bug: en `repro` pon UN comando que falle en el commit base "
                "(donde estaba el bug) y salga 0 en tu punta. El motor lo corre en las dos copias; "
                "si no falla antes o no pasa después, devuelve tu entrega."
            )
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

## Si lo que falla no es el código del bloque
Si lo que falla es la máquina, una Acepta que no puede pasar aquí o un servicio externo, no lo
arregles por tu cuenta ni cambies el plan o la máquina. Escribe en `$MOTOR_RESULT`
`{{"bloqueado": "<motivo en una frase>"}}` y termina. El motor no le pasa el bloque a otro agente:
avisa y espera una nota, un plan corregido o una máquina corregida.

{seccion_pstack}## Cómo se entrega
1. Un commit por fila, con el id de la fila al inicio del mensaje. Nunca `--no-verify`.
2. `git push origin HEAD:refs/heads/{rama}` (nunca a `{rc.principal}`).
   PROHIBIDO mergear, cerrar, aprobar o marcar listo un PR (`gh pr merge`, la API, la web): eso
   solo lo hace el motor. Si te dicen que el merge falló por conflicto, trae `{rc.principal}` a tu
   rama (merge o rebase), resuelve y vuelve a entregar.
3. Escribe en `$MOTOR_RESULT` un JSON: {{"sha": "<git rev-parse HEAD>", "resumen": "..."{repro_json}}}.
   Lo que digas no cuenta como prueba: el motor verifica el SHA y corre cada `Acepta`.{repro_texto}
"""

    def _brief_revisar(
        self,
        plan: Plan,
        block,
        a: m.StartAttempt,
        rc: RepoConf,
        seccion_pstack: str = "",
    ) -> str:
        filas = "\n".join(
            f"- {r.id}: {r.contenido}"
            + (f" | Acepta: `{r.acepta}`" if r.acepta else "")
            for r in block.rows
        )
        return f"""{self.cx.conf.reglas_revisor}
# Revisión {plan.id} · bloque {block.id} · SHA {a.sha}

Estás en una copia de solo lectura de ese SHA (diferencia contra `{rc.principal}`). Revisa contra lo pedido:

{filas}

{seccion_pstack}Un hallazgo bloquea solo si trae el comando que lo reproduce (`repro`). El motor lo corre en una copia
de este SHA: si sale 0, no reprodujo nada y el hallazgo baja a nota. Lo demás va en `notas`.
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
            st = plataforma.estado_proc(att.proc) if att.proc else "muerto"
            if st == "vivo":
                if (
                    time.time() - att.started
                    <= self.cx.policy.timeout_s + HOLGURA_VIVO_S
                ):
                    return None
                self._mata_intento(d, att.proc)
                return {
                    "kind": "perdido",
                    "detalle": "sigue 'vivo' mucho después de su tope de tiempo; el motor lo detuvo",
                }
            if (
                not oc.exists()
            ):  # pudo aparecer entre la primera lectura y la consulta del pid
                if st == "corte":
                    return {
                        "kind": "corte",
                        "detalle": "la máquina se apagó o reinició",
                    }
                self._mata_intento(d, None)
                return {
                    "kind": "perdido",
                    "detalle": "el proceso desapareció sin dejar resultado",
                }
        o = json.loads(oc.read_text(encoding="utf-8"))
        if o.get("puerta"):  # runwrap lo cortó en vivo: no cargó poteto-mode a tiempo
            return {"kind": "sin_pstack", "detalle": o["puerta"]}
        if att.role == "implementar" and self._entrego(plan, block_id, d):
            c = {
                "kind": "terminado"
            }  # dejó su entrega: lo que pasó después con el proceso no la borra
        else:
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
                ukey=claves.cooldown(att.dir),
            )
            return c
        if c["kind"] != "terminado":
            return c
        if (falta := self._juzga_pstack(d)) is not None:
            return falta
        revisando = att.role == "revisar"
        malo = "sin_resultado" if not revisando else "veredicto_invalido"
        try:
            res = json.loads((d / "result.json").read_text(encoding="utf-8"))
        except OSError:
            return {
                "kind": "sin_resultado",
                "detalle": "terminó sin escribir el resultado",
            }
        except ValueError:
            return {"kind": malo, "detalle": "result.json no es JSON válido"}
        if not revisando and (motivo := _bloqueado(res)):
            return {
                "kind": "bloqueado",
                "detalle": motivo,
                "huella_maquina": huella.de_maquina(self.cx.maquina),
            }
        if motivo := _resultado_invalido(res, revisando):
            return {"kind": malo, "detalle": motivo}
        block = plan.block(block_id)
        if revisando:
            if res["sha"] != att.sha:
                return {
                    "kind": "veredicto_invalido",
                    "detalle": "el veredicto no cita el SHA revisado",
                }
            veredicto = res["veredicto"]
            bloqueantes = [
                {**b, "repro": b.get("repro", "")} for b in res["bloqueantes"]
            ]
            notas = list(res["notas"])
            bloqueantes, bajados = self._reproduce_bloqueantes(
                plan, snap, block, att.sha, bloqueantes
            )
            notas += [
                f"Bajó de bloqueante a nota (su repro salió 0, no reprodujo nada): {b['texto']}"
                for b in bajados
            ]
            if bajados and not any(b["repro"].strip() for b in bloqueantes):
                veredicto = "APROBADO"  # ya no queda nada que se pueda reproducir
            self.cx.store.append(
                plan.id,
                block_id,
                "Reviewed",
                {
                    "sha": att.sha,
                    "veredicto": veredicto,
                    "modelo": att.modelo,
                    "bloqueantes": bloqueantes,
                    "notas": notas,
                    **({"bajados": bajados} if bajados else {}),
                },
                ukey=claves.revisado(att.dir),
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
        if self._es_la_base_al_cargar(snap, block, punta):
            return {
                "kind": "sin_cambios",
                "detalle": "entregó la punta de la rama principal tal como estaba al cargar el plan: "
                "no cambió nada",
            }
        if block.es_bug and (
            falla := self._comprueba_repro(plan, snap, block, res, punta)
        ):
            return falla
        return {
            "kind": "entregado",
            "sha": punta,
            "resumen": str(res.get("resumen", ""))[:500],
        }

    def interrumpe(self, plan: Plan, snap: m.Snapshot, a: m.Interrumpe) -> None:
        """Detiene el intento en curso, guarda la foto de lo que llevaba y lo anota como `interrumpido`
        (no es falla del modelo). Idempotente: si ya terminó, el evento repetido no entra."""
        inicio = next(
            (
                e.data
                for e in self.cx.store.events(plan.id)
                if e.kind == "AttemptStarted"
                and (e.block, e.data["n"], e.data["role"]) == (a.block, a.n, a.role)
            ),
            None,
        )
        if inicio is None:
            return
        self._mata_intento(Path(inicio["dir"]), inicio.get("proc") or None)
        foto = ""
        w = self.cx.home / "wt" / plan.id / a.block
        if a.role == "implementar" and w.exists():
            wip = self.cx.ws.rama_wip(plan.id, a.block)
            privado = False
            if a.block in snap.blocks:
                repo = snap.blocks[a.block].block.repo
                privado = bool(snap.repos.get(repo, {}).get("privado", False))
            foto = foto_wip(w, wip, local=not privado)
        self.cx.store.append(
            plan.id,
            a.block,
            "AttemptEnded",
            {
                "n": a.n,
                "role": a.role,
                "outcome": {
                    "kind": "interrumpido",
                    "detalle": a.motivo,
                    "foto": foto,
                },
            },
            ukey=claves.intento_terminado(plan.id, a.block, a.role, a.n),
        )

    def _es_la_base_al_cargar(self, snap: m.Snapshot, block, punta: str) -> bool:
        """¿Lo entregado es la rama principal de cuando se cargó el plan, o algo que ya estaba en ella?
        Eso no es un cambio: dárselo por bueno terminaba en un `Merged` sin que nadie mergeara nada."""
        base = snap.repos.get(block.repo, {}).get("sha", "")
        if not base:
            return False
        if punta == base:
            return True
        espejo = self.cx.ws.espejo(block.repo)
        return (
            git("merge-base", "--is-ancestor", punta, base, cwd=espejo).returncode == 0
        )

    def _entrego(self, plan: Plan, block_id: str, d: Path) -> bool:
        """Hay un `result.json` válido cuyo SHA es la punta remota de la rama del bloque."""
        try:
            res = json.loads((d / "result.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return False
        if _resultado_invalido(res, False):
            return False
        sha = res["sha"]
        if len(sha) < 7:
            return False
        block = plan.block(block_id)
        punta = self.cx.ws.punta(block.repo, self.cx.ws.rama(plan.id, block_id))
        return bool(punta) and punta.startswith(sha)

    def _mata_intento(self, d: Path, proc: dict | None) -> None:
        """Detiene lo que quede de un intento perdido (agente y supervisor) antes de relanzar en la
        misma carpeta. Solo mata un pid si sigue siendo el proceso que se lanzó (misma hora de inicio)."""
        if proc and proc.get("pid"):
            plataforma.matar_proc(int(proc["pid"]), proc.get("inicio", ""))
        try:
            pid = int((d / "agent.pid").read_text(encoding="utf-8").strip())
            inicio = (d / "agent.inicio").read_text(encoding="utf-8").strip()
        except (OSError, ValueError):
            return
        plataforma.matar_proc(pid, inicio)

    def _corre_en(
        self, plan: Plan, snap: m.Snapshot, block, sha: str, nombre: str, cmd: str
    ):
        """Corre `cmd` en una copia limpia de `sha` (con el entorno de pruebas del repo)."""
        rc = self.repo_conf(snap, block.repo)
        w = self.cx.ws.copia(
            block.repo, sha, self.cx.home / "tmp" / f"{nombre}-{plan.id}-{block.id}"
        )
        tope = float(self.cx.conf.tiempos.get("repro_tope_s", 300))
        return _run(cmd, w, self.env_base(dict(rc.prueba_env)), tope)

    def _comprueba_repro(
        self, plan: Plan, snap: m.Snapshot, block, res: dict, punta: str
    ) -> dict | None:
        """Un arreglo de bug trae un `repro` que falla en el commit base y sale 0 en la punta entregada."""
        repro = str(res.get("repro", "")).strip()
        if not repro:
            return {
                "kind": "sin_repro",
                "detalle": "el bloque es de bug y tu resultado no trae `repro`: un comando que falle en el "
                "commit base y salga 0 en tu punta",
            }
        rc = self.repo_conf(snap, block.repo)
        espejo = self.cx.ws.espejo(block.repo)
        base = git(
            "merge-base", f"origin/{rc.principal}", punta, cwd=espejo
        ).stdout.strip()
        if not base:
            return {
                "kind": "sin_repro",
                "detalle": f"no encontré el commit base de tu rama contra {rc.principal}",
            }
        p = self._corre_en(plan, snap, block, base, "repro-base", repro)
        if p.returncode == 0:
            return {
                "kind": "sin_repro",
                "detalle": f"tu `repro` sale 0 en el commit base ({base[:7]}), donde debe fallar: "
                "no demuestra el bug",
            }
        p = self._corre_en(plan, snap, block, punta, "repro-punta", repro)
        if p.returncode != 0:
            return {
                "kind": "sin_repro",
                "detalle": f"tu `repro` salió {p.returncode} en tu punta ({punta[:7]}), donde debe salir 0: "
                f"{(p.stdout + p.stderr)[-300:]}",
            }
        return None

    def _reproduce_bloqueantes(
        self, plan: Plan, snap: m.Snapshot, block, sha: str, bloqueantes: list
    ) -> tuple[list, list]:
        """Corre el `repro` de cada bloqueante en una copia del commit revisado. Si sale 0, no reprodujo
        nada: el bloqueante baja a nota. (bloqueantes que se quedan, bloqueantes que bajan)"""
        quedan: list = []
        bajan: list = []
        for i, b in enumerate(bloqueantes):
            cmd = str(b.get("repro", "")).strip() if isinstance(b, dict) else ""
            if (
                cmd
                and self._corre_en(
                    plan, snap, block, sha, f"repro-rev{i}", cmd
                ).returncode
                == 0
            ):
                bajan.append(b)
            else:
                quedan.append(b)
        return quedan, bajan

    # ------------------------------------------------------------------ pasos
    def _huellas(self, campo: str, block, snap: m.Snapshot) -> tuple[str, str]:
        """(huella del insumo, huella de la máquina) que lleva el resultado que se va a anotar."""
        return huella.insumo_de(campo, block, snap.repos), huella.de_maquina(
            self.cx.maquina
        )

    _APARTE = {
        "verify": ("VerifyStarted", "Verified", "verified"),
        "deploy": ("DeployStarted", "Deployed", "deployed"),
        "prove": ("ProveStarted", "Proved", "proved"),
    }

    def _aparte(self, kind: str, plan: Plan, snap: m.Snapshot, a) -> None:
        started_kind, terminal_kind, campo = self._APARTE[kind]
        block = plan.block(a.block)
        h_insumo, h_maquina = self._huellas(campo, block, snap)
        starteds = [
            e
            for e in self.cx.store.events(plan.id)
            if e.kind == started_kind
            and e.block == a.block
            and e.data.get("sha") == a.sha
        ]
        if starteds:
            ultimo = starteds[-1]
            d = Path(ultimo.data["dir"]) if ultimo.data.get("dir") else None
            if d is not None:
                oc = d / "outcome.json"
                if oc.exists():
                    payload = json.loads(oc.read_text(encoding="utf-8"))
                    if payload.get("error"):
                        oc.replace(d / "outcome-error.json")
                        raise RuntimeError(str(payload["error"]))
                    datos = payload.get("data") or {}
                    if (
                        datos.get("huella", h_insumo) == h_insumo
                        and datos.get("huella_maquina", h_maquina) == h_maquina
                    ):
                        if terminal_kind == "Verified":
                            self.cx.store.append(
                                plan.id,
                                a.block,
                                "Verified",
                                datos,
                                ukey=claves.verificado(
                                    plan.id, a.block, a.sha, h_insumo, h_maquina
                                ),
                            )
                        elif terminal_kind == "Deployed":
                            self.cx.store.append(
                                plan.id,
                                a.block,
                                "Deployed",
                                datos,
                                ukey=claves.desplegado(
                                    plan.id, a.block, a.sha, h_insumo, h_maquina
                                ),
                            )
                        else:
                            self.cx.store.append(
                                plan.id,
                                a.block,
                                "Proved",
                                datos,
                                ukey=claves.probado(
                                    plan.id, a.block, a.sha, h_insumo, h_maquina
                                ),
                            )
                        return
                else:
                    proc = ultimo.data.get("proc") or {}
                    if proc and plataforma.estado_proc(proc) == "vivo":
                        return
        n = max((int(e.data.get("n", 0)) for e in starteds), default=0) + 1
        d = self.cx.home / "aparte" / plan.id / f"{a.block}-{kind}-{n}"
        d.mkdir(parents=True, exist_ok=True)
        spec = {
            "kind": kind,
            "block": a.block,
            "sha": a.sha,
            "plan": plan.to_dict(),
            "repos": snap.repos,
            "merged": {bid: bv.merged for bid, bv in snap.blocks.items()},
            "deploy_started": {
                bid: sorted(bv.deploy_started) for bid, bv in snap.blocks.items()
            },
            "verify_started": {
                bid: sorted(bv.verify_started) for bid, bv in snap.blocks.items()
            },
            "prove_started": {
                bid: sorted(bv.prove_started) for bid, bv in snap.blocks.items()
            },
        }
        texto_spec = json.dumps(spec, ensure_ascii=False)
        (d / "spec.json").write_text(texto_spec, encoding="utf-8")
        cmd = [
            sys.executable,
            "-m",
            "motor.aparte",
            "--home",
            str(self.cx.home),
            "--dir",
            str(d),
        ]
        proc = plataforma.lanzar_separado(
            cmd, RAIZ, self.env_base({"PYTHONPATH": str(RAIZ)}), d / "aparte.log"
        )
        datos_started = {"sha": a.sha, "dir": str(d), "proc": proc, "n": n}
        if started_kind == "VerifyStarted":
            self.cx.store.append(
                plan.id,
                a.block,
                "VerifyStarted",
                datos_started,
                ukey=claves.verificacion_iniciada(plan.id, a.block, a.sha, n),
            )
        elif started_kind == "DeployStarted":
            self.cx.store.append(
                plan.id,
                a.block,
                "DeployStarted",
                datos_started,
                ukey=claves.despliegue_iniciado(plan.id, a.block, a.sha, n),
            )
        else:
            self.cx.store.append(
                plan.id,
                a.block,
                "ProveStarted",
                datos_started,
                ukey=claves.prueba_iniciada(plan.id, a.block, a.sha, n),
            )

    def verify(self, plan: Plan, snap: m.Snapshot, a: m.Verify) -> None:
        self._aparte("verify", plan, snap, a)

    def _verify_data(self, plan: Plan, snap: m.Snapshot, a: m.Verify) -> dict:
        block = plan.block(a.block)
        rc = self.repo_conf(snap, block.repo)
        w = self.cx.ws.copia(
            block.repo, a.sha, self.cx.home / "tmp" / f"verifica-{plan.id}-{a.block}"
        )
        fallas, faltan = [], []
        h_insumo, h_maquina = self._huellas("verified", block, snap)
        tope = float(self.cx.conf.tiempos.get("verifica_tope_s", 1800))
        for r in block.rows:
            if r.acepta:
                p = _run(r.acepta, w, self.env_base(dict(rc.prueba_env)), tope)
                if p.returncode != 0:
                    fallas.append(
                        f"{r.id}: `{r.acepta}` salió {p.returncode}: {(p.stdout + p.stderr)[-300:]}"
                    )
                    faltan += comandos_faltantes(p.stdout + p.stderr)
        return {
            "sha": a.sha,
            "ok": not fallas,
            "detalle": "\n".join(fallas),
            "falta_comando": sorted(set(faltan)),
            "huella": h_insumo,
            "huella_maquina": h_maquina,
        }

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
            self.adopta_externo(plan, snap, a.block, a.sha, base, ya_en_base=True)
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
            ukey=claves.pr_abierto(plan.id, a.block, pr.numero),
        )

    def mark_ready(self, plan: Plan, snap: m.Snapshot, a: m.MarkReady) -> None:
        bv = snap.blocks[a.block]
        self.cx.gh.marcar_listo(plan.block(a.block).repo, int(bv.pr["numero"]))
        self.cx.store.append(
            plan.id,
            a.block,
            "MarkedReady",
            {"sha": a.sha},
            ukey=claves.marcado_listo(plan.id, a.block, a.sha),
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
                ukey=claves.pr_cerrado(plan.id, block_id, numero),
            )
            return True
        self.adopta_externo(plan, snap, block_id, sha, v.merge_commit)
        return True

    def adopta_externo(
        self,
        plan: Plan,
        snap: m.Snapshot,
        block_id: str,
        sha: str,
        commit: str,
        ya_en_base: bool = False,
    ) -> None:
        """Con `merge`, la única puerta de `Merged`: un merge que alguien hizo por fuera del motor se
        adopta para que el plan siga, y se avisa una vez."""
        bv = snap.blocks[block_id]
        clave = f"externo:{block_id}"
        avisado = clave in bv.notices or any(
            e.kind == "NoticeQueued" and e.data["key"] == clave
            for e in self.cx.store.events(plan.id)
        )  # el log, no solo la foto: un reintento con la foto vieja no avisa dos veces
        if not avisado:
            self.notify(
                plan,
                snap,
                m.Notify(clave, block_id, "externo", (("bloque", bv.block.aviso),)),
            )
        datos = {"sha": sha, "commit": commit, "externo": True}
        if ya_en_base:
            datos["ya_en_base"] = True
        self.cx.store.append(
            plan.id,
            block_id,
            "Merged",
            datos,
            ukey=claves.integrado(plan.id, block_id, sha),
        )

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
                ukey=claves.checks_esperando(
                    plan.id, a.block, a.sha, tuple(v.faltan), v.github_termino
                ),
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
                ukey=claves.checks_resultado(plan.id, a.block, a.sha),
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
            rechazos = sum(
                1
                for e in self.cx.store.events(plan.id)
                if e.kind == "ChecksResult"
                and e.block == t.block
                and e.data.get("sha") == t.sha
            )
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
                ukey=claves.checks_resultado(plan.id, t.block, t.sha, rechazo=rechazos),
            )
            return
        self.cx.store.append(
            plan.id,
            t.block,
            "Merged",
            {"sha": t.sha, "commit": pr.merge_commit},
            ukey=claves.integrado(plan.id, t.block, t.sha),
        )

    def _receta(self, snap: m.Snapshot, block) -> tuple[str, str, str]:
        return huella.receta_de(block, snap.repos)

    def deploy(self, plan: Plan, snap: m.Snapshot, a: m.Deploy) -> None:
        self._aparte("deploy", plan, snap, a)

    def _deploy_data(self, plan: Plan, snap: m.Snapshot, a: m.Deploy) -> dict:
        bv = snap.blocks[a.block]
        block = bv.block
        rc = self.repo_conf(snap, block.repo)
        desplegar, checklist, rollback = self._receta(snap, block)
        h_insumo, h_maquina = self._huellas("deployed", block, snap)
        faltan: list[str] = []
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
            and not any(
                e.kind == "Deployed" and e.data.get("sha") == a.sha
                for e in self.cx.store.events(plan.id)
            )
            and _run(f"bash {checklist}", w, env, tope).returncode == 0
        ):
            ok, detalle = True, "ya estaba desplegado (se comprobó antes de repetir)"
        else:
            pasos, ok = [], True
            for paso in (desplegar, checklist):
                p = _run(f"bash {paso}", w, env, tope)
                pasos.append(f"{paso}: salida {p.returncode}")
                if p.returncode != 0:
                    # Sin la salida el agente solo veía "salida 1" y no sabía qué arreglar
                    # (INGESTA 01: desplegar.sh pedía un SHA como argumento que el motor no da).
                    ok = False
                    pasos.append(_cola(p))
                    faltan += comandos_faltantes(p.stdout + p.stderr)
                    rb = _run(f"bash {rollback}", w, env, tope)
                    pasos.append(f"rollback: salida {rb.returncode}")
                    if rb.returncode != 0:
                        pasos.append(_cola(rb))
                    pasos.append(RECETA)
                    break
            detalle = "\n".join(pasos)
        return {
            "sha": a.sha,
            "ok": ok,
            "detalle": detalle,
            "falta_comando": sorted(set(faltan)),
            "huella": h_insumo,
            "huella_maquina": h_maquina,
        }

    def prove(self, plan: Plan, snap: m.Snapshot, a: m.Prove) -> None:
        self._aparte("prove", plan, snap, a)

    def _prove_data(self, plan: Plan, snap: m.Snapshot, a: m.Prove) -> dict:
        bv = snap.blocks[a.block]
        block = bv.block
        h_insumo, h_maquina = self._huellas("proved", block, snap)
        faltan: list[str] = []
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
            p = _run(block.prueba_real, w, env, tope)
            # La salida puede ir a un archivo (la sonda de INGESTA 01): sin esto el agente veía "".
            ok, detalle = (
                p.returncode == 0,
                _cola(p) or f"salió {p.returncode} sin imprimir nada",
            )
            if not ok:
                faltan = comandos_faltantes(p.stdout + p.stderr)
        else:
            ok, detalle = True, "el bloque no declara prueba real"
        return {
            "sha": a.sha,
            "ok": ok,
            "detalle": detalle,
            "falta_comando": sorted(set(faltan)),
            "huella": h_insumo,
            "huella_maquina": h_maquina,
        }

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
                    ukey=claves.humano_hecho(plan.id, a.block),
                )
            self._ultimo_poll[f"sonda:{clave}"] = ahora
            return
        if ahora - self._ultimo_poll.get(f"sonda:{clave}", 0) < SONDA_CADA_S:
            return
        argv, shell, exe = _posix(h.sonda)
        p = subprocess.Popen(
            argv,
            shell=shell,
            env=self.env_base({"MOTOR_HOME": str(self.cx.home)}),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            executable=exe,
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
            plan.id, "", "Limpiado", {"ramas": borradas}, ukey=claves.limpiado(plan.id)
        )

    def purga_registros(self, planes: list[Plan], now: float) -> None:
        """Borra los registros de intentos de los planes limpiados hace más de `registros_dias`,
        y las copias de pstack que ningún intento usa hace ese mismo tiempo (menos la vigente)."""
        dias = float(self.cx.conf.tiempos.get("registros_dias", 14))
        vigente = self._pstack[1][0] if self._pstack and self._pstack[1] else ""
        raiz = self.cx.home / "pstack"
        for x in raiz.iterdir() if raiz.is_dir() else ():
            try:
                vieja = now - x.stat().st_mtime > dias * 86400
            except OSError:
                continue
            if vieja and x.name != vigente:
                shutil.rmtree(x, ignore_errors=True)
        for p in planes:
            limpio = next(
                (e for e in self.cx.store.events(p.id) if e.kind == "Limpiado"), None
            )
            carpeta = self.cx.home / "intentos" / p.id
            if limpio and now - limpio.ts > dias * 86400 and carpeta.exists():
                shutil.rmtree(carpeta, ignore_errors=True)

    def notify(self, plan: Plan, snap: m.Snapshot, a: m.Notify) -> None:
        if a.template == "humano" and a.block and snap.blocks[a.block].human_done:
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
            ukey=claves.aviso_encolado(plan.id, a.key),
        )


def _escribe_marca(ruta: Path, datos: dict) -> None:
    tmp = ruta.with_name(ruta.name + ".tmp")
    tmp.write_text(json.dumps(datos), encoding="utf-8")
    os.replace(tmp, ruta)


def _bloqueado(res: object) -> str:
    """El motivo si el agente dijo que no es el código del bloque lo que falla; vacío si no."""
    motivo = res.get("bloqueado") if isinstance(res, dict) else None
    return motivo.strip()[:500] if isinstance(motivo, str) else ""


def _resultado_invalido(res: object, revisando: bool) -> str:
    """Frontera de `result.json`: el motivo exacto si no cumple la forma, vacío si cumple."""
    if not isinstance(res, dict):
        return f"result.json no es un objeto (es {type(res).__name__})"

    def falta(clave: str, tipo: str) -> str:
        return f"result.json: '{clave}' tiene que ser {tipo}"

    if not isinstance(res.get("sha"), str):
        return falta("sha", "texto")
    if not revisando:
        return "" if isinstance(res.get("resumen"), str) else falta("resumen", "texto")
    if res.get("veredicto") not in ("APROBADO", "CAMBIOS"):
        return "result.json: 'veredicto' tiene que ser APROBADO o CAMBIOS"
    bloqueantes = res.get("bloqueantes")
    if not isinstance(bloqueantes, list):
        return falta("bloqueantes", "una lista")
    for i, b in enumerate(bloqueantes):
        if not isinstance(b, dict):
            return f"result.json: bloqueantes[{i}] tiene que ser un objeto con 'texto'"
        for clave, obligatoria in (
            ("texto", True),
            ("repro", False),
            ("arreglo", False),
        ):
            if clave in b and not isinstance(b[clave], str):
                return f"result.json: bloqueantes[{i}].{clave} tiene que ser texto"
            if obligatoria and clave not in b:
                return f"result.json: bloqueantes[{i}] no trae 'texto'"
    notas = res.get("notas")
    if not isinstance(notas, list) or not all(isinstance(n, str) for n in notas):
        return falta("notas", "una lista de textos")
    return ""


def _iso_a_epoch(iso: str | None) -> float:
    if not iso:
        return 0.0
    import datetime as dt

    try:
        return dt.datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return 0.0
