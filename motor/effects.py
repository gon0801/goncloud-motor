"""Un efecto por acción, idempotente: antes de actuar mira si el efecto ya existe.

Separado por conocimiento: `Workspace` sabe de git local, `GitHubPort` de PRs y checks,
`plataforma` de procesos, `Notifier` de Telegram. Aquí solo se orquestan.
"""

from __future__ import annotations

import contextlib
import datetime as dt
import hashlib
import json
import os
import re
import shlex
import shutil
import signal
import string
import subprocess
import sys
import tempfile
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from . import claves, efimeras, entrega, huella, plataforma, relevo, senal
from . import machine as m
from . import pstack as ps
from .conf import Conf, Maquina, RepoConf, argv_de_ruta
from .fallas import PROVEEDORES
from .guias import guias
from .github import (
    Borrador,
    Conflicto,
    GitHubPort,
    MergeRechazado,
    PoliticaBloquea,
    PuntaMovida,
    clasificar_merge,
)
from .notify import Notifier, render
from .plan import HUMAN_KINDS, Plan, PlanError
from .plataforma.windows import argv_shell, lanzamiento_wsl, path_con_python_real
from .store import Store
from .workspace import Workspace, foto_wip, git

RAIZ = Path(__file__).resolve().parent.parent
_FALTA = re.compile(
    r"(?:^|\s)([\w.+-]+): (?:command not found|orden no encontrada)"
    r"|'([^']+)' is not recognized as an internal or external command"
)


def con_python(env: dict, en_windows: bool | None = None) -> dict:
    if en_windows is None:
        en_windows = os.name == "nt"
    if en_windows:
        return env
    ruta = env.get("PATH", "")
    if not shutil.which("python", path=ruta) and shutil.which("python3", path=ruta):
        env["PATH"] = os.pathsep.join([str(RAIZ / "motor" / "shims" / "python"), ruta])
    return env


def _dir_shims_python() -> Path:
    """Carpeta con `python` y `python3` que ejecutan el Python del motor (`sys.executable`)."""
    clave = hashlib.sha256(os.path.realpath(sys.executable).encode()).hexdigest()[:12]
    d = Path(tempfile.gettempdir()) / f"motor-python-{clave}"
    contenido = f"#!/bin/sh\nexec {shlex.quote(sys.executable)} \"$@\"\n"
    try:
        d.mkdir(parents=True, exist_ok=True)
        for nombre in ("python", "python3"):
            f = d / nombre
            if f.is_symlink() and os.path.realpath(f) == os.path.realpath(
                sys.executable
            ):
                continue
            try:
                if f.is_symlink() or f.exists():
                    f.unlink()
                f.symlink_to(sys.executable)
                continue
            except OSError:
                pass
            try:
                actual = f.read_text(encoding="utf-8")
            except OSError:
                actual = ""
            if actual != contenido:
                tmp = d / f".{nombre}.tmp"
                tmp.write_text(contenido, encoding="utf-8")
                tmp.chmod(0o755)
                os.replace(tmp, f)
            else:
                os.chmod(f, 0o755)
    except OSError:
        return Path(sys.executable).parent
    return d


def entorno_maquina(maquina, extra: dict | None = None) -> dict:
    """El único entorno de la máquina: previa y verificación salen de aquí."""
    env = {**os.environ}
    if maquina is not None:
        env.update(dict(getattr(maquina, "env", ()) or ()))
        env.update(dict(getattr(maquina, "secretos", ()) or ()))
    env["MOTOR_RAIZ"] = str(RAIZ)
    if extra:
        env.update(dict(extra))
    env.pop(senal.VARIABLE, None)
    env.pop("MOTOR_LLAMADA", None)
    if os.name == "nt":
        return con_python(env)
    path_extra = (
        tuple(getattr(maquina, "path_extra", ()) or ()) if maquina is not None else ()
    )
    partes = [
        str(_dir_shims_python()),
        str(RAIZ / "motor" / "shims"),
        *path_extra,
        env.get("PATH", ""),
    ]
    env["PATH"] = os.pathsep.join(p for p in partes if p)
    return env


def con_python_de_comando(env: dict, en_windows: bool | None = None) -> dict:
    if en_windows is None:
        en_windows = os.name == "nt"
    if not en_windows:
        return env
    salida = dict(env)
    salida["PATH"] = path_con_python_real(salida.get("PATH", ""))
    return salida


def comandos_faltantes(salida: str) -> list[str]:
    """Comandos que la máquina no tiene, según lo que dijo el shell (bash o cmd de Windows)."""
    return sorted({a or b for a, b in _FALTA.findall(salida)})


def _marca_agente(att) -> tuple[dict | None, str]:
    """Identidad del agente, o un fallo si las marcas están a medias o no se leen."""
    d = getattr(att, "dir", None)
    if not d:
        return None, ""
    base = Path(d)
    pid_p = base / "agent.pid"
    ini_p = base / "agent.inicio"
    try:
        hay_pid = pid_p.exists()
        hay_ini = ini_p.exists()
    except OSError as e:
        raise OSError(e.errno, "no pude ver las marcas del agente") from e
    if not hay_pid and not hay_ini:
        return None, ""
    if hay_pid != hay_ini:
        return None, "parcial"
    try:
        pid = int(pid_p.read_text(encoding="utf-8").strip())
        inicio = ini_p.read_text(encoding="utf-8").strip()
    except (OSError, ValueError):
        return None, "ilegible"
    if not pid or not inicio:
        return None, "parcial"
    proc = getattr(att, "proc", None) or {}
    boot = proc.get("boot_id") or plataforma.boot_id()
    job = ""
    job_p = base / "agent.job"
    try:
        if job_p.exists():
            job = job_p.read_text(encoding="utf-8").strip()
    except OSError:
        return None, "ilegible"
    return {"pid": pid, "inicio": inicio, "boot_id": boot, "job": job}, ""


def observar_arbol(att) -> str:
    """`vivo`, `ausente` o `incierto`.

    El supervisor y el agente son identidades distintas. El agente corre en otro grupo,
    así que un supervisor muerto no demuestra el cierre si la marca del agente sigue
    viva. Una marca a medias, ilegible o un pid que no se puede consultar deja la
    observación incierta. Un `proc` vacío y sin marcas es un lanzamiento que no llegó
    a existir.
    """
    proc = getattr(att, "proc", None) or {}
    duda = False
    identidades: list[dict] = []
    if proc.get("pid") and proc.get("inicio"):
        identidades.append(proc)
    elif proc.get("pid") or proc.get("inicio"):
        duda = True
        if proc.get("pid"):
            try:
                plataforma.estado_proc(proc)
            except (OSError, TypeError, ValueError):
                return "incierto"
    else:
        # Un `proc` vacío es una reserva vieja o a medias. No es ausencia.
        duda = True
    try:
        agente, fallo = _marca_agente(att)
    except OSError:
        return "incierto"
    if fallo:
        duda = True
    elif agente is not None:
        identidades.append(agente)
    if not identidades and not duda:
        return "ausente"
    try:
        estados = [plataforma.estado_proc(ident) for ident in identidades]
    except (OSError, TypeError, ValueError):
        return "incierto"
    if any(estado == "vivo" for estado in estados):
        return "vivo"
    for ident, estado in zip(identidades, estados):
        if estado == "corte":
            continue
        grupo = plataforma.presencia_grupo(
            int(ident["pid"]),
            str(ident.get("inicio") or ""),
            str(ident.get("job") or ""),
        )
        if grupo == "vivo":
            return "vivo"
        if grupo == "incierto":
            duda = True
    if duda:
        return "incierto"
    return "ausente"


def intento_vivo(att) -> bool:
    return observar_arbol(att) == "vivo"


def arbol_ausente(att) -> bool:
    return observar_arbol(att) == "ausente"


def _identidades(att) -> list[dict]:
    proc = getattr(att, "proc", None) or {}
    identidades = []
    if proc.get("pid") and proc.get("inicio"):
        identidades.append(proc)
    try:
        agente, fallo = _marca_agente(att)
    except OSError:
        return identidades
    if agente is not None and not fallo:
        identidades.append(agente)
    return identidades


def _senales(att) -> list[dict]:
    """Identidades que siguen vivas y se pueden señalar. Una duda no autoriza un pid ajeno."""
    proc = getattr(att, "proc", None) or {}
    candidatos = []
    if proc.get("pid") and proc.get("inicio"):
        candidatos.append(proc)
    try:
        agente, fallo = _marca_agente(att)
    except OSError:
        agente, fallo = None, "ilegible"
    if agente is not None and not fallo:
        candidatos.append(agente)
    vivos = []
    for ident in candidatos:
        try:
            if plataforma.estado_proc(ident) == "vivo":
                vivos.append(ident)
        except (OSError, TypeError, ValueError):
            continue
    return vivos


def _term_pid(pid: int) -> None:
    """Pide el cierre de ese grupo. No escala a KILL y no usa SIGCONT."""
    if os.name == "nt":
        try:
            os.kill(pid, signal.SIGTERM)
        except OSError:
            return
        return
    try:
        os.killpg(pid, signal.SIGTERM)
    except OSError:
        try:
            os.kill(pid, signal.SIGTERM)
        except OSError:
            return


CASOS_PRUEBAS = {
    "dinero": "dinero (pujas, precios, presupuestos o cobros)",
    "datos": "datos (escribir datos de producción)",
    "instala": "instala (instalar, borrar o deshacer en las máquinas)",
    "falla": "falla (una falla que ya pasó en producción)",
}


def texto_pruebas(block, revisar: bool = False) -> str:
    marca = getattr(block, "pruebas", "")
    if marca:
        caso = CASOS_PRUEBAS.get(marca, marca)
        if revisar:
            return (
                f"Marca de pruebas: este bloque lleva `pruebas: {marca}`, el caso {caso}; va UNA prueba."
            )
        return (
            f"Marca de pruebas: este bloque lleva `pruebas: {marca}`, el caso {caso}. Escribe UNA prueba."
        )
    if revisar:
        return "No pidas pruebas nuevas; lo que falte va en notas."
    return "No escribas pruebas nuevas: basta comprobar que corre."


def inputs_de_workflow(texto: str) -> list[tuple[str, bool]]:
    """(nombre, obligatorio) de los inputs de `workflow_dispatch` de un workflow."""
    out: list[tuple[str, bool]] = []
    en_dispatch = en_inputs = False
    actual: str | None = None
    for linea in texto.splitlines():
        if re.match(r"^\s*workflow_dispatch\s*:", linea):
            en_dispatch = True
            continue
        if en_dispatch and re.match(r"^\s+inputs\s*:", linea):
            en_inputs = True
            continue
        if en_inputs:
            m = re.match(r"^\s{6}([A-Za-z_][\w-]*)\s*:\s*$", linea)
            if m:
                actual = m.group(1)
                out.append((actual, False))
                continue
            m = re.match(r"^\s+required\s*:\s*true\s*$", linea)
            if m and actual:
                out[-1] = (actual, True)
                continue
            if linea.strip() and not linea.startswith("        "):
                if not re.match(r"^\s{4}\S", linea):
                    break
    return out


