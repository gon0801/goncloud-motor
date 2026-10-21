"""Configuración en tres capas, una por tipo de verdad, sin copias.

1. Conf compartida (un repo, el mismo en las tres máquinas): papeles, carriles de modelos,
   rutas modelo -> cuenta -> CLI, reglas y avisos. Es la única fuente de "qué agente hace qué".
2. `.motor.toml` de cada repo: checks, método de merge y receta de despliegue. Se lee de la
   rama BASE al tomar el plan y queda fijado (un agente no cambia las reglas de su propia rama).
3. `maquina.toml` + `secretos.env` de cada máquina: solo hechos de la máquina. Esquema cerrado:
   si trae papeles, modelos o reglas, se rechaza (así la instalación es idéntica por tipo).
"""

from __future__ import annotations

import os
import stat
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

import tomllib


class ConfError(ValueError):
    pass


# --------------------------------------------------------------------------- compartida


@dataclass(frozen=True)
class Ruta:
    """Un modelo concreto, la cuenta que lo paga y cómo se lanza sin ventana."""

    modelo: str
    cuenta: str
    cli: str
    argv: tuple[str, ...]  # plantilla: {brief} {prompt} {result}
    max_parallel: int = 1  # por cuenta y máquina se suma en machine._ocupados
    env: tuple[
        tuple[str, str], ...
    ] = ()  # p. ej. DeepSeek por el CLI de Claude; ${VAR} sale de secretos.env


@dataclass(frozen=True)
class Conf:
    sha: str  # versión de la conf (auditoría)
    papeles: dict[str, str]  # papel -> carril
    carriles: dict[str, tuple[str, ...]]  # carril -> modelos en orden de relevo
    rutas: dict[str, Ruta]  # modelo -> ruta
    reglas: str  # preámbulo de todo encargo
    reglas_revisor: str
    tiempos: dict[str, float]
    avisos: dict[str, str]  # {"base_url", "chat", "token_env"}
    motor: dict[str, str] = field(
        default_factory=dict
    )  # versión que la conf fija: {"version", "sha"}

    def escalera(self, papel: str) -> tuple[str, ...]:
        return self.carriles[self.papeles[papel]]

    def cuenta(self, modelo: str) -> str:
        return self.rutas[modelo].cuenta


def _git_sha(d: Path) -> str:
    r = subprocess.run(
        ["git", "-C", str(d), "rev-parse", "HEAD"],
        check=False,
        capture_output=True,
        text=True,
        errors="replace",
        stdin=subprocess.DEVNULL,
    )
    return r.stdout.strip() if r.returncode == 0 else "sin-git"


def load_conf(d: Path) -> Conf:
    papeles = tomllib.loads((d / "papeles.toml").read_text(encoding="utf-8"))["papeles"]
    carriles = {
        k: tuple(v)
        for k, v in tomllib.loads((d / "modelos.toml").read_text(encoding="utf-8"))[
            "carriles"
        ].items()
    }
    raw_rutas = tomllib.loads((d / "rutas.toml").read_text(encoding="utf-8"))["rutas"]
    rutas = {
        m: Ruta(
            modelo=m,
            cuenta=r["cuenta"],
            cli=r["cli"],
            argv=tuple(r["argv"]),
            max_parallel=int(r.get("max_parallel", 1)),
            env=tuple(sorted(r.get("env", {}).items())),
        )
        for m, r in raw_rutas.items()
    }
    general = (
        tomllib.loads((d / "general.toml").read_text(encoding="utf-8"))
        if (d / "general.toml").exists()
        else {}
    )
    errores = []
    fijada = general.get("motor", {})
    for k, v in fijada.items():
        if k not in ("version", "sha") or not isinstance(v, str) or not v.strip():
            errores.append(
                f"general.toml [motor]: solo version y sha, como texto; vino {k} = {v!r}"
            )
    for papel, carril in papeles.items():
        if carril not in carriles:
            errores.append(f"el papel {papel} apunta al carril {carril}, que no existe")
    for carril, modelos in carriles.items():
        for mo in modelos:
            if mo not in rutas:
                errores.append(
                    f"el carril {carril} nombra al modelo {mo}, que no tiene ruta"
                )
    if errores:
        raise ConfError("; ".join(errores))
    reglas_dir = d / "reglas"
    return Conf(
        sha=_git_sha(d),
        papeles=papeles,
        carriles=carriles,
        rutas=rutas,
        reglas=(reglas_dir / "encargo.md").read_text(encoding="utf-8")
        if (reglas_dir / "encargo.md").exists()
        else "",
        reglas_revisor=(reglas_dir / "revisor.md").read_text(encoding="utf-8")
        if (reglas_dir / "revisor.md").exists()
        else "",
        tiempos={k: float(v) for k, v in general.get("tiempos", {}).items()},
        avisos={k: str(v) for k, v in general.get("avisos", {}).items()},
        motor={
            k: v
            for k, v in fijada.items()
            if k in ("version", "sha") and isinstance(v, str)
        },
    )


# --------------------------------------------------------------------------- máquina

_CLAVES_MAQUINA = {
    "nombre",
    "conf",
    "path_extra",
    "env",
    "wsl",
    "boot_id_archivo",
    "github",
    "no_dormir",
    "tiene",
}
_PROHIBIDAS = {"papeles", "carriles", "rutas", "modelos", "reglas", "roles", "avisos"}


