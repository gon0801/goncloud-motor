"""Capacidades de un comando y sus hijos. El Job administra vida, no permisos."""

from __future__ import annotations

import hashlib
import ipaddress
import json
import os
import shutil
import socket
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

import tomllib

from .autenticacion_kimi import acceso_kimi
from .catalogo_agentes import ADAPTADORES, cliente_para
from .suscripciones import CuentaNoDisponible, leer_cuenta


@dataclass(frozen=True)
class ComandoAislado:
    argv: list[str]
    entorno: dict[str, str]
    mecanismo: str
    jaula: dict | None = None
    ejecutable: dict | None = None


def lecturas_del_interprete() -> tuple[Path, ...]:
    """La instalación del intérprete que corre este supervisor, para leerla y
    nada más. En la Mini el Python 3.12 de uv vive bajo el home, que la jaula
    niega: sin esto arranca con "No module named encodings" y el comprobador
    se queda con el Python 3.9 del sistema. Se concede el ejecutable (y su
    enlace) y los prefijos de la instalación; nunca el home ni sus padres."""
    home = Path.home().resolve()
    amplios = {home, *home.parents}
    candidatos = [Path(sys.executable)]
    candidatos += [
        Path(p) for p in (sys.base_prefix, sys.prefix, sys.base_exec_prefix, sys.exec_prefix)
    ]
    salida: list[Path] = []

    def agregar(ruta: Path) -> None:
        if ruta.exists() and ruta not in amplios and ruta not in salida:
            salida.append(ruta)

    for candidato in candidatos:
        agregar(candidato)
        agregar(candidato.resolve())
        # `~/.local/bin/python3` apunta a un directorio que a su vez es un
        # enlace: cada salto bajo el home se concede como tal, porque la jaula
        # solo conoce las rutas resueltas.
        actual = Path(os.path.abspath(candidato))
        recorrido = Path(actual.anchor)
        for parte in actual.parts[1:]:
            recorrido = recorrido / parte
            if recorrido.is_symlink() and home in recorrido.parents:
                agregar(recorrido)
    return tuple(salida)


def _codex_windows() -> Path:
    directo = shutil.which("codex.exe")
    if directo:
        return Path(directo).resolve()
    shim = shutil.which("codex.cmd")
    if shim:
        paquete = Path(shim).parent / "node_modules" / "@openai" / "codex"
        for arquitectura in ("x64", "arm64"):
            raiz = paquete / "node_modules" / "@openai" / f"codex-win32-{arquitectura}"
            candidatos = list(raiz.glob("vendor/*/bin/codex.exe"))
            if len(candidatos) == 1:
                return candidatos[0].resolve()
    raise RuntimeError("no se encontro el ejecutable nativo instalado de Codex/MXC")


