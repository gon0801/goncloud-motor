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

from motor.flota.actualizaciones import (
    _copiar_arbol,
    _crear_generacion,
    _reutilizar_pieza,
    _sha256,
    actualizar,
    preparar,
)
from motor.flota.compartida import exportar, importar
from motor.flota.control import (
    _atomico,
    _poner_enlace,
    aplicar,
    asignar,
    recuperar,
    revertir,
)
from motor.flota.manifiesto import Herramienta, Politica, Puesto, guardar

POLITICA = (
    'generacion="ensayo"\n[[puesto]]\nfuncion="director"\n'
    'modelo=["p/m"]\nesfuerzo=["low"]\n'
)
POLITICA_B = (
    'generacion="ensayo-b"\n[[puesto]]\nfuncion="director"\n'
    'modelo=["p/m"]\nesfuerzo=["low"]\n'
)
GROK = {
    "nombre": "grok",
    "tipo": "npm",
    "paquete": "@xai-official/grok",
    "version": "v2",
    "fuente": "https://registry.npmjs.org/%40xai-official%2Fgrok/latest",
    "tarball": "https://registry.npmjs.org/@xai-official/grok/-/grok-v2.tgz",
    "integridad": "sha512-AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA",
}
OPENCLAW = {
    "nombre": "openclaw",
    "tipo": "npm",
    "paquete": "openclaw",
    "version": "v2",
    "fuente": "https://registry.npmjs.org/openclaw/latest",
    "tarball": "https://registry.npmjs.org/openclaw/-/openclaw-v2.tgz",
    "integridad": "sha512-BBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB",
}


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


