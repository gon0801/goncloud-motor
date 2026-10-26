"""Identidad de procesos del intento. El avance no se mide por el tamaño del log."""

from __future__ import annotations

import json
import os
import shutil
import socket
import sys
import time
from pathlib import Path

from motor import plataforma


class Marca:
    def __init__(self, directorio: Path, proc: dict | None):
        self.dir = str(directorio)
        self.proc = proc or {}


def observar(directorio: Path, proc: dict | None) -> str:
    comprobador = comprobador_actual(directorio)
    if comprobador:
        estado = observar(comprobador[0], comprobador[1])
        if estado != "ausente":
            return estado
    if not proc:
        return "ausente"
    if proc.get("host") and proc["host"] != socket.gethostname():
        return "incierto"
    from motor.effects import observar_arbol

    estado = observar_arbol(Marca(directorio, proc))
    if estado != "ausente":
        return estado
    try:
        identidades = json.loads(
            (directorio / "arbol.json").read_text(encoding="utf-8")
        )
        if any(plataforma.estado_proc(x) == "vivo" for x in identidades):
            return "vivo"
    except (OSError, ValueError, TypeError):
        pass
    return estado


def herramienta_en_curso(directorio: Path) -> bool | None:
    """None: nadie escribió qué herramienta corre. False: el archivo ya la vio terminada."""
    comprobador = comprobador_actual(directorio)
    if comprobador:
        estado = observar(comprobador[0], comprobador[1])
        if estado == "vivo":
            return True
        if estado == "incierto":
            return None
    path = Path(directorio) / "herramienta.json"
    if not path.exists():
        return None
    try:
        datos = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return True
    if datos.get("observada"):
        if not datos.get("activas"):
            return False
        if os.name == "nt":
            return True
        try:
            agente = int((directorio / "agent.pid").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        actuales = descendientes(agente)
        return None if actuales is None else bool(actuales)
    pid = datos.get("pid")
    inicio = datos.get("inicio")
    if not pid or not inicio:
        return True
    try:
        estado = plataforma.estado_proc(
            {"pid": int(pid), "inicio": str(inicio), "boot_id": plataforma.boot_id()}
        )
    except (OSError, TypeError, ValueError):
        return True
    return estado == "vivo"


def _base_de(cwd: Path) -> Path | None:
    """El trabajador del ciclo vive en base/espacio/<encargo>. Otro cwd no se enjaula aquí."""
    cwd = Path(cwd).resolve()
    if cwd.parent.name == "espacio":
        return cwd.parent.parent
    return None


def _entorno(env: dict, espacio: Path) -> dict:
    """El trabajador no hereda secretos del supervisor. TMPDIR queda en su espacio."""
    permitidas = {
        "PATH",
        "SYSTEMROOT",
        "WINDIR",
        "COMSPEC",
        "PATHEXT",
        "LANG",
        "TERM",
        "TZ",
    }
    limpio = {
        k: v
        for k, v in env.items()
        if k.upper() in permitidas or k.upper().startswith("LC_")
    }
    limpio["TMPDIR"] = str(espacio)
    limpio["PYTHONDONTWRITEBYTECODE"] = "1"
    return limpio


def _persistir_proc(directorio: Path, proc: dict) -> None:
    directorio.mkdir(parents=True, exist_ok=True)
    tmp = directorio / "proc.json.tmp"
    tmp.write_text(json.dumps(proc), encoding="utf-8")
    os.replace(tmp, directorio / "proc.json")


def _enjaular(
    cmd: list[str],
    cwd: Path,
    log: Path,
    runtime: Path | None = None,
    lectura: bool = False,
) -> tuple[list[str], str]:
    sandbox = "/usr/bin/sandbox-exec"
    if not os.path.exists(sandbox) or _base_de(cwd) is None:
        return cmd, "insuficiente"
    espacio = Path(cwd).resolve()
    # El modelo puede leer las herramientas instaladas, pero no el HOME de producción.
    perfil = [
        "(version 1)",
        "(allow default)",
        "(deny file-write*)",
        f"(deny file-read* (subpath {json.dumps(str(Path.home()))}))",
        "(allow file-read-metadata)",
    ]
    for path in (
        espacio,
        Path(log).resolve(),
        *([runtime.resolve()] if runtime else []),
    ):
        perfil.append(f"(allow file-read* (subpath {json.dumps(str(path))}))")
    if not lectura:
        perfil.append(f"(allow file-write* (subpath {json.dumps(str(espacio))}))")
    if runtime:
        perfil.append(
            f"(allow file-write* (subpath {json.dumps(str(runtime.resolve()))}))"
        )
    for path in ("/dev/null", "/dev/tty"):
        perfil.append(f'(allow file-write* (literal "{path}"))')
    path = Path(log).parent / "jaula.sb"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(perfil), encoding="utf-8")
    return [sandbox, "-f", str(path), *cmd], "jaula"