def _entorno(
    runtime: Path, suscripcion: bool, cmd: list[str], *, agente: str | None = None
) -> dict[str, str]:
    permitidas = {
        "PATH",
        "SYSTEMROOT",
        "SYSTEMDRIVE",
        "WINDIR",
        "COMSPEC",
        "PATHEXT",
        "LANG",
        "TERM",
        "TZ",
        "PROGRAMDATA",
        "PROGRAMFILES",
        "PROGRAMFILES(X86)",
        "PROGRAMW6432",
    }
    env = {
        k: v
        for k, v in os.environ.items()
        if k.upper() in permitidas or k.upper().startswith("LC_")
    }
    if os.name == "nt":
        env["PATH"] = (
            str(Path(sys.executable).resolve().parent)
            + os.pathsep
            + env.get("PATH", "")
        )
    temporal = runtime / "tmp"
    temporal.mkdir(parents=True, exist_ok=True)
    env.update(
        HOME=str(runtime),
        USERPROFILE=str(runtime),
        APPDATA=str(runtime / "AppData" / "Roaming"),
        LOCALAPPDATA=str(runtime / "AppData" / "Local"),
        XDG_CONFIG_HOME=str(runtime / ".config"),
        XDG_DATA_HOME=str(runtime / ".local" / "share"),
        TMPDIR=str(temporal),
        TEMP=str(temporal),
        TMP=str(temporal),
        # El CLI de Claude no mira TMPDIR para el temporal de su shell: usa
        # CLAUDE_CODE_TMPDIR y, si falta, cae en /tmp. Fuera de la jaula eso es
        # EPERM en cada orden. Ver incidente-mini-cuota-20261029.
        CLAUDE_CODE_TMPDIR=str(temporal),
        PYTHONDONTWRITEBYTECODE="1",
        CODEX_HOME=str(runtime / ".codex"),
    )
    for path in (
        env["APPDATA"],
        env["LOCALAPPDATA"],
        env["CODEX_HOME"],
        env["XDG_CONFIG_HOME"],
        env["XDG_DATA_HOME"],
    ):
        Path(path).mkdir(parents=True, exist_ok=True)
    if suscripcion:
        nombre = agente or Path(cmd[0]).stem.lower()
        if nombre in ("claude", "opencode", "qwen", "grok"):
            from .suscripciones import preparar

            preparar(nombre, runtime, cmd, env)
            return env
        if nombre == "muse":
            cuenta = leer_cuenta(nombre)
            documento = json.loads(
                (Path.home() / ".config/muse/auth.json").read_text(encoding="utf-8")
            )
            destino = runtime / ".config/muse/auth.json"
            destino.parent.mkdir(parents=True, exist_ok=True)
            destino.write_text(
                json.dumps(
                    {
                        "schema_version": documento.get("schema_version", 1),
                        "providers": {"meta": cuenta},
                    }
                ),
                encoding="utf-8",
            )
            destino.chmod(0o600)
            return env
        if nombre == "kimi":
            expira = _preparar_kimi(runtime, cmd)
            env.update(
                KIMI_CODE_HOME=str(runtime / ".kimi-code"),
                MOTOR_KIMI_ACCESS_EXPIRES_AT=str(expira),
                KIMI_CODE_NO_AUTO_UPDATE="1",
                KIMI_DISABLE_TELEMETRY="1",
                KIMI_CODE_MODEL_CATALOG_REFRESH_ON_START="false",
            )
            if cliente_para(nombre) == "opencode":
                from .suscripciones import preparar_kimi_opencode

                preparar_kimi_opencode(runtime, env)
            return env
        if nombre == "codex":
            origen = Path(os.environ.get("CODEX_HOME", str(Path.home() / ".codex")))
            destino = runtime / ".codex"
        else:
            return env
        auth = origen / "auth.json"
        if auth.is_file():
            if nombre == "codex":
                try:
                    datos = json.loads(auth.read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    raise CuentaNoDisponible(
                        nombre, "no se pudo leer el login de suscripcion"
                    ) from None
                if datos.get("auth_mode") != "chatgpt" or datos.get("OPENAI_API_KEY"):
                    raise CuentaNoDisponible(
                        nombre,
                        "el encargo necesita autenticacion por suscripcion de ChatGPT",
                    )
            destino.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(auth, destino / "auth.json")
            (destino / "auth.json").chmod(0o600)
    return env


def _preparar_kimi(runtime: Path, cmd: list[str]) -> float:
    origen = Path(os.environ.get("KIMI_CODE_HOME", str(Path.home() / ".kimi-code")))
    try:
        config = tomllib.loads((origen / "config.toml").read_text(encoding="utf-8"))
        alias = (
            cmd[cmd.index("--model") + 1]
            if "--model" in cmd
            else config["default_model"]
        )
        if alias.startswith("motor-kimi/"):
            alias = "kimi-code/" + alias.partition("/")[2]
        if alias == "kimi-code/k3" and alias not in config["models"]:
            modelo = {
                "provider": "managed:kimi-code",
                "model": "k3",
                "max_context_size": 1048576,
                "capabilities": [
                    "thinking",
                    "always_thinking",
                    "image_in",
                    "video_in",
                    "tool_use",
                ],
                "support_efforts": ["low", "high", "max"],
                "default_effort": "high",
            }
        else:
            modelo = config["models"][alias]
        proveedor = config["providers"][modelo["provider"]]
    except (OSError, ValueError, TypeError, KeyError):
        raise CuentaNoDisponible(
            "kimi", "no se pudo leer el modelo de la suscripcion configurada"
        ) from None
    oauth = proveedor.get("oauth", {})
    base_url = proveedor.get("base_url", "").rstrip("/")
    oauth_host = oauth.get("oauth_host", "https://auth.kimi.com").rstrip("/")
    pares = {
        "https://api.kimi.com/coding/v1": "https://auth.kimi.com",
        "https://api.kimi.ai/coding/v1": "https://auth.kimi.ai",
    }
    clave = oauth.get("key", "")
    if (
        modelo["provider"] != "managed:kimi-code"
        or proveedor.get("api_key")
        or proveedor.get("api_key_env")
        or pares.get(base_url) != oauth_host
        or not isinstance(clave, str)
        or not clave.startswith("oauth/")
    ):
        raise CuentaNoDisponible(
            "kimi", "el encargo necesita la suscripcion OAuth de Kimi Code"
        )
    destino = runtime / ".kimi-code"
    try:
        acceso = acceso_kimi(
            origen, credencial=clave.removeprefix("oauth/"), oauth_host=oauth_host
        )
    except RuntimeError as exc:
        raise CuentaNoDisponible("kimi", str(exc)) from None
    destino.mkdir(parents=True, exist_ok=True)
    for relativo in ("device_id", "region"):
        fuente = origen / relativo
        if fuente.is_file():
            shutil.copyfile(fuente, destino / relativo)
            (destino / relativo).chmod(0o600)
    texto = [
        f"default_model = {json.dumps(alias)}",
        '[providers."managed:kimi-code"]',
        'type = "kimi"',
        "api_key = " + json.dumps(acceso.token),
        "base_url = " + json.dumps(base_url),
        "[models." + json.dumps(alias) + "]",
        'provider = "managed:kimi-code"',
    ]
    for clave in (
        "model",
        "max_context_size",
        "max_output_size",
        "capabilities",
        "support_efforts",
        "default_effort",
    ):
        if clave in modelo:
            texto.append(clave + " = " + json.dumps(modelo[clave]))
    (destino / "config.toml").write_text("\n".join(texto) + "\n", encoding="utf-8")
    (destino / "config.toml").chmod(0o600)
    return acceso.expira


def _muse_local(
    argv: list[str], runtime: Path, entorno: dict[str, str]
) -> tuple[list[str], dict]:
    origen = Path(argv[0]).resolve()
    destino = runtime / "cli" / origen.name
    destino.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(origen, destino)
    shutil.copymode(origen, destino)
    with destino.open("rb") as fuente:
        huella = hashlib.file_digest(fuente, "sha256").hexdigest()
    try:
        version = subprocess.run(
            [str(destino), "--version"],
            cwd=runtime,
            env=entorno,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=20,
            check=True,
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError) as exc:
        raise RuntimeError(
            "no se pudo comprobar la version de Muse: " + type(exc).__name__
        ) from exc
    procedencia = {
        "origen": str(origen),
        "ejecutable": str(destino),
        "sha256": huella,
        "version": version,
        "tipo": "instalacion-local-copiada",
    }
    return [str(destino), *argv[1:]], procedencia


def aislar(
    cmd: list[str],
    espacio: Path,
    runtime: Path,
    *,
    lectura: bool = False,
    suscripcion: bool = False,
    lecturas_extra: tuple[Path, ...] = (),
    agente: str | None = None,
) -> ComandoAislado:
    espacio, runtime = espacio.resolve(), runtime.resolve()
    if espacio.parent.name != "espacio" or runtime.is_relative_to(espacio):
        raise RuntimeError("la jaula necesita espacio concedido y runtime separado")
    runtime.mkdir(parents=True, exist_ok=True)
    nombre = agente or Path(cmd[0]).stem.lower()
    if suscripcion and usa_wsl(nombre):
        return _comando_wsl(cmd, espacio, runtime, lectura, lecturas_extra, nombre)
    env = _entorno(runtime, suscripcion, cmd, agente=agente)
    procedencia = None
    if os.name == "nt":
        raices = [
            Path(sys.executable).resolve().parent,
            Path(os.environ["SystemRoot"]),
        ]
        if Path(cmd[0]).stem.lower() == "codex":
            ejecutable = Path(cmd[0])
            broker = (
                ejecutable.resolve() if ejecutable.is_absolute() else _codex_windows()
            )
            if broker.is_relative_to(espacio) or broker.is_relative_to(runtime):
                raise RuntimeError("el broker de aislamiento pertenece al trabajador")
            raices.append(broker.parent)
            argv = [str(broker), *cmd[1:]]
        else:
            from motor.plataforma.windows import argv_windows

            argv = argv_windows(cmd, env)
            if not Path(argv[0]).is_absolute():
                ejecutable = shutil.which(argv[0], path=env.get("PATH"))
                if not ejecutable:
                    raise FileNotFoundError(argv[0])
                argv = [str(Path(ejecutable).resolve()), *argv[1:]]
        if nombre == "muse":
            argv, procedencia = _muse_local(argv, runtime, env)
        raices.extend(path.resolve() for path in lecturas_extra)
        if nombre in ADAPTADORES and ADAPTADORES[nombre].psec:
            raices.append(Path(argv[0]).parent)
            env["NODE_OPTIONS"] = "--preserve-symlinks --preserve-symlinks-main"
        raices.extend(
            Path(os.environ[k])
            for k in ("ProgramFiles", "ProgramFiles(x86)")
            if k in os.environ
        )
        if suscripcion and nombre not in ADAPTADORES:
            raise RuntimeError("el agente no tiene adaptador Windows de suscripcion")
        if suscripcion and nombre == "codex":
            capacidades = [
                'web_search="disabled"',
                "projects." + json.dumps(str(espacio)) + '.trust_level="untrusted"',
                *(
                    "features." + nombre + "=false"
                    for nombre in (
                        "apps",
                        "plugins",
                        "hooks",
                        "multi_agent",
                        "image_generation",
                        "standalone_web_search",
                    )
                ),
            ]
            argv = [argv[0], *(x for c in capacidades for x in ("-c", c)), *argv[1:]]
        limites = {
            "lectura": [str(x) for x in raices] + ([str(espacio)] if lectura else []),
            "escritura": [str(runtime)] + ([] if lectura else [str(espacio)]),
            "internet": suscripcion,
            "proveedor": sorted(
                {
                    str(ipaddress.ip_address(fila[4][0]))
                    for host in ADAPTADORES[nombre].dominios
                    for fila in socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
                    if ipaddress.ip_address(fila[4][0]).is_global
                }
            )
            if suscripcion
            else [],
            "runtime": str(runtime),
        }
        if suscripcion and not limites["proveedor"]:
            raise RuntimeError(
                "no hay direcciones publicas del proveedor para la suscripcion"
            )
        if nombre in ADAPTADORES and ADAPTADORES[nombre].psec:
            from motor.plataforma.psec_windows import enumeracion_para

            limites["enumeracion"] = enumeracion_para(limites)
        return ComandoAislado(argv, env, "appcontainer", limites, procedencia)
    sandbox = Path("/usr/bin/sandbox-exec")
    if not sandbox.is_file():
        raise RuntimeError(
            "sin aislamiento efectivo; trabajador no habilitado en este host"
        )
    perfil = [
        "(version 1)",
        "(allow default)",
        "(deny file-write*)",
        f"(deny file-read* (subpath {json.dumps(str(Path.home()))}))",
        "(allow file-read-metadata)",
    ]
    if nombre == "muse":
        from .autenticacion_muse import ejecutable_muse

        nativo = ejecutable_muse()
        cmd = [str(nativo), *cmd[1:]]
        perfil.append(f"(allow file-read* (literal {json.dumps(str(nativo))}))")
        procedencia = {
            "origen": str(nativo),
            "ejecutable": str(nativo),
            "sha256": hashlib.sha256(nativo.read_bytes()).hexdigest(),
            "tipo": "instalacion-local-firmada",
        }
    for path in (espacio, runtime, *(p.resolve() for p in lecturas_extra)):
        perfil.append(f"(allow file-read* (subpath {json.dumps(str(path))}))")
    for path in lecturas_extra:
        # Un enlace concedido se lee como enlace además de su destino resuelto.
        sin_resolver = Path(os.path.abspath(path))
        if sin_resolver != sin_resolver.resolve():
            perfil.append(f"(allow file-read* (literal {json.dumps(str(sin_resolver))}))")
    for path in (runtime,) if lectura else (espacio, runtime):
        perfil.append(f"(allow file-write* (subpath {json.dumps(str(path))}))")
    for path in ("/dev/null", "/dev/tty"):
        perfil.append(f'(allow file-write* (literal "{path}"))')
    path = runtime / "jaula.sb"
    path.write_text("\n".join(perfil), encoding="utf-8")
    return ComandoAislado(
        [str(sandbox), "-f", str(path), *cmd], env, "seatbelt", ejecutable=procedencia
    )


def usa_wsl(agente: str | None) -> bool:
    """En Windows, el agente que necesita shell corre dentro de WSL con la jaula de
    bubblewrap: el bash de Git (msys) no arranca en un AppContainer. Los demás agentes
    siguen en la jaula de Windows."""
    return _en_windows() and usa_shell_de_git(agente)


def _comando_wsl(
    cmd: list[str],
    espacio: Path,
    runtime: Path,
    lectura: bool,
    lecturas_extra: tuple[Path, ...],
    agente: str,
) -> ComandoAislado:
    """Describe el lanzamiento en la distro. `argv` es el comando del agente tal como lo
    arma el motor, con rutas de Windows; las rutas y el entorno de la distro los resuelve
    `jaula_wsl.argv_guardian` al lanzar, cuando ya se sumó el entorno extra del intento.
    La jaula no tiene red propia: sale solo a los dominios del proveedor del agente, por el
    proxy del guardián, igual que el AppContainer solo concede las direcciones del proveedor."""
    from motor.plataforma import jaula_linux, jaula_wsl

    if agente not in ADAPTADORES:
        raise RuntimeError("el agente no tiene adaptador de suscripcion")
    jaula = jaula_wsl.jaula_de(
        espacio,
        runtime,
        lectura=lectura,
        lecturas=tuple(p.resolve() for p in lecturas_extra),
        agente=agente,
        red=jaula_linux.RED_PROVEEDOR,
        dominios=ADAPTADORES[agente].dominios,
    )
    return ComandoAislado(list(cmd), {}, jaula_wsl.MECANISMO, jaula)


@dataclass(frozen=True)
class SondaEntorno:
    """Resultado de comprobar, bajo el aislamiento real, lo que el trabajador
    necesita para empezar. `ok` es la única evidencia de recuperación válida:
    una fecha, una generación o un cambio de modelo no la sustituyen."""

    ok: bool
    causa: str = ""
    condicion: str = ""


_GUION_SONDA_BASE = """set -u
marca="${MOTOR_SONDA_MARCA:?}"
temporal="${CLAUDE_CODE_TMPDIR:?}"
uid=$(id -u)
base="$temporal/claude-$uid/sonda-$marca"
if mkdir -p "$base" 2>/dev/null; then :; else echo "temp-cli:$base"; exit 3; fi
if : > "$base/marca" 2>/dev/null; then :; else echo "temp-cli:$base"; exit 3; fi
rm -rf "$base" 2>/dev/null
shell="${TMPDIR:-}"
if [ -z "$shell" ]; then echo "temp-shell:sinnada"; exit 4; fi
if ( set -C; : > "$shell/marca-sonda-$marca" ) 2>/dev/null; then :; else
  echo "temp-shell:$shell"; exit 4
fi
rm -f "$shell/marca-sonda-$marca" 2>/dev/null
# Nada del producto se toca: nombre irrepetible y sin sobrescribir.
if ( set -C; : > "$MOTOR_SONDA_ESPACIO/.sonda-$marca" ) 2>/dev/null; then :; else
  echo "espacio:$MOTOR_SONDA_ESPACIO"; exit 5
fi
rm -f "$MOTOR_SONDA_ESPACIO/.sonda-$marca" 2>/dev/null
for ruta in "$@"; do
  if [ -d "$ruta" ]; then
    if ls "$ruta" >/dev/null 2>&1; then :; else echo "fuente:$ruta"; exit 6; fi
  else
    if cat "$ruta" >/dev/null 2>&1; then :; else echo "fuente:$ruta"; exit 6; fi
  fi
done
"""

_GUION_SONDA = _GUION_SONDA_BASE + "echo ok\n"

# Lo que el agente de WSL usa: bash, sh y un `git push` a un bare local. Cada fallo nombra
# la operacion (shell | git) y trae el codigo y la salida en una linea.
_GUION_SHELL_WSL = r"""
una_linea() { tr '\n' ' ' | cut -c1-300; }
probar() {
  clase="$1"; shift
  salida=$("$@" 2>&1); rc=$?
  if [ "$rc" != 0 ] || [ "$(printf %s "$salida" | tail -n 1)" != ok ]; then
    echo "$clase:codigo $rc: $(printf %s "$salida" | una_linea)"; rm -rf "${carpeta:-/nonexistent}"; exit 7
  fi
}
probar shell /bin/bash -c 'echo ok'
probar shell /bin/sh -c 'echo ok'
carpeta="$TMPDIR/git-sonda-$marca"
rm -rf "$carpeta"
if mkdir -p "$carpeta" 2>/dev/null; then :; else echo "temp-shell:$TMPDIR"; exit 4; fi
probar git /bin/bash -c 'cd "$1" && git init -q a && git init -q --bare b.git && cd a && git -c user.email=sonda@motor -c user.name=sonda commit -q --allow-empty -m sonda && git push -q ../b.git HEAD:main && echo ok' sonda "$carpeta"
rm -rf "$carpeta"
echo ok
"""

_GUION_SONDA_WSL = _GUION_SONDA_BASE + _GUION_SHELL_WSL
PLAZO_SONDA_WSL_S = 60


def _sonda_de(marca: str) -> SondaEntorno:
    clase, _, detalle = marca.partition(":")
    if clase == "temp-cli":
        return SondaEntorno(
            False,
            f"no se puede crear el temporal del CLI en {detalle}",
            "que el temporal del CLI caiga en una ruta escribible del aislamiento",
        )
    if clase == "temp-shell":
        return SondaEntorno(
            False,
            f"no se puede escribir en el temporal del shell ({detalle})",
            "que el temporal del shell caiga en una ruta escribible del aislamiento",
        )
    if clase == "espacio":
        return SondaEntorno(
            False,
            f"el espacio concedido no admite escritura ({detalle})",
            "que el espacio concedido vuelva a ser escribible",
        )
    if clase == "fuente":
        return SondaEntorno(
            False,
            f"no se puede leer la fuente autorizada {detalle}",
            "que la fuente autorizada exista y se conceda al trabajador",
        )
    if clase in ("shell", "git"):
        quien = "bash" if clase == "shell" else "git"
        return SondaEntorno(
            False,
            f"{quien} no funciona en la jaula de WSL ({detalle})",
            f"que {quien} funcione dentro de la jaula de WSL con el entorno del agente",
        )
    return SondaEntorno(
        False,
        f"la sonda del entorno no terminó: {detalle or 'sin detalle'}",
        "que el aislamiento del host funcione",
    )


_GUION_SONDA_PY = """
import os, shutil, sys
marca = os.environ["MOTOR_SONDA_MARCA"]
def falla(clase, detalle):
    print(f"{clase}:{detalle}")
    sys.exit(3)
temporal = os.environ.get("CLAUDE_CODE_TMPDIR") or ""
if not temporal:
    falla("temp-cli", "sinnada")
base = os.path.join(temporal, "sonda-" + marca)
try:
    os.makedirs(base, exist_ok=True)
    with open(os.path.join(base, "marca"), "x"):
        pass
    shutil.rmtree(base)
except OSError:
    falla("temp-cli", base)
shell = os.environ.get("TMPDIR") or ""
if not shell:
    falla("temp-shell", "sinnada")
marca_shell = os.path.join(shell, "marca-sonda-" + marca)
try:
    with open(marca_shell, "x"):
        pass
    os.unlink(marca_shell)
except OSError:
    falla("temp-shell", shell)
espacio = os.environ["MOTOR_SONDA_ESPACIO"]
marca_espacio = os.path.join(espacio, ".sonda-" + marca)
try:
    with open(marca_espacio, "x"):
        pass
    os.unlink(marca_espacio)
except OSError:
    falla("espacio", espacio)
for ruta in sys.argv[1:]:
    try:
        if os.path.isdir(ruta):
            os.listdir(ruta)
        else:
            with open(ruta, "rb") as f:
                f.read(1)
    except OSError:
        falla("fuente", ruta)
print("ok")
"""


def _en_windows() -> bool:
    return os.name == "nt"


SHELL_DE_GIT = frozenset({"claude"})


def usa_shell_de_git(agente: str | None) -> bool:
    """Claude Code corre sus órdenes con el bash de Git. Decide el agente que se
    va a lanzar en ese intento, no la lista de agentes autorizados."""
    return agente in SHELL_DE_GIT


def sondear(
    espacio: Path,
    runtime: Path,
    lecturas: tuple[Path, ...] = (),
    agente: str | None = None,
) -> SondaEntorno:
    """Comprueba con comandos locales, y sin llamar a ningún modelo, las
    operaciones que el trabajador necesita al arrancar: su temporal de shell,
    la escritura en el espacio y la lectura de las fuentes autorizadas. En
    Windows corre dentro de la jaula. Un agente que usa el bash de Git (`usa_wsl`)
    corre en la distro, así que su sonda es la de WSL, con bash, `sh` y un
    `git push` a un bare local; el AppContainer, donde msys no arranca, no se
    prueba para él porque no lo ejecuta.

    No acredita lo que no pudo ejecutar: sin jaula, o con una fuente declarada
    que ya no está, el resultado es un fallo con su condición. Nunca propaga una
    excepción: la llama el supervisor, que en la PC arranca una vez desde una
    tarea programada y no se reinicia solo.
    """
    import uuid

    espacio, runtime = Path(espacio).resolve(), Path(runtime).resolve()
    fuentes = tuple(Path(p).expanduser().resolve() for p in lecturas)
    marca = uuid.uuid4().hex[:16]
    windows = _en_windows()
    if usa_wsl(agente):
        return _sondear_en_wsl(espacio, runtime, fuentes, marca)
    try:
        if windows:
            argv_sonda = [
                sys.executable,
                "-c",
                _GUION_SONDA_PY,
                *(str(p) for p in fuentes),
            ]
        else:
            argv_sonda = [
                "/bin/sh",
                "-c",
                _GUION_SONDA,
                "sonda",
                *(str(p) for p in fuentes),
            ]
        comando = aislar(
            argv_sonda,
            espacio,
            runtime,
            lecturas_extra=fuentes,
        )
    except Exception as exc:  # noqa: BLE001  la sonda informa, no propaga
        return SondaEntorno(
            False,
            f"no se pudo preparar el aislamiento: {type(exc).__name__}: {exc}",
            "que el aislamiento del host esté disponible",
        )
    try:
        entorno = dict(comando.entorno)
        entorno["MOTOR_SONDA_ESPACIO"] = str(espacio)
        entorno["MOTOR_SONDA_MARCA"] = marca
        codigo, salida, error = _ejecutar_en_jaula(comando, espacio, entorno, plazo=90)
    except Exception as exc:  # noqa: BLE001  la sonda informa, no propaga
        return SondaEntorno(
            False,
            f"no se pudo ejecutar la sonda bajo el aislamiento: {type(exc).__name__}: {exc}",
            "que el aislamiento del host esté disponible",
        )
    return _resultado_de_sonda(codigo, salida, error)


def _resultado_de_sonda(
    codigo: int, salida: str, error: str, wsl: bool = False
) -> SondaEntorno:
    lineas = [linea for linea in (salida or "").splitlines() if linea.strip()]
    if codigo == 0 and lineas and lineas[-1].strip() == "ok":
        return SondaEntorno(True)
    if not lineas:
        detalle = " ".join((error or "").split())[:300]
        return SondaEntorno(
            False,
            f"la sonda del entorno no terminó: codigo {codigo}"
            + (f": {detalle}" if detalle else ""),
            "que la jaula de WSL funcione" if wsl else "que el aislamiento del host funcione",
        )
    return _sonda_de(lineas[-1].strip())


def _sondear_en_wsl(
    espacio: Path, runtime: Path, fuentes: tuple[Path, ...], marca: str
) -> SondaEntorno:
    """La sonda de un agente que corre en WSL, dentro de la misma jaula en que correrá: temporal
    del CLI y del shell, escritura en el espacio, lectura de las fuentes autorizadas, bash, sh
    y un `git push` a un bare local. Con la misma regla que la de Windows: no acredita lo que
    no pudo ejecutar y nunca propaga una excepción."""
    try:
        from motor.plataforma import jaula_wsl

        runtime.mkdir(parents=True, exist_ok=True)
        jaula = jaula_wsl.jaula_de(
            espacio, runtime, lecturas=tuple(p for p in fuentes if p.exists())
        )
        entorno = {
            "MOTOR_SONDA_ESPACIO": jaula_wsl.a_wsl(str(espacio)),
            "MOTOR_SONDA_MARCA": marca,
        }
        cmd = ["/bin/sh", "-c", _GUION_SONDA_WSL, "sonda", *(jaula_wsl.a_wsl(str(p)) for p in fuentes)]
        codigo, salida, error = jaula_wsl.ejecutar(
            jaula, cmd, entorno, control=runtime.with_name(runtime.name + "-control"),
            cwd=espacio, plazo=PLAZO_SONDA_WSL_S,
        )
    except Exception as exc:  # noqa: BLE001  la sonda informa, no propaga
        return SondaEntorno(
            False,
            f"no se pudo ejecutar la sonda en la jaula de WSL: {type(exc).__name__}: {exc}",
            "que la jaula de WSL esté disponible",
        )
    return _resultado_de_sonda(codigo, salida, error, wsl=True)


def _herramientas_git(entorno: dict[str, str]) -> dict[str, str]:
    """Git y los shells de su instalación, donde los busca Claude Code: el
    `bin\\bash.exe` de la instalación que resuelve `git` por el PATH del agente,
    y en su defecto el de la ruta habitual. Una ruta que no existe no se omite:
    la sonda la prueba y falla con su nombre."""
    from motor.plataforma.windows import BASH_GIT_POR_OMISION

    git = shutil.which("git", path=entorno.get("PATH"))
    raiz = Path(git).resolve().parent.parent if git else None
    if raiz is not None and (raiz / "bin" / "bash.exe").is_file():
        bash = raiz / "bin" / "bash.exe"
    else:
        bash = Path(BASH_GIT_POR_OMISION)
    return {
        "MOTOR_SONDA_SHELL": str(bash),
        "MOTOR_SONDA_SH": str(bash.parent.parent / "usr" / "bin" / "sh.exe"),
        "MOTOR_SONDA_GIT": git or str(bash.parent.parent / "cmd" / "git.exe"),
    }


def lanzar_en_jaula(
    comando: ComandoAislado, cwd: Path, entorno: dict[str, str], *, plazo: float
) -> tuple[int, str]:
    codigo, salida, _error = _ejecutar_en_jaula(comando, cwd, entorno, plazo=plazo)
    return codigo, salida


def _ejecutar_en_jaula(
    comando: ComandoAislado, cwd: Path, entorno: dict[str, str], *, plazo: float
) -> tuple[int, str, str]:
    """Corre `comando` como lo lanza el motor y devuelve código, salida y error.
    En Windows eso es dentro del AppContainer (`aislar` solo lo describe);
    `subprocess.run` lo ejecutaría sin jaula y la sonda daría por bueno un
    entorno que el agente no tiene."""
    if comando.mecanismo == "wsl-bwrap":  # jaula_wsl.MECANISMO
        raise RuntimeError("un comando de la jaula de WSL no se ejecuta con subprocess.run")
    if comando.mecanismo != "appcontainer":
        hecho = subprocess.run(
            comando.argv,
            cwd=str(cwd),
            env=entorno,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=plazo,
        )
        return hecho.returncode, hecho.stdout or "", hecho.stderr or ""
    from motor import plataforma
    from motor.plataforma.jaula_windows import crear

    creado = crear(
        comando.argv,
        comando.jaula,
        cwd=cwd,
        env=entorno,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    p = creado.proceso
    try:
        creado.liberar()
        salida, error = p.communicate(timeout=plazo)
        return p.returncode, salida or "", error or ""
    finally:
        plataforma.matar_grupo(p)
