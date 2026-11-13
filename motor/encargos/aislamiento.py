"""Capacidades de un comando y sus hijos. El Job administra vida, no permisos."""

from __future__ import annotations
from motor.io import escribir_texto, leer_json, leer_texto

import hashlib
import json
import os
import shlex
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

import tomllib

from .autenticacion_kimi import acceso_kimi
from .catalogo_agentes import cliente_para
from .suscripciones import CuentaNoDisponible


@dataclass(frozen=True)
class ComandoAislado:
    argv: list[str]
    entorno: dict[str, str]
    mecanismo: str
    ejecutable: dict | None = None


MECANISMO_DIRECTO = "directo"
ENTORNO_DEL_AGENTE = "MOTOR_ENTORNO_AGENTE"
GUARDIA = "MOTOR_GUARDIA"
GUARDIA_GH = Path(__file__).resolve().parent.parent / "shims" / "gh_guard.py"


def delta_de_entorno(comando: ComandoAislado) -> dict[str, str]:
    """La parte del entorno que el motor añade o cambia (el temporal del intento, la suscripción,
    el intérprete en el PATH). Viaja al wrapper en `ENTORNO_DEL_AGENTE`, nunca por disco: trae las
    llaves de las suscripciones. El resto lo hereda el agente del supervisor."""
    if comando.mecanismo != MECANISMO_DIRECTO:
        return dict(comando.entorno)
    return {
        clave: valor
        for clave, valor in comando.entorno.items()
        if os.environ.get(clave) != valor
    }


def entorno_recibido() -> dict[str, str]:
    """El delta que `lanzar_administrado` dejó en el entorno del wrapper. Lo saca de ahí para que
    el agente no lo herede."""
    crudo = os.environ.pop(ENTORNO_DEL_AGENTE, None)
    if crudo is None:
        raise RuntimeError(f"falta {ENTORNO_DEL_AGENTE} en el entorno del wrapper")
    return json.loads(crudo)


def _es_carpeta_de_shims(ruta: str) -> bool:
    return os.path.isfile(os.path.join(ruta or ".", "llamada_guard.py"))


def path_con_interprete(path: str, runtime: Path) -> str:
    """`python3` de la sesión es el intérprete que corre este supervisor, no el
    primero que halle el PATH (el 3.9 del sistema no trae `tomllib`). Se deja
    en `runtime/bin` un `python3` que lo ejecuta y esa carpeta va justo después
    de los shims del motor, que siguen al frente."""
    carpeta = runtime / "bin"
    carpeta.mkdir(parents=True, exist_ok=True)
    contenido = f'#!/bin/sh\nexec {shlex.quote(sys.executable)} "$@"\n'
    destino = carpeta / "python3"
    if not destino.is_file() or leer_texto(destino) != contenido:
        temporal = carpeta / ".python3.tmp"
        escribir_texto(temporal, contenido)
        temporal.chmod(0o755)
        os.replace(temporal, destino)
    partes = path.split(os.pathsep) if path else []
    n = 0
    while n < len(partes) and _es_carpeta_de_shims(partes[n]):
        n += 1
    partes.insert(n, str(carpeta))
    return os.pathsep.join(partes)


def carpeta_de_guardia(runtime: Path) -> Path:
    """El `gh` del agente: la guardia que niega integrar y sus lanzadores con el intérprete del
    supervisor, `gh` para sh y el bash de Git, `gh.cmd` para cmd. La guardia va copiada junto a
    ellos porque busca el `gh` real quitando su propia carpeta del PATH."""
    carpeta = runtime / "guardia"
    carpeta.mkdir(parents=True, exist_ok=True)
    python = Path(sys.executable)
    archivos = {
        "gh_guard.py": GUARDIA_GH.read_bytes(),
        "gh": (
            f'#!/bin/sh\nexec {shlex.quote(python.as_posix())} '
            '"$(dirname "$0")/gh_guard.py" "$@"\n'
        ).encode(),
        "gh.cmd": f'@echo off\r\n"{python}" "%~dp0gh_guard.py" %*\r\n'.encode(),
    }
    for nombre, contenido in archivos.items():
        temporal = carpeta / f".{nombre}.tmp"
        temporal.write_bytes(contenido)
        os.chmod(temporal, 0o755)
        os.replace(temporal, carpeta / nombre)
    return carpeta


