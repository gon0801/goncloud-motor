from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path

from motor.encargos.dominio import Fase, TransicionIlegal, reducir
from motor.encargos.registro import EventoIlegal, Registro
from motor.encargos.situacion import Detenido, Deteniendo

H0 = "2026-11-17T10:00:00Z"
H1 = "2026-11-17T10:01:00Z"
H2 = "2026-11-17T10:02:00Z"
H3 = "2026-11-17T10:03:00Z"
H4 = "2026-11-17T10:04:00Z"


def evento(kind: str, hora: str = H0, **datos: object) -> dict:
    return {"kind": kind, "data": {"hora": hora, **datos}}


def pedido():
    return reducir(None, evento("entrega", id="p", clave="p", huella="h", texto="pedido"),
                   nuevo=False)


def esperar(e, clase="checks_merge", hora=H1, abierta=H1):
    return reducir(e, evento("espera", hora, motivo="pendiente", reanuda_en=H2,
                            clase=clase, abierta=abierta, tope=H3), nuevo=True)


class TablaEsperas(unittest.TestCase):
    def test_todas_las_clases_tienen_un_solo_tope(self):
        from motor.encargos.politica import ESPERAS_V5
        from motor.encargos.tabla_esperas import ESPERAS

        self.assertIs(ESPERAS_V5, False)
        self.assertEqual(len(ESPERAS), 27)
        self.assertEqual(set(ESPERAS), {fila.clase for fila in ESPERAS.values()})
        for fila in ESPERAS.values():
            with self.subTest(clase=fila.clase):
                self.assertEqual(
                    (fila.conducta.tope_s is not None) +
                    (fila.conducta.tope_intentos is not None), 1,
                )
                if fila.conducta.tope_intentos is not None:
                    self.assertEqual(fila.tipo, "por_hora")
        self.assertEqual(ESPERAS["deteniendo"].conducta.al_tope, "avisar")
        self.assertEqual(ESPERAS["despliegue_en_curso"].conducta.al_tope, "avisar")
        self.assertEqual(ESPERAS["tropiezo_merge"].conducta.tope_s, 6 * 3600)
        self.assertEqual(ESPERAS["repo"].hoy.tope_intentos, 3)


