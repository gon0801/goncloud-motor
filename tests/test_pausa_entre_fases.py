"""Una pausa del operador no agota modelos ni contamina la fase siguiente."""

import json
import contextlib
import io
import tempfile
import unittest
from unittest import mock
from types import SimpleNamespace

from motor.encargos import ciclo, cli
from tests import test_reanudar_fase_integrada as integrado

setUpModule = integrado.setUpModule


class PausaEntreFases(unittest.TestCase):
    legado_integrado = integrado.ReanudarFaseIntegrada.legado_integrado

    def preparar(self, tmp):
        r, gh, integrado = self.legado_integrado(
            tmp, ("propuesta", "motor-despliegue", "meta-sombra"),
        )
        r.reg.anotar(r.enc, {"kind": "ruta_agentes", "data": {
            "agentes": ["opencode", "codex"],
        }}, "rutas-autorizadas")
        r.reg.anotar(r.enc, {"kind": "reserva", "data": {
            "rol": "responsable", "generacion": r.fold().generacion + 1,
        }}, "autor-a-reserva")
        r.reg.anotar(r.enc, {"kind": "intento_abierto", "data": {
            "id": "autor-a", "pid": 99999999, "inicio": "ausente",
            "rol": "responsable", "agente": "opencode", "modelo": "zai-coding-plan/glm-5.3-flash",
        }}, "autor-a")
        r.reg.anotar(r.enc, {"kind": "intento_cerrado", "data": {}}, "autor-a-cerrado")
        r.reg.anotar(r.enc, {"kind": "reserva", "data": {
            "rol": "revisor", "generacion": r.fold().generacion + 1,
        }}, "revision-interrumpida-reserva")
        r.reg.anotar(r.enc, {"kind": "intento_abierto", "data": {
            "id": "revision-interrumpida", "pid": 99999999, "inicio": "ausente",
            "rol": "revisor", "agente": "codex", "modelo": "openai/gpt-6-astra",
        }}, "revision-interrumpida")
        for rol in ("responsable", "revisor"):
            r.reg.anotar(r.enc, {"kind": "sin_progreso", "data": {
                "rol": rol, "huella": "bloqueo-anterior", "escalar": True,
                "diagnostico": "el nivel de entrega sigue bloqueado",
            }}, "relevo-anterior-" + rol)
        return r, gh, integrado

    def parar_continuar(self, r):
        ciclo.ordenar(r.reg, r.enc, "parar", "pausa", r.hora)
        r.reg.anotar(r.enc, {"kind": "intento_cerrado", "data": {}}, "cerrado-por-pausa")
        r.reg.anotar(r.enc, {"kind": "arbol", "data": {
            "observacion": "ausente", "hora": r.hora,
        }}, "arbol-ausente")
        self.assertEqual(r.fold().estado, "detenido")
        ciclo.ordenar(r.reg, r.enc, "continuar", "continuacion", r.hora)

    def test_el_resultado_del_intento_cortado_no_es_falla_del_proveedor(self):
        with tempfile.TemporaryDirectory() as tmp:
            r, gh, integrado = self.preparar(tmp)
            (r.d / "outcome.json").write_text(json.dumps({
                "fallo_runtime": "el CLI termino con codigo 1, sin error estructurado",
                "sesion_runtime": "sesion-del-revisor",
            }), encoding="utf-8")
            (r.d / "arranque.json").write_text(json.dumps({
                "agente": "codex", "modelo": "openai/gpt-6-astra",
            }), encoding="utf-8")
            self.parar_continuar(r)

            ciclo._incorporar(r.reg, r.fold(), r.espacio, r.hora, r.d)

            self.assertEqual(r.eventos("proveedor_fallo"), [])
            self.assertEqual(r.fold().pausas, ())
            self.assertTrue((r.d / "incorporado").is_file())
            final = ciclo._paso_entrega(r.reg, r.fold(), gh, r.hora)
            self.assertEqual(final.fase_activa, "motor-despliegue")
            self.assertEqual(final.estado, "recibido")
            self.assertEqual(final.fases[0].integrado_sha, integrado.merge_commit)
            self.comprobar_modelo_de_b(r)

    def comprobar_modelo_de_b(self, r):
        elegidos = []

        def comprobar(reg, hora, epoch, lanzar, **kwargs):
            argv, meta = lanzar(reg.fold(r.enc), r.d / "prompt", r.espacio, "nueva")
            elegidos.append((reg.fold(r.enc).fase_activa, meta["modelo"]))
            self.assertEqual(argv, ["comando-de-ensayo"])
            return []

        with mock.patch.object(cli, "barrer", side_effect=comprobar), \
                mock.patch.object(cli, "_disponible", return_value=True), \
                mock.patch.object(cli, "_modelos_para", return_value=("zai-coding-plan/glm-5.3-flash",)), \
                mock.patch.object(cli, "_lanzador", return_value=lambda *args: ["comando-de-ensayo"]), \
                contextlib.redirect_stdout(io.StringIO()):
            cli._supervisor(r.reg, SimpleNamespace(
                agente="", respaldo=[], una_vez=True, umbral=30, intervalo=1,
            ))
        self.assertEqual(elegidos, [("motor-despliegue", "zai-coding-plan/glm-5.3-flash")])

    def test_cambiar_de_fase_reinicia_el_relevo_y_los_bloqueos(self):
        with tempfile.TemporaryDirectory() as tmp:
            r, gh, _ = self.preparar(tmp)
            self.parar_continuar(r)

            final = ciclo._paso_entrega(r.reg, r.fold(), gh, r.hora)

            self.assertEqual(final.fase_activa, "motor-despliegue")
            self.assertEqual(final.relevos_por_rol, ())
            self.assertEqual(final.relevo_huella_por_rol, ())
            self.assertEqual(final.relevos, 0)
            self.assertEqual(final.sin_progreso, 0)
            self.assertEqual(final.bloqueo_huella, "")
            self.assertEqual(final.pendientes_entrega, ())

    def test_una_fase_sin_commits_no_publica_pr_ni_cierra_forzada(self):
        with tempfile.TemporaryDirectory() as tmp:
            r, gh, _ = self.legado_integrado(
                tmp, ("propuesta", "motor-despliegue", "meta-sombra"),
            )
            r.reg.anotar(r.enc, {"kind": "fases", "data": {"fases": [
                {"id": f.id, "resultado": f.resultado} for f in r.fold().fases
            ]}}, "fases-sin-dependencias-como-el-encargo-real")
            final = ciclo._paso_entrega(r.reg, r.fold(), gh, r.hora)
            self.assertEqual(final.fase_activa, "motor-despliegue")
            r.reg.anotar(r.enc, {"kind": "pendiente_entrega", "data": {
                "texto": "no hay otro modelo autorizado",
            }}, "pendiente-sin-trabajo")
            with mock.patch.object(gh, "abrir_pr", wraps=gh.abrir_pr) as abrir:
                final = ciclo._entregar_con_pendientes(r.reg, r.fold(), gh, r.hora)
            self.assertEqual(final.estado, "necesita_usuario")
            self.assertEqual(abrir.call_count, 0)
            self.assertEqual(r.eventos("cierre_forzado"), [])


if __name__ == "__main__":
    unittest.main()
