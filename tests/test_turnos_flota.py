from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from motor.flota.actualizaciones import _crear_generacion, preparar
from motor.flota.control import _atomico, aplicar, asignar, revertir

POLITICA = (
    'generacion="ensayo"\n[[puesto]]\nfuncion="director"\n'
    'modelo=["p/m"]\nesfuerzo=["low"]\n'
)
POLITICA_B = (
    'generacion="ensayo-b"\n[[puesto]]\nfuncion="director"\n'
    'modelo=["p/m"]\nesfuerzo=["low"]\n'
)


def _repo(origen: Path) -> Path:
    origen.mkdir(parents=True)
    for args in (
        ("init", "-q"),
        ("config", "user.email", "ensayo@localhost"),
        ("config", "user.name", "Ensayo"),
    ):
        subprocess.run(
            ["git", "-C", str(origen), *args], check=True, capture_output=True
        )
    (origen / "comprobar.py").write_text("raise SystemExit(0)\n")
    subprocess.run(["git", "-C", str(origen), "add", "."], check=True)
    subprocess.run(["git", "-C", str(origen), "commit", "-qm", "base"], check=True)
    return origen


def _config_poteto(gen: Path, con_poteto: bool = True) -> None:
    """La configuración exportada que trae una generación completa."""
    paquete = gen / "config/hermes/shared/sistema-agentes/poteto"
    (paquete / "skills/poteto-mode").mkdir(parents=True, exist_ok=True)
    (paquete / "poteto.v1.json").write_text('{"files": []}\n', encoding="utf-8")
    if con_poteto:
        (paquete / "skills/poteto-mode/SKILL.md").write_text(
            "---\nname: poteto-mode\n---\n\n# Poteto mode\n", encoding="utf-8"
        )
    (gen / "config/hermes/model-routing.source.json").write_text(
        "{}\n", encoding="utf-8"
    )
    (gen / "config/hermes/source-revision.json").write_text(
        '{"revision": "ensayo"}\n', encoding="utf-8"
    )
    plugin = gen / "config/hermes/plugins/goncloud-team"
    plugin.mkdir(parents=True, exist_ok=True)
    (plugin / "poteto.py").write_text(
        "def comprobar_copia(ruta):\n    return None\n", encoding="utf-8"
    )


def _runtime_preparada(home: Path, gen: Path, nombre: str, version: str) -> None:
    """runtime.json y recibo de una generación con un componente falso."""
    argv = [sys.executable, "-c", "print(" + repr(version) + ")"]
    runtime = {
        "schema": 1,
        "lanzadores": {nombre: argv},
        "componentes": [{"nombre": nombre, "version": version}],
        "observaciones": {nombre: {"version": version, "ejecutable": argv}},
    }
    ruta = gen / "runtime.json"
    _atomico(ruta, runtime)
    _atomico(
        home / "preparaciones" / (gen.name + ".json"),
        {
            "estado": "preparada",
            "generacion": gen.name,
            "componentes": [nombre],
            "integridad_runtime": hashlib.sha256(ruta.read_bytes()).hexdigest(),
        },
    )


