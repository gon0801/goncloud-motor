"""Prepara y publica generaciones de herramientas y configuración compartida."""

from __future__ import annotations
from motor.io import escribir_texto, leer_json

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
import urllib.parse
from dataclasses import replace
from pathlib import Path

from .cerrojo import _cerrojo_flota
from .control import _atomico, _operacion_flota, _trabajos_sobre, aplicar, asignar
from .distribuidores import (
    _descargar,
    _entorno_generacion,
    _git,
    _instalar_hermes,
    _instalar_npm,
    _json_url,
    _run,
    _validar_componente,
    descubrir,
)
from .manifiesto import Herramienta, cargar, emitir, guardar, proyectar_tabla

__all__ = ["actualizar", "comprobar_runtime", "descubrir", "estado", "preparar"]


def _repos_predeterminados(motor_origen: Path) -> dict[str, Path]:
    padre = motor_origen.parent
    candidatos = {}
    for nombre, base in (
        ("hermes", "goncloud-hermes"),
        ("claw", "goncloud-openclaw"),
        ("conf", "goncloud-motor-conf"),
    ):
        opciones = [padre / (base + "-encargos-3-4"), padre / base]
        encontrado = next((p for p in opciones if (p / ".git").exists()), None)
        if encontrado:
            candidatos[nombre] = encontrado
    return candidatos


def _snapshot_repos(repos: dict[str, Path]) -> dict:
    return {
        nombre: {
            "origen": str(Path(ruta).resolve()),
            "revision": _git(Path(ruta), "rev-parse", "HEAD"),
        }
        for nombre, ruta in repos.items()
    }


def _crear_generacion(
    home: Path, motor_origen: Path, componentes: list[dict], repos: dict, politica: str
) -> tuple[str, Path]:
    motor = home / "motor"
    if not motor.exists():
        _run(["git", "clone", "--no-hardlinks", str(motor_origen), str(motor)])
    base = _git(motor_origen, "rev-parse", "HEAD")
    _git(motor, "fetch", str(motor_origen), base)
    snapshot = {"componentes": componentes, "repos": repos, "politica": politica}
    contenido = (
        json.dumps(snapshot, sort_keys=True, ensure_ascii=False, indent=2) + "\n"
    )
    with tempfile.TemporaryDirectory(prefix="flota-indice-", dir=str(home)) as temporal:
        env = {
            **os.environ,
            "GIT_INDEX_FILE": str(Path(temporal) / "index"),
            "GIT_AUTHOR_NAME": "Motor de encargos",
            "GIT_AUTHOR_EMAIL": "motor@localhost",
            "GIT_COMMITTER_NAME": "Motor de encargos",
            "GIT_COMMITTER_EMAIL": "motor@localhost",
            "GIT_AUTHOR_DATE": "2000-01-01T00:00:00Z",
            "GIT_COMMITTER_DATE": "2000-01-01T00:00:00Z",
        }
        _git(motor, "read-tree", base, env=env)
        blob = _git(motor, "hash-object", "-w", "--stdin", env=env, entrada=contenido)
        _git(
            motor,
            "update-index",
            "--add",
            "--cacheinfo",
            "100644," + blob + ",flota-runtime.json",
            env=env,
        )
        blob = _git(motor, "hash-object", "-w", "--stdin", env=env, entrada=politica)
        _git(
            motor,
            "update-index",
            "--add",
            "--cacheinfo",
            "100644," + blob + ",manifesto.toml",
            env=env,
        )
        arbol = _git(motor, "write-tree", env=env)
        revision = _git(
            motor,
            "commit-tree",
            arbol,
            "-p",
            base,
            env=env,
            entrada="Candidata de componentes\n",
        )
    _git(motor, "update-ref", "refs/flota/" + revision, revision)
    gen = home / "generaciones" / revision
    if not gen.exists():
        gen.parent.mkdir(exist_ok=True)
        _git(motor, "worktree", "add", "--detach", str(gen), revision)
    return revision, gen


def _utilidad_configuracion(gen: Path, contrato: dict) -> dict:
    env = _entorno_generacion(gen)
    util = {
        "argv": [sys.executable],
        "pythonpath": [contrato["src"], contrato["site_packages"]],
    }
    try:
        version = _run(
            [sys.executable, "-c", "import yaml; print(yaml.__version__)"],
            env=env,
            timeout=30,
        )
        util["pyyaml"] = {"version": version, "origen": "interprete del controlador"}
    except (OSError, RuntimeError, subprocess.SubprocessError):
        fuente = "https://pypi.org/pypi/PyYAML/json"
        metadata = _json_url(fuente)
        version = metadata["info"]["version"]
        if not version or any(c.isspace() for c in version):
            raise ValueError("version PyYAML invalida")
        hashes = sorted(
            {f["digests"]["sha256"] for f in metadata["urls"] if not f.get("yanked")}
        )
        if not hashes or any(
            len(h) != 64 or any(c not in "0123456789abcdef" for c in h) for h in hashes
        ):
            raise ValueError("PyPI no publico integridad para PyYAML")
        uv = shutil.which("uv")
        if not uv:
            raise RuntimeError(
                "el configurador requiere PyYAML o uv para su utilidad aislada"
            )
        destino = gen / "herramientas/config-utils"
        destino.mkdir(parents=True, exist_ok=True)
        requisitos = destino / "requirements.txt"
        escribir_texto(requisitos, "PyYAML=="
            + version
            + " "
            + " ".join("--hash=sha256:" + h for h in hashes)
            + "\n")
        _run(
            [
                uv,
                "pip",
                "install",
                "--python",
                sys.executable,
                "--target",
                str(destino),
                "--index-url",
                "https://pypi.org/simple",
                "--require-hashes",
                "-r",
                str(requisitos),
            ],
            env=env,
        )
        util["pythonpath"].append(str(destino))
        util["pyyaml"] = {
            "version": version,
            "fuente": fuente,
            "sha256_distribuciones": hashes,
        }
    util["pythonpath"] = [str(Path(p).relative_to(gen)) for p in util["pythonpath"]]
    return util


