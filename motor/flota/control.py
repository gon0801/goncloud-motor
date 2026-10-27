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


def _trabajos_sobre(home: Path, generacion: str) -> list[dict]:
    carpeta = home / "trabajos"
    if not carpeta.is_dir():
        return []
    ocupantes = []
    for path in sorted(carpeta.glob("*.json")):
        try:
            doc = _leer(path)
        except (OSError, ValueError):
            continue
        if not doc or str(doc.get("generacion") or "") != generacion:
            continue
        if doc.get("reserva") and not doc.get("pid"):
            ocupantes.append(doc)
            continue
        if not doc.get("pid"):
            continue
        from motor import plataforma

        try:
            estado = plataforma.estado_proc(doc)
        except (OSError, TypeError, ValueError):
            estado = "incierto"
        if estado == "vivo":
            ocupantes.append(doc)
            continue
        directorio = doc.get("directorio")
        if directorio:
            try:
                from motor.encargos.sesion import observar

                observacion = observar(Path(str(directorio)), doc)
            except Exception:
                observacion = "incierto"
            if observacion in ("vivo", "incierto"):
                ocupantes.append(doc)
        elif estado == "incierto":
            ocupantes.append(doc)
    return ocupantes


def _gen_integra(gen: Path, destino: str) -> bool:
    if not gen.is_dir():
        return False
    try:
        cabeza_gen = _git(gen, "rev-parse", "HEAD").stdout.strip()
        if cabeza_gen != destino:
            return False
        estado = _git(gen, "status", "--porcelain", "--untracked-files=no").stdout
    except (OSError, subprocess.CalledProcessError):
        return False
    return not estado.strip()


def _asegurar_gen(home: Path, destino: str, gen: Path) -> bool:
    if _gen_integra(gen, destino):
        return True
    if _trabajos_sobre(home, destino):
        return False
    motor = home / "motor"
    subprocess.run(
        ["git", "-C", str(motor), "worktree", "remove", "--force", str(gen)],
        check=False,
        capture_output=True,
        text=True,
    )
    if gen.exists():
        shutil.rmtree(gen, ignore_errors=True)
    subprocess.run(
        ["git", "-C", str(motor), "worktree", "prune"],
        check=False,
        capture_output=True,
        text=True,
    )
    if gen.exists():
        return False
    _git(motor, "worktree", "add", "--detach", str(gen), destino)
    return _gen_integra(gen, destino)


def anotar_trabajo(
    home: Path,
    trabajo: str,
    generacion: str,
    herramienta: str,
    identidad: dict | None = None,
) -> None:
    doc = {"trabajo": trabajo, "generacion": generacion, "herramienta": herramienta}
    path = home / "trabajos" / f"{trabajo}.json"
    try:
        previo = _leer(path)
    except (OSError, ValueError):
        previo = None
    if previo:
        if previo.get("directorio"):
            doc["directorio"] = previo["directorio"]
        if previo.get("reserva"):
            doc["reserva"] = True
    if identidad:
        for campo in ("pid", "inicio", "boot_id", "host", "directorio"):
            if identidad.get(campo) not in (None, ""):
                doc[campo] = identidad[campo]
        if identidad.get("reserva"):
            doc["reserva"] = True
        elif identidad.get("reserva") is False:
            doc.pop("reserva", None)
    _atomico(path, doc)


def liberar_trabajo(home: Path, trabajo: str) -> None:
    try:
        (home / "trabajos" / f"{trabajo}.json").unlink(missing_ok=True)
    except OSError:
        pass


def leer_trabajo(home: Path, trabajo: str) -> dict | None:
    return _leer(home / "trabajos" / f"{trabajo}.json")


def asignar(home: Path) -> str:
    actual = home / "actual"
    if actual.is_symlink():
        return actual.resolve().name
    return cabeza(home)


