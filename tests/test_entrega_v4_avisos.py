"""El corte y la palabra evitan avisos retrospectivos y ráfagas."""

from __future__ import annotations

import tempfile
import unittest
import json
from dataclasses import replace
from unittest import mock

from motor.encargos import politica
from motor.encargos.entrega.tipos import PrAbierto, PrDeFase
from motor.integraciones import directores
from tests.test_aviso_cierre import Banco, HORA, T0
from tests.test_entrega_v4_reporte import encargo


class AvisosPorPalabra(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.banco = Banco(self.tmp.name)
        self.addCleanup(self.banco.cerrar)

    def _terminar(self) -> str:
        ident = self.banco.encargo("# Resultado\n\nPedido", "resultado")
        self.banco.terminar(ident)
        return ident

    def _limpio(self, ident: str) -> None:
        self.banco.reg.anotar(
            ident, {"kind": "limpio", "data": {"hora": HORA}},
            f"limpio:{ident}",
        )

    def _fallo(self, ident: str, resultado: str, intento: int) -> None:
        self.banco.reg.anotar(
            ident, {"kind": "limpieza", "data": {
                "recurso": "rama", "clave": "encargo/x",
                "resultado": resultado, "intento": intento, "hora": HORA,
            }},
            f"limpieza:{ident}:{intento}",
        )

    def test_el_cierre_espera_limpio_solo_si_el_interruptor_esta_encendido(self) -> None:
        ident = self._terminar()
        with mock.patch.object(politica, "ENTREGA_V4", True):
            self.assertEqual(directores.drenar(self.banco.reg, ahora=T0)["avisos"], [])
            self._limpio(ident)
            avisos = directores.drenar(self.banco.reg, ahora=T0 + 1)["avisos"]
        self.assertEqual(len(avisos), 1)
        self.assertTrue(avisos[0]["entregado"])
        self.assertEqual(avisos[0]["palabra"], "respondido")
        self.assertEqual(directores.drenar(self.banco.reg, ahora=T0 + 2)["avisos"], [])

    def test_el_cierre_apagado_y_el_cambio_de_palabra_se_envian_una_vez(self) -> None:
        ident = self._terminar()
        primero = directores.drenar(self.banco.reg, ahora=T0)["avisos"]
        self.assertEqual(len(primero), 1)
        self.assertEqual(primero[0]["palabra"], "falta limpiar")
        with mock.patch.object(politica, "ENTREGA_V4", True):
            self.assertEqual(
                directores.drenar(self.banco.reg, ahora=T0 + 0.5)["avisos"], [],
                "encender el interruptor no crea avisos si la palabra no cambió",
            )
        self._limpio(ident)
        segundo = directores.drenar(self.banco.reg, ahora=T0 + 1)["avisos"]
        cambios = [a for a in segundo if a.get("tipo") == "palabra-v4"]
        self.assertEqual(len(cambios), 1)
        self.assertEqual(cambios[0]["palabra"], "respondido")
        tercero = directores.drenar(self.banco.reg, ahora=T0 + 2)["avisos"]
        self.assertFalse([a for a in tercero if a.get("tipo") == "palabra-v4"
                          and a.get("novedad")])

    def test_un_fallo_reintentable_no_cierra_aunque_quede_un_limpio_anterior(self) -> None:
        ident = self._terminar()
        self._limpio(ident)
        self._fallo(ident, "fallo", 1)
        with mock.patch.object(politica, "ENTREGA_V4", True):
            self.assertEqual(directores.drenar(self.banco.reg, ahora=T0)["avisos"], [])
            self._fallo(ident, "agotado", 3)
            salida = directores.drenar(self.banco.reg, ahora=T0 + 1)["avisos"]
        self.assertEqual(len(salida), 1)
        self.assertEqual(salida[0]["palabra"], "falta limpiar")

    def test_detencion_nueva_se_avisa_y_repetida_no_sale_de_golpe(self) -> None:
        ident = self.banco.encargo("# Pausa\n\nPedido", "pausa", via="durable")
        self.banco.reg.anotar(
            ident, {"kind": "orden", "data": {"accion": "parar", "hora": HORA}},
            f"parar:{ident}",
        )
        self.banco.reg.anotar(
            ident, {"kind": "arbol", "data": {"observacion": "ausente", "hora": HORA}},
            f"arbol:{ident}",
        )
        self.assertEqual(self.banco.reg.fold(ident).estado, "detenido")
        primer = directores.drenar(self.banco.reg, ahora=T0)["avisos"]
        detenidos = [a for a in primer if a.get("tipo") == "detenido"]
        self.assertEqual(len(detenidos), 1)
        self.assertEqual(detenidos[0]["palabra"], "detenido")
        self.banco.reg.anotar(
            ident, {"kind": "orden", "data": {"accion": "continuar", "hora": HORA}},
            f"continuar:{ident}",
        )
        self.banco.reg.anotar(
            ident, {"kind": "orden", "data": {"accion": "parar", "hora": HORA}},
            f"parar-2:{ident}",
        )
        self.banco.reg.anotar(
            ident, {"kind": "arbol", "data": {"observacion": "ausente", "hora": HORA}},
            f"arbol-2:{ident}",
        )
        segundo = directores.drenar(self.banco.reg, ahora=T0 + 1)["avisos"]
        nuevos = [a for a in segundo if a.get("tipo") == "detenido"]
        self.assertEqual(len([a for a in nuevos if a["novedad"]]), 1)
        self.assertNotEqual(nuevos[0]["id"], detenidos[0]["id"])

    def test_detenido_anterior_al_corte_no_se_avisa_al_abrir(self) -> None:  # no-roja-en-base: el silencio histórico también existía antes del aviso nuevo
        ident = self.banco.encargo("# Antiguo\n\nPedido", "antiguo", via="durable")
        self.banco.reg.anotar(
            ident, {"kind": "orden", "data": {"accion": "parar", "hora": HORA}},
            f"parar:{ident}",
        )
        self.banco.reg.anotar(
            ident, {"kind": "arbol", "data": {"observacion": "ausente", "hora": HORA}},
            f"arbol:{ident}",
        )
        self.banco.reg.db.execute(
            "DELETE FROM cortes_avisos WHERE version = 'palabras-v4'"
        )
        self.banco.reg.db.commit()
        self.banco.reabrir()
        salida = directores.drenar(self.banco.reg, ahora=T0)["avisos"]
        self.assertFalse([a for a in salida if a.get("tipo") == "detenido"])

    def test_final_anterior_al_corte_conserva_el_cierre_viejo(self) -> None:  # no-roja-en-base: el cierre legado ya usaba este texto antes del corte v4
        ident = self._terminar()
        self.banco.reg.db.execute(
            "DELETE FROM cortes_avisos WHERE version = 'palabras-v4'"
        )
        self.banco.reg.db.commit()
        self.banco.reabrir()
        avisos = directores.drenar(self.banco.reg, ahora=T0)["avisos"]
        self.assertEqual([a["encargo"] for a in avisos], [ident])
        self.assertFalse(avisos[0].get("tipo") in ("cierre-v4", "palabra-v4"))
        self.assertNotIn("Falta limpiar", self.banco.cuerpos()[0]["cuerpo"])

    def test_pr_abierto_final_avisa_antes_de_limpio(self) -> None:
        from motor.encargos.entrega.avisos import trabajos_nuevos

        listo = replace(
            encargo(nivel="pr", limpio=""),
            prs=(PrDeFase("F", PrAbierto(7, "h", "main")),),
        )
        registro = mock.Mock()
        registro.fold.return_value = None
        vista = mock.Mock()
        vista.get.return_value = None
        vista.archivado.return_value = None
        propuestos = trabajos_nuevos(registro, listo, 0, vista, True)
        self.assertEqual(len(propuestos), 1)
        aviso, rep = propuestos[0]
        self.assertEqual(aviso["tipo"], "cierre-v4")
        self.assertEqual(aviso["palabra"], rep.palabra)
        self.assertEqual(rep.palabra, "listo para tu merge")

    def test_cierre_local_pendiente_no_se_descarta_al_configurar_transporte(self) -> None:  # no-roja-en-base: el cierre sin tipo ya se reenviaba al configurar el transporte
        ident = self._terminar()
        config = self.banco.base / "canal.json"
        configurado = config.read_text(encoding="utf-8")
        config.write_text(json.dumps({"tg": {}}), encoding="utf-8")
        primero = directores.drenar(self.banco.reg, ahora=T0)["avisos"]
        self.assertEqual([a["estado"] for a in primero], ["local-pendiente"])
        aviso = primero[0]["id"]
        directores.drenar(self.banco.reg, ahora=T0 + 1)
        self.assertEqual(self.banco.aviso_guardado(aviso)["estado"], "local-pendiente")
        config.write_text(configurado, encoding="utf-8")
        tercero = directores.drenar(self.banco.reg, ahora=T0 + 2)["avisos"]
        self.assertEqual([a["id"] for a in tercero], [aviso])
        self.assertEqual(tercero[0]["estado"], "publicado")
        self.assertEqual(self.banco.aviso_guardado(aviso)["encargo"], ident)
        self.assertEqual(len(self.banco.cuerpos()), 1)
        directores.drenar(self.banco.reg, ahora=T0 + 3)
        self.assertEqual(len(self.banco.cuerpos()), 1)

    def test_cambio_local_pendiente_no_se_descarta_al_configurar_transporte(self) -> None:
        ident = self._terminar()
        self.assertEqual(len(directores.drenar(self.banco.reg, ahora=T0)["avisos"]), 1)
        config = self.banco.base / "canal.json"
        configurado = config.read_text(encoding="utf-8")
        config.write_text(json.dumps({"tg": {}}), encoding="utf-8")
        self._limpio(ident)
        primero = directores.drenar(self.banco.reg, ahora=T0 + 1)["avisos"]
        self.assertEqual([a["tipo"] for a in primero], ["palabra-v4"])
        self.assertEqual(primero[0]["estado"], "local-pendiente")
        aviso = primero[0]["id"]
        directores.drenar(self.banco.reg, ahora=T0 + 2)
        self.assertEqual(self.banco.aviso_guardado(aviso)["estado"], "local-pendiente")
        config.write_text(configurado, encoding="utf-8")
        tercero = directores.drenar(self.banco.reg, ahora=T0 + 3)["avisos"]
        self.assertEqual([a["id"] for a in tercero], [aviso])
        self.assertEqual(tercero[0]["estado"], "publicado")
        directores.drenar(self.banco.reg, ahora=T0 + 4)
        self.assertEqual(len(self.banco.cuerpos()), 2)


if __name__ == "__main__":
    unittest.main()
