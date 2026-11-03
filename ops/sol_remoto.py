#!/usr/bin/env python3
"""Invoca el Sol de la cuenta del Mac sobre un archivo del SHA local."""

import argparse
import base64
import json
import os
import re
import shlex
import subprocess
import sys
import tempfile
from pathlib import Path

REMOTO = r"""
import base64, io, json, os, pathlib, select, signal, subprocess, sys, tarfile, tempfile, time

def interrumpido(signum, frame):
    raise SystemExit(128 + signum)

for senal in (signal.SIGHUP, signal.SIGTERM, signal.SIGINT):
    signal.signal(senal, interrumpido)

def emitir(objeto):
    print(json.dumps(objeto), flush=True)

def textos_de_error(obj, acum):
    if isinstance(obj, str):
        s = obj.strip()
        if s[:1] in "{[":
            try:
                anidado = json.loads(s)
            except ValueError:
                acum.append(s)
            else:
                textos_de_error(anidado, acum)
            return
        if s:
            acum.append(s)
    elif isinstance(obj, dict):
        for valor in obj.values():
            textos_de_error(valor, acum)
    elif isinstance(obj, list):
        for valor in obj:
            textos_de_error(valor, acum)

def canales_de_error(stderr, stdout):
    partes = [stderr]
    for linea in stdout.splitlines():
        bruto = linea.strip()
        if not bruto.startswith("{"):
            continue
        try:
            ev = json.loads(bruto)
        except ValueError:
            continue
        if isinstance(ev, dict) and ev.get("type") in ("error", "turn.failed"):
            acum = []
            textos_de_error(ev, acum)
            partes.append("\n".join(acum))
    return "\n".join(partes)

def clasifica_codex(texto):
    try:
        from motor.fallas import clasifica
    except ImportError:
        sys.path.insert(0, os.path.expanduser("~/motor"))
        from motor.fallas import clasifica
    return clasifica("codex", "", texto)

entrada = json.loads(sys.stdin.readline())
with tempfile.TemporaryDirectory(prefix="motor-sol-") as temporal:
    raiz = pathlib.Path(temporal)
    checkout = raiz / "checkout"
    checkout.mkdir()
    with tarfile.open(fileobj=io.BytesIO(base64.b64decode(entrada["archive"], validate=True)), mode="r:gz") as tar:
        tar.extractall(checkout, filter="data")
    texto = {"type": "string"}
    esquema = {
        "type": "object", "additionalProperties": False,
        "required": ["sha", "veredicto", "bloqueantes", "notas"],
        "properties": {
            "sha": {"type": "string", "const": entrada["sha"]},
            "veredicto": {"type": "string", "enum": ["APROBADO", "CAMBIOS"]},
            "bloqueantes": {"type": "array", "items": {
                "type": "object", "additionalProperties": False,
                "required": ["texto", "repro", "arreglo"],
                "properties": {k: texto for k in ("texto", "repro", "arreglo")}}},
            "notas": {"type": "array", "items": texto}}}
    schema = raiz / "schema.json"
    schema.write_text(json.dumps(esquema))
    final = raiz / "final.json"
    prompt = raiz / "prompt.txt"
    prompt.write_text(entrada["prompt"] + "\n\nEl checkout es un git archive sin metadatos .git. "
        "Su SHA original, declarado por el motor, es " + entrada["sha"] + ". "
        "Revisa estos archivos y devuelve ese SHA. El CLI almacena tu respuesta final JSON. "
        "No escribas rutas del Mini, son inaccesibles desde este Mac.\n")
    proceso = None
    try:
        with prompt.open() as fuente, (raiz / "codex.out").open("w+") as salida, (raiz / "codex.err").open("w+") as err:
            proceso = subprocess.Popen([
                "/opt/homebrew/bin/codex", "exec", "--json", "--model", "gpt-5.6-sol",
                "-c", 'model_reasoning_effort="medium"', "--sandbox", "read-only",
                "--skip-git-repo-check", "--output-schema", str(schema),
                "--output-last-message", str(final), "-"],
                cwd=checkout, stdin=fuente, stdout=salida, stderr=err,
                start_new_session=True,
                env={**os.environ, "PATH": "/opt/homebrew/bin:" + os.environ.get("PATH", "")})
            while proceso.poll() is None:
                emitir({"latido": time.time()})
                if select.select([sys.stdin], [], [], 5)[0] and not os.read(0, 1):
                    raise RuntimeError("Se cerro la conexion SSH")
            if proceso.returncode:
                err.seek(0)
                salida.seek(0)
                fallo = clasifica_codex(canales_de_error(err.read(), salida.read()))
                error = {"categoria": fallo["kind"], "codigo": proceso.returncode}
                pausa = fallo.get("retry_after_s")
                if type(pausa) is int or type(pausa) is float:
                    error["retry_after_s"] = pausa
                reinicio = fallo.get("reset_at")
                if isinstance(reinicio, str) and reinicio:
                    error["reset_at"] = reinicio
                emitir({"error": error})
                raise SystemExit(proceso.returncode)
            emitir({"resultado": json.loads(final.read_text())})
    finally:
        if proceso is not None:
            try:
                os.killpg(proceso.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            try:
                proceso.wait(timeout=5)
            except subprocess.TimeoutExpired:
                os.killpg(proceso.pid, signal.SIGKILL)
                proceso.wait()
            try:
                os.killpg(proceso.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
"""