def _entorno_configuracion(gen: Path, runtime: dict) -> dict:
    env = _entorno_generacion(gen)
    env["PYTHONPATH"] = os.pathsep.join(
        str(gen / p) for p in runtime["configuradores"]["hermes"]["pythonpath"]
    )
    return env


def _plugins_hermes(
    config: Path, home: Path, python: list[str], instalacion: Path, env: dict
) -> None:
    manifiesto = leer_json((config / "director-plugins.json"))
    director = manifiesto["director"]
    if Path(director).name != director:
        raise ValueError("nombre del director invalido")
    bootstrap = """
import runpy, sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from hermes_cli.profiles import create_profile, get_profile_dir
from hermes_cli.default_soul import DEFAULT_SOUL_MD


def leer_texto(ruta):
    # Este programa viaja a otro intérprete con python -c: solo stdlib.
    with open(ruta, 'r', encoding='utf-8') as archivo:
        return archivo.read()


def escribir_texto(ruta, texto):
    with open(ruta, 'w', encoding='utf-8', newline='\\n') as archivo:
        archivo.write(texto)
config, director = Path(sys.argv[3]), sys.argv[2]
poteto = runpy.run_path(str(config / "plugins/goncloud-team/poteto.py"))
soul = "\\n\\n".join(leer_texto((config / name)) for name in
                     ("shared/CONTRACT.md", "shared/BUSINESS.md", "roles/" + director + ".md")) + "\\n\\n" + poteto["bloque"]()
profile = get_profile_dir(director)
if not profile.exists():
    create_profile(director, no_alias=True, description="Director de ensayo", soul=soul)
else:
    target = profile / "SOUL.md"
    if target.is_file() and leer_texto(target) == DEFAULT_SOUL_MD:
        escribir_texto(target, soul)
"""
    _run([*python, "-c", bootstrap, str(instalacion), director, str(config)], env=env)
    for plugin in manifiesto["plugins"]:
        nombre, revision, repo = plugin["name"], plugin["sha"], plugin["repo"]
        url = urllib.parse.urlsplit(repo)
        if (
            Path(nombre).name != nombre
            or url.scheme != "https"
            or url.hostname != "github.com"
            or url.username
        ):
            raise ValueError("origen del plugin no admitido")
        if len(revision) != 40 or any(c not in "0123456789abcdef" for c in revision):
            raise ValueError("revision de plugin invalida")
        destino = home / "profiles" / director / "plugins" / nombre
        if not destino.exists():
            _run(["git", "clone", "--no-checkout", repo, str(destino)])
            _git(destino, "checkout", "--detach", revision)
        if _git(destino, "rev-parse", "HEAD") != revision:
            raise ValueError("el plugin no coincide con la revision fijada")


