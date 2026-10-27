"""Prepara y publica generaciones de herramientas y configuración compartida."""

from __future__ import annotations

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
from .control import _atomico, _trabajos_sobre, aplicar, asignar
from .distribuidores import (
    _descargar,
    _entorno_generacion,
    _git,
    _instalar_hermes,
    _instalar_npm,
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


def _plugins_hermes(config: Path, home: Path, python: str, instalacion: Path) -> None:
    manifiesto = json.loads(
        (config / "director-plugins.json").read_text(encoding="utf-8")
    )
    director = manifiesto["director"]
    if Path(director).name != director:
        raise ValueError("nombre del director invalido")
    _run(
        [
            python,
            "-c",
            (
                "import sys; sys.path.insert(0,sys.argv[1]); "
                "from hermes_cli.profiles import create_profile,get_profile_dir; "
                "p=get_profile_dir(sys.argv[2]); "
                "None if p.exists() else create_profile(sys.argv[2],no_alias=True,description='Director de ensayo')"
            ),
            str(instalacion),
            director,
        ],
        env=_entorno_generacion(home.parents[1]),
    )
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


def _copiar_config(gen: Path, repos: dict, runtime: dict) -> None:
    permitidos = {
        "hermes": [
            "scripts",
            "plugins",
            "shared",
            "skills",
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
    for nombre, spec in repos.items():
        if nombre not in permitidos:
            raise ValueError("repositorio de configuracion desconocido")
        destino = gen / "config" / nombre
        if not destino.exists():
            repo = Path(spec["origen"])
            archivos = _git(
                repo,
                "ls-tree",
                "--name-only",
                spec["revision"],
                "--",
                *permitidos[nombre],
            ).splitlines()
            destino.mkdir(parents=True)
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
        proyeccion = json.loads(tabla.read_text(encoding="utf-8"))
        proyeccion.update(proyectar_tabla(cargar(gen / "manifesto.toml")))
        _atomico(tabla, proyeccion)
        runtime["politica"] = str(tabla.relative_to(gen))
        if "hermes" in runtime["lanzadores"]:
            python = _argv_real(gen, runtime["lanzadores"]["hermes"])[0]
            script = hermes / "scripts/configure_team.py"
            home_hermes = gen / "perfiles/hermes"
            args = [
                python,
                str(script),
                "--home",
                str(home_hermes),
                "--install-dir",
                str(gen / "herramientas/hermes/src"),
                "--preserve-local-soul",
            ]
            _plugins_hermes(
                hermes, home_hermes, python, gen / "herramientas/hermes/src"
            )
            _run(args, env=_entorno_generacion(gen))
            _run([*args, "--check"], env=_entorno_generacion(gen))
            runtime["config"]["hermes"] = str(home_hermes.relative_to(gen))
        if "openclaw" in runtime["lanzadores"]:
            destino = gen / "perfiles/claw"
            destino.mkdir(parents=True, exist_ok=True)
            salida = _run(
                [
                    sys.executable,
                    str(hermes / "scripts/claw_models.py"),
                    "--source",
                    str(tabla),
                    "desired",
                ]
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
            runtime["config"]["claw"] = str(
                (destino / "openclaw.json").relative_to(gen)
            )
            _comprobar_claw(gen, runtime)
            _run(
                [
                    sys.executable,
                    str(hermes / "scripts/claw_models.py"),
                    "--source",
                    str(tabla),
                    "check",
                    "--live",
                    str(destino / "modelos.json"),
                ]
            )
            runtime["claw_modelos"] = str((destino / "modelos.json").relative_to(gen))


def _argv_real(gen: Path, argv: list[str]) -> list[str]:
    return [
        str(gen / arg) if not Path(arg).is_absolute() and (gen / arg).exists() else arg
        for arg in argv
    ]


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
    fuentes = _snapshot_repos(
        repos if repos is not None else _repos_predeterminados(motor_origen)
    )
    manifiesto = home / "manifiesto.toml"
    semilla = (
        Path((fuentes.get("conf") or {}).get("origen", "")) / "flota/manifesto.toml"
    )
    with _cerrojo_flota(home):
        if (home / "motor").exists():
            anterior = home / "generaciones" / asignar(home) / "runtime.json"
            if anterior.is_file():
                anteriores = json.loads(anterior.read_text(encoding="utf-8")).get(
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
            and json.loads(recibo.read_text()).get("estado") == "preparada"
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
        try:
            for doc in componentes:
                destino = gen / "herramientas" / doc["nombre"]
                if destino.exists():
                    shutil.rmtree(destino)
                if doc["tipo"] == "npm":
                    artefacto = _descargar(doc, home / "artefactos")
                    argv = _instalar_npm(doc, artefacto, destino, home / "npm-cache")
                else:
                    argv = _instalar_hermes(doc, destino)
                respuesta = _run(
                    [*argv, "--version"], env=_entorno_generacion(gen), timeout=60
                )
                if not respuesta:
                    raise ValueError("el ejecutable no informa su version")
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
            _copiar_config(gen, fuentes, runtime)
            runtime["archivos"] = {}
            for argv in runtime["lanzadores"].values():
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
            for directorio in (gen / "config", gen / "perfiles"):
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
    runtime = json.loads(path.read_text(encoding="utf-8"))
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
    recibo = json.loads(recibo_path.read_text(encoding="utf-8"))
    if recibo.get("estado") != "preparada":
        raise ValueError("la candidata no tiene una preparacion comprobada")
    _comprobar_runtime(gen, recibo)
    runtime = json.loads((gen / "runtime.json").read_text(encoding="utf-8"))
    for nombre, argv in runtime["lanzadores"].items():
        version = _run(
            [*_argv_real(gen, argv), "--version"],
            env=_entorno_generacion(gen),
            timeout=60,
        )
        if version[:200] != runtime["observaciones"][nombre]["version"]:
            raise ValueError(
                "el ejecutable cambio de version tras preparar la candidata"
            )
    config = runtime.get("config", {})
    if "hermes" in config:
        python = _argv_real(gen, runtime["lanzadores"]["hermes"])[0]
        _run(
            [
                python,
                str(gen / "config/hermes/scripts/configure_team.py"),
                "--home",
                str(gen / config["hermes"]),
                "--install-dir",
                str(gen / "herramientas/hermes/src"),
                "--check",
            ],
            env=_entorno_generacion(gen),
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
) -> dict:
    candidata = preparar(home, catalogo, motor_origen=motor_origen, repos=repos)
    resultado = aplicar(home.resolve(), candidata["generacion"])
    return {**resultado, "preparacion": candidata}


def estado(home: Path) -> dict:
    if not (home / "motor").exists():
        return {"estado": "sin_instalacion", "componentes": []}
    generacion = asignar(home)
    runtime_path = home / "generaciones" / generacion / "runtime.json"
    runtime = (
        json.loads(runtime_path.read_text(encoding="utf-8"))
        if runtime_path.is_file()
        else {}
    )
    recibos = [
        json.loads(p.read_text(encoding="utf-8"))
        for p in sorted((home / "preparaciones").glob("*.json"))
    ]
    return {"generacion": generacion, "runtime": runtime, "preparaciones": recibos}
