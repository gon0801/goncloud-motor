"""El nivel alcanzado se deriva de los hechos del registro.

Una tabla de una prueba por fila del contrato de `nivel_de`. Las pruebas de
defecto fallan en la aserción sobre el nivel guardado de la base; las de
comportamiento que ya existía se declaran caracterizaciones.
"""

from __future__ import annotations

import dataclasses
import tempfile
import unittest
from pathlib import Path

from motor.encargos.ciclo import ahora_iso
from motor.encargos.dominio import Fase, nivel_cumplido
from motor.encargos.registro import Registro

SHA = "a" * 40
SHA_NUEVO = "b" * 40


def _bloqueante(hid: str = "F1") -> dict:
    return {
        "id": hid,
        "criterio": "comportamiento",
        "titulo": "fuga de token en el log",
        "reproduccion": "cat log.txt -> token",
        "ancla": "log.txt:1",
        "origen": "deepseek",
        "gravedad": "bloqueante",
        "estado": "abierto",
    }


class _Nivel(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.reg = Registro(Path(tmp.name) / "base")
        self.addCleanup(self.reg.close)
        self.id = "nivel-" + self._testMethodName
        self.hora = ahora_iso()
        self.serie = 0

    def anotar(self, kind: str, **data):
        self.serie += 1
        resultado = self.reg.anotar(
            self.id,
            {"kind": kind, "data": {**data, "hora": self.hora}},
            f"nivel:{self.serie}",
        )
        self.assertNotEqual(resultado, "ilegal", (kind, data, "ilegal"))
        return self.reg.fold(self.id)

    def encargo(self, nivel: str = "pr"):
        self.anotar(
            "entrega",
            id=self.id,
            clave=self.id,
            huella="pedido",
            texto="entregar el producto",
            entrega_nivel=nivel,
            repo="ejemplo/app",
            rama="cambio",
            base="main",
        )
        self.anotar("producto_listo", version="version-1", manifiesto=[])
        return self.reg.fold(self.id)

    def pr_abierto(self, sha: str = SHA):
        if self.reg.fold(self.id) is None:
            self.encargo()
        self.anotar("entrega_externa", paso="commit", sha=sha, numero=7)
        self.anotar("entrega_externa", paso="push", sha=sha, numero=7)
        self.anotar("entrega_externa", paso="pr_abierto", sha=sha, numero=7)
        return self.anotar("entrega_externa", paso="pr_listo", sha=sha, numero=7)

    def deepseek(self, sha: str = SHA, hallazgos=(), huella: str = "h"):
        self.anotar(
            "revision",
            origen="deepseek",
            veredicto="cambios" if hallazgos else "aprobado",
            version="version-1",
            revision_id="rev-ds",
            hallazgos=list(hallazgos),
        )
        return self.anotar(
            "entrega_externa",
            paso="deepseek_leido",
            sha=sha,
            numero=7,
            huella=huella,
        )


class SinPr(_Nivel):
    def test_sin_pr_una_fase_preparada_alcanza_terminado(self):  # no-roja-en-base: caracterización de una fase cerrada sin efectos externos
        self.encargo("terminado")
        e = self.anotar("comprobacion", clase="confirmado", evidencia="hecho")
        self.assertEqual(e.nivel_alcanzado, "terminado")


class ConPr(_Nivel):
    def test_un_pr_sin_lectura_deepseek_queda_en_pr_abierto(self):  # no-roja-en-base: caracterización de un PR recién abierto
        e = self.pr_abierto()
        self.assertEqual(e.nivel_alcanzado, "pr_abierto")

    def test_la_lectura_del_sha_vigente_sin_bloqueantes_alcanza_pr(self):  # no-roja-en-base: caracterización de la lectura de DeepSeek
        self.pr_abierto()
        e = self.deepseek()
        self.assertEqual(e.nivel_alcanzado, "pr")

    def test_deepseek_sin_revision_alcanza_pr(self):  # no-roja-en-base: caracterización de la omisión tras el tope
        self.pr_abierto()
        e = self.anotar(
            "entrega_externa",
            paso="deepseek_sin_revision",
            sha=SHA,
            numero=7,
            huella="sin-revision",
        )
        self.assertEqual(e.nivel_alcanzado, "pr")

    def test_un_bloqueante_deepseek_abierto_impide_pr(self):  # no-roja-en-base: caracterización de que DeepSeek bloquea con Critical, High o Medium
        self.pr_abierto()
        e = self.deepseek(hallazgos=[_bloqueante()])
        self.assertEqual(e.nivel_alcanzado, "pr_abierto")

    def test_pr_con_pendientes_alcanza_pr(self):  # no-roja-en-base: caracterización de la entrega con pendientes
        self.pr_abierto()
        e = self.anotar(
            "entrega_externa",
            paso="pr_con_pendientes",
            sha=SHA,
            numero=7,
            recibo="7",
        )
        self.assertEqual(e.nivel_alcanzado, "pr")

    def test_sin_cambios_solo_cumple_niveles_sin_efectos_externos(self):  # no-roja-en-base: caracterización de una fase sin cambios publicables
        self.encargo("terminado")
        e = self.anotar(
            "entrega_externa",
            paso="sin_cambios",
            sha=SHA,
            evidencia="la rama no aporta cambios",
        )
        self.assertEqual(e.nivel_alcanzado, "sin_cambios")

    def test_un_ci_rojo_del_sha_vigente_vuelve_a_pr_abierto(self):
        self.pr_abierto()
        self.deepseek()
        e = self.anotar(
            "ci_pr",
            fase="fase_unica",
            sha=SHA,
            estado="rojo",
            fallas=["gate"],
            clase="codigo",
            reabrir=True,
        )
        self.assertEqual(e.nivel_alcanzado, "pr_abierto")

    def test_merge_alcanza_integrado_y_despliegue_alcanza_desplegado(self):  # no-roja-en-base: caracterización del merge y del despliegue
        self.encargo("desplegado")
        self.pr_abierto()
        integrado = self.anotar(
            "entrega_externa", paso="integrado", sha=SHA, recibo=SHA
        )
        self.assertEqual(integrado.nivel_alcanzado, "integrado")
        desplegado = self.anotar(
            "entrega_externa", paso="desplegado", sha=SHA, recibo="recibo"
        )
        self.assertEqual(desplegado.nivel_alcanzado, "desplegado")

    def test_una_fase_sin_despliegue_cumple_integrado(self):  # no-roja-en-base: caracterización de la fase que no requiere despliegue
        self.encargo("desplegado")
        self.pr_abierto()
        self.anotar(
            "politica_despliegue",
            fuente="plans/producto.md",
            huella="h",
            requiere_despliegue=False,
        )
        integrado = self.anotar(
            "entrega_externa", paso="integrado", sha=SHA, recibo=SHA
        )
        self.assertTrue(nivel_cumplido(integrado))
        self.assertEqual(integrado.nivel_alcanzado, "integrado")


class Defectos(_Nivel):
    def test_el_cambio_de_head_baja_a_pr_abierto(self):
        self.pr_abierto()
        e = self.deepseek()
        self.assertEqual(e.nivel_alcanzado, "pr")

        e = self.anotar("entrega_externa", paso="commit", sha=SHA_NUEVO, numero=7)

        self.assertEqual(e.nivel_alcanzado, "pr_abierto")

    def test_cerrar_el_ultimo_bloqueante_devuelve_el_nivel_a_pr_sin_otro_evento(
        self,
    ):
        self.pr_abierto()
        self.deepseek(hallazgos=[_bloqueante()])
        e = self.anotar(
            "revision",
            origen="independiente",
            veredicto="aprobado",
            version="version-1",
            revision_id="rev-ind",
            hallazgos=[{**_bloqueante(), "estado": "resuelto", "origen": "deepseek"}],
        )

        self.assertEqual(e.nivel_alcanzado, "pr")

    def test_el_nivel_no_es_un_campo_guardado_de_la_fase(self):
        nombres = {f.name for f in dataclasses.fields(Fase)}
        self.assertNotIn("nivel_alcanzado", nombres)
        self.assertIn("ultimo_paso_entrega", nombres)


if __name__ == "__main__":
    unittest.main()
