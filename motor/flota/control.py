from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import sys
from pathlib import Path

from .manifiesto import Politica, cargar, guardar, proyectar_tabla


def _atomico(path: Path, doc: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporal = path.with_suffix(path.suffix + ".tmp")
    temporal.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporal.replace(path)


def _leer(path: Path) -> dict | None:
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def _git(motor: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(motor), *args],
        check=check,
        capture_output=True,
        text=True,
    )


def cabeza(home: Path) -> str:
    return _git(home / "motor", "rev-parse", "HEAD").stdout.strip()


def anotar_trabajo(home: Path, trabajo: str, generacion: str, herramienta: str) -> None:
    _atomico(
        home / "trabajos" / f"{trabajo}.json",
        {"trabajo": trabajo, "generacion": generacion, "herramienta": herramienta},
    )


def leer_trabajo(home: Path, trabajo: str) -> dict | None:
    return _leer(home / "trabajos" / f"{trabajo}.json")


def asignar(home: Path) -> str:
    actual = home / "actual"
    if actual.is_symlink():
        return actual.resolve().name
    return cabeza(home)


def _comprobar(gen: Path) -> int:
    script = gen / "comprobar.py"
    if not script.is_file():
        return 1
    return subprocess.run([sys.executable, str(script)], cwd=gen).returncode


def _enlace(home: Path, gen: Path) -> None:
    actual = home / "actual"
    if actual.is_symlink() or actual.exists():
        actual.unlink()
    actual.symlink_to(gen, target_is_directory=True)


def aplicar(home: Path, destino: str, corte: str = "") -> dict:
    motor = home / "motor"
    head = cabeza(home)
    completo = _leer(home / "instalacion.json")
    if (
        completo
        and completo.get("etapa") == "completo"
        and (completo.get("destino") or {}).get("codigo") == destino
        and (home / "actual").is_symlink()
        and (home / "actual").resolve() == (home / "generaciones" / destino).resolve()
    ):
        return {"accion": "igual", "generacion": destino}
    recibo = {
        "intento": destino[:12],
        "maquina": "ensayo",
        "etapa": "origen",
        "anterior": {"codigo": head, "conf": ""},
        "destino": {"codigo": destino, "conf": ""},
        "causa": "",
    }
    _atomico(home / "instalacion-pendiente.json", recibo)
    if corte == "pendiente":
        return {"accion": "cortado", "etapa": "origen", "vigente": head}
    gen = home / "generaciones" / destino
    if not gen.exists():
        _git(motor, "worktree", "add", "--detach", str(gen), destino)
    recibo["etapa"] = "codigo"
    _atomico(home / "instalacion-pendiente.json", recibo)
    if corte == "worktree":
        return {"accion": "cortado", "etapa": "codigo", "vigente": asignar(home) if (home / "actual").exists() else head}
    if _comprobar(gen) != 0:
        (home / "instalacion-pendiente.json").unlink(missing_ok=True)
        _atomico(
            home / "candidata.json",
            {"estado": "rechazada", "destino": destino, "motivo": "la comprobacion fallo"},
        )
        return {"accion": "rechazada", "vigente": head, "destino": destino}
    _enlace(home, gen)
    recibo["etapa"] = "completo"
    recibo["causa"] = ""
    _atomico(home / "instalacion.json", recibo)
    (home / "instalacion-pendiente.json").unlink(missing_ok=True)
    return {"accion": "aplicada", "generacion": destino, "anterior": head}


def recuperar(home: Path) -> dict:
    pend_path = home / "instalacion-pendiente.json"
    pend = _leer(pend_path)
    if pend is None:
        return {"accion": "nada"}
    destino = (pend.get("destino") or {}).get("codigo") or ""
    anterior = (pend.get("anterior") or {}).get("codigo") or ""
    gen = home / "generaciones" / destino
    actual = home / "actual"
    apunta = False
    if gen.exists() and actual.is_symlink():
        apunta = actual.resolve() == gen.resolve()
    if gen.exists() and _comprobar(gen) == 0:
        if not apunta:
            _enlace(home, gen)
        pend["etapa"] = "completo"
        pend["causa"] = ""
        _atomico(home / "instalacion.json", pend)
        pend_path.unlink(missing_ok=True)
        return {"accion": "completo", "generacion": destino}
    if gen.exists() and _comprobar(gen) != 0:
        if apunta and anterior:
            previa = home / "generaciones" / anterior
            if previa.exists():
                _enlace(home, previa)
            else:
                actual.unlink()
        pend_path.unlink(missing_ok=True)
        _atomico(
            home / "candidata.json",
            {"estado": "rechazada", "destino": destino, "motivo": "la comprobacion fallo"},
        )
        return {"accion": "rechazada", "destino": destino}
    pend_path.unlink(missing_ok=True)
    _atomico(
        home / "recuperacion.json",
        {"accion": "revertido", "anterior": anterior, "destino": destino},
    )
    return {"accion": "revertido", "anterior": anterior, "destino": destino}