class HechosDeEspera(unittest.TestCase):
    def test_espera_vieja_y_abierta_entre_reintentos(self):
        e = reducir(pedido(), evento("espera", H1, motivo="vieja", reanuda_en=H2,
                                     abierta=H0), nuevo=False)
        self.assertEqual((e.situacion.clase, e.espera_clase, e.espera_abierta, e.espera_tope),
                         ("legada", "legada", H1, ""))
        e = esperar(e, hora=H2)
        e = reducir(e, evento("plazo", H2), nuevo=True)
        self.assertEqual(e.espera_clase, "checks_merge")
        e = esperar(e, hora=H3, abierta=H1)
        self.assertEqual((e.situacion.clase, e.situacion.abierta, e.espera_abierta),
                         ("checks_merge", H1, H1))
        e = reducir(e, evento("plazo", H3), nuevo=True)
        e = esperar(e, "proveedor", H3, abierta=H3)
        self.assertEqual(e.espera_abierta, H3)

    def test_avance_reinicia_ventana_y_racha_y_orden_la_vacia(self):
        e = esperar(pedido(), "proveedor")
        self.assertEqual(e.racha_esperas_desde, H1)
        e = reducir(e, evento("plazo", H2), nuevo=True)
        e = esperar(e, "github_pasajero", H2)
        self.assertEqual(e.racha_esperas_desde, H1)
        e = reducir(e, evento("revision", H2), nuevo=False)
        self.assertEqual((e.racha_esperas_desde, e.ultimo_avance), ("", H2))
        e = esperar(e, "proveedor", H3, abierta=H3)
        self.assertEqual((e.espera_abierta, e.racha_esperas_desde), (H3, H3))
        e = reducir(e, evento("plazo", H3), nuevo=True)
        e = esperar(e, "orden_espera", H3)
        self.assertEqual(e.racha_esperas_desde, "")

    def test_avance_renueva_apertura_de_la_misma_clase(self):
        e = esperar(pedido(), "checks_merge")
        e = reducir(e, evento("plazo", H2), nuevo=True)
        e = reducir(e, evento("revision", H2), nuevo=False)
        e = esperar(e, "checks_merge", H3, abierta=H3)
        self.assertEqual((e.espera_clase, e.espera_abierta), ("checks_merge", H3))

    def test_una_fila_que_no_detiene_no_mueve_la_racha(self):
        e = esperar(pedido(), "despliegue_en_curso")
        self.assertEqual(e.racha_esperas_desde, "")
        e = reducir(e, evento("plazo", H2), nuevo=True)
        e = esperar(e, "proveedor", H2, abierta=H2)
        self.assertEqual(e.racha_esperas_desde, H2)
        e = reducir(e, evento("plazo", H3), nuevo=True)
        e = esperar(e, "destino_ocupado", H3, abierta=H3)
        self.assertEqual(e.racha_esperas_desde, H2)

    def test_rechaza_clase_desconocida_y_tope_desactualizado(self):
        with self.assertRaises(TransicionIlegal):
            esperar(pedido(), "inventada")
        e = esperar(pedido())
        with self.assertRaises(TransicionIlegal):
            reducir(e, evento("tope_espera", H2, clase="checks_merge",
                              abierta=H1, tope="otro"), nuevo=True)
        with self.assertRaises(TransicionIlegal):
            reducir(e, evento("tope_espera", H2, clase="checks_merge",
                              abierta=H0, tope=H3), nuevo=True)
        vencida = reducir(e, evento("plazo", H2), nuevo=True)
        with self.assertRaises(TransicionIlegal):
            reducir(vencida, evento("tope_espera", H3, clase="checks_merge",
                                    abierta=H1, tope=H3), nuevo=True)

    def test_tope_detiene_y_continuar_reabre(self):
        from motor.encargos.situacion import PorTope

        e = esperar(pedido())
        e = reducir(e, evento("tope_espera", H3, clase="checks_merge",
                              abierta=H1, tope=H3), nuevo=True)
        self.assertEqual(e.estado, "detenido")
        self.assertEqual(e.situacion.causa, PorTope("checks_merge", H1, H3))
        self.assertEqual(e.tope_de, ("checks_merge", H1, H3))
        e = reducir(e, evento("orden", H3, accion="continuar"), nuevo=True)
        self.assertEqual((e.estado, e.tope_de, e.espera_abierta, e.turnos_sin_avance),
                         ("recibido", (), "", 0))

    def test_otras_aperturas_tienen_clase_y_tope_comprobable(self):
        for clase, hecho in (
            ("autoridad", evento("autoridad", H1, alcanzable=False)),
            ("efecto_incierto", evento("efecto", H1, id="x", estado="incierto")),
            ("impedimento_trabajo", evento("impedimento", H1, clase="entorno",
                                           causa="sin acceso", condicion="acceso")),
        ):
            with self.subTest(clase=clase):
                e = reducir(pedido(), hecho, nuevo=True)
                self.assertEqual((e.espera_clase, e.espera_abierta), (clase, H1))
                e = reducir(e, evento("tope_espera", H2, clase=clase,
                                      abierta=H1, tope=H2), nuevo=True)
                self.assertEqual((e.estado, e.tope_de), ("detenido", (clase, H1, H2)))

    def test_autoridad_recuperada_invalida_su_tope_aunque_siga_esperando(self):
        e = esperar(pedido(), "checks_merge")
        e = reducir(e, evento("autoridad", H2, alcanzable=False), nuevo=True)
        e = reducir(e, evento("autoridad", H3, alcanzable=True), nuevo=True)
        self.assertTrue(e.autoridad)
        with self.assertRaises(TransicionIlegal):
            reducir(e, evento("tope_espera", H4, clase="autoridad",
                              abierta=H2, tope=H4), nuevo=True)

    def test_un_tope_avisar_no_pisa_cancelacion_ni_otro_tope(self):
        from motor.encargos.situacion import PorTope

        e = reducir(pedido(), evento("orden", H1, accion="cancelar"), nuevo=True)
        self.assertIsInstance(e.situacion, Deteniendo)
        e = reducir(e, evento("tope_espera", H2, clase="deteniendo",
                              abierta=H1, tope=H2), nuevo=True)
        self.assertEqual((e.estado, e.orden_parada, e.tope_de),
                         ("deteniendo", "cancelar", ()))
        e = reducir(e, evento("arbol", H3, observacion="ausente"), nuevo=True)
        self.assertEqual(e.estado, "cancelado")

        e = esperar(pedido())
        from motor.encargos.encargo import evolucionar
        e = evolucionar(e, reserva=True)
        e = reducir(e, evento("tope_espera", H2, clase="checks_merge",
                              abierta=H1, tope=H3), nuevo=True)
        self.assertEqual((e.estado, e.tope_de), ("deteniendo", ("checks_merge", H1, H3)))
        e = reducir(e, evento("tope_espera", H3, clase="deteniendo",
                              abierta=H2, tope=H3), nuevo=True)
        self.assertEqual((e.orden_parada, e.tope_de), ("tope", ("checks_merge", H1, H3)))
        e = evolucionar(e, reserva=False)
        e = reducir(e, evento("arbol", H3, observacion="ausente"), nuevo=True)
        self.assertEqual(e.situacion.causa, PorTope("checks_merge", H1, H3))

    def test_impedimento_no_reemplaza_tope_de_cancelacion_vigente(self):
        e = reducir(pedido(), evento("intento_abierto", H1, id="i",
                                     pid=1, inicio=H1), nuevo=True)
        e = reducir(e, evento("orden", H2, accion="cancelar"), nuevo=True)
        e = reducir(e, evento("impedimento", H3, clase="entorno",
                              causa="sin acceso", condicion="acceso"), nuevo=True)
        self.assertEqual((e.espera_clase, e.espera_abierta), ("deteniendo", H2))
        e = reducir(e, evento("tope_espera", H4, clase="deteniendo",
                              abierta=H2, tope=H4), nuevo=True)
        self.assertEqual((e.estado, e.orden_parada, e.tope_de),
                         ("deteniendo", "cancelar", ()))
        e = reducir(e, evento("arbol", H4, observacion="ausente"), nuevo=True)
        self.assertEqual(e.estado, "cancelado")

    def test_falta_de_avance_detiene_y_continuar_reinicia(self):
        from motor.encargos.situacion import FaltaDeAvance

        for regla, desde, turnos in (("turnos", H0, 8), ("turno_vivo", H1, 0),
                                      ("esperas", H1, 0)):
            with self.subTest(regla=regla):
                e = pedido()
                if regla == "turnos":
                    for i in range(8):
                        e = reducir(e, evento("intento_abierto", H1, id=str(i),
                                              pid=1, inicio=H1), nuevo=False)
                elif regla == "turno_vivo":
                    e = reducir(e, evento("reserva", H1), nuevo=False)
                else:
                    e = esperar(e)
                e = reducir(e, evento("falta_de_avance", H2, regla=regla,
                                      desde=desde, turnos=turnos), nuevo=True)
                self.assertIsInstance(e.situacion, (Detenido, Deteniendo))
                self.assertEqual(e.falta_de_avance_de, (regla, desde, turnos))
                if e.estado == "deteniendo":
                    from motor.encargos.encargo import evolucionar
                    e = evolucionar(e, reserva=False, proc=None)
                    e = reducir(e, evento("arbol", H3, observacion="ausente"), nuevo=True)
                self.assertEqual(e.situacion.causa, FaltaDeAvance(regla, desde, turnos))
                e = reducir(e, evento("orden", H3, accion="continuar"), nuevo=True)
                self.assertEqual((e.estado, e.falta_de_avance_de, e.turnos_sin_avance),
                                 ("recibido", (), 0))

    def test_turno_cerrado_y_continuado_rechaza_decision_anterior(self):
        e = reducir(pedido(), evento("reserva", H1), nuevo=False)
        e = reducir(e, evento("orden", H2, accion="parar"), nuevo=True)
        e = reducir(e, evento("reserva_cerrada", H2), nuevo=True)
        e = reducir(e, evento("arbol", H2, observacion="ausente"), nuevo=True)
        e = reducir(e, evento("orden", H3, accion="continuar"), nuevo=True)
        self.assertEqual((e.estado, e.proc, e.reserva, e.reloj_turno),
                         ("recibido", None, False, H1))
        with self.assertRaises(TransicionIlegal):
            reducir(e, evento("falta_de_avance", H3, regla="turno_vivo",
                              desde=H1, turnos=0), nuevo=True)

    def test_falta_de_avance_no_pisa_causa_de_encargo_detenido(self):
        from motor.encargos.situacion import PorTope

        e = esperar(pedido(), "proveedor")
        e = reducir(e, evento("tope_espera", H3, clase="proveedor",
                              abierta=H1, tope=H3), nuevo=True)
        self.assertEqual(e.situacion.causa, PorTope("proveedor", H1, H3))
        with self.assertRaises(TransicionIlegal):
            reducir(e, evento("falta_de_avance", "2026-11-17T10:04:00Z", regla="esperas",
                              desde=H1, turnos=0), nuevo=True)
        self.assertEqual(e.situacion.causa, PorTope("proveedor", H1, H3))

    def test_turnos_y_reloj_cuentan_solo_hechos_con_avance(self):
        e = pedido()
        self.assertEqual(e.primer_evento, H0)
        e = reducir(e, {"kind": "intento_abierto", "data": {}}, nuevo=False)
        self.assertEqual((e.turnos_sin_avance, e.reloj_turno), (0, ""))
        e = reducir(e, evento("intento_abierto", H1, id="i", pid=1, inicio=H1), nuevo=False)
        self.assertEqual((e.turnos_sin_avance, e.reloj_turno), (1, H1))
        e = reducir(e, evento("conciliacion_fase", H2), nuevo=False)
        self.assertEqual((e.primer_evento, e.reloj_turno), (H0, H1))
        e = reducir(e, evento("incorporado", H2, generacion=0), nuevo=False)
        self.assertEqual((e.turnos_sin_avance, e.reloj_turno), (1, H1))
        e = reducir(e, evento("actividad", H2, observada=True), nuevo=False)
        self.assertEqual(e.reloj_turno, H2)
        e = reducir(e, evento("revision", H3), nuevo=False)
        self.assertEqual((e.ultimo_avance, e.turnos_sin_avance, e.reloj_turno),
                         (H3, 0, H3))

    def test_actividad_en_espera_renueva_reloj_y_rechaza_decision_obsoleta(self):
        e = reducir(pedido(), evento("intento_abierto", H1, id="i",
                                     pid=1, inicio=H1), nuevo=True)
        e = reducir(e, evento("espera", H2, motivo="red", reanuda_en=H4),
                    nuevo=True)
        e = reducir(e, evento("actividad", H3, observada=True,
                              hecho="sigue compilando"), nuevo=True)
        self.assertEqual(e.reloj_turno, H3)
        with self.assertRaises(TransicionIlegal):
            reducir(e, evento("falta_de_avance", H4, regla="turno_vivo",
                              desde=H1, turnos=1), nuevo=True)

    def test_producto_repetido_no_cuenta_como_avance(self):
        from dataclasses import replace

        e = replace(pedido(), fases=(Fase("f", "resultado"),), fase_activa="f")
        e = reducir(e, evento("producto_listo", H1, fase="f", version="v",
                              sha_git="sha"), nuevo=True)
        self.assertEqual(e.ultimo_avance, H1)
        e = reducir(e, evento("producto_listo", H2, fase="f", version="v",
                              sha_git="sha"), nuevo=True)
        self.assertEqual(e.ultimo_avance, H1)
        e = reducir(e, evento("producto_listo", H3, fase="f", version="v",
                              sha_git="otro"), nuevo=True)
        self.assertEqual(e.ultimo_avance, H3)

    def test_guardias_de_falta_de_avance_rechazan_decisiones_viejas(self):
        e = esperar(pedido(), "proveedor")
        self.assertEqual(
            reducir(e, evento("falta_de_avance", H2, regla="esperas",
                              desde=H1, turnos=0), nuevo=True).estado,
            "detenido",
        )
        for regla, desde, turnos in (("esperas", H0, 0), ("turnos", H0, 8),
                                      ("turno_vivo", H0, 0), ("no", H1, 0)):
            with self.subTest(regla=regla), self.assertRaises(TransicionIlegal):
                reducir(e, evento("falta_de_avance", H2, regla=regla,
                                  desde=desde, turnos=turnos), nuevo=True)

    def test_actividad_avance_y_continuar_invalidan_decisiones_anteriores(self):
        e = reducir(pedido(), evento("reserva", H1), nuevo=False)
        e = reducir(e, evento("actividad", H2, observada=True), nuevo=False)
        with self.assertRaises(TransicionIlegal):
            reducir(e, evento("falta_de_avance", H3, regla="turno_vivo",
                              desde=H1, turnos=0), nuevo=True)
        e = esperar(pedido(), "proveedor")
        e = reducir(e, evento("revision", H2), nuevo=False)
        with self.assertRaises(TransicionIlegal):
            reducir(e, evento("falta_de_avance", H3, regla="esperas",
                              desde=H1, turnos=0), nuevo=True)
        for i in range(8):
            e = reducir(e, evento("intento_abierto", H3, id=str(i), pid=1,
                                  inicio=H3), nuevo=False)
            e = reducir(e, evento("intento_cerrado", H3), nuevo=False)
        e = reducir(e, evento("falta_de_avance", H3, regla="turnos",
                              desde=H2, turnos=8), nuevo=True)
        self.assertEqual(e.estado, "detenido")
        e = reducir(e, evento("orden", H3, accion="continuar"), nuevo=True)
        with self.assertRaises(TransicionIlegal):
            reducir(e, evento("falta_de_avance", H3, regla="turnos",
                              desde=H2, turnos=8), nuevo=True)

    def test_aplicar_datos_malos_no_lanza(self):
        from motor.encargos.reductor import TABLA

        e = pedido()
        for kind in ("espera", "tope_espera", "falta_de_avance"):
            for datos in ({}, {"clase": [], "abierta": {}, "tope": float("nan")},
                          {"regla": {}, "desde": [], "turnos": float("inf")},
                          {"clase": "sin_clase", "regla": "no", "hora": None},
                          [], None):
                with self.subTest(kind=kind, datos=datos):
                    self.assertIn(kind, TABLA)
                    self.assertIsNotNone(TABLA[kind].aplicar(e, datos, H0, False))
                    if isinstance(datos, dict):
                        self.assertIsNotNone(reducir(e, {"kind": kind, "data": datos},
                                                    nuevo=False))