def _copiar_config(gen: Path, repos: dict, runtime: dict, vigente: Path | None = None) -> None:
    permitidos = {
        "hermes": [
            "scripts",
            "plugins",
            "shared",
            "skills",
            "roles",
            "team.json",
            "director-plugins.json",
            "model-routing.source.json",
            "claw-routing.json",
            "pinned-files.json",
            "patches",
        ],
        "claw": ["agents", "scripts/agent-work/contexto", "docs/superpowers/specs"],
        "conf": ["flota"],
    }
    # Una actualización parcial que no encuentra los repos reutiliza la
    # configuración exportada de la generación vigente en vez de omitirla:
    # el paquete completo es condición de la candidata.
    for nombre in permitidos:
        if nombre in repos or vigente is None:
            continue
        origen = Path(vigente) / "config" / nombre
        destino = gen / "config" / nombre
        if not origen.is_dir() or destino.exists():
            continue
        destino.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(origen, destino)
    for nombre, spec in repos.items():
        if nombre not in permitidos:
            raise ValueError("repositorio de configuracion desconocido")
        destino = gen / "config" / nombre
        repo = Path(spec["origen"])
        archivos = _git(
            repo,
            "ls-tree",
            "--name-only",
            spec["revision"],
            "--",
            *permitidos[nombre],
        ).splitlines()
        destino.mkdir(parents=True, exist_ok=True)
        if archivos:
            with tempfile.TemporaryDirectory(
                prefix="flota-config-", dir=str(gen)
            ) as tmp:
                paquete = Path(tmp) / "config.tar"
                _git(
                    repo,
                    "archive",
                    "--format=tar",
                    "--output=" + str(paquete),
                    spec["revision"],
                    "--",
                    *archivos,
                )
                with tarfile.open(paquete) as entrada:
                    entrada.extractall(destino, filter="data")
        _atomico(destino / "source-revision.json", {"revision": spec["revision"]})
    hermes = gen / "config/hermes"
    runtime["config"] = {}
    if hermes.is_dir():
        paquete = hermes / "shared/sistema-agentes/poteto"
        if not (paquete / "poteto.v1.json").is_file():
            raise ValueError("el paquete poteto completo no esta disponible")
        _run(
            [
                sys.executable,
                "-c",
                "import runpy,sys; p=runpy.run_path(sys.argv[1]); p['comprobar_copia'](sys.argv[2])",
                str(hermes / "plugins/goncloud-team/poteto.py"),
                str(paquete),
            ],
            env=_entorno_generacion(gen),
        )
        runtime["poteto"] = str(paquete.relative_to(gen))
        tabla = hermes / "model-routing.source.json"
        proyeccion = leer_json(tabla)
        proyeccion.update(proyectar_tabla(cargar(gen / "manifesto.toml")))
        _atomico(tabla, proyeccion)
        runtime["politica"] = str(tabla.relative_to(gen))
        if "hermes" in runtime["lanzadores"]:
            python = _argv_real(gen, runtime["configuradores"]["hermes"]["argv"])
            script = hermes / "scripts/configure_team.py"
            home_hermes = gen / "perfiles/hermes"
            args = [
                *python,
                str(script),
                "--home",
                str(home_hermes),
                "--install-dir",
                str(gen / "herramientas/hermes/src"),
                "--preserve-local-soul",
            ]
            _plugins_hermes(
                hermes,
                home_hermes,
                python,
                gen / "herramientas/hermes/src",
                _entorno_configuracion(gen, runtime),
            )
            _run(args, env=_entorno_configuracion(gen, runtime))
            _run([*args, "--check"], env=_entorno_configuracion(gen, runtime))
            runtime["config"]["hermes"] = str(home_hermes.relative_to(gen))
        if "openclaw" in runtime["lanzadores"]:
            destino = _escribir_claw_desde_tabla(gen, tabla)
            runtime["config"]["claw"] = str(
                (destino / "openclaw.json").relative_to(gen)
            )
            _comprobar_claw(gen, runtime)
            runtime["claw_modelos"] = str((destino / "modelos.json").relative_to(gen))


_ESTADO_OPERATIVO = frozenset(
    {
        "cache",
        "installs",
        "logs",
        "sessions",
        "image_cache",
        "backups",
        "__pycache__",
        ".git",
    }
)


def _causa_pieza(doc: dict, exc: BaseException, componentes: list) -> str:
    from .distribuidores import _pista_de_fallo

    detalle = str(exc)
    pista = _pista_de_fallo(detalle)
    if pista and pista not in detalle:
        detalle = f"{detalle}; {pista}"
    anterior = next(
        (c for c in componentes if c.get("nombre") == doc.get("nombre")), None
    )
    if anterior is None:
        return (
            f"no se pudo preparar {doc.get('nombre')}: {detalle}. "
            "Se conserva la instalacion vigente."
        )
    if anterior.get("version") == doc.get("version") and anterior.get(
        "integridad"
    ) == doc.get("integridad"):
        return (
            f"no se pudo preparar {doc['nombre']} {doc.get('version')}: {detalle}. "
            "No se encontro la instalacion vigente de esa misma pieza para reutilizar. "
            "Se conserva la instalacion vigente."
        )
    return (
        f"la candidata no pudo preparar {doc['nombre']} {doc.get('version')}: {detalle}. "
        f"La vigente tiene {anterior.get('version')} y no es la misma pieza; "
        "se conserva la instalacion vigente."
    )


def _reutilizar_pieza(
    doc: dict,
    destino: Path,
    gen: Path,
    vigente: Path | None,
    runtime_vigente: dict,
) -> tuple[list[str], str, None] | None:
    if vigente is None:
        return None
    misma = next(
        (
            c
            for c in runtime_vigente.get("componentes") or []
            if c.get("nombre") == doc.get("nombre")
            and c.get("version") == doc.get("version")
            and c.get("integridad") == doc.get("integridad")
        ),
        None,
    )
    origen = vigente / "herramientas" / str(doc.get("nombre"))
    argv = list((runtime_vigente.get("lanzadores") or {}).get(doc.get("nombre")) or [])
    if misma is None or not argv or not origen.is_dir():
        return None
    shutil.copytree(origen, destino, symlinks=True)
    prefijos = _prefijos_de(vigente)
    nuevo = str(gen.resolve())
    _copiar_soporte_lanzador(gen, vigente, argv, prefijos, nuevo)
    # La pieza copiada se contrasta con los hashes que la vigente registró al
    # preparar: sin eso, una pieza alterada en la vigente entraría a la
    # candidata como si fuera la de catálogo. Si no coincide, no se reutiliza.
    # Las claves se registraron con separadores nativos (str(relative_to)),
    # así que el prefijo se compara por partes y no por texto con '/'.
    nombre = doc.get("nombre")
    for clave, huella in (runtime_vigente.get("archivos") or {}).items():
        partes = Path(str(clave)).parts
        if len(partes) < 3 or partes[0] != "herramientas" or partes[1] != nombre:
            continue
        ruta = gen / str(clave)
        if not ruta.is_file() or _sha256(ruta) != huella:
            shutil.rmtree(destino, ignore_errors=True)
            return None
    traducido = [_traducir_ruta(str(arg), prefijos, nuevo) for arg in argv]
    version = str(
        ((runtime_vigente.get("observaciones") or {}).get(doc["nombre"]) or {}).get(
            "version"
        )
        or doc.get("version")
        or ""
    )
    return traducido, version, None


