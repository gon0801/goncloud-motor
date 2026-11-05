"""El nivel global no convierte una fase documental en un despliegue."""

from dataclasses import replace
import json
from pathlib import Path
import subprocess
import tempfile
import unittest

from motor.encargos import fases
from motor.encargos.dominio import VersionProducto, con_fase, nivel_cumplido, reducir
from motor.encargos.registro import Registro


class PoliticaDeFase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.espacio = Path(self.tmp.name) / "repo"
        self.espacio.mkdir()
        self.interno = self.espacio / ".interno"
        self.interno.mkdir()
        self.reg = Registro(Path(self.tmp.name) / "registro")
        self.addCleanup(self.reg.close)
        self.reg.anotar("e", {"kind": "entrega", "data": {
            "id": "e", "clave": "e", "huella": "e", "texto": "Ejecuta plans/producto.md",
            "entrega_nivel": "desplegado", "repo": "owner/repo",
        }}, "entrega")
        self.propuesta = [
            {"id": "A", "resultado": "Documentación y SQL"},
            {"id": "B", "resultado": "Receta disponible", "requiere": ["A"]},
            {"id": "D", "resultado": "Servicio desplegado", "requiere": ["B"]},
        ]

    def organizar(self, propuesta=None):
        self.reg.anotar("e", {"kind": "fases", "data": {
            "fases": propuesta or self.propuesta,
        }}, "fases")
        return self.reg.fold("e")

    def plan(self, texto):
        ruta = self.espacio / "plans/producto.md"
        ruta.parent.mkdir(exist_ok=True)
        ruta.write_text(texto, encoding="utf-8")
        return ruta

    def integrada(self, enc):
        return con_fase(enc, replace(enc.fase_actual, nivel_alcanzado="integrado"))

    def test_metadata_false_sobrevive_lectura_y_cierra_a_integrada(self):
        self.propuesta[0]["requiere_despliegue"] = False
        (self.interno / "fases.json").write_text(json.dumps(self.propuesta))
        enc = self.organizar(list(fases.leer_propuesta(self.interno)))
        from motor.encargos.politica_despliegue import adoptar
        enc = adoptar(self.reg, enc, self.espacio, "h")
        self.assertTrue(nivel_cumplido(self.integrada(enc)))

    def test_metadata_sin_politica_adoptada_no_permite_cierre(self):
        self.propuesta[0]["requiere_despliegue"] = False
        enc = self.organizar()
        self.assertFalse(nivel_cumplido(self.integrada(enc)))

    def test_propuesta_no_cambia_politica_ya_adoptada(self):
        from motor.encargos.politica_despliegue import adoptar
        enc = self.organizar()
        self.plan("## Bloque A — SQL\nlane: fast\n")
        adoptar(self.reg, enc, self.espacio, "h")
        self.propuesta[0]["requiere_despliegue"] = True
        resultado = self.reg.anotar("e", {"kind": "fases", "data": {
            "fases": self.propuesta,
        }}, "cambio-politica")
        self.assertEqual(resultado, "ilegal")

    def test_metadata_booleana_no_acepta_texto_false(self):
        self.propuesta[0]["requiere_despliegue"] = "false"
        (self.interno / "fases.json").write_text(json.dumps(self.propuesta))
        with self.assertRaisesRegex(ValueError, "booleano"):
            fases.leer_propuesta(self.interno)

    def test_lane_json_equivale_al_contrato_del_plan(self):
        from motor.encargos.politica_despliegue import adoptar
        self.propuesta[0]["lane"] = "gate"
        (self.interno / "fases.json").write_text(json.dumps(self.propuesta))
        enc = self.organizar(list(fases.leer_propuesta(self.interno)))
        enc = adoptar(self.reg, enc, self.espacio, "h")
        self.assertTrue(nivel_cumplido(self.integrada(enc)))

    def test_desconocida_no_rebaja_el_nivel_global(self):
        self.assertFalse(nivel_cumplido(self.integrada(self.organizar())))

    def test_metadata_de_receta_sobrevive_al_registro_y_legacy_no_la_borra(self):
        receta = {"desplegar": "ops/deploy.sh", "checklist": "ops/check.sh", "rollback": "ops/back.sh"}
        self.propuesta[0].update(requiere_despliegue=True, despliegue=receta)
        (self.interno / "fases.json").write_text(json.dumps(self.propuesta))
        enc = self.organizar(list(fases.leer_propuesta(self.interno)))
        from motor.encargos.politica_despliegue import adoptar, resolver
        enc = adoptar(self.reg, enc, self.espacio, "h")
        legacy = [{k: v for k, v in f.items() if k not in ("requiere_despliegue", "despliegue")} for f in self.propuesta]
        self.reg.anotar("e", {"kind": "fases", "data": {"fases": legacy}}, "legacy")
        politica = resolver(self.espacio, self.reg.fold("e"))
        self.assertIs(politica.requiere_despliegue, True)
        self.assertEqual((politica.desplegar, politica.checklist, politica.rollback),
                         ("ops/deploy.sh", "ops/check.sh", "ops/back.sh"))

    def test_plan_legacy_adopta_politica_una_vez_y_cierre_avanza_a_b(self):
        from motor.encargos.politica_despliegue import adoptar
        enc = self.organizar()
        self.plan("## Bloque A — SQL\nlane: fast\ndepende: preexistente\n\n## Bloque B — receta\nlane: gate\nprueba-real: test -f ops/deploy.sh\n")
        enc = adoptar(self.reg, enc, self.espacio, "h1")
        enc = adoptar(self.reg, enc, self.espacio, "h2")
        eventos = [e for e in self.reg.eventos("e") if e["kind"] == "politica_despliegue"]
        self.assertEqual(len(eventos), 1)
        self.assertEqual(eventos[0]["data"]["fase"], "A")
        self.assertIs(eventos[0]["data"]["requiere_despliegue"], False)
        cerrado = reducir(self.integrada(enc), {"kind": "comprobacion", "data": {
            "clase": "cierre_comprobado", "fase": "A", "evidencia": "PR integrado y revisión vigente",
        }})
        self.assertEqual(cerrado.fase_activa, "B")
        self.assertEqual(cerrado.fases[0].estado, "preparada")

    def test_release_lee_solo_receta_de_su_bloque(self):
        from motor.encargos.politica_despliegue import resolver
        enc = replace(self.organizar(), fase_activa="D")
        self.plan("## Bloque A — documentos\nlane: fast\n## Bloque D — servicio\nlane: release\ndespliegue: desplegar=ops/deploy.sh checklist=ops/check.sh rollback=ops/back.sh\n")
        politica = resolver(self.espacio, enc)
        self.assertIs(politica.requiere_despliegue, True)
        self.assertEqual(politica.desplegar, "ops/deploy.sh")
        self.assertEqual(politica.checklist, "ops/check.sh")
        self.assertEqual(politica.rollback, "ops/back.sh")
        self.assertFalse(nivel_cumplido(self.integrada(enc)))

    def test_receta_explicita_invalida_no_usa_la_receta_del_repo(self):
        from motor.encargos.politica_despliegue import adoptar, resolver
        enc = self.organizar()
        (self.espacio / ".motor.toml").write_text(
            '[deploy]\ndesplegar="repo/deploy.sh"\n'
            'checklist="repo/check.sh"\nrollback="repo/back.sh"\n'
        )
        for receta in (
            "desplegar=ops/deploy.sh checklist=ops/check.sh rolback=ops/back.sh",
            "desplegar=ops/deploy.sh checklist=ops/check.sh rollback=",
            'desplegar="ops/deploy.sh checklist=ops/check.sh rollback=ops/back.sh',
            "desplegar=ops/deploy.sh checklist=ops/check.sh rollback=ops/back.sh rollback=otro.sh",
            "",
        ):
            with self.subTest(receta=receta):
                self.plan(f"## Bloque A — servicio\nlane: release\ndespliegue: {receta}\n")
                politica = resolver(self.espacio, enc)
                self.assertIsNone(politica.requiere_despliegue)
                self.assertEqual((politica.desplegar, politica.checklist, politica.rollback), ("", "", ""))
                adoptado = adoptar(self.reg, enc, self.espacio, "h")
                self.assertFalse(nivel_cumplido(self.integrada(adoptado)))
                self.assertFalse(any(e["kind"] == "politica_despliegue" for e in self.reg.eventos("e")))

    def test_gate_con_receta_explicita_valida_exige_despliegue(self):
        from motor.encargos.politica_despliegue import adoptar, resolver
        enc = self.organizar()
        self.plan(
            "## Bloque A — servicio\nlane: gate\n"
            "despliegue: desplegar=ops/deploy.sh checklist=ops/check.sh rollback=ops/back.sh\n"
        )
        politica = resolver(self.espacio, enc)
        self.assertIs(politica.requiere_despliegue, True)
        self.assertEqual((politica.desplegar, politica.checklist, politica.rollback),
                         ("ops/deploy.sh", "ops/check.sh", "ops/back.sh"))
        adoptado = adoptar(self.reg, enc, self.espacio, "h")
        self.assertIs(adoptado.fase_actual.requiere_despliegue, True)
        self.assertFalse(nivel_cumplido(self.integrada(adoptado)))

    def test_release_sin_linea_despliegue_usa_receta_del_repo(self):
        from motor.encargos.politica_despliegue import resolver
        enc = self.organizar()
        self.plan("## Bloque A — servicio\nlane: release\n")
        (self.espacio / ".motor.toml").write_text(
            '[deploy]\ndesplegar="repo/deploy.sh"\n'
            'checklist="repo/check.sh"\nrollback="repo/back.sh"\n'
        )
        politica = resolver(self.espacio, enc)
        self.assertIs(politica.requiere_despliegue, True)
        self.assertEqual((politica.desplegar, politica.checklist, politica.rollback),
                         ("repo/deploy.sh", "repo/check.sh", "repo/back.sh"))

    def test_plan_sin_lane_no_se_deduce_por_prosa_ni_otros_bloques(self):
        from motor.encargos.politica_despliegue import resolver
        enc = self.organizar()
        for texto in ("## Bloque A — solo documentación, sin deploy\nprueba-real: true\n",
                      "## Bloque B — receta\nlane: gate\n",
                      "## Bloque A — SQL\nlane: desconocido\n"):
            with self.subTest(texto=texto):
                self.plan(texto)
                self.assertIsNone(resolver(self.espacio, enc).requiere_despliegue)

    def test_plan_adjunto_explicito_y_ruta_ajena_no_autorizada(self):
        from motor.encargos.politica_despliegue import resolver
        enc = self.organizar()
        for nombre in ("adjunto.md", "adjunto.txt"):
            with self.subTest(nombre=nombre):
                externo = Path(self.tmp.name) / nombre
                externo.write_text("## Bloque A — SQL\nlane: fast\n")
                self.assertIsNone(resolver(self.espacio, replace(enc, texto=str(externo))).requiere_despliegue)
                self.assertIs(resolver(self.espacio, replace(enc, adjuntos=(str(externo),))).requiere_despliegue, False)

    def test_plan_del_commit_integrado_prevalece_sobre_edicion_local(self):
        from motor.encargos.politica_despliegue import resolver
        enc = self.organizar()
        archivo = self.plan("## Bloque A — servicio\nlane: release\n")
        def git(*args):
            return subprocess.check_output(["git", "-C", str(self.espacio), *args], text=True).strip()
        git("init", "-q")
        git("add", "plans/producto.md")
        git("-c", "user.name=Prueba", "-c", "user.email=prueba@example.test", "commit", "-qm", "plan")
        enc = con_fase(enc, replace(enc.fase_actual, integrado_sha=git("rev-parse", "HEAD")))
        archivo.write_text("## Bloque A — documentos\nlane: fast\n")
        self.assertIs(resolver(self.espacio, enc).requiere_despliegue, True)

        # Un merge remoto aún no descargado puede usar el producto revisado,
        # pero nunca esa edición local que convertiría release en fast.
        enc = con_fase(enc, replace(
            enc.fase_actual,
            integrado_sha="f" * 40,
            producto=VersionProducto.desde("v1", (), git("rev-parse", "HEAD")),
        ))
        self.assertIs(resolver(self.espacio, enc).requiere_despliegue, True)

    def test_implicita_despliega_y_toma_receta_de_motor_toml(self):
        from motor.encargos.politica_despliegue import resolver
        (self.espacio / ".motor.toml").write_text('[deploy]\ndesplegar="ops/deploy.sh"\nchecklist="ops/check.sh"\nrollback="ops/back.sh"\nrequiere=["ssh"]\n')
        politica = resolver(self.espacio, self.reg.fold("e"))
        self.assertIs(politica.requiere_despliegue, True)
        self.assertEqual(politica.desplegar, "ops/deploy.sh")

    def test_recibo_desplegado_permanece_ligado_a_fase(self):
        enc = self.organizar()
        enc = reducir(enc, {"kind": "entrega_externa", "data": {
            "fase": "A", "paso": "desplegado", "recibo": "deploy-real-1",
        }})
        self.assertEqual(enc.fase_actual.desplegado_recibo, "deploy-real-1")
