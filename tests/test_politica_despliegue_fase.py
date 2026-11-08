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


IDS_DESCRIPTIVOS = (
    "propuesta", "motor-despliegue", "motor-meta", "meta-sombra", "meta-visible",
    "meta-viva", "retiro-tipado", "pausa-sombra", "pausa-lista",
)
RECETA = "desplegar=ops/deploy.sh checklist=ops/check.sh rollback=ops/back.sh"
PLAN_DESCRIPTIVO = "\n".join(
    f"## Bloque {letra} — {ident}\nlane: {'gate' if i < 3 else 'release'}\n"
    + (f"despliegue: {RECETA}\n" if i >= 3 else "")
    for i, (letra, ident) in enumerate(zip("ABCDEFGHI", IDS_DESCRIPTIVOS))
)


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
        return con_fase(enc, replace(enc.fase_actual, ultimo_paso_entrega="integrado"))

    def test_ids_descriptivos_corresponden_a_los_nueve_bloques_en_orden(self):
        from motor.encargos.politica_despliegue import resolver
        enc = self.organizar([{"id": ident, "resultado": ident} for ident in IDS_DESCRIPTIVOS])
        self.plan(PLAN_DESCRIPTIVO)
        for i, ident in enumerate(IDS_DESCRIPTIVOS):
            with self.subTest(fase=ident):
                politica = resolver(self.espacio, replace(enc, fase_activa=ident))
                self.assertIs(politica.requiere_despliegue, i >= 3)
                self.assertIn(f"#Bloque {'ABCDEFGHI'[i]}", politica.fuente)
                self.assertEqual(politica.desplegar, "ops/deploy.sh" if i >= 3 else "")

    def test_adopta_y_conserva_la_correspondencia_descriptiva_del_plan_adjunto(self):
        from motor.encargos.politica_despliegue import adoptar, resolver
        propuesta = [{"id": ident, "resultado": ident} for ident in IDS_DESCRIPTIVOS]
        enc = self.organizar(propuesta)
        externo = Path(self.tmp.name) / "adjunto.md"
        externo.write_text(PLAN_DESCRIPTIVO, encoding="utf-8")
        enc = replace(enc, texto="Usa el plan adjunto", adjuntos=(str(externo),))
        adoptado = adoptar(self.reg, enc, self.espacio, "h1")
        eventos = [ev for ev in self.reg.eventos("e") if ev["kind"] == "politica_despliegue"]
        self.assertEqual(len(eventos), 1)
        self.assertEqual(eventos[0]["data"]["fase"], "propuesta")
        self.assertIn("#Bloque A", eventos[0]["data"]["fuente"])
        self.assertEqual(eventos[0]["data"]["correspondencia"], {
            "archivo": str(externo.resolve()),
            "fases": [list(par) for par in zip(IDS_DESCRIPTIVOS, "ABCDEFGHI")],
        })
        self.assertTrue(nivel_cumplido(self.integrada(adoptado)))
        adoptar(self.reg, adoptado, self.espacio, "h2")
        self.assertEqual(len([ev for ev in self.reg.eventos("e") if ev["kind"] == "politica_despliegue"]), 1)
        externo.write_text(PLAN_DESCRIPTIVO.replace("lane: gate", "lane: release"), encoding="utf-8")
        self.assertIs(resolver(self.espacio, adoptado).requiere_despliegue, False)

    def test_mapa_incompleto_o_sustituido_no_cambia_el_contrato_adoptado(self):
        from motor.encargos.politica_despliegue import adoptar
        propuesta = [{"id": ident, "resultado": ident} for ident in IDS_DESCRIPTIVOS]
        self.plan(PLAN_DESCRIPTIVO)
        enc = adoptar(self.reg, self.organizar(propuesta), self.espacio, "h1")
        mapa = list(enc.despliegue_mapa)
        intercambiado = mapa.copy()
        intercambiado[1] = (mapa[1][0], mapa[3][1])
        intercambiado[3] = (mapa[3][0], mapa[1][1])
        for i, (archivo, pares) in enumerate((
            (enc.despliegue_archivo, mapa[:-1]),
            (enc.despliegue_archivo, mapa[:-1] + [mapa[0]]),
            (enc.despliegue_archivo, mapa[:-1] + [(mapa[-1][0], "A")]),
            (enc.despliegue_archivo, intercambiado),
            (enc.despliegue_archivo + ".otro", mapa),
        )):
            with self.subTest(caso=i):
                resultado = self.reg.anotar("e", {"kind": "politica_despliegue", "data": {
                    "fase": "propuesta", "requiere_despliegue": False,
                    "fuente": "otra", "huella": "otra",
                    "correspondencia": {"archivo": archivo, "fases": pares},
                }}, f"cambio-mapa-{i}")
                self.assertEqual(resultado, "ilegal")
                self.assertEqual(self.reg.fold("e").despliegue_mapa, enc.despliegue_mapa)

    def test_reordenar_fases_no_cambia_los_bloques_ya_asociados(self):
        from motor.encargos.politica_despliegue import adoptar, resolver
        propuesta = [{"id": ident, "resultado": ident} for ident in IDS_DESCRIPTIVOS]
        self.plan(PLAN_DESCRIPTIVO)
        adoptar(self.reg, self.organizar(propuesta), self.espacio, "h1")
        propuesta[1], propuesta[3] = propuesta[3], propuesta[1]
        self.assertEqual(self.reg.anotar("e", {"kind": "fases", "data": {
            "fases": propuesta,
        }}, "fases-reordenadas"), "nuevo")
        enc = self.reg.fold("e")
        for ident, despliega in (("motor-despliegue", False), ("meta-sombra", True)):
            with self.subTest(fase=ident):
                self.assertIs(resolver(self.espacio, replace(enc, fase_activa=ident)).requiere_despliegue, despliega)

    def test_reordenar_bloques_del_plan_no_cambia_la_correspondencia_adoptada(self):
        from motor.encargos.politica_despliegue import adoptar, resolver
        self.plan(PLAN_DESCRIPTIVO)
        propuesta = [{"id": ident, "resultado": ident} for ident in IDS_DESCRIPTIVOS]
        enc = adoptar(self.reg, self.organizar(propuesta), self.espacio, "h1")
        bloques = PLAN_DESCRIPTIVO.split("## Bloque ")[1:]
        bloques[1], bloques[3] = bloques[3], bloques[1]
        self.plan("".join("## Bloque " + b for b in bloques))
        self.assertIs(resolver(self.espacio, replace(enc, fase_activa="motor-despliegue")).requiere_despliegue, False)
        self.assertIs(resolver(self.espacio, replace(enc, fase_activa="meta-sombra")).requiere_despliegue, True)

    def test_correspondencia_adoptada_con_bloque_ausente_no_se_infiere_de_nuevo(self):
        from motor.encargos.politica_despliegue import adoptar, resolver
        self.plan(PLAN_DESCRIPTIVO)
        propuesta = [{"id": ident, "resultado": ident} for ident in IDS_DESCRIPTIVOS]
        enc = adoptar(self.reg, self.organizar(propuesta), self.espacio, "h1")
        self.plan(PLAN_DESCRIPTIVO.replace("## Bloque D", "## Bloque nuevo"))
        self.assertIsNone(resolver(self.espacio, replace(enc, fase_activa="meta-sombra")).requiere_despliegue)

    def test_posicion_ambigua_conserva_desconocido(self):
        from motor.encargos.politica_despliegue import resolver
        for ids, plan in (
            (IDS_DESCRIPTIVOS, PLAN_DESCRIPTIVO.rsplit("## Bloque I", 1)[0]),
            (IDS_DESCRIPTIVOS, PLAN_DESCRIPTIVO.replace("## Bloque I", "## Bloque H")),
            (("propuesta", "A", *IDS_DESCRIPTIVOS[2:]), PLAN_DESCRIPTIVO),
        ):
            with self.subTest(ids=ids, plan=plan[-40:]):
                self.plan(plan)
                # Cada caso usa una propuesta nueva para no adoptar una política.
                enc = reducir(self.reg.fold("e"), {"kind": "fases", "data": {
                    "fases": [{"id": ident, "resultado": ident} for ident in ids],
                }})
                self.assertIsNone(resolver(self.espacio, enc).requiere_despliegue)

    def test_dos_planes_posicionales_no_eligen_uno_aunque_la_politica_coincida(self):
        from motor.encargos.politica_despliegue import resolver
        enc = self.organizar([{"id": ident, "resultado": ident} for ident in IDS_DESCRIPTIVOS])
        self.plan(PLAN_DESCRIPTIVO)
        otro = self.espacio / "plans/otro.md"
        otro.write_text(PLAN_DESCRIPTIVO, encoding="utf-8")
        enc = replace(enc, adjuntos=(str(otro),))
        self.assertIsNone(resolver(self.espacio, enc).requiere_despliegue)

    def test_coincidencia_explicita_prevalece_sobre_otra_fuente_posicional(self):
        from motor.encargos.politica_despliegue import resolver
        enc = self.organizar([{"id": ident, "resultado": ident} for ident in IDS_DESCRIPTIVOS])
        self.plan(PLAN_DESCRIPTIVO)
        otro = self.espacio / "plans/explicito.md"
        otro.write_text(f"## Bloque propuesta\nlane: release\ndespliegue: {RECETA}\n", encoding="utf-8")
        enc = replace(enc, adjuntos=(str(otro),))
        self.assertIs(resolver(self.espacio, enc).requiere_despliegue, True)

    def test_adoptar_otra_fase_no_oculta_la_regla_explicita_de_un_despliegue(self):
        from motor.encargos.politica_despliegue import adoptar, resolver
        enc = self.organizar([{"id": ident, "resultado": ident} for ident in IDS_DESCRIPTIVOS])
        self.plan(PLAN_DESCRIPTIVO)
        otro = self.espacio / "plans/explicito.md"
        otro.write_text(f"## Bloque motor-despliegue\nlane: release\ndespliegue: {RECETA}\n", encoding="utf-8")
        enc = replace(enc, texto=enc.texto + " y plans/explicito.md")
        antes = resolver(self.espacio, replace(enc, fase_activa="motor-despliegue"))
        self.assertIs(antes.requiere_despliegue, True)
        adoptado = adoptar(self.reg, enc, self.espacio, "h1")
        # El fixture conserva las fuentes autorizadas al reconstruir el encargo.
        adoptado = replace(adoptado, texto=enc.texto, fase_activa="motor-despliegue")
        despues = resolver(self.espacio, adoptado)
        self.assertIs(despues.requiere_despliegue, True)
        self.assertEqual(despues.fuente, antes.fuente)
        self.assertEqual(despues.desplegar, antes.desplegar)

    def test_ejemplos_en_codigo_no_alteran_la_correspondencia_posicional(self):
        from motor.encargos.politica_despliegue import resolver
        enc = self.organizar([{"id": ident, "resultado": ident} for ident in IDS_DESCRIPTIVOS])
        self.plan("```markdown\n## Bloque ficticio\nlane: release\n```\n" + PLAN_DESCRIPTIVO)
        self.assertIs(resolver(self.espacio, enc).requiere_despliegue, False)

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