def _comprobar_detalle(gen: Path) -> tuple[int, str]:
    script = gen / "comprobar.py"
    if script.is_file():
        try:
            rc = subprocess.run(
                [sys.executable, str(script)], cwd=gen, timeout=120
            ).returncode
        except (OSError, subprocess.TimeoutExpired):
            return 1, "comprobar.py"
        return rc, "comprobar.py"
    if (gen / "motor" / "__main__.py").is_file():
        try:
            ayuda = subprocess.run(
                [sys.executable, "-m", "motor", "--help"],
                cwd=gen,
                capture_output=True,
                text=True,
                timeout=30,
            )
            if ayuda.returncode != 0:
                return ayuda.returncode or 1, "motor --help"
            compilado = subprocess.run(
                [sys.executable, "-m", "compileall", "-q", str(gen / "motor")],
                cwd=gen,
                capture_output=True,
                text=True,
                timeout=120,
            )
            return compilado.returncode, "motor --help y compileall"
        except (OSError, subprocess.TimeoutExpired):
            return 1, "motor --help y compileall"
    if (gen / "pyproject.toml").is_file():
        try:
            rc = subprocess.run(
                [sys.executable, "-m", "compileall", "-q", str(gen)],
                cwd=gen,
                capture_output=True,
                text=True,
                timeout=120,
            ).returncode
        except (OSError, subprocess.TimeoutExpired):
            return 1, "compileall"
        return rc, "compileall"
    if any(gen.glob("*.py")) or (gen / "motor").is_dir():
        try:
            rc = subprocess.run(
                [sys.executable, "-m", "compileall", "-q", str(gen)],
                cwd=gen,
                capture_output=True,
                text=True,
                timeout=120,
            ).returncode
        except (OSError, subprocess.TimeoutExpired):
            return 1, "compileall"
        return rc, "compileall"
    return 1, "ninguno"


def _comprobar(gen: Path) -> int:
    return _comprobar_detalle(gen)[0]


def _enlace(home: Path, gen: Path) -> None:
    actual = home / "actual"
    if actual.is_symlink() or actual.exists():
        actual.unlink()
    actual.symlink_to(gen, target_is_directory=True)


def _anterior_real(home: Path) -> str:
    actual = home / "actual"
    if actual.is_symlink():
        return asignar(home)
    completo = _leer(home / "instalacion.json")
    if completo:
        codigo = (completo.get("destino") or {}).get("codigo")
        if codigo:
            return str(codigo)
    return cabeza(home)


def revertir(home: Path) -> dict:
    pend_path = home / "instalacion-pendiente.json"
    inst_path = home / "instalacion.json"
    pend = _leer(pend_path)
    comp = _leer(inst_path)
    if pend is None and comp is None:
        return {"accion": "nada"}
    usado = ""
    recibo = None
    consumir_pendiente = False
    if pend is not None and comp is not None:
        pend_anterior = str((pend.get("anterior") or {}).get("codigo") or "")
        comp_destino = str((comp.get("destino") or {}).get("codigo") or "")
        vigente = asignar(home) if (home / "actual").is_symlink() else comp_destino
        if pend_anterior and pend_anterior in {vigente, comp_destino}:
            recibo = pend
            usado = "pendiente"
            consumir_pendiente = True
        else:
            recibo = comp
            usado = "instalacion"
            consumir_pendiente = True
    elif pend is not None:
        recibo = pend
        usado = "pendiente"
        consumir_pendiente = True
    else:
        recibo = comp
        usado = "instalacion"
    assert recibo is not None
    anterior = str((recibo.get("anterior") or {}).get("codigo") or "")
    destino = str((recibo.get("destino") or {}).get("codigo") or "")
    if not anterior:
        return {"accion": "nada"}
    motor = home / "motor"
    if (motor / ".git").exists() or (motor / ".git").is_file():
        subprocess.run(
            ["git", "-C", str(motor), "-c", "core.hooksPath=/dev/null", "checkout", "-q", "--detach", anterior],
            check=False,
            capture_output=True,
            text=True,
        )
    previa = home / "generaciones" / anterior
    actual = home / "actual"
    if previa.exists():
        _enlace(home, previa)
    elif actual.is_symlink():
        actual.unlink()
    if usado == "instalacion":
        historial = home / "historial"
        historial.mkdir(parents=True, exist_ok=True)
        nombre = f"instalacion-{destino}.json" if destino else "instalacion.json"
        destino_historial = historial / nombre
        if destino_historial.exists():
            destino_historial = historial / f"instalacion-{destino}-{recibo.get('intento') or 'revertida'}.json"
        inst_path.replace(destino_historial)
    if consumir_pendiente:
        pend_path.unlink(missing_ok=True)
    doc = {"accion": "revertido", "anterior": anterior, "destino": destino, "recibo": usado}
    _atomico(home / "recuperacion.json", doc)
    return {"accion": "revertido", "anterior": anterior, "generacion": anterior, "destino": destino}


