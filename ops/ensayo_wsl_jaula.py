"""Ensayo en la PC: bash, git push, permisos y parada DENTRO de la jaula de WSL.

Lo corre el operador en Windows, no el implementador (que trabaja en la Mini). Usa el mismo
camino que el motor: `jaula_wsl.ejecutar`, el guardián de `jaula_linux` y la jaula de
bubblewrap. No llama a ningún modelo, salvo con `--llamar`, que hace una sola pregunta corta.

    python ops\\ensayo_wsl_jaula.py [--llamar]

Cada línea es `- caso: ok` o `- caso: FALLA <detalle>`. Sale con 0 si todo está en ok.
La distro es la de `MOTOR_WSL_DISTRO` (vacía, la distro por omisión de WSL).
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from motor.encargos.aislamiento import aislar, sondear
from motor.plataforma import jaula_wsl

PUSH = (
    'cd "$TMPDIR" && rm -rf a b.git && git init -q a && git init -q --bare b.git && cd a && '
    "git -c user.email=x@x -c user.name=x commit -q --allow-empty -m x && "
    "git push -q ../b.git HEAD:main && echo ok"
)

# (caso, el revisor monta el espacio de solo lectura, script de sh que debe terminar en `ok`)
CASOS = [
    ("bash", False, "/bin/bash -c 'echo ok'"),
    ("sh", False, "/bin/sh -c 'echo ok'"),
    ("git push a un bare local (responsable)", False, PUSH),
    ("git push a un bare local (revisor, en su temporal)", True, PUSH),
    ("el responsable escribe en el espacio", False, "echo x > ./f && rm ./f && echo ok"),
    ("el revisor NO escribe en el espacio", True, "echo x > ./f 2>/dev/null && echo escribio || echo ok"),
    ("nadie escribe fuera del espacio y del runtime", False, "echo x > /etc/f 2>/dev/null || echo ok"),
    ("no se ve el disco de Windows fuera de lo concedido", False, "[ ! -e /mnt/c/Windows ] && echo ok"),
    ("no se ve el home de WSL", False, '[ -z "$(ls /home 2>/dev/null)" ] && echo ok'),
]


def _en_windows() -> bool:
    return sys.platform == "win32"


def _resultado(codigo: int, salida: str, error: str) -> tuple[bool, str]:
    ultima = (salida or "").split()[-1:]
    ok = codigo == 0 and ultima == ["ok"]
    return ok, f"codigo={codigo} salida={' '.join((salida or '').split())[:200]!r} error={' '.join((error or '').split())[:300]!r}"


def _parada(base: Path, espacio: Path, runtime: Path) -> tuple[bool, str]:
    """Lanza un agente con dos `sleep`, lo cierra desde Windows y comprueba que no queda nada."""
    jaula = jaula_wsl.jaula_de(espacio, runtime)
    control = base / "control-parada"
    control.mkdir()
    guion = espacio / "dormir.sh"
    guion.write_text("#!/bin/sh\nsleep 4399 &\nsleep 4398\n", encoding="utf-8", newline="\n")
    argv = jaula_wsl.argv_guardian(jaula, ["/bin/sh", str(jaula_wsl.a_wsl(str(guion)))], {}, control=control, cwd=espacio)
    p = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                         env=jaula_wsl.entorno_windows())
    try:
        fin = time.monotonic() + 30
        while time.monotonic() < fin:
            if (jaula_wsl.control_de(control) or {}).get("estado") == "activo":
                break
            time.sleep(0.2)
        else:
            return False, "la jaula no llego a estar activa en 30 s"
        vivo = jaula_wsl.estado_en_distro(control, jaula["distro"])
        cierre = jaula_wsl.cerrar_en_distro(control, jaula["distro"])
        distro = ["-d", jaula["distro"]] if jaula["distro"] else []
        resto = subprocess.run(
            ["wsl.exe", *distro, "--exec", "/bin/sh", "-c", "pgrep -f 'sleep 439[89]' >/dev/null && echo vivos || echo ninguno"],
            capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=30, env=jaula_wsl.entorno_windows(),
        ).stdout.strip()
        ok = vivo == "vivo" and cierre == "ausente" and resto == "ninguno"
        return ok, f"antes={vivo} cierre={cierre} procesos_de_la_jaula={resto}"
    finally:
        if p.poll() is None:
            p.kill()
        p.wait()


def main(argv: list | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--llamar", action="store_true", help="una pregunta corta a Claude dentro de la jaula")
    args = ap.parse_args(argv)
    if not _en_windows():
        print("Este ensayo corre en la PC (Windows): lanza wsl.exe y el supervisor solo usa WSL ahi.")
        return 2
    fallos = 0

    def informa(caso: str, ok: bool, detalle: str) -> None:
        nonlocal fallos
        fallos += not ok
        print(f"- {caso}: {'ok' if ok else 'FALLA'} {detalle if not ok else ''}".rstrip())

    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp).resolve()
        ident = "enc_ensayo" + uuid.uuid4().hex[:8]
        espacio = base / "espacio" / ident
        runtime = base / "runtime" / ident
        espacio.mkdir(parents=True)
        runtime.mkdir(parents=True)
        for n, (caso, revisor, script) in enumerate(CASOS):
            try:
                jaula = jaula_wsl.jaula_de(espacio, runtime, lectura=revisor)
                codigo, salida, error = jaula_wsl.ejecutar(
                    jaula, ["/bin/sh", "-c", script], {}, control=base / f"control-{n}", cwd=espacio, plazo=60
                )
                informa(caso, *_resultado(codigo, salida, error))
            except Exception as exc:  # noqa: BLE001  el ensayo informa, no se cae
                informa(caso, False, f"{type(exc).__name__}: {exc}")
        try:
            comando = aislar(["claude", "--version"], espacio, runtime, suscripcion=True, agente="claude")
            codigo, salida, error = jaula_wsl.ejecutar(
                comando.jaula, comando.argv, {}, control=base / "control-claude", cwd=espacio, plazo=90
            )
            informa("claude --version con la suscripcion de la distro", codigo == 0 and bool(salida.strip()),
                    f"codigo={codigo} error={' '.join(error.split())[:300]!r}")
            if args.llamar:
                cmd = [*comando.argv[:1], "--safe-mode", "--print", "Responde solo: ok"]
                codigo, salida, error = jaula_wsl.ejecutar(
                    comando.jaula, cmd, {}, control=base / "control-llamada", cwd=espacio, plazo=180
                )
                informa("una pregunta corta a Claude dentro de la jaula", codigo == 0 and "ok" in salida.lower(),
                        f"codigo={codigo} salida={' '.join(salida.split())[:200]!r} error={' '.join(error.split())[:300]!r}")
        except Exception as exc:  # noqa: BLE001
            informa("claude --version con la suscripcion de la distro", False, f"{type(exc).__name__}: {exc}")
        try:
            informa("parar cierra el arbol dentro de la distro", *_parada(base, espacio, runtime))
        except Exception as exc:  # noqa: BLE001
            informa("parar cierra el arbol dentro de la distro", False, f"{type(exc).__name__}: {exc}")
        try:
            sonda = sondear(espacio, runtime / "sonda", (), "claude")
            informa("sondear(claude) antes de lanzar", sonda.ok, f"causa={sonda.causa!r} condicion={sonda.condicion!r}")
        except Exception as exc:  # noqa: BLE001
            informa("sondear(claude) antes de lanzar", False, f"{type(exc).__name__}: {exc}")
    print("TODO EN ORDEN" if not fallos else f"{fallos} FALLA(S)")
    return 1 if fallos else 0


if __name__ == "__main__":
    sys.exit(main())
