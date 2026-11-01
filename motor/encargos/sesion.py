"""Identidad de procesos del intento. El avance no se mide por el tamaño del log."""

from __future__ import annotations

import json
import os
import socket
import sys
import time
from pathlib import Path

from motor import plataforma
from motor.encargos.dominio import fuentes_autorizadas


class Marca:
    def __init__(self, directorio: Path, proc: dict | None):
        self.dir = str(directorio)
        self.proc = proc or {}


def _en_distro(directorio: Path) -> bool:
    """El intento corre dentro de WSL: Windows solo ve a `wsl.exe`, el resto vive en la distro."""
    try:
        spec = json.loads((Path(directorio) / "arranque.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    from motor.plataforma.jaula_wsl import MECANISMO

    return spec.get("aislamiento") == MECANISMO


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
    if _en_distro(directorio):
        from motor.plataforma import jaula_wsl

        en_distro = jaula_wsl.estado_en_distro(directorio, jaula_wsl.distro())
        if en_distro != "ausente":
            return en_distro
    try:
        identidades = json.loads(
            (directorio / "arbol.json").read_text(encoding="utf-8")
        )
        raiz_inicio = str(proc.get("inicio") or "")
        for x in identidades:
            inicio = str(x.get("inicio") or "")
            if (
                inicio.isdigit()
                and raiz_inicio.isdigit()
                and int(inicio) < int(raiz_inicio)
            ):
                continue
            if plataforma.estado_proc(x) == "vivo":
                return "vivo"
    except (OSError, ValueError, TypeError):
        pass
    if os.name == "nt":
        from motor.plataforma.jaula_windows import limpiar

        limpiar(directorio)
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
        if datos.get("modelo_en_curso") is True:
            return True
        if not datos.get("activas"):
            if "modelo_en_curso" in datos and datos["modelo_en_curso"] is None:
                return None
            return False
        if _en_distro(directorio):
            # Windows solo ve a wsl.exe: los hijos de la herramienta no se enumeran desde
            # aqui. Una herramienta abierta en el flujo del agente cuenta como en curso.
            return True
        try:
            agente = int((directorio / "agent.pid").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        actuales = descendientes(agente)
        if actuales is None:
            return None
        if os.name == "nt":
            from motor.plataforma.jaula_windows import imagen_proceso

            consola = (
                Path(os.environ["SystemRoot"]) / "System32" / "conhost.exe"
            ).resolve()
            for ident in actuales:
                imagen = imagen_proceso(ident["pid"], ident["inicio"])
                if imagen is None:
                    return None
                if Path(imagen).resolve() != consola:
                    return True
            return False
        return bool(actuales)
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


def _persistir_proc(directorio: Path, proc: dict) -> None:
    directorio.mkdir(parents=True, exist_ok=True)
    tmp = directorio / "proc.json.tmp"
    tmp.write_text(json.dumps(proc), encoding="utf-8")
    os.replace(tmp, directorio / "proc.json")


def preparar_administrado(
    cmd: list[str],
    cwd: Path,
    directorio: Path,
    enc,
    sesion: str,
    hora: str,
    runtime: Path,
    entorno_extra: dict[str, str] | None = None,
    *,
    agente: str | None = None,
    modelo: str = "",
    sesion_modo: str = "",
    poteto_digest: str = "",
) -> None:
    from motor.runwrap import _publica

    from .aislamiento import aislar
    from .catalogo_agentes import cliente_para

    generacion = (entorno_extra or {}).get("MOTOR_GENERACION_RUTA")
    concesiones = [Path(generacion)] if generacion else []
    concesiones.extend(fuentes_autorizadas(enc))
    comando = aislar(
        cmd, cwd, runtime, lectura=enc.rol == "revisor", suscripcion=True,
        lecturas_extra=tuple(concesiones),
        agente=agente,
        interprete=True,
    )
    if entorno_extra:
        comando.entorno.update(entorno_extra)
    spec = {
        "cmd": comando.argv,
        "cwd": str(cwd),
        "env": comando.entorno,
        "aislamiento": comando.mecanismo,
        "jaula": comando.jaula,
        "ejecutable": comando.ejecutable,
        "base": str(_base_de(cwd)),
        "encargo": enc.id,
        "token": enc.conductor,
        "host": socket.gethostname(),
        "sesion": sesion,
        "rol": enc.rol,
        "agente": agente or Path(cmd[0]).stem.lower(),
        "cliente": cliente_para(agente or Path(cmd[0]).stem.lower()),
        "modelo": modelo,
        "sesion_modo": sesion_modo,
        "poteto_digest": poteto_digest,
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
    spec = json.loads(spec_path.read_text(encoding="utf-8"))
    proc.update(
        {
            "aislamiento": spec["aislamiento"],
            "host": socket.gethostname(),
            "token": token,
        }
    )
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
    if _en_distro(directorio):
        from motor.plataforma import jaula_wsl

        jaula_wsl.cerrar_en_distro(directorio, jaula_wsl.distro())
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
        from motor.plataforma.windows import _consulta_pid, _descendientes

        pids = _descendientes(raiz)
        if pids is None:
            return None
        identidades = []
        for pid in pids:
            estado = _consulta_pid(pid)
            if estado == "muerto":
                continue
            if estado == "incierto":
                return None
            inicio = plataforma.hora_inicio(pid)
            if not inicio:
                if _consulta_pid(pid) == "muerto":
                    continue
                return None
            identidades.append(
                {"pid": pid, "inicio": inicio, "boot_id": plataforma.boot_id()}
            )
        return identidades
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
