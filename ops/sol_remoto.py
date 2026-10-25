#!/usr/bin/env python3
"""Invoca el Sol de la cuenta del Mac sobre un archivo del SHA local."""

import argparse
import base64
import json
import os
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
        with prompt.open() as fuente, (raiz / "codex.log").open("w+") as log:
            proceso = subprocess.Popen([
                "/opt/homebrew/bin/codex", "exec", "--model", "gpt-5.6-sol",
                "-c", 'model_reasoning_effort="medium"', "--sandbox", "read-only",
                "--skip-git-repo-check", "--output-schema", str(schema),
                "--output-last-message", str(final), "-"],
                cwd=checkout, stdin=fuente, stdout=log, stderr=subprocess.STDOUT,
                start_new_session=True,
                env={**os.environ, "PATH": "/opt/homebrew/bin:" + os.environ.get("PATH", "")})
            while proceso.poll() is None:
                emitir({"latido": time.time()})
                if select.select([sys.stdin], [], [], 5)[0] and not os.read(0, 1):
                    raise RuntimeError("Se cerro la conexion SSH")
            if proceso.returncode:
                log.seek(0)
                fallo = log.read().lower()
                categoria = "desconocido"
                patrones = {
                    "modelo_no_disponible": ("model is disabled", "model_not_found", "model is not supported", "not included in your", "does not have access to model"),
                    "auth": ("authentication required", "unauthorized", "invalid api key", "not logged in", "token expired", "please log in"),
                    "cuota": ("quota exceeded", "insufficient_quota", "usage limit", "usage_limit_reached", "rate limit", "out of credits"),
                    "transitorio": ("connection error", "connection reset", "timed out", "timeout", "network error", "temporarily unavailable"),
                }
                for tipo, palabras in patrones.items():
                    if any(palabra in fallo for palabra in palabras):
                        categoria = tipo
                        break
                emitir({"error": {"categoria": categoria, "codigo": proceso.returncode}})
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
        "prompt": args.brief.read_text(),
    }
    proceso = subprocess.Popen(
        [
            "ssh",
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
            mensajes = {
                "auth": "Authentication required",
                "cuota": "quota exceeded",
                "modelo_no_disponible": "model is disabled for this account",
                "transitorio": "connection error",
                "desconocido": "Codex termino con codigo",
            }
            if (
                not isinstance(error, dict)
                or set(error) != {"categoria", "codigo"}
                or error["categoria"] not in mensajes
                or type(error["codigo"]) is not int
            ):
                raise ValueError("Error remoto invalido")
            categoria = error["categoria"]
            texto = mensajes[categoria]
            if categoria == "desconocido":
                texto += " " + str(error["codigo"])
            print(categoria + ": " + texto, file=sys.stderr, flush=True)
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
