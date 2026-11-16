from __future__ import annotations

import unittest
import tempfile
import subprocess
import sys
from dataclasses import fields, replace
from pathlib import Path

from motor.encargos.dominio import Efecto, Encargo, Fase, Proc, reducir
from tools.plegado import campos_de


def _pedido() -> Encargo:
    return reducir(None, {"kind": "entrega", "data": {
        "id": "e", "clave": "e", "huella": "h", "texto": "pedido",
    }}, nuevo=False)


class SituacionGuardada(unittest.TestCase):
    def test_constructor_tipado_no_cambia_causas_ni_efectos_en_silencio(self) -> None:
        from motor.encargos.situacion import (
            Conciliando, Deteniendo, Detenido, EstadoIncoherente,
            FalloInterno, PorOrden, situacion_de,
        )

        base = dict(id="e", clave="e", huella="h", director="", texto="pedido",
                    adjuntos=(), restricciones=(), canal="")
        detenido = Encargo(**base, situacion=Detenido(PorOrden("parar")))
        self.assertEqual(detenido.orden_parada, "parar")
        self.assertEqual(situacion_de(detenido).causa, PorOrden("parar"))
        reabierto = Encargo(**base, reaperturas=1,
                           situacion=Detenido(PorOrden("reabrir")))
        self.assertEqual(situacion_de(reabierto).causa, PorOrden("reabrir"))
        fallo_vacio = FalloInterno("", "", "")
        detenido_por_fallo = Encargo(**base, situacion=Detenido(fallo_vacio))
        deteniendo_por_fallo = Encargo(**base,
                                      situacion=Deteniendo(fallo_vacio, False))
        self.assertEqual(detenido_por_fallo.orden_parada, "fallo_interno")
        self.assertEqual(situacion_de(detenido_por_fallo).causa, fallo_vacio)
        self.assertEqual(deteniendo_por_fallo.orden_parada, "fallo_interno")
        self.assertEqual(situacion_de(deteniendo_por_fallo).orden, fallo_vacio)

        with self.assertRaises(EstadoIncoherente):
            Encargo(**base, situacion=Detenido(PorOrden("reabrir")))
        with self.assertRaises(EstadoIncoherente):
            Encargo(**base, reaperturas=1, situacion=Detenido(PorOrden("importar")))
        fallo = FalloInterno("kind", "ukey", "ajena")
        for situacion in (Detenido(fallo), Deteniendo(fallo, False),
                          Conciliando("ef")):
            with self.subTest(situacion=situacion), self.assertRaises(EstadoIncoherente):
                Encargo(**base, situacion=situacion)

        efecto = Efecto("ef", "destino", "operacion", "clave", "incierto")
        conciliando = Encargo(**base, efectos=(efecto,),
                             situacion=Conciliando("ef"))
        self.assertEqual(situacion_de(conciliando).efecto, "ef")

    def test_migrador_ignora_contexto_y_detecta_encargo(self) -> None:
        from tools.migrar_usos_situacion import _migrar

        with tempfile.TemporaryDirectory() as temporal:
            ruta = Path(temporal) / "usos.py"
            ruta.write_text("replace(contexto, intento='I')\n", encoding="utf-8")
            self.assertEqual(_migrar(ruta, comprobar=True), 0)
            ruta.write_text("replace(e, intento='I')\n", encoding="utf-8")
            with self.assertRaisesRegex(RuntimeError, "faltan 1 escrituras"):
                _migrar(ruta, comprobar=True)
            for texto in ("replace(detenido, estado='detenido')\n",
                          "replace(reg.fold(ident), estado='detenido')\n"):
                with self.subTest(texto=texto):
                    ruta.write_text(texto, encoding="utf-8")
                    with self.assertRaisesRegex(RuntimeError, "faltan 1 escrituras"):
                        _migrar(ruta, comprobar=True)
            for texto in (
                "import dataclasses\ndataclasses.replace(e, estado='detenido')\n",
                "import dataclasses as dc\ndc.replace(e, estado='detenido')\n",
                "from dataclasses import replace as cambiar\n"
                "cambiar(e, estado='detenido')\n",
            ):
                with self.subTest(texto=texto):
                    ruta.write_text(texto, encoding="utf-8")
                    with self.assertRaisesRegex(RuntimeError, "faltan 1 escrituras"):
                        _migrar(ruta, comprobar=True)
                    self.assertEqual(_migrar(ruta), 1)
                    self.assertIn("evolucionar(e, estado='detenido')",
                                  ruta.read_text(encoding="utf-8"))

    def test_migrador_vigila_escrituras_planas_en_la_suite(self) -> None:
        raiz = Path(__file__).resolve().parents[1]
        proceso = subprocess.run(
            [sys.executable, str(raiz / "tools/migrar_usos_situacion.py"), "--check"],
            cwd=raiz, text=True, capture_output=True, check=False,
        )
        self.assertEqual(proceso.returncode, 0, proceso.stdout + proceso.stderr)

    def test_migrador_recorre_todas_las_rutas_que_escriben_encargos(self) -> None:
        from tools.migrar_usos_situacion import _rutas

        with tempfile.TemporaryDirectory() as temporal:
            raiz = Path(temporal)
            for carpeta in ("motor/encargos", "motor/integraciones", "ops", "tools", "tests"):
                ruta = raiz / carpeta
                ruta.mkdir(parents=True)
                (ruta / "uso.py").write_text("replace(e, estado='detenido')\n",
                                            encoding="utf-8")
            self.assertEqual(
                {ruta.parent.relative_to(raiz).as_posix() for ruta in _rutas(raiz)},
                {"motor/encargos", "motor/integraciones", "ops", "tools", "tests"},
            )

    def test_constructor_tipado_conserva_escritura_espera_y_detencion(self) -> None:
        from motor.encargos.situacion import (
            Cancelar, Deteniendo, Ejecutando, EsperandoReintento, situacion_de,
        )

        def directo(situacion) -> Encargo:
            return Encargo(
                id="e", clave="e", huella="h", director="", texto="pedido",
                adjuntos=(), restricciones=(), canal="", situacion=situacion,
                ultimo_hecho="hecho",
            )

        proc = Proc(123, "inicio", "boot")
        activo = directo(Ejecutando("F", "I", proc, True))
        self.assertEqual((activo.fase_activa, activo.intento, activo.proc, activo.reserva),
                         ("F", "I", proc, True))
        self.assertEqual(situacion_de(activo).fase, "F")
        self.assertEqual(situacion_de(replace(activo, fase_activa="G")).fase, "G")

        espera = directo(EsperandoReintento("red", "mañana"))
        self.assertEqual((espera.motivo, espera.reanuda_en), ("red", "mañana"))
        self.assertEqual(situacion_de(espera).motivo, "red")

        detiene = directo(Deteniendo(Cancelar(), True))
        self.assertEqual((detiene.orden_parada, detiene.intento_interrumpido),
                         ("cancelar", True))
        self.assertEqual(situacion_de(detiene).orden.accion, "cancelar")

    def test_constructor_tipado_conserva_cierre_forzado_y_rechaza_conflictos(self) -> None:
        from motor.encargos.situacion import (
            CierreForzado, Contexto, Ejecutando, EstadoIncoherente,
            Parcial, Terminado, situacion_de,
        )

        fase = Fase("F", "resultado")
        base = dict(id="e", clave="e", huella="h", director="", texto="pedido",
                    adjuntos=(), restricciones=(), canal="")
        cerrado = Encargo(**base, fases=(fase,), evidencia=("probado",),
                          situacion=Terminado(Parcial(("F",)), CierreForzado("F", "h")))
        self.assertEqual((cerrado.cierre_forzado_fase, cerrado.cierre_forzado_huella),
                         (fase, "h"))
        self.assertEqual(situacion_de(cerrado).forzado, CierreForzado("F", "h"))

        with self.assertRaises(EstadoIncoherente):
            Encargo(**base, situacion=Ejecutando(
                "F", "I", None, False, contexto=Contexto(intento="otro"),
            ))

    def test_estado_tiene_un_solo_dueno_y_la_proyeccion_legada_no_lo_pierde(self) -> None:
        from motor.encargos.dominio import CAMPOS_LEGADOS
        from motor.encargos.situacion import Recibido

        plano = _pedido()
        self.assertIsInstance(plano.situacion, Recibido)
        nombres = {campo.name for campo in fields(plano)}
        self.assertIn("situacion", nombres)
        self.assertEqual(set(CAMPOS_LEGADOS), {
            "estado", "orden_parada", "reanuda_en", "intento", "proc", "reserva",
            "intento_interrumpido", "cierre_forzado_fase", "cierre_forzado_huella",
        })
        self.assertTrue(set(CAMPOS_LEGADOS).isdisjoint(nombres))
        self.assertTrue({"escrituras", "intentos_nuevos", "evidencia"} <= nombres)
        self.assertEqual(campos_de(plano)["estado"], "recibido")
        self.assertNotIn("situacion", campos_de(plano))

    def test_recibido_conserva_contexto_de_intento_y_cierre_historico(self) -> None:
        from motor.encargos.encargo import evolucionar
        from motor.encargos.situacion import Recibido

        fase = Fase("F", "resultado")
        proc = Proc(1, "inicio", "boot")
        encargo = evolucionar(
            _pedido(), intento="i", proc=proc, reserva=True,
            reanuda_en="después", cierre_forzado_fase=fase,
            cierre_forzado_huella="huella",
        )
        self.assertIsInstance(encargo.situacion, Recibido)
        self.assertEqual(
            (encargo.intento, encargo.proc, encargo.reserva, encargo.reanuda_en,
             encargo.cierre_forzado_fase, encargo.cierre_forzado_huella),
            ("i", proc, True, "después", fase, "huella"),
        )
        legado = campos_de(encargo)
        self.assertNotIn("situacion", legado)
        self.assertEqual(legado["estado"], "recibido")
        self.assertEqual(legado["cierre_forzado_huella"], "huella")
        self.assertEqual(legado["proc"]["pid"], 1)


if __name__ == "__main__":
    unittest.main()