def aplicar(home: Path, destino: str, corte: str = "") -> dict:
    motor = home / "motor"
    anterior = _anterior_real(home)
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
        "anterior": {"codigo": anterior, "conf": ""},
        "destino": {"codigo": destino, "conf": ""},
        "causa": "",
    }
    _atomico(home / "instalacion-pendiente.json", recibo)
    if corte == "pendiente":
        return {"accion": "cortado", "etapa": "origen", "vigente": anterior}
    gen = home / "generaciones" / destino
    if not gen.exists():
        _git(motor, "worktree", "add", "--detach", str(gen), destino)
    elif not _asegurar_gen(home, destino, gen):
        (home / "instalacion-pendiente.json").unlink(missing_ok=True)
        _atomico(
            home / "candidata.json",
            {"estado": "alterada", "destino": destino, "motivo": "candidata alterada"},
        )
        return {"accion": "rechazada", "vigente": anterior, "destino": destino, "motivo": "candidata alterada"}
    recibo["etapa"] = "codigo"
    _atomico(home / "instalacion-pendiente.json", recibo)
    if corte == "worktree":
        return {"accion": "cortado", "etapa": "codigo", "vigente": asignar(home) if (home / "actual").exists() else anterior}
    rc, comprobador = _comprobar_detalle(gen)
    recibo["comprobador"] = comprobador
    if rc != 0:
        (home / "instalacion-pendiente.json").unlink(missing_ok=True)
        _atomico(
            home / "candidata.json",
            {"estado": "rechazada", "destino": destino, "motivo": "la comprobacion fallo", "comprobador": comprobador},
        )
        return {"accion": "rechazada", "vigente": anterior, "destino": destino}
    if destino != anterior and _trabajos_sobre(home, anterior):
        return {
            "accion": "ocupada",
            "vigente": anterior,
            "destino": destino,
            "generacion_preparada": destino,
        }
    _enlace(home, gen)
    recibo["etapa"] = "completo"
    recibo["causa"] = ""
    _atomico(home / "instalacion.json", recibo)
    (home / "instalacion-pendiente.json").unlink(missing_ok=True)
    return {"accion": "aplicada", "generacion": destino, "anterior": anterior}


