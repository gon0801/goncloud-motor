from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from motor.flota.control import (
    anotar_trabajo,
    aplicar,
    asignar,
    leer_trabajo,
    recuperar,
)

RAIZ = Path(__file__).resolve().parents[1]
REVERSA = RAIZ / "ops" / "reversa_ensayo.sh"


def _git(motor: Path, *args: str) -> str:
    hecho = subprocess.run(
        ["git", "-C", str(motor), *args],
        check=True,
        capture_output=True,
        text=True,
    )
    return hecho.stdout.strip()


def _commit(motor: Path, archivos: dict[str, str], mensaje: str) -> str:
    for nombre, texto in archivos.items():
        path = motor / nombre
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(texto, encoding="utf-8")
    _git(motor, "add", ".")
    _git(motor, "commit", "-m", mensaje)
    return _git(motor, "rev-parse", "HEAD")


class ReversaFlota(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="flota-ensayo-"))
        self.home = self.tmp / "home"
        self.motor = self.home / "motor"
        self.motor.mkdir(parents=True)
        _git(self.motor, "init", "-b", "main")
        _git(self.motor, "config", "user.email", "ensayo@local")
        _git(self.motor, "config", "user.name", "ensayo")
        self.buena = _commit(
            self.motor,
            {"comprobar.py": "raise SystemExit(0)\n", "marca.txt": "buena\n"},
            "buena",
        )
        self.mala = _commit(
            self.motor,
            {"comprobar.py": "raise SystemExit(1)\n"},
            "mala",
        )
        self.nueva = _commit(
            self.motor,
            {"comprobar.py": "raise SystemExit(0)\n", "marca.txt": "nueva\n"},
            "nueva",
        )
        _git(self.motor, "checkout", "-q", "--detach", self.buena)

    def tearDown(self) -> None:
        for gen in (self.home / "generaciones").glob("*"):
            if gen.is_dir():
                subprocess.run(
                    ["git", "-C", str(self.motor), "worktree", "remove", "--force", str(gen)],
                    check=False,
                    capture_output=True,
                )
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_candidata_invalida_conserva_la_generacion(self) -> None:
        self.assertEqual(aplicar(self.home, self.buena)["accion"], "aplicada")
        antes = (self.home / "actual").resolve()
        salida = aplicar(self.home, self.mala)
        self.assertEqual(salida["accion"], "rechazada")
        self.assertEqual((self.home / "actual").resolve(), antes)
        self.assertFalse((self.home / "instalacion-pendiente.json").exists())
        cand = json.loads((self.home / "candidata.json").read_text(encoding="utf-8"))
        self.assertEqual(cand["estado"], "rechazada")
        self.assertEqual(cand["destino"], self.mala)

    def test_el_trabajo_activo_conserva_su_generacion(self) -> None:
        aplicar(self.home, self.buena)
        anotar_trabajo(self.home, "uno", self.buena, "codex")
        aplicar(self.home, self.nueva)
        self.assertEqual(leer_trabajo(self.home, "uno")["generacion"], self.buena)
        self.assertEqual(asignar(self.home), self.nueva)
        self.assertTrue((self.home / "generaciones" / self.buena).is_dir())
        self.assertEqual(
            ((self.home / "generaciones" / self.buena) / "marca.txt").read_text(encoding="utf-8"),
            "buena\n",
        )

    def test_recuperar_completa_lo_que_ya_quedo_comprobado(self) -> None:
        aplicar(self.home, self.buena)
        cortado = aplicar(self.home, self.nueva, "worktree")
        self.assertEqual(cortado["accion"], "cortado")
        self.assertTrue((self.home / "instalacion-pendiente.json").exists())
        self.assertEqual(asignar(self.home), self.buena)
        self.assertEqual(recuperar(self.home)["accion"], "completo")
        self.assertEqual(asignar(self.home), self.nueva)
        self.assertFalse((self.home / "instalacion-pendiente.json").exists())
        self.assertEqual(recuperar(self.home)["accion"], "nada")

    def test_recuperar_revierte_si_la_candidata_no_llego_a_instalarse(self) -> None:
        aplicar(self.home, self.buena)
        self.assertEqual(aplicar(self.home, self.nueva, "pendiente")["accion"], "cortado")
        self.assertFalse((self.home / "generaciones" / self.nueva).exists())
        self.assertEqual(recuperar(self.home)["accion"], "revertido")
        self.assertEqual(asignar(self.home), self.buena)
        self.assertFalse((self.home / "instalacion-pendiente.json").exists())

    def test_la_reversa_no_arranca_el_motor_nuevo(self) -> None:
        self.assertEqual(aplicar(self.home, self.buena)["accion"], "aplicada")
        self.assertEqual(aplicar(self.home, self.nueva)["accion"], "aplicada")
        self.assertEqual(
            (self.home / "actual").resolve(),
            (self.home / "generaciones" / self.nueva).resolve(),
        )
        roto = self.home / "arranque-del-motor"
        _commit(
            self.motor,
            {
                "comprobar.py": "raise SystemExit(0)\n",
                "motor/__init__.py": "",
                "motor/__main__.py": (
                    "import os\n"
                    "from pathlib import Path\n"
                    "Path(os.environ['MOTOR_MARCA']).write_text('arranque')\n"
                    "raise SystemExit(1)\n"
                ),
            },
            "rota",
        )
        mala = _git(self.motor, "rev-parse", "HEAD")
        _git(self.motor, "checkout", "-q", "--detach", mala)
        (self.home / "instalacion-pendiente.json").write_text(
            json.dumps(
                {
                    "anterior": {"codigo": self.buena},
                    "destino": {"codigo": mala},
                    "etapa": "codigo",
                }
            ),
            encoding="utf-8",
        )
        hecho = subprocess.run(
            ["python3", "-m", "motor"],
            cwd=self.motor,
            capture_output=True,
            text=True,
            env={"MOTOR_MARCA": str(roto), "PATH": "/usr/bin:/bin"},
        )
        self.assertNotEqual(hecho.returncode, 0)
        self.assertTrue(roto.is_file())
        roto.unlink()
        reversa = subprocess.run(
            ["sh", str(REVERSA), "--home", str(self.home)],
            check=True,
            capture_output=True,
            text=True,
            env={"MOTOR_MARCA": str(roto), "PATH": "/usr/bin:/bin:/opt/homebrew/bin"},
        )
        self.assertIn("revertido", reversa.stdout)
        self.assertFalse(roto.exists())
        self.assertEqual(_git(self.motor, "rev-parse", "HEAD"), self.buena)
        self.assertEqual(
            (self.home / "actual").resolve(),
            (self.home / "generaciones" / self.buena).resolve(),
        )
        self.assertFalse((self.home / "instalacion-pendiente.json").exists())
        otra = subprocess.run(
            ["sh", str(REVERSA), "--home", str(self.home)],
            check=True,
            capture_output=True,
            text=True,
        )
        self.assertEqual(otra.stdout.strip(), "nada")
        self.assertEqual(_git(self.motor, "rev-parse", "HEAD"), self.buena)


if __name__ == "__main__":
    unittest.main()