def _prefijos_de(vigente: Path) -> list[str]:
    vistos = []
    for candidato in (vigente.resolve(), vigente):
        texto = str(candidato)
        if texto not in vistos:
            vistos.append(texto)
    return sorted(vistos, key=len, reverse=True)


def _traducir_ruta(texto: str, prefijos: list[str], nuevo: str) -> str:
    for viejo in prefijos:
        if texto.startswith(viejo):
            return nuevo + texto[len(viejo) :]
    return texto


def _relativo_a_vigente(texto: str, vigente: Path) -> Path | None:
    ruta = Path(texto)
    if not ruta.is_absolute():
        return ruta
    for base in (vigente, vigente.resolve()):
        try:
            return ruta.relative_to(base)
        except ValueError:
            continue
    return None


def _directorio_node(rel: Path) -> Path | None:
    """herramientas/node/<huella>/node.exe, el Node que Windows deja en la generación."""
    partes = rel.parts
    if len(partes) < 3 or partes[0] != "herramientas" or partes[1] != "node":
        return None
    huella = partes[2]
    if huella in (".", ".."):
        return None
    return Path(*partes[:3])


def _raiz_de_soporte(rel: Path) -> Path:
    partes = rel.parts
    if "venv" in partes:
        return Path(*partes[: partes.index("venv") + 1])
    corte = next(i for i, parte in enumerate(partes) if parte in _ESTADO_OPERATIVO)
    return Path(*partes[: corte + 1])


def _copiar_binario(origen: Path, destino: Path) -> None:
    """Copia sin reescribir bytes. node.exe no es texto de configuración."""
    if destino.exists() or destino.is_symlink():
        return
    if not origen.exists() and not origen.is_symlink():
        return
    destino.parent.mkdir(parents=True, exist_ok=True)
    if origen.is_dir() and not origen.is_symlink():
        shutil.copytree(origen, destino, symlinks=True)
        return
    shutil.copy2(origen, destino, follow_symlinks=False)


def _copiar_entrada(
    origen: Path, destino: Path, prefijos: list[str], nuevo: str
) -> None:
    if origen.is_symlink():
        destino.parent.mkdir(parents=True, exist_ok=True)
        if destino.is_symlink() or destino.exists():
            return
        destino.symlink_to(_traducir_ruta(os.readlink(origen), prefijos, nuevo))
        return
    if not origen.exists():
        return
    if origen.is_dir():
        destino.mkdir(parents=True, exist_ok=True)
        for item in origen.iterdir():
            _copiar_entrada(item, destino / item.name, prefijos, nuevo)
        return
    if destino.exists():
        return
    _copiar_arbol(origen, destino, prefijos, nuevo)


def _copiar_soporte_lanzador(
    gen: Path,
    vigente: Path,
    argv: list[str],
    prefijos: list[str],
    nuevo: str,
) -> None:
    """Copia el entorno que el lanzador reutilizado necesita.

    El árbol de perfiles omite cache e installs. El python de Hermes vive en
    un venv bajo installs y apunta al intérprete de herramientas/. Sin esa
    copia, reutilizar la pieza deja un lanzador que no existe.
    """
    vistos: set[Path] = set()
    for arg in argv:
        rel = _relativo_a_vigente(str(arg), vigente)
        if rel is None:
            continue
        node = _directorio_node(rel)
        if node is not None:
            if node not in vistos:
                vistos.add(node)
                _copiar_binario(vigente / node, gen / node)
            continue
        if not any(parte in _ESTADO_OPERATIVO for parte in rel.parts):
            continue
        raiz = _raiz_de_soporte(rel)
        if raiz in vistos:
            continue
        vistos.add(raiz)
        _copiar_entrada(vigente / raiz, gen / raiz, prefijos, nuevo)


def _reemplazar_rutas(data: bytes, prefijos: list[str], nuevo: str) -> bytes:
    for viejo in prefijos:
        # json.dumps escapa cada barra invertida. El texto plano trae una sola.
        formas = [(viejo, nuevo)]
        if "\\" in viejo or "\\" in nuevo:
            formas.insert(
                0, (viejo.replace("\\", "\\\\"), nuevo.replace("\\", "\\\\"))
            )
        for origen, destino in formas:
            data = data.replace(origen.encode(), destino.encode())
    return data