def entorno_suscripcion(espacio: Path, runtime: Path, cmd: list[str]) -> dict:
    runtime.mkdir(parents=True, exist_ok=True)
    limpio = _entorno(os.environ.copy(), espacio)
    limpio["HOME"] = str(runtime)
    limpio["XDG_CONFIG_HOME"] = str(runtime / ".config")
    temporal = runtime / "tmp"
    temporal.mkdir(exist_ok=True)
    limpio["TMPDIR"] = str(temporal)
    limpio.pop("SSH_AUTH_SOCK", None)
    limpio.pop("PYTHONPATH", None)
    limpio.pop("MOTOR_HOME", None)
    if any(Path(item).name == "codex" for item in cmd[:1]):
        auth = (
            Path(os.environ.get("CODEX_HOME", str(Path.home() / ".codex")))
            / "auth.json"
        )
        destino = runtime / ".codex"
        destino.mkdir(exist_ok=True)
        if auth.is_file():
            shutil.copyfile(auth, destino / "auth.json")
            (destino / "auth.json").chmod(0o600)
        limpio["CODEX_HOME"] = str(destino)
    if any(Path(item).name == "grok" for item in cmd[:1]):
        auth = Path.home() / ".grok" / "auth.json"
        destino = runtime / ".grok"
        destino.mkdir(exist_ok=True)
        if auth.is_file():
            shutil.copyfile(auth, destino / "auth.json")
            (destino / "auth.json").chmod(0o600)
    return limpio


def lanzar(cmd: list[str], cwd: Path, log: Path, env: dict | None = None) -> dict:
    log = Path(log)
    log.parent.mkdir(parents=True, exist_ok=True)
    envuelto, aislamiento = _enjaular(list(cmd), Path(cwd), log)
    if aislamiento != "jaula":
        raise RuntimeError(
            "este host no aisla trabajadores; solo transporte y control nativos disponibles"
        )
    proc = plataforma.lanzar_separado(
        envuelto, cwd, _entorno(env or os.environ.copy(), Path(cwd)), log
    )
    proc["aislamiento"] = aislamiento
    _persistir_proc(log.parent, proc)
    return proc


def preparar_administrado(
    cmd: list[str],
    cwd: Path,
    directorio: Path,
    enc,
    sesion: str,
    hora: str,
    runtime: Path,
) -> None:
    from motor.runwrap import _publica

    env = entorno_suscripcion(cwd, runtime, cmd)
    envuelto, aislamiento = _enjaular(
        cmd, cwd, directorio / "log.txt", runtime, lectura=enc.rol == "revisor"
    )
    if aislamiento != "jaula":
        raise RuntimeError(
            "sin aislamiento efectivo; trabajador no habilitado en este host"
        )
    spec = {
        "cmd": envuelto,
        "cwd": str(cwd),
        "env": env,
        "base": str(_base_de(cwd)),
        "encargo": enc.id,
        "token": enc.conductor,
        "host": socket.gethostname(),
        "sesion": sesion,
        "rol": enc.rol,
        "hora": hora,
    }
    spec_path = directorio / "arranque.json"
    _publica(spec_path, json.dumps(spec))
    plataforma.sincroniza_directorio(directorio)