def instalar_pre_push(espacio: Path, ramas: tuple[str, ...]) -> None:
    """Lo que el motor lanza para el agente empuja solo cada `rama` y `rama-<fase>`: la rama base
    la integra el motor después de la revisión. El hook actúa con `MOTOR_GUARDIA` en el entorno,
    así que no frena al motor ni a sus recetas de despliegue. Es la segunda red, detrás de la
    guardia de gh: `--no-verify` lo salta."""
    git = espacio / ".git"
    if not git.is_dir():
        return
    permitidas = " | ".join(
        f"{shlex.quote('refs/heads/' + r)} | {shlex.quote('refs/heads/' + r + '-')}*"
        for r in ramas
    )
    aviso = shlex.quote(
        f"solo empujas {', '.join(ramas)} y sus fases; la rama base la integra el motor "
        "después de la revisión independiente"
    )
    ganchos = git / "hooks"
    ganchos.mkdir(exist_ok=True)
    escribir_texto(
        ganchos / "pre-push",
        f"""#!/bin/sh
[ -n "${{{GUARDIA}:-}}" ] || exit 0
while read -r _ _ destino _; do
  case "$destino" in
    {permitidas}) ;;
    *) printf '%s: %s\\n' "$destino" {aviso} >&2; exit 1 ;;
  esac
done
""",
    )
    (ganchos / "pre-push").chmod(0o755)


def temporal_de(runtime: Path) -> Path:
    """Temporal propio de una corrida: vacío, dentro de su runtime."""
    temporal = runtime / "tmp"
    if temporal.is_symlink() or temporal.is_file():
        temporal.unlink(missing_ok=True)
    else:
        shutil.rmtree(temporal, ignore_errors=True)
    temporal.mkdir(parents=True, exist_ok=True)
    return temporal


def limpiar_temporal(runtime: Path) -> None:
    temporal = runtime / "tmp"
    if temporal.is_symlink() or temporal.is_file():
        temporal.unlink(missing_ok=True)
    else:
        shutil.rmtree(temporal, ignore_errors=True)


def _entorno(
    runtime: Path, suscripcion: bool, cmd: list[str], *, agente: str | None = None
) -> dict[str, str]:
    # El agente trabaja con el entorno del usuario, como él (decisión del
    # 2-nov). Solo se aparta lo que el motor necesita para darle a cada agente
    # su suscripción y su temporal, que no bloquea nada.
    env = dict(os.environ)
    if os.name == "nt":
        # El PATH conserva el intérprete del supervisor por delante.
        env["PATH"] = (
            str(Path(sys.executable).resolve().parent)
            + os.pathsep
            + env.get("PATH", "")
        )
    # Solo el motor integra, y después de la revisión independiente.
    guardia = carpeta_de_guardia(runtime)
    env["PATH"] = str(guardia) + os.pathsep + env.get("PATH", "")
    env[GUARDIA] = str(guardia)
    temporal = temporal_de(runtime)
    env.update(
        TMPDIR=str(temporal),
        TEMP=str(temporal),
        TMP=str(temporal),
        # El CLI de Claude no mira TMPDIR para el temporal de su shell: usa
        # CLAUDE_CODE_TMPDIR y, si falta, cae en /tmp. Ver incidente-mini-cuota-20261029.
        CLAUDE_CODE_TMPDIR=str(temporal),
        # OpenCode guarda en XDG_DATA_HOME el auth.json que el motor preparó
        # para este intento (suscripciones.preparar).
        XDG_DATA_HOME=str(runtime / ".local" / "share"),
        PYTHONDONTWRITEBYTECODE="1",
    )
    Path(env["XDG_DATA_HOME"]).mkdir(parents=True, exist_ok=True)
    if suscripcion:
        nombre = agente or Path(cmd[0]).stem.lower()
        if nombre in ("claude", "opencode", "qwen", "grok"):
            from .suscripciones import preparar

            preparar(nombre, runtime, cmd, env)
            return env
        if nombre == "muse":
            # Muse no tiene variable ni bandera propia para su carpeta de
            # cuenta: el binario nativo lee $XDG_CONFIG_HOME/muse/auth.json y,
            # sin esa variable, ~/.config/muse/auth.json. Se deja el entorno
            # real del usuario y el CLI usa su login, como cuando se corre a
            # mano. MUSE_AUTH_PATH solo la entiende el lanzador bash, que no es
            # el que el motor ejecuta.
            return env
        if nombre in ("antigravity", "agy"):
            # agy tampoco tiene variable ni bandera para su carpeta de sesión
            # (~/.gemini/antigravity-cli; en Windows, bajo %USERPROFILE%\.gemini).
            # --settings es un overlay de claves de settings.json que no se
            # guarda, y --log-file solo mueve el log. No se copia la sesión.
            # El prefijo común de arriba apunta XDG_DATA_HOME al runtime; aquí
            # se devuelve el valor del usuario. HOME y USERPROFILE no se tocan.
            previo = os.environ.get("XDG_DATA_HOME")
            if previo is None:
                env.pop("XDG_DATA_HOME", None)
            else:
                env["XDG_DATA_HOME"] = previo
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
            destino.mkdir(parents=True, exist_ok=True)
            env["CODEX_HOME"] = str(destino)
        else:
            return env
        auth = origen / "auth.json"
        if auth.is_file():
            if nombre == "codex":
                try:
                    datos = leer_json(auth)
                except (OSError, ValueError):
                    raise CuentaNoDisponible(
                        nombre, "no se pudo leer el login de suscripcion"
                    ) from None
                if datos.get("auth_mode") != "chatgpt" or datos.get("OPENAI_API_KEY"):
                    raise CuentaNoDisponible(
                        nombre,
                        "el encargo necesita autenticacion por suscripcion de ChatGPT",
                    )
            shutil.copyfile(auth, destino / "auth.json")
            (destino / "auth.json").chmod(0o600)
    return env


