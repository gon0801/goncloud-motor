"""Qué puede correr esta máquina y esta base, antes de mandar un encargo.

`motor director capacidades --base B [--json]` mira Python, el mecanismo de
jaula, los agentes instalados (presencia y versión; nunca autenticación ni
cuota), Java y TLC (si verificar.sh puede correr), los recursos de
capacidades.json y si son usables, el canal de avisos, la presencia de
MOTOR_KUMA_URL (nunca su valor) y el arranque con relanzamiento
(launchd con KeepAlive en macOS, el vigilante en Windows).

Sale con código distinto de 0 si falta algo de lo que la máquina declara.
La lectura se documenta en docs/capacidades-por-maquina.md.
"""

from __future__ import annotations

import os
import plistlib
import re
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path

from motor.encargos.capacidades import ConfigInvalida, cargar, abrir_lectura
from motor.encargos.catalogo_agentes import ADAPTADORES, cliente_para

HOME_MOTOR = Path.home() / ".motor"
ETIQUETAS_LAUNCHD = ("com.goncloud.motor", "com.goncloud.motor-encargos")
LLAVE_RUN = r"HKCU\Software\Microsoft\Windows\CurrentVersion\Run"
NOMBRE_LLAVE = "GoncloudMotorEncargos"
VIGILANTE = "iniciar_encargos_pc.py"
TOKEN_ENV = "MOTOR_TELEGRAM_TOKEN"


def _correr(argv, timeout=20):
    return subprocess.run(
        argv, capture_output=True, timeout=timeout, stdin=subprocess.DEVNULL
    )


def _texto(crudo) -> str:
    if isinstance(crudo, bytes):
        crudo = crudo.decode("utf-8", errors="replace")
    texto = " ".join(str(crudo or "").split())
    return texto[:200]


def _primera(r) -> str:
    crudo = (r.stderr or b"").strip().splitlines() or (r.stdout or b"").strip().splitlines()
    return _texto(crudo[0]) if crudo else ""


# ----------------------------------------------------------------- secciones


def _jaula(correr, sandbox_exec: Path = Path("/usr/bin/sandbox-exec")) -> dict:
    if os.name == "nt":
        try:
            r = correr(["wsl.exe", "-l", "-q"], 30)
        except (OSError, subprocess.TimeoutExpired):
            return {"mecanismo": "wsl", "estado": "falta", "detalle": "wsl.exe no corre"}
        distros = [
            linea.strip("\x00 ﻿\r\n")
            for linea in r.stdout.decode("utf-16-le", errors="replace").splitlines()
            if linea.strip("\x00 ﻿\r\n")
        ]
        if distros:
            return {"mecanismo": "wsl", "estado": "ok", "detalle": f"distro WSL: {distros[0]}"}
        return {"mecanismo": "wsl", "estado": "falta", "detalle": "sin distro WSL instalada"}
    if sys.platform == "darwin":
        if sandbox_exec.is_file():
            return {"mecanismo": "seatbelt", "estado": "ok", "detalle": str(sandbox_exec)}
        return {"mecanismo": "seatbelt", "estado": "falta", "detalle": f"falta {sandbox_exec}"}
    try:
        r = correr(["bwrap", "--version"], 15)
    except (OSError, subprocess.TimeoutExpired):
        return {"mecanismo": "bubblewrap", "estado": "falta", "detalle": "bwrap no corre"}
    if r.returncode == 0:
        return {"mecanismo": "bubblewrap", "estado": "ok", "detalle": _primera(r)}
    return {"mecanismo": "bubblewrap", "estado": "falta", "detalle": _primera(r)}


