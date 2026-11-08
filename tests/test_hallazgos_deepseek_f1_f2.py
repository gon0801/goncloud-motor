"""Hallazgos bloqueantes F1 y F2 de DeepSeek sobre la entrega.

F1: un paso que devuelve el trabajo a un rol (`Devolver`) no puede quedar
bloqueado por la compuerta de lanzamiento: el rol tiene que recibir su turno.
F2: el fallo al atender la respuesta de un encargo no aborta la de los demás.
"""

from __future__ import annotations

import subprocess
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from motor.encargos import cli as cli_encargos
from motor.encargos import necesidades as nec
from motor.encargos.ciclo import _asegurar_repo, ahora_iso, barrer
from motor.github import FakeGitHub
from tests.test_flujo_revisiones import (
    Recorrido,
    _git_de_ensayo,
    sonda_sin_aislamiento_del_host,
)


def setUpModule():
    sonda_sin_aislamiento_del_host()


def _repo(tmp: str, clave: str) -> Recorrido:
    raiz = Path(tmp)
    bare = raiz / "repo.git"
    semilla = raiz / "semilla"
    semilla.mkdir()
    (semilla / "base.txt").write_text("base\n")
    _git_de_ensayo(semilla, ["init", "--bare", "-b", "main", "--quiet", str(bare)])
    _git_de_ensayo(semilla, ["config", "core.longpaths", "true"], donde=bare)
    for args in (
        ["init", "-b", "main", "--quiet"],
        ["add", "-A"],
        ["-c", "user.name=t", "-c", "user.email=t@l", "commit", "--quiet", "-m", "base"],
        ["push", "--quiet", str(bare), "main"],
    ):
        _git_de_ensayo(semilla, args)
    r = Recorrido(
        tmp, clave, "agrega saludo.txt con hola",
        entrega_nivel="pr", repo=str(bare), base_rama="main",
    )
    assert _asegurar_repo(r.fold(), r.espacio) == ""
    return r


class DevolverNoDejaSinTurno(unittest.TestCase):
    def test_el_responsable_recibe_su_turno_tras_devolver_la_entrega(self):
        tmp = self.enterContext(tempfile.TemporaryDirectory(ignore_cleanup_errors=True))
        r = _repo(tmp, "f1-devolver")
        self.addCleanup(r.reg.close)
        (r.espacio / "saludo.txt").write_text("hola")
        r.incorporar()
        r.juicio({"veredicto": "aprobado", "evidencia": "lei saludo.txt"})
        r.incorporar()
        sha = subprocess.run(
            ["git", "-C", str(r.espacio), "rev-parse", "HEAD"],
            capture_output=True, text=True, check=True,
        ).stdout.strip()
        (Path(r.espacio) / ".git" / "MERGE_HEAD").write_text(sha + "\n")
        falso = Path(tmp) / "fake"
        falso.mkdir()
        gh = FakeGitHub(falso)
        lanzamientos: list[str] = []

        def lanzador(enc, prompt, espacio, sesion):
            lanzamientos.append(enc.rol)
            return None

        with mock.patch(
            "motor.flota.runtime.exigir_poteto",
            return_value=(Path("/tmp/poteto-mode/SKILL.md"), "digest-de-ensayo"),
        ):
            barrer(r.reg, ahora_iso(), time.time(), lanzador, gh=gh)
        e = r.fold()
        self.assertEqual(e.estrategia, "integrar_main")
        self.assertEqual(e.rol, "responsable")
        self.assertEqual(
            lanzamientos, ["responsable"],
            "el responsable quedó sin turno tras devolver la entrega",
        )


class RespuestasDeTodosLosEncargos(unittest.TestCase):
    def test_un_fallo_no_aborta_las_respuestas_de_los_demas(self):
        llamadas: list[str] = []

        def continuar(reg, enc, espacio, directorio, hora):
            llamadas.append(enc.clave)
            if enc.clave == "uno":
                raise RuntimeError("fallo de uno")
            return {}

        encs = {
            "enc_1": SimpleNamespace(
                clave="uno",
                solicitudes=[SimpleNamespace(estado="respuesta")],
            ),
            "enc_2": SimpleNamespace(
                clave="dos",
                solicitudes=[SimpleNamespace(estado="respuesta")],
            ),
        }
        reg = SimpleNamespace(
            base=Path("/tmp"),
            ids=lambda: ["enc_1", "enc_2"],
            fold=lambda ident: encs[ident],
        )
        with mock.patch.object(nec, "continuar_respuestas", side_effect=continuar):
            cli_encargos._atender_respuestas_antes_del_barrido(reg, ahora_iso())

        self.assertEqual(llamadas, ["uno", "dos"])


if __name__ == "__main__":
    unittest.main()
