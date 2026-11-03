"""Ejecuta un pedido normal o comprueba procesos y stdin en un host de ensayo.

El ensayo anterior por ejemplos y confirmaciones inyectadas fue retirado.
La receta vigente está en docs/encargos-ensayo.md.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))

from motor import plataforma
from motor.encargos.registro import exigir_base


def motor(
    base: Path, *args: str, entrada: str | None = None
) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-B", "-m", "motor", *args, "--base", str(base)],
        input=entrada,
        capture_output=True,
        text=True,
        cwd=RAIZ,
        env={**os.environ, "PYTHONPATH": str(RAIZ)},
        timeout=45,
        check=False,
    )


def nativo(base: Path) -> int:
    base = exigir_base(base)
    marca_hijo = "hijo-" + uuid.uuid4().hex + ".pid"
    codigo = (
        "import pathlib,subprocess,sys,time\n"
        "p=subprocess.Popen([sys.executable,'-c','import time; time.sleep(120)'])\n"
        "pathlib.Path('hijo.pid').write_text(str(p.pid))\n"
        "time.sleep(120)\n"
    )
    codigo = codigo.replace("'hijo.pid'", repr(marca_hijo))
    proc = plataforma.lanzar_separado(
        [sys.executable, "-B", "-c", codigo],
        base,
        os.environ.copy(),
        base / "proceso.log",
    )
    hijo = 0
    try:
        fin = time.time() + 10
        while time.time() < fin:
            path = base / marca_hijo
            if path.is_file() and path.read_text().strip():
                hijo = int(path.read_text())
                break
            time.sleep(0.1)
        vivo = plataforma.estado_proc(proc) == "vivo"
    finally:
        plataforma.matar_proc(int(proc["pid"]), str(proc.get("inicio") or ""))
        plataforma.cerrar_huerfanos(
            int(proc["pid"]), str(proc.get("inicio") or ""), proc.get("job") or ""
        )
    time.sleep(0.5)
    corrida = motor(
        base / "registro",
        "encargos-aplicar",
        entrada=json.dumps(
            {
                "op": "encargar",
                "clave": "nativo-1",
                "texto": "pedido por stdin, comillas ' y $()",
            }
        ),
    )
    resultado = {
        "os": os.name,
        "vivo": vivo,
        "hijo": hijo,
        "hijo_muerto": hijo > 0 and not plataforma.pid_vivo(hijo),
        "padre_muerto": plataforma.estado_proc(proc) != "vivo",
        "cli": corrida.returncode,
        "salida": corrida.stdout,
        "error": corrida.stderr,
        "alcance": "procesos nativos y transporte; no acredita aislamiento del trabajador",
    }
    (base / "informe-nativo.json").write_text(
        json.dumps(resultado, ensure_ascii=False, indent=2)
    )
    print(json.dumps(resultado, ensure_ascii=False), flush=True)
    return (
        0
        if vivo
        and resultado["hijo_muerto"]
        and resultado["padre_muerto"]
        and corrida.returncode == 0
        else 1
    )


def encargo(args) -> int:
    base = exigir_base(args.base or Path(tempfile.mkdtemp(prefix="motor-encargo-")))
    if args.autoridad:
        (base / "autoridad.json").write_bytes(args.autoridad.read_bytes())
    pedido = ["encargar", "--clave", args.clave]
    pedido.extend(
        ["--archivo", str(args.archivo.resolve())]
        if args.archivo
        else ["--texto", args.texto]
    )
    alta = motor(base, *pedido)
    if alta.returncode != 0:
        print(alta.stdout + alta.stderr, file=sys.stderr)
        return 1
    encargo_id = json.loads(alta.stdout)["encargo"]
    fin = time.time() + args.tiempo
    ultima = None
    salida = {}
    try:
        while time.time() < fin:
            corrida = motor(
                base,
                "supervisor",
                "--una-vez",
                "--agente",
                args.agente,
                "--umbral",
                str(args.umbral),
            )
            if corrida.returncode != 0:
                raise RuntimeError(corrida.stdout + corrida.stderr)
            consulta = motor(base, "ver", encargo_id)
            if consulta.returncode != 0:
                raise RuntimeError(consulta.stdout + consulta.stderr)
            salida = json.loads(consulta.stdout)
            if salida != ultima:
                print(json.dumps(salida, ensure_ascii=False), flush=True)
                ultima = salida
            if salida["estado"] in (
                "terminado",
                "detenido",
                "cancelado",
                "necesita_usuario",
            ):
                break
            time.sleep(args.intervalo)
    finally:
        if salida.get("estado") != "terminado":
            parada = motor(base, "parar", encargo_id, "--clave", "cierre-del-ensayo")
            if parada.returncode == 0:
                salida = json.loads(parada.stdout)
        (base / "resultado-ensayo.json").write_text(
            json.dumps(salida, ensure_ascii=False, indent=2)
        )
        print(str(base), flush=True)
    return 0 if salida.get("estado") == "terminado" else 1


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--remoto-local", type=Path, help="solo procesos nativos e ingreso por stdin"
    )
    ap.add_argument("--base", type=Path)
    ap.add_argument(
        "--autoridad", type=Path, help="configuracion del destino de ensayo"
    )
    pedido = ap.add_mutually_exclusive_group()
    pedido.add_argument("--texto")
    pedido.add_argument("--archivo", type=Path)
    ap.add_argument("--clave", default="ensayo-normal-1")
    ap.add_argument("--agente", choices=("codex", "grok"), default="codex")
    ap.add_argument("--tiempo", type=float, default=1200)
    ap.add_argument("--umbral", type=float, default=300)
    ap.add_argument("--intervalo", type=float, default=2)
    args = ap.parse_args()
    if args.remoto_local:
        return nativo(args.remoto_local)
    if not args.archivo and not args.texto:
        ap.error("indica --texto, --archivo o --remoto-local")
    return encargo(args)


if __name__ == "__main__":
    raise SystemExit(main())
