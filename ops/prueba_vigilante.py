"""Prueba real del contrato de lanzamiento del vigilante de un agente.

    python3 ops/prueba_vigilante.py [--raiz <carpeta del motor>]

Prueba que el vigilante que corre a un agente es el del motor de --raiz (por omisión, el de la
carpeta que contiene esta ops/), lanzado como lo lanza start_attempt: env_base del motor con los
shims delante en el PATH y comando_runwrap (python -P -m motor.runwrap desde la carpeta del
intento). Monta un home temporal (nunca ~/.motor, donde corren otros planes), un repo y una
carpeta de intento que traen cada uno su señuelo en motor/runwrap.py, y un agente falso. Sale 0
solo si ningún señuelo corrió, el agente corrió sin MOTOR_LLAMADA y con los shims del motor en el
PATH (sus llamadas a otros CLIs pasan por ellos y cuentan en los topes; su propio ejecutable se
resuelve sin ellos), y el libro de cuentas del home temporal tuvo una sola entrada mientras corría
(la suya) y ninguna al terminar.

No pasa --wip-rama ni --entrega-rama: eso ejercita el trabajo git del vigilante, no su contrato
de lanzamiento, y lo cubren tests/test_runwrap.py y tests/test_entrega.py. Borra todo lo temporal
al salir.
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
from types import SimpleNamespace

# El señuelo de A.1: si algo corre este runwrap en vez del del motor, la prueba lo ve.
SENUELO = (
    "import sys\n"
    "from pathlib import Path\n"
    "Path(sys.argv[sys.argv.index('--dir') + 1], 'señuelo').write_text('x')\n"
    "sys.exit(9)\n"
)

# El agente falso. El home sale del contrato real (MOTOR_ATTEMPT_DIR y sus cupos.json: de ahí
# cuelgan los shims y la anotación en el libro), no de una variable propia. Y runwrap lo anota en
# el libro después de lanzarlo: por eso espera antes de leer.
AGENTE = """\
import json
import os
import time
from pathlib import Path

cupos = json.loads(
    (Path(os.environ["MOTOR_ATTEMPT_DIR"]) / "cupos.json").read_text(encoding="utf-8")
)
libro = Path(cupos["home"]) / "llamadas"
fin = time.time() + 10
while time.time() < fin and not (libro.is_dir() and any(libro.glob("*.json"))):
    time.sleep(0.05)
