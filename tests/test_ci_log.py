"""`ci.log`: el revisor pide el log de un check del PR de su encargo.

El `gh` es un doble del puerto: devuelve los checks del SHA del encargo y el
log del job pedido. La operacion no crea ramas ni PRs, no cruza repos y no
espera a una persona: lo que falta lo dice en la respuesta.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from motor.encargos import capacidades as cap
from motor.encargos import comprobacion as comp
from motor.encargos import necesidades as nec
from motor.encargos.dominio import AlcanceRevision
from motor.github import ChecksVerdict

SHA = "835b2f1a0c3d4e5f60718293a4b5c6d7e8f90123"
LOG = (
    "jaula-macos\tPrueba\t2026-11-01T12:00:00Z \x1b[32mok\x1b[0m test_jaula_macos\r\n"
    "jaula-macos\tPrueba\t2026-11-01T12:00:01Z Ran 12 tests in 3.1s\n"
    "jaula-macos\tPrueba\t2026-11-01T12:00:01Z OK (señal, ñandú)\n"
)


class GhDeMentira:
    """El puerto de GitHub con los checks y el log que la prueba elige."""

    def __init__(self, corridas=None, logs=None):
        self.corridas = (
            corridas
            if corridas is not None
            else [
                {
                    "nombre": "jaula-macos",
                    "run_id": "1234",
                    "run_id_corrida": "1234",
                    "job_id": "99",
                    "url": "https://github.com/o/r/actions/runs/1234",
                    "status": "completed",
                    "conclusion": "success",
                }
            ]
        )
        self.logs = {"jaula-macos": LOG} if logs is None else logs
        self.llamadas: list[tuple] = []

    def checks(self, repo, numero, sha, extra, relanzadas=(), *, base_politica=""):
        self.llamadas.append(("checks", repo, numero, sha, tuple(extra)))
        return ChecksVerdict(
            sha=sha,
            terminal=True,
            green=True,
            faltan=(),
            fallas=(),
            corridas=tuple(self.corridas),
        )

    def log_fallido_job(self, repo, run_id, job, job_id=""):
        self.llamadas.append(("log", repo, run_id, job, job_id))
        return self.logs.get(job, "")

    def llamadas_de(self, clase: str) -> list[tuple]:
        return [llamada for llamada in self.llamadas if llamada[0] == clase]


class _CiLog(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(tmp.cleanup)
        self.raiz = Path(tmp.name)
        self.espacio = self.raiz / "espacio"
        self.espacio.mkdir()
        self.destino = self.raiz / "destino"
        self.recursos = {
            "ci-github": {"tipo": "remoto", "raiz": str(self.raiz / "ci")},
        }
        self.contexto = {
            "pr": 189,
            "sha": SHA,
            "rama": "encargo/enc_x-f",
            "repo": "o/r",
            "base": "main",
        }

    def pedir(self, gh, parametros=None, contexto=None):
        datos = {"job": "jaula-macos"}
        datos.update(parametros or {})
        with mock.patch("motor.github.GhGitHub", lambda *a, **k: gh):
            return cap.ejecutar(
                self.recursos,
                "ci.log",
                "ci-github",
                datos,
                self.destino,
                espacio=self.espacio,
                contexto=self.contexto if contexto is None else contexto,
            )

    def test_el_revisor_recibe_el_log_del_check_en_su_espacio(self):
        gh = GhDeMentira()

        r = self.pedir(gh)

        self.assertEqual(r["estado"], "ok", r)
        copia = Path(r["datos"]["copia"])
        self.assertTrue(copia.is_relative_to(self.espacio), r["datos"]["copia"])
        self.assertIn(".interno", copia.parts)
        self.assertEqual(copia.read_bytes(), LOG.encode("utf-8"))
        self.assertIn("OK (señal, ñandú)", r["datos"]["texto"])
        self.assertEqual(r["datos"]["pr"], 189)
        self.assertEqual(r["datos"]["sha"], SHA)
        self.assertEqual(r["datos"]["run"], "1234")
        self.assertEqual(r["evidencia"]["tipo"], "ci_log")
        self.assertEqual(gh.llamadas_de("log")[0][1], "o/r")

    def test_un_check_inexistente_responde_con_los_reales_sin_espera(self):
        r = self.pedir(GhDeMentira(), {"job": "no-existe"})

        self.assertEqual(r["estado"], "error", r)
        self.assertIn("no existe", r["motivo"])
        self.assertIn("jaula-macos", r["motivo"])
        self.assertEqual(r["datos"]["checks"], ["jaula-macos"])
        self.assertFalse((self.espacio / ".interno" / "logs").exists())

    def test_un_log_enorme_se_recorta_al_tope(self):
        enorme = "x" * 100_000 + "\n" + "y" * 100_000 + "\n"
        gh = GhDeMentira(logs={"jaula-macos": enorme})

        with mock.patch.object(cap, "MAX_LOG", 4096):
            r = self.pedir(gh)

        self.assertEqual(r["estado"], "ok", r)
        self.assertTrue(r["datos"]["recortado"], r["datos"])
        self.assertLessEqual(r["datos"]["bytes"], 4096)
        copia = Path(r["datos"]["copia"])
        self.assertTrue(copia.is_file())
        self.assertLessEqual(copia.stat().st_size, 4096)
        self.assertLessEqual(len(r["datos"]["texto"]), cap.MAX_TEXTO)

    def test_no_lee_un_pr_ajeno_al_encargo(self):
        gh = GhDeMentira()

        r = self.pedir(gh, {"pr": "190"})

        self.assertEqual(r["estado"], "error", r)
        self.assertIn("no es el del encargo", r["motivo"])
        self.assertEqual(gh.llamadas, [])

    def test_usa_el_repo_del_encargo_y_no_uno_del_parametro(self):
        gh = GhDeMentira()

        r = self.pedir(gh, {"repo": "otro/r"})

        self.assertEqual(r["estado"], "ok", r)
        self.assertEqual(gh.llamadas_de("checks")[0][1], "o/r")

    def test_sin_pr_del_encargo_no_lee_nada(self):
        gh = GhDeMentira()

        r = self.pedir(gh, contexto={**self.contexto, "pr": 0})

        self.assertEqual(r["estado"], "error", r)
        self.assertIn("todavia no tiene un PR", r["motivo"])
        self.assertEqual(gh.llamadas, [])

    def test_el_script_real_importa_el_motor_y_escribe_el_resultado(self):
        """El mismo argv con el que `necesidades._ejecutar` lanza la herramienta."""
        destino = self.raiz / "destino-script"
        spec = {
            "operacion": "ci.log",
            "recurso": "ci-github",
            "recurso_config": self.recursos["ci-github"],
            "parametros": {"job": "jaula-macos"},
            "destino": str(destino),
            "autorizado": True,
            "espacio": str(self.espacio),
            "contexto": {**self.contexto, "pr": 0},
        }
        ruta = self.raiz / "spec.json"
        ruta.write_text(json.dumps(spec), encoding="utf-8")

        corrida = subprocess.run(
            [sys.executable, "-P", str(Path(cap.__file__)), "--spec", str(ruta)],
            capture_output=True,
            text=True,
            env={**os.environ},
        )

        self.assertEqual(corrida.returncode, 0, corrida.stderr)
        resultado = json.loads((destino / "resultado.json").read_text(encoding="utf-8"))
        self.assertEqual(resultado["estado"], "error", resultado)
        self.assertIn("todavia no tiene un PR", resultado["motivo"])

    def test_el_catalogo_ofrece_ci_log_solo_en_ci_github(self):
        catalogo = cap.catalogo(
            {
                "ci-github": {"tipo": "remoto", "raiz": "/tmp/ci"},
                "otro": {"tipo": "remoto", "raiz": "/tmp/otro"},
            }
        )
        operaciones = {f["recurso"]: f["operaciones"] for f in catalogo}

        self.assertIn("ci.log", operaciones["ci-github"])
        self.assertNotIn("ci.log", operaciones["otro"])

    def test_la_inspeccion_ci_log_del_revisor_es_valida(self):
        try:
            inspeccion = comp._inspeccion_de(
                {
                    "operacion": "ci.log",
                    "recurso": "ci-github",
                    "parametros": {"job": "jaula-macos"},
                }
            )
        except Exception:  # noqa: BLE001  en la base la operacion no existe
            inspeccion = None

        self.assertIsNotNone(inspeccion)
        self.assertEqual(inspeccion.operacion, "ci.log")

    def test_la_solicitud_ci_log_del_buzon_es_valida(self):
        try:
            solicitud = nec._solicitud_de(
                SimpleNamespace(id="enc_x"),
                {
                    "id": "log-1",
                    "encargo": "enc_x",
                    "operacion": "ci.log",
                    "recurso": "ci-github",
                    "fase": "f",
                    "intento": "i",
                    "generacion": 1,
                    "conductor": "",
                    "parametros": {"job": "jaula-macos"},
                },
            )
        except Exception:  # noqa: BLE001  en la base la operacion no existe
            solicitud = None

        self.assertIsNotNone(solicitud)
        self.assertEqual(solicitud.operacion, "ci.log")

    def test_el_prompt_del_revisor_menciona_ci_log(self):
        texto = comp.instrucciones_revision(
            AlcanceRevision(clase="completa", version="v", base_version="b"),
            Path("resultado.json"),
        )

        self.assertIn("ci.log", texto)
        self.assertIn("job", texto)

    def test_el_puente_menciona_ci_log(self):
        texto = nec.bloque_capacidades(Path("/tmp"), self.recursos)

        self.assertIn("ci.log", texto)


if __name__ == "__main__":
    unittest.main()
