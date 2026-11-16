"""La palabra publicada sale del estado plegado, nunca del nivel de una sola fase."""

from __future__ import annotations

import unittest
import contextlib
import io
import json
import tempfile
from dataclasses import replace

from motor.encargos import cli
from motor.encargos.dominio import Encargo, EntregaAutorizada, Fase
from motor.encargos.entrega.tipos import PrAbierto, PrDeFase, PrMergeado
from motor.encargos.situacion import (
    Cancelado, Completo, Detenido, Parcial, PorOrden, PorOtraBase, Terminado,
)
from tests.test_aviso_cierre import Banco


def encargo(nivel: str = "desplegado", fases: tuple[Fase, ...] | None = None,
            situacion=None, limpio: str = "t") -> Encargo:
    if fases is None:
        fases = (Fase("F", "resultado", estado="preparada", evidencia="comprobado"),)
    if situacion is None:
        faltantes = tuple(f.id for f in fases if f.estado != "preparada")
        situacion = Terminado(Parcial(faltantes) if faltantes else Completo(), None)
    return Encargo(
        id="x", clave="pedido", huella="h", director="d", texto="pedido",
        adjuntos=(), restricciones=(), canal="tg", situacion=situacion,
        entrega=EntregaAutorizada(nivel, "o/r", "encargo/x", "main"),
        fases=fases, limpio=limpio,
    )


def integrado(enc: Encargo, *, base: str = "main",
              desplegado: bool = False) -> Encargo:
    from motor.encargos.despliegue import clave_de_recibo

    fase = replace(enc.fases[0], integrado_sha="m")
    if desplegado:
        fase = replace(
            fase, desplegado_recibo=(
                f"despliegues/{clave_de_recibo(enc, fase)}/recibo.json"
            ),
        )
    return replace(
        enc, fases=(fase, *enc.fases[1:]),
        prs=(PrDeFase("F", PrMergeado(7, "h", base, "m")),),
    )