def _agentes(which) -> list[dict]:
    filas = []
    for agente in sorted(ADAPTADORES):
        cliente = cliente_para(agente)
        ruta = which(cliente)
        if not ruta:
            filas.append({
                "agente": agente,
                "estado": "falta",
                "detalle": f"{cliente}: no está en PATH",
            })
            continue
        version = ""
        if os.name == "nt":
            from motor.plataforma.windows import resolve_shim

            argv = [*resolve_shim(cliente, os.environ.get("PATH", "")), "--version"]
        else:
            argv = [ruta, "--version"]
        try:
            r = _correr(argv, 20)
            if r.returncode == 0:
                version = _primera(r)
        except (OSError, subprocess.TimeoutExpired):
            pass
        filas.append({
            "agente": agente,
            "estado": "ok",
            "detalle": f"{cliente} {version or '(--version no respondió)'} en {ruta}",
        })
    return filas


def _java_tlc(correr, which) -> dict:
    jar = os.environ.get("TLA2TOOLS", "")
    if not jar:
        if which("mise"):
            return {
                "estado": "ok",
                "detalle": "mise presente: verificar.sh instala java@temurin-25 y tla2tools 1.7.4",
            }
        return {
            "estado": "falta",
            "detalle": "sin TLA2TOOLS y sin mise: verificar.sh no puede correr",
        }
    if not Path(jar).is_file():
        return {"estado": "falta", "detalle": f"TLA2TOOLS apunta a {jar} y no existe"}
    java_cmd = os.environ.get("JAVA") or "java"
    try:
        r = correr([java_cmd, "-version"], 15)
    except (OSError, subprocess.TimeoutExpired):
        return {"estado": "falta", "detalle": f"java ({java_cmd}) no corre"}
    if r.returncode != 0:
        return {"estado": "falta", "detalle": f"java ({java_cmd}) terminó con código {r.returncode}"}
    return {"estado": "ok", "detalle": f"{_primera(r)}; tla2tools en {jar}"}


def _recursos(base: Path, correr, which) -> tuple[str, list[dict]]:
    try:
        recursos = cargar(base)
    except ConfigInvalida as exc:
        return str(exc), []
    filas = []
    for nombre in sorted(recursos):
        recurso = recursos[nombre]
        tipo = recurso["tipo"]
        if tipo == "sqlite":
            filas.append(_recurso_sqlite(nombre, recurso))
        elif tipo == "remoto" and str(recurso.get("transporte") or "fichero") == "http":
            filas.append({
                "recurso": nombre, "tipo": tipo, "estado": "aviso",
                "detalle": "remoto http: no se sondea",
            })
        elif tipo == "remoto":
            filas.append(_recurso_raiz(nombre, recurso))
        else:
            filas.append(_recurso_ci(nombre, recurso, correr, which))
    return "", filas


def _ruta_absoluta(valor: str) -> Path:
    ruta = Path(str(valor or "")).expanduser()
    if not ruta.is_absolute():
        raise ValueError(f"la ruta del recurso debe ser absoluta: {valor}")
    return ruta


def _recurso_sqlite(nombre: str, recurso: dict) -> dict:
    try:
        ruta = _ruta_absoluta(recurso.get("ruta") or "")
        if not ruta.is_file():
            return {"recurso": nombre, "tipo": "sqlite", "estado": "falta",
                    "detalle": f"{ruta} no existe"}
        db = abrir_lectura(ruta)
        try:
            db.execute("SELECT name FROM sqlite_master LIMIT 1").fetchall()
        finally:
            db.close()
        return {"recurso": nombre, "tipo": "sqlite", "estado": "ok", "detalle": str(ruta)}
    except Exception as exc:  # noqa: BLE001 - la causa es lo que importa
        return {"recurso": nombre, "tipo": "sqlite", "estado": "falta",
                "detalle": f"{type(exc).__name__}: {exc}"[:200]}


def _recurso_raiz(nombre: str, recurso: dict) -> dict:
    try:
        raiz = _ruta_absoluta(recurso.get("raiz") or "")
    except ValueError as exc:
        return {"recurso": nombre, "tipo": "remoto", "estado": "falta", "detalle": str(exc)}
    if not raiz.is_dir() or not os.access(raiz, os.R_OK | os.X_OK):
        return {
            "recurso": nombre, "tipo": "remoto", "estado": "falta",
            "detalle": f"la raiz {raiz} no es una carpeta legible",
        }
    return {"recurso": nombre, "tipo": "remoto", "estado": "ok", "detalle": f"raiz {raiz}"}