def _copiar_arbol(origen: Path, destino: Path, prefijos: list[str], nuevo: str) -> None:
    if origen.is_symlink():
        destino.parent.mkdir(parents=True, exist_ok=True)
        if not (destino.is_symlink() or destino.exists()):
            destino.symlink_to(
                _traducir_ruta(os.readlink(origen), prefijos, nuevo),
                target_is_directory=origen.is_dir(),
            )
        return
    if not origen.exists():
        return
    if origen.is_dir():
        destino.mkdir(parents=True, exist_ok=True)
        for item in origen.iterdir():
            if item.name in _ESTADO_OPERATIVO:
                continue
            _copiar_arbol(item, destino / item.name, prefijos, nuevo)
        shutil.copymode(origen, destino)
        return
    destino.parent.mkdir(parents=True, exist_ok=True)
    destino.write_bytes(_reemplazar_rutas(origen.read_bytes(), prefijos, nuevo))
    shutil.copymode(origen, destino)


def _escribir_claw_desde_tabla(gen: Path, tabla: Path) -> Path:
    """modelos.json y openclaw.json salen de la tabla, con el claw_models exportado.

    La publicadora y la réplica usan el mismo script. No es poteto ni un checkout.
    """
    script = gen / "config/hermes/scripts/claw_models.py"
    destino = gen / "perfiles/claw"
    destino.mkdir(parents=True, exist_ok=True)
    salida = _run(
        [sys.executable, str(script), "--source", str(tabla), "desired"]
    )
    configuracion = json.loads(salida)
    configuracion["agents"]["ownership"] = "explicit"
    _atomico(destino / "modelos.json", configuracion)
    for nombre, entry in configuracion["agents"]["entries"].items():
        entry["workspace"] = str(destino / "workspaces" / nombre)
        entry["agentDir"] = str(destino / "agents" / nombre / "agent")
    configuracion.update(
        {
            "channels": {},
            "bindings": [],
            "cron": {"enabled": False},
            "hooks": {},
        }
    )
    _atomico(destino / "openclaw.json", configuracion)
    _run(
        [
            sys.executable,
            str(script),
            "--source",
            str(tabla),
            "check",
            "--live",
            str(destino / "modelos.json"),
        ]
    )
    return destino


def _reutilizar_config(gen: Path, vigente: Path, runtime: dict) -> None:
    if not (vigente / "config").is_dir():
        raise RuntimeError(
            "la replica no tiene la configuracion exportada de la generacion "
            "vigente (config/). No se consultan checkouts de conf ni de Hermes. "
            "Se conserva la instalacion vigente."
        )
    prefijos = _prefijos_de(vigente)
    nuevo = str(gen.resolve())
    _copiar_arbol(vigente / "config", gen / "config", prefijos, nuevo)
    perfiles = vigente / "perfiles"
    if perfiles.is_dir():
        _copiar_arbol(perfiles, gen / "perfiles", prefijos, nuevo)
    paquete = gen / "config/hermes/shared/sistema-agentes/poteto"
    if (paquete / "poteto.v1.json").is_file() or (
        paquete / "skills/poteto-mode/SKILL.md"
    ).is_file():
        runtime["poteto"] = str(paquete.relative_to(gen))
    runtime["config"] = {}
    if "hermes" in runtime["lanzadores"] and (
        vigente / "perfiles/hermes"
    ).is_dir():
        runtime["config"]["hermes"] = "perfiles/hermes"
    tabla = gen / "config/hermes/model-routing.source.json"
    if tabla.is_file():
        # El manifiesto de la candidata es el importado. La tabla vigente conserva
        # bindings y metadatos; las cadenas salen de esa proyección.
        proyeccion = leer_json(tabla)
        proyeccion.update(proyectar_tabla(cargar(gen / "manifesto.toml")))
        _atomico(tabla, proyeccion)
        runtime["politica"] = str(tabla.relative_to(gen))
        script = gen / "config/hermes/scripts/claw_models.py"
        if script.is_file() and "openclaw" in runtime["lanzadores"]:
            # La derivación corre ANTES de resolver runtime["config"]["claw"]:
            # así el openclaw.json declarado es el derivado y comprobar_runtime
            # lo valida; sin openclaw como lanzador, la generación no usa Claw
            # y ninguna falla del script debe rechazar la candidata.
            _escribir_claw_desde_tabla(gen, tabla)
    if "openclaw" in runtime["lanzadores"] and (
        gen / "perfiles/claw/openclaw.json"
    ).is_file():
        runtime["config"]["claw"] = "perfiles/claw/openclaw.json"
    modelos = gen / "perfiles/claw/modelos.json"
    if modelos.is_file():
        runtime["claw_modelos"] = str(modelos.relative_to(gen))
    runtime["config_reutilizada"] = True


def _preparar_componente(
    doc: dict, destino: Path, gen: Path, home: Path
) -> tuple[list[str], str, dict | None]:
    if doc["tipo"] == "npm":
        if destino.exists():
            shutil.rmtree(destino)
        artefacto = _descargar(doc, home / "artefactos")
        argv = _instalar_npm(doc, artefacto, destino, home / "npm-cache")
        respuesta = _run(
            [*argv, "--version"], env=_entorno_generacion(gen), timeout=60
        )
        contrato = None
    else:
        contrato = _instalar_hermes(doc, destino)
        argv = contrato["argv"]
        respuesta = contrato["version"]
    if not respuesta:
        raise ValueError("el ejecutable no informa su version")
    return argv, respuesta, contrato