def lanzar_administrado(directorio: Path, token: str) -> dict:
    spec_path = directorio / "arranque.json"
    argv = [
        sys.executable,
        "-P",
        str(Path(__file__).with_name("arranque.py")),
        str(spec_path),
    ]
    proc = plataforma.lanzar_separado(
        argv, directorio, os.environ.copy(), directorio / "wrapper.log"
    )
    proc.update({"aislamiento": "jaula", "host": socket.gethostname(), "token": token})
    _persistir_proc(directorio, proc)
    return proc


def _cerrar_identidad(ident: dict) -> None:
    if ident.get("host") and ident["host"] != socket.gethostname():
        return
    if not ident.get("pid") or not ident.get("inicio"):
        return
    pid = int(ident["pid"])
    inicio = str(ident.get("inicio") or "")
    job = str(ident.get("job") or "")
    plataforma.matar_proc(pid, inicio)
    plataforma.cerrar_huerfanos(pid, inicio, job)


def cerrar(directorio: Path, proc: dict | None) -> str:
    """Cierra al padre y al grupo aunque el padre ya no esté en la lista de vivos."""
    from motor.effects import _senales, observar_arbol

    if proc and proc.get("host") and proc["host"] != socket.gethostname():
        return "incierto"
    comprobador = comprobador_actual(directorio)
    if comprobador:
        cerrar(comprobador[0], comprobador[1])
    if (
        proc
        and (not proc.get("host") or proc["host"] == socket.gethostname())
        and os.name != "nt"
    ):
        import signal

        # Congela primero al lanzador y luego sus hijos para que el cierre no genere otros.
        identidades = [proc]
        for _ in range(4):
            for ident in identidades:
                if plataforma.estado_proc(ident) == "vivo":
                    try:
                        os.kill(int(ident["pid"]), signal.SIGSTOP)
                    except ProcessLookupError:
                        pass
            nuevos = guardar_descendientes(directorio, int(proc["pid"]))
            if nuevos is None:
                break
            agregados = [x for x in nuevos if x not in identidades]
            identidades.extend(agregados)
            if not agregados:
                break
        for ident in reversed(identidades):
            if plataforma.estado_proc(ident) == "vivo":
                try:
                    os.kill(int(ident["pid"]), signal.SIGKILL)
                except ProcessLookupError:
                    pass
    if proc:
        _cerrar_identidad(proc)
    att = Marca(directorio, proc)
    for ident in _senales(att):
        if proc and int(ident.get("pid") or 0) == int(proc.get("pid") or 0):
            continue
        _cerrar_identidad(ident)
    fin = time.time() + 2
    visto = observar_arbol(att)
    while visto == "vivo" and time.time() < fin:
        time.sleep(0.05)
        if proc:
            plataforma.cerrar_huerfanos(
                int(proc["pid"]),
                str(proc.get("inicio") or ""),
                str(proc.get("job") or ""),
            )
        visto = observar_arbol(att)
    return observar(directorio, proc)


def crecer_log(directorio: Path, texto: str) -> None:
    """Utilidad del ensayo: agrandar el log no es un hecho."""
    path = Path(directorio) / "log.txt"
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(texto)