def es_archivo_prueba(ruta: str) -> bool:
    partes = [p.lower() for p in ruta.replace("\\", "/").split("/") if p]
    if not partes:
        return False
    nombre = partes[-1]
    return (
        any(p in ("tests", "test", "__tests__") for p in partes[:-1])
        or nombre.startswith("test_")
        or nombre.startswith("test")
        and nombre.endswith(".py")
        or "_test." in nombre
        or ".test." in nombre
    )


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
    avance: Callable[[], None] = lambda: None
    # MOTOR_PROGRAMADAS_SISTEMA=0: el banco de pruebas no lee crontab, plist ni schtasks reales,
    # tampoco en los subprocesos (daemon y aparte) que heredan el entorno.
    programadas_sistema: bool = field(
        default_factory=lambda: os.environ.get("MOTOR_PROGRAMADAS_SISTEMA") != "0"
    )


def _posix(cmd: str, bash: str = "") -> tuple:
    if os.name != "nt":
        return cmd, True, "/bin/bash"
    return argv_shell(cmd, bash)


RECETA = (
    "El motor corre cada script de la receta con `bash <script>`, sin argumentos, desde una copia "
    "del commit mergeado; MOTOR_SHA trae ese commit. "
    "Contrato de salida: 0 es bien, y el checklist solo sale 0 si producción ya corre el commit de "
    "MOTOR_SHA (si corre otro, sale distinto de 0). "
    "75 dice «no es el código; reintenta luego»: el motor reintenta a los 15 minutos sin culpar al "
    "agente, no hace rollback porque no cambió nada, y tres horas seguidas de 75 son atasco. "
    "Cualquier otro código es una falla: en el despliegue el motor hace rollback con el tercer script."
)


def _cola(p: subprocess.CompletedProcess) -> str:
    return (p.stdout + p.stderr)[-500:].strip()


def _run(
    cmd: str, cwd: Path | None, env: dict, timeout: float = 900, bash: str = ""
) -> subprocess.CompletedProcess:
    argv, shell, exe = _posix(cmd, bash)
    env = con_python_de_comando(env)
    with tempfile.TemporaryFile() as out_f, tempfile.TemporaryFile() as err_f:
        creado = plataforma.crear_proceso(
            argv,
            plataforma.Propiedad.COMANDO_CONTENIDO,
            cwd=cwd,
            env=env,
            stdout=out_f,
            stderr=err_f,
            stdin=subprocess.DEVNULL,
            shell=shell,
            executable=exe,
        )
        creado.liberar()
        p = creado.proceso
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


@contextlib.contextmanager
def _lock_despliegue(home: Path, repo: str):
    """Dos despliegues del mismo repo en la misma máquina, de uno en uno (también entre procesos)."""
    d = home / "locks-despliegue"
    d.mkdir(parents=True, exist_ok=True)
    f = open(d / (hashlib.sha256(repo.encode()).hexdigest()[:16] + ".lock"), "a+")
    try:
        try:
            import fcntl

            fcntl.flock(f, fcntl.LOCK_EX)
        except ImportError:
            import msvcrt

            f.seek(0)
            msvcrt.locking(f.fileno(), msvcrt.LK_LOCK, 1)
        yield
    finally:
        try:
            import fcntl

            fcntl.flock(f, fcntl.LOCK_UN)
        except ImportError:
            import msvcrt

            f.seek(0)
            msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)
        f.close()


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


def _texto_guia(f: Path) -> str:
    """El texto de una guía tal como el anexo del encargo lo pega."""
    return f.read_text(encoding="utf-8").strip()


PLANTILLA_PROMPT = (
    "Lee el encargo completo en {ruta} ({kb} KB) con tu herramienta de lectura de archivos, "
    "no con `cat`, y ejecútalo completo sin preguntar nada."
)