def _argv_real(gen: Path, argv: list[str]) -> list[str]:
    return [
        str(gen / arg) if not Path(arg).is_absolute() and (gen / arg).exists() else arg
        for arg in argv
    ]


@_operacion_flota
def preparar(
    home: Path,
    catalogo: dict | None = None,
    *,
    motor_origen: Path | None = None,
    repos: dict[str, Path] | None = None,
) -> dict:
    home = home.resolve()
    if (
        home in (Path.home().resolve(), (Path.home() / ".motor").resolve())
        or (home / "motor.db").exists()
    ):
        raise ValueError("la preparacion requiere un home de componentes propio")
    from .compartida import leer_fuente

    fuente = leer_fuente(home)
    if fuente is not None:
        aprobado = fuente["catalogo"]
        if catalogo is not None and catalogo != aprobado:
            raise ValueError(
                "la replica aplica el catalogo publicado, sin cambiar versiones"
            )
        catalogo = aprobado
    motor_origen = (motor_origen or Path(__file__).resolve().parents[2]).resolve()
    catalogo = catalogo if catalogo is not None else descubrir()
    componentes = catalogo.get("componentes") or []
    if not componentes:
        raise ValueError("no hay componentes oficiales disponibles")
    nombres = [c.get("nombre") for c in componentes]
    if len(set(nombres)) != len(nombres):
        raise ValueError("componente repetido")
    for doc in componentes:
        _validar_componente(doc)
    home.mkdir(parents=True, exist_ok=True)
    fuentes = (
        {}
        if fuente is not None
        else _snapshot_repos(
            repos if repos is not None else _repos_predeterminados(motor_origen)
        )
    )
    manifiesto = home / "manifiesto.toml"
    semilla = (
        Path((fuentes.get("conf") or {}).get("origen", "")) / "flota/manifesto.toml"
    )
    with _cerrojo_flota(home):
        if fuente is None and (home / "motor").exists():
            anterior = home / "generaciones" / asignar(home) / "runtime.json"
            if anterior.is_file():
                anteriores = leer_json(anterior).get(
                    "componentes", []
                )
                fusion = {c["nombre"]: c for c in anteriores}
                fusion.update({c["nombre"]: c for c in componentes})
                componentes = [fusion[n] for n in sorted(fusion)]
                for doc in componentes:
                    _validar_componente(doc)
                nombres = [c["nombre"] for c in componentes]
                catalogo = {**catalogo, "componentes": componentes}
        if not manifiesto.is_file() and not semilla.is_file():
            raise ValueError("no se encontro la fuente compartida flota/manifesto.toml")
        politica = cargar(manifiesto if manifiesto.is_file() else semilla)
        herramientas = {h.nombre: h for h in politica.herramientas}
        herramientas.update(
            {
                doc["nombre"]: Herramienta(
                    doc["nombre"], doc["fuente"], doc["version"], doc["integridad"]
                )
                for doc in componentes
            }
        )
        politica = replace(
            politica, herramientas=tuple(herramientas[n] for n in sorted(herramientas))
        )
        revision, gen = _crear_generacion(
            home, motor_origen, componentes, fuentes, emitir(politica)
        )
        recibo = home / "preparaciones" / (revision + ".json")
        runtime_path = gen / "runtime.json"
        if (
            recibo.exists()
            and runtime_path.is_file()
            and leer_json(recibo).get("estado") == "preparada"
        ):
            comprobar_runtime(gen)
            return {
                "accion": "preparada",
                "generacion": revision,
                "runtime": str(runtime_path),
                "reutilizada": True,
            }
        if _trabajos_sobre(home, revision):
            raise RuntimeError(
                "la generacion esta ocupada; no se modifica su instalacion"
            )
        runtime = {
            "schema": 1,
            "lanzadores": {},
            "env": {
                "HERMES_HOME": "perfiles/hermes",
                "HERMES_PROFILES_DIR": "perfiles/hermes/profiles",
                "HERMES_RUNTIME_DIR": "herramientas/hermes/tools",
                "OPENCLAW_STATE_DIR": "perfiles/claw",
                "OPENCLAW_CONFIG_PATH": "perfiles/claw/openclaw.json",
            },
            "componentes": componentes,
            "no_disponibles": catalogo.get("no_disponibles") or [],
        }
        _atomico(
            recibo,
            {"estado": "preparando", "generacion": revision, "catalogo": catalogo},
        )
        vigentes: set[str] = set()
        gen_vigente: Path | None = None
        runtime_vigente: dict = {}
        try:
            gen_vigente = home / "generaciones" / asignar(home)
            ruta_vigente = gen_vigente / "runtime.json"
            if ruta_vigente.is_file():
                runtime_vigente = leer_json(ruta_vigente)
                vigentes = set(runtime_vigente.get("lanzadores") or {})
        except (OSError, ValueError):
            vigentes = set()
            runtime_vigente = {}
        if gen_vigente is not None and not gen_vigente.is_dir():
            gen_vigente = None
        try:
            for doc in componentes:
                destino = gen / "herramientas" / doc["nombre"]
                motivo_reuso = ""
                try:
                    argv, respuesta, contrato = _preparar_componente(
                        doc, destino, gen, home
                    )
                except (
                    OSError,
                    ValueError,
                    RuntimeError,
                    subprocess.SubprocessError,
                ) as exc:
                    shutil.rmtree(destino, ignore_errors=True)
                    pieza = (
                        _reutilizar_pieza(
                            doc, destino, gen, gen_vigente, runtime_vigente
                        )
                        if fuente is not None
                        else None
                    )
                    if pieza is not None:
                        argv, respuesta, contrato = pieza
                        motivo_reuso = (
                            f"{exc}; se reutiliza la instalacion vigente"
                        )
                    elif doc["nombre"] in vigentes:
                        raise RuntimeError(
                            _causa_pieza(
                                doc,
                                exc,
                                runtime_vigente.get("componentes") or [],
                            )
                        ) from exc
                    else:
                        runtime["no_disponibles"].append(
                            {
                                "nombre": doc["nombre"],
                                "estado": "pendiente",
                                "motivo": str(exc)[:300],
                            }
                        )
                        continue
                if (
                    doc["nombre"] == "hermes"
                    and (gen / "herramientas/hermes/src").is_dir()
                ):
                    runtime["env"]["PYTHONPATH"] = "herramientas/hermes/src"
                if doc["nombre"] == "hermes" and contrato is not None:
                    runtime.setdefault("configuradores", {})["hermes"] = (
                        _utilidad_configuracion(gen, contrato)
                    )
                # Las rutas de la generacion se resuelven al lanzar, nunca por PATH global.
                runtime["lanzadores"][doc["nombre"]] = [
                    str(Path(a).relative_to(gen))
                    if Path(a).is_absolute() and gen in Path(a).parents
                    else a
                    for a in argv
                ]
                runtime.setdefault("observaciones", {})[doc["nombre"]] = {
                    "version": respuesta[:200],
                    "ejecutable": runtime["lanzadores"][doc["nombre"]],
                }
                if motivo_reuso:
                    runtime["observaciones"][doc["nombre"]]["motivo"] = motivo_reuso[
                        :300
                    ]
                if doc["nombre"] == "hermes" and contrato is not None:
                    observacion = dict(contrato["launcher"])
                    observacion["ruta"] = str(
                        Path(observacion["ruta"]).relative_to(gen)
                    )
                    runtime["observaciones"]["hermes"].update(
                        {
                            "entrada": "pm.environments.venv_command + hermes_cli.main",
                            "launcher": observacion,
                        }
                    )
            if not runtime["lanzadores"]:
                raise RuntimeError(
                    "la candidata no dejo ninguna herramienta utilizable; "
                    "se conserva la generacion vigente"
                )
            if fuente is not None:
                if gen_vigente is None:
                    raise RuntimeError(
                        "la replica no tiene generacion vigente de la que "
                        "reutilizar config/, perfiles/ y poteto. No se consultan "
                        "checkouts de conf ni de Hermes. Se conserva la instalacion vigente."
                    )
                _reutilizar_config(gen, gen_vigente, runtime)
            else:
                _copiar_config(gen, fuentes, runtime, vigente=gen_vigente)
            runtime["archivos"] = {}
            comandos = list(runtime["lanzadores"].values())
            comandos.extend(
                c["argv"] for c in runtime.get("configuradores", {}).values()
            )
            for argv in comandos:
                for arg in _argv_real(gen, argv):
                    ruta = Path(arg)
                    if ruta.is_absolute() and ruta.is_file():
                        clave = (
                            str(ruta.relative_to(gen))
                            if gen in ruta.parents
                            else str(ruta)
                        )
                        runtime["archivos"][clave] = _sha256(ruta)
            configuraciones = {
                "model-routing.source.json",
                "poteto.v1.json",
                "hermes.json",
                "modelos.json",
                "openclaw.json",
                "goncloud-managed.json",
                "team.json",
                "director-plugins.json",
                "claw-routing.json",
                "source-revision.json",
            }
            for directorio in (
                gen / "config",
                gen / "perfiles",
                gen / "herramientas/config-utils",
            ):
                for ruta in sorted(directorio.rglob("*")):
                    if (
                        ruta.is_file()
                        and (
                            ruta.suffix in (".py", ".toml", ".md", ".yaml", ".yml")
                            or ruta.name in configuraciones
                        )
                        and not {"__pycache__", ".git"}.intersection(ruta.parts)
                    ):
                        runtime["archivos"][str(ruta.relative_to(gen))] = _sha256(ruta)
            _atomico(runtime_path, runtime)
            guardar(manifiesto, politica)
            _atomico(
                recibo,
                {
                    "estado": "preparada",
                    "generacion": revision,
                    "catalogo": catalogo,
                    "runtime": str(runtime_path),
                    "integridad_runtime": _sha256(runtime_path),
                    "componentes": nombres,
                },
            )
        except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
            runtime_path.unlink(missing_ok=True)
            _atomico(
                recibo,
                {
                    "estado": "rechazada",
                    "generacion": revision,
                    "componentes": nombres,
                    "motivo": str(exc)[:300],
                },
            )
            raise
    return {
        "accion": "preparada",
        "generacion": revision,
        "runtime": str(runtime_path),
        "reutilizada": False,
    }


