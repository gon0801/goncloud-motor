"""5.3: volver a mirar una espera por hecho no escribe.

Con el interruptor encendido, cada clase por hecho tiene una lectura de cierre
compartida por el paso y el barrido. Mientras la lectura diga «todavía no» (o no
se pueda leer) no se escribe nada; cuando cierra, se escribe el evento de cierre
según cómo se abrió la espera. La cadencia vive en memoria del proceso.
"""

from __future__ import annotations

import tempfile
import time
import unittest
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from motor.encargos import ciclo, cierres, despliegue, politica_despliegue
from motor.encargos.dominio import Fase
from motor.encargos.frontera import DestinoEnsayo
from motor.encargos.registro import Registro

H0 = "2026-11-18T10:00:00Z"
PASO = 400  # más que el intervalo mayor de la tabla


def _recibir(reg: Registro, clave: str = "pedido-1") -> str:
    return ciclo.recibir(reg, "Crea dato.txt con 42", clave, H0)["encargo"]


def _eventos(reg: Registro, enc: str, kind: str) -> list[dict]:
    return [e for e in reg.eventos(enc) if e["kind"] == kind]


class Banco(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.base = Path(self._tmp.name) / "base"
        self.reg = Registro(self.base)
        self.addCleanup(self.reg.close)
        self.enc = _recibir(self.reg)

    def abrir(self, clase: str, motivo: str = "pendiente", hora: str = H0) -> None:
        with mock.patch("motor.encargos.politica.ESPERAS_V5", True):
            ciclo.marcar_espera(self.reg, self.enc, clase, motivo, hora)

    def cerrar(self, epoch: float = 0.0, hora: str = H0, enc=None):
        enc = enc or self.reg.fold(self.enc)
        with mock.patch("motor.encargos.politica.ESPERAS_V5", True):
            return ciclo._cerrar_espera_por_hecho(
                self.reg, enc, None, Path(self._tmp.name) / "espacio",
                Path(self._tmp.name) / "directorio", "ausente", hora, epoch,
            )


class CincuentaLecturas(Banco):
    def test_cada_clase_por_hecho_no_escribe_mientras_no_cierra(self):
        for clase in cierres.clases_por_hecho():
            with self.subTest(clase=clase):
                self.setUp()
                self.abrir(clase)
                antes = _eventos(self.reg, self.enc, "espera")
                with mock.patch.dict(cierres.CIERRES, {clase: lambda ctx: False}):
                    for i in range(50):
                        self.cerrar(epoch=1.0 + PASO * (i + 1))
                self.assertEqual(_eventos(self.reg, self.enc, "espera"), antes)
                self.assertEqual(_eventos(self.reg, self.enc, "plazo"), [])
                self.assertEqual(_eventos(self.reg, self.enc, "tope_espera"), [])

    def test_la_lectura_que_cierra_escribe_plazo(self):
        self.abrir("checks_merge")
        with mock.patch.dict(cierres.CIERRES, {"checks_merge": lambda ctx: True}):
            enc = self.cerrar(epoch=1.0)
        self.assertEqual(enc.estado, "recibido")
        self.assertEqual(len(_eventos(self.reg, self.enc, "plazo")), 1)

    def test_una_lectura_none_no_escribe(self):
        self.abrir("revision_deepseek")
        with mock.patch.dict(cierres.CIERRES, {"revision_deepseek": lambda ctx: None}):
            for i in range(50):
                self.cerrar(epoch=1.0 + PASO * i)
        self.assertEqual(_eventos(self.reg, self.enc, "plazo"), [])
        self.assertEqual(len(_eventos(self.reg, self.enc, "espera")), 1)

    def test_reiniciar_vuelve_a_leer_enseguida(self):
        llamadas: list[float] = []

        def lee(ctx):
            llamadas.append(ctx.epoch)
            return False

        self.abrir("checks_merge")
        with mock.patch.dict(cierres.CIERRES, {"checks_merge": lee}):
            self.cerrar(epoch=1.0)
            self.cerrar(epoch=2.0)  # dentro de la cadencia: no lee
            self.assertEqual(len(llamadas), 1)
            cierres.olvidar(self.reg)
            self.cerrar(epoch=2.0)  # sin memoria: lee enseguida
        self.assertEqual(len(llamadas), 2)

    def test_una_espera_larga_sin_eventos_no_dispara_quietud(self):
        self.abrir("checks_merge")
        with mock.patch(
            "motor.encargos.politica.ESPERAS_V5", True
        ), mock.patch.dict(cierres.CIERRES, {"checks_merge": lambda ctx: False}):
            for i in range(31):
                ciclo.barrer(
                    self.reg, H0, 1.0 + 60 * i, None, gh=None,
                    umbral_s=10**9,
                )
        self.assertEqual(_eventos(self.reg, self.enc, "quietud"), [])


class AutoridadDosAperturas(Banco):
    def test_la_abierta_por_espera_se_cierra_con_plazo(self):
        self.abrir("autoridad")
        # La espera de autoridad se abre cuando la autoridad falta; el cierre
        # solo escribe plazo, sin inventar el evento de autoridad.
        enc = self.cerrar(epoch=1.0)
        self.assertEqual(enc.estado, "recibido")
        self.assertEqual(len(_eventos(self.reg, self.enc, "plazo")), 1)

    def test_la_abierta_por_evento_la_cierra_autoridad(self):
        self.reg.anotar(
            self.enc,
            {"kind": "autoridad", "data": {"alcanzable": False, "hora": H0}},
            f"autoridad:{self.enc}:caida:{H0}",
        )
        enc = self.reg.fold(self.enc)
        self.assertEqual(enc.espera_clase, "autoridad")
        self.assertNotEqual(enc.estado, "esperando_reintento")
        with mock.patch(
            "motor.encargos.transporte.cargar", return_value={}
        ), mock.patch(
            "motor.encargos.transporte.operar",
            return_value={"alcanzada": True, "salida": {"estado": ""}},
        ):
            ciclo._mirar_autoridad(self.reg, enc, H0)
        eventos = _eventos(self.reg, self.enc, "autoridad")
        self.assertTrue(eventos[-1]["data"]["alcanzable"])


class LecturasPorClase(unittest.TestCase):
    def test_checks_en_marcha_no_cierra_y_terminados_si(self):
        ctx = SimpleNamespace(
            gh=SimpleNamespace(checks_requeridos=lambda *a: SimpleNamespace(en_marcha=("ci",))),
            enc=SimpleNamespace(pr_numero=1, pr_sha="a" * 40, entrega=SimpleNamespace(repo="r")),
        )
        self.assertFalse(cierres._checks_merge(ctx))
        ctx.gh = SimpleNamespace(
            checks_requeridos=lambda *a: SimpleNamespace(en_marcha=())
        )
        self.assertTrue(cierres._checks_merge(ctx))

    def test_checks_ilegibles_no_son_verde(self):
        ctx = SimpleNamespace(
            gh=SimpleNamespace(checks_requeridos=mock.Mock(side_effect=RuntimeError("502"))),
            enc=SimpleNamespace(pr_numero=1, pr_sha="a" * 40, entrega=SimpleNamespace(repo="r")),
        )
        self.assertIsNone(cierres._checks_merge(ctx))

    def test_una_politica_que_bloquea_despierta_el_paso(self):
        from motor.github import PoliticaBloquea

        ctx = SimpleNamespace(
            gh=SimpleNamespace(
                checks_requeridos=mock.Mock(
                    side_effect=PoliticaBloquea("la política del PR cambió")
                )
            ),
            enc=SimpleNamespace(
                pr_numero=1, pr_sha="a" * 40, entrega=SimpleNamespace(repo="r")
            ),
        )
        self.assertTrue(cierres._checks_merge(ctx))

    def test_una_punta_movida_despierta_el_paso(self):
        from motor.github import PuntaMovida

        ctx = SimpleNamespace(
            gh=SimpleNamespace(
                checks_requeridos=mock.Mock(side_effect=PuntaMovida("movida"))
            ),
            enc=SimpleNamespace(
                pr_numero=1, pr_sha="a" * 40, entrega=SimpleNamespace(repo="r")
            ),
        )
        self.assertTrue(cierres._checks_merge(ctx))

    def test_sin_puerto_github_cierra_cuando_aparece(self):
        self.assertFalse(cierres._sin_puerto_github(SimpleNamespace(gh=None)))
        self.assertTrue(cierres._sin_puerto_github(SimpleNamespace(gh=object())))

    def test_cabeza_desviada_cierra_cuando_concilia(self):
        enc = SimpleNamespace(
            pr_numero=1, pr_sha="a" * 40, entrega=SimpleNamespace(repo="r")
        )
        igual = SimpleNamespace(gh=SimpleNamespace(ver=lambda *a: SimpleNamespace(head="a" * 40)), enc=enc)
        distinta = SimpleNamespace(gh=SimpleNamespace(ver=lambda *a: SimpleNamespace(head="b" * 40)), enc=enc)
        self.assertTrue(cierres._cabeza_desviada(igual))
        self.assertFalse(cierres._cabeza_desviada(distinta))

    def test_despliegue_en_curso_cierra_cuando_el_trabajador_no_vive(self):
        enc = object()
        ctx = SimpleNamespace(registro=object(), enc=enc)
        with mock.patch.object(despliegue, "observacion", return_value="vivo"):
            self.assertFalse(cierres._despliegue_en_curso(ctx))
        with mock.patch.object(despliegue, "observacion", return_value="ausente"):
            self.assertTrue(cierres._despliegue_en_curso(ctx))

    def test_despliegue_sin_receta_no_cierra_con_una_receta_invalida(self):
        enc = SimpleNamespace(
            fase_actual=SimpleNamespace(integrado_sha="a" * 40),
            fase_activa="uno",
        )
        ctx = SimpleNamespace(
            espacio=Path("."), enc=enc, registro=SimpleNamespace(base=Path(".")),
        )
        invalida = SimpleNamespace(
            requiere_despliegue=True, desplegar="", checklist="c.sh", rollback="r.sh"
        )
        valida = SimpleNamespace(
            requiere_despliegue=True, desplegar="d.sh", checklist="c.sh", rollback="r.sh"
        )
        with mock.patch.object(
            politica_despliegue, "resolver", return_value=SimpleNamespace(
                requiere_despliegue=None
            ),
        ):
            self.assertFalse(cierres._despliegue_sin_receta(ctx))
        sin_despliegue = SimpleNamespace(
            requiere_despliegue=False, desplegar="", checklist="", rollback=""
        )
        with mock.patch.object(
            politica_despliegue, "resolver", return_value=sin_despliegue
        ):
            # La fase ya no necesita desplegar: la espera quedó resuelta aunque
            # no haya receta ni máquina que validar.
            self.assertTrue(cierres._despliegue_sin_receta(ctx))
        with mock.patch.object(
            politica_despliegue, "resolver", return_value=invalida
        ), mock.patch(
            "motor.encargos.despliegue._maquina", return_value=object()
        ):
            self.assertFalse(cierres._despliegue_sin_receta(ctx))
        with mock.patch.object(
            politica_despliegue, "resolver", return_value=valida
        ), mock.patch(
            "motor.encargos.despliegue._maquina", return_value=object()
        ):
            self.assertTrue(cierres._despliegue_sin_receta(ctx))
        sin_sha = SimpleNamespace(fase_actual=SimpleNamespace(integrado_sha=""), fase_activa="uno")
        with mock.patch.object(
            politica_despliegue, "resolver", return_value=valida
        ), mock.patch(
            "motor.encargos.despliegue._maquina", return_value=object()
        ):
            self.assertFalse(
                cierres._despliegue_sin_receta(
                    SimpleNamespace(
                        espacio=Path("."), enc=sin_sha,
                        registro=SimpleNamespace(base=Path(".")),
                    )
                )
            )

    def test_efecto_incierto_consulta_el_destino(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        base = Path(tmp.name)
        enc = SimpleNamespace(
            efectos=(SimpleNamespace(estado="incierto", clave="k"),)
        )
        ctx = SimpleNamespace(registro=SimpleNamespace(base=base), enc=enc)
        self.assertFalse(cierres._efecto_incierto(ctx))
        DestinoEnsayo(base / "destino").aplicar("k", "cuerpo", "publicar")
        self.assertTrue(cierres._efecto_incierto(ctx))

    def test_autoridad_sigue_el_plegado(self):
        self.assertTrue(cierres._autoridad(SimpleNamespace(enc=SimpleNamespace(autoridad=True))))
        self.assertFalse(cierres._autoridad(SimpleNamespace(enc=SimpleNamespace(autoridad=False))))

    def test_observacion_del_comprobador(self):
        ctx = SimpleNamespace(
            registro=object(), enc=SimpleNamespace(fase_activa="uno"),
            espacio=Path("."), directorio=Path("."),
        )
        with mock.patch(
            "motor.encargos.comprobacion.leer_declaradas", return_value=[]
        ), mock.patch(
            "motor.encargos.comprobacion.version_producto",
            return_value=SimpleNamespace(firma="v"),
        ), mock.patch(
            "motor.encargos.comprobacion.huella_procedimiento", return_value="h"
        ), mock.patch(
            "motor.encargos.ciclo._observacion_de_la_version", return_value=None
        ), mock.patch(
            "motor.encargos.comprobacion.iniciar_comprobacion"
        ) as relanzar:
            self.assertFalse(cierres._observacion_comprobador(ctx))
            relanzar.assert_called_once()
        with mock.patch(
            "motor.encargos.comprobacion.leer_declaradas", return_value=[]
        ), mock.patch(
            "motor.encargos.comprobacion.version_producto",
            return_value=SimpleNamespace(firma="v"),
        ), mock.patch(
            "motor.encargos.comprobacion.huella_procedimiento", return_value="h"
        ), mock.patch(
            "motor.encargos.ciclo._observacion_de_la_version", return_value={}
        ), mock.patch(
            "motor.encargos.comprobacion.iniciar_comprobacion"
        ) as relanzar:
            self.assertTrue(cierres._observacion_comprobador(ctx))
            relanzar.assert_not_called()


class DeepSeekNoEscribeSinCambio(Banco):
    def test_un_pr_sin_comentario_dispara_a_los_dos_minutos_una_vez(self):
        from tests.dobles import github_por_numero
        from tests.test_flujo_revisiones import _entrega_pr

        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            vuelta, r = _entrega_pr(self, tmp, "ds-disparo", github_por_numero)
            vuelta.gh.textos[None] = None
            e = vuelta("uno")
            self.assertEqual(e.estado, "esperando_reintento")
            self.assertEqual(e.espera_clase, "revision_deepseek")
            t0 = time.time()
            with mock.patch("motor.encargos.politica.ESPERAS_V5", True):
                ciclo.barrer(r.reg, ciclo.ahora_iso(t0 + 130), t0 + 130, None, gh=vuelta.gh)
                for i in range(1, 51):
                    ciclo.barrer(
                        r.reg, ciclo.ahora_iso(t0 + 130 + 61 * i),
                        t0 + 130 + 61 * i, None, gh=vuelta.gh,
                    )
            disparos = [
                ev for ev in r.reg.eventos(r.enc)
                if ev["kind"] == "entrega_externa"
                and ev["data"].get("paso") == "deepseek_disparado"
            ]
            self.assertEqual(len(disparos), 1)
            esperas = [
                ev for ev in r.reg.eventos(r.enc)
                if ev["kind"] == "espera"
                and ev["data"].get("clase") == "revision_deepseek"
            ]
            self.assertEqual(len(esperas), 1, "volver a mirar no escribe otra espera")

    def test_cincuenta_relecturas_sin_cambio_no_escriben(self):
        from tests.dobles import github_por_numero
        from tests.test_flujo_revisiones import _entrega_pr

        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            vuelta, r = _entrega_pr(self, tmp, "ds-v53", github_por_numero)
            vuelta.gh.textos[None] = (
                'DeepSeek: CHANGES_REQUESTED\nai-review:findings='
                '[{"id": "F1", "severity": "high", "status": "open", '
                '"title": "x", "file": "saludo.txt"}]'
            )
            e = vuelta("uno")
            self.assertEqual(e.deepseek_sha, e.pr_sha)
            directorio = r.espacio
            t0 = time.time()
            with mock.patch("motor.encargos.politica.ESPERAS_V5", True):
                ciclo._releer_deepseek(r.reg, e, vuelta.gh, directorio, e.hora, t0)
                antes = len(_eventos(r.reg, r.enc, "deepseek_lectura"))
                for i in range(1, 51):
                    ciclo._releer_deepseek(
                        r.reg, r.fold(), vuelta.gh, directorio, e.hora, t0 + 61 * i
                    )
                self.assertEqual(
                    len(_eventos(r.reg, r.enc, "deepseek_lectura")), antes
                )
                # Un comentario nuevo sí es un cambio leído.
                vuelta.gh.textos[None] = (
                    'DeepSeek: CHANGES_REQUESTED\nai-review:findings='
                    '[{"id": "F2", "severity": "high", "status": "open", '
                    '"title": "y", "file": "saludo.txt"}]'
                )
                ciclo._releer_deepseek(
                    r.reg, r.fold(), vuelta.gh, directorio, e.hora, t0 + 61 * 51
                )
            self.assertEqual(
                len(_eventos(r.reg, r.enc, "deepseek_lectura")), antes + 1
            )


class TopeDeLasEsperasQueOmiten(Banco):
    def _enc_deepseek(self):
        sha = "a" * 40
        fase = replace(Fase("f", "x"), pr_sha=sha, pr_numero=1)
        return replace(
            self.reg.fold(self.enc),
            fases=(fase,),
            fase_activa="f",
            primer_envio_por_sha=((sha, H0),),
        )

    def test_revision_y_lectura_fallida_vencen_a_los_treinta_minutos(self):
        for clase in ("revision_deepseek", "lectura_deepseek_fallida"):
            with self.subTest(clase=clase):
                self.setUp()
                self.abrir(clase)
                enc = self._enc_deepseek()
                with mock.patch.dict(cierres.CIERRES, {clase: lambda ctx: None}):
                    self.cerrar(
                        enc=enc, epoch=1.0, hora="2026-11-18T10:29:00Z"
                    )
                    self.assertEqual(
                        _eventos(self.reg, self.enc, "plazo"), []
                    )
                    self.cerrar(
                        enc=self.reg.fold(self.enc), epoch=2.0,
                        hora="2026-11-18T10:30:01Z",
                    )
                self.assertEqual(len(_eventos(self.reg, self.enc, "plazo")), 1)


class DisparoBajoParada(unittest.TestCase):
    def _ctx(self, puede: bool):
        sha = "a" * 40
        enc = SimpleNamespace(
            fase_actual=SimpleNamespace(pr_sha=sha, pr_numero=1),
            entrega=SimpleNamespace(repo="r"),
            primer_envio_por_sha=((sha, H0),),
            pr_sha=sha,
        )
        gh = SimpleNamespace(comentarios_deepseek=lambda *a: [])
        return cierres.Contexto(
            registro=object(), enc=enc, gh=gh, espacio=Path("."),
            hora="2026-11-18T10:02:00Z", epoch=122.0, puede_actuar=puede,
        )

    def test_una_parada_o_falta_de_autoridad_no_dispara(self):
        with mock.patch(
            "motor.encargos.ciclo._accion_deepseek_si_toca"
        ) as accion:
            self.assertFalse(cierres._revision_deepseek(self._ctx(False)))
            accion.assert_not_called()
        with mock.patch(
            "motor.encargos.ciclo._accion_deepseek_si_toca"
        ) as accion:
            self.assertFalse(cierres._revision_deepseek(self._ctx(True)))
            accion.assert_called_once()


class MultifaseDeepSeek(unittest.TestCase):
    SHA_A = "a" * 40
    SHA_B = "b" * 40

    def _enc(self, reg, enc_id):
        return SimpleNamespace(
            id=enc_id,
            fase_actual=SimpleNamespace(pr_numero=202, pr_sha=self.SHA_B),
            entrega=SimpleNamespace(repo="r"),
            primer_envio_por_sha=((self.SHA_A, H0), (self.SHA_B, "2026-11-18T10:20:00Z")),
            pr_sha=self.SHA_B,
            pr_numero=202,
        )

    def test_la_espera_por_la_fase_a_no_cierra_con_b(self):
        reg = Registro(Path(tempfile.mkdtemp()) / "base")
        self.addCleanup(reg.close)
        enc_id = _recibir(reg, "multifase")
        ciclo.marcar_espera(
            reg, enc_id, "lectura_deepseek_fallida", "A ilegible", H0,
            desde=H0, numero=101, sha=self.SHA_A, fase="A",
        )
        llamadas: list[tuple[int, str]] = []

        def comentarios(repo, numero, sha):
            llamadas.append((numero, sha))
            if numero == 101:
                raise RuntimeError("gh api: 502")
            return [{"texto": "DeepSeek: APPROVED", "sha": sha}]

        ctx = cierres.Contexto(
            registro=reg, enc=self._enc(reg, enc_id),
            gh=SimpleNamespace(comentarios_deepseek=comentarios),
            hora=H0, epoch=1.0,
        )
        self.assertIsNone(cierres._lectura_deepseek_fallida(ctx))
        self.assertEqual(llamadas, [(101, self.SHA_A)])

        def comentarios_ok(repo, numero, sha):
            llamadas.append((numero, sha))
            return [{"texto": "DeepSeek: APPROVED", "sha": sha}]

        ctx = replace(
            ctx, gh=SimpleNamespace(comentarios_deepseek=comentarios_ok)
        )
        self.assertTrue(cierres._lectura_deepseek_fallida(ctx))
        self.assertEqual(llamadas[-1], (101, self.SHA_A))

    def _cierre_multifase(self, reg, enc_id, huella_a="", huella_b=""):
        a = Fase(
            "A", "x", estado="preparada", evidencia="ok", pr_numero=101,
            pr_sha=self.SHA_A, deepseek_sha=self.SHA_A,
            deepseek_huella=huella_a, ultimo_paso_entrega="deepseek_leido",
        )
        b = Fase(
            "B", "x", pr_numero=202, pr_sha=self.SHA_B,
            deepseek_sha=self.SHA_B, deepseek_huella=huella_b,
            ultimo_paso_entrega="deepseek_leido",
        )

        def enc_cerrando():
            base = reg.fold(enc_id)
            return replace(
                base, fase_activa="B", fases=(a, b),
                entrega=replace(base.entrega, nivel="integrado"),
                primer_envio_por_sha=(
                    (self.SHA_A, H0),
                    (self.SHA_B, "2026-11-18T10:20:00Z"),
                ),
            )

        def comentarios(repo, numero, sha):
            if numero == 101:
                raise RuntimeError("gh api: 502")
            return []

        return enc_cerrando, SimpleNamespace(comentarios_deepseek=comentarios)

    def test_al_vencer_a_se_omite_y_el_cierre_sigue(self):
        reg = Registro(Path(tempfile.mkdtemp()) / "base")
        self.addCleanup(reg.close)
        enc_id = _recibir(reg, "cierre-multifase")
        enc_cerrando, gh = self._cierre_multifase(reg, enc_id)
        with mock.patch("motor.encargos.politica.ESPERAS_V5", True):
            antes = ciclo._veredicto_antes_de_cerrar(
                reg, enc_cerrando(), gh, "2026-11-18T10:29:00Z"
            )
            self.assertIsNotNone(antes)
            self.assertEqual(antes.estado, "esperando_reintento")
            esperas = [e for e in reg.eventos(enc_id) if e["kind"] == "espera"]
            self.assertEqual(esperas[-1]["data"]["numero"], 101)

            reg.anotar(
                enc_id, {"kind": "plazo", "data": {"hora": "2026-11-18T10:30:00Z"}},
                f"plazo:{enc_id}:A",
            )
            self.assertIsNone(
                ciclo._veredicto_antes_de_cerrar(
                    reg, enc_cerrando(), gh, "2026-11-18T10:30:00Z"
                )
            )
        notas = [e for e in reg.eventos(enc_id) if e["kind"] == "nota_pr"]
        self.assertTrue(
            any(
                "sin relectura de DeepSeek del PR #101" in e["data"]["texto"]
                for e in notas
            ),
            notas,
        )
        self.assertEqual(
            len([e for e in reg.eventos(enc_id) if e["kind"] == "espera"]), 1
        )

    def test_con_el_interruptor_apagado_la_relectura_vencida_sigue_esperando(self):
        reg = Registro(Path(tempfile.mkdtemp()) / "base")
        self.addCleanup(reg.close)
        enc_id = _recibir(reg, "cierre-sin-interruptor")
        enc_cerrando, gh = self._cierre_multifase(reg, enc_id)
        with mock.patch("motor.encargos.politica.ESPERAS_V5", False):
            resultado = ciclo._veredicto_antes_de_cerrar(
                reg, enc_cerrando(), gh, "2026-11-18T10:30:00Z"
            )
        self.assertIsNotNone(resultado)
        self.assertEqual(resultado.estado, "esperando_reintento")
        self.assertFalse(
            [e for e in reg.eventos(enc_id) if e["kind"] == "nota_pr"]
        )
        self.assertEqual(
            len([e for e in reg.eventos(enc_id) if e["kind"] == "espera"]), 1
        )

    def test_apagado_la_decision_es_la_de_la_fase_activa(self):
        sin = ciclo.HUELLA_DEEPSEEK_SIN_REVISION
        reg = Registro(Path(tempfile.mkdtemp()) / "base")
        self.addCleanup(reg.close)
        enc_id = _recibir(reg, "base-b-normal")
        enc_cerrando, gh = self._cierre_multifase(
            reg, enc_id, huella_a=sin, huella_b=""
        )
        with mock.patch("motor.encargos.politica.ESPERAS_V5", False):
            resultado = ciclo._veredicto_antes_de_cerrar(
                reg, enc_cerrando(), gh, "2026-11-18T10:30:00Z"
            )
        self.assertIsNotNone(resultado)
        self.assertEqual(resultado.estado, "esperando_reintento")
        esperas = [e for e in reg.eventos(enc_id) if e["kind"] == "espera"]
        self.assertEqual(esperas[-1]["data"]["numero"], 101)

        reg2 = Registro(Path(tempfile.mkdtemp()) / "base")
        self.addCleanup(reg2.close)
        enc_id2 = _recibir(reg2, "base-b-omitida")
        enc_cerrando2, gh2 = self._cierre_multifase(
            reg2, enc_id2, huella_a="", huella_b=sin
        )
        with mock.patch("motor.encargos.politica.ESPERAS_V5", False):
            resultado2 = ciclo._veredicto_antes_de_cerrar(
                reg2, enc_cerrando2(), gh2, "2026-11-18T10:30:00Z"
            )
        self.assertIsNone(resultado2)
        self.assertEqual(
            [e for e in reg2.eventos(enc_id2) if e["kind"] == "espera"], []
        )

    def test_encendido_el_criterio_es_por_fase(self):
        sin = ciclo.HUELLA_DEEPSEEK_SIN_REVISION
        reg = Registro(Path(tempfile.mkdtemp()) / "base")
        self.addCleanup(reg.close)
        enc_id = _recibir(reg, "v5-b-normal")
        enc_cerrando, gh = self._cierre_multifase(
            reg, enc_id, huella_a=sin, huella_b=""
        )
        with mock.patch("motor.encargos.politica.ESPERAS_V5", True):
            resultado = ciclo._veredicto_antes_de_cerrar(
                reg, enc_cerrando(), gh, "2026-11-18T10:29:00Z"
            )
        self.assertIsNone(resultado)
        self.assertEqual(
            [e for e in reg.eventos(enc_id) if e["kind"] == "espera"], []
        )

        reg2 = Registro(Path(tempfile.mkdtemp()) / "base")
        self.addCleanup(reg2.close)
        enc_id2 = _recibir(reg2, "v5-b-omitida")
        enc_cerrando2, gh2 = self._cierre_multifase(
            reg2, enc_id2, huella_a="", huella_b=sin
        )
        with mock.patch("motor.encargos.politica.ESPERAS_V5", True):
            resultado2 = ciclo._veredicto_antes_de_cerrar(
                reg2, enc_cerrando2(), gh2, "2026-11-18T10:29:00Z"
            )
        self.assertIsNotNone(resultado2)
        self.assertEqual(resultado2.estado, "esperando_reintento")
        esperas = [e for e in reg2.eventos(enc_id2) if e["kind"] == "espera"]
        self.assertEqual(esperas[-1]["data"]["numero"], 101)

    def test_el_tope_sin_primer_envio_usa_el_registrado(self):
        reg = Registro(Path(tempfile.mkdtemp()) / "base")
        self.addCleanup(reg.close)
        enc_id = _recibir(reg, "tope-sin-envio")
        ciclo.marcar_espera(
            reg, enc_id, "lectura_deepseek_fallida", "no se pudo leer", H0,
            desde=H0, numero=101, sha=self.SHA_A, fase="A",
        )
        enc = replace(reg.fold(enc_id), primer_envio_por_sha=())
        self.assertFalse(ciclo._paso_el_tope_de_lectura(
            reg, enc, 101, self.SHA_A, "2026-11-18T10:29:00Z"
        ))
        self.assertTrue(ciclo._paso_el_tope_de_lectura(
            reg, enc, 101, self.SHA_A, "2026-11-18T10:30:00Z"
        ))
        self.assertFalse(ciclo._paso_el_tope_de_lectura(
            reg, enc, 999, "c" * 40, "2026-11-18T10:30:00Z"
        ))

    def test_una_espera_legada_sin_identidad_conserva_su_clave(self):
        reg = Registro(Path(tempfile.mkdtemp()) / "base")
        self.addCleanup(reg.close)
        enc_id = _recibir(reg, "legado-identidad")
        reg.anotar(
            enc_id,
            {"kind": "espera", "data": {
                "motivo": "no se pudo leer",
                "reanuda_en": "2026-11-18T10:01:00Z",
                "hora": H0,
                "clase": "lectura_deepseek_fallida",
                "abierta": H0,
                "tope": "2026-11-18T10:30:00Z",
            }},
            f"espera:{enc_id}:2026-11-18T10:01:00Z:no se pudo leer",
        )
        reg.anotar(
            enc_id,
            {"kind": "plazo", "data": {"hora": "2026-11-18T10:01:00Z"}},
            f"plazo:{enc_id}:legado",
        )
        antes = len([e for e in reg.eventos(enc_id) if e["kind"] == "espera"])
        ciclo.marcar_espera(
            reg, enc_id, "lectura_deepseek_fallida", "no se pudo leer", H0,
            reanuda_en="2026-11-18T10:01:00Z",
            numero=101, sha=self.SHA_A, fase="A",
        )
        self.assertEqual(
            len([e for e in reg.eventos(enc_id) if e["kind"] == "espera"]), antes
        )
        self.assertEqual(reg.fold(enc_id).estado, "recibido")

    def test_un_tope_legado_sin_identidad_no_se_hereda(self):
        reg = Registro(Path(tempfile.mkdtemp()) / "base")
        self.addCleanup(reg.close)
        enc_id = _recibir(reg, "tope-legado")
        reg.anotar(
            enc_id,
            {"kind": "espera", "data": {
                "motivo": "no se pudo leer",
                "reanuda_en": "2026-11-18T10:01:00Z",
                "hora": H0,
                "clase": "lectura_deepseek_fallida",
                "abierta": H0,
                "tope": "2026-11-18T10:30:00Z",
            }},
            f"espera:{enc_id}:2026-11-18T10:01:00Z:no se pudo leer",
        )
        reg.anotar(
            enc_id,
            {"kind": "plazo", "data": {"hora": "2026-11-18T10:01:00Z"}},
            f"plazo:{enc_id}:legado",
        )
        enc = replace(reg.fold(enc_id), primer_envio_por_sha=())
        self.assertFalse(ciclo._paso_el_tope_de_lectura(
            reg, enc, 202, self.SHA_B, "2026-11-18T11:00:00Z"
        ))
        ciclo.marcar_espera(
            reg, enc_id, "lectura_deepseek_fallida", "no se pudo leer",
            "2026-11-18T11:00:00Z", desde="", numero=202, sha=self.SHA_B,
            fase="B",
        )
        esperas = [e for e in reg.eventos(enc_id) if e["kind"] == "espera"]
        self.assertEqual(esperas[-1]["data"]["numero"], 202)
        self.assertEqual(esperas[-1]["data"]["abierta"], "2026-11-18T11:00:00Z")
        self.assertFalse(ciclo._paso_el_tope_de_lectura(
            reg, enc, 202, self.SHA_B, "2026-11-18T11:29:00Z"
        ))
        self.assertTrue(ciclo._paso_el_tope_de_lectura(
            reg, enc, 202, self.SHA_B, "2026-11-18T11:30:00Z"
        ))

    def test_otra_fase_con_el_mismo_motivo_estrena_su_identidad(self):
        reg = Registro(Path(tempfile.mkdtemp()) / "base")
        self.addCleanup(reg.close)
        enc_id = _recibir(reg, "identidad-multifase")
        ciclo.marcar_espera(
            reg, enc_id, "lectura_deepseek_fallida", "no se pudo leer", H0,
            desde=H0, numero=202, sha=self.SHA_B, fase="B",
        )
        ciclo.marcar_espera(
            reg, enc_id, "lectura_deepseek_fallida", "no se pudo leer", H0,
            desde=H0, numero=101, sha=self.SHA_A, fase="A",
        )
        esperas = [e for e in reg.eventos(enc_id) if e["kind"] == "espera"]
        self.assertEqual(len(esperas), 2)
        self.assertEqual(
            (esperas[-1]["data"]["numero"], esperas[-1]["data"]["sha"]),
            (101, self.SHA_A),
        )

    def test_el_tope_registrado_de_a_manda_sobre_el_primer_envio_de_b(self):
        reg = Registro(Path(tempfile.mkdtemp()) / "base")
        self.addCleanup(reg.close)
        enc_id = _recibir(reg, "tope-multifase")
        fase_b = replace(Fase("B", "x"), pr_sha=self.SHA_B, pr_numero=202)
        enc = replace(
            reg.fold(enc_id),
            fase_activa="B",
            fases=(fase_b,),
            espera_clase="lectura_deepseek_fallida",
            espera_abierta=H0,
            espera_tope="2026-11-18T10:30:00Z",
            primer_envio_por_sha=(
                (self.SHA_A, H0), (self.SHA_B, "2026-11-18T10:20:00Z"),
            ),
        )
        self.assertFalse(ciclo._tope_de_la_espera_omitir(enc, "2026-11-18T10:29:00Z"))
        self.assertTrue(ciclo._tope_de_la_espera_omitir(enc, "2026-11-18T10:30:00Z"))
        # Un evento antiguo sin tope registrado cae en el primer envío activo.
        antiguo = replace(enc, espera_tope="")
        self.assertFalse(ciclo._tope_de_la_espera_omitir(
            antiguo, "2026-11-18T10:30:00Z"
        ))
        self.assertTrue(ciclo._tope_de_la_espera_omitir(
            antiguo, "2026-11-18T10:50:00Z"
        ))


if __name__ == "__main__":
    unittest.main()
