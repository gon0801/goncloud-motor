"""Fuentes oficiales y preparación aislada de los distribuidores soportados."""

from __future__ import annotations

import base64
import hashlib
import json
import os
import shutil
import subprocess
import urllib.parse
import urllib.request
from pathlib import Path

NPM = {
    "openclaw": "openclaw",
    "codex": "@openai/codex",
    "claude": "@anthropic-ai/claude-code",
    "grok": "@xai-official/grok",
    "qwen": "@qwen-code/qwen-code",
    "kimi": "@moonshot-ai/kimi-code",
    "opencode": "opencode-ai",
}
HERMES = "https://github.com/NousResearch/hermes-agent.git"
REGISTRY = "https://registry.npmjs.org/"


def _run(
    argv: list[str],
    *,
    cwd: Path | None = None,
    env: dict | None = None,
    entrada: str | None = None,
    timeout: int = 900,
) -> str:
    try:
        hecho = subprocess.run(
            argv,
            cwd=cwd,
            env=env,
            input=entrada,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
            timeout=timeout,
        )
    except OSError as exc:
        raise RuntimeError(f"no se pudo ejecutar {Path(argv[0]).name}: {exc}") from exc
    if hecho.returncode:
        # No copiar salida de instaladores o entornos que puedan contener credenciales.
        raise RuntimeError(f"fallo {Path(argv[0]).name} (salida {hecho.returncode})")
    return hecho.stdout.strip()


def _json_url(url: str) -> dict:
    request = urllib.request.Request(
        url, headers={"User-Agent": "goncloud-flota", "Accept": "application/json"}
    )
    with urllib.request.urlopen(request, timeout=45) as respuesta:
        return json.load(respuesta)


def _git(
    repo: Path, *args: str, env: dict | None = None, entrada: str | None = None
) -> str:
    return _run(["git", "-C", str(repo), *args], env=env, entrada=entrada)


def descubrir(componentes: tuple[str, ...] | list[str] | None = None) -> dict:
    seleccion = list(componentes) if componentes is not None else [*NPM, "hermes"]
    desconocidos = set(seleccion) - {*NPM, "hermes"}
    if desconocidos:
        raise ValueError("componentes desconocidos: " + ", ".join(sorted(desconocidos)))
    encontrados, ausentes = [], []
    for nombre in seleccion:
        try:
            if nombre in NPM:
                paquete = NPM[nombre]
                fuente = REGISTRY + urllib.parse.quote(paquete, safe="") + "/latest"
                doc = _json_url(fuente)
                dist = doc["dist"]
                componente = {
                    "nombre": nombre,
                    "tipo": "npm",
                    "paquete": paquete,
                    "version": doc["version"],
                    "fuente": fuente,
                    "tarball": dist["tarball"],
                    "integridad": dist["integrity"],
                }
            else:
                fuente = "https://api.github.com/repos/NousResearch/hermes-agent/releases/latest"
                version = _json_url(fuente)["tag_name"]
                refs = _run(
                    [
                        "git",
                        "ls-remote",
                        HERMES,
                        "refs/tags/" + version,
                        "refs/tags/" + version + "^{}",
                    ]
                )
                revisiones = [
                    line.split()[0] for line in refs.splitlines() if line.strip()
                ]
                if not revisiones:
                    raise ValueError("la version oficial no tiene referencia Git")
                revision = revisiones[-1]
                componente = {
                    "nombre": nombre,
                    "tipo": "git",
                    "repositorio": HERMES,
                    "version": version,
                    "revision": revision,
                    "integridad": "git:" + revision,
                    "fuente": fuente,
                }
            _validar_componente(componente)
            encontrados.append(componente)
        except (
            OSError,
            ValueError,
            KeyError,
            RuntimeError,
            subprocess.SubprocessError,
        ):
            ausentes.append(
                {
                    "nombre": nombre,
                    "estado": "no_disponible",
                    "motivo": "no se pudo comprobar el canal oficial",
                }
            )
    return {"componentes": encontrados, "no_disponibles": ausentes}


def _validar_componente(doc: dict) -> None:
    nombre = doc.get("nombre")
    if nombre in NPM:
        if doc.get("tipo") != "npm" or doc.get("paquete") != NPM[nombre]:
            raise ValueError("paquete fuera del registro de componentes")
        fuente = REGISTRY + urllib.parse.quote(NPM[nombre], safe="")
        if not str(doc.get("fuente", "")).startswith(fuente + "/"):
            raise ValueError("fuente npm no oficial")
        url = urllib.parse.urlsplit(str(doc.get("tarball", "")))
        if (
            url.scheme != "https"
            or url.hostname != "registry.npmjs.org"
            or url.username
        ):
            raise ValueError("el artefacto no pertenece al registro npm oficial")
        if not urllib.parse.unquote(url.path).startswith("/" + NPM[nombre] + "/-/"):
            raise ValueError("el artefacto no pertenece al paquete oficial declarado")
        algoritmo, _, digest = str(doc.get("integridad", "")).partition("-")
        if algoritmo not in ("sha512", "sha256") or not digest:
            raise ValueError("falta integridad SRI del distribuidor")
        base64.b64decode(digest, validate=True)
    elif nombre == "hermes":
        revision = str(doc.get("revision", ""))
        if doc.get("tipo") != "git" or doc.get("repositorio") != HERMES:
            raise ValueError("repositorio Hermes no oficial")
        if len(revision) != 40 or any(c not in "0123456789abcdef" for c in revision):
            raise ValueError("revision Git invalida")
        if doc.get("integridad") != "git:" + revision:
            raise ValueError("integridad Git no coincide")
    else:
        raise ValueError("componente no admitido")
    version = doc.get("version")
    if not isinstance(version, str) or not version or any(c.isspace() for c in version):
        raise ValueError("version invalida")