def _sha256(path: Path) -> str:
    huella = hashlib.sha256()
    with path.open("rb") as entrada:
        for bloque in iter(lambda: entrada.read(1024 * 1024), b""):
            huella.update(bloque)
    return huella.hexdigest()


def _comprobar_runtime(gen: Path, recibo: dict) -> None:
    path = gen / "runtime.json"
    if _sha256(path) != recibo.get("integridad_runtime"):
        raise ValueError(
            "runtime de la candidata alterado; se conserva la instalacion vigente"
        )
    runtime = leer_json(path)
    for nombre, huella in runtime.get("archivos", {}).items():
        ruta = Path(nombre) if Path(nombre).is_absolute() else gen / nombre
        if _sha256(ruta) != huella:
            raise ValueError(
                "ejecutable de la candidata alterado; se conserva la instalacion vigente"
            )


def _comprobar_claw(gen: Path, runtime: dict) -> None:
    resultado = _run(
        [
            *_argv_real(gen, runtime["lanzadores"]["openclaw"]),
            "config",
            "validate",
            "--json",
        ],
        env=_entorno_generacion(gen),
        timeout=60,
    )
    if json.loads(resultado).get("valid") is not True:
        raise ValueError(
            "la configuracion Claw no es valida para el ejecutable preparado"
        )