def _preparar_kimi(runtime: Path, cmd: list[str]) -> float:
    origen = Path(os.environ.get("KIMI_CODE_HOME", str(Path.home() / ".kimi-code")))
    try:
        config = tomllib.loads(leer_texto((origen / "config.toml")))
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
    escribir_texto((destino / "config.toml"), "\n".join(texto) + "\n")
    (destino / "config.toml").chmod(0o600)
    return acceso.expira


def _bash_de_git(entorno: dict[str, str]) -> str:
    """El bash de la instalación de Git que resuelve el PATH del agente. Si no
    está, devuelve la ruta habitual de la PC para Claude. La sonda de Windows
    usa Python y no comprueba este bash."""
    from motor.plataforma.windows import BASH_GIT_POR_OMISION

    git = shutil.which("git", path=entorno.get("PATH"))
    if git:
        raiz = Path(git).resolve().parent.parent
        if (raiz / "bin" / "bash.exe").is_file():
            return str(raiz / "bin" / "bash.exe")
    return BASH_GIT_POR_OMISION


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
    interprete: bool = False,
    ramas: tuple[str, ...] = (),
) -> ComandoAislado:
    espacio, runtime = espacio.resolve(), runtime.resolve()
    if espacio.parent.name != "espacio" or runtime.is_relative_to(espacio):
        raise RuntimeError("el lanzamiento necesita espacio concedido y runtime separado")
    runtime.mkdir(parents=True, exist_ok=True)
    runtime.chmod(0o700)
    if ramas:
        instalar_pre_push(espacio, ramas)
    nombre = agente or Path(cmd[0]).stem.lower()
    env = _entorno(runtime, suscripcion, cmd, agente=agente)
    if nombre in ("antigravity", "agy"):
        # El hook que niega las herramientas de espera y agendado vive en el
        # runtime del intento. `--add-dir` lo suma a los workspaces de agy.
        # Aqui no se crea nada en la configuracion real: solo se retira, si
        # existe, el motor-turno.md que dejo la version anterior.
        from .agentes import preparar_hooks_turno, retirar_agente_turno

        retirar_agente_turno()
        cmd = [*cmd, "--add-dir", str(preparar_hooks_turno(runtime))]
    if os.name == "nt":
        # Windows lanza directo, como POSIX: sin AppContainer ni WSL y con el
        # entorno del usuario. Solo se resuelven los shims .cmd de npm y se le
        # da a Claude el bash de Git que usa para sus órdenes.
        from motor.plataforma.windows import argv_windows

        argv = argv_windows(cmd, env)
        if not Path(argv[0]).is_absolute():
            ejecutable = shutil.which(argv[0], path=env.get("PATH"))
            if not ejecutable:
                raise FileNotFoundError(argv[0])
            argv = [str(Path(ejecutable).resolve()), *argv[1:]]
        if nombre == "claude":
            env["CLAUDE_CODE_GIT_BASH_PATH"] = _bash_de_git(env)
        procedencia = None
        if nombre == "muse":
            argv, procedencia = _muse_local(argv, runtime, env)
        return ComandoAislado(
            argv, env, MECANISMO_DIRECTO, ejecutable=procedencia
        )
    # macOS y Linux: lanzamiento directo, sin jaula (decisión del 2-nov). El
    # agente corre como el usuario, con su entorno. Se conservan el cambio a
    # muse nativo con su procedencia y el PATH con el intérprete del supervisor.
    if interprete:
        env["PATH"] = path_con_interprete(env.get("PATH", ""), runtime)
    procedencia = None
    if nombre == "muse":
        from .autenticacion_muse import ejecutable_muse

        nativo = ejecutable_muse()
        cmd = [str(nativo), *cmd[1:]]
        procedencia = {
            "origen": str(nativo),
            "ejecutable": str(nativo),
            "sha256": hashlib.sha256(nativo.read_bytes()).hexdigest(),
            "tipo": "instalacion-local-firmada",
        }
    return ComandoAislado(cmd, env, MECANISMO_DIRECTO, ejecutable=procedencia)