class PalabrasPuras(unittest.TestCase):
    def test_una_fila_por_palabra_con_interruptor_encendido_y_apagado(self) -> None:
        from motor.encargos.reporte import reporte

        listo = replace(encargo(nivel="pr", limpio=""), prs=(
            PrDeFase("F", PrAbierto(7, "h", "main")),
        ))
        parcial = encargo(fases=(Fase(
            "F", "resultado", estado="bloqueada",
            evidencia="fallo", bloqueo_causa="falta evidencia",
        ),))
        sin_cambios = encargo(fases=(Fase(
            "F", "resultado", estado="preparada", evidencia="comprobado",
            ultimo_paso_entrega="sin_cambios",
        ),))
        casos = (
            (encargo(situacion=Detenido(PorOrden("parar")), limpio=""), "detenido"),
            (listo, "listo para tu merge"),
            (encargo(limpio=""), "falta limpiar"),
            (encargo(situacion=Cancelado()), "cancelado"),
            (parcial, "entrega parcial"),
            (integrado(encargo(), desplegado=True), "entregado"),
            (integrado(encargo()), "integrado"),
            (encargo(nivel="terminado"), "respondido"),
            (sin_cambios, "sin cambios"),
        )
        for activo in (False, True):
            for enc, palabra in casos:
                with self.subTest(activo=activo, palabra=palabra):
                    rep = reporte(enc, activo)
                    self.assertIsNotNone(rep)
                    self.assertEqual(rep.palabra, palabra)
                    self.assertTrue(rep.causa)
                    self.assertEqual(len(rep.fases), len(enc.fases))
                    self.assertTrue(all(f.id and f.palabra and f.causa for f in rep.fases))
        self.assertIn("no está encendida", reporte(encargo(limpio=""), False).causa)
        self.assertNotIn("no está encendida", reporte(encargo(limpio=""), True).causa)

    def test_precedencia_en_los_empates(self) -> None:
        from motor.encargos.reporte import reporte

        abierto = PrDeFase("F", PrAbierto(7, "h", "main"))
        fallo = (("rama", "encargo/x", "fallo", 1, "t"),)
        parcial = encargo(fases=(Fase("F", "r", estado="bloqueada",
                                     evidencia="x", bloqueo_causa="sin avance"),))
        casos = (
            (replace(encargo(situacion=Detenido(PorOrden("parar")), limpio=""),
                     prs=(abierto,), limpieza=fallo), "detenido"),
            (replace(encargo(nivel="pr", limpio=""), prs=(abierto,),
                     limpieza=fallo), "listo para tu merge"),
            (replace(encargo(situacion=Cancelado()), limpieza=fallo), "falta limpiar"),
            (replace(parcial, limpio=""), "falta limpiar"),
            (replace(parcial, situacion=Cancelado()), "cancelado"),
            (replace(parcial, fases=(replace(parcial.fases[0], integrado_sha="m"),)),
             "entrega parcial"),
            (integrado(encargo(nivel="terminado")), "integrado"),
            (encargo(nivel="terminado", fases=(Fase(
                "F", "r", estado="preparada", evidencia="comprobado",
                ultimo_paso_entrega="sin_cambios",
            ),)), "sin cambios"),
        )
        for activo in (False, True):
            for enc, palabra in casos:
                with self.subTest(activo=activo, palabra=palabra):
                    self.assertEqual(reporte(enc, activo).palabra, palabra)

    def test_pr_abierto_no_oculta_otra_fase_bloqueada(self) -> None:
        from motor.encargos.reporte import reporte

        lista = Fase("F", "r", estado="preparada", evidencia="comprobado", pr_numero=7)
        bloqueada = Fase("G", "r", estado="bloqueada", evidencia="fallo",
                         bloqueo_causa="falta acceso")
        enc = replace(encargo(nivel="pr", fases=(lista, bloqueada)),
                      prs=(PrDeFase("F", PrAbierto(7, "h", "main")),))
        rep = reporte(enc, True)
        self.assertEqual(rep.palabra, "entrega parcial")
        self.assertIn("G", rep.causa)
        self.assertIn("falta acceso", rep.causa)

    def test_un_registro_viejo_integrado_no_se_reporta_limpio(self) -> None:
        from motor.encargos.reporte import reporte

        antiguo = integrado(encargo(limpio=""), desplegado=True)
        antiguo = replace(antiguo, prs=())
        self.assertEqual(reporte(antiguo, False).palabra, "falta limpiar")

    def test_fallo_de_limpieza_nombra_el_recurso_aunque_haya_limpio(self) -> None:
        from motor.encargos.reporte import reporte

        fallo = replace(
            encargo(), limpieza=(("rama", "encargo/x", "fallo", 1, "t"),),
        )
        rep = reporte(fallo, True)
        self.assertEqual(rep.palabra, "falta limpiar")
        self.assertIn("rama", rep.causa)
        agotado = replace(
            fallo, limpieza=(("rama", "encargo/x", "agotado", 3, "t"),),
        )
        self.assertIn("rama", reporte(agotado, True).causa)

    def test_resultado_con_pr_no_se_declara_respondido(self) -> None:
        from motor.encargos.reporte import reporte

        con_pr = replace(
            encargo(nivel="terminado"),
            prs=(PrDeFase("F", PrAbierto(7, "h", "main")),),
        )
        self.assertEqual(reporte(con_pr, True).palabra, "entrega parcial")

    def test_integrado_no_exige_despliegue_que_no_se_pidio(self) -> None:
        from motor.encargos.reporte import reporte

        rep = reporte(integrado(encargo(nivel="integrado")), True)
        self.assertEqual(rep.palabra, "integrado")
        self.assertIn("no se pidió desplegar", rep.causa)
        limitado = replace(
            integrado(encargo()), nivel_efectivo="integrado",
            nivel_efectivo_motivo="la máquina no puede desplegar",
        )
        causa = reporte(limitado, True).causa
        self.assertIn("no se pudo desplegar", causa)
        self.assertIn("la máquina", causa)

    def test_una_fase_sin_cambios_no_oculta_otra_integrada_o_desplegada(self) -> None:
        from motor.encargos.despliegue import clave_de_recibo
        from motor.encargos.reporte import reporte

        f = Fase("F", "cambio", estado="preparada", evidencia="comprobado",
                 integrado_sha="m")
        g = Fase("G", "sin cambio", estado="preparada", evidencia="comprobado",
                 ultimo_paso_entrega="sin_cambios")
        pr = PrDeFase("F", PrMergeado(7, "h", "main", "m"))
        for desplegado in (False, True):
            base = encargo(fases=(f, g))
            fase_f = f
            if desplegado:
                fase_f = replace(
                    f, desplegado_recibo=f"despliegues/{clave_de_recibo(base, f)}/recibo.json",
                )
            for fases in ((fase_f, g), (g, fase_f)):
                with self.subTest(desplegado=desplegado, primero=fases[0].id):
                    actual = replace(base, fases=fases, prs=(pr,))
                    rep = reporte(actual, True)
                    self.assertEqual(
                        rep.palabra, "entregado" if desplegado else "integrado",
                    )
                    self.assertEqual(
                        {a.id: a.palabra for a in rep.fases},
                        {"F": "entregado" if desplegado else "integrado",
                         "G": "sin cambios"},
                    )

    def test_reintegracion_con_recibo_anterior_no_acredita_despliegue(self) -> None:
        from motor.encargos.despliegue import clave_de_recibo
        from motor.encargos.reporte import reporte

        desplegado = integrado(encargo(), desplegado=True)
        nuevo = replace(desplegado.fases[0], integrado_sha="n")
        anterior = reporte(replace(desplegado, fases=(nuevo,)), True)
        self.assertEqual(anterior.palabra, "integrado")
        self.assertIn("versión anterior", anterior.causa)
        vigente = replace(
            nuevo, desplegado_recibo=(
                f"despliegues/{clave_de_recibo(desplegado, nuevo)}/recibo.json"
            ),
        )
        self.assertEqual(
            reporte(replace(desplegado, fases=(vigente,)), True).palabra,
            "entregado",
        )

    def test_merge_en_otra_base_no_da_entregado_sin_aceptacion(self) -> None:
        from motor.encargos.reporte import reporte

        otra = integrado(encargo(), base="otra", desplegado=True)
        self.assertEqual(reporte(otra, True).palabra, "integrado")
        self.assertIn("otra", reporte(otra, True).causa)
        aceptada = replace(
            otra, prs=(replace(otra.prs[0], aceptada_en="otra"),),
        )
        rep = reporte(aceptada, True)
        self.assertEqual(rep.palabra, "entregado")
        self.assertIn("otra", rep.causa)
        self.assertIn("otra", rep.fases[0].causa)

    def test_detencion_por_otra_base_nombra_la_causa_y_continuar(self) -> None:
        from motor.encargos.reporte import reporte

        detenido = replace(
            encargo(situacion=Detenido(PorOrden("parar")), limpio=""),
            situacion=Detenido(PorOtraBase("F", "otra")),
            otra_base_de=("F", "otra"),
        )
        rep = reporte(detenido, True)
        self.assertEqual(rep.palabra, "detenido")
        self.assertIn("F", rep.causa)
        self.assertIn("otra", rep.causa)
        self.assertIn("continuar", rep.causa)

    def test_pr_apilado_no_cuenta_hasta_que_su_base_este_integrada(self) -> None:
        from motor.encargos.reporte import reporte

        fases = (
            Fase("F", "r", estado="preparada", evidencia="comprobado",
                 integrado_sha="m1"),
            Fase("G", "g", estado="preparada", evidencia="comprobado",
                 integrado_sha="m2"),
        )
        enc = encargo(
            nivel="integrado", fases=fases,
        )
        pila = (
            PrDeFase("F", PrMergeado(7, "h1", "encargo/x-G", "m1")),
            PrDeFase("G", PrAbierto(8, "h2", "main")),
        )
        enc = replace(enc, prs=pila)
        self.assertEqual(reporte(enc, True).palabra, "entrega parcial")
        mergeada = replace(
            enc, prs=(pila[0], PrDeFase("G", PrMergeado(8, "h2", "main", "m2"))),
        )
        self.assertEqual(reporte(mergeada, True).palabra, "integrado")

    def test_motor_ver_incluye_el_reporte_sin_cambiar_la_vista(self) -> None:
        from motor.encargos.dominio import vista

        with tempfile.TemporaryDirectory() as tmp:
            banco = Banco(tmp)
            try:
                ident = banco.encargo("# Pedido\n\nResulta", "pedido")
                antes = vista(banco.reg.fold(ident))
                salida = io.StringIO()
                with contextlib.redirect_stdout(salida):
                    self.assertEqual(
                        cli.main(["ver", ident, "--base", str(banco.base)]), 0
                    )
                datos = json.loads(salida.getvalue())
                self.assertIn("reporte", datos)
                self.assertIsNone(datos["reporte"])
                self.assertEqual(vista(banco.reg.fold(ident)), antes)
                banco.terminar(ident)
                antes = vista(banco.reg.fold(ident))
                salida = io.StringIO()
                with contextlib.redirect_stdout(salida):
                    self.assertEqual(
                        cli.main(["ver", ident, "--base", str(banco.base)]), 0
                    )
                rep = json.loads(salida.getvalue())["reporte"]
                self.assertEqual(rep["palabra"], "falta limpiar")
                self.assertIn("fases", rep)
                self.assertEqual(vista(banco.reg.fold(ident)), antes)
            finally:
                banco.cerrar()


if __name__ == "__main__":
    unittest.main()