@dataclass(frozen=True)
class Maquina:
    nombre: str
    conf: Path
    path_extra: tuple[str, ...] = ()
    env: tuple[tuple[str, str], ...] = ()  # p. ej. certificados de Kaspersky en la PC
    wsl: tuple[str, ...] = ()  # CLIs que corren dentro de WSL
    github: str = "real"  # real | falso:<carpeta> (pruebas)
    tiene: tuple[str, ...] = ()  # capacidades de esta máquina, p. ej. "ssh-goncloud"
    # MacBook: comandos para no dormirse (ni con la tapa cerrada) mientras haya un plan en curso
    no_dormir_encender: tuple[str, ...] = ()
    no_dormir_apagar: tuple[str, ...] = ()
    secretos: tuple[tuple[str, str], ...] = field(default=(), repr=False)


def valida_maquina(raw: dict) -> None:
    """Esquema cerrado de maquina.toml (también lo usa `motor conf valida` sobre maquinas/*.toml)."""
    if "nombre" not in raw or "conf" not in raw:
        raise ConfError("maquina.toml necesita 'nombre' y 'conf'")
    nd = raw.get("no_dormir")
    if nd is not None and not (
        isinstance(nd.get("encender"), list) and isinstance(nd.get("apagar"), list)
    ):
        raise ConfError(
            "[no_dormir] lleva dos listas: encender = [...] y apagar = [...]"
        )
    tiene = raw.get("tiene", [])
    if not (
        isinstance(tiene, list) and all(isinstance(x, str) and x.strip() for x in tiene)
    ):
        raise ConfError(
            '"tiene" es una lista de nombres: tiene = ["ssh-goncloud", ...]'
        )
    malas = set(raw) & _PROHIBIDAS
    if malas:
        raise ConfError(
            f"maquina.toml no puede traer {sorted(malas)}: eso vive en la conf compartida, "
            "igual para las tres máquinas"
        )
    extras = set(raw) - _CLAVES_MAQUINA
    if extras:
        raise ConfError(f"maquina.toml trae claves desconocidas: {sorted(extras)}")


def load_maquina(home: Path) -> Maquina:
    raw = tomllib.loads((home / "maquina.toml").read_text(encoding="utf-8"))
    valida_maquina(raw)
    return Maquina(
        nombre=raw["nombre"],
        conf=Path(raw["conf"]).expanduser(),
        path_extra=tuple(raw.get("path_extra", [])),
        env=tuple(sorted(raw.get("env", {}).items())),
        wsl=tuple(raw.get("wsl", [])),
        github=raw.get("github", "real"),
        tiene=tuple(raw.get("tiene", [])),
        no_dormir_encender=tuple(raw.get("no_dormir", {}).get("encender", [])),
        no_dormir_apagar=tuple(raw.get("no_dormir", {}).get("apagar", [])),
        secretos=tuple(sorted(load_secretos(home).items())),
    )


def load_secretos(home: Path) -> dict[str, str]:
    """Tokens de larga duración (sin llavero). Exige permisos de dueño en POSIX."""
    p = home / "secretos.env"
    if not p.exists():
        return {}
    if os.name == "posix" and stat.S_IMODE(p.stat().st_mode) & 0o077:
        raise ConfError("secretos.env tiene permisos abiertos; debe ser 600")
    out = {}
    for linea in p.read_text(encoding="utf-8").splitlines():
        linea = linea.strip()
        if linea and not linea.startswith("#") and "=" in linea:
            k, v = linea.split("=", 1)
            out[k.strip()] = v.strip()
    return out


# --------------------------------------------------------------------------- repo


@dataclass(frozen=True)
class RepoConf:
    """`.motor.toml` de un repo, tal como estaba en la rama base al tomar el plan."""

    principal: str = "main"
    merge: str = "squash"
    checks_extra: tuple[str, ...] = ()
    desplegar: str = ""
    checklist: str = ""
    rollback: str = ""
    requiere: tuple[str, ...] = ()
    prueba_env: tuple[tuple[str, str], ...] = ()

    @staticmethod
    def parse(texto: str) -> RepoConf:
        raw = tomllib.loads(texto) if texto.strip() else {}
        gh, dep = raw.get("github", {}), raw.get("deploy", {})
        return RepoConf(
            principal=raw.get("repo", {}).get("principal", "main"),
            merge=gh.get("merge", "squash"),
            checks_extra=tuple(gh.get("checks_extra", [])),
            desplegar=dep.get("desplegar", ""),
            checklist=dep.get("checklist", ""),
            rollback=dep.get("rollback", ""),
            requiere=tuple(dep.get("requiere", [])),
            prueba_env=tuple(sorted(raw.get("pruebas", {}).get("env", {}).items())),
        )


def requisitos_faltantes(
    requiere: tuple[str, ...], tiene: tuple[str, ...]
) -> tuple[str, ...]:
    """Lo que un repo pide en `[deploy] requiere` y la máquina no declara en `tiene`."""
    return tuple(x for x in dict.fromkeys(requiere) if x not in tiene)


def motivo_requiere(maquina: str, repo: str, faltan: tuple[str, ...]) -> str:
    """Una frase llana para quien entregó el plan (sin rutas ni jerga)."""
    nombre = repo.removesuffix(".git").rstrip("/").split("/")[-1].split(":")[-1]
    cosas = ", ".join(faltan)
    return (
        f"{maquina} no puede llevar este plan: {nombre} necesita {cosas} para desplegar y esa máquina no lo tiene. "
        "Entrégalo en una máquina que sí lo tenga."
    )