class RegistroEsperas(unittest.TestCase):
    def test_nuevos_kinds_se_escriben_en_secuencias_legales(self):
        from motor.encargos.situacion import FaltaDeAvance
        from motor.encargos.reporte import reporte

        temp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, temp)
        reg = Registro(Path(temp) / "base")
        self.addCleanup(reg.close)
        inicio = evento("entrega", H0, id="p", clave="p", huella="h", texto="pedido")
        reg.anotar("p", inicio, "inicio:p")
        reg.anotar("p", evento("espera", H1, motivo="pendiente", reanuda_en=H2,
                               clase="checks_merge", abierta=H1, tope=H3), "espera:p")
        reg.anotar("p", evento("tope_espera", H3, clase="checks_merge", abierta=H1,
                               tope=H3), "tope:p")
        self.assertEqual(reg.fold("p").tope_de, ("checks_merge", H1, H3))
        self.assertIn("checks_merge", reporte(reg.fold("p"), False).causa)
        reg.anotar("p", evento("orden", H3, accion="continuar"), "continuar:p")
        with self.assertRaises(EventoIlegal):
            reg.anotar("p", evento("falta_de_avance", H3, regla="turnos",
                                   desde=H0, turnos=7), "invalido:p")
        for i in range(8):
            reg.anotar("p", evento("intento_abierto", H3, id=str(i), pid=1,
                                   inicio=H3), f"turno:{i}")
            reg.anotar("p", evento("intento_cerrado", H3), f"cierre:{i}")
        reg.anotar("p", evento("falta_de_avance", H3, regla="turnos",
                               desde=H0, turnos=8), "falta:p")
        self.assertEqual(reg.fold("p").situacion.causa, FaltaDeAvance("turnos", H0, 8))

    def test_comprobar_conserva_la_cancelacion_tras_un_aviso(self):
        from tools.plegado import cargar_reductor, comprobar

        temp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, temp)
        reg = Registro(Path(temp) / "base")
        self.addCleanup(reg.close)
        reg.anotar("p", evento("entrega", H0, id="p", clave="p", huella="h",
                                texto="pedido"), "inicio:p")
        reg.anotar("p", evento("orden", H1, accion="cancelar"), "cancelar:p")
        reg.anotar("p", evento("tope_espera", H2, clase="deteniendo", abierta=H1,
                               tope=H2), "tope:p")
        reg.anotar("p", evento("arbol", H3, observacion="ausente"), "arbol:p")
        resultado = comprobar(cargar_reductor(Path(__file__).resolve().parents[1]),
                              reg.base / "registro.sqlite")
        self.assertFalse(
            [v for v in resultado.violaciones if v.regla == "cancelacion_definitiva"],
        )