@dataclass(frozen=True)
class SondaEntorno:
    """Resultado de comprobar, bajo el lanzamiento real, lo que el trabajador
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

def _sonda_de(marca: str) -> SondaEntorno:
    clase, _, detalle = marca.partition(":")
    if clase == "temp-cli":
        return SondaEntorno(
            False,
            f"no se puede crear el temporal del CLI en {detalle}",
            "que el temporal del CLI caiga en una ruta escribible del lanzamiento",
        )
    if clase == "temp-shell":
        return SondaEntorno(
            False,
            f"no se puede escribir en el temporal del shell ({detalle})",
            "que el temporal del shell caiga en una ruta escribible del lanzamiento",
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
    return SondaEntorno(
        False,
        f"la sonda del entorno no terminó: {detalle or 'sin detalle'}",
        "que el lanzamiento directo del host funcione",
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


def sondear(
    espacio: Path,
    runtime: Path,
    lecturas: tuple[Path, ...] = (),
) -> SondaEntorno:
    """Comprueba con comandos locales, y sin llamar a ningún modelo, las
    operaciones que el trabajador necesita al arrancar: su temporal de shell,
    la escritura en el espacio y la lectura de las fuentes autorizadas. Corre
    con lanzamiento directo y el entorno del usuario. En Windows usa Python;
    en POSIX, /bin/sh. No comprueba el bash de Git que usa Claude.

    No acredita lo que no pudo ejecutar: con una fuente declarada que ya no
    está, el resultado es un fallo con su condición. Nunca propaga una
    excepción: la llama el supervisor, que en la PC arranca una vez desde una
    tarea programada y no se reinicia solo.
    """
    import uuid

    espacio, runtime = Path(espacio).resolve(), Path(runtime).resolve()
    fuentes = tuple(Path(p).expanduser().resolve() for p in lecturas)
    marca = uuid.uuid4().hex[:16]
    try:
        if os.name == "nt":
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
            f"no se pudo preparar el lanzamiento: {type(exc).__name__}: {exc}",
            "que el lanzamiento directo del host esté disponible",
        )
    try:
        entorno = dict(comando.entorno)
        entorno["MOTOR_SONDA_ESPACIO"] = str(espacio)
        entorno["MOTOR_SONDA_MARCA"] = marca
        hecho = subprocess.run(
            comando.argv,
            cwd=str(espacio),
            env=entorno,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=90,
        )
    except Exception as exc:  # noqa: BLE001  la sonda informa, no propaga
        return SondaEntorno(
            False,
            f"no se pudo ejecutar la sonda bajo el lanzamiento: {type(exc).__name__}: {exc}",
            "que el lanzamiento directo del host esté disponible",
        )
    return _resultado_de_sonda(hecho.returncode, hecho.stdout or "", hecho.stderr or "")


def _resultado_de_sonda(codigo: int, salida: str, error: str) -> SondaEntorno:
    lineas = [linea for linea in (salida or "").splitlines() if linea.strip()]
    if codigo == 0 and lineas and lineas[-1].strip() == "ok":
        return SondaEntorno(True)
    if not lineas:
        detalle = " ".join((error or "").split())[:300]
        return SondaEntorno(
            False,
            f"la sonda del entorno no terminó: codigo {codigo}"
            + (f": {detalle}" if detalle else ""),
            "que el lanzamiento directo del host funcione",
        )
    return _sonda_de(lineas[-1].strip())