def _descargar(doc: dict, cache: Path) -> Path:
    algoritmo, digest = doc["integridad"].split("-", 1)
    esperado = base64.b64decode(digest, validate=True)
    artefacto = cache / (
        hashlib.sha256(doc["integridad"].encode()).hexdigest() + ".tgz"
    )
    cache.mkdir(parents=True, exist_ok=True)
    if not artefacto.is_file():
        temporal = artefacto.with_suffix(".descarga")
        try:
            with (
                urllib.request.urlopen(doc["tarball"], timeout=90) as respuesta,
                temporal.open("wb") as salida,
            ):
                shutil.copyfileobj(respuesta, salida)
            temporal.replace(artefacto)
        finally:
            temporal.unlink(missing_ok=True)
    huella = hashlib.new(algoritmo)
    with artefacto.open("rb") as entrada:
        for bloque in iter(lambda: entrada.read(1024 * 1024), b""):
            huella.update(bloque)
    if huella.digest() != esperado:
        artefacto.unlink()
        raise ValueError("el artefacto npm no coincide con su integridad oficial")
    return artefacto


def _npm() -> list[str]:
    node, npm = shutil.which("node"), shutil.which("npm")
    if not node or not npm:
        raise RuntimeError("Node y npm no estan disponibles")
    ruta = Path(npm).resolve()
    candidatos = [
        ruta,
        ruta.parent / "node_modules/npm/bin/npm-cli.js",
        Path(node).parent / "node_modules/npm/bin/npm-cli.js",
    ]
    for candidato in candidatos:
        if candidato.name == "npm-cli.js" and candidato.is_file():
            return [node, str(candidato)]
        if candidato.is_file() and candidato.suffix == ".js":
            return [node, str(candidato)]
    if os.name != "nt":
        return [npm]
    raise RuntimeError(
        "no se encontro npm-cli.js junto a Node; no se ejecutan shims cmd"
    )


def _instalar_npm(doc: dict, artefacto: Path, destino: Path, cache: Path) -> list[str]:
    destino.mkdir(parents=True, exist_ok=True)
    _run(
        [
            *_npm(),
            "install",
            "--prefix",
            str(destino),
            "--cache",
            str(cache),
            "--no-audit",
            "--no-fund",
            "--package-lock=false",
            "--registry=" + REGISTRY,
            str(artefacto),
        ],
        env=_entorno_generacion(destino.parents[1]),
    )
    paquete = destino / "node_modules" / doc["paquete"]
    metadata = json.loads((paquete / "package.json").read_text(encoding="utf-8"))
    if (
        metadata.get("name") != doc["paquete"]
        or metadata.get("version") != doc["version"]
    ):
        raise ValueError("la instalacion npm no coincide con la version preparada")
    if doc["nombre"] == "codex":
        ejecutable = "codex.exe" if os.name == "nt" else "codex"
        candidatos = list(
            (destino / "node_modules/@openai").glob(
                f"codex-*/vendor/*/bin/{ejecutable}"
            )
        )
        if len(candidatos) != 1:
            raise RuntimeError(
                "el paquete Codex no contiene un unico ejecutable nativo compatible"
            )
        return [str(candidatos[0])]
    binarios = metadata.get("bin")
    relativo = (
        binarios if isinstance(binarios, str) else (binarios or {}).get(doc["nombre"])
    )
    if not relativo:
        raise ValueError("el paquete no declara su ejecutable")
    ejecutable = (paquete / relativo).resolve()
    if (
        destino / "node_modules"
    ).resolve() not in ejecutable.parents or not ejecutable.is_file():
        raise ValueError("ejecutable npm fuera del prefijo")
    with ejecutable.open("rb") as entrada:
        cabecera = entrada.read(80)
    if ejecutable.suffix in (".js", ".mjs", ".cjs") or (
        cabecera.startswith(b"#!") and b"node" in cabecera
    ):
        return [str(Path(shutil.which("node")).resolve()), str(ejecutable)]
    return [str(ejecutable)]