def _runtime_qwen(home: Path, gen: Path) -> None:
    """Agrega un lanzador de qwen al runtime existente de la generación."""
    ruta = gen / "runtime.json"
    runtime = json.loads(ruta.read_text(encoding="utf-8"))
    argv = [sys.executable, "-c", "print('v-qwen')"]
    runtime["lanzadores"]["qwen"] = argv
    runtime["componentes"].append({"nombre": "qwen", "version": "v-qwen"})
    runtime.setdefault("observaciones", {})["qwen"] = {
        "version": "v-qwen",
        "ejecutable": argv,
    }
    _atomico(ruta, runtime)
    recibo = home / "preparaciones" / (gen.name + ".json")
    doc = json.loads(recibo.read_text(encoding="utf-8"))
    doc["componentes"] = sorted(set(doc.get("componentes", [])) | {"qwen"})
    doc["integridad_runtime"] = hashlib.sha256(ruta.read_bytes()).hexdigest()
    _atomico(recibo, doc)


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
        self.assertEqual(aplicar(home, a, sin_turnos=True)["accion"], "aplicada")
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

    def test_todos_los_turnos_fallan_en_ambas_no_se_aplica(self):
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
        self.assertEqual(salida["accion"], "rechazada", salida)
        self.assertIn("ningun turno real fue exitoso", salida["motivo"])
        self.assertIn("fallo_comun", salida["motivo"])
        self.assertEqual(asignar(self.home), a)
        informe = json.loads(
            (self.home / "turnos" / (b + ".json")).read_text(encoding="utf-8")
        )
        self.assertEqual(informe["turnos"]["codex"]["estado"], "fallo_comun")
        pendientes = sorted(
            (self.motor_motor / "avisos" / "pendientes").glob("*.json")
        )
        self.assertEqual(len(pendientes), 1)
        aviso = json.loads(pendientes[0].read_text(encoding="utf-8"))
        self.assertIn("ningun turno real exitoso en la candidata", aviso["texto"])

    def test_fallo_comun_con_algun_exitoso_no_bloquea(self):
        origen, a, gen_a = self._flota("origen-l")
        b, gen_b = _crear_generacion(self.home, origen, [], {}, POLITICA_B)
        _config_poteto(gen_b)
        _runtime_preparada(self.home, gen_b, "codex", "v1")
        _runtime_qwen(self.home, gen_b)

        def turno(agente, gen, raiz, tope_s=150):
            if agente == "qwen":
                return {
                    "estado": "fallo",
                    "rc": 1,
                    "segundos": 1.0,
                    "error": "la cuenta no tiene cupo hoy",
                }
            return {"estado": "ok", "salida": "listo", "segundos": 1.0, "rc": 0}

        with (
            patch(
                "motor.flota.turnos._sesion_presente",
                side_effect=lambda agente: agente in ("codex", "qwen"),
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
        self.assertEqual(informe["turnos"]["qwen"]["estado"], "fallo_comun")
        self.assertEqual(informe["turnos"]["codex"]["estado"], "ok")

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

    def test_par_sin_clientes_no_se_aplica_y_el_flag_del_operador_si(self):
        origen, a, gen_a = self._flota("origen-k")
        _runtime_preparada(self.home, gen_a, "hermes", "v1")
        b, gen_b = _crear_generacion(self.home, origen, [], {}, POLITICA_B)
        _config_poteto(gen_b)
        _runtime_preparada(self.home, gen_b, "hermes", "v2")

        with patch.dict(os.environ, {"MOTOR_HOME": str(self.motor_motor)}):
            salida = aplicar(self.home, b)
        self.assertEqual(salida["accion"], "rechazada", salida)
        self.assertIn("no se probó ningún agente", salida["motivo"])
        self.assertEqual(asignar(self.home), a)
        candidata = json.loads((self.home / "candidata.json").read_text())
        self.assertEqual(candidata["estado"], "rechazada")
        pendientes = sorted(
            (self.motor_motor / "avisos" / "pendientes").glob("*.json")
        )
        self.assertEqual(len(pendientes), 1)
        aviso = json.loads(pendientes[0].read_text(encoding="utf-8"))
        self.assertIn("no se probó ningún agente", aviso["texto"])

        with patch.dict(os.environ, {"MOTOR_HOME": str(self.motor_motor)}):
            con_flag = aplicar(self.home, b, sin_turnos=True)
        self.assertEqual(con_flag["accion"], "aplicada", con_flag)
        self.assertEqual(asignar(self.home), b)
        instalacion = json.loads((self.home / "instalacion.json").read_text())
        self.assertTrue(instalacion.get("sin_turnos"), instalacion)

    def test_primer_arranque_sin_clientes_no_se_aplica_y_el_flag_si(self):
        origen = _repo(self.base / "origen-primer-sin-clientes")
        a, gen_a = _crear_generacion(self.home, origen, [], {}, POLITICA)
        _config_poteto(gen_a)
        _runtime_preparada(self.home, gen_a, "hermes", "v1")

        with patch.dict(os.environ, {"MOTOR_HOME": str(self.motor_motor)}):
            salida = aplicar(self.home, a)
        self.assertEqual(salida["accion"], "rechazada", salida)
        self.assertIn("no se probó ningún agente", salida["motivo"])
        self.assertFalse((self.home / "actual").is_symlink())
        candidata = json.loads((self.home / "candidata.json").read_text())
        self.assertEqual(candidata["estado"], "rechazada")
        pendientes = sorted(
            (self.motor_motor / "avisos" / "pendientes").glob("*.json")
        )
        self.assertEqual(len(pendientes), 1)
        aviso = json.loads(pendientes[0].read_text(encoding="utf-8"))
        self.assertIn("no se probó ningún agente", aviso["texto"])

        with patch.dict(os.environ, {"MOTOR_HOME": str(self.motor_motor)}):
            con_flag = aplicar(self.home, a, sin_turnos=True)
        self.assertEqual(con_flag["accion"], "aplicada", con_flag)
        instalacion = json.loads((self.home / "instalacion.json").read_text())
        self.assertTrue(instalacion.get("sin_turnos"), instalacion)

    def test_recuperar_primer_arranque_sin_clientes_no_activa(self):
        origen = _repo(self.base / "origen-recuperar-sin-clientes")
        a, gen_a = _crear_generacion(self.home, origen, [], {}, POLITICA)
        _config_poteto(gen_a)
        _runtime_preparada(self.home, gen_a, "hermes", "v1")

        cortado = aplicar(self.home, a, "worktree")
        self.assertEqual(cortado["accion"], "cortado", cortado)
        with patch.dict(os.environ, {"MOTOR_HOME": str(self.motor_motor)}):
            salida = recuperar(self.home)
        self.assertEqual(salida["accion"], "rechazada", salida)
        self.assertIn("no se probó ningún agente", salida["motivo"])
        self.assertFalse((self.home / "actual").is_symlink())
        self.assertFalse((self.home / "instalacion-pendiente.json").exists())

    def test_recuperar_usa_el_sin_turnos_guardado_en_la_pendiente(self):
        origen = _repo(self.base / "origen-recuperar-flag-pendiente")
        a, gen_a = _crear_generacion(self.home, origen, [], {}, POLITICA)
        _config_poteto(gen_a)
        _runtime_preparada(self.home, gen_a, "hermes", "v1")

        cortado = aplicar(self.home, a, "worktree", sin_turnos=True)
        self.assertEqual(cortado["accion"], "cortado", cortado)
        pendiente = json.loads(
            (self.home / "instalacion-pendiente.json").read_text()
        )
        self.assertTrue(pendiente.get("sin_turnos"), pendiente)

        salida = recuperar(self.home)
        self.assertEqual(salida["accion"], "completo", salida)
        self.assertEqual(asignar(self.home), a)
        instalacion = json.loads((self.home / "instalacion.json").read_text())
        self.assertTrue(instalacion.get("sin_turnos"), instalacion)

    def test_recuperar_rechazo_por_turnos_devuelve_la_vigente(self):
        origen, a, gen_a = self._flota("origen-recuperar-activa")
        b, gen_b = _crear_generacion(self.home, origen, [], {}, POLITICA_B)
        _config_poteto(gen_b)
        _runtime_preparada(self.home, gen_b, "hermes", "v2")

        cortado = aplicar(self.home, b, "worktree")
        self.assertEqual(cortado["accion"], "cortado", cortado)
        # Activacion interrumpida: `actual` ya apunta a la candidata rechazada.
        _poner_enlace(self.home, gen_b)
        self.assertEqual(asignar(self.home), b)

        with patch.dict(os.environ, {"MOTOR_HOME": str(self.motor_motor)}):
            salida = recuperar(self.home)
        self.assertEqual(salida["accion"], "rechazada", salida)
        self.assertIn("no se probó ningún agente", salida["motivo"])
        self.assertEqual(asignar(self.home), a)
        candidata = json.loads((self.home / "candidata.json").read_text())
        self.assertEqual(candidata["estado"], "rechazada")
        self.assertFalse((self.home / "instalacion-pendiente.json").exists())

    def test_candidata_que_retira_un_agente_con_sesion_bloquea(self):
        origen, a, gen_a = self._flota("origen-g")
        b, gen_b = _crear_generacion(self.home, origen, [], {}, POLITICA_B)
        _config_poteto(gen_b)
        _runtime_preparada(self.home, gen_b, "codex", "v-turno")

        def lanzador_con_retiro(agente, gen):
            if gen is not None and gen.resolve() == gen_b.resolve():
                return None
            return ["/bin/true"]

        with patch.dict(os.environ, {"MOTOR_HOME": str(self.motor_motor)}):
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
        pendientes = sorted(
            (self.motor_motor / "avisos" / "pendientes").glob("*.json")
        )
        self.assertEqual(len(pendientes), 1)
        aviso = json.loads(pendientes[0].read_text(encoding="utf-8"))
        self.assertIn("retira el ejecutable de codex", aviso["texto"])

    def test_fallo_del_control_de_turnos_rechaza_y_avisa(self):
        origen, a, gen_a = self._flota("origen-control-roto")
        b, gen_b = _crear_generacion(self.home, origen, [], {}, POLITICA_B)
        _config_poteto(gen_b)
        _runtime_preparada(self.home, gen_b, "codex", "v2")

        with patch.dict(os.environ, {"MOTOR_HOME": str(self.motor_motor)}):
            with patch(
                "motor.flota.turnos.turnos_candidata",
                side_effect=RuntimeError("boom"),
            ):
                salida = aplicar(self.home, b)
        self.assertEqual(salida["accion"], "rechazada", salida)
        self.assertIn("el control de turnos no pudo correr", salida["motivo"])
        self.assertIn("boom", salida["motivo"])
        self.assertEqual(asignar(self.home), a)
        self.assertFalse((self.home / "instalacion-pendiente.json").exists())
        pendientes = sorted(
            (self.motor_motor / "avisos" / "pendientes").glob("*.json")
        )
        self.assertEqual(len(pendientes), 1)
        aviso = json.loads(pendientes[0].read_text(encoding="utf-8"))
        self.assertIn("boom", aviso["texto"])

    def test_primer_arranque_sin_turno_exitoso_no_se_aplica(self):
        origen = _repo(self.base / "origen-primer")
        a, gen_a = _crear_generacion(
            self.home, origen, [], {}, POLITICA + '[[puesto]]\nfuncion="codigo"\n'
        )
        _config_poteto(gen_a)
        _runtime_preparada(self.home, gen_a, "codex", "v1")

        def turno(agente, gen, raiz, tope_s=150):
            return {
                "estado": "fallo",
                "rc": 1,
                "segundos": 1.0,
                "error": "la cuenta no tiene cupo hoy",
            }

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
                salida = aplicar(self.home, a)
        self.assertEqual(salida["accion"], "rechazada", salida)
        self.assertIn("ningun turno real fue exitoso", salida["motivo"])
        self.assertFalse((self.home / "actual").is_symlink())
        pendientes = sorted(
            (self.motor_motor / "avisos" / "pendientes").glob("*.json")
        )
        self.assertEqual(len(pendientes), 1)

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
                con_flag = aplicar(self.home, a, sin_turnos=True)
        self.assertEqual(con_flag["accion"], "aplicada", con_flag)
        instalacion = json.loads((self.home / "instalacion.json").read_text())
        self.assertTrue(instalacion.get("sin_turnos"), instalacion)

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

    def _replica(self, nombre: str, con_openclaw: bool = False):
        """Home de réplica con una generación vigente que ya exportó config y perfiles."""
        origen, _a, gen_a = self._flota(nombre)
        _runtime_preparada(self.home, gen_a, "grok", "v1")
        ruta = gen_a / "runtime.json"
        runtime = json.loads(ruta.read_text(encoding="utf-8"))
        componentes = [GROK]
        herramientas = [
            Herramienta(
                "grok", GROK["fuente"], GROK["version"], GROK["integridad"]
            )
        ]
        if con_openclaw:
            # OpenClaw también vive en el catálogo exportado: la candidata lo
            # prepara, lo declara lanzador y así la derivación de Claw corre.
            componentes.append(OPENCLAW)
            herramientas.append(
                Herramienta(
                    "openclaw",
                    OPENCLAW["fuente"],
                    OPENCLAW["version"],
                    OPENCLAW["integridad"],
                )
            )
            runtime["lanzadores"]["openclaw"] = list(runtime["lanzadores"]["grok"])
            runtime["observaciones"]["openclaw"] = dict(
                runtime["observaciones"]["grok"]
            )
        runtime["componentes"] = componentes
        _atomico(ruta, runtime)
        guardar(
            self.home / "manifiesto.toml",
            Politica(
                "ensayo",
                (Puesto("director", (("p/m", "low"),)),),
                (),
                tuple(herramientas),
                (),
                None,
                (),
            ),
        )
        snap = self.base / (nombre + "-snap.json")
        exportar(self.home, snap)
        importar(self.home, snap)
        plugin = gen_a / "config/hermes/plugins/goncloud-team/poteto.py"
        plugin.write_text(
            "raise FileNotFoundError('checkout de conf')\n", encoding="utf-8"
        )
        (gen_a / "config/conf").mkdir(parents=True)
        (gen_a / "config/conf/marca.txt").write_text("conf-vigente\n", encoding="utf-8")
        claw = gen_a / "perfiles/claw"
        claw.mkdir(parents=True)
        (claw / "openclaw.json").write_text(
            json.dumps({"workspace": str(gen_a / "perfiles/claw/workspaces/main")}),
            encoding="utf-8",
        )
        perfil = gen_a / "perfiles/hermes/profiles/director"
        perfil.mkdir(parents=True)
        (perfil / "config.yaml").write_text("home: " + str(gen_a) + "\n", encoding="utf-8")
        cache = gen_a / "perfiles/hermes/cache"
        cache.mkdir(parents=True)
        (cache / "no-copiar.txt").write_text("cache\n", encoding="utf-8")
        return origen, gen_a

    def test_replica_sin_checkouts_aplica_la_configuracion_vigente(self):
        origen, gen_a = self._replica("origen-replica")
        (gen_a / "config/hermes/model-routing.source.json").write_text(
            json.dumps(
                {
                    "bindings": {"director": "adaptador"},
                    "chains": {"director": ["viejo (low)"]},
                }
            )
            + "\n",
            encoding="utf-8",
        )

        def fake(doc_, destino, gen, home):
            destino.mkdir(parents=True)
            script = destino / "cli.py"
            script.write_text("print(" + repr(doc_["version"]) + ")", encoding="utf-8")
            return [sys.executable, str(script)], doc_["version"], None

        with (
            patch(
                "motor.flota.actualizaciones._preparar_componente", side_effect=fake
            ),
            patch("motor.flota.turnos._sesion_presente", return_value=False),
        ):
            salida = actualizar(
                self.home,
                motor_origen=origen,
                repos={
                    "hermes": self.base / "no-esta-hermes",
                    "conf": self.base / "no-esta-conf",
                },
                sin_turnos=True,
            )
        self.assertEqual(salida["accion"], "aplicada", salida)
        nueva = self.home / "generaciones" / salida["generacion"]
        self.assertEqual(
            (nueva / "config/conf/marca.txt").read_text(encoding="utf-8"),
            "conf-vigente\n",
        )
        self.assertIn(
            "raise FileNotFoundError",
            (nueva / "config/hermes/plugins/goncloud-team/poteto.py").read_text(
                encoding="utf-8"
            ),
        )
        workspace = json.loads(
            (nueva / "perfiles/claw/openclaw.json").read_text(encoding="utf-8")
        )["workspace"]
        self.assertEqual(
            Path(workspace).resolve(),
            (nueva / "perfiles/claw/workspaces/main").resolve(),
        )
        self.assertNotEqual(
            Path(workspace).resolve(),
            (gen_a / "perfiles/claw/workspaces/main").resolve(),
        )
        home_yaml = (
            (nueva / "perfiles/hermes/profiles/director/config.yaml")
            .read_text(encoding="utf-8")
            .split("home: ", 1)[1]
            .strip()
        )
        self.assertEqual(Path(home_yaml).resolve(), nueva.resolve())
        self.assertFalse((nueva / "perfiles/hermes/cache/no-copiar.txt").exists())
        runtime = json.loads((nueva / "runtime.json").read_text(encoding="utf-8"))
        self.assertEqual(
            runtime["poteto"], "config/hermes/shared/sistema-agentes/poteto"
        )
        tabla = json.loads(
            (nueva / "config/hermes/model-routing.source.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(tabla["bindings"], {"director": "adaptador"})
        self.assertEqual(tabla["chains"], {"director": ["p/m (low)"]})
        self.assertEqual(tabla["source"], "flota/manifesto.toml")
        self.assertEqual(
            runtime["politica"], "config/hermes/model-routing.source.json"
        )

    def test_la_reproyeccion_actualiza_los_perfiles_claw(self):
        origen, gen_a = self._replica("origen-claw", con_openclaw=True)
        hermes = gen_a / "config/hermes"
        (hermes / "model-routing.source.json").write_text(
            json.dumps({"chains": {"director": ["viejo/modelo (low)"]}}) + "\n",
            encoding="utf-8",
        )
        (hermes / "claw-routing.json").write_text(
            json.dumps({"agents": {"main": {"row": "director", "thinking": False}}})
            + "\n",
            encoding="utf-8",
        )
        script = hermes / "scripts/claw_models.py"
        script.parent.mkdir(parents=True, exist_ok=True)
        script.write_text(
            "import json, sys\n"
            "args = sys.argv[1:]\n"
            "fuente = json.load(open(args[args.index('--source') + 1], encoding='utf-8'))\n"
            "modelo = fuente['chains']['director'][0].split(' (', 1)[0]\n"
            "deseado = {'agents': {'entries': {'main': {'model': {\n"
            "'primary': modelo, 'fallbacks': []}}}}}\n"
            "if 'desired' in args:\n"
            "    print(json.dumps(deseado))\n"
            "elif 'check' in args:\n"
            "    vivo = json.load(open(args[args.index('--live') + 1], encoding='utf-8'))\n"
            "    if vivo['agents']['entries']['main']['model']['primary'] != modelo:\n"
            "        raise SystemExit(1)\n",
            encoding="utf-8",
        )
        claw = gen_a / "perfiles/claw"
        viejo = {
            "agents": {
                "entries": {
                    "main": {
                        "model": {"primary": "viejo/modelo", "fallbacks": []},
                        "workspace": str(gen_a / "perfiles/claw/workspaces/main"),
                    }
                }
            }
        }
        (claw / "modelos.json").write_text(json.dumps(viejo) + "\n", encoding="utf-8")
        (claw / "openclaw.json").write_text(json.dumps(viejo) + "\n", encoding="utf-8")

        def fake(doc_, destino, gen, home):
            destino.mkdir(parents=True)
            marca = destino / "cli.py"
            marca.write_text("print(" + repr(doc_["version"]) + ")", encoding="utf-8")
            return [sys.executable, str(marca)], doc_["version"], None

        with patch(
            "motor.flota.actualizaciones._preparar_componente", side_effect=fake
        ):
            salida = preparar(self.home, motor_origen=origen, repos={})
        nueva = self.home / "generaciones" / salida["generacion"]
        modelos = json.loads(
            (nueva / "perfiles/claw/modelos.json").read_text(encoding="utf-8")
        )
        abierto = json.loads(
            (nueva / "perfiles/claw/openclaw.json").read_text(encoding="utf-8")
        )
        self.assertEqual(
            modelos["agents"]["entries"]["main"]["model"]["primary"], "p/m"
        )
        self.assertEqual(
            abierto["agents"]["entries"]["main"]["model"]["primary"], "p/m"
        )
        self.assertEqual(
            Path(abierto["agents"]["entries"]["main"]["workspace"]).resolve(),
            (nueva / "perfiles/claw/workspaces/main").resolve(),
        )
        self.assertEqual(abierto["channels"], {})
        self.assertNotIn("viejo/modelo", json.dumps(modelos))
        self.assertNotIn("viejo/modelo", json.dumps(abierto))
        # El orden importa: config["claw"] se resuelve DESPUÉS de derivar, así
        # que el openclaw.json declarado es el derivado y queda hashado para
        # que comprobar_runtime lo valide. El modelos.json derivado también
        # queda declarado con su huella.
        runtime_nuevo = json.loads(
            (nueva / "runtime.json").read_text(encoding="utf-8")
        )
        self.assertEqual(
            runtime_nuevo["config"]["claw"], "perfiles/claw/openclaw.json"
        )
        declarados = {
            Path(str(clave)).parts for clave in runtime_nuevo["archivos"]
        }
        self.assertIn(Path("perfiles/claw/openclaw.json").parts, declarados)
        self.assertIn(Path("perfiles/claw/modelos.json").parts, declarados)

    def test_la_misma_pieza_se_reutiliza_si_no_se_puede_preparar(self):
        origen, gen_a = self._replica("origen-reuso")
        marca = gen_a / "herramientas/grok/marca.txt"
        marca.parent.mkdir(parents=True)
        marca.write_text("vigente\n", encoding="utf-8")
        real = gen_a / "herramientas/grok/real-python"
        real.write_text("binario\n", encoding="utf-8")
        venv_rel = Path(
            "perfiles/hermes/installs/abc/environments/e/venv/bin/python"
        )
        enlace = gen_a / venv_rel
        enlace.parent.mkdir(parents=True)
        enlace.symlink_to(real.resolve())
        ruta = gen_a / "runtime.json"
        runtime = json.loads(ruta.read_text(encoding="utf-8"))
        runtime["lanzadores"]["grok"] = [venv_rel.as_posix()]
        _atomico(ruta, runtime)

        def falla(doc_, destino, gen, home):
            raise RuntimeError("fallo git [] (salida 128)")

        with patch(
            "motor.flota.actualizaciones._preparar_componente", side_effect=falla
        ):
            salida = preparar(self.home, motor_origen=origen, repos={})
        nueva = self.home / "generaciones" / salida["generacion"]
        self.assertEqual(
            (nueva / "herramientas/grok/marca.txt").read_text(encoding="utf-8"),
            "vigente\n",
        )
        runtime = json.loads((nueva / "runtime.json").read_text(encoding="utf-8"))
        self.assertIn(
            "se reutiliza la instalacion vigente",
            runtime["observaciones"]["grok"]["motivo"],
        )
        nuevo_enlace = nueva / venv_rel
        self.assertTrue(nuevo_enlace.is_symlink(), nuevo_enlace)
        self.assertEqual(
            nuevo_enlace.resolve(),
            (nueva / "herramientas/grok/real-python").resolve(),
        )

    def test_una_pieza_alterada_en_la_vigente_no_se_reutiliza(self):
        origen, gen_a = self._replica("origen-alterada")
        real = gen_a / "herramientas/grok/real-python"
        real.parent.mkdir(parents=True, exist_ok=True)
        real.write_text("binario\n", encoding="utf-8")
        ruta = gen_a / "runtime.json"
        runtime = json.loads(ruta.read_text(encoding="utf-8"))
        runtime["archivos"] = {"herramientas/grok/real-python": _sha256(real)}
        _atomico(ruta, runtime)
        # Alteración posterior a la preparación: el contenido ya no es el registrado.
        real.write_text("alterado\n", encoding="utf-8")

        def falla(doc_, destino, gen, home):
            raise RuntimeError("fallo git [] (salida 128)")

        with patch(
            "motor.flota.actualizaciones._preparar_componente", side_effect=falla
        ):
            with self.assertRaises(RuntimeError) as ctx:
                preparar(self.home, motor_origen=origen, repos={})
        self.assertIn("fallo git", str(ctx.exception))
        self.assertEqual(asignar(self.home), gen_a.name)

    def test_la_pieza_alterada_no_se_reutiliza_con_claves_nativas(self):
        base = self.base / "claves-nativas"
        vigente = base / "vigente"
        pieza = vigente / "herramientas" / "grok"
        pieza.mkdir(parents=True)
        cli = pieza / "cli.txt"
        cli.write_text("original\n", encoding="utf-8")
        argv = [sys.executable, "-c", "print('v2')"]
        doc = dict(GROK)
        runtime = {
            "lanzadores": {"grok": argv},
            "componentes": [GROK],
            "observaciones": {"grok": {"version": "v2", "ejecutable": argv}},
            # La clave sale de str(relative_to(gen)): separador nativo del
            # sistema. El './' delante la deja fuera del prefijo textual
            # 'herramientas/<n>/' que comparaba el codigo anterior (en Linux
            # la forma nativa coincide con la textual y no discriminaria):
            # la comparacion por partes de ruta debe reconocerla igual.
            "archivos": {
                f"./{Path('herramientas') / 'grok' / 'cli.txt'}": _sha256(cli)
            },
        }
        gen_sana = base / "gen-sana"
        gen_sana.mkdir(parents=True)
        sana = _reutilizar_pieza(
            doc, gen_sana / "herramientas" / "grok", gen_sana, vigente, runtime
        )
        self.assertIsNotNone(sana)
        # Alteración posterior a la preparación: el contenido ya no es el
        # registrado y la candidata no debe recibir la pieza.
        cli.write_text("alterado\n", encoding="utf-8")
        gen_alterada = base / "gen-alterada"
        gen_alterada.mkdir(parents=True)
        destino = gen_alterada / "herramientas" / "grok"
        rechazada = _reutilizar_pieza(doc, destino, gen_alterada, vigente, runtime)
        self.assertIsNone(rechazada)
        self.assertFalse(destino.exists())

    def test_una_version_distinta_que_falla_conserva_la_vigente_y_lo_dice(self):
        origen, gen_a = self._replica("origen-distinta")
        ruta = gen_a / "runtime.json"
        runtime = json.loads(ruta.read_text(encoding="utf-8"))
        runtime["componentes"] = [{**GROK, "version": "v1"}]
        runtime["observaciones"]["grok"]["version"] = "v1"
        _atomico(ruta, runtime)

        def falla(doc_, destino, gen, home):
            raise RuntimeError("fallo node [npm-cli.js] (salida 1)")

        with patch(
            "motor.flota.actualizaciones._preparar_componente", side_effect=falla
        ):
            with self.assertRaises(RuntimeError) as ctx:
                preparar(self.home, motor_origen=origen, repos={})
        self.assertIn("se conserva la instalacion vigente", str(ctx.exception))
        self.assertIn("v1", str(ctx.exception))
        self.assertIn("v2", str(ctx.exception))
        self.assertEqual(asignar(self.home), gen_a.name)

    def test_reutilizar_npm_copia_el_node_de_la_generacion(self):
        origen, gen_a = self._replica("origen-node")
        huella = "ab" * 32
        node_rel = Path("herramientas/node") / huella / "node.exe"
        nodo = gen_a / node_rel
        nodo.parent.mkdir(parents=True)
        nodo.write_bytes(b"NODE" + str(gen_a).encode() + b"NODE")
        (gen_a / "herramientas/grok").mkdir(parents=True, exist_ok=True)
        (gen_a / "herramientas/grok/cli.js").write_text(
            "console.log('v2')\n", encoding="utf-8"
        )
        ruta = gen_a / "runtime.json"
        runtime = json.loads(ruta.read_text(encoding="utf-8"))
        runtime["lanzadores"]["grok"] = [
            node_rel.as_posix(),
            "herramientas/grok/cli.js",
        ]
        _atomico(ruta, runtime)

        def falla(doc_, destino, gen, home):
            raise RuntimeError("fallo node [npm-cli.js] (salida 1)")

        with patch(
            "motor.flota.actualizaciones._preparar_componente", side_effect=falla
        ):
            salida = preparar(self.home, motor_origen=origen, repos={})
        nueva = self.home / "generaciones" / salida["generacion"]
        copiado = nueva / node_rel
        self.assertTrue(copiado.is_file(), copiado)
        self.assertEqual(copiado.read_bytes(), nodo.read_bytes())
        runtime = json.loads((nueva / "runtime.json").read_text(encoding="utf-8"))
        self.assertEqual(
            runtime["lanzadores"]["grok"][0], node_rel.as_posix()
        )


class CopiaConfigReplica(unittest.TestCase):
    def test_las_rutas_escapadas_en_json_se_reescriben(self):
        viejo = "C:\\flota\\generaciones\\aaa"
        nuevo = "C:\\flota\\generaciones\\bbb"
        with tempfile.TemporaryDirectory(prefix="copia-json-") as tmp:
            origen = Path(tmp) / "config"
            origen.mkdir()
            (origen / "openclaw.json").write_text(
                json.dumps({"workspace": viejo + "\\perfiles\\claw"}) + "\n",
                encoding="utf-8",
            )
            (origen / "nota.txt").write_text("home: " + viejo + "\n", encoding="utf-8")
            destino = Path(tmp) / "nueva"
            _copiar_arbol(origen, destino, [viejo], nuevo)
            workspace = json.loads(
                (destino / "openclaw.json").read_text(encoding="utf-8")
            )["workspace"]
            self.assertEqual(workspace, nuevo + "\\perfiles\\claw")
            self.assertEqual(
                (destino / "nota.txt").read_text(encoding="utf-8"),
                "home: " + nuevo + "\n",
            )

    def test_la_copia_conserva_permisos_y_symlinks(self):
        with tempfile.TemporaryDirectory(prefix="copia-modo-") as tmp:
            origen = Path(tmp) / "config"
            privado = origen / "bin"
            privado.mkdir(parents=True)
            os.chmod(privado, 0o700)
            script = privado / "tool.sh"
            script.write_text("#!/bin/sh\n", encoding="utf-8")
            os.chmod(script, 0o750)
            real = origen / "real.txt"
            real.write_text("dato\n", encoding="utf-8")
            enlace = origen / "enlace"
            enlace.symlink_to("real.txt")
            absoluto = origen / "enlace-abs"
            absoluto.symlink_to(real)
            solo_lectura = origen / "solo-lectura"
            solo_lectura.mkdir()
            (solo_lectura / "adentro.txt").write_text("si\n", encoding="utf-8")
            os.chmod(solo_lectura, 0o500)
            destino = Path(tmp) / "nueva"
            try:
                _copiar_arbol(origen, destino, [str(origen)], str(destino))
                self.assertEqual(
                    (destino / "solo-lectura" / "adentro.txt").read_text(
                        encoding="utf-8"
                    ),
                    "si\n",
                )
                if os.name == "posix":
                    self.assertEqual(
                        (destino / "solo-lectura").stat().st_mode & 0o777, 0o500
                    )
            finally:
                os.chmod(solo_lectura, 0o700)
                if (destino / "solo-lectura").exists():
                    os.chmod(destino / "solo-lectura", 0o700)
            self.assertTrue((destino / "enlace").is_symlink())
            self.assertEqual(os.readlink(destino / "enlace"), "real.txt")
            self.assertTrue((destino / "enlace-abs").is_symlink())
            self.assertEqual(os.readlink(destino / "enlace-abs"), str(destino / "real.txt"))
            self.assertEqual(
                (destino / "real.txt").read_text(encoding="utf-8"), "dato\n"
            )
            if os.name == "posix":
                self.assertEqual((destino / "bin" / "tool.sh").stat().st_mode & 0o777, 0o750)
                self.assertEqual((destino / "bin").stat().st_mode & 0o777, 0o700)


class GuardaHomeTest(unittest.TestCase):
    def test_la_guarda_bloquea_escrituras_en_el_motor_real(self):
        import tests.guarda_home as guarda

        with tempfile.TemporaryDirectory(prefix="guarda-motor-") as tmp:
            falso = Path(tmp) / "motor-real"
            (falso / "avisos" / "pendientes").mkdir(parents=True)
            aviso = falso / "avisos" / "pendientes" / "aviso.json"
            with patch.object(guarda, "_MOTOR_REAL", os.path.realpath(falso)):
                self.assertTrue(guarda._prohibida(aviso))
                with self.assertRaises(RuntimeError):
                    aviso.write_text("{}", encoding="utf-8")
            self.assertFalse(aviso.exists())

    def test_la_guarda_permite_escribir_en_el_temporal(self):
        import tests.guarda_home as guarda

        with tempfile.TemporaryDirectory(prefix="guarda-temp-") as tmp:
            aviso = Path(tmp) / "aviso.json"
            self.assertFalse(guarda._prohibida(aviso))
            aviso.write_text("{}", encoding="utf-8")
            self.assertTrue(aviso.exists())

    def test_la_guarda_audita_este_hilo_aunque_otro_este_en_el_gancho(self):
        import threading

        import tests.guarda_home as guarda

        with tempfile.TemporaryDirectory(prefix="guarda-hilos-") as tmp:
            falso = Path(tmp) / "motor-real"
            (falso / "avisos").mkdir(parents=True)
            aviso = falso / "avisos" / "aviso.json"
            leento = Path(tmp) / "leento.json"
            leento.write_text("", encoding="utf-8")
            dentro = threading.Event()
            esperar = threading.Event()
            original = guarda._prohibida

            def lenta(ruta):
                if os.path.realpath(ruta) == os.path.realpath(leento):
                    dentro.set()
                    esperar.wait(2)
                return original(ruta)

            def el_otro():
                with patch.object(guarda, "_prohibida", side_effect=lenta):
                    guarda._auditar("open", (str(leento), "w", None))

            with patch.object(guarda, "_MOTOR_REAL", os.path.realpath(falso)):
                hilo = threading.Thread(target=el_otro)
                hilo.start()
                try:
                    self.assertTrue(
                        dentro.wait(2), "el otro hilo nunca llego al gancho"
                    )
                    with self.assertRaises(RuntimeError):
                        aviso.write_text("{}", encoding="utf-8")
                finally:
                    esperar.set()
                    hilo.join(5)
            self.assertFalse(aviso.exists())


if __name__ == "__main__":
    unittest.main()