def _recurso_ci(nombre: str, recurso: dict, correr, which) -> dict:
    repo = str(recurso.get("repo") or "")
    gh = which("gh")
    if not gh:
        return {"recurso": nombre, "tipo": "ci", "estado": "falta",
                "detalle": f"gh no está instalado: no se puede correr CI sobre {repo}"}
    try:
        r = correr([gh, "repo", "view", repo, "--json", "nameWithOwner"], 30)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"recurso": nombre, "tipo": "ci", "estado": "falta",
                "detalle": f"gh no respondió para {repo}: {exc}"}
    if r.returncode != 0:
        causa = _texto((r.stderr or b"") + (r.stdout or b""))
        return {"recurso": nombre, "tipo": "ci", "estado": "falta",
                "detalle": f"gh no alcanza {repo}: {causa}"}
    return {"recurso": nombre, "tipo": "ci", "estado": "ok",
            "detalle": f"gh ve {repo}: CI disponible"}


def _avisos_y_senal(home: Path) -> tuple[dict, dict]:
    from motor.conf import ConfError, load_conf, load_maquina

    avisos = {"estado": "falta", "detalle": ""}
    senal = {"estado": "falta", "detalle": "MOTOR_KUMA_URL no está en secretos.env ni en el entorno"}
    try:
        mq = load_maquina(home)
        conf = load_conf(mq.conf)
    except (ConfError, OSError, KeyError, tomllib.TOMLDecodeError) as exc:
        detalle = f"maquina.toml o conf ilegibles en {home}: {type(exc).__name__}: {exc}"[:200]
        return {"estado": "falta", "detalle": detalle}, senal
    secretos = dict(mq.secretos)
    token_env = conf.avisos.get("token_env", TOKEN_ENV)
    chat = conf.avisos.get("chat", "")
    if chat and secretos.get(token_env):
        avisos = {"estado": "ok", "detalle": f"chat configurado y {token_env} presente"}
    else:
        avisos = {
            "estado": "falta",
            "detalle": "falta el chat de la conf" if not chat
            else f"falta {token_env} en secretos.env",
        }
    if secretos.get("MOTOR_KUMA_URL") or os.environ.get("MOTOR_KUMA_URL"):
        senal = {"estado": "ok", "detalle": "MOTOR_KUMA_URL presente"}
    return avisos, senal


def _arranque(correr, launchd_dir: Path = Path("/Library/LaunchDaemons")) -> dict:
    if os.name == "nt":
        try:
            r = correr(["reg", "query", LLAVE_RUN, "/v", NOMBRE_LLAVE], 15)
        except (OSError, subprocess.TimeoutExpired):
            return {"mecanismo": "vigilante", "estado": "falta", "detalle": "reg no consultó la llave Run"}
        valor = ""
        if r.returncode == 0:
            for linea in (r.stdout or b"").decode("utf-8", errors="replace").splitlines():
                partes = re.split(r"\s{2,}", linea.strip())
                if partes and partes[0] == NOMBRE_LLAVE and len(partes) >= 3:
                    valor = partes[-1]
        if VIGILANTE in valor.lower():
            return {"mecanismo": "vigilante", "estado": "ok",
                    "detalle": "la llave Run lanza iniciar_encargos_pc.py"}
        return {
            "mecanismo": "vigilante", "estado": "falta",
            "detalle": "la llave Run no lanza el vigilante"
            + (f": {valor}" if valor else " (llave ausente)"),
        }
    if sys.platform == "darwin":
        causas = []
        for etiqueta in ETIQUETAS_LAUNCHD:
            ruta = launchd_dir / f"{etiqueta}.plist"
            try:
                with open(ruta, "rb") as f:
                    datos = plistlib.load(f)
            except OSError as exc:
                causas.append(f"{etiqueta}: {exc.strerror or exc}")
                continue
            except ValueError as exc:
                causas.append(f"{etiqueta}: plist ilegible ({exc})")
                continue
            if datos.get("KeepAlive") is True:
                return {"mecanismo": "launchd", "estado": "ok",
                        "detalle": f"{ruta} con KeepAlive"}
            causas.append(f"{etiqueta}: sin KeepAlive")
        return {"mecanismo": "launchd", "estado": "falta",
                "detalle": "; ".join(causas) or "sin LaunchDaemon del motor"}
    return {"mecanismo": "ninguno", "estado": "aviso",
            "detalle": "sin política de arranque declarada para Linux"}