def recuperar_arranque(directorio: Path) -> tuple[str, dict | None]:
    """Con el turno tomado no queda un lanzador concurrente entre la consulta y spawn."""
    from .frontera import turno

    spec_path = directorio / "arranque.json"
    try:
        spec = json.loads(spec_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return "incierto", None
    with turno(Path(spec["base"])):
        for nombre in ("proc.json", "wrapper.json"):
            try:
                proc = json.loads((directorio / nombre).read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if proc.get("token") == spec["token"]:
                return observar(directorio, proc), proc
        import subprocess

        if os.name == "nt":
            script = (
                "Get-CimInstance Win32_Process | Select-Object ProcessId,CommandLine | "
                "ConvertTo-Json -Compress"
            )
            try:
                p = subprocess.run(
                    ["powershell", "-NoProfile", "-Command", script],
                    capture_output=True,
                    text=True,
                    timeout=15,
                    check=False,
                )
                if p.returncode != 0:
                    return "incierto", None
                rows = json.loads(p.stdout or "[]")
                if isinstance(rows, dict):
                    rows = [rows]
                candidatos = [
                    (int(x["ProcessId"]), str(x.get("CommandLine") or "")) for x in rows
                ]
            except (OSError, ValueError, KeyError, subprocess.TimeoutExpired):
                return "incierto", None
        else:
            try:
                p = subprocess.run(
                    ["/bin/ps", "-axww", "-o", "pid=,command="],
                    capture_output=True,
                    text=True,
                    timeout=10,
                    check=False,
                )
                if p.returncode != 0:
                    return "incierto", None
                candidatos = []
                for linea in p.stdout.splitlines():
                    partes = linea.strip().split(None, 1)
                    if len(partes) == 2 and partes[0].isdigit():
                        candidatos.append((int(partes[0]), partes[1]))
            except (OSError, subprocess.TimeoutExpired):
                return "incierto", None
        for pid, comando in candidatos:
            if (
                str(spec_path) in comando
                and str(
                    Path(__file__).with_name(spec.get("bootstrap") or "arranque.py")
                )
                in comando
            ):
                inicio = plataforma.hora_inicio(pid)
                if inicio:
                    proc = {
                        "pid": pid,
                        "inicio": inicio,
                        "boot_id": plataforma.boot_id(),
                        "host": socket.gethostname(),
                        "token": spec["token"],
                    }
                    _persistir_proc(directorio, proc)
                    return "vivo", proc
                return "incierto", None
        return "ausente", None


def descendientes(raiz: int) -> list[dict] | None:
    """Consulta PID/PPID, sin leer argumentos ni entornos de procesos ajenos."""
    import subprocess

    if os.name == "nt":
        return None
    try:
        p = subprocess.run(
            ["/bin/ps", "-ax", "-o", "pid=,ppid="],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        if p.returncode != 0:
            return None
        padres = {
            int(x[0]): int(x[1])
            for linea in p.stdout.splitlines()
            if len(x := linea.split()) == 2
        }
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return None
    vistos = {raiz}
    while True:
        nuevos = {pid for pid, padre in padres.items() if padre in vistos} - vistos
        if not nuevos:
            break
        vistos.update(nuevos)
    identidades = []
    for pid in sorted(vistos - {raiz}):
        inicio = plataforma.hora_inicio(pid)
        if inicio:
            identidades.append(
                {"pid": pid, "inicio": inicio, "boot_id": plataforma.boot_id()}
            )
    return identidades


def guardar_descendientes(directorio: Path, raiz: int) -> list[dict] | None:
    from motor.runwrap import _publica

    actuales = descendientes(raiz)
    if actuales is None:
        return None
    path = directorio / "arbol.json"
    try:
        previos = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        previos = []
    for ident in previos:
        if ident not in actuales and plataforma.estado_proc(ident) == "vivo":
            actuales.append(ident)
    _publica(path, json.dumps(actuales))
    return actuales


def comprobador_actual(directorio: Path) -> tuple[Path, dict | None] | None:
    path = directorio / "comprobador-actual"
    if not path.is_file():
        return None
    nombre = path.read_text(encoding="utf-8").strip()
    if not nombre.startswith("comprobador-") or "/" in nombre or "\\" in nombre:
        return None
    cd = directorio / nombre
    estado, proc = recuperar_arranque(cd)
    if proc is None and estado == "incierto":
        return cd, {"pid": 0, "inicio": "", "boot_id": ""}
    return cd, proc
