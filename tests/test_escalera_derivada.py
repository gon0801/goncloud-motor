"""La escalera de cada rol sale de los ids y del registro, no de contadores.

Estas pruebas fallan con el codigo de 874daed: alli la escalera era un conjunto
de campos guardados que cada camino reiniciaba a mano.
"""

from __future__ import annotations

import dataclasses
import tempfile
import unittest
from pathlib import Path

from motor.encargos import ciclo, dominio
from motor.encargos.ciclo import ahora_iso, recibir
from motor.encargos.dominio import relevos_de
from motor.encargos.registro import Registro
from tests.test_escalera_ids import Recorrido, _bloqueante


class LaEscaleraNoEsUnConjuntoDeCamposGuardados(unittest.TestCase):
    def test_ningun_contador_ni_huella_vive_fuera_del_registro(self):
        nombres = {f.name for f in dataclasses.fields(dominio.Encargo)}
        for nombre in (
            "relevos",
            "relevo_huella",
            "sin_progreso",
            "bloqueo_huella",
            "relevos_por_rol",
            "relevo_huella_por_rol",
        ):
            self.assertNotIn(nombre, nombres, nombre)

    def test_las_dos_listas_de_reinicio_manual_ya_no_existen(self):
        self.assertFalse(hasattr(dominio, "_limpiar_para_continuar"))
        self.assertFalse(hasattr(dominio, "_reiniciar_escalera"))


class ContinuarNoReiniciaLaEscalera(unittest.TestCase):
    def _tras_el_diagnostico(self, tmp: str) -> Recorrido:
        r = Recorrido(tmp, "escalera-continuar", "malo.txt con B")
        self.addCleanup(r.reg.close)
        (r.espacio / "malo.txt").write_text("X")
        r.incorporar()
        r.juicio(
            {
                "veredicto": "cambios",
                "evidencia": "lei malo.txt",
                "bloqueantes": [_bloqueante("malo.txt no contiene B")],
            }
        )
        r.incorporar()
        e = r.incorporar()
        self.assertEqual(e.estrategia, "diagnosticar_bloqueo")
        self.assertEqual(relevos_de(e, "responsable"), 0)
        return r

    def _detener(self, r: Recorrido) -> None:
        hora = ciclo.ahora_iso()
        r.reg.anotar(
            r.enc, {"kind": "orden", "data": {"accion": "parar", "hora": hora}},
            "parar-escalera",
        )
        r.reg.anotar(
            r.enc,
            {"kind": "arbol", "data": {"observacion": "ausente", "hora": hora}},
            "arbol-escalera",
        )
        self.assertEqual(r.fold().estado, "detenido")

    def test_continuar_conserva_el_diagnostico_y_la_siguiente_vuelta_releva(self):
        with tempfile.TemporaryDirectory() as tmp:
            r = self._tras_el_diagnostico(tmp)
            self._detener(r)

            ciclo.ordenar(r.reg, r.enc, "continuar", "continuar-escalera", ciclo.ahora_iso())
            continuado = r.fold()
            self.assertEqual(continuado.estrategia, "diagnosticar_bloqueo")
            self.assertEqual(relevos_de(continuado, "responsable"), 0)

            e = r.incorporar()
            self.assertEqual(relevos_de(e, "responsable"), 1)
            self.assertEqual(e.pendientes_entrega, ())


class ElCorteDeInstrumentoConservaLaCausa(unittest.TestCase):
    """F2 (DeepSeek): dos causas de instrumento distintas no comparten escalón.

    El corte anota su propia causa en la huella (`instrumento:<clave>`). Si la
    escalera la ignorara y usara sólo el estado del producto, la segunda causa
    vería el relevo de la primera y entregaría con pendientes sin relevar.
    """

    def test_dos_causas_distintas_no_comparten_el_escalon(self):  # no-roja-en-base: la base ya distingue las causas por la huella del corte; la prueba conserva ese comportamiento tras derivar la escalera
        with tempfile.TemporaryDirectory() as tmp:
            reg = Registro(Path(tmp) / "base")
            self.addCleanup(reg.close)
            ident = recibir(reg, "pedido", "instrumento-f2", ahora_iso())["encargo"]
            for causa in ("A", "B"):
                reg.anotar(
                    ident,
                    {
                        "kind": "sin_progreso",
                        "data": {
                            "huella": f"instrumento:{causa}",
                            "diagnostico": f"el instrumento falló por {causa}",
                            "escalar": True,
                            "rol": "responsable",
                            "hora": ahora_iso(),
                        },
                    },
                    f"corte-instrumento-{causa}",
                )
            e = reg.fold(ident)
            self.assertEqual(e.pendientes_entrega, ())
            self.assertNotEqual(e.estado, "terminado")


class ElRelevoDeInstrumentoRotaElModelo(unittest.TestCase):
    """F5 (DeepSeek): el corte de instrumento cuenta como relevo del rol.

    `cli` decide rotar el modelo con `relevos_de(enc, rol) > 0`. El corte de
    instrumento es un relevo de esa época; si no cuenta, el rol sigue con el
    mismo modelo que ya falló.
    """

    def test_el_corte_de_instrumento_cuenta_como_relevo_del_rol(self):  # no-roja-en-base: la base ya cuenta el corte como relevo y rota el modelo; la prueba conserva esa decision al derivar la escalera
        with tempfile.TemporaryDirectory() as tmp:
            reg = Registro(Path(tmp) / "base")
            self.addCleanup(reg.close)
            ident = recibir(reg, "pedido", "instrumento-f5", ahora_iso())["encargo"]
            reg.anotar(
                ident,
                {
                    "kind": "sin_progreso",
                    "data": {
                        "huella": "instrumento:abc123",
                        "diagnostico": "el comprobador falló dos veces igual",
                        "escalar": True,
                        "rol": "responsable",
                        "hora": ahora_iso(),
                    },
                },
                "corte-instrumento-unico",
            )
            e = reg.fold(ident)
            self.assertEqual(relevos_de(e, "responsable"), 1)


if __name__ == "__main__":
    unittest.main()