def comprobar_runtime(gen: Path) -> dict:
    gen = gen.resolve()
    recibo_path = gen.parents[1] / "preparaciones" / (gen.name + ".json")
    recibo = leer_json(recibo_path)
    if recibo.get("estado") != "preparada":
        raise ValueError("la candidata no tiene una preparacion comprobada")
    _comprobar_runtime(gen, recibo)
    runtime = leer_json((gen / "runtime.json"))
    if not runtime.get("lanzadores"):
        raise ValueError("la candidata no tiene herramientas utilizables")
    pendientes = {
        c.get("nombre") for c in runtime.get("no_disponibles", [])
    }
    ausentes = [
        nombre
        for nombre in recibo.get("componentes", [])
        if nombre not in runtime["lanzadores"] and nombre not in pendientes
    ]
    if ausentes:
        raise ValueError(
            "la candidata omite herramientas sin declararlas pendientes: "
            + ", ".join(sorted(ausentes))
        )
    for nombre, argv in runtime["lanzadores"].items():
        env = _entorno_generacion(gen)
        if nombre == "hermes":
            env["PYTHONPATH"] = str(gen / "herramientas/hermes/src")
        version = _run(
            [*_argv_real(gen, argv), "--version"],
            env=env,
            timeout=60,
        )
        if version[:200] != runtime["observaciones"][nombre]["version"]:
            raise ValueError(
                "el ejecutable cambio de version tras preparar la candidata"
            )
    config = runtime.get("config", {})
    if "hermes" in config and not runtime.get("config_reutilizada"):
        python = _argv_real(gen, runtime["configuradores"]["hermes"]["argv"])
        _run(
            [
                *python,
                str(gen / "config/hermes/scripts/configure_team.py"),
                "--home",
                str(gen / config["hermes"]),
                "--install-dir",
                str(gen / "herramientas/hermes/src"),
                "--check",
                "--preserve-local-soul",
            ],
            env=_entorno_configuracion(gen, runtime),
        )
    if "claw" in config:
        _comprobar_claw(gen, runtime)
    return {"estado": "comprobada", "componentes": sorted(runtime["lanzadores"])}


def actualizar(
    home: Path,
    catalogo: dict | None = None,
    *,
    motor_origen: Path | None = None,
    repos: dict[str, Path] | None = None,
    sin_turnos: bool = False,
) -> dict:
    candidata = preparar(home, catalogo, motor_origen=motor_origen, repos=repos)
    resultado = aplicar(home.resolve(), candidata["generacion"], sin_turnos=sin_turnos)
    return {**resultado, "preparacion": candidata}


@_operacion_flota
def estado(home: Path) -> dict:
    from .compartida import leer_fuente

    fuente = leer_fuente(home)
    if not (home / "motor").exists():
        return {"estado": "sin_instalacion", "componentes": []}
    generacion = asignar(home)
    runtime_path = home / "generaciones" / generacion / "runtime.json"
    runtime = (
        leer_json(runtime_path)
        if runtime_path.is_file()
        else {}
    )
    recibos = [
        leer_json(p)
        for p in sorted((home / "preparaciones").glob("*.json"))
    ]
    deseadas = (
        {h.nombre: h.version for h in cargar(home / "manifiesto.toml").herramientas}
        if (home / "manifiesto.toml").is_file()
        else {}
    )
    aplicadas = {c["nombre"]: c["version"] for c in runtime.get("componentes", [])}
    diferencias = [
        {"componente": nombre, "deseada": version, "aplicada": aplicadas.get(nombre)}
        for nombre, version in deseadas.items()
        if aplicadas.get(nombre) != version
    ]
    return {
        "generacion": generacion,
        "runtime": runtime,
        "preparaciones": recibos,
        "fuente": {
            "modo": "replica",
            "origen": fuente["origen"],
            "hash": fuente["hash"],
        }
        if fuente
        else {"modo": "publicador"},
        "diferencias": diferencias,
    }
