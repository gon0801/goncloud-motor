"""Conserva el backend de la cuenta Meta que administra el cliente oficial."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path


def cuenta_muse(*, exportar: bool = False) -> dict:
    path = Path.home() / ".config/muse/auth.json"
    datos = json.loads(path.read_text(encoding="utf-8"))
    cuenta = datos.get("providers", {}).get("meta", {})
    if (
        not isinstance(cuenta, dict)
        or cuenta.get("subs_active") is not True
        or cuenta.get("api_base_url", "https://api.meta.ai/v1").rstrip("/")
        != "https://api.meta.ai/v1"
    ):
        raise RuntimeError("Muse necesita la suscripcion Meta del host")
    if cuenta.get("storage") == "keychain":
        if sys.platform != "darwin" or cuenta.get("mechanism") != "oauth":
            raise RuntimeError("el backend de la cuenta Meta no esta disponible")
    elif not isinstance(cuenta.get("access_token"), str) or not cuenta["access_token"]:
        raise RuntimeError("falta el acceso de la suscripcion Meta")
    campos = (
        "mechanism",
        "obtained_via",
        "api_base_url",
        "subs_active",
        "storage",
        "access_token",
    )
    salida = {k: cuenta[k] for k in campos if k in cuenta}
    if exportar and cuenta.get("storage") == "keychain":
        try:
            resultado = subprocess.run(
                [
                    "/usr/bin/security",
                    "find-generic-password",
                    "-w",
                    "-s",
                    "ai.meta.dev.credentials",
                    "-a",
                    "meta",
                ],
                capture_output=True,
                text=True,
                timeout=12,
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            raise RuntimeError(
                "macOS necesita autorizar la lectura de la credencial Meta en el host"
            ) from None
        if resultado.returncode:
            raise RuntimeError(
                "macOS necesita autorizar la lectura de la credencial Meta en el host"
            )
        raw = resultado.stdout.strip()
        try:
            secreto = json.loads(raw)
        except ValueError:
            secreto = raw
        token = secreto.get("access_token") if isinstance(secreto, dict) else secreto
        if not isinstance(token, str) or not re.fullmatch(
            r"[A-Za-z0-9._~+/-]+=*", token
        ):
            raise RuntimeError(
                "el formato de la credencial Meta del host no es compatible"
            )
        salida.update(storage="file", access_token=token)
    return salida


def ejecutable_muse() -> Path:
    ruta = shutil.which("muse")
    if not ruta:
        raise RuntimeError("no se encontro el cliente Muse")
    path = Path(ruta).resolve()
    if os.name != "nt":
        version = path.parent / ".muse-version"
        if version.is_file():
            valor = version.read_text(encoding="utf-8").strip()
            if not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+-R[0-9]+(?:\.[0-9]+)?", valor):
                raise RuntimeError("la version instalada de Muse es invalida")
            nativo = path.with_name("muse-bin-" + valor)
            if not nativo.is_file() or not os.access(nativo, os.X_OK):
                raise RuntimeError("falta el binario instalado de Muse")
            return nativo
    return path