def prompt_de_encargo(ruta: str, brief: str) -> str:
    """Lo que se le dice al CLI al lanzarlo: lee el encargo de `ruta` (con su tamaño) y ejecútalo.
    Lo usan `start_attempt` y ops/pstack/mide.sh, para medir con el mismo prompt que el motor.
    El KB sale de los bytes del brief, no de sus caracteres."""
    kb = max(1, round(len(brief.encode("utf-8")) / 1024))
    return PLANTILLA_PROMPT.format(ruta=ruta, kb=kb)


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
    clis = dict.fromkeys(r.cli for r in conf.rutas.values())
    piden: dict[str, list[str]] = {}
    for (lane, papel), e in pc.exigencias.items():
        for cli in clis:
            for correccion in (False, True):
                for g in guias(e, cli, correccion):
                    quien = f"{papel} en {lane}"
                    if not (d / g).is_file() and quien not in piden.setdefault(g, []):
                        piden[g].append(quien)
    for g, quienes in piden.items():
        errores.append(
            f"pstack.toml: la guía {g} no está en la copia; la piden: {', '.join(quienes)}"
        )
    for r in conf.rutas.values():
        # NoMedido (sin lector) no invalida la conf. El mapa del CLI sí se exige.
        if not (d / "pstack-motor" / f"{r.cli}.md").is_file():
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
        self._dir_aparte: Path | None = None

    # ------------------------------------------------------------------ entorno
    def env_base(self, extra: dict | None = None) -> dict:
        return entorno_maquina(self.cx.maquina, extra)

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

    def prepara_repos(self, plan: Plan) -> list[dict]:
        """Lee los pines fuera de SQLite. No publica eventos."""
        pines = []
        for repo in dict.fromkeys(b.repo for b in plan.blocks):
            try:
                self.cx.ws.espejo(repo)
                sha, texto = self.conf_base(repo)
            except (OSError, RuntimeError) as e:
                linea = (str(e).splitlines() or ["no pude leer el repo"])[0][:240]
                raise PlanError([f"no pude leer {repo}: {linea}"]) from e
            pines.append(
                {
                    "repo": repo,
                    "sha": sha,
                    "texto": texto,
                    "privado": bool(self.cx.gh.es_privado(repo)),
                }
            )
        return pines

    def pin_repos(self, plan: Plan) -> None:
        """Fija en el log pines ya leídos. La aceptación de un plan usa un solo lote."""
        for pin in self.prepara_repos(plan):
            if any(
                e.kind == "RepoPinned" and e.data["repo"] == pin["repo"]
                for e in self.cx.store.events(plan.id)
            ):
                continue
            self.cx.store.append(
                plan.id,
                "",
                "RepoPinned",
                pin,
                ukey=claves.repo_fijado(plan.id, pin["repo"]),
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

    def _guias_del_intento(self, lane: str, role: str, cli: str, correccion: bool):
        """(conf pstack, copia fija, exigencia, guías) de un intento; None si la conf no usa pstack.
        Si falta una guía en la copia fija, el intento no se lanza: el error nombra el archivo."""
        pc, copia = self.cx.conf.pstack, self.asegura_pstack()
        if pc is None or copia is None:
            return None
        e = pc.exigencia(lane, role)
        lista = guias(e, cli, correccion)
        raiz = copia[1]
        for rel in lista:
            if not (raiz / rel).is_file():
                raise FileNotFoundError(
                    f"falta la guía {rel} en la copia fija {raiz}: el intento no se lanza"
                )
        return pc, copia, e, lista

    def _prepara_pstack(
        self,
        d: Path,
        lane: str,
        role: str,
        cli: str,
        cwd: Path,
        sesion: str,
        correccion: bool,
        guias_del=None,
    ) -> dict | None:
        """Fija lo que este intento exige y dónde se lee su registro; lo guarda en `pstack.json`.
        `collect` juzga contra esto, no contra la conf de entonces: la conf puede cambiar a medias.
        Si falta una guía en la copia fija, el intento no se lanza: el error nombra el archivo.
        `guias_del` es el chequeo ya hecho (`_guias_del_intento`), para no repetirlo."""
        if guias_del is None:
            guias_del = self._guias_del_intento(lane, role, cli, correccion)
        if guias_del is None:
            return None
        pc, (arbol, raiz), e, lista = guias_del
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
            "exigencia": e.to_dict(),
            "arbol": arbol,
            "raiz": str(raiz),
            "sesion": sesion,
            "registros": [*registros, str(d / "log.txt")],
            "guias": [
                {"ruta": rel, "bytes": len(_texto_guia(raiz / rel).encode("utf-8"))}
                for rel in lista
            ],
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
            ps.instrucciones(ps.Exigencia.from_dict(info["exigencia"]), info["cli"])
        )
        return "\n".join(partes) + "\n"

    def _anexo_guias(self, info: dict | None) -> str:
        """Las guías completas al final del encargo: un título por archivo y su ruta absoluta."""
        if info is None:
            return ""
        raiz = Path(info["raiz"])
        partes = ["## Anexo: tus guías\n"]
        for g in info["guias"]:
            f = raiz / g["ruta"]
            partes.append(f"### {g['ruta']}\n`{f}`\n\n{_texto_guia(f)}\n")
        return "\n".join(partes) + "\n"

    def _juzga_pstack(self, d: Path) -> dict | None:
        """Lee el registro real del intento, guarda `huella.json` y, si el motor exige pstack y falta
        algo, lo anota en huella.json y deja pasar la entrega. Un registro ilegible tambien pasa."""
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
            h = ps.huella(texto, info["cli"])
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
        if info["exigir"] and estado == "no_verificable":
            self._avisa_no_verificable(info["cli"])
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
        notice_id = f"pstack:{cli}:{dia}"
        texto = (
            f"En {self.cx.maquina.nombre}: no pude leer el registro de {cli}, así que no comprobé que "
            "usara poteto-mode. Dejé pasar la entrega. Este aviso sale una vez al día por herramienta."
        )
        self.cx.store.append(
            "",
            "",
            "NoticeRequested",
            {"notice_id": notice_id, "key": notice_id, "texto": texto},
            ukey=f"notice:{notice_id}",
        )
        self.cx.notifier.materializa(notice_id, texto, notice_id)

    # ------------------------------------------------------------------ intentos
    def _encargo(
        self, plan: Plan, block, a: m.StartAttempt, cwd, rc, info_pstack, snap=None
    ) -> str:
        """El brief completo de un intento: encargo, cómo se entrega y, al final, el anexo de guías."""
        seccion = self._seccion_pstack(info_pstack)
        if a.role == "implementar":
            brief = self._brief_implementar(plan, block, a, cwd, rc, seccion)
        else:
            comentarios = (
                self._comentarios_deepseek(snap, block, a.sha) if snap else []
            )
            brief = self._brief_revisar(plan, block, a, rc, seccion, comentarios)
        prefijo = ""
        if snap is not None:
            from .criterios import expediente_del_bloque, texto as texto_expediente
            prefijo = texto_expediente(expediente_del_bloque(block.id, snap))
        return prefijo + brief + self._anexo_guias(info_pstack)

    def start_attempt(self, plan: Plan, snap: m.Snapshot, a: m.StartAttempt) -> None:
        if not self._puede_lanzar(snap):
            return
        d = self.attempt_dir(plan.id, a.block, a.role, a.n)
        if (d / "started").exists():
            # La marca ya existe: se concilia. No se lanza otro agente por falta de pid.
            datos = _lee_json(d / "started") or {}
            estado, datos = _estado_marca(d, datos)
            datos["estado"] = estado
            _escribe_marca(d / "started", datos)
            evento = evento_de_arranque(datos)
            if evento is not None and "n" in evento:
                self.cx.store.append(
                    plan.id,
                    a.block,
                    "AttemptStarted",
                    evento,
                    ukey=claves.intento_iniciado(plan.id, a.block, a.role, a.n),
                )
            return
        if self._hay_otro_arbol(snap, a.block, a.role, a.n):
            return
        block = plan.block(a.block)
        ruta = self.cx.conf.rutas[a.modelo]
        guias_del = self._guias_del_intento(block.lane, a.role, ruta.cli, a.correccion)
        d.mkdir(parents=True, exist_ok=True)
        rc = self.repo_conf(snap, block.repo)
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
            espera = ["--entrega-rama", self.cx.ws.rama(plan.id, block.id)]
        else:
            cwd = self.cx.ws.copia(block.repo, a.sha, d / "revision")
            wip = []
            espera = ["--entrega-sha", a.sha]
        # un uuid por intento: el CLI lo usa como id de sesión y así el motor sabe qué registro leer
        marca_sesion = d / "sesion"
        if not marca_sesion.exists():
            marca_sesion.write_text(str(uuid.uuid4()), encoding="utf-8")
        sesion = marca_sesion.read_text(encoding="utf-8").strip()
        info_pstack = self._prepara_pstack(
            d, block.lane, a.role, ruta.cli, cwd, sesion, a.correccion, guias_del
        )
        if a.role == "revisar":
            bv = snap.blocks.get(a.block) if snap else None
            if bv is not None and bv.pr:
                self._dispara_deepseek(
                    plan, snap, block, int(bv.pr["numero"]), a.sha
                )
        brief = self._encargo(plan, block, a, cwd, rc, info_pstack, snap)
        (d / "brief.md").write_text(brief, encoding="utf-8")
        result = d / "result.json"
        prompt = prompt_de_encargo(str(d / "brief.md"), brief)
        argv = argv_de_ruta(
            ruta,
            brief=str(d / "brief.md"),
            prompt=prompt,
            result=str(result),
            pstack=info_pstack["raiz"] if info_pstack else "",
            sesion=sesion,
        )
        base = self.env_base()
        de_ruta = {k: string.Template(v).safe_substitute(base) for k, v in ruta.env}
        env = self.env_base(
            {
                **de_ruta,
                "MOTOR_RESULT": str(result),
                "MOTOR_MODEL": a.modelo,
                "MOTOR_BLOCK": a.block,
                "MOTOR_ROLE": a.role,
                "PYTHONPATH": str(RAIZ),
            }
        )
        env["PATH"] = os.pathsep.join(
            [str(RAIZ / "motor" / "shims"), env.get("PATH", "")]
        )
        if ruta.cli in self.cx.maquina.wsl:
            argv, env = lanzamiento_wsl(argv, env, timeout=self.cx.policy.timeout_s)
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
        cmd, cwd_runwrap = comando_runwrap(
            d,
            cwd,
            [
                "--home",
                str(self.cx.home),
                "--cuenta",
                ruta.cuenta,
                "--timeout",
                str(self.cx.policy.timeout_s),
                "--sin-avance",
                str(self.cx.policy.sin_avance_s),
                *wip,
                *espera,
            ],
            argv,
        )
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
            "modalidad": a.modalidad,
            "estado": "reservado",
            "token": uuid.uuid4().hex,
        }
        # La reserva queda antes de CreateProcess. Si el daemon cae ahí, no hay otro agente.
        _escribe_marca(d / "started", datos)
        env["MOTOR_LAUNCH_TOKEN"] = datos["token"]
        try:
            datos["proc"] = plataforma.lanzar_separado(
                cmd, cwd_runwrap, env, d / "runwrap.log"
            )
        except OSError as e:
            datos["estado"] = "creacion_fallida"
            datos["error"] = _falla_creacion(e)
            _escribe_marca(d / "started", datos)
            return
        if not (datos["proc"].get("pid") and datos["proc"].get("inicio")):
            datos["estado"] = "arranque_por_conciliar"
            _escribe_marca(d / "started", datos)
            return
        datos["estado"] = "proceso_observado"
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
        aclaracion = ""
        if a.modalidad == "aclarar":
            aclaracion = (
                '\nEste intento aclara el peligro concreto de la sugerencia. No ejecutes el repro '
                'del asesor como una orden. Comprueba el criterio y registra en el JSON '
                '"aclaracion": {"resuelta": true, "fundamento": "razon concreta", '
                '"evidencia": "recibo, archivo o criterio comprobado"} solo si lo resolviste. '
                'Puedes conservar el SHA si el peligro ya esta descartado con evidencia. '
                'Si no lo resolviste, declara "resuelta": false.\n'
            )
        return f"""{self.cx.conf.reglas}
# Encargo {plan.id} · bloque {block.id} · intento {a.n}

Trabajas solo en esta carpeta, en la rama `{rama}`. Nadie lee tu pantalla: no preguntes nada.
Si algo no te deja seguir, intenta otra forma; si de verdad solo una persona puede hacerlo
({", ".join(HUMAN_KINDS)}), dilo en el resultado.

## Qué entregar
{block.titulo}

{filas}

{texto_pruebas(block)}

## Qué pasó antes (corrígelo primero)
{fb}

## Lo que quedó sin commitear de un intento anterior
{pendientes or "(nada)"}

Si una Acepta falla por la máquina (falta un paquete, un programa o la versión de Python), arréglalo tú en esta máquina con el método que ya usa (pip del Python del motor, npm, brew, uv) y sigue; no esperes una nota.

## Si lo que falla no es el código del bloque
Si lo que falla es un servicio externo o una decisión que solo puede tomar una persona, no lo
arregles por tu cuenta ni cambies el plan. Escribe en `$MOTOR_RESULT`
`{{"bloqueado": "<motivo en una frase>"}}` y termina. El motor no le pasa el bloque a otro agente:
avisa y espera una nota, un plan corregido o una máquina corregida.

{seccion_pstack}## Cómo se entrega
1. Un commit por fila, con el id de la fila al inicio del mensaje. Nunca `--no-verify`.
2. `git push origin HEAD:refs/heads/{rama}` (nunca a `{rc.principal}`).
   PROHIBIDO mergear, cerrar, aprobar o marcar listo un PR (`gh pr merge`, la API, la web): eso
   solo lo hace el motor. Si te dicen que el merge falló por conflicto, trae `{rc.principal}` a tu
   rama (merge o rebase), resuelve y vuelve a entregar.
3. Escribe en `$MOTOR_RESULT` un JSON: {{"sha": "<git rev-parse HEAD>", "resumen": "..."}}.
   Lo que digas no cuenta como prueba: el motor verifica el SHA y corre cada `Acepta`.
{aclaracion}
"""

    def _brief_revisar(
        self,
        plan: Plan,
        block,
        a: m.StartAttempt,
        rc: RepoConf,
        seccion_pstack: str = "",
        comentarios_deepseek=None,
    ) -> str:
        filas = "\n".join(
            f"- {r.id}: {r.contenido}"
            + (f" | Acepta: `{r.acepta}`" if r.acepta else "")
            for r in block.rows
        )
        if comentarios_deepseek is None:
            try:
                prs = [
                    e
                    for e in self.cx.store.events(plan.id)
                    if e.kind == "PrOpened" and e.block == block.id
                ]
                fn = getattr(self.cx.gh, "comentarios_deepseek", None)
                if prs and fn is not None:
                    comentarios_deepseek = list(
                        fn(block.repo, int(prs[-1].data["numero"]), a.sha) or []
                    )
            except Exception:  # noqa: BLE001 - sin comentarios se revisa igual
                comentarios_deepseek = ()
        deepseek = ""
        if comentarios_deepseek:
            lineas = "\n".join(
                f"- {c.get('texto', c) if isinstance(c, dict) else c}"
                for c in comentarios_deepseek
            )
            deepseek = (
                "\n## Comentarios de DeepSeek para este SHA\n"
                "DeepSeek ya publicó estos comentarios para este SHA. Son sugerencias: "
                "nunca frenan nada por sí solos. Decide con la regla de siempre.\n"
                f"{lineas}\n"
            )
        antes = ""
        if a.feedback:
            antes = (
                "## Qué pasó antes (corrígelo primero)\n"
                + "\n".join(f"- {x}" for x in a.feedback)
                + "\n\n"
            )
        return f"""{self.cx.conf.reglas_revisor}
# Revisión {plan.id} · bloque {block.id} · SHA {a.sha}

Estás en una copia de solo lectura de ese SHA (diferencia contra `{rc.principal}`). Revisa contra lo pedido:

{filas}

{texto_pruebas(block, revisar=True)}
{deepseek}
{antes}{seccion_pstack}Un hallazgo es una sugerencia, no una orden: el motor no corre tu `repro` ni frena la entrega por el.
Lo que no sea el pedido va en `notas`.
Escribe en `$MOTOR_RESULT`:
{{"sha": "{a.sha}", "veredicto": "APROBADO" | "CAMBIOS",
  "bloqueantes": [{{"texto": "...", "repro": "comando", "arreglo": "..."}}], "notas": ["..."]}}
Escríbelo una sola vez, al final, con tu panel ya sintetizado. No escribas un veredicto provisional: el motor no te cierra mientras tus subagentes o tus llamadas a otros modelos sigan trabajando.
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
            obs = observar_arbol(att)
            if (
                not oc.exists()
            ):  # pudo aparecer entre la primera lectura y la consulta del pid
                proc = att.proc or {}
                wrapper = bool(proc.get("pid") and proc.get("inicio"))
                if obs == "vivo" and wrapper:
                    if (
                        time.time() - att.started
                        <= self.cx.policy.timeout_s + HOLGURA_VIVO_S
                    ):
                        return None
                    self._mata_intento(d, proc)
                    if observar_arbol(att) != "ausente":
                        return None
                    return {
                        "kind": "perdido",
                        "detalle": "sigue 'vivo' mucho después de su tope de tiempo; el motor lo detuvo",
                    }
                if obs == "incierto":
                    return None
                if obs == "vivo":
                    self._mata_intento(d, proc or None)
                    if observar_arbol(att) != "ausente":
                        return None
                else:
                    try:
                        corte = (
                            plataforma.estado_proc(proc) == "corte" if wrapper else False
                        )
                    except (OSError, TypeError, ValueError):
                        return None
                    if corte:
                        return {
                            "kind": "corte",
                            "detalle": "la máquina se apagó o reinició",
                        }
                    self._mata_intento(d, proc or None)
                    if observar_arbol(att) != "ausente":
                        return None
                return {
                    "kind": "perdido",
                    "detalle": "el proceso desapareció sin dejar resultado",
                }
        o = json.loads(oc.read_text(encoding="utf-8"))
        c = self._juzga_intento(plan, snap, block_id, att, d, o)
        if "toco_carpeta" in o:
            c["toco_carpeta"] = o["toco_carpeta"]
        return c

    @staticmethod
    def _veredicto_entregado(att: m.Attempt, d: Path) -> bool:
        try:
            datos = (d / entrega.ENTREGA).read_bytes()
        except OSError:
            return False
        return entrega.Espera(sha=att.sha).acepta(datos, d)

    def _juzga_intento(
        self,
        plan: Plan,
        snap: m.Snapshot,
        block_id: str,
        att: m.Attempt,
        d: Path,
        o: dict,
    ) -> dict:
        if o.get("creacion_agente") == "fallida":
            return _juicio_creacion(o)
        if (att.role == "implementar" and self._entrego(plan, block_id, d)) or (
            att.role == "revisar" and self._veredicto_entregado(att, d)
        ):
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
                errores=o.get("stderr_tail", ""),
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
            res = json.loads(entrega.ruta(d).read_text(encoding="utf-8"))
        except OSError:
            return {
                "kind": "sin_resultado",
                "detalle": "terminó sin escribir el resultado",
            }
        except ValueError:
            return {"kind": malo, "detalle": "result.json no es JSON válido"}
        if not revisando and (motivo := entrega.bloqueado(res)):
            return {
                "kind": "bloqueado",
                "detalle": motivo,
                "huella_maquina": huella.de_maquina(self.cx.maquina),
            }
        if motivo := entrega.resultado_invalido(res, revisando):
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
            aclarar = []
            from .criterios import (
                Aclarar,
                Expediente,
                Nota,
                Sugerencia,
                criterio_de_hallazgo,
                expediente_del_bloque,
                valorar,
            )
            exp = expediente_del_bloque(block_id, snap) if snap is not None else Expediente((), (), (), ())
            for i, hallazgo in enumerate(bloqueantes):
                if not isinstance(hallazgo, dict):
                    continue
                juicio = valorar(
                    Sugerencia(
                        id=f"{att.sha}:{i}",
                        intento_o_comentario=att.dir,
                        criterio=criterio_de_hallazgo(hallazgo, exp),
                        escenario_propuesto=str(hallazgo.get("texto") or ""),
                        conducta_rota=str(hallazgo.get("texto") or ""),
                        repro_propuesto=str(hallazgo.get("repro") or ""),
                    ),
                    exp,
                )
                if isinstance(juicio, Aclarar):
                    aclarar.append(
                        {
                            "id": f"{att.sha}:{i}",
                            "criterios": list(juicio.criterios),
                            "mandato": juicio.mandato,
                            "bloque": juicio.alcance.bloque,
                            "criterio": juicio.alcance.criterio,
                            "escenario": juicio.alcance.escenario,
                        }
                    )
                    continue
                motivo = juicio.motivo if isinstance(juicio, Nota) else "queda como nota"
                notas.append(f"asesoria, no es una orden: {motivo}")
            datos = {
                "sha": att.sha,
                "veredicto": veredicto,
                "modelo": att.modelo,
                "bloqueantes": bloqueantes,
                "notas": notas,
            }
            if aclarar:
                datos["aclarar"] = aclarar
            self.cx.store.append(
                plan.id,
                block_id,
                "Reviewed",
                datos,
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
                **self._aclaracion_entregada(att, res),
            }
        if self._es_la_base_al_cargar(snap, block, punta):
            return {
                "kind": "sin_cambios",
                "detalle": "entregó la punta de la rama principal tal como estaba al cargar el plan: "
                "no cambió nada",
                **self._aclaracion_entregada(att, res),
            }
        entregado = {
            "kind": "entregado",
            "sha": punta,
            "resumen": str(res.get("resumen", ""))[:500],
            **self._aclaracion_entregada(att, res),
        }
        if not getattr(block, "pruebas", ""):
            try:
                rc2 = self.repo_conf(snap, block.repo)
                espejo = self.cx.ws.espejo(block.repo)
                base = git(
                    "merge-base", f"origin/{rc2.principal}", punta, cwd=espejo
                ).stdout.strip()
                if base:
                    diff = git(
                        "diff", "--name-only", "--diff-filter=A", base, punta, cwd=espejo
                    ).stdout
                    nuevas = [x for x in diff.splitlines() if es_archivo_prueba(x)]
                    if nuevas:
                        entregado["pruebas_sin_marca"] = nuevas
            except Exception:  # noqa: BLE001 - anotar nunca frena una entrega
                pass
        return entregado

    @staticmethod
    def _aclaracion_entregada(att, res) -> dict:
        if att.modalidad == "aclarar" and isinstance(res.get("aclaracion"), dict):
            return {"aclaracion": res["aclaracion"]}
        return {}

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
        foto = self.foto_de(plan, snap, a.block, a.role)
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
        """Hay una entrega válida. El sha del implementador es la punta remota de su rama."""
        try:
            res = json.loads(entrega.ruta(d).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return False
        punta = ""
        if not entrega.bloqueado(res) and not entrega.resultado_invalido(res, False):
            block = plan.block(block_id)
            punta = self.cx.ws.punta(block.repo, self.cx.ws.rama(plan.id, block_id))
        return entrega.es_valida(res, revisando=False, punta=punta)

    def _hay_otro_arbol(self, snap: m.Snapshot, block: str, role: str, n: int) -> bool:
        """Otro intento abierto del mismo papel cuyo árbol no está demostrado ausente."""
        abiertos = []
        bv = snap.blocks.get(block) if snap is not None else None
        if bv is not None:
            abiertos.extend(
                a
                for a in bv.attempts
                if a.role == role and a.n != n and a.ended is None
            )
        for (bid, an, arole), att in getattr(snap, "huerfanos", ()) or ():
            if bid == block and arole == role and an != n and att.ended is None:
                abiertos.append(att)
        return any(not arbol_ausente(a) for a in abiertos)

    def _puede_lanzar(self, snap: m.Snapshot) -> bool:
        """Retención del home o un plan que no está activo no arrancan agentes."""
        if getattr(snap, "control", "Activo") != "Activo":
            return False
        home = getattr(self.cx, "home", None)
        if home is None:
            return True
        from .control import leer

        return leer(home) is None

    def foto_de(self, plan: Plan, snap: m.Snapshot, block: str, role: str) -> str:
        w = self.cx.home / "wt" / plan.id / block
        if role != "implementar" or not w.exists():
            return ""
        privado = False
        if block in snap.blocks:
            repo = snap.blocks[block].block.repo
            privado = bool(snap.repos.get(repo, {}).get("privado", False))
        return foto_wip(w, self.cx.ws.rama_wip(plan.id, block), local=not privado)

    def cerrar_arboles(self, intentos: list, plazo_s: float) -> list:
        """SIGTERM a cada identidad viva, espera el plazo y recién entonces escala.

        Devuelve los intentos que no están demostrados ausentes. No escribe eventos
        y no envía SIGCONT. Una observación incierta vuelve en la lista.
        """
        for a in intentos:
            for ident in _senales(a):
                _term_pid(int(ident["pid"]))
        limite = time.time() + max(0.0, plazo_s)
        while time.time() < limite and any(intento_vivo(a) for a in intentos):
            time.sleep(0.05)
        for a in intentos:
            for ident in _identidades(a):
                pid = int(ident["pid"])
                inicio = str(ident.get("inicio") or "")
                try:
                    sigue = plataforma.estado_proc(ident) == "vivo"
                except (OSError, TypeError, ValueError):
                    continue
                if sigue:
                    plataforma.matar_proc(pid, inicio)
                elif (
                    plataforma.presencia_grupo(pid, inicio, str(ident.get("job") or ""))
                    == "vivo"
                ):
                    plataforma.cerrar_huerfanos(
                        pid, inicio, str(ident.get("job") or "")
                    )
        return [a for a in intentos if not arbol_ausente(a)]

    def _mata_intento(self, d: Path, proc: dict | None) -> None:
        """Detiene lo que quede de un intento perdido (agente y supervisor) antes de relanzar en la
        misma carpeta. Solo mata un pid si sigue siendo el proceso que se lanzó (misma hora de inicio)."""
        if proc and proc.get("pid"):
            plataforma.matar_proc(int(proc["pid"]), proc.get("inicio", ""))
            plataforma.cerrar_huerfanos(
                int(proc["pid"]),
                str(proc.get("inicio") or ""),
                str(proc.get("job") or ""),
            )
        try:
            pid = int((d / "agent.pid").read_text(encoding="utf-8").strip())
            inicio = (d / "agent.inicio").read_text(encoding="utf-8").strip()
        except (OSError, ValueError):
            return
        try:
            job = (d / "agent.job").read_text(encoding="utf-8").strip()
        except OSError:
            job = ""
        plataforma.matar_proc(pid, inicio)
        plataforma.cerrar_huerfanos(pid, inicio, job)

    def _corre_en(
        self, plan: Plan, snap: m.Snapshot, block, sha: str, nombre: str, cmd: str
    ):
        """Corre `cmd` en una copia limpia de `sha` (con el entorno de pruebas del repo)."""
        rc = self.repo_conf(snap, block.repo)
        w = self.cx.ws.copia(
            block.repo, sha, self.cx.home / "tmp" / f"{nombre}-{plan.id}-{block.id}"
        )
        tope = float(self.cx.conf.tiempos.get("repro_tope_s", 300))
        try:
            return _run(
                cmd,
                w,
                self.env_base(dict(rc.prueba_env)),
                tope,
                bash=self.cx.maquina.bash,
            )
        finally:
            self.cx.avance()

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

    def _anota_arranque_aparte(self, plan, block, kind, datos) -> None:
        started_kind = self._APARTE[kind][0]
        self.cx.store.append(
            plan.id,
            block,
            started_kind,
            datos,
            ukey=claves.POR_TIPO[started_kind](plan.id, block, datos["sha"], datos["n"]),
        )

    def _recupera_arranques_aparte(self, plan, a, kind) -> None:
        raiz = self.cx.home / "aparte" / plan.id
        pendientes = []
        for d in raiz.glob(f"{a.block}-{kind}-{a.sha[:12]}-*"):
            marca = d / "started"
            if not marca.exists():
                continue
            crudo = _lee_json(marca) or {}
            if crudo.get("sha") != a.sha:
                continue
            estado, datos = _estado_marca(d, crudo)
            datos["estado"] = estado
            _escribe_marca(marca, datos)
            if evento_de_arranque(datos) is not None:
                pendientes.append(datos)
        for datos in sorted(pendientes, key=lambda x: int(x["n"])):
            self._anota_arranque_aparte(plan, a.block, kind, datos)

    def _arranque_aparte_abierto(self, plan, a, kind) -> bool:
        """Una reserva o una ventana ambigua no abre otro proceso del mismo paso.

        La creación fallida no gasta el paso: se puede volver a intentar. No se inventa
        la causa de un `proc` viejo vacío.
        """
        raiz = self.cx.home / "aparte" / plan.id
        if not raiz.is_dir():
            return False
        for d in raiz.glob(f"{a.block}-{kind}-{a.sha[:12]}-*"):
            marca = d / "started"
            crudo = _lee_json(marca)
            if crudo is None:
                return True
            if crudo.get("sha") != a.sha:
                continue
            estado, datos = _estado_marca(d, crudo)
            if datos != crudo:
                _escribe_marca(marca, datos)
            if estado in ("reservado", "arranque_por_conciliar"):
                return True
        return False

    def _aparte(self, kind: str, plan: Plan, snap: m.Snapshot, a) -> None:
        started_kind, terminal_kind, campo = self._APARTE[kind]
        block = plan.block(a.block)
        h_insumo, h_maquina = self._huellas(campo, block, snap)
        self._recupera_arranques_aparte(plan, a, kind)
        starteds = [
            e
            for e in self.cx.store.events(plan.id)
            if e.kind == started_kind
            and e.block == a.block
            and e.data.get("sha") == a.sha
        ]
        eventos = self.cx.store.events(plan.id)
        reverificar = False
        if kind == "verify":
            previos = [
                e
                for e in eventos
                if e.kind == "Verified"
                and e.block == a.block
                and e.data.get("sha") == a.sha
            ]
            if previos and m._es_falla_maquina(previos[-1].data):
                ultima_nota = max(
                    (
                        e.ts
                        for e in eventos
                        if e.kind == "Note" and e.block == a.block
                    ),
                    default=0.0,
                )
                reverificar = ultima_nota > previos[-1].ts
        terminales = [
            e
            for e in eventos
            if e.kind == terminal_kind
            and e.block == a.block
            and e.data.get("sha") == a.sha
            and huella.vigente(e.data, h_insumo, h_maquina)
        ]
        if (
            kind in ("deploy", "prove")
            and terminales
            and terminales[-1].data.get("codigo") == 75
            and not terminales[-1].data.get("ok")
        ):
            reverificar = True  # el planificador ya aplica la espera de 15 minutos
        if starteds:
            ultimo = starteds[-1]
            pendiente = not terminales or ultimo.id > terminales[-1].id
            d = Path(ultimo.data["dir"]) if ultimo.data.get("dir") else None
            if d is not None:
                oc = d / "outcome.json"
                if oc.exists():
                    payload = json.loads(oc.read_text(encoding="utf-8"))
                    if payload.get("error"):
                        oc.replace(d / "outcome-error.json")
                        if kind in ("deploy", "prove"):
                            tipo = "instalar" if kind == "deploy" else "probar"
                            ident = ultimo.data.get("intencion") or self._confirma_intencion(
                                plan, a.block, tipo, a.sha, f"{tipo}:{a.sha}"
                            )
                            self._efecto(plan, a.block, ident, "incierto", str(payload["error"]))
                            self._avisa_incierto(plan, snap, a.block, ident)
                            return
                        raise RuntimeError(str(payload["error"]))
                    datos = payload.get("data") or {}
                    if kind in ("deploy", "prove") and datos.get("sha") == a.sha:
                        tipo = "instalar" if kind == "deploy" else "probar"
                        ident = ultimo.data.get("intencion") or self._confirma_intencion(
                            plan, a.block, tipo, a.sha, f"{tipo}:{a.sha}"
                        )
                        res = self._resultado_de(kind, datos)
                        self._efecto(plan, a.block, ident, res, str(datos.get("detalle") or ""))
                        if res == "incierto":
                            self._avisa_incierto(plan, snap, a.block, ident)
                    if not pendiente and not reverificar:
                        return
                    if (
                        pendiente
                        and datos.get("sha") == a.sha
                        and datos.get("huella", h_insumo) == h_insumo
                        and datos.get("huella_maquina", h_maquina) == h_maquina
                    ):
                        if terminal_kind == "Verified":
                            anotado = self.cx.store.append(
                                plan.id,
                                a.block,
                                "Verified",
                                datos,
                                ukey=claves.verificado(
                                    plan.id,
                                    a.block,
                                    a.sha,
                                    h_insumo,
                                    h_maquina,
                                    vez=int(ultimo.data.get("n", 0))
                                    if int(ultimo.data.get("n", 0)) > 1
                                    else 0,
                                ),
                            )
                        elif terminal_kind == "Deployed":
                            anotado = self.cx.store.append(
                                plan.id,
                                a.block,
                                "Deployed",
                                datos,
                                ukey=claves.desplegado(
                                    plan.id,
                                    a.block,
                                    a.sha,
                                    h_insumo,
                                    h_maquina,
                                    vez=int(ultimo.data.get("n", 0))
                                    if datos.get("codigo") == 75
                                    else 0,
                                ),
                            )
                        else:
                            anotado = self.cx.store.append(
                                plan.id,
                                a.block,
                                "Proved",
                                datos,
                                ukey=claves.probado(
                                    plan.id,
                                    a.block,
                                    a.sha,
                                    h_insumo,
                                    h_maquina,
                                    vez=int(ultimo.data.get("n", 0))
                                    if datos.get("codigo") == 75
                                    else 0,
                                ),
                            )
                        if anotado or datos.get("codigo") != 75:
                            return
                else:
                    if not pendiente and not reverificar:
                        return
                    proc = ultimo.data.get("proc") or {}
                    if proc and plataforma.estado_proc(proc) == "vivo":
                        return
        if kind in ("deploy", "prove") and self._frena_escritor(plan, snap, a, kind, starteds, terminales):
            return
        if self._arranque_aparte_abierto(plan, a, kind):
            return
        n = max((int(e.data.get("n", 0)) for e in starteds), default=0) + 1
        d = self.cx.home / "aparte" / plan.id / f"{a.block}-{kind}-{a.sha[:12]}-{n}"
        d.mkdir(parents=True, exist_ok=True)
        marcados: dict[str, set[str]] = {}
        for e in self.cx.store.events(plan.id):
            if (
                e.kind == "DeployStarted"
                and e.data.get("sha")
                and e.data.get("dir")
                and (Path(e.data["dir"]) / "desplegando").exists()
            ):
                marcados.setdefault(e.block, set()).add(e.data["sha"])
        spec = {
            "kind": kind,
            "block": a.block,
            "sha": a.sha,
            "huella": h_insumo,
            "huella_maquina": h_maquina,
            "deploy_marcados": {bid: sorted(shas) for bid, shas in marcados.items()},
            "programadas_sistema": self.cx.programadas_sistema,
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
        if kind in ("deploy", "prove"):
            tipo = "instalar" if kind == "deploy" else "probar"
            spec["intencion"] = self._id_intencion(a.block, tipo, f"{a.sha}:{h_insumo}:{h_maquina}")
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
        datos_started = {
            "sha": a.sha,
            "dir": str(d),
            "proc": {},
            "n": n,
            "huella": h_insumo,
            "huella_maquina": h_maquina,
            "estado": "reservado",
            "token": uuid.uuid4().hex,
        }
        if "intencion" in spec:
            datos_started["intencion"] = spec["intencion"]
        _escribe_marca(d / "started", datos_started)
        try:
            datos_started["proc"] = plataforma.lanzar_separado(
                cmd,
                RAIZ,
                self.env_base(
                    {
                        "PYTHONPATH": str(RAIZ),
                        "MOTOR_LAUNCH_TOKEN": datos_started["token"],
                    }
                ),
                d / "aparte.log",
            )
        except OSError as e:
            datos_started["estado"] = "creacion_fallida"
            datos_started["error"] = _falla_creacion(e)
            _escribe_marca(d / "started", datos_started)
            return
        if not (datos_started["proc"].get("pid") and datos_started["proc"].get("inicio")):
            datos_started["estado"] = "arranque_por_conciliar"
            _escribe_marca(d / "started", datos_started)
            return
        datos_started["estado"] = "proceso_observado"
        _escribe_marca(d / "started", datos_started)
        self._anota_arranque_aparte(plan, a.block, kind, datos_started)

    def verify(self, plan: Plan, snap: m.Snapshot, a: m.Verify) -> None:
        bv = snap.blocks.get(a.block) if snap else None
        if bv is not None and bv.pr:
            self._dispara_deepseek(
                plan, snap, plan.block(a.block), int(bv.pr["numero"]), a.sha
            )
        self._aparte("verify", plan, snap, a)

    def _verify_data(self, plan: Plan, snap: m.Snapshot, a: m.Verify) -> dict:
        block = plan.block(a.block)
        rc = self.repo_conf(snap, block.repo)
        w = self.cx.ws.copia(
            block.repo, a.sha, self.cx.home / "tmp" / f"verifica-{plan.id}-{a.block}"
        )
        fallas, faltan = [], []
        codigos: list[int] = []
        h_insumo, h_maquina = self._huellas("verified", block, snap)
        tope = float(self.cx.conf.tiempos.get("verifica_tope_s", 1800))
        for r in block.rows:
            if r.acepta:
                p = _run(
                    r.acepta,
                    w,
                    self.env_base(dict(rc.prueba_env)),
                    tope,
                    bash=self.cx.maquina.bash,
                )
                if p.returncode != 0:
                    codigos.append(p.returncode)
                    fallas.append(
                        f"{r.id}: `{r.acepta}` salió {p.returncode}: {(p.stdout + p.stderr)[-300:]}"
                    )
                    from .previa import faltantes_de_salida

                    faltan += faltantes_de_salida(p.stdout + p.stderr, p.returncode)
        codigo = next((c for c in codigos if c in (126, 127)), codigos[0] if codigos else 0)
        return {
            "sha": a.sha,
            "ok": not fallas,
            "detalle": "\n".join(fallas),
            "codigo": codigo,
            "falta_comando": sorted(set(faltan)),
            "huella": h_insumo,
            "huella_maquina": h_maquina,
            "evidencia": 1,
            "criterios": [
                f"{plan.id}/{block.id}/{r.id}" for r in block.rows if r.acepta
            ],
        }

    def _comentarios_deepseek(self, snap: m.Snapshot, block, sha: str) -> list:
        """Comentarios de DeepSeek ya publicados para ese SHA. Sin ellos, se revisa igual."""
        bv = snap.blocks.get(block.id) if snap else None
        if bv is None or not bv.pr:
            return []
        fn = getattr(self.cx.gh, "comentarios_deepseek", None)
        if fn is None:
            return []
        try:
            return list(fn(block.repo, int(bv.pr["numero"]), sha) or [])
        except Exception:  # noqa: BLE001 - DeepSeek nunca frena la revisión
            return []

    def _dispara_deepseek(
        self, plan: Plan, snap: m.Snapshot, block, numero: int, sha: str
    ) -> None:
        """Dispara la revisión de DeepSeek de este PR. Un SHA ya disparado no se repite."""
        try:
            if any(
                e.kind == "DeepSeekDisparado"
                and e.block == block.id
                and e.data.get("sha") == sha
                for e in self.cx.store.events(plan.id)
            ):
                return
            fn = getattr(self.cx.gh, "disparar_workflow", None)
            if fn is None:
                return
            espejo = self.cx.ws.espejo(block.repo)
            worker = git(
                "show", f"{sha}:.github/workflows/ai-review-worker.yml", cwd=espejo
            )
            if worker.returncode != 0:
                return
            publish = git(
                "show", f"{sha}:.github/workflows/ai-review-publish.yml", cwd=espejo
            )
            if publish.returncode == 0:
                archivo, texto = "ai-review-publish.yml", publish.stdout
            else:
                archivo, texto = "ai-review-worker.yml", worker.stdout
            rc = self.repo_conf(snap, block.repo)
            base = git(
                "rev-parse", f"origin/{rc.principal}", cwd=espejo
            ).stdout.strip()
            previos = sum(
                1
                for e in self.cx.store.events(plan.id)
                if e.kind == "DeepSeekDisparado" and e.block == block.id
            )
            valores = {
                "pr_number": str(numero),
                "head_sha": sha,
                "base_sha": base,
                "request_id": str(1000 + previos + 1),
                "coordinator_run_id": str(numero),
                "finding_identity": "current",
            }
            inputs = {}
            for nombre, obligatorio in inputs_de_workflow(texto):
                if nombre in valores and (obligatorio or nombre == "pr_number"):
                    inputs[nombre] = valores[nombre]
                elif obligatorio:
                    inputs[nombre] = valores.get(nombre, "motor")
            fn(block.repo, archivo, inputs)
            self.cx.store.append(
                plan.id,
                block.id,
                "DeepSeekDisparado",
                {"sha": sha, "pr": numero, "workflow": archivo, "inputs": inputs},
                ukey=claves.deepseek_disparado(plan.id, block.id, sha),
            )
        except Exception:  # noqa: BLE001 - DeepSeek es una sugerencia, nunca frena
            return

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
        self._dispara_deepseek(plan, snap, block, pr.numero, a.sha)

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
        mismo_contenido = v.head == sha
        if v.estado == "mergeado" and not mismo_contenido:
            d = self.cx.ws.espejo(block.repo)
            original = git("rev-parse", "--verify", f"{sha}^{{tree}}", cwd=d)
            if original.returncode == 0:
                punta = git("rev-parse", "--verify", f"{v.head}^{{tree}}", cwd=d)
                if punta.returncode != 0:
                    # GitHub puede borrar la rama al unir: pedir el commit conservado por el PR.
                    r = git("fetch", "-q", "origin", v.head, cwd=d)
                    if r.returncode != 0:
                        raise RuntimeError(
                            f"no pude comprobar el contenido del PR {numero}: {r.stderr[-300:]}"
                        )
                    punta = git("rev-parse", "--verify", f"{v.head}^{{tree}}", cwd=d)
                mismo_contenido = (
                    punta.returncode == 0
                    and original.stdout.strip() == punta.stdout.strip()
                )
        if v.estado != "mergeado" or not mismo_contenido:
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

    def reconcilia_prs(self, plan: Plan, snap: m.Snapshot) -> None:
        """Reconoce uniones externas tambien fuera de CHECKS, antes de decidir otro intento."""
        for bid, bv in snap.blocks.items():
            if not bv.pr or not bv.head() or bv.running_productivo():
                continue
            k = f"pr:{plan.id}:{bid}:{bv.pr['numero']}"
            ahora = time.time()
            if ahora - self._ultimo_poll.get(k, 0) < self.cx.checks_cada_s:
                continue
            self._ya_mergeado(plan, snap, bid, bv.head())
            self._ultimo_poll[k] = ahora

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
        numero = bv.pr.get("numero") if bv.pr else None
        ident = self._id_intencion(block_id, "merge", f"{sha}:{numero}")
        propia = any(
            e.kind == "Intencion" and e.data.get("id") == ident
            for e in self.cx.store.events(plan.id)
        )
        if propia:
            self._efecto(plan, block_id, ident, "aplicado", commit)
        clave = f"externo:{block_id}"
        avisado = clave in bv.notices or _aviso_previo(
            self.cx.store.events(plan.id), clave
        )  # el log, no solo la foto: un reintento con la foto vieja no avisa dos veces
        if not propia and not avisado:
            self.notify(
                plan,
                snap,
                m.Notify(clave, block_id, "externo", (("bloque", bv.block.aviso),)),
            )
        datos = {"sha": sha, "commit": commit}
        if not propia:
            datos["externo"] = True
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
        relanzadas = tuple(
            dict.fromkeys(
                str(rid)
                for e in self.cx.store.events(plan.id)
                if e.kind == "ChecksRelanzados"
                and e.block == a.block
                and e.data.get("sha") == a.sha
                and e.data.get("motivo") == "saltada"
                for rid in e.data.get("run_ids", [])
            )
        )
        v = self.cx.gh.checks(
            block.repo,
            int(snap.blocks[a.block].pr["numero"]),
            a.sha,
            tuple(dict.fromkeys([*rc.checks_extra, *block.checks])),
            relanzadas,
        )
        if not v.terminal:
            # Se anota solo cuando (faltan, github_termino) cambia respecto del último
            # ChecksEsperando del store, no del snap: un sondeo puede traer un snap fijo.
            previos = [
                e
                for e in self.cx.store.events(plan.id)
                if e.kind == "ChecksEsperando"
                and e.block == a.block
                and e.data.get("sha") == a.sha
            ]
            ultimo = previos[-1].data if previos else None
            if ultimo is None or (
                list(ultimo.get("faltan", [])),
                ultimo.get("github_termino"),
            ) != (list(v.faltan), v.github_termino):
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
                        plan.id,
                        a.block,
                        a.sha,
                        tuple(v.faltan),
                        v.github_termino,
                        vez=len(previos),
                    ),
                )
            self._relanza_saltada(plan, snap, a, v)
            return
        if not v.green and self._relanza_fallidas_tras_reentrega(plan, snap, a, v):
            return  # una falla pasajera no se le cuenta todavía al agente
        if (
            v.terminal
        ):  # solo se anota el resultado final: los sondeos no ensucian el log
            detalle = ""
            if v.fallas:
                detalle = self._detalle_rojo(plan, snap, a, v)
            previos = sum(
                1
                for e in self.cx.store.events(plan.id)
                if e.kind == "ChecksResult"
                and e.block == a.block
                and e.data.get("sha") == a.sha
            )
            self.cx.store.append(
                plan.id,
                a.block,
                "ChecksResult",
                {"sha": a.sha, "green": v.green, "detalle": detalle},
                ukey=claves.checks_resultado(plan.id, a.block, a.sha, vez=previos),
            )

    def _relanza_saltada(self, plan, snap, a, v) -> None:
        """Una obligatoria saltada en borrador y que GitHub ya no va a correr: se relanza una vez."""
        if not (v.github_termino and v.faltan):
            return
        esp = snap.blocks[a.block].esperando.get(a.sha) or {}
        if not esp.get("github_termino"):
            return
        espera = getattr(getattr(self.cx, "policy", None), "aviso_incoherente_s", 1800)
        if time.time() - esp.get("desde", time.time()) < espera:
            return
        if any(
            e.kind == "ChecksRelanzados"
            and e.block == a.block
            and e.data.get("sha") == a.sha
            and e.data.get("motivo") == "saltada"
            for e in self.cx.store.events(plan.id)
        ):
            return
        repo = plan.block(a.block).repo
        ids = []
        for nombre in v.faltan:
            for c in v.corridas:
                if c.get("nombre") == nombre and c.get("run_id"):
                    self.cx.gh.relanzar_corrida(repo, c["run_id"])
                    ids.append(c["run_id"])
        if ids:
            self.cx.store.append(
                plan.id,
                a.block,
                "ChecksRelanzados",
                {"sha": a.sha, "motivo": "saltada", "run_ids": ids},
                ukey=claves.checks_relanzados(plan.id, a.block, a.sha, "saltada"),
            )

    def _relanza_fallidas_tras_reentrega(self, plan, snap, a, v) -> bool:
        eventos = self.cx.store.events(plan.id)
        rojos = [
            e
            for e in eventos
            if e.kind == "ChecksResult"
            and e.block == a.block
            and e.data.get("sha") == a.sha
            and not e.data.get("green")
        ]
        if not rojos:
            return False
        bv = snap.blocks[a.block]
        if bv.entrega_ts(a.sha) <= rojos[-1].ts:
            return False
        if any(
            e.kind == "ChecksRelanzados"
            and e.block == a.block
            and e.data.get("sha") == a.sha
            and e.data.get("motivo") == "fallidas"
            and e.ts > rojos[-1].ts
            for e in eventos
        ):
            return False
        repo = plan.block(a.block).repo
        ids = []
        for c in v.corridas_fallidas:
            if c.get("run_id"):
                self.cx.gh.relanzar_fallidas(repo, c["run_id"])
                ids.append(c["run_id"])
        if not ids:
            return False
        previos = sum(
            1
            for e in eventos
            if e.kind == "ChecksRelanzados"
            and e.block == a.block
            and e.data.get("sha") == a.sha
            and e.data.get("motivo") == "fallidas"
        )
        self.cx.store.append(
            plan.id,
            a.block,
            "ChecksRelanzados",
            {"sha": a.sha, "motivo": "fallidas", "run_ids": ids},
            ukey=claves.checks_relanzados(plan.id, a.block, a.sha, "fallidas", previos),
        )
        return True

    def _detalle_rojo(self, plan, snap, a, v) -> str:
        numero = int((snap.blocks[a.block].pr or {}).get("numero", 0))
        partes = [f"fallaron: {list(v.fallas)} en el PR #{numero}"]
        log = getattr(self.cx.gh, "log_fallido", None)
        for c in v.corridas_fallidas:
            partes.append(f"{c.get('nombre', '')}: {c.get('url', '')}".strip())
            if log and c.get("run_id"):
                texto = log(plan.block(a.block).repo, c["run_id"])
                if texto:
                    partes.append("\n".join(texto.splitlines()[-80:]))
        return "\n".join(partes)

    def _checks_rojo_por_merge(self, plan, t, detalle: str) -> None:
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
            {"sha": t.sha, "green": False, "detalle": detalle},
            ukey=claves.checks_resultado(plan.id, t.block, t.sha, rechazo=rechazos),
        )

    def _id_intencion(self, block: str, tipo: str, entradas: str) -> str:
        return f"{block}:{tipo}:{entradas}"

    def _confirma_intencion(self, plan: Plan, block: str, tipo: str, entradas: str, recibo: str) -> str:
        from . import formato

        home = getattr(self.cx, "home", None)
        if home is not None:
            formato.activa(home)
        ident = self._id_intencion(block, tipo, entradas)
        self.cx.store.append(
            plan.id,
            block,
            "Intencion",
            {
                "id": ident,
                "bloque": block,
                "tipo": tipo,
                "entradas": entradas,
                "recibo_esperado": recibo,
                "observador": "effects",
            },
            ukey=claves.intencion(plan.id, ident),
        )
        return ident

    def _resultado_de(self, kind: str, datos: dict) -> str:
        if datos.get("ok"):
            return "aplicado"
        if (
            kind == "deploy"
            and datos.get("produccion_tocada")
            and datos.get("rollback_ok") is False
        ):
            return "incierto"
        return "no_aplicado"

    def _efecto(
        self, plan: Plan, block: str, ident: str, resultado: str, evidencia: str = ""
    ) -> None:
        anteriores = [
            e
            for e in self.cx.store.events(plan.id, "EfectoObservado")
            if e.data.get("intencion") == ident
        ]
        if anteriores and anteriores[-1].data.get("resultado") == resultado:
            return
        vez = sum(e.data.get("resultado") == resultado for e in anteriores)
        self.cx.store.append(
            plan.id,
            block,
            "EfectoObservado",
            {"intencion": ident, "resultado": resultado, "evidencia": evidencia[:300]},
            ukey=claves.efecto_observado(plan.id, ident, resultado, vez),
        )

    def _ultimo_efecto(self, plan: Plan, ident: str) -> str | None:
        ultimo = None
        for e in self.cx.store.events(plan.id):
            if e.kind == "EfectoObservado" and str(e.data.get("intencion") or "") == ident:
                ultimo = str(e.data.get("resultado") or "")
        return ultimo

    def _avisa_incierto(self, plan: Plan, snap: m.Snapshot, block: str, ident: str) -> None:
        clave = f"incierto:{ident}"
        bv = snap.blocks.get(block)
        if (
            (bv and clave in bv.notices)
            or clave in snap.plan_notices
            or _aviso_previo(self.cx.store.events(plan.id), clave)
        ):
            return
        self.notify(plan, snap, m.Notify(clave, block, "incierto", ()))

    def _otra_abierta(self, plan, block: str, tipo: str, excepto: str) -> str:
        ids: list[str] = []
        for e in self.cx.store.events(plan.id):
            if e.kind != "Intencion" or e.block != block or e.data.get("tipo") != tipo:
                continue
            ident = str(e.data.get("id") or "")
            if tipo == "merge" and ident:
                entradas = str(e.data.get("entradas") or ident.removeprefix(f"{block}:merge:"))
                sha = entradas.rsplit(":", 1)[0]
                confirmado = next(
                    (
                        x
                        for x in self.cx.store.events(plan.id, "Merged")
                        if x.block == block and x.data.get("sha") == sha and x.data.get("commit")
                    ),
                    None,
                )
                if confirmado is not None:
                    self._efecto(plan, block, ident, "aplicado", confirmado.data["commit"])
            if ident and ident != excepto and ident not in ids:
                ids.append(ident)
        for ident in ids:
            if self._ultimo_efecto(plan, ident) in (None, "incierto"):
                return ident
        return ""

    def _frena_escritor(self, plan, snap, a, kind, starteds, terminales) -> bool:
        """True: no lanzar. La intencion queda escrita antes del proceso nuevo."""
        tipo = "instalar" if kind == "deploy" else "probar"
        campo = self._APARTE[kind][2]
        h_insumo, h_maquina = self._huellas(campo, plan.block(a.block), snap)
        entradas = f"{a.sha}:{h_insumo}:{h_maquina}"
        ident = self._id_intencion(a.block, tipo, entradas)
        abierta = self._otra_abierta(plan, a.block, tipo, ident)
        if abierta:
            self._avisa_incierto(plan, snap, a.block, abierta)
            return True
        ultimo = self._ultimo_efecto(plan, ident)
        if ultimo == "aplicado":
            return True
        if ultimo == "incierto":
            self._avisa_incierto(plan, snap, a.block, ident)
            return True
        if starteds:
            ultimo_s = starteds[-1]
            dprev = Path(ultimo_s.data["dir"]) if ultimo_s.data.get("dir") else None
            tiene = bool(dprev and (dprev / "outcome.json").exists())
            proc = ultimo_s.data.get("proc") or {}
            vivo = bool(proc) and plataforma.estado_proc(proc) == "vivo"
            pendiente = not terminales or ultimo_s.id > terminales[-1].id
            if pendiente and not tiene and not vivo:
                anterior = ultimo_s.data.get("intencion") or self._confirma_intencion(
                    plan, a.block, tipo, a.sha, f"{tipo}:{a.sha}"
                )
                self._efecto(plan, a.block, anterior, "incierto", "proceso muerto sin resultado")
                self._avisa_incierto(plan, snap, a.block, anterior)
                return True
        pedidos = self.cx.store.events(plan.id, "Intencion")
        if ultimo is None and any(e.data.get("id") == ident for e in pedidos):
            self._efecto(plan, a.block, ident, "incierto", "intencion sin recibo de arranque")
            self._avisa_incierto(plan, snap, a.block, ident)
            return True
        self._confirma_intencion(plan, a.block, tipo, entradas, f"{tipo}:{entradas}")
        if ultimo == "no_aplicado":
            self._efecto(plan, a.block, ident, "incierto", "reintento reservado tras no_aplicado")
        return False

    def merge(self, plan: Plan, snap: m.Snapshot, a: m.Merge) -> None:
        t = a.ticket
        if not isinstance(t, m.MergeTicket):
            raise TypeError("merge sin MergeTicket")
        block = plan.block(t.block)
        rc = self.repo_conf(snap, block.repo)
        if self._ya_mergeado(plan, snap, t.block, t.sha):
            return
        if block.lane == "fast":
            self.cx.gh.marcar_listo(block.repo, t.pr)
            self.cx.store.append(
                plan.id,
                t.block,
                "MarkedReady",
                {"sha": t.sha},
                ukey=claves.marcado_listo(plan.id, t.block, t.sha),
            )
        ident = self._id_intencion(t.block, "merge", f"{t.sha}:{t.pr}")
        abierta = self._otra_abierta(plan, t.block, "merge", ident)
        if abierta:
            self._avisa_incierto(plan, snap, t.block, abierta)
            return
        ultimo = self._ultimo_efecto(plan, ident)
        if ultimo == "aplicado":
            return
        if ultimo == "incierto":
            self._avisa_incierto(plan, snap, t.block, ident)
            return
        self._confirma_intencion(plan, t.block, "merge", f"{t.sha}:{t.pr}", f"merge:{t.sha}:{t.pr}")
        if ultimo == "no_aplicado":
            self._efecto(plan, t.block, ident, "incierto", "reintento reservado tras no_aplicado")
        try:
            pr = self.cx.gh.merge(
                block.repo,
                t.pr,
                t.sha,
                rc.merge,
                f"{plan.id} {t.block}: {block.titulo}",
            )
        except MergeRechazado as e0:
            self._efecto(plan, t.block, ident, "no_aplicado", str(e0)[:300])
            e = clasificar_merge(e0)
            if isinstance(e, PuntaMovida):
                if self.cx.gh.ver(block.repo, t.pr).head == t.sha:
                    # GitHub todavía asienta el PR recién abierto (MOTOR 01, PR #9): misma punta, se reintenta
                    raise RuntimeError(
                        f"GitHub pidió reintentar el merge: {str(e)[:200]}"
                    ) from None
                self._checks_rojo_por_merge(
                    plan,
                    t,
                    "alguien subió un commit a la rama del PR después de la revisión; "
                    "revísalo y vuelve a entregar",
                )
                return
            if isinstance(e, Borrador):
                self.cx.gh.marcar_listo(block.repo, t.pr)
                self.cx.store.append(
                    plan.id,
                    t.block,
                    "MarkedReady",
                    {"sha": t.sha},
                    ukey=claves.marcado_listo(plan.id, t.block, t.sha),
                )
                return
            if isinstance(e, PoliticaBloquea):
                n = sum(
                    1
                    for x in self.cx.store.events(plan.id)
                    if x.kind == "ChecksInvalidados"
                    and x.block == t.block
                    and x.data.get("sha") == t.sha
                )
                self.cx.store.append(
                    plan.id,
                    t.block,
                    "ChecksInvalidados",
                    {"sha": t.sha, "detalle": str(e)[:500]},
                    ukey=claves.checks_invalidados(plan.id, t.block, t.sha, n),
                )
                return
            if isinstance(e, Conflicto):
                self._checks_rojo_por_merge(
                    plan,
                    t,
                    f"el merge falló por conflicto con {rc.principal}: trae {rc.principal} a tu rama, "
                    f"resuelve y vuelve a entregar (no mergees tú). GitHub dijo: {str(e)[:300]}",
                )
                return
            raise RuntimeError(f"GitHub aún no deja mergear: {str(e)[:300]}") from None
        except Exception as ex:
            self._efecto(plan, t.block, ident, "incierto", str(ex)[:300])
            raise
        self._efecto(plan, t.block, ident, "aplicado", str(getattr(pr, "merge_commit", "") or ""))
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
            block.repo,
            commit,
            self.cx.home / efimeras.TMP / f"despliega-{plan.id}-{a.block}",
        )
        env = self.env_base(dict(rc.prueba_env))
        # La instalación no tiene .git; la copia (worktree) sí. Sin esto,
        # instala-todo.sh hace `git -C "$MOTOR_RAIZ"` y sale 3.
        env["MOTOR_RAIZ"] = str(w)
        env["MOTOR_SHA"] = commit
        tope = float(self.cx.conf.tiempos.get("despliegue_tope_s", 900))
        codigo = 0
        produccion_tocada = False
        rollback_ok = True
        if not (desplegar and checklist and rollback):
            ok, detalle = (
                False,
                "el bloque es de despliegue y el repo no trae receta completa",
            )
        else:
            with _lock_despliegue(self.cx.home, block.repo):
                if self._dir_aparte is not None:
                    marca = self._dir_aparte / "desplegando"
                    marca.write_text(a.sha, encoding="utf-8")
                pasos, ok = [], True
                for paso in (desplegar, checklist):
                    p = _run(f"bash {paso}", w, env, tope, bash=self.cx.maquina.bash)
                    pasos.append(f"{paso}: salida {p.returncode}")
                    if p.returncode == 75:
                        ok, codigo = False, 75
                        pasos.append("no es el código; reintenta luego")
                        pasos.append(_cola(p))
                        break
                    if p.returncode != 0:
                        # Sin la salida el agente solo veía "salida 1" y no sabía qué arreglar
                        # (INGESTA 01: desplegar.sh pedía un SHA como argumento que el motor no da).
                        ok, codigo = False, p.returncode
                        pasos.append(_cola(p))
                        from .previa import faltantes_de_salida

                        faltan += faltantes_de_salida(
                            p.stdout + p.stderr, p.returncode
                        )
                        produccion_tocada = True
                        rollback_ok = self._rollback(rollback, w, env, tope, pasos)
                        pasos.append(RECETA)
                        break
                    # Lo que imprime un paso que sale 0 también cuenta: avisos como «la etiqueta ya existe».
                    if cola := _cola(p):
                        pasos.append(cola)
                    if paso == desplegar:
                        produccion_tocada = True
                        problemas, notas, malas = self._efimeras(w)
                        pasos += notas
                        if problemas:
                            ok = False
                            pasos += problemas
                            rollback_ok = self._rollback(rollback, w, env, tope, pasos)
                            if malas and self._efimeras(w)[2]:
                                pasos.append("La marcha atrás no la quitó.")
                            break
                detalle = "\n".join(pasos)
        return {
            "sha": a.sha,
            "ok": ok,
            "detalle": detalle,
            "codigo": codigo,
            "falta_comando": sorted(set(faltan)),
            "huella": h_insumo,
            "huella_maquina": h_maquina,
            "evidencia": 1,
            "produccion_tocada": produccion_tocada,
            "rollback_ok": rollback_ok,
        }

    def _rollback(self, rollback: str, w: Path, env: dict, tope: float, pasos: list) -> bool:
        rb = _run(f"bash {rollback}", w, env, tope, bash=self.cx.maquina.bash)
        pasos.append(f"rollback: salida {rb.returncode}")
        if cola := _cola(rb):
            pasos.append(cola)
        return rb.returncode == 0

    def _efimeras(self, w: Path) -> tuple[list[str], list[str], set[str]]:
        """Tras desplegar: ¿alguna tarea programada apunta a la copia `w`, que el motor borra al
        terminar? Devuelve (problemas que tumban el despliegue, notas para el detalle, tareas malas)."""
        conf_dir = getattr(self.cx.conf, "dir", None)
        try:
            tareas, _, sin_leer, negados = efimeras.lee_tareas(
                self.cx.maquina.nombre,
                conf_dir,
                self.cx.programadas_sistema,
                bash=self.cx.maquina.bash,
            )
        except Exception as e:  # noqa: BLE001 - un error aquí no puede dejar el despliegue sin marcha atrás
            return (
                [
                    f"No pude revisar las tareas programadas ({type(e).__name__}: {e}); "
                    f"sin eso no puedo asegurar que la receta no dejó tareas apuntando a {w}."
                ],
                [],
                set(),
            )
        home = Path.home()
        sistema = (
            set(efimeras.nombres_del_sistema(home))
            if self.cx.programadas_sistema
            else set()
        )
        problemas, notas = [], []
        malas = set(efimeras.tareas_con(tareas, w, home))
        for t in sorted(malas):
            problemas.append(
                f"La receta dejó {t} apuntando a {w}, una carpeta que el motor borra al terminar. "
                "Instala lo que corre la tarea en una carpeta fija (por ejemplo ~/.local/share/<nombre>) "
                "y apunta ahí."
            )
        for fuente, motivo in sin_leer.items():
            if fuente in sistema:
                problemas.append(
                    f"No pude leer {fuente} ({motivo}); sin eso no puedo asegurar que la receta no dejó "
                    f"tareas apuntando a {w}."
                )
            else:
                notas.append(f"fuente extra sin leer: {fuente} ({motivo})")
        for archivo, motivo in negados.items():
            notas.append(f"archivo que no pude abrir: {archivo} ({motivo})")
        return problemas, notas, malas

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
            p = _run(block.prueba_real, w, env, tope, bash=self.cx.maquina.bash)
            # La salida puede ir a un archivo (la sonda de INGESTA 01): sin esto el agente veía "".
            ok, detalle = (
                p.returncode == 0,
                _cola(p) or f"salió {p.returncode} sin imprimir nada",
            )
            codigo = p.returncode
            if codigo == 75:
                detalle = f"no es el código; reintenta luego: {detalle}"
            if not ok:
                from .previa import faltantes_de_salida

                faltan = faltantes_de_salida(p.stdout + p.stderr, p.returncode)
        else:
            ok, detalle, codigo = True, "el bloque no declara prueba real", 0
        return {
            "sha": a.sha,
            "ok": ok,
            "detalle": detalle,
            "codigo": codigo,
            "falta_comando": sorted(set(faltan)),
            "huella": h_insumo,
            "huella_maquina": h_maquina,
            "evidencia": 1,
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
        argv, shell, exe = _posix(h.sonda, self.cx.maquina.bash)
        p = subprocess.Popen(
            argv,
            shell=shell,
            env=con_python_de_comando(self.env_base({"MOTOR_HOME": str(self.cx.home)})),
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
        if not m.puede_cerrar(snap):
            return
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
            w = self.cx.home / efimeras.WT / plan.id / bid
            if w.exists():
                git("worktree", "remove", "--force", str(w), cwd=d)
            git("branch", "-q", "-D", self.cx.ws.rama(plan.id, bid), cwd=d)
        shutil.rmtree(self.cx.home / efimeras.WT / plan.id, ignore_errors=True)
        for x in (self.cx.home / efimeras.TMP).glob(f"*-{plan.id}-*"):
            shutil.rmtree(x, ignore_errors=True)
        for x in (self.cx.home / efimeras.INTENTOS / plan.id).glob("*/revision"):
            shutil.rmtree(x, ignore_errors=True)
        for d in espejos:
            git("worktree", "prune", cwd=d)
        self.cx.store.append(
            plan.id, "", "Limpiado", {"ramas": borradas}, ukey=claves.limpiado(plan.id)
        )

    def purga_registros(self, _planes: list[Plan], now: float) -> None:
        """Borra los registros de intentos de los planes limpiados hace más de `registros_dias`,
        y las copias de pstack que ningún intento usa hace ese mismo tiempo (menos la vigente)."""
        dias = float(self.cx.conf.tiempos.get("registros_dias", 14))
        vigente = self._pstack[1][0] if self._pstack and self._pstack[1] else ""
        raiz = self.cx.home / efimeras.PSTACK
        for x in raiz.iterdir() if raiz.is_dir() else ():
            try:
                vieja = now - x.stat().st_mtime > dias * 86400
            except OSError:
                continue
            if vieja and x.name != vigente:
                shutil.rmtree(x, ignore_errors=True)
        store = getattr(self.cx, "store", None)
        ultima_carga: dict[str, int] = {}
        limpios: dict[str, float] = {}
        if store is not None:
            for e in store.events(kind="PlanLoaded"):
                ultima_carga[e.plan] = e.id
            for e in store.events(kind="Limpiado"):
                if ultima_carga.get(e.plan, 0) <= e.id:
                    limpios[e.plan] = e.ts
        for pid, ts in limpios.items():
            carpeta = self.cx.home / efimeras.INTENTOS / pid
            if now - ts > dias * 86400 and carpeta.exists():
                shutil.rmtree(carpeta, ignore_errors=True)

    def notify(self, plan: Plan, snap: m.Snapshot, a: m.Notify) -> None:
        if a.template == "humano" and a.block and snap.blocks[a.block].human_done:
            return  # la sonda ya lo vio hecho en este mismo paso: no se molesta a David
        texto = render(
            a.template, dict(a.campos), self.cx.maquina.nombre, plan.titulo or plan.id
        )
        notice_id = f"plan:{plan.id}:{a.key}"
        if not self.cx.store.append(
            plan.id,
            a.block,
            "NoticeRequested",
            {"notice_id": notice_id, "key": a.key, "texto": texto},
            ukey=claves.aviso_encolado(plan.id, a.key),
        ):
            return  # un aviso histórico con la misma clave no se reenvía
        self.cx.notifier.materializa(notice_id, texto, f"{plan.id}:{a.key}")


def comando_runwrap(
    d: Path, cwd: Path, opciones: list[str], argv: list[str]
) -> tuple[list[str], Path]:
    """(comando, carpeta de lanzamiento) del vigilante de un agente. Sale siempre del motor instalado:
    se lanza desde la carpeta del intento (no desde la copia del repo, que puede traer su propio
    `motor/`) y con `-P`, para que ni esa carpeta ni nada local se cuele en `sys.path`. El agente
    trabaja en `cwd`, que runwrap recibe en `--cwd`."""
    cmd = [
        sys.executable,
        "-P",
        "-m",
        "motor.runwrap",
        "--dir",
        str(d),
        "--cwd",
        str(cwd),
        *opciones,
        "--",
        *argv,
    ]
    return cmd, d


def _escribe_marca(ruta: Path, datos: dict) -> None:
    tmp = ruta.with_name(ruta.name + ".tmp")
    tmp.write_text(json.dumps(datos), encoding="utf-8")
    os.replace(tmp, ruta)


def _lee_json(ruta: Path) -> dict | None:
    try:
        data = json.loads(ruta.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _falla_creacion(e: BaseException) -> dict:
    return {
        "tipo": type(e).__name__,
        "winerror": getattr(e, "winerror", None),
        "errno": getattr(e, "errno", None),
        "mensaje": _sanea_causa(str(e)),
    }


def _sanea_causa(texto: str) -> str:
    linea = (texto or "").splitlines()[0][:240]
    return re.sub(r"/bot[^/\s]+", "/bot<oculto>", linea)


def _juicio_creacion(o: dict) -> dict:
    """El wrapper arrancó y el agente no. No es una falla del modelo ni un relevo."""
    err = o.get("error") if isinstance(o.get("error"), dict) else {}
    tipo = str(err.get("tipo") or "OSError")[:80]
    return {
        "kind": "creacion_fallida",
        "detalle": f"no pude crear el agente: {tipo}",
        "error": {
            "tipo": tipo,
            "winerror": err.get("winerror"),
            "errno": err.get("errno"),
            "mensaje": _sanea_causa(str(err.get("mensaje") or "")),
        },
    }


def _aviso_previo(eventos, key: str) -> bool:
    return any(
        e.kind in ("NoticeQueued", "NoticeRequested") and e.data.get("key") == key
        for e in eventos
    )


def evento_de_arranque(datos: dict) -> dict | None:
    """`AttemptStarted` solo con un proceso observado. El resto no gasta un intento."""
    estado = datos.get("estado")
    proc = datos.get("proc") if isinstance(datos.get("proc"), dict) else {}
    if estado in ("reservado", "creacion_fallida", "arranque_por_conciliar"):
        return None
    if not (proc.get("pid") and proc.get("inicio")):
        return None
    return datos


def _estado_marca(d: Path, datos: dict) -> tuple[str, dict]:
    """Concilia la marca con el wrapper y el resultado. No inventa la causa ni relanza."""
    proc = datos.get("proc") if isinstance(datos.get("proc"), dict) else {}
    estado = datos.get("estado")
    if estado == "creacion_fallida":
        return "creacion_fallida", datos
    if proc.get("pid") and proc.get("inicio"):
        return "proceso_observado", {**datos, "proc": proc, "estado": "proceso_observado"}
    token = datos.get("token")
    wrapper = _lee_json(d / "wrapper.json")
    if (
        wrapper
        and wrapper.get("pid")
        and wrapper.get("inicio")
        and ((wrapper.get("token") == token) if token else wrapper.get("token") in ("", None))
    ):
        proc = {
            "pid": wrapper["pid"],
            "inicio": wrapper["inicio"],
            "boot_id": wrapper.get("boot_id") or "",
            "job": wrapper.get("job") or "",
        }
        return "proceso_observado", {**datos, "proc": proc, "estado": "proceso_observado"}
    if estado is None and not proc:
        return "arranque_por_conciliar", {**datos, "estado": "arranque_por_conciliar", "proc": {}}
    return "arranque_por_conciliar", {
        **datos,
        "estado": "arranque_por_conciliar",
        "proc": proc,
    }


def _iso_a_epoch(iso: str | None) -> float:
    if not iso:
        return 0.0
    import datetime as dt

    try:
        return dt.datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return 0.0
