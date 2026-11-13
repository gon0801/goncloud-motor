"""Una llave de suscripción no descansa en un archivo que otro usuario pueda leer.

La de Qwen viaja por entorno: no toca el disco y llega al proceso del lanzador. La de OpenCode es un
archivo que el CLI lee durante el lanzamiento: aunque el motor muera justo después de escribirlo,
otro usuario no lo alcanza.
"""

from __future__ import annotations

import json
import os
import shutil
import stat
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from motor import plataforma
from motor.encargos import sesion

FALSA = "sk-FALSA-0001"
TOKEN_PLAN = "https://token-plan.ap-southeast-1.maas.aliyuncs.com/compatible-mode/v1"


def _legible_por_otros(ruta: Path, base: Path) -> bool:
    archivo = stat.S_IMODE(ruta.stat().st_mode)
    carpetas = [stat.S_IMODE(p.stat().st_mode) for p in ruta.parents if p.is_relative_to(base)]
    return any(
        archivo & lectura and all(m & paso for m in carpetas)
        for lectura, paso in ((stat.S_IRGRP, stat.S_IXGRP), (stat.S_IROTH, stat.S_IXOTH))
    )


def _con_la_llave(base: Path) -> list[Path]:
    return [f for f in base.rglob("*") if f.is_file() and FALSA.encode() in f.read_bytes()]


@unittest.skipIf(os.name == "nt", "permisos POSIX")
class LaLlaveNoQuedaAlAlcanceDeOtros(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp()).resolve()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.home = self.tmp / "home"
        self.base = self.tmp / "base"
        self.espacio = self.base / "espacio" / "enc_x"
        self.espacio.mkdir(parents=True)
        self.directorio = self.base / "ejecucion" / "enc_x" / "intento-1"
        self.directorio.mkdir(parents=True)
        self.runtime = self.base / "runtime" / "enc_x" / "t"
        self.runtime.mkdir(parents=True)
        entorno = {
            k: v
            for k, v in os.environ.items()
            if k not in ("QWEN_HOME", "XDG_DATA_HOME", "BAILIAN_TOKEN_PLAN_API_KEY")
        }
        parche = mock.patch.dict(os.environ, {**entorno, "HOME": str(self.home)}, clear=True)
        parche.start()
        self.addCleanup(parche.stop)
        mascara = os.umask(0o022)
        self.addCleanup(os.umask, mascara)

    def _preparar(self, cmd: list[str], agente: str):
        enc = SimpleNamespace(
            id="enc_x", rol="responsable", conductor="t", adjuntos=(),
            entrega=SimpleNamespace(rama=""), fases=(),
        )
        return sesion.preparar_administrado(
            cmd,
            self.espacio,
            self.directorio,
            enc,
            "s1",
            "2026-11-12T00:00:00Z",
            self.runtime,
            agente=agente,
        )

    def test_la_llave_de_qwen_no_toca_el_disco_y_llega_al_lanzador(self):
        qwen = self.home / ".qwen"
        qwen.mkdir(parents=True)
        (qwen / "settings.json").write_text(
            json.dumps(
                {
                    "security": {"auth": {"selectedType": "openai"}},
                    "model": {"name": "qwen3", "baseUrl": TOKEN_PLAN},
                    "env": {"BAILIAN_TOKEN_PLAN_API_KEY": FALSA},
                }
            ),
            encoding="utf-8",
        )
        (qwen / "settings.json").chmod(0o600)
        entorno = self._preparar(["qwen", "--model", "qwen3"], "qwen")
        self.assertEqual(_con_la_llave(self.base), [], "preparar no escribe la llave")

        lanzados = []

        def lanzar_separado(argv, cwd, env, log):
            lanzados.append(env)
            return {"pid": os.getpid(), "inicio": "", "boot_id": "", "job": "", "separacion": ""}

        with mock.patch.object(plataforma, "lanzar_separado", lanzar_separado):
            sesion.lanzar_administrado(self.directorio, "t", entorno)
        self.assertEqual(_con_la_llave(self.base), [], "lanzar no escribe la llave")
        self.assertEqual(len(lanzados), 1)
        self.assertIn(FALSA, "".join(lanzados[0].values()), "el lanzador recibe la llave")

    def test_la_credencial_de_opencode_no_es_legible_aunque_el_motor_muera_al_escribirla(self):
        datos = self.home / ".local" / "share" / "opencode"
        datos.mkdir(parents=True)
        (datos / "auth.json").write_text(
            json.dumps({"opencode-go": {"type": "api", "key": FALSA}}), encoding="utf-8"
        )
        (datos / "auth.json").chmod(0o600)
        chmod = Path.chmod

        def muere_antes_de_restringir(ruta: Path, modo: int, **kw):
            if ruta.is_file():
                raise KeyboardInterrupt("el motor murió tras escribir la credencial")
            return chmod(ruta, modo, **kw)

        with mock.patch.object(Path, "chmod", muere_antes_de_restringir):
            with self.assertRaises(KeyboardInterrupt):
                self._preparar(["opencode", "run", "--model", "opencode-go/glm"], "opencode")
        escritas = _con_la_llave(self.base)
        self.assertTrue(escritas, "la credencial se escribió para el CLI")
        self.assertEqual([f for f in escritas if _legible_por_otros(f, self.base)], [])


if __name__ == "__main__":
    unittest.main()
