"""El freno de vueltas sin avance no para encargos por error.

Cinco casos del 1-nov (docs/flujo-revisiones.md, «El freno»). Los casos 3 y 4, la
lectura de DeepSeek, están en tests/test_lectura_deepseek.py. Aquí:

1. Doble conteo en un barrido (enc_1b8916e8a0bac627, 07:46Z): una vuelta contó dos
   veces, por la observación del supervisor y por la lectura de DeepSeek, y escaló
   sin dar el turno de diagnóstico.
2. Cuenta con una revisión externa pendiente (mismo encargo, 09:03Z): escaló con
   sin_progreso 4 mientras esperaba a DeepSeek sobre el commit nuevo, contando un F1
   viejo que DeepSeek ya había descartado.
5. Escala como «el mismo bloqueo» con hallazgos nuevos (enc_3f7de8a83bd2aae6, #152):
   tras la vuelta de diagnóstico de F4, la lectura trajo F5 y F6 nuevos y escaló."""

from __future__ import annotations

import tempfile
import unittest
from unittest import mock

from motor.encargos import ciclo
from motor.encargos.aislamiento import SondaEntorno
from motor.encargos.ciclo import ahora_iso
from motor.encargos.dominio import relevos_de, revision_externa_pendiente
from motor.github import veredicto
from tests.test_flujo_revisiones import Recorrido, _entrega_pr, _lanzaria
from tests.test_lectura_deepseek import _comentario, _hallazgo, _ids
from tests.dobles import github_por_commit


def setUpModule():
    parche = mock.patch(
        "motor.encargos.aislamiento.sondear", return_value=SondaEntorno(True)
    )
    parche.start()
    globals()["_PARCHE_SONDA"] = parche


def tearDownModule():
    parche = globals().pop("_PARCHE_SONDA", None)
    if parche is not None:
        parche.stop()


def _devolver_el_trabajo(r, marca: str) -> None:
    """El propietario corrige, o el supervisor vuelve a dar el turno: el trabajo
    vuelve al responsable con lo que hay abierto."""
    e = r.fold()
    r.reg.anotar(
        r.enc,
        {
            "kind": "estrategia",
            "data": {
                "estrategia": "investigar",
                "rol": "responsable",
                "motivo": "volver a intentarlo",
                "siguiente": "corregir lo abierto",
                "hora": ahora_iso(),
            },
        },
        f"t-devolver:{marca}:{e.generacion}",
    )


class UnaVueltaCuentaUnaVez(unittest.TestCase):
    def _en_espera_del_commit_nuevo(self, tmp, clave):
        """F1 abierto del commit viejo; el responsable entregó otro commit y DeepSeek
        aún no lo revisó (es el estado de enc_1b8916e8a0bac627 a las 07:46Z)."""
        vuelta, r = _entrega_pr(self, tmp, clave, github_por_commit)
        gh = vuelta.gh
        gh.textos = [_comentario(_hallazgo("F1"))]
        e = vuelta("uno")
        self.assertEqual(_ids(e), ["F1"])
        viejo = e.producto.sha_git
        e = vuelta("dos")
        nuevo = e.producto.sha_git
        self.assertNotEqual(viejo, nuevo)
        self.assertEqual(e.deepseek_sha, viejo)
        self.assertEqual(e.estado, "esperando_reintento")
        return vuelta, r, gh

    def test_observacion_y_lectura_de_deepseek_en_un_barrido_cuentan_una_vez(self):  # no-roja-en-base: la base ya muestra esta escalera; la prueba afirma estrategia y relevos por rol en vez del contador
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            vuelta, r, gh = self._en_espera_del_commit_nuevo(tmp, "fr-1")
            gh.textos = [_comentario(_hallazgo("F1")), _comentario(_hallazgo("F1"))]
            _devolver_el_trabajo(r, "uno")

            # Un barrido: el supervisor incorpora la vuelta del responsable (misma
            # versión) y, ya con la revisión de DeepSeek del commit nuevo, la lee.
            r.incorporar()
            e = r.fold()
            if ciclo._aplica_entrega(e):
                e = ciclo._paso_entrega(r.reg, e, gh, ahora_iso())

            self.assertEqual(e.estrategia, "diagnosticar_bloqueo")
            self.assertNotEqual(e.estado, "necesita_usuario")

    def test_dos_fuentes_de_la_misma_vuelta_no_suman(self):  # no-roja-en-base: solo cambió el doble de GitHub; el comportamiento probado ya estaba en la base
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            vuelta, r = _entrega_pr(self, tmp, "fr-1b", github_por_commit)
            vuelta.gh.textos = [_comentario(_hallazgo("F1"))]
            e = vuelta("uno")
            hora = ahora_iso()
            ciclo._sin_avance(r.reg, e, "F1", "fuente uno", hora, "uno")
            ciclo._sin_avance(r.reg, r.fold(), "F1", "fuente dos", hora, "dos")
            e = r.fold()
            self.assertEqual(e.estrategia, "diagnosticar_bloqueo")
            self.assertNotEqual(e.estado, "necesita_usuario")

    def test_la_vuelta_siguiente_si_cuenta_y_releva(self):  # no-roja-en-base: la base ya muestra esta escalera; la prueba afirma estrategia y relevos por rol en vez del contador
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            vuelta, r = _entrega_pr(self, tmp, "fr-1c", github_por_commit)
            vuelta.gh.textos = [_comentario(_hallazgo("F1"))]
            vuelta("uno")
            r.incorporar()
            self.assertEqual(r.fold().estrategia, "diagnosticar_bloqueo")
            r.incorporar()
            e = r.fold()
            self.assertNotEqual(e.estado, "necesita_usuario")
            self.assertEqual(relevos_de(e, "responsable"), 1)
            self.assertTrue(_lanzaria(e))


