"""Reversa desde recibos, sin arrancar ni importar el motor que se retira."""

from __future__ import annotations

import argparse
import json
import runpy
import subprocess
import sys
from pathlib import Path

_cerrojo_flota = runpy.run_path(
    str(Path(__file__).resolve().parents[1] / "motor/flota/cerrojo.py")
)["_cerrojo_flota"]


def leer(path: Path):
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


def ocupada(home: Path, vigente: str, anterior: str) -> bool:
    trabajos = [leer(p) for p in (home / "trabajos").glob("*.json")]
    if not any(d and d.get("generacion") == vigente for d in trabajos):
        return False
    # Sólo el runtime anterior observa identidades y árboles. Nunca el destino roto.
    raiz = home / "generaciones" / anterior
    consulta = """
import json, sys
from pathlib import Path
raiz = Path(sys.argv[1]).resolve()
sys.path.insert(0, str(raiz))
from motor import plataforma
from motor.encargos import sesion
assert raiz in Path(sesion.__file__).resolve().parents
for doc in json.load(sys.stdin):
    if doc.get("reserva") and not doc.get("pid"):
        print("true")
        break
    if not doc.get("pid"):
        continue
    if doc.get("directorio"):
        estado = sesion.observar(Path(doc["directorio"]), doc)
    else:
        estado = plataforma.estado_proc(doc)
    if estado in ("vivo", "incierto"):
        print("true")
        break
else:
    print("false")
"""
    try:
        r = subprocess.run(
            [sys.executable, "-I", "-B", "-c", consulta, str(raiz), str(home), vigente],
            input=json.dumps(
                [d for d in trabajos if d and d.get("generacion") == vigente]
            ),
            capture_output=True,
            text=True,
            timeout=30,
            check=True,
        )
        respuesta = json.loads(r.stdout)
        if not isinstance(respuesta, bool):
            raise TypeError("observacion invalida")
        return respuesta
    except (OSError, ValueError, TypeError, subprocess.SubprocessError) as exc:
        raise RuntimeError(
            "No se pudo observar el trabajo con el runtime anterior; se conserva la instalacion"
        ) from exc


def revertir(home: Path) -> str:
    with _cerrojo_flota(home):
        pend_path, inst_path = (
            home / "instalacion-pendiente.json",
            home / "instalacion.json",
        )
        pend, inst = leer(pend_path), leer(inst_path)
        if pend is None and inst is None:
            return "nada"
        actual = home / "actual"
        comp_destino = ((inst or {}).get("destino") or {}).get("codigo", "")
        vigente = actual.resolve().name if actual.is_symlink() else comp_destino
        if pend is not None and inst is not None:
            pend_anterior = (pend.get("anterior") or {}).get("codigo", "")
            usa_pend = bool(pend_anterior and pend_anterior in (vigente, comp_destino))
        else:
            usa_pend = pend is not None
        recibo = pend if usa_pend else inst
        anterior = (recibo.get("anterior") or {}).get("codigo", "")
        destino = (recibo.get("destino") or {}).get("codigo", "")
        if not anterior:
            return "nada"
        if vigente != anterior and ocupada(home, vigente, anterior):
            return "ocupada " + vigente
        motor = home / "motor"
        if (motor / ".git").exists():
            subprocess.run(
                [
                    "git",
                    "-C",
                    str(motor),
                    "-c",
                    "core.hooksPath=/dev/null",
                    "checkout",
                    "-q",
                    "--detach",
                    anterior,
                ],
                check=True,
                capture_output=True,
                text=True,
            )
        previa = home / "generaciones" / anterior
        if actual.is_symlink():
            actual.unlink()
        if previa.exists():
            actual.symlink_to(previa, target_is_directory=True)
        if not usa_pend:
            historial = home / "historial"
            historial.mkdir(exist_ok=True)
            archivo = historial / ("instalacion-" + destino + ".json")
            if archivo.exists():
                archivo = historial / (
                    "instalacion-"
                    + destino
                    + "-"
                    + str(recibo.get("intento") or "revertida")
                    + ".json"
                )
            inst_path.replace(archivo)
        if pend is not None:
            pend_path.unlink()
        return "revertido " + anterior


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--home", required=True, type=Path)
    args = ap.parse_args()
    if not args.home.is_dir():
        ap.error("falta el home de ensayo")
    try:
        print(revertir(args.home.resolve()))
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