def recuperar(home: Path) -> dict:
    pend_path = home / "instalacion-pendiente.json"
    pend = _leer(pend_path)
    if pend is None:
        comp = _leer(home / "instalacion.json")
        if comp and comp.get("etapa") == "completo":
            destino_c = str((comp.get("destino") or {}).get("codigo") or "")
            gen_c = home / "generaciones" / destino_c
            if destino_c and gen_c.exists():
                rc_c, _nombre_c = _comprobar_detalle(gen_c)
                if rc_c != 0:
                    return revertir(home)
                if (gen_c / "motor" / "__main__.py").is_file():
                    try:
                        arranque = subprocess.run(
                            [sys.executable, "-m", "motor", "--help"],
                            cwd=gen_c,
                            capture_output=True,
                            text=True,
                            timeout=30,
                        )
                        if arranque.returncode != 0:
                            return revertir(home)
                    except (OSError, subprocess.TimeoutExpired):
                        return revertir(home)
        return {"accion": "nada"}
    destino = (pend.get("destino") or {}).get("codigo") or ""
    anterior = (pend.get("anterior") or {}).get("codigo") or ""
    gen = home / "generaciones" / destino
    vigente_rec = asignar(home) if (home / "actual").is_symlink() else anterior
    if destino and vigente_rec and destino != vigente_rec and _trabajos_sobre(home, vigente_rec):
        return {"accion": "ocupada", "vigente": vigente_rec, "destino": destino, "generacion_preparada": destino}
    if destino and gen.exists() and not _asegurar_gen(home, destino, gen):
        _atomico(
            home / "candidata.json",
            {"estado": "alterada", "destino": destino, "motivo": "candidata alterada"},
        )
        return {"accion": "rechazada", "destino": destino, "motivo": "candidata alterada"}
    actual = home / "actual"
    apunta = False
    if gen.exists() and actual.is_symlink():
        apunta = actual.resolve() == gen.resolve()
    rc, comprobador = _comprobar_detalle(gen) if gen.exists() else (1, "ninguno")
    if gen.exists() and rc == 0:
        if not apunta:
            _enlace(home, gen)
        pend["etapa"] = "completo"
        pend["causa"] = ""
        pend["comprobador"] = comprobador
        _atomico(home / "instalacion.json", pend)
        pend_path.unlink(missing_ok=True)
        return {"accion": "completo", "generacion": destino}
    if gen.exists() and rc != 0:
        if apunta and anterior:
            previa = home / "generaciones" / anterior
            if previa.exists():
                _enlace(home, previa)
            else:
                actual.unlink()
        pend_path.unlink(missing_ok=True)
        _atomico(
            home / "candidata.json",
            {"estado": "rechazada", "destino": destino, "motivo": "la comprobacion fallo", "comprobador": comprobador},
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


def _json_objeto(path: Path) -> dict:
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ValueError(f"JSON invalido en el paquete: {path.name}") from exc
    if not isinstance(doc, dict):
        raise ValueError(f"JSON no es un objeto en el paquete: {path.name}")
    return doc


def _localizar_paquete(paquete: Path) -> tuple[Path, list[Path]]:
    paquete = Path(paquete)
    if not paquete.is_dir():
        raise ValueError("el paquete no trae SKILL.md")
    declarados: list[str] = []
    manifiesto = paquete / "poteto.v1.json"
    if manifiesto.is_file():
        doc = _json_objeto(manifiesto)
        crudos = doc.get("files")
        if isinstance(crudos, list):
            for fila in crudos:
                if isinstance(fila, dict) and isinstance(fila.get("path"), str):
                    declarados.append(str(fila["path"]))
    candidatos: list[Path] = []
    for declarado in declarados:
        ruta = Path(declarado)
        if ruta.name == "SKILL.md" and "poteto-mode" in ruta.parts:
            candidatos.append(ruta)
    candidatos.extend(
        [
            Path("skills/poteto-mode/SKILL.md"),
            Path("SKILL.md"),
        ]
    )
    for declarado in declarados:
        ruta = Path(declarado)
        if ruta.name == "SKILL.md":
            candidatos.append(ruta)
    skill_rel = None
    vistos: set[Path] = set()
    for candidato in candidatos:
        if candidato in vistos:
            continue
        vistos.add(candidato)
        if (paquete / candidato).is_file():
            skill_rel = candidato
            break
    if skill_rel is None:
        raise ValueError("el paquete no trae SKILL.md")
    if not (paquete / skill_rel).read_bytes().strip():
        raise ValueError("la skill del paquete esta vacia")
    runtimes = sorted(
        (
            path
            for path in paquete.rglob("*.json")
            if path.parent.name == "runtimes" and path.is_file()
        ),
        key=lambda p: p.relative_to(paquete).as_posix(),
    )
    if not runtimes:
        runtimes = sorted(
            (
                path
                for path in paquete.glob("*.json")
                if path.name != "poteto.v1.json" and path.is_file()
            ),
            key=lambda p: p.name,
        )
    for runtime in runtimes:
        _json_objeto(runtime)
    return skill_rel, runtimes


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
        skill_rel, runtime_paths = _localizar_paquete(Path(paquete))
        texto = json.dumps(tabla, ensure_ascii=False, indent=2) + "\n"
        (temporal / "model-routing.source.json").write_text(
            texto, encoding="utf-8"
        )
        poteto = temporal / "poteto"
        shutil.copytree(paquete, poteto)
        digest = hashlib.sha256((poteto / skill_rel).read_bytes()).hexdigest()
        herramientas = hashlib.sha256()
        for fuente in runtime_paths:
            herramientas.update((poteto / fuente.relative_to(Path(paquete))).read_bytes())
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