class SinContarConUnaRevisionExternaPendiente(unittest.TestCase):
    def test_con_deepseek_pendiente_del_commit_nuevo_el_freno_no_escala(self):  # no-roja-en-base: la base ya muestra esta escalera; la prueba afirma estrategia y relevos por rol en vez del contador
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            vuelta, r = _entrega_pr(self, tmp, "fr-2", github_por_commit)
            gh = vuelta.gh
            gh.textos = [_comentario(_hallazgo("F1"))]
            vuelta("uno")
            e = vuelta("dos")
            self.assertEqual(e.estado, "esperando_reintento")
            # F1 sigue abierto en el estado aunque DeepSeek ya lo descartó: el motor
            # no ha leído el commit nuevo, que es donde está el descarte.
            self.assertEqual(_ids(e), ["F1"])

            for i in range(4):
                _devolver_el_trabajo(r, f"p{i}")
                r.incorporar()
                e = r.fold()
                self.assertNotEqual(e.estado, "necesita_usuario", i)
                self.assertNotEqual(e.estrategia, "diagnosticar_bloqueo", i)
            self.assertEqual(
                [ev for ev in r.reg.eventos(r.enc) if ev["kind"] == "sin_progreso"], []
            )
            self.assertTrue(revision_externa_pendiente(e))

            # DeepSeek descarta F1 sobre el commit nuevo: la lectura lo cierra y la
            # fase sigue sin pasar por el freno.
            gh.textos = [_comentario(_hallazgo("F1")), _comentario()]
            e = ciclo._paso_entrega(r.reg, r.fold(), gh, ahora_iso())
            self.assertEqual(_ids(e), [])
            self.assertEqual(e.nivel_alcanzado, "pr")