def _runtime_hermes(destino: Path) -> dict:
    codigo, store = destino / "src", destino / "tools"
    facts = json.loads((store / "facts.json").read_text(encoding="utf-8"))
    entry = (store / facts["packages"]["python"]["entry"]).resolve()
    if store.resolve() not in entry.parents:
        raise ValueError("Python de PM fuera de su almacen")
    bootstrap = entry / ("python.exe" if os.name == "nt" else "bin/python3")
    consulta = (
        "import json; from pathlib import Path; "
        "from pm.environments import committed_venv,venv_command,site_packages; "
        "p=Path.cwd(); v=committed_venv(p); assert v is not None; "
        "print(json.dumps({'python':venv_command(p,v),'site_packages':str(site_packages(v))}))"
    )
    env = _entorno_generacion(destino.parents[1])
    env["PYTHONPATH"] = str(codigo)
    contrato = json.loads(
        _run([str(bootstrap), "-c", consulta], cwd=codigo, env=env, timeout=60)
    )
    launcher = codigo / ".hermes/bin" / ("hermes.exe" if os.name == "nt" else "hermes")
    if not Path(contrato["python"][0]).is_file():
        raise RuntimeError("PM no publico un entorno completo")
    for path in [*contrato["python"], contrato["site_packages"]]:
        candidato = Path(path)
        if (
            candidato.is_absolute()
            and destino.parents[1].resolve() not in candidato.resolve().parents
        ):
            raise ValueError("el entorno Hermes apunta fuera de la generacion")
    argv = [*contrato["python"], "-m", "hermes_cli.main"]
    env["PYTHONPATH"] = str(codigo)
    version = _run([*argv, "--version"], env=env, timeout=60)
    observacion = {"ruta": str(launcher), "estado": "ausente"}
    if launcher.is_file():
        try:
            _run([str(launcher), "--version"], env=env, timeout=60)
            observacion["estado"] = "comprobado"
        except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
            causa = exc.__cause__ if isinstance(exc.__cause__, OSError) else exc
            observacion.update({"estado": "no_disponible", "motivo": str(exc)[:300]})
            if getattr(causa, "winerror", None) == 4551:
                observacion.update(
                    {
                        "estado": "bloqueado",
                        "winerror": 4551,
                        "motivo": "AppControl no permite el lanzador exe; se usa la entrada oficial PM comprobada",
                    }
                )
    return {
        "argv": argv,
        "python": contrato["python"],
        "version": version,
        "site_packages": contrato["site_packages"],
        "src": str(codigo),
        "launcher": observacion,
    }


def _instalar_hermes(doc: dict, destino: Path) -> dict:
    codigo = destino / "src"
    if not codigo.exists():
        _run(
            ["git", "clone", "--no-checkout", "--filter=blob:none", HERMES, str(codigo)]
        )
        _git(codigo, "checkout", "--detach", doc["revision"])
    if _git(codigo, "rev-parse", "HEAD") != doc["revision"]:
        raise ValueError("la revision Hermes preparada no coincide")
    try:
        return _runtime_hermes(destino)
    except (OSError, ValueError, KeyError, RuntimeError, subprocess.SubprocessError):
        # El instalador oficial recupera su propio journal y cache parcial.
        pass
    env = _entorno_generacion(destino.parents[1])
    env["HERMES_RUNTIME_DIR"] = str(destino / "tools")
    if os.name == "nt":
        argv = [
            "powershell.exe",
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(codigo / "setup-hermes.ps1"),
            "-RuntimeOnly",
        ]
    else:
        argv = ["bash", str(codigo / "setup-hermes.sh"), "--runtime-only"]
    _run(argv, cwd=codigo, env=env, timeout=1800)
    return _runtime_hermes(destino)


def _entorno_generacion(gen: Path) -> dict:
    datos = gen / "datos"
    rutas = {
        "HOME": datos / "home",
        "USERPROFILE": datos / "home",
        "APPDATA": datos / "appdata",
        "LOCALAPPDATA": datos / "localappdata",
        "XDG_CONFIG_HOME": datos / "config",
        "XDG_DATA_HOME": datos / "data",
        "XDG_CACHE_HOME": datos / "cache",
        "TEMP": datos / "tmp",
        "TMP": datos / "tmp",
        "UV_CACHE_DIR": datos / "uv-cache",
        "UV_INSTALL_DIR": datos / "uv-bin",
        "UV_TOOL_DIR": datos / "uv-tools",
        "UV_PYTHON_INSTALL_DIR": datos / "uv-python",
        "HERMES_HOME": gen / "perfiles/hermes",
        "HERMES_PROFILES_DIR": gen / "perfiles/hermes/profiles",
        "HERMES_RUNTIME_DIR": gen / "herramientas/hermes/tools",
        "OPENCLAW_STATE_DIR": gen / "perfiles/claw",
    }
    for ruta in rutas.values():
        ruta.mkdir(parents=True, exist_ok=True)
    env = {
        **os.environ,
        **{nombre: str(ruta) for nombre, ruta in rutas.items()},
        "OPENCLAW_CONFIG_PATH": str(gen / "perfiles/claw/openclaw.json"),
        "GONCLOUD_MUSE_LOGIN": str(datos / "sin-credencial-muse.json"),
        "OPENCLAW_CONFIG_READONLY": "1",
    }
    for nombre in ("PYTHONPATH", "PYTHONHOME", "VIRTUAL_ENV"):
        env.pop(nombre, None)
    return env