time.sleep(0.3)
entradas = sorted(libro.glob("*.json")) if libro.is_dir() else []
notas = {
    "MOTOR_LLAMADA": os.environ.get("MOTOR_LLAMADA"),
    "libro": [
        [r.get("tipo"), r.get("cuenta")]
        for r in (json.loads(f.read_text(encoding="utf-8")) for f in entradas)
    ],
    "senuelo_del_repo": Path("motor/runwrap.py").is_file(),
    "shims_en_path": [
        p
        for p in os.environ.get("PATH", "").split(os.pathsep)
        if os.path.isfile(os.path.join(p or ".", "llamada_guard.py"))
    ],
}
Path(os.environ["PRUEBA_NOTAS"]).write_text(json.dumps(notas), encoding="utf-8")
"""


def git(cwd: Path, *args: str) -> None:
    r = subprocess.run(
        ["git", "-c", "user.name=prueba", "-c", "user.email=prueba@l", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if r.returncode != 0:
        raise SystemExit(f"git {' '.join(args)} en {cwd}: {r.stderr.strip()}")


def senuelo_en(carpeta: Path) -> None:
    (carpeta / "motor").mkdir(parents=True, exist_ok=True)
    (carpeta / "motor" / "__init__.py").write_text("", encoding="utf-8")
    (carpeta / "motor" / "runwrap.py").write_text(SENUELO, encoding="utf-8")


def monta(tmp: Path) -> tuple[Path, Path, Path]:
    """(home, repo, intento): home temporal vacío, repo e intento con su señuelo, agente falso."""
    home = tmp / "home"
    repo = tmp / "repo"
    repo.mkdir(parents=True)
    git(repo, "init", "-q", "-b", "main")
    senuelo_en(repo)
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "señuelo")
    d = home / "intentos" / "demo" / "A-implementar-1"
    senuelo_en(d)
    (d / "brief.md").write_text(
        "Encargo de prueba: nada que entregar.\n", encoding="utf-8"
    )
    (d / "sesion").write_text(str(uuid.uuid4()), encoding="utf-8")
    (d / "cupos.json").write_text(
        json.dumps(
            {
                "home": str(home),
                "rutas": {},
                "max_parallel": {},
                "agentes_por_maquina": 1,
            }
        ),
        encoding="utf-8",
    )
    (tmp / "agente.py").write_text(AGENTE, encoding="utf-8")
    return home, repo, d


def cola(ruta: Path, n: int = 15) -> str:
    try:
        lineas = ruta.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return "(sin log)"
    return "\n".join(lineas[-n:]) or "(log vacío)"


def espera_outcome(d: Path, proc: dict, problemas: list[str]) -> None:
    from motor import plataforma  # main ya puso en sys.path el motor de --raiz

    outcome = d / "outcome.json"
    tope = (
        time.time() + 180
    )  # 120 del --timeout y margen para un arranque en frío de uv en la PC
    gracia = False
    while not outcome.is_file():
        if time.time() > tope:
            plataforma.matar_proc(int(proc["pid"]), proc.get("inicio", ""))
            problemas.append(
                "el vigilante no terminó en 180 s; cola de su log:\n"
                + cola(d / "runwrap.log")
            )
            return
        if plataforma.estado_proc(proc) != "vivo":
            if gracia:  # el outcome se escribe justo antes de salir: una gracia y basta
                problemas.append(
                    "el vigilante murió sin outcome; cola de su log:\n"
                    + cola(d / "runwrap.log")
                )
                return
            gracia = True
            time.sleep(1)
            continue
        time.sleep(0.2)
    fin = time.time() + 30  # en Windows, borrar el CWD de un proceso vivo falla
    while time.time() < fin and plataforma.estado_proc(proc) == "vivo":
        time.sleep(0.2)
    if plataforma.estado_proc(proc) == "vivo":
        problemas.append("el vigilante seguía vivo 30 s después de su outcome")


def juzga(raiz: Path, tmp: Path, home: Path, d: Path, problemas: list[str]) -> None:
    if (d / "señuelo").exists():
        problemas.append("corrió un señuelo: quedó 'señuelo' en la carpeta del intento")
    try:
        outcome = json.loads((d / "outcome.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        problemas.append("el vigilante no dejó outcome.json en el intento")
        outcome = None
    if outcome is not None and outcome.get("exit") != 0:
        problemas.append(
            f"el agente no salió 0 (exit {outcome.get('exit')}); cola de su log:\n"
            + (outcome.get("tail") or cola(d / "log.txt"))
        )
    libro = home / "llamadas"
    vivos = sorted(libro.glob("*.json")) if libro.is_dir() else []
    if vivos:  # runwrap borra su entrada al terminar: una zombi bloquearía los topes
        problemas.append(
            f"quedaron entradas en el libro de cuentas: {[f.name for f in vivos]}"
        )
    try:
        notas = json.loads((tmp / "notas.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        problemas.append("el agente no dejó sus notas")
        return
    if notas.get("MOTOR_LLAMADA") is not None:
        problemas.append(
            f"el agente arrancó dentro de una llamada (MOTOR_LLAMADA="
            f"{notas.get('MOTOR_LLAMADA')!r})"
        )
    if notas.get("libro") != [["agente", "prueba-vigilante"]]:
        problemas.append(
            "el libro de cuentas mientras corría no fue solo la entrada del agente: "
            f"{notas.get('libro')}"
        )
    if notas.get("senuelo_del_repo") is not True:
        problemas.append("el agente no corrió en el repo del señuelo")
    # el contrato real es al revés de "sin shims": el agente los conserva en el PATH para que sus
    # llamadas a otros CLIs cuenten; sin ellos (runwrap quitándolos del env) los topes se rompen
    if str(raiz / "motor" / "shims") not in (notas.get("shims_en_path") or []):
        problemas.append(
            f"el PATH del agente no trae los shims de {raiz}: sus llamadas no contarían"
        )


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="prueba del contrato de lanzamiento del vigilante"
    )
    ap.add_argument(
        "--raiz",
        type=Path,
        default=Path(__file__).resolve().parent.parent,
        help="carpeta del motor que se prueba (omisión: la que contiene esta ops/)",
    )
    a = ap.parse_args(argv)
    raiz = a.raiz.resolve()
    if not (raiz / "motor" / "effects.py").is_file():
        print(f"no hay motor en {raiz}: falta motor/effects.py", file=sys.stderr)
        return 2
    sys.path.insert(0, str(raiz))
    from motor import plataforma, senal
    from motor.effects import Effects, comando_runwrap

    with tempfile.TemporaryDirectory(prefix="prueba-vigilante-") as t:
        tmp = Path(t)
        home, repo, d = monta(tmp)
        problemas: list[str] = []
        # veneno a propósito: el env del lanzamiento no puede llevárselos al agente
        os.environ["MOTOR_LLAMADA"] = "1"
        os.environ[senal.VARIABLE] = "señuelo"
        cx = SimpleNamespace(
            maquina=SimpleNamespace(env={}, secretos={}, path_extra=[])
        )
        env = Effects(cx).env_base(
            {
                "MOTOR_RESULT": str(d / "result.json"),
                "MOTOR_MODEL": "prueba",
                "MOTOR_BLOCK": "A",
                "MOTOR_ROLE": "implementar",
                "PYTHONPATH": str(raiz),
                "PRUEBA_NOTAS": str(tmp / "notas.json"),
            }
        )
        if "MOTOR_LLAMADA" in env or senal.VARIABLE in env:
            problemas.append(
                "el env del lanzamiento arrastraba la llamada en curso o la señal"
            )
        # como start_attempt: los shims delante; el agente los conserva (sus llamadas a otros
        # CLIs cuentan), pero su ejecutable se resuelve sin ellos (motor/runwrap._sin_shims)
        env["PATH"] = os.pathsep.join(
            [str(raiz / "motor" / "shims"), env.get("PATH", "")]
        )
        cmd, cwd_runwrap = comando_runwrap(
            d,
            repo,
            [
                "--home",
                str(home),
                "--cuenta",
                "prueba-vigilante",
                "--timeout",
                "120",
                "--sin-avance",
                "30",
            ],
            [sys.executable, str(tmp / "agente.py")],
        )
        proc = plataforma.lanzar_separado(cmd, cwd_runwrap, env, d / "runwrap.log")
        espera_outcome(d, proc, problemas)
        juzga(raiz, tmp, home, d, problemas)
        if problemas:
            print("\n".join(f"- {p}" for p in problemas), file=sys.stderr)
            return 1
        print(
            f"El vigilante de {raiz} lanzó al agente sin señuelos, sin MOTOR_LLAMADA"
            " y con una sola entrada en el libro de cuentas."
        )
        return 0


if __name__ == "__main__":
    sys.exit(main())