class CommitNuevoConElNivelPrYaAlcanzado(unittest.TestCase):
    """`_sha_final_sin_leer`: con el nivel pr ya alcanzado, un commit que DeepSeek aún no
    leyó no cierra por el atajo de `_paso_entrega`: se lee primero."""

    def test_el_commit_nuevo_se_lee_antes_de_cerrar(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            vuelta, r = _entrega_pr(self, tmp, "fr-sha", github_por_commit)
            gh = vuelta.gh
            gh.textos = [_comentario(), _comentario()]
            real = gh.comentarios_deepseek
            llamadas = []

            def falla_la_segunda(repo, numero, sha=""):
                llamadas.append(sha)
                if len(llamadas) == 2:
                    raise RuntimeError("gh api: 502")
                return real(repo, numero, sha)

            gh.comentarios_deepseek = falla_la_segunda
            e = vuelta("uno")
            viejo = e.producto.sha_git
            # La comprobación previa falló: el nivel pr está alcanzado y la fase sigue abierta.
            self.assertEqual(e.nivel_alcanzado, "pr")
            self.assertNotEqual(e.estado, "terminado")

            gh.comentarios_deepseek = real
            e = vuelta("dos")
            nuevo = e.producto.sha_git
            self.assertNotEqual(viejo, nuevo)
            self.assertEqual(e.deepseek_sha, nuevo)
            self.assertEqual(e.estado, "terminado")
            self.assertEqual(gh.vistos, [viejo, nuevo])


class ChecksEnMarcha(unittest.TestCase):
    """`veredicto.en_marcha`: los requeridos que faltan y están corriendo o en cola."""

    def test_una_corrida_sin_completar_esta_en_marcha(self):
        v = veredicto(
            "s", ("ci",), [{"name": "ci", "status": "in_progress", "conclusion": None}], "",
        )
        self.assertEqual((v.faltan, v.en_marcha), (("ci",), ("ci",)))

    def test_un_estado_legacy_pendiente_esta_en_marcha(self):
        v = veredicto("s", ("ci",), [], "", [{"context": "ci", "state": "pending"}])
        self.assertEqual((v.faltan, v.en_marcha), (("ci",), ("ci",)))

    def test_un_requerido_que_nadie_corre_no_esta_en_marcha(self):
        v = veredicto("s", ("ci",), [], "", [])
        self.assertEqual((v.faltan, v.en_marcha), (("ci",), ()))

    def test_un_requerido_en_rojo_o_terminado_no_esta_en_marcha(self):
        v = veredicto(
            "s", ("ci", "lint"),
            [
                {"name": "ci", "status": "completed", "conclusion": "failure"},
                {"name": "lint", "status": "completed", "conclusion": "success"},
            ],
            "",
        )
        self.assertEqual((v.fallas, v.faltan, v.en_marcha), (("ci",), (), ()))


class UnBloqueanteNuevoEsInformacionNueva(unittest.TestCase):
    def _tras_el_diagnostico_de_f4(self, tmp, clave):
        vuelta, r = _entrega_pr(self, tmp, clave, github_por_commit)
        gh = vuelta.gh
        gh.textos = [_comentario(_hallazgo("F4"))]
        e = vuelta("uno")
        self.assertEqual(_ids(e), ["F4"])
        r.incorporar()
        e = r.fold()
        self.assertEqual(e.estrategia, "diagnosticar_bloqueo")
        return vuelta, r, gh

    def test_f5_y_f6_nuevos_tras_el_diagnostico_vuelven_al_responsable(self):  # no-roja-en-base: la base ya muestra esta escalera; la prueba afirma estrategia y relevos por rol en vez del contador
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            vuelta, r, gh = self._tras_el_diagnostico_de_f4(tmp, "fr-5")
            gh.textos = [
                _comentario(_hallazgo("F4")),
                _comentario(_hallazgo("F4"), _hallazgo("F5", "high"), _hallazgo("F6", "medium")),
            ]
            e = vuelta("dos")
            self.assertEqual(_ids(e), ["F4", "F5", "F6"])
            self.assertNotEqual(e.estado, "necesita_usuario")
            self.assertEqual(e.estrategia, "investigar")
            self.assertTrue(_lanzaria(e))

    def test_los_mismos_bloqueantes_tras_el_diagnostico_relevan(self):  # no-roja-en-base: la base ya muestra esta escalera; la prueba afirma estrategia y relevos por rol en vez del contador
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            vuelta, r, gh = self._tras_el_diagnostico_de_f4(tmp, "fr-5b")
            r.incorporar()
            e = r.fold()
            self.assertNotEqual(e.estado, "necesita_usuario")
            self.assertEqual(relevos_de(e, "responsable"), 1)
            self.assertTrue(_lanzaria(e))
            self.assertEqual(e.pendientes_entrega, ())

    def test_lo_nuevo_y_luego_lo_mismo_recorre_la_escalera_otra_vez(self):  # no-roja-en-base: la base ya muestra esta escalera; la prueba afirma estrategia y relevos por rol en vez del contador
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            vuelta, r, gh = self._tras_el_diagnostico_de_f4(tmp, "fr-5c")
            gh.textos = [
                _comentario(_hallazgo("F4")),
                _comentario(_hallazgo("F4"), _hallazgo("F5", "high")),
            ]
            e = vuelta("dos")
            self.assertEqual(e.estrategia, "investigar")
            r.incorporar()
            e = r.fold()
            self.assertEqual(e.estrategia, "diagnosticar_bloqueo")
            r.incorporar()
            e = r.fold()
            self.assertNotEqual(e.estado, "necesita_usuario")
            self.assertEqual(relevos_de(e, "responsable"), 1)

    def test_una_serie_que_siempre_trae_hallazgos_nuevos_no_escala_por_si_sola(self):  # no-roja-en-base: la base ya muestra esta escalera; la prueba afirma estrategia y relevos por rol en vez del contador
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            vuelta, r = _entrega_pr(self, tmp, "fr-5d", github_por_commit)
            gh = vuelta.gh
            acumulados = [_hallazgo("F0")]
            gh.textos = [_comentario(*acumulados)]
            e = vuelta("v0")
            self.assertEqual(e.estrategia, "investigar")
            # Seis vueltas seguidas, cada una con un hallazgo nuevo y sin cerrar
            # ninguno: informacion nueva, no «el mismo bloqueo». No hay tope.
            for i in range(1, 7):
                acumulados = acumulados + [_hallazgo(f"F{i}")]
                gh.textos.append(_comentario(*acumulados))
                e = vuelta(f"v{i}")
                self.assertEqual(e.estrategia, "investigar", i)
                self.assertNotEqual(e.estado, "necesita_usuario", i)
                self.assertTrue(_lanzaria(e), i)

    def test_cerrar_un_bloqueante_reinicia_la_serie(self):  # no-roja-en-base: la base ya muestra esta escalera; la prueba afirma estrategia y relevos por rol en vez del contador
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            vuelta, r = _entrega_pr(self, tmp, "fr-5e", github_por_commit)
            gh = vuelta.gh
            gh.textos = [
                _comentario(_hallazgo("F0")),
                _comentario(_hallazgo("F0"), _hallazgo("F1")),
                _comentario(_hallazgo("F1")),
            ]
            vuelta("v0")
            e = vuelta("v1")
            self.assertEqual(e.estrategia, "investigar")
            e = vuelta("v2")
            self.assertEqual(_ids(e), ["F1"])
            self.assertEqual(e.estrategia, "investigar")


class SinTopeDeVueltasNuevas(unittest.TestCase):
    def test_seis_vueltas_con_hallazgos_nuevos_no_escalan(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            r = Recorrido(tmp, "sin-tope-nuevos", "malo.txt con B")
            self.addCleanup(r.reg.close)
            (r.espacio / "malo.txt").write_text("X")
            r.incorporar()
            for i in range(1, 7):
                e = r.fold()
                r.reg.anotar(
                    r.enc,
                    {
                        "kind": "revision",
                        "data": {
                            "version": e.producto.firma,
                            "alcance": "completa",
                            "veredicto": "cambios",
                            "origen": "independiente",
                            "revisor": "opus",
                            "hallazgos": [
                                {
                                    "id": f"F{i}",
                                    "gravedad": "bloqueante",
                                    "criterio": "comportamiento",
                                    "titulo": f"falla nueva {i}",
                                    "reproduccion": "cat malo.txt",
                                    "ancla": "malo.txt:1",
                                    "origen": "independiente",
                                    "estado": "abierto",
                                }
                            ],
                        },
                    },
                    f"rev-sin-tope-{i}",
                )
                e = r.fold()
                ciclo._sin_avance(
                    r.reg, e, f"falla nueva {i}", "intento", ahora_iso(), f"r{i}"
                )
                e = r.fold()
                self.assertNotEqual(e.estado, "necesita_usuario", i)
                self.assertEqual(e.estrategia, "investigar", i)


    def test_dos_fuentes_con_hallazgos_nuevos_en_un_barrido_no_escalan(self):  # no-roja-en-base: la base ya muestra esta escalera; la prueba afirma estrategia y relevos por rol en vez del contador
        # F5 de la revision de DeepSeek sobre #254: con el contador de vueltas nuevas
        # y su tope, dos fuentes del mismo barrido que traian un bloqueante nuevo cada
        # una contaban la vuelta dos veces y el encargo escalaba a la tercera. Sin
        # tope, ninguna vuelta asi escala y el responsable recibe lo de las dos.
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            r = Recorrido(tmp, "dos-fuentes-nuevos", "malo.txt con B")
            self.addCleanup(r.reg.close)
            (r.espacio / "malo.txt").write_text("X")
            r.incorporar()

            def fuente(hallazgo: str, serie: str) -> None:
                e = r.fold()
                r.reg.anotar(
                    r.enc,
                    {
                        "kind": "revision",
                        "data": {
                            "version": e.producto.firma,
                            "alcance": "completa",
                            "veredicto": "cambios",
                            "origen": "independiente",
                            "revisor": "opus",
                            "hallazgos": [
                                {
                                    "id": hallazgo,
                                    "gravedad": "bloqueante",
                                    "criterio": "comportamiento",
                                    "titulo": f"falla nueva {hallazgo}",
                                    "reproduccion": "cat malo.txt",
                                    "ancla": "malo.txt:1",
                                    "origen": "independiente",
                                    "estado": "abierto",
                                }
                            ],
                        },
                    },
                    f"rev-dos-fuentes-{hallazgo}",
                )
                ciclo._sin_avance(
                    r.reg, r.fold(), f"falla nueva {hallazgo}", "intento",
                    ahora_iso(), serie,
                )

            for i in range(1, 7):
                fuente(f"A{i}", f"supervisor-{i}")
                fuente(f"B{i}", f"deepseek-{i}")
                e = r.fold()
                self.assertNotEqual(e.estado, "necesita_usuario", i)
                self.assertEqual(e.estrategia, "investigar", i)
                self.assertLessEqual({f"A{i}", f"B{i}"}, set(e.devuelto_con), i)
                self.assertTrue(_lanzaria(e), i)

if __name__ == "__main__":
    unittest.main()
