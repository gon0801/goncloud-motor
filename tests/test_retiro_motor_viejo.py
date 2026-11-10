"""Retirar el motor viejo borra carpetas en las máquinas: la migración no pierde
llaves y el retiro se niega sin `maquina.toml` en la base o con un despliegue en
curso, y solo borra cuando nada depende ya de lo que borra."""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from motor.conf import load_secretos
from motor.effects import _lock_despliegue
from motor.encargos import retiro
from motor.encargos.maquina import de_base


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-c", "user.email=p@p", "-c", "user.name=p", *args],
        cwd=cwd, check=True, capture_output=True, text=True,
    ).stdout.strip()


def _enlazar(enlace: Path, destino: Path) -> None:
    try:
        enlace.symlink_to(destino, target_is_directory=True)
    except OSError as exc:
        raise unittest.SkipTest(f"esta máquina no crea enlaces: {exc}") from exc


def _correr_sin_tareas(argv):
    if argv[0] == "schtasks":
        return subprocess.CompletedProcess(argv, 1, "", "no existe")
    return subprocess.run(argv, capture_output=True, text=True)


class RetiroDelMotorViejo(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(tmp.cleanup)
        self.home = Path(tmp.name) / "casa"
        self.base = self.home / ".motor-encargos"
        self.viejo = self.home / ".motor"
        self.conf = self.home / "motor-conf"
        for carpeta in (self.base, self.viejo / "avisos" / "pendientes", self.conf, self.home / "motor"):
            carpeta.mkdir(parents=True)
        (self.conf / "conf.toml").write_text("[avisos]\n", encoding="utf-8")
        _enlazar(self.viejo / "conf", self.conf)
        (self.viejo / "maquina.toml").write_text(
            f'# la Mac\nnombre = "mac"\nconf = {json.dumps(str(self.viejo / "conf"))}\n'
            'tiene = ["ssh-goncloud"]\n',
            encoding="utf-8",
        )
        self._secretos(self.viejo, "TOKEN_A=viejo-a\nCOMPARTIDA=vieja\nTOKEN_B=viejo-b\n")
        self._secretos(self.base, "COMPARTIDA=de-la-base\nPROPIA=propia")
        self.vigia = self.home / "vigia"
        self.vigia.mkdir()
        _enlazar(self.vigia / "avisos", self.viejo / "avisos" / "pendientes")

    def _secretos(self, carpeta: Path, texto: str) -> None:
        (carpeta / "secretos.env").write_text(texto, encoding="utf-8")
        os.chmod(carpeta / "secretos.env", 0o600)

    def _supervisor_con_el_cambio(self) -> None:
        arbol = self.home / "motor-sucesor"
        (arbol / "motor" / "encargos").mkdir(parents=True)
        (arbol / retiro.MODULO_DEL_CAMBIO).write_text("", encoding="utf-8")
        _git(arbol, "init", "-q")
        _git(arbol, "add", ".")
        _git(arbol, "commit", "-q", "-m", "cambio")
        (self.base / "supervisor-pasada.json").write_text(json.dumps({
            "sha": _git(arbol, "rev-parse", "HEAD"), "ruta": str(arbol), "pid": os.getpid(),
            "hora": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        }), encoding="utf-8")

    def _retirar(self, mirar: bool = False) -> tuple[int, str]:
        codigo, lineas = retiro.retirar(self.home, self.base, mirar, correr=_correr_sin_tareas)
        return codigo, "\n".join(lineas)

    def test_la_migracion_junta_las_llaves_con_el_valor_de_la_base_y_sin_imprimirlas(self):
        juntos = {
            "COMPARTIDA": "de-la-base", "PROPIA": "propia",
            "TOKEN_A": "viejo-a", "TOKEN_B": "viejo-b",
        }
        primera = "\n".join(retiro.migrar(self.home, self.base))
        self.assertEqual(load_secretos(self.base), juntos)
        segunda = "\n".join(retiro.migrar(self.home, self.base))
        self.assertEqual(load_secretos(self.base), juntos)
        if os.name == "posix":
            self.assertEqual(os.stat(self.base / "secretos.env").st_mode & 0o777, 0o600)
        self.assertIn("agregadas TOKEN_A, TOKEN_B", primera)
        self.assertIn("queda el valor de la base para COMPARTIDA", primera)
        for valor in ("viejo-a", "viejo-b", "vieja", "de-la-base", "propia"):
            self.assertNotIn(valor, primera + segunda)
        self.assertIn("no faltaba ninguna llave", segunda)
        self.assertIn("maquina.toml: ya estaba en la base", segunda)
        self.assertEqual(
            de_base(self.base).conf, self.conf.resolve()
        )

    def test_se_niega_sin_maquina_toml_o_con_un_despliegue_en_curso_y_si_no_borra(self):
        self._supervisor_con_el_cambio()

        codigo, salida = self._retirar()
        self.assertEqual(codigo, 1)
        self.assertIn(f"falta maquina.toml en la base {self.base}", salida)
        self.assertTrue(self.viejo.is_dir())

        retiro.migrar(self.home, self.base)
        with _lock_despliegue(self.base, "gon0801/un-repo"):
            codigo, salida = self._retirar()
        self.assertEqual(codigo, 1)
        self.assertIn("hay un despliegue en curso en esta máquina", salida)
        self.assertNotIn("maquina.toml", salida)
        self.assertTrue(self.viejo.is_dir())

        codigo, salida = self._retirar(mirar=True)
        self.assertEqual((codigo, "se niega" in salida), (0, False))
        self.assertIn(f"borraría: {self.viejo}", salida)
        self.assertIn(f"borraría el enlace: {self.vigia / 'avisos'}", salida)
        self.assertTrue(self.viejo.is_dir())

        codigo, salida = self._retirar()
        self.assertEqual(codigo, 0, salida)
        self.assertFalse(os.path.lexists(self.viejo))
        self.assertFalse(os.path.lexists(self.home / "motor"))
        self.assertFalse(os.path.lexists(self.vigia / "avisos"))
        self.assertEqual((self.conf / "conf.toml").read_text(encoding="utf-8"), "[avisos]\n")


if __name__ == "__main__":
    unittest.main()