# ------------------------------------------------------------------ reporte


def reporte(base: Path, home: Path | None = None, correr=None, which=None) -> dict:
    correr = correr or _correr
    which = which or shutil.which
    base = Path(base)
    avisos, senal = _avisos_y_senal(Path(home) if home else HOME_MOTOR)
    recursos_causa, recursos_filas = _recursos(base, correr, which)
    if not (base / "capacidades.json").is_file():
        recursos_causa = (
            "sin capacidades.json en la base: los agentes no pueden pedir recursos"
        )
    agentes = _agentes(which)
    secciones = {
        "jaula": _jaula(correr),
        "java_tlc": _java_tlc(correr, which),
        "avisos": avisos,
        "senal": senal,
        "arranque": _arranque(correr),
    }
    fallidos = [f["recurso"] for f in recursos_filas if f["estado"] == "falta"]
    recursos = {
        "estado": "falta" if recursos_causa or fallidos else "ok",
        "detalle": recursos_causa
        or ("no usables: " + ", ".join(fallidos) if fallidos
            else f"{len(recursos_filas)} recursos usables"),
        "filas": recursos_filas,
    }
    secciones["recursos"] = recursos
    secciones["agentes"] = {
        "estado": "falta" if any(f["estado"] == "falta" for f in agentes) else "ok",
        "detalle": f"{sum(1 for f in agentes if f['estado'] == 'ok')} de {len(agentes)} clientes en PATH",
        "filas": agentes,
    }
    nombre = ""
    try:
        from motor.conf import ConfError, load_maquina

        nombre = load_maquina(Path(home) if home else HOME_MOTOR).nombre
    except Exception:  # noqa: BLE001 - el nombre es informativo, no una condición
        pass
    faltas = [
        f"{clave}: {seccion['detalle']}"
        for clave, seccion in secciones.items()
        if seccion["estado"] == "falta"
    ]
    return {
        "maquina": {
            "sistema": (
                "macos" if sys.platform == "darwin"
                else "windows" if os.name == "nt" else "linux"
            ),
            "nombre": nombre,
            "python": sys.version.split()[0],
            "ejecutable": sys.executable,
        },
        **secciones,
        "faltas": faltas,
        "ok": not faltas,
    }


def texto(datos: dict) -> str:
    maquina = datos.get("maquina") or {}
    lineas = [
        f"maquina    {maquina.get('sistema') or '?'}"
        + (f" {maquina['nombre']}" if maquina.get("nombre") else "")
        + f" · Python {maquina.get('python') or '?'}",
    ]
    for clave in ("jaula", "java_tlc", "avisos", "senal", "arranque", "recursos", "agentes"):
        seccion = datos.get(clave) or {}
        lineas.append(f"{clave:<10} {seccion.get('estado', '?'):<5}  {seccion.get('detalle') or ''}")
        for fila in seccion.get("filas", []):
            nombre = fila.get("recurso") or fila.get("agente") or ""
            lineas.append(f"  {nombre:<10} {fila.get('estado', '?'):<5}  {fila.get('detalle') or ''}")
    if datos.get("faltas"):
        lineas.append("faltas:")
        lineas.extend(f"  - {f}" for f in datos["faltas"])
    else:
        lineas.append("todo lo que la máquina declara está")
    return "\n".join(lineas)
