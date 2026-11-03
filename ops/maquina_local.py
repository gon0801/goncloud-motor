"""Origen privado de maquina.toml y del enlace conf durante instalación y reversa."""

import base64
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys


def _guarda(path, raw, mode=0o600):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "wb") as f:
        f.write(raw)
        f.flush()
        os.fsync(f.fileno())
    os.chmod(tmp, mode)
    os.replace(tmp, path)


def _enlace(path):
    junction = getattr(os.path, "isjunction", lambda p: False)(path)
    if path.is_symlink() or junction:
        target = os.readlink(path)
        if os.name == "nt" and target.startswith("\\\\?\\"):
            target = target[4:]
        return {"destino": target, "junction": junction}
    if os.path.lexists(path):
        raise ValueError("conf no es un enlace; no sustituyo su contenido")
    return None


def valida(home, ref):
    name = ref["archivo"]
    if not re.fullmatch(r"privado/origen-[A-Za-z0-9_-]+\.json", name):
        raise ValueError("referencia de origen inválida")
    raw = (home / name).read_bytes()
    if hashlib.sha256(raw).hexdigest() != ref["sha256"]:
        raise ValueError("el origen privado cambió")
    data = json.loads(raw)
    toml = data["toml"]
    if toml is not None:
        expected = name.replace("origen-", "maquina-").replace(".json", ".toml")
        if toml["archivo"] != expected:
            raise ValueError("referencia del TOML inválida")
        if hashlib.sha256((home / expected).read_bytes()).hexdigest() != toml["sha256"]:
            raise ValueError("el TOML privado cambió")
    _enlace(home / "conf")
    return data


def captura(home, intento, reintento=False):
    if not re.fullmatch(r"[A-Za-z0-9_-]+", intento):
        raise ValueError("intento inválido")
    name = f"privado/origen-{intento}.json"
    meta = home / name
    if meta.exists():
        if reintento:
            pending = json.loads((home / "instalacion-pendiente.json").read_bytes())
            ref = pending["anterior"]["maquina"]
            if ref["archivo"] != name:
                raise ValueError("el origen privado no es el del intento pendiente")
        else:
            ref = {"archivo": name, "sha256": hashlib.sha256(meta.read_bytes()).hexdigest()}
        valida(home, ref)
        return ref
    if reintento:
        raise ValueError("el intento pendiente no tiene su origen privado; no lo reinvento")
    data = {"toml": None, "enlace": _enlace(home / "conf")}
    toml = home / "maquina.toml"
    if os.path.lexists(toml):
        raw = toml.read_bytes()
        backup = f"privado/maquina-{intento}.toml"
        _guarda(home / backup, raw)
        data["toml"] = {"archivo": backup, "sha256": hashlib.sha256(raw).hexdigest(),
                        "modo": stat.S_IMODE(toml.stat().st_mode)}
    raw = json.dumps(data, ensure_ascii=False, sort_keys=True).encode()
    _guarda(meta, raw)
    return {"archivo": name, "sha256": hashlib.sha256(raw).hexdigest()}


def restaura(home, ref):
    data = valida(home, ref)
    toml = home / "maquina.toml"
    if data["toml"] is None:
        toml.unlink(missing_ok=True)
    else:
        before = data["toml"]
        _guarda(toml, (home / before["archivo"]).read_bytes(), before["modo"])
    path = home / "conf"
    actual = _enlace(path)
    target = data["enlace"]
    if actual != target:
        if actual is not None:
            if actual["junction"]:
                os.rmdir(path)
            else:
                path.unlink()
        if target is not None:
            if target["junction"]:
                subprocess.run(["cmd.exe", "/d", "/c", "mklink", "/j",
                                str(path), target["destino"]], check=True,
                               stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE)
            else:
                path.symlink_to(target["destino"], target_is_directory=True)
    if _enlace(path) != target:
        raise ValueError("el enlace anterior no quedó restaurado")
    if data["toml"] is not None:
        if hashlib.sha256(toml.read_bytes()).hexdigest() != data["toml"]["sha256"]:
            raise ValueError("el TOML anterior no quedó restaurado")


def main():
    home = Path.home() / ".motor"
    action, value = sys.argv[1:3]
    if action == "captura":
        ref = captura(home, value, sys.argv[3] == "si")
        print(base64.b64encode(json.dumps(ref).encode()).decode())
    else:
        ref = json.loads(base64.b64decode(value))
        if action == "valida":
            valida(home, ref)
        elif action == "restaura":
            restaura(home, ref)
        else:
            raise ValueError("operación inválida")


if __name__ == "__main__":
    main()