class TurnosFlota(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="turnos-flota-")
        self.base = Path(self.tmp.name)
        self.home = self.base / "home"
        self.home.mkdir()
        (self.home / "manifiesto.toml").write_text(POLITICA, encoding="utf-8")
        self.motor_motor = self.base / "motor-home"

    def tearDown(self):
        self.tmp.cleanup()

    def _flota(self, nombre: str, config: bool = True, poteto: bool = True):
        origen = _repo(self.base / nombre)
        home = self.home
        a, gen_a = _crear_generacion(home, origen, [], {}, POLITICA)
        if config:
            _config_poteto(gen_a, con_poteto=poteto)
        self.assertEqual(aplicar(home, a)["accion"], "aplicada")
        return origen, a, gen_a

    def test_candidata_sin_config_no_se_aplica(self):
        origen, a, _gen_a = self._flota("origen-a")
        b, _gen_b = _crear_generacion(
            self.home, origen, [], {}, POLITICA + '[[puesto]]\nfuncion="codigo"\n'
        )
        salida = aplicar(self.home, b)
        self.assertEqual(salida["accion"], "rechazada")
        self.assertIn("configuracion", salida["motivo"])
        candidata = json.loads((self.home / "candidata.json").read_text())
        self.assertEqual(candidata["estado"], "rechazada")
        self.assertIn("configuracion exportada", candidata["motivo"])
        self.assertEqual(asignar(self.home), a)
        self.assertFalse((self.home / "instalacion-pendiente.json").exists())

    def test_candidata_sin_poteto_no_se_aplica(self):
        origen, a, _gen_a = self._flota("origen-b")
        b, gen_b = _crear_generacion(self.home, origen, [], {}, POLITICA_B)
        _config_poteto(gen_b, con_poteto=False)
        salida = aplicar(self.home, b)
        self.assertEqual(salida["accion"], "rechazada")
        self.assertIn("poteto-mode", salida["motivo"])
        self.assertEqual(asignar(self.home), a)
        # La candidata queda preparada en su generación, no aplicada.
        self.assertTrue(gen_b.is_dir())
        self.assertFalse((self.home / "instalacion-pendiente.json").exists())

    def test_preparar_parcial_reutiliza_config_de_vigente(self):
        origen, a, gen_a = self._flota("origen-c")
        _runtime_preparada(self.home, gen_a, "grok", "v1")
        doc = {
            "nombre": "grok",
            "tipo": "npm",
            "paquete": "@xai-official/grok",
            "version": "v2",
            "fuente": "https://registry.npmjs.org/%40xai-official%2Fgrok/latest",
            "tarball": "https://registry.npmjs.org/@xai-official/grok/-/grok-v2.tgz",
            "integridad": "sha512-AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA",
        }

        def fake(doc_, destino, gen, home):
            destino.mkdir(parents=True)
            script = destino / "cli.py"
            script.write_text("print(" + repr(doc_["version"]) + ")", encoding="utf-8")
            return ["python", str(script)], doc_["version"], None

        with patch(
            "motor.flota.actualizaciones._preparar_componente", side_effect=fake
        ):
            salida = preparar(
                self.home, {"componentes": [doc]}, motor_origen=origen, repos={}
            )
        runtime = json.loads(
            (self.home / "generaciones" / salida["generacion"] / "runtime.json").read_text()
        )
        self.assertEqual(runtime["componentes"][0]["version"], "v2")
        self.assertEqual(
            runtime.get("poteto"), "config/hermes/shared/sistema-agentes/poteto"
        )
        paquete = (
            self.home
            / "generaciones"
            / salida["generacion"]
            / "config/hermes/shared/sistema-agentes/poteto"
        )
        self.assertTrue((paquete / "skills/poteto-mode/SKILL.md").is_file())
        self.assertEqual(
            json.loads(
                (
                    self.home
                    / "generaciones"
                    / salida["generacion"]
                    / "config/hermes/source-revision.json"
                ).read_text()
            )["revision"],
            "ensayo",
        )

    def test_candidata_completa_con_turnos_bien_si_se_aplica(self):
        origen, a, gen_a = self._flota("origen-d")
        b, gen_b = _crear_generacion(self.home, origen, [], {}, POLITICA_B)
        _config_poteto(gen_b)
        _runtime_preparada(self.home, gen_b, "codex", "v-turno")

        def turno(agente, gen, raiz, tope_s=150):
            return {"estado": "ok", "salida": "listo", "segundos": 1.2, "rc": 0}

        with (
            patch(
                "motor.flota.turnos._sesion_presente",
                side_effect=lambda agente: agente == "codex",
            ),
            patch("motor.flota.turnos._turno_agente", side_effect=turno),
            patch(
                "motor.flota.turnos.resolver_lanzador",
                side_effect=lambda agente, gen: ["/bin/true"],
            ),
        ):
            salida = aplicar(self.home, b)
        self.assertEqual(salida["accion"], "aplicada", salida)
        self.assertEqual(salida["generacion"], b)
        self.assertEqual(salida["turnos"]["codex"]["estado"], "ok")
        self.assertEqual(salida["turnos"]["qwen"]["estado"], "sin_sesion")
        self.assertEqual(asignar(self.home), b)
        informe = json.loads(
            (self.home / "turnos" / (b + ".json")).read_text(encoding="utf-8")
        )
        self.assertEqual(informe["fallidos"], [])
        self.assertEqual(informe["turnos"]["codex"]["salida"], "listo")

    def test_turno_fallido_no_aplica_y_avisa(self):
        origen, a, gen_a = self._flota("origen-e")
        b, gen_b = _crear_generacion(self.home, origen, [], {}, POLITICA_B)
        _config_poteto(gen_b)
        _runtime_preparada(self.home, gen_b, "codex", "v-turno")

        def turno(agente, gen, raiz, tope_s=150):
            if gen.resolve() == gen_b.resolve():
                return {
                    "estado": "fallo",
                    "rc": 1,
                    "segundos": 2.0,
                    "error": "instrumento_fallo: boom literal de codex",
                }
            return {"estado": "ok", "salida": "listo", "segundos": 1.0, "rc": 0}

        with patch.dict(os.environ, {"MOTOR_HOME": str(self.motor_motor)}):
            with (
                patch(
                    "motor.flota.turnos._sesion_presente",
                    side_effect=lambda agente: agente == "codex",
                ),
                patch("motor.flota.turnos._turno_agente", side_effect=turno),
                patch(
                    "motor.flota.turnos.resolver_lanzador",
                    side_effect=lambda agente, gen: ["/bin/true"],
                ),
            ):
                salida = aplicar(self.home, b)
        self.assertEqual(salida["accion"], "rechazada")
        self.assertIn("codex", salida["motivo"])
        self.assertIn("boom literal de codex", salida["motivo"])
        self.assertEqual(asignar(self.home), a)
        pendientes = sorted(
            (self.motor_motor / "avisos" / "pendientes").glob("*.json")
        )
        self.assertEqual(len(pendientes), 1)
        aviso = json.loads(pendientes[0].read_text(encoding="utf-8"))
        self.assertIn("codex", aviso["texto"])
        self.assertIn("boom literal de codex", aviso["texto"])
        candidata = json.loads((self.home / "candidata.json").read_text())
        self.assertEqual(candidata["turnos"]["codex"]["error"], "instrumento_fallo: boom literal de codex")

    def test_turno_fallido_en_ambas_generaciones_no_bloquea(self):
        origen, a, gen_a = self._flota("origen-f")
        b, gen_b = _crear_generacion(self.home, origen, [], {}, POLITICA_B)
        _config_poteto(gen_b)
        _runtime_preparada(self.home, gen_b, "codex", "v-turno")

        def turno(agente, gen, raiz, tope_s=150):
            return {
                "estado": "fallo",
                "rc": 1,
                "segundos": 1.0,
                "error": "la cuenta no tiene cupo hoy",
            }

        with (
            patch(
                "motor.flota.turnos._sesion_presente",
                side_effect=lambda agente: agente == "codex",
            ),
            patch("motor.flota.turnos._turno_agente", side_effect=turno),
            patch(
                "motor.flota.turnos.resolver_lanzador",
                side_effect=lambda agente, gen: ["/bin/true"],
            ),
        ):
            salida = aplicar(self.home, b)
        self.assertEqual(salida["accion"], "aplicada", salida)
        self.assertEqual(asignar(self.home), b)
        informe = json.loads(
            (self.home / "turnos" / (b + ".json")).read_text(encoding="utf-8")
        )
        self.assertEqual(informe["turnos"]["codex"]["estado"], "fallo_comun")

    def test_candidata_sin_clientes_no_retira_los_de_la_vigente(self):
        origen, a, gen_a = self._flota("origen-i")
        _runtime_preparada(self.home, gen_a, "codex", "v1")
        b, gen_b = _crear_generacion(self.home, origen, [], {}, POLITICA_B)
        _config_poteto(gen_b)
        _runtime_preparada(self.home, gen_b, "hermes", "v1")

        with patch.dict(os.environ, {"MOTOR_HOME": str(self.motor_motor)}):
            with patch("motor.flota.turnos._sesion_presente", return_value=True):
                salida = aplicar(self.home, b)
        self.assertEqual(salida["accion"], "rechazada", salida)
        self.assertIn("retira el ejecutable de codex", salida["motivo"])
        self.assertEqual(asignar(self.home), a)
        candidata = json.loads((self.home / "candidata.json").read_text())
        self.assertEqual(candidata["turnos"]["codex"]["estado"], "fallo")
        pendientes = sorted(
            (self.motor_motor / "avisos" / "pendientes").glob("*.json")
        )
        self.assertEqual(len(pendientes), 1)
        aviso = json.loads(pendientes[0].read_text(encoding="utf-8"))
        self.assertIn("codex", aviso["texto"])
        self.assertIn("retira el ejecutable", aviso["texto"])

    def test_sin_ningun_turno_real_no_se_aplica(self):
        origen, a, gen_a = self._flota("origen-j")
        _runtime_preparada(self.home, gen_a, "codex", "v1")
        b, gen_b = _crear_generacion(self.home, origen, [], {}, POLITICA_B)
        _config_poteto(gen_b)
        _runtime_preparada(self.home, gen_b, "codex", "v2")

        with patch.dict(os.environ, {"MOTOR_HOME": str(self.motor_motor)}):
            with patch("motor.flota.turnos._sesion_presente", return_value=False):
                salida = aplicar(self.home, b)
        self.assertEqual(salida["accion"], "rechazada", salida)
        self.assertIn("ningun agente corrio un turno real", salida["motivo"])
        self.assertIn("sin_sesion", salida["motivo"])
        self.assertEqual(asignar(self.home), a)
        candidata = json.loads((self.home / "candidata.json").read_text())
        self.assertEqual(candidata["estado"], "rechazada")
        self.assertEqual(candidata["turnos"]["codex"]["estado"], "sin_sesion")
        pendientes = sorted(
            (self.motor_motor / "avisos" / "pendientes").glob("*.json")
        )
        self.assertEqual(len(pendientes), 1)
        aviso = json.loads(pendientes[0].read_text(encoding="utf-8"))
        self.assertIn("ningun turno real", aviso["texto"])

    def test_candidata_que_retira_un_agente_con_sesion_bloquea(self):
        origen, a, gen_a = self._flota("origen-g")
        b, gen_b = _crear_generacion(self.home, origen, [], {}, POLITICA_B)
        _config_poteto(gen_b)
        _runtime_preparada(self.home, gen_b, "codex", "v-turno")

        def lanzador_con_retiro(agente, gen):
            if gen is not None and gen.resolve() == gen_b.resolve():
                return None
            return ["/bin/true"]

        with (
            patch(
                "motor.flota.turnos._sesion_presente",
                side_effect=lambda agente: agente == "codex",
            ),
            patch(
                "motor.flota.turnos.resolver_lanzador",
                side_effect=lanzador_con_retiro,
            ),
        ):
            salida = aplicar(self.home, b)
        self.assertEqual(salida["accion"], "rechazada")
        self.assertIn("retira el ejecutable de codex", salida["motivo"])
        self.assertEqual(asignar(self.home), a)

    def test_la_reversa_regresa_a_la_vigente_tras_los_turnos(self):
        origen, a, _gen_a = self._flota("origen-h")
        b, gen_b = _crear_generacion(self.home, origen, [], {}, POLITICA_B)
        _config_poteto(gen_b)
        _runtime_preparada(self.home, gen_b, "codex", "v-turno")

        def turno(agente, gen, raiz, tope_s=150):
            return {"estado": "ok", "salida": "listo", "segundos": 0.5, "rc": 0}

        with (
            patch(
                "motor.flota.turnos._sesion_presente",
                side_effect=lambda agente: agente == "codex",
            ),
            patch("motor.flota.turnos._turno_agente", side_effect=turno),
            patch(
                "motor.flota.turnos.resolver_lanzador",
                side_effect=lambda agente, gen: ["/bin/true"],
            ),
        ):
            self.assertEqual(aplicar(self.home, b)["accion"], "aplicada")
        self.assertEqual(asignar(self.home), b)
        self.assertEqual(revertir(self.home)["accion"], "revertido")
        self.assertEqual(asignar(self.home), a)


if __name__ == "__main__":
    unittest.main()
