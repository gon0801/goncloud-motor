"""5.2: el tope de cada espera tiene su salida y su aviso.

Una prueba por acción de la tabla (`detener`, `avisar` y las que no actúan en el
barrido), más el aviso que sale por su camino, `continuar` abriendo ventana y la
reanudación sin hora. Con el interruptor apagado no pasa nada de esto.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from motor.encargos import ciclo, reporte, resumen
from motor.encargos.entrega import avisos
from motor.encargos.registro import Registro
from motor.encargos.situacion import Detenido, PorTope

H0 = "2026-11-18T10:00:00Z"
H_59 = "2026-11-18T10:59:00Z"
H_61 = "2026-11-18T11:01:00Z"
H_6H = "2026-11-18T16:01:00Z"


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

    def espera(self, clase: str, hora: str = H0, **kw) -> None:
        ciclo.marcar_espera(self.reg, self.enc, clase, "pendiente", hora, **kw)

    def tope(self, hora: str):
        with mock.patch("motor.encargos.politica.ESPERAS_V5", True):
            return ciclo._tope_de_la_espera(
                self.reg, self.reg.fold(self.enc), hora
            )


class Detener(Banco):
    def test_un_minuto_antes_sigue_y_al_tope_detiene(self):
        with mock.patch("motor.encargos.politica.ESPERAS_V5", True):
            self.espera("checks_merge")
            antes = self.tope(H_59)
        self.assertEqual(antes.estado, "esperando_reintento")
        self.assertEqual(_eventos(self.reg, self.enc, "tope_espera"), [])
        despues = self.tope(H_61)
        self.assertEqual(despues.estado, "detenido")
        self.assertIsInstance(despues.situacion, Detenido)
        self.assertIsInstance(despues.situacion.causa, PorTope)
        self.assertEqual(despues.situacion.causa.clase, "checks_merge")

    def test_un_solo_aviso_por_tope_y_continuar_abre_ventana(self):
        self.espera("checks_merge")
        detenido = self.tope(H_61)
        self.assertEqual(_eventos(self.reg, self.enc, "tope_espera").__len__(), 1)
        self.assertEqual(
            avisos._detencion_nueva(self.reg, detenido, 0),
            _eventos(self.reg, self.enc, "tope_espera")[0]["id"],
        )
        with mock.patch("motor.encargos.politica.ESPERAS_V5", True):
            ciclo.ordenar(self.reg, self.enc, "continuar", "sigo", H_61)
        fresco = self.reg.fold(self.enc)
        self.assertEqual(fresco.estado, "recibido")
        self.assertEqual((fresco.tope_de, fresco.espera_abierta), ((), ""))
        with mock.patch("motor.encargos.politica.ESPERAS_V5", True):
            self.espera("checks_merge", H_61)
        data = _eventos(self.reg, self.enc, "espera")[-1]["data"]
        self.assertEqual(data["abierta"], H_61)

    def test_el_texto_de_la_detencion_nombra_la_espera(self):
        self.espera("checks_merge")
        texto = reporte._detencion(self.tope(H_61))
        self.assertIn("espera «checks del merge»", texto)
        self.assertIn("abierta a las", texto)


class Avisar(Banco):
    def test_avisa_una_vez_y_sigue_esperando(self):
        with mock.patch("motor.encargos.politica.ESPERAS_V5", True):
            self.espera("despliegue_en_curso")
            uno = self.tope(H_61)
            dos = self.tope(H_61)
        self.assertEqual(uno.estado, "esperando_reintento")
        self.assertEqual(dos.estado, "esperando_reintento")
        self.assertEqual(len(_eventos(self.reg, self.enc, "tope_espera")), 1)

    def test_el_aviso_sale_por_su_camino_y_no_dice_termino(self):
        from motor.integraciones import directores

        self.espera("despliegue_en_curso")
        self.tope(H_61)
        vista = directores.VistaAvisos(self.base)
        with mock.patch("motor.encargos.politica.ESPERAS_V5", True):
            trabajos = directores._trabajos_de_encargo(
                self.reg, self.enc, self.reg.fold(self.enc), 0, 0, 0, vista
            )
        topes = [cuerpo for aviso, cuerpo in trabajos if aviso.get("tipo") == "tope"]
        self.assertEqual(len(topes), 1, trabajos)
        self.assertIn("pasó su tope", topes[0])
        self.assertNotIn("Terminó tu encargo", topes[0])

    def test_apagado_no_emite_ni_reenvia_el_aviso_de_tope(self):
        from motor.integraciones import directores

        self.espera("despliegue_en_curso")
        self.tope(H_61)
        vista = directores.VistaAvisos(self.base)
        # Un aviso de tope guardado como incierto antes de apagar el interruptor.
        aviso = dict(avisos.topes_nuevos(self.reg, self.reg.fold(self.enc), 0)[0])
        aviso.update(estado="incierto", canal="telegram")
        vista.put(aviso)
        cuerpo = self.base / "canal" / "telegram" / aviso["id"]
        cuerpo.parent.mkdir(parents=True)
        cuerpo.write_text("sigo esperando", encoding="utf-8")
        encargos = {self.enc: self.reg.fold(self.enc)}
        with (
            mock.patch("motor.encargos.politica.ESPERAS_V5", False),
            mock.patch.object(directores, "_publicar_aviso") as publicar,
        ):
            trabajos = directores._trabajos_de_encargo(
                self.reg, self.enc, self.reg.fold(self.enc), 0, 0, 0, vista
            )
            publicados = directores._reintentar_avisos_sueltos(
                self.reg, directores.Vuelta(1.0), set(), "", encargos, vista,
            )
        self.assertFalse([a for a, _ in trabajos if a.get("tipo") == "tope"])
        self.assertEqual(publicados, [])
        publicar.assert_not_called()

    def test_un_aviso_guardado_de_una_espera_cerrada_se_descarta(self):
        from motor.integraciones import directores

        self.espera("despliegue_en_curso")
        self.tope(H_61)
        vista = directores.VistaAvisos(self.base)
        # El aviso queda en el canal y el encargo sigue; luego la espera cierra.
        guardado = [d for d in vista.docs.values() if d.get("tipo") == "tope"]
        if not guardado:
            aviso = avisos.topes_nuevos(self.reg, self.reg.fold(self.enc), 0)[0]
            aviso["estado"] = "incierto"
            vista.put(aviso)
        self.reg.anotar(
            self.enc, {"kind": "plazo", "data": {"hora": H_61}}, f"plazo:{self.enc}:x"
        )
        # Con la espera cerrada, el aviso guardado ya no vale.
        enc = self.reg.fold(self.enc)
        doc = next(iter(vista.docs.values()))
        self.assertFalse(directores._el_tope_sigue_vigente(enc, doc))

    def test_si_la_espera_cerro_el_aviso_no_sale(self):
        self.espera("despliegue_en_curso")
        self.tope(H_61)
        self.reg.anotar(
            self.enc, {"kind": "plazo", "data": {"hora": H_61}}, f"plazo:{self.enc}:1"
        )
        self.assertEqual(
            avisos.topes_nuevos(self.reg, self.reg.fold(self.enc), 0), ()
        )


class SinAccionEnElBarrido(Banco):
    def test_las_filas_omitir_pendientes_devolver_impedimento_y_seguir_no_escriben(self):
        casos = (
            ("revision_deepseek", H_6H),
            ("destino_ocupado", H_6H),
            ("empuje", H_6H),
            ("despliegue_sin_receta_otra_fase", H_6H),
            ("orden_espera", H_6H),
        )
        for clase, hora in casos:
            with self.subTest(clase=clase):
                self.setUp()
                with mock.patch("motor.encargos.politica.ESPERAS_V5", True):
                    self.espera(clase, H0, reanuda_en=H_6H)
                    self.tope(hora)
                self.assertEqual(_eventos(self.reg, self.enc, "tope_espera"), [])


class Apagado(Banco):
    def test_sin_el_interruptor_no_hay_tope(self):
        self.espera("checks_merge")
        with mock.patch("motor.encargos.politica.ESPERAS_V5", False):
            ciclo._tope_de_la_espera(self.reg, self.reg.fold(self.enc), H_61)
        self.assertEqual(_eventos(self.reg, self.enc, "tope_espera"), [])
        self.assertEqual(self.reg.fold(self.enc).estado, "esperando_reintento")


class ReanudacionSinHora(Banco):
    def test_vence_a_los_sesenta_minutos_de_su_apertura(self):
        self.espera("orden_espera", H0, reanuda_en="nunca")
        enc = self.reg.fold(self.enc)
        with mock.patch("motor.encargos.politica.ESPERAS_V5", True):
            self.assertFalse(ciclo._vencio_sin_hora(enc, H_59))
            self.assertTrue(ciclo._vencio_sin_hora(enc, H_61))
        with mock.patch("motor.encargos.politica.ESPERAS_V5", False):
            self.assertFalse(ciclo._vencio_sin_hora(enc, H_6H))


class ReintentosLargos(Banco):
    def test_repo_encendido_no_corta_a_la_tercera(self):
        with mock.patch("motor.encargos.politica.ESPERAS_V5", True):
            for i in range(4):
                ciclo._fallo_de_repo(
                    self.reg, self.reg.fold(self.enc),
                    "git fetch: no se alcanza el remoto", H0, f"repo-{i}",
                )
        enc = self.reg.fold(self.enc)
        self.assertFalse([i for i in enc.impedimentos if i.clase == "repo"])

    def test_repo_encendido_sigue_la_escalera(self):
        with mock.patch("motor.encargos.politica.ESPERAS_V5", True):
            for i in range(3):
                ciclo._fallo_de_repo(
                    self.reg, self.reg.fold(self.enc),
                    "git fetch: no se alcanza el remoto", f"2026-11-18T10:0{i}:00Z",
                    f"repo-{i}",
                )
        esperas = _eventos(self.reg, self.enc, "espera")
        self.assertEqual(
            [e["data"]["reanuda_en"] for e in esperas],
            ["2026-11-18T10:05:00Z", "2026-11-18T10:16:00Z", "2026-11-18T10:32:00Z"],
        )

    def test_el_tropiezo_de_merge_se_reintenta_con_el_interruptor_encendido(self):
        from motor.respuestas_merge import BaseMovida, tras_el_rechazo

        causa = "base movida: la base cambió"
        with mock.patch("motor.encargos.politica.ESPERAS_V5", True):
            self.assertEqual(
                tras_el_rechazo(BaseMovida(causa), None, causa, 9), (causa, causa)
            )
        self.assertEqual(tras_el_rechazo(BaseMovida(causa), None, causa, 9)[0], "")

    def test_la_escalera_del_merge_espacia_los_reintentos(self):
        from motor.encargos.tabla_esperas import reanuda_en

        with mock.patch("motor.encargos.politica.ESPERAS_V5", True):
            self.assertEqual(
                [reanuda_en("tropiezo_merge", H0, n) for n in (1, 2, 3, 4)],
                [
                    "2026-11-18T10:02:00Z", "2026-11-18T10:05:00Z",
                    "2026-11-18T10:15:00Z", "2026-11-18T10:30:00Z",
                ],
            )


class Vencimiento(Banco):
    def test_una_hora_que_no_se_lee_no_adelanta_el_plazo(self):
        self.espera("proveedor", H0, reanuda_en="0")
        enc = self.reg.fold(self.enc)
        with mock.patch("motor.encargos.politica.ESPERAS_V5", True):
            self.assertFalse(ciclo._vencio_la_reanudacion(enc, H0))
            self.assertFalse(ciclo._vencio_la_reanudacion(enc, H_59))
            self.assertTrue(ciclo._vencio_la_reanudacion(enc, H_61))

    def test_una_hora_con_desplazamiento_vence_en_su_instante(self):
        self.espera("proveedor", H0, reanuda_en="2026-11-18T03:00:00-07:00")
        enc = self.reg.fold(self.enc)
        with mock.patch("motor.encargos.politica.ESPERAS_V5", True):
            self.assertFalse(ciclo._vencio_la_reanudacion(enc, "2026-11-18T09:59:00Z"))
            self.assertTrue(ciclo._vencio_la_reanudacion(enc, "2026-11-18T10:00:00Z"))


class FueraDeEsperandoReintento(Banco):
    def test_el_tope_de_un_conciliando_detiene(self):
        self.reg.anotar(
            self.enc,
            {"kind": "efecto", "data": {
                "id": "e1", "estado": "incierto", "hora": H0,
            }},
            f"efecto:e1:{self.enc}",
        )
        self.assertEqual(self.reg.fold(self.enc).estado, "conciliando")
        with mock.patch("motor.encargos.politica.ESPERAS_V5", True):
            antes = self.tope(H_59)
            despues = self.tope(H_61)
        self.assertEqual(antes.estado, "conciliando")
        self.assertIsInstance(despues.situacion, Detenido)


class Resumen(Banco):
    def test_un_detenido_sale_con_su_causa(self):
        self.espera("checks_merge")
        self.tope(H_61)
        with mock.patch("motor.encargos.politica.ESPERAS_V5", True):
            cuerpo = resumen.texto(self.reg)
        with mock.patch("motor.encargos.politica.ESPERAS_V5", False):
            self.assertNotIn("checks del merge", resumen.texto(self.reg))
        self.assertIn("Crea dato.txt con 42", cuerpo)
        self.assertIn("checks del merge", cuerpo)
        self.assertIn("llegó a su tope", cuerpo)

    def test_la_alerta_de_dos_horas_lee_la_apertura(self):
        self.espera("proveedor", H0)
        enc = self.reg.fold(self.enc)
        desde = resumen._epoch(enc.espera_abierta)
        with mock.patch("motor.encargos.politica.ESPERAS_V5", True):
            self.assertTrue(
                resumen._espera_sostenida(enc, desde + 3 * 60 * 60, None)
            )
            self.assertFalse(
                resumen._espera_sostenida(enc, desde + 60 * 60, None)
            )
        with mock.patch("motor.encargos.politica.ESPERAS_V5", False):
            self.assertFalse(
                resumen._espera_sostenida(enc, desde + 3 * 60 * 60, None)
            )


class SinRecetaDeclarada(unittest.TestCase):
    """Una fase válida sin declaración de despliegue cuenta intentos y llega al tope.

    `requiere_despliegue=None` en una fase con `resultado` válido: el camino real
    `_paso_despliegue_fase` la deja esperando cinco veces y a la quinta deja el
    impedimento terminal y la conciliación `espera_agotada`. Apagado el
    interruptor, no cuenta ni escribe.
    """

    HORAS = (
        "2026-11-18T10:00:00Z",
        "2026-11-18T10:01:00Z",
        "2026-11-18T10:02:00Z",
        "2026-11-18T10:03:00Z",
        "2026-11-18T10:04:00Z",
    )

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        raiz = Path(self._tmp.name)
        self.espacio = raiz / "repo"
        self.espacio.mkdir()
        (self.espacio / ".interno").mkdir()
        self.reg = Registro(raiz / "registro")
        self.addCleanup(self.reg.close)
        self.reg.anotar("e", {"kind": "entrega", "data": {
            "id": "e", "clave": "e", "huella": "e",
            "texto": "Ejecuta plans/producto.md",
            "entrega_nivel": "desplegado", "repo": "owner/repo",
        }}, "entrega")
        self.reg.anotar("e", {"kind": "fases", "data": {"fases": [
            {"id": "A", "resultado": "Documentación y SQL"},
            {"id": "B", "resultado": "Receta disponible", "requiere": ["A"]},
            {"id": "D", "resultado": "Servicio desplegado", "requiere": ["B"]},
        ]}}, "fases")
        # B queda integrada con su resultado válido y sin receta declarada.
        self.reg.anotar("e", {"kind": "conciliacion_fase", "data": {
            "fase": "B", "paso": "integrado", "sha": "b" * 40, "hora": H0,
        }}, "integrado-B")

    def _fase(self, enc):
        return next(f for f in enc.fases if f.id == "B")

    def _paso(self, hora, encendido=True):
        with mock.patch("motor.encargos.politica.ESPERAS_V5", encendido):
            return ciclo._paso_despliegue_fase(
                self.reg, self.reg.fold("e"), self._fase(self.reg.fold("e")),
                self.espacio, hora,
            )

    def _pasos_de_b(self):
        return [
            e["data"]["paso"] for e in _eventos(self.reg, "e", "conciliacion_fase")
            if e["data"].get("fase") == "B"
        ]

    def test_cinco_intentos_y_el_quinto_deja_el_impedimento_terminal(self):
        for numero, hora in enumerate(self.HORAS, start=1):
            enc = self._paso(hora)
            fase = self._fase(enc)
            if numero < 5:
                self.assertFalse(fase.despliegue_espera_agotada, numero)
                self.assertEqual(fase.despliegue_espera_intentos, numero)
            else:
                self.assertTrue(fase.despliegue_espera_agotada)
                self.assertIn("la receta no arrancó 5 veces", fase.despliegue_impedido)
        esperas = [
            paso for paso in self._pasos_de_b()
            if paso in ("espera", "espera_agotada")
        ]
        self.assertEqual(
            esperas,
            ["espera", "espera", "espera", "espera", "espera_agotada"],
        )
        impedimentos = [
            e["data"]["clase"] for e in _eventos(self.reg, "e", "impedimento")
            if e["data"].get("clase", "").startswith("despliegue:")
        ]
        self.assertTrue(impedimentos)

    def test_apagado_no_cuenta_ni_escribe(self):
        enc = self._paso(self.HORAS[0], encendido=False)
        fase = self._fase(enc)
        self.assertEqual(fase.despliegue_espera_intentos, 0)
        self.assertFalse(fase.despliegue_espera_agotada)
        self.assertNotIn("espera", self._pasos_de_b())


class CarreraDelVencimiento(Banco):
    """Una orden que cambia la espera entre el vencimiento y el plazo."""

    def test_una_orden_que_cambia_la_espera_no_reabre_la_nueva(self):
        original = ciclo._tope_de_la_espera

        def carrera(registro, enc, hora):
            # La orden entra después de la lectura del barrido y antes del tope.
            ciclo.marcar_espera(
                registro, enc.id, "orden_espera", "hasta mañana", hora,
                reanuda_en="2026-11-19T10:00:00Z",
            )
            return original(registro, enc, hora)

        with mock.patch("motor.encargos.politica.ESPERAS_V5", True):
            self.espera("despliegue_en_curso", H0)
        with (
            mock.patch("motor.encargos.politica.ESPERAS_V5", True),
            mock.patch.object(
                ciclo, "_tope_de_la_espera", side_effect=carrera
            ),
        ):
            ciclo._barrer_encargos(
                self.reg, [self.enc], H_61,
                ciclo._epoch_de_hora(H_61), None,
            )
        fresco = self.reg.fold(self.enc)
        self.assertEqual(fresco.estado, "esperando_reintento")
        self.assertEqual(fresco.espera_clase, "orden_espera")
        self.assertEqual(fresco.reanuda_en, "2026-11-19T10:00:00Z")
        self.assertEqual(_eventos(self.reg, self.enc, "plazo"), [])


class TopeAntesDelSondeo(Banco):
    """El tope se cobra en el recorrido común antes de que el sondeo atienda."""

    def test_una_receta_sin_declarar_llega_al_tope_aunque_el_sondeo_atienda(self):
        with (
            mock.patch("motor.encargos.politica.ESPERAS_V5", True),
            mock.patch("motor.encargos.politica.ENTREGA_V4", False),
        ):
            self.espera("despliegue_sin_receta", H0)
            with mock.patch.object(
                ciclo, "conducir_pr",
                side_effect=lambda registro, enc, *a, **k: (enc, True),
            ) as conducir:
                ciclo.barrer(
                    self.reg, H_61, ciclo._epoch_de_hora(H_61), None,
                    gh=object(),
                )
        self.assertEqual(self.reg.fold(self.enc).estado, "detenido")
        self.assertTrue(_eventos(self.reg, self.enc, "tope_espera"))
        conducir.assert_not_called()


class TopeDeDeteniendo(Banco):
    """La espera de `deteniendo` avisa a su tope sin soltar la parada."""

    def test_un_proceso_que_no_muere_avisa_y_sigue_deteniendo(self):
        ciclo.ordenar(self.reg, self.enc, "parar", "paro", H0)
        self.assertEqual(self.reg.fold(self.enc).estado, "deteniendo")
        with (
            mock.patch("motor.encargos.politica.ESPERAS_V5", True),
            mock.patch("motor.encargos.politica.ENTREGA_V4", False),
            mock.patch.object(ciclo, "observar", return_value="vivo"),
            mock.patch.object(ciclo, "cerrar"),
        ):
            ciclo.barrer(
                self.reg, H_61, ciclo._epoch_de_hora(H_61), None,
            )
        fresco = self.reg.fold(self.enc)
        self.assertEqual(fresco.estado, "deteniendo")
        self.assertEqual(fresco.orden_parada, "parar")
        self.assertTrue(_eventos(self.reg, self.enc, "tope_espera"))
