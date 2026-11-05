"""La recuperación explícita restaura sólo la fase que cerró el atajo."""

import json
from contextlib import closing
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from motor.encargos import ciclo
from motor.encargos.dominio import TransicionIlegal, reducir
from tests import test_reanudar_fase_integrada as integrado

setUpModule = integrado.setUpModule


class ReabrirCierreForzado(unittest.TestCase):
    legado_integrado = integrado.ReanudarFaseIntegrada.legado_integrado

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        r, self.gh, _ = self.legado_integrado(
            tmp.name, ("propuesta", "motor-despliegue", "meta-sombra"),
        )
        self.r = r
        ciclo._paso_entrega(r.reg, r.fold(), self.gh, r.hora)
        r.reg.anotar(r.enc, {"kind": "conductor", "data": {
            "token": "conductor-anterior", "host": "host-anterior",
        }}, "conductor-anterior")
        r.reg.anotar(r.enc, {"kind": "pendiente_entrega", "data": {
            "texto": "no queda otro modelo autorizado",
        }}, "pendiente-anterior")
        self.antes = r.fold()
        self.assertEqual(self.antes.conductor, "conductor-anterior")
        self.cerrar("cierre-original")

    def cerrar(self, clave):
        self.r.reg.anotar(self.r.enc, {"kind": "cierre_forzado", "data": {
            "evidencia": "entregado con pendientes abiertos: no hay modelo",
            "hora": self.r.hora,
        }}, clave)
        self.assertEqual(self.r.fold().estado, "terminado")

    def reabrir(self, clave="reabrir-original", **kw):
        r = self.r
        salida = ciclo.ordenar(r.reg, r.enc, "reabrir", clave, r.hora, **kw)
        self.assertFalse(salida.get("rechazada"), salida.get("motivo"))
        return r.fold()

    def test_reabrir_restaura_b_con_a_intacta_y_espera_continuar(self):
        r = self.r
        nuevo = self.reabrir()
        self.assertEqual(nuevo.estado, "detenido")
        self.assertEqual(nuevo.fases, self.antes.fases)
        self.assertEqual(nuevo.fase_activa, "motor-despliegue")
        self.assertFalse(nuevo.escrituras or nuevo.intentos_nuevos)
        self.assertEqual(nuevo.pendientes_entrega, ())
        self.assertEqual(nuevo.relevos_por_rol, ())
        self.assertEqual(nuevo.conductor, "")
        self.assertEqual(nuevo.intento, "")
        lanzador = mock.Mock(side_effect=AssertionError("no autorizado"))
        ciclo.barrer(r.reg, r.hora, time.time(), lanzador, gh=self.gh)
        self.assertEqual(r.fold().estado, "detenido")
        ciclo.ordenar(r.reg, r.enc, "continuar", "continuar-recuperado", r.hora)
        self.assertEqual(r.fold().fase_activa, "motor-despliegue")
        self.assertEqual(r.fold().fases[0], self.antes.fases[0])
        self.assertEqual(r.fold().estado, "recibido")

    def test_repetir_clave_no_reabre_dos_veces(self):
        r = self.r
        self.reabrir()
        eventos = r.reg.eventos(r.enc)
        salida = ciclo.ordenar(r.reg, r.enc, "reabrir", "reabrir-original", r.hora)
        self.assertTrue(salida.get("retransmision"))
        self.assertEqual(eventos, r.reg.eventos(r.enc))

    def test_resultados_del_conductor_anterior_se_rechazan(self):
        r = self.r
        self.reabrir()
        salida = ciclo.recibir_resultado(
            r.reg, r.enc, "conductor-anterior", "host-anterior", self.antes.huella,
            {}, {}, r.hora,
        )
        self.assertTrue(salida.get("conflicto"), salida)
        ciclo.ordenar(r.reg, r.enc, "continuar", "seguir", r.hora)
        (r.d / "outcome.json").write_text(json.dumps({"fallo_runtime": "fallo antiguo"}))
        ciclo._incorporar(r.reg, r.fold(), r.espacio, r.hora, r.d)
        self.assertEqual(r.eventos("proveedor_fallo"), [])
        self.assertEqual(r.fold().fase_activa, "motor-despliegue")

    def test_un_outcome_residual_no_restaura_la_sesion_anterior(self):
        r = self.r
        self.reabrir()
        ciclo.ordenar(r.reg, r.enc, "continuar", "seguir", r.hora)
        (r.d / "outcome.json").write_text(json.dumps({
            "fallo_runtime": "fallo antiguo", "sesion_runtime": "sesion-revisor-anterior",
        }))
        ciclo._incorporar(r.reg, r.fold(), r.espacio, r.hora, r.d)
        self.assertEqual(r.fold().sesion_responsable, "")
        self.assertEqual(r.fold().sesion_revisor, "")

    def test_la_firma_antigua_no_reabre_otro_cierre(self):
        r = self.r
        firma = r.fold().cierre_forzado_huella
        self.reabrir()
        ciclo.ordenar(r.reg, r.enc, "continuar", "seguir", r.hora)
        self.cerrar("cierre-segundo")
        self.assertNotEqual(r.fold().cierre_forzado_huella, firma)
        salida = ciclo.ordenar(r.reg, r.enc, "reabrir", "orden-tardia", r.hora,
                               fase="motor-despliegue", cierre=firma)
        self.assertTrue(salida.get("rechazada"))
        self.assertEqual(r.fold().estado, "terminado")

    def test_la_orden_remota_conserva_la_firma_aunque_no_llegue(self):
        from motor.encargos import transporte
        r = self.r
        firma = r.fold().cierre_forzado_huella
        mensajes = []

        def enviar(base, mensaje):
            mensajes.append(mensaje)
            return {"alcanzada": False}

        with mock.patch.object(transporte, "cargar", return_value={"host": "autoridad"}), \
                mock.patch.object(transporte, "operar", side_effect=enviar):
            salida = ciclo.ordenar(r.reg, r.enc, "reabrir", "reabrir-remoto", r.hora)
        self.assertTrue(salida.get("autoridad_pendiente"))
        self.assertEqual(r.fold().estado, "detenido")
        self.assertEqual(mensajes, [{"op": "reabrir", "encargo": r.enc,
                                   "clave": "reabrir-remoto", "fase": "motor-despliegue",
                                   "cierre": firma}])
        eventos = r.reg.eventos(r.enc)
        with mock.patch.object(transporte, "cargar", return_value={"host": "autoridad"}), \
                mock.patch.object(transporte, "operar", side_effect=enviar):
            repetida = ciclo.ordenar(r.reg, r.enc, "reabrir", "reabrir-remoto", r.hora)
        self.assertTrue(repetida.get("retransmision"))
        self.assertTrue(repetida.get("autoridad_pendiente"))
        self.assertEqual(len(mensajes), 2)
        self.assertEqual(mensajes[0], mensajes[1])
        self.assertEqual(r.reg.eventos(r.enc), eventos)

    def test_otro_cierre_forzado_no_colisiona_con_el_anterior(self):
        r = self.r
        for n in range(2):
            self.reabrir(f"reabrir-{n}")
            ciclo.ordenar(r.reg, r.enc, "continuar", f"seguir-{n}", r.hora)
            r.reg.anotar(r.enc, {"kind": "pendiente_entrega", "data": {
                "texto": "la misma causa",
            }}, f"pendiente-{n}")
            ciclo._entregar_con_pendientes(r.reg, r.fold(), None, r.hora)
            self.assertEqual(r.fold().estado, "terminado")
        self.assertEqual(len(r.eventos("cierre_forzado")), 3)

    def test_reabrir_invalida_el_marcador_del_cierre_anterior(self):
        r = self.r
        ciclo._marcar_autoridad_cerrada(r.reg, r.fold(), r.hora)
        marcador = ciclo._marcador_autoridad_final(r.reg, r.fold())
        self.reabrir()
        self.assertTrue(ciclo._nuevo_cierre_tras_marcador(r.reg, r.fold(), marcador))

    def test_cierre_normal_y_cancelacion_siguen_siendo_terminales(self):
        for estado in ("terminado", "cancelado"):
            with self.subTest(estado=estado):
                from dataclasses import replace
                normal = replace(self.antes, estado=estado)
                with self.assertRaisesRegex(TransicionIlegal, "cierre forzado"):
                    reducir(normal, {"kind": "orden", "data": {"accion": "reabrir"}})

    def test_el_cierre_normal_tras_reparar_ci_invalida_la_reapertura_vieja(self):
        e = reducir(None, {"kind": "entrega", "data": {
            "id": "ci", "clave": "ci", "huella": "ci", "texto": "producto",
        }})
        e = reducir(e, {"kind": "cierre_forzado", "data": {"evidencia": "pendiente"}})
        orden = {"accion": "reabrir", "fase": e.fase_activa, "cierre": e.cierre_forzado_huella}
        e = reducir(e, {"kind": "ci_pr", "data": {
            "estado": "rojo", "reabrir": True, "sha": "sha",
        }})
        e = reducir(e, {"kind": "comprobacion", "data": {
            "clase": "confirmado", "evidencia": "corregido y comprobado",
        }})
        self.assertEqual(e.estado, "terminado")
        with self.assertRaisesRegex(TransicionIlegal, "cierre forzado"):
            reducir(e, {"kind": "orden", "data": orden})

    def test_un_conflicto_remoto_no_acredita_la_sincronizacion(self):
        from motor.encargos import transporte
        r = self.r
        with mock.patch.object(transporte, "cargar", return_value={"host": "autoridad"}), \
                mock.patch.object(transporte, "operar", return_value={
                    "alcanzada": True, "salida": {"conflicto": True},
                }):
            salida = ciclo.ordenar(r.reg, r.enc, "reabrir", "conflicto-remoto", r.hora)
        self.assertTrue(salida.get("autoridad_pendiente"))

    def test_respuesta_perdida_reintenta_la_misma_orden_en_el_barrido(self):
        import sqlite3
        from motor.encargos import transporte
        from motor.encargos.registro import Registro
        r = self.r
        remote_base = r.base.parent / "autoridad"
        remote_base.mkdir()
        with closing(sqlite3.connect(remote_base / "registro.sqlite")) as dst:
            r.reg.db.backup(dst)
        remote = Registro(remote_base)
        self.addCleanup(remote.close)
        claves = []

        def enviar(base, mensaje):
            if mensaje["op"] == "ver":
                return {"alcanzada": True, "salida": {"estado": remote.fold(r.enc).estado}}
            claves.append(mensaje["clave"])
            salida = ciclo.ordenar(remote, r.enc, "reabrir", mensaje["clave"], r.hora,
                                   fase=mensaje["fase"], cierre=mensaje["cierre"])
            return {"alcanzada": len(claves) > 1, "salida": salida}

        with mock.patch.object(transporte, "cargar", side_effect=lambda b: None if b == remote.base else {}), \
                mock.patch.object(transporte, "operar", side_effect=enviar):
            salida = ciclo.ordenar(r.reg, r.enc, "reabrir", "respuesta-perdida", r.hora)
            self.assertTrue(salida.get("autoridad_pendiente"))
            ciclo._mirar_autoridad(r.reg, r.fold(), r.hora)
        self.assertEqual(claves, ["respuesta-perdida", "respuesta-perdida"])
        self.assertEqual(len([e for e in remote.eventos(r.enc)
                              if e["kind"] == "orden" and e["data"]["accion"] == "reabrir"]), 1)
        self.assertIsNotNone(r.reg.por_ukey(f"autoridad-orden:{r.enc}:{salida['recibo']}"))


if __name__ == "__main__":
    unittest.main()
