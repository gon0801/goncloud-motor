"""Juzga un encargo ya terminado contra los puntos de David y lo esperado.

Uso, siempre en solo lectura:

    python3 ops/canario_encargos.py <encargo> [--base ~/.motor-encargos]
        [--esperado ops/canario_encargos.esperado.json] [--arbol <ruta>]
        [--sha-desplegado <sha>] [--archivo-desplegado <ruta> --marca <texto>]
        [--detencion <encargo> [--archivo-detencion <ruta>]]

`--detencion` nombra el encargo cuyo checklist de despliegue falla a propósito:
de él sale la fila "se detiene". `--archivo-detencion` es el archivo que su
despliegue deja y su rollback quita; su marca es el id de ese encargo.

Tres capas, cada una en su módulo:

- `canario_observar.observar` lee el registro, GitHub, el disco de la base, el
  clon del servicio y los procesos, y devuelve una `Observacion`
  (`canario_observacion`).
- `canario_juzgar.juzgar` es pura: de la observación y lo esperado saca una
  `Fila` por cada entrada de `TABLA`, con `pasa`, `falla` o `no medido`.
- El archivo de lo esperado dice qué resultado se espera en cada fila y los
  topes. Una fila que pasaba y falla es regresión; una que empieza a pasar
  obliga a actualizar el archivo.

Sale con 0 solo si cada fila da lo esperado, con 1 si alguna no, y con 2 si no
puede juzgar: el encargo no está en el registro o el archivo de lo esperado no
tiene la forma.

La salida trae ids, ramas y números de PR: no va a un log público.
"""

from __future__ import annotations

import argparse
import io
import sys
from datetime import datetime, timezone
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
if str(RAIZ) not in sys.path:
    sys.path.insert(0, str(RAIZ))

from motor.github import GhGitHub  # noqa: E402
from ops.canario_juzgar import ESPERADO, Fila, juzgar, leer_esperado  # noqa: E402
from ops.canario_observar import LectorPR, observar  # noqa: E402


def tabla(filas: tuple[Fila, ...]) -> str:
    lineas = [
        "| Fila | Punto | Resultado | Esperado | Observado | Fuente |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    lineas += [
        f"| {f.nombre} | {f.punto} | {f.resultado} | {f.esperado} | {f.observado} | {f.fuente} |"
        for f in filas
    ]
    distintas = [f for f in filas if f.discrepancia]
    if distintas:
        lineas.append("")
        lineas.append("No coincide con lo esperado:")
        lineas += [f"- {f.nombre}: {f.discrepancia}" for f in distintas]
    else:
        lineas.append("")
        lineas.append("Coincide con lo esperado.")
    return "\n".join(lineas)


def _ahora() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def main(argv: list[str] | None = None, gh: LectorPR | None = None) -> int:
    for flujo in (sys.stdout, sys.stderr):
        if isinstance(flujo, io.TextIOWrapper):
            flujo.reconfigure(encoding="utf-8")
    p = argparse.ArgumentParser(
        description="Juzga un encargo terminado contra la tabla del canario.",
        epilog="La salida trae ids, ramas y números de PR: no la mandes a un log público.",
    )
    p.add_argument("encargo")
    p.add_argument("--base", type=Path, default=Path.home() / ".motor-encargos")
    p.add_argument("--esperado", type=Path, default=ESPERADO)
    p.add_argument("--arbol", type=Path, help="árbol del servicio; por omisión, el de la última pasada")
    p.add_argument("--sha-desplegado", default="", help="por omisión, el de instalacion.json")
    p.add_argument("--archivo-desplegado", type=Path)
    p.add_argument("--marca", default="")
    p.add_argument("--detencion", default="", help="el encargo cuyo checklist falla a propósito")
    p.add_argument("--archivo-detencion", type=Path)
    args = p.parse_args(argv)
    if (args.archivo_desplegado is None) != (not args.marca):
        p.error("--archivo-desplegado y --marca van juntos")
    if args.archivo_detencion is not None and not args.detencion:
        p.error("--archivo-detencion pide --detencion")
    try:
        esperado = leer_esperado(args.esperado)
    except (OSError, ValueError) as exc:
        print(f"canario: {exc}", file=sys.stderr)
        return 2
    if not (args.base / "registro.sqlite").is_file():
        print(f"canario: no existe {args.base / 'registro.sqlite'}", file=sys.stderr)
        return 2
    obs = observar(
        args.base, args.encargo, gh or GhGitHub(),
        hora=_ahora(),
        arbol=args.arbol,
        sha_desplegado=args.sha_desplegado,
        archivo=(args.archivo_desplegado, args.marca) if args.archivo_desplegado else None,
        detencion=args.detencion,
        archivo_detencion=args.archivo_detencion,
    )
    if obs.pliegue is None:
        print(f"canario: el encargo {args.encargo} no está en el registro", file=sys.stderr)
        return 2
    filas = juzgar(obs, esperado)
    print(f"Encargo {args.encargo}, observado {obs.hora}.\n")
    print(tabla(filas))
    return 1 if any(f.discrepancia for f in filas) else 0


if __name__ == "__main__":
    raise SystemExit(main())