def validar(resultado, sha):
    if not isinstance(resultado, dict) or set(resultado) != {
        "sha",
        "veredicto",
        "bloqueantes",
        "notas",
    }:
        raise ValueError("Forma invalida del veredicto remoto")
    if resultado["sha"] != sha or resultado["veredicto"] not in ("APROBADO", "CAMBIOS"):
        raise ValueError("SHA o veredicto remoto invalido")
    if not isinstance(resultado["notas"], list) or not all(
        isinstance(nota, str) for nota in resultado["notas"]
    ):
        raise ValueError("Notas remotas invalidas")
    if not isinstance(resultado["bloqueantes"], list) or not all(
        isinstance(b, dict)
        and set(b) == {"texto", "repro", "arreglo"}
        and all(isinstance(v, str) for v in b.values())
        for b in resultado["bloqueantes"]
    ):
        raise ValueError("Bloqueantes remotos invalidos")


MENSAJES = {
    "auth": "Authentication required",
    "cuota": "quota exceeded",
    "rate_limit": "rate limit reached",
    "modelo_no_disponible": "model is disabled for this account",
    "transitorio": "connection error",
    "falla": "Codex termino con codigo",
}
_RESET_AT = re.compile(r"\d{4}-\d\d-\d\dT\d\d:\d\d(?::\d\d)?Z?")


def _segundos(pausa):
    if pausa == int(pausa):
        return str(int(pausa))
    return format(pausa, "f").rstrip("0").rstrip(".")


def error_publico(error):
    if (
        not isinstance(error, dict)
        or not {"categoria", "codigo"}
        <= set(error)
        <= {"categoria", "codigo", "retry_after_s", "reset_at"}
        or error["categoria"] not in MENSAJES
        or type(error["codigo"]) is not int
    ):
        raise ValueError("Error remoto invalido")
    pausa = error.get("retry_after_s")
    if "retry_after_s" in error and (
        type(pausa) not in (int, float)
        or pausa != pausa
        or pausa < 0
        or pausa == float("inf")
    ):
        raise ValueError("Error remoto invalido")
    reinicio = error.get("reset_at")
    if "reset_at" in error and (
        not isinstance(reinicio, str) or _RESET_AT.fullmatch(reinicio) is None
    ):
        raise ValueError("Error remoto invalido")
    categoria = error["categoria"]
    texto = MENSAJES[categoria]
    if categoria == "falla":
        texto += " " + str(error["codigo"])
    if categoria in ("cuota", "rate_limit") and "retry_after_s" in error:
        texto += "; retry after " + _segundos(pausa) + " seconds"
    if categoria == "cuota" and "reset_at" in error:
        texto += "; resets at " + reinicio
    return categoria + ": " + texto


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--brief", required=True, type=Path)
    parser.add_argument("--result", required=True, type=Path)
    args = parser.parse_args(argv)
    sha = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
    archive = subprocess.check_output(["git", "archive", "--format=tar.gz", sha])
    entrada = {
        "sha": sha,
        "archive": base64.b64encode(archive).decode(),
        "prompt": args.brief.read_text(encoding="utf-8"),
    }
    ssh = (
        str(Path(os.environ["ProgramFiles"]) / "Git" / "usr" / "bin" / "ssh.exe")
        if os.name == "nt"
        else "ssh"
    )
    proceso = subprocess.Popen(
        [
            ssh,
            "-T",
            "-oBatchMode=yes",
            "-oConnectTimeout=10",
            "mac",
            "/opt/homebrew/bin/python3 -c " + shlex.quote(REMOTO),
        ],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
    )
    resultado = None
    error = None
    try:
        proceso.stdin.write(json.dumps(entrada) + "\n")
        proceso.stdin.flush()
        for linea in proceso.stdout:
            evento = json.loads(linea)
            if isinstance(evento, dict) and set(evento) == {"latido"}:
                print(linea.rstrip(), flush=True)
            elif (
                isinstance(evento, dict)
                and set(evento) == {"resultado"}
                and resultado is None
            ):
                resultado = evento["resultado"]
            elif (
                isinstance(evento, dict) and set(evento) == {"error"} and error is None
            ):
                error = evento["error"]
            else:
                raise ValueError("Evento SSH inesperado")
        codigo = proceso.wait()
        if error is not None:
            print(error_publico(error), file=sys.stderr, flush=True)
            raise RuntimeError("Fallo del proveedor remoto; no se guarda un veredicto")
        if codigo != 0:
            raise RuntimeError("Fallo SSH o Codex remoto; no se guarda un veredicto")
        validar(resultado, sha)
        temporal = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w", dir=args.result.parent, delete=False
            ) as f:
                temporal = Path(f.name)
                json.dump(resultado, f)
                f.write("\n")
            os.replace(temporal, args.result)
        finally:
            if temporal is not None:
                temporal.unlink(missing_ok=True)
    finally:
        proceso.stdin.close()
        proceso.stdout.close()
        if proceso.poll() is None:
            proceso.terminate()
            try:
                proceso.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proceso.kill()
                proceso.wait()


if __name__ == "__main__":
    main()