def anotar_respaldo(home: Path, politica: Politica, respaldo: dict) -> Politica:
    from dataclasses import replace

    from .manifiesto import Respaldo

    nuevo = replace(
        politica,
        respaldo=Respaldo(
            respaldo["funcion"],
            respaldo["modelo"],
            respaldo["esfuerzo"],
            respaldo["hasta"],
            respaldo["motivo"],
        ),
    )
    guardar(home / "manifiesto.toml", nuevo)
    cargada = cargar(home / "manifiesto.toml")
    if tuple(p.cadena for p in cargada.puestos) != tuple(p.cadena for p in politica.puestos):
        raise RuntimeError("el respaldo cambio la cadena deseada")
    return cargada


def publicar_proyeccion(home: Path, politica: Politica, paquete: Path) -> dict:
    import os
    import tempfile

    salida = home / "proyeccion"
    home.mkdir(parents=True, exist_ok=True)
    temporal = Path(
        tempfile.mkdtemp(prefix=".proyeccion-", dir=str(home))
    )
    respaldo = home / ".proyeccion-anterior"
    try:
        tabla = proyectar_tabla(politica)
        dirigir = [p for p in politica.puestos if p.funcion == "dirigir"]
        if not dirigir:
            raise ValueError("la proyección necesita el puesto dirigir")
        if not (Path(paquete) / "SKILL.md").is_file():
            raise ValueError("el paquete no trae SKILL.md")
        texto = json.dumps(tabla, ensure_ascii=False, indent=2) + "\n"
        (temporal / "model-routing.source.json").write_text(
            texto, encoding="utf-8"
        )
        poteto = temporal / "poteto"
        shutil.copytree(paquete, poteto)
        digest = hashlib.sha256((poteto / "SKILL.md").read_bytes()).hexdigest()
        herramientas = hashlib.sha256()
        for path in sorted((poteto / "runtimes").glob("*.json")):
            herramientas.update(path.read_bytes())
        flota = {
            "schema": "sistema-agentes.flota.v1",
            "rootId": "flota-directores",
            "unitId": "director",
            "attempt": 1,
            "specialist": "director",
            "host": "ensayo",
            "executor": "cli",
            "accountRef": "suscripcion",
            "model": {"id": dirigir[0].cadena[0][0]},
            "generation": 1,
            "permissions": ["read"],
            "attestations": [
                {"what": "poteto", "digest": f"sha256:{digest}"},
                {"what": "tools", "digest": "sha256:" + herramientas.hexdigest()},
            ],
        }
        (temporal / "flota.json").write_text(
            json.dumps(flota, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        json.loads((temporal / "model-routing.source.json").read_text(encoding="utf-8"))
        json.loads((temporal / "flota.json").read_text(encoding="utf-8"))
    except BaseException:
        shutil.rmtree(temporal, ignore_errors=True)
        raise
    if (respaldo.exists() or respaldo.is_symlink()) and not (
        salida.exists() or salida.is_symlink()
    ):
        os.rename(respaldo, salida)
    elif respaldo.exists() or respaldo.is_symlink():
        if respaldo.is_dir() and not respaldo.is_symlink():
            shutil.rmtree(respaldo)
        else:
            respaldo.unlink()
    movida = False
    try:
        if salida.exists() or salida.is_symlink():
            os.rename(salida, respaldo)
            movida = True
        os.rename(temporal, salida)
    except BaseException:
        if movida and not salida.exists() and respaldo.exists():
            os.rename(respaldo, salida)
        shutil.rmtree(temporal, ignore_errors=True)
        raise
    if movida:
        shutil.rmtree(respaldo, ignore_errors=True)
    return {"proyeccion": str(salida), "poteto": digest}


def discrepancia(config_modelo: str, cli_modelo: str) -> str:
    if cli_modelo and config_modelo and cli_modelo != config_modelo:
        return (
            f"el CLI incluido reporta {cli_modelo}; la configuracion dice {config_modelo}"
        )
    return ""


def observar_ejecutable(nombre: str, argv: list[str]) -> dict:
    if not argv or not str(argv[0]).strip():
        raise ValueError("observar necesita un comando")
    try:
        hecho = subprocess.run(argv, capture_output=True, text=True, timeout=20)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"nombre": nombre, "estado": "ausente", "detalle": str(exc)}
    texto = "\n".join(parte for parte in (hecho.stdout, hecho.stderr) if parte).strip()
    primera = texto.splitlines()[0] if texto else ""
    if "@" in primera or any(m in primera.lower() for m in ("token", "secret", "password")):
        primera = "presente"
    binario = shutil.which(argv[0])
    integridad = ""
    if binario:
        integridad = "sha256:" + hashlib.sha256(Path(binario).read_bytes()).hexdigest()
    return {
        "nombre": nombre,
        "estado": "presente" if hecho.returncode == 0 else "fallo",
        "version": primera[:200],
        "integridad": integridad,
        "rc": hecho.returncode,
    }


def escribir_observacion(home: Path, host: str, doc: dict) -> None:
    _atomico(home / "observaciones" / f"{host}.json", doc)
