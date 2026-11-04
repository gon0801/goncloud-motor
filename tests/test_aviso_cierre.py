"""El aviso de cierre se lee en lenguaje llano y un aviso viejo no sale de golpe."""

import hashlib
import json
import os
import re
import sys
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from motor.encargos.registro import Registro
from motor.integraciones import directores

HORA = "2026-11-01T10:00:00Z"
T0 = 1_000_000.0

TRANSPORTE = (
    "import os,sys,json\n"
    "from pathlib import Path\n"
    "base=Path(os.environ['MOTOR_AVISO_BASE'])\n"
    "ident=os.environ['MOTOR_AVISO_ID']\n"
    "cuerpo=sys.stdin.read()\n"
    "with open(base/'cuerpos.log','a') as f: "
    "f.write(json.dumps({'id':ident,'cuerpo':cuerpo})+'\\n')\n"
    "if (base/'caido').exists():\n"
    " sys.stderr.write('el canal está caído'); sys.exit(1)\n"
    "with open(base/'entregados.log','a') as f: f.write(ident+'\\n')\n"
    "print(json.dumps({'recibo':'ok-'+ident}))\n"
)

CONCILIACION = (
    "import os,sys,json\n"
    "from pathlib import Path\n"
    "base=Path(os.environ['MOTOR_AVISO_BASE'])\n"
    "ident=sys.argv[-1]\n"
    "sys.stdin.read()\n"
    "log=base/'entregados.log'\n"
    "if log.exists() and ident in log.read_text().split():\n"
    " print(json.dumps({'recibo':'conciliado-'+ident}))\n"
    "else:\n"
    " print(json.dumps({'ausente':True}))\n"
)

PROHIBIDOS = ("enc_", "av_", "h_", "F12", "/", "\\", "`", ".py", ".json", ".md", " " * 2)


class Banco:
    def __init__(self, tmp: str):
        self.base = Path(tmp) / "base"
        self.reg = Registro(self.base)
        envio = Path(tmp) / "envio.py"
        envio.write_text(TRANSPORTE, encoding="utf-8")
        check = Path(tmp) / "check.py"
        check.write_text(CONCILIACION, encoding="utf-8")
        cfg = {
            "comando": [sys.executable, "-B", str(envio)],
            "conciliacion": [sys.executable, "-B", str(check)],
            "idempotente": True,
        }
        (self.base / "canal.json").write_text(json.dumps({"tg": cfg}), encoding="utf-8")

    def caer(self) -> None:
        (self.base / "caido").write_text("x")

    def levantar(self) -> None:
        (self.base / "caido").unlink()

    def cuerpos(self) -> list[dict]:
        log = self.base / "cuerpos.log"
        if not log.exists():
            return []
        return [
            json.loads(linea)
            for linea in log.read_text(encoding="utf-8").splitlines()
            if linea.strip()
        ]

    def aviso_guardado(self, ident: str) -> dict:
        return json.loads(
            (self.base / "avisos" / f"{ident}.json").read_text(encoding="utf-8")
        )

    def escribir_aviso(self, ident: str, guardado: dict) -> None:
        (self.base / "avisos" / f"{ident}.json").write_text(
            json.dumps(guardado), encoding="utf-8"
        )

    def encargo(self, texto: str, clave: str) -> str:
        return directores.encargar(
            self.reg, texto, clave, HORA, "hermes", "tg", "directa"
        )["encargo"]

    def terminar(self, ident: str, pasos: list[dict] | None = None) -> None:
        for n, paso in enumerate(pasos or []):
            self.reg.anotar(
                ident,
                {"kind": "entrega_externa", "data": {**paso, "hora": HORA}},
                f"paso-{ident}-{n}",
            )
        self.reg.anotar(
            ident,
            {"kind": "resultado_directo", "data": {"evidencia": "listo", "hora": HORA}},
            f"fin-{ident}",
        )

    def cancelar(self, ident: str) -> None:
        self.reg.anotar(
            ident,
            {"kind": "orden", "data": {"accion": "cancelar", "hora": HORA}},
            f"cancela-{ident}",
        )
        self.reg.anotar(
            ident,
            {"kind": "arbol", "data": {"observacion": "ausente", "hora": HORA}},
            f"arbol-{ident}",
        )

    def olvidar_corte_de_cierres(self) -> None:
        """La base de la versión anterior no tenía el corte de cierres de cancelados."""
        self.reg.db.execute(
            "DELETE FROM cortes_avisos WHERE version = 'cierres-cancelados-v1'"
        )
        self.reg.db.commit()

    def reabrir(self) -> None:
        """Cierra y vuelve a abrir la base, como el primer arranque tras el despliegue."""
        self.reg.close()
        self.reg = Registro(self.base)

    def cerrar(self) -> None:
        self.reg.close()


class Base(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.b = Banco(self._tmp.name)
        self.addCleanup(self.b.cerrar)

    def encargo_terminado(self, clave: str, pasos: list[dict] | None = None) -> str:
        ident = self.b.encargo(
            f"# {clave} listo para revisar\n\nTrabaja el pedido y comprueba.",
            clave,
        )
        self.b.terminar(ident, pasos)
        return ident


class ElCierreEnLlano(Base):
    def test_el_cierre_de_un_encargo_con_pr_es_llano_corto_y_trae_el_numero(self):
        ident = self.encargo_terminado(
            "avisos-caidos",
            [{"paso": "pr_listo", "numero": 160}, {"paso": "deepseek_leido"}],
        )
        avisos = directores.drenar(self.b.reg, ahora=T0)["avisos"]
        self.assertEqual(len(avisos), 1)
        self.assertTrue(avisos[0]["entregado"])
        cuerpo = self.b.cuerpos()[0]["cuerpo"]
        self.assertEqual(
            cuerpo,
            "Terminó tu encargo «avisos-caidos listo para revisar». "
            "El PR #160 está abierto; el CI corre en el PR. listo",
        )
        self.assertLess(len(cuerpo), 300)
        for prohibido in PROHIBIDOS:
            self.assertNotIn(prohibido, cuerpo)
        self.assertIsNone(re.search(r"\b[0-9a-f]{7,64}\b", cuerpo), "sin SHAs")
        self.assertIn("#160", cuerpo)

    def test_integrado_y_desplegado_se_dicen_en_una_linea(self):
        uno = self.encargo_terminado(
            "fase-a",
            [
                {"paso": "pr_listo", "numero": 9},
                {"paso": "deepseek_leido"},
                {"paso": "integrado"},
            ],
        )
        dos = self.encargo_terminado(
            "fase-b",
            [
                {"paso": "pr_listo", "numero": 10},
                {"paso": "deepseek_leido"},
                {"paso": "integrado"},
                {"paso": "desplegado"},
            ],
        )
        salida = directores.drenar(self.b.reg, ahora=T0)["avisos"]
        self.assertTrue(all(a["entregado"] for a in salida))
        ids = {a["encargo"]: a["id"] for a in salida}
        cuerpos = {c["id"]: c["cuerpo"] for c in self.b.cuerpos()}
        self.assertEqual(
            cuerpos[ids[uno]],
            "Terminó tu encargo «fase-a listo para revisar». Quedó integrado. listo",
        )
        self.assertEqual(
            cuerpos[ids[dos]],
            "Terminó tu encargo «fase-b listo para revisar». Quedó desplegado. listo",
        )

    def test_un_encargo_cancelado_lo_dice_en_una_frase_simple(self):
        ident = self.b.encargo(
            "# Un pedido que no pudo terminar\n\nAlgo que se cancela.",
            "cancelado-uno",
        )
        self.b.cancelar(ident)
        avisos = directores.drenar(self.b.reg, ahora=T0)["avisos"]
        self.assertEqual(len(avisos), 1)
        self.assertTrue(avisos[0]["entregado"])
        cuerpo = self.b.cuerpos()[0]["cuerpo"]
        self.assertEqual(
            cuerpo,
            "Terminó tu encargo «Un pedido que no pudo terminar». "
            "Quedó cancelado y no se completó.",
        )
        for prohibido in PROHIBIDOS:
            self.assertNotIn(prohibido, cuerpo)

    def test_sin_pr_ni_nivel_el_cierre_usa_la_clave_como_titulo(self):
        ident = self.b.encargo("un pedido sin encabezados", "pedido-desnudo")
        self.b.terminar(ident)
        avisos = directores.drenar(self.b.reg, ahora=T0)["avisos"]
        self.assertTrue(avisos[0]["entregado"])
        self.assertEqual(
            self.b.cuerpos()[0]["cuerpo"],
            "Terminó tu encargo «pedido-desnudo». listo",
        )

    def test_el_cierre_de_la_via_directa_vuelve_a_llevar_el_resultado(self):
        ident = self.b.encargo("# Crea dato.txt con 42\n\ntexto", "directa-uno")
        resultado = "leí dato.txt: 42"
        huella = hashlib.sha256(resultado.encode()).hexdigest()
        self.b.reg.anotar(
            ident,
            {"kind": "resultado_directo", "data": {"evidencia": huella, "hora": HORA}},
            f"fin-{ident}",
        )
        espacio = self.b.base / "espacio" / ident
        espacio.mkdir(parents=True)
        (espacio / "resultado.txt").write_text(resultado, encoding="utf-8")
        otro = self.b.encargo("# Sin resultado legible\n\ntexto", "directa-dos")
        self.b.reg.anotar(
            otro,
            {"kind": "resultado_directo", "data": {"evidencia": "a" * 64, "hora": HORA}},
            f"fin-{otro}",
        )
        salida = {
            a["encargo"]: a
            for a in directores.drenar(self.b.reg, ahora=T0)["avisos"]
        }
        self.assertTrue(salida[ident]["entregado"])
        self.assertTrue(salida[otro]["entregado"])
        cuerpos = {c["id"]: c["cuerpo"] for c in self.b.cuerpos()}
        self.assertEqual(
            cuerpos[salida[ident]["id"]],
            f"Terminó tu encargo «Crea dato.txt con 42». {resultado}",
        )
        self.assertEqual(
            cuerpos[salida[otro]["id"]],
            "Terminó tu encargo «Sin resultado legible».",
        )

    def test_el_cierre_lleva_las_correcciones_que_ningun_intento_recibio(self):
        ident = self.encargo_terminado(
            "con-correccion",
            [{"paso": "pr_listo", "numero": 5}, {"paso": "deepseek_leido"}],
        )
        self.b.reg.anotar(
            ident,
            {"kind": "procedimiento", "data": {
                "nota": "Correcciones que ningún intento pudo recibir: el dato debe ser 43",
                "ajustes_anotados": 1,
                "hora": HORA,
            }},
            f"sin-intento-{ident}",
        )
        salida = directores.drenar(self.b.reg, ahora=T0)["avisos"]
        self.assertTrue(salida[0]["entregado"])
        self.assertEqual(
            self.b.cuerpos()[0]["cuerpo"],
            "Terminó tu encargo «con-correccion listo para revisar». "
            "El PR #5 está abierto; el CI corre en el PR. "
            "Correcciones que ningún intento pudo recibir: el dato debe ser 43 listo",
        )

    def test_la_nota_legada_sin_tildes_tambien_llega_en_el_cierre(self):
        ident = self.b.encargo(
            "# Un cierre legado\n\npedido", "legado-uno"
        )
        self.b.terminar(ident)
        self.b.reg.anotar(
            ident,
            {"kind": "procedimiento", "data": {
                "nota": "correcciones que ningun intento pudo recibir: el dato debe ser 43",
                "ajustes_anotados": 1,
                "hora": HORA,
            }},
            f"legado-{ident}",
        )
        salida = directores.drenar(self.b.reg, ahora=T0)["avisos"]
        self.assertTrue(salida[0]["entregado"])
        self.assertIn("ningun intento pudo recibir", self.b.cuerpos()[0]["cuerpo"])
        self.assertIn("el dato debe ser 43", self.b.cuerpos()[0]["cuerpo"])


class UnAvisoViejoNoSaleDeGolpe(Base):
    def test_un_aviso_previo_al_despliegue_de_hace_7_horas_se_descarta(self):
        self.encargo_terminado("legado-viejo")
        self.b.caer()
        primera = directores.drenar(self.b.reg, ahora=T0)["avisos"]
        aviso_id = primera[0]["id"]
        guardado = self.b.aviso_guardado(aviso_id)
        del guardado["primer_intento"]
        self.b.escribir_aviso(aviso_id, guardado)
        segunda = directores.drenar(self.b.reg, ahora=T0 + 7 * 3600)["avisos"]
        guardado = self.b.aviso_guardado(aviso_id)
        self.assertEqual(guardado["estado"], "descartado")
        self.assertEqual(
            guardado["motivo"],
            "canal caído demasiado tiempo; queda en el registro",
        )
        self.assertFalse(segunda[0]["entregado"])
        self.assertEqual(
            len(self.b.cuerpos()), 1, "el aviso de la versión anterior no se reenvía"
        )

    def test_un_aviso_previo_al_despliegue_de_hace_1_hora_si_se_envia(self):
        self.encargo_terminado("legado-reciente")
        self.b.caer()
        primera = directores.drenar(self.b.reg, ahora=T0)["avisos"]
        aviso_id = primera[0]["id"]
        guardado = self.b.aviso_guardado(aviso_id)
        del guardado["primer_intento"]
        self.b.escribir_aviso(aviso_id, guardado)
        self.b.levantar()
        avisos = directores.drenar(self.b.reg, ahora=T0 + 3600)["avisos"]
        self.assertTrue(avisos[0]["entregado"])
        self.assertEqual(len(self.b.cuerpos()), 2, "el fallo y el reintento que sí salió")

    def test_los_avisos_de_ayer_no_salen_cuando_el_canal_vuelve(self):
        # Telegram caído un día: la versión anterior reintentó cada hora y el
        # último fallo es reciente; la edad se cuenta desde el primer fallo.
        self.encargo_terminado("de-ayer")
        self.b.caer()
        primera = directores.drenar(self.b.reg, ahora=T0)["avisos"]
        aviso_id = primera[0]["id"]
        guardado = self.b.aviso_guardado(aviso_id)
        del guardado["primer_intento"]
        guardado["fallos"] = 20
        guardado["proximo_intento"] = directores._iso(T0)
        self.b.escribir_aviso(aviso_id, guardado)
        self.b.levantar()
        avisos = directores.drenar(self.b.reg, ahora=T0 + 3600)["avisos"]
        self.assertEqual(self.b.aviso_guardado(aviso_id)["estado"], "descartado")
        self.assertFalse(avisos[0]["entregado"])
        self.assertEqual(
            len(self.b.cuerpos()), 1, "el reintento nocturno y ningún envío nuevo"
        )

    def test_un_aviso_previo_al_despliegue_envejece_desde_su_fallo_original(self):
        self.encargo_terminado("legado-5h")
        self.b.caer()
        primera = directores.drenar(self.b.reg, ahora=T0)["avisos"]
        aviso_id = primera[0]["id"]
        guardado = self.b.aviso_guardado(aviso_id)
        del guardado["primer_intento"]
        guardado["proximo_intento"] = directores._iso(T0 - 5 * 3600 + 60)
        guardado["fallos"] = 1
        self.b.escribir_aviso(aviso_id, guardado)
        directores.drenar(self.b.reg, ahora=T0 + 1)
        guardado = self.b.aviso_guardado(aviso_id)
        self.assertLess(
            guardado["primer_intento"],
            T0,
            "la época del primer envío no se reancla al despliegue",
        )
        self.b.levantar()
        directores.drenar(self.b.reg, ahora=T0 + 2 * 3600)
        guardado = self.b.aviso_guardado(aviso_id)
        self.assertEqual(guardado["estado"], "descartado")
        self.assertEqual(
            len(self.b.cuerpos()), 2, "los dos fallos previos y ningún envío"
        )

    def test_un_aviso_pendiente_previo_al_despliegue_no_se_envia(self):
        self.encargo_terminado("pendiente-viejo")
        directores.drenar(self.b.reg, ahora=T0, cortar="antes")
        aviso_id = directores.drenar(self.b.reg, ahora=T0, cortar="antes")["avisos"][0][
            "id"
        ]
        guardado = self.b.aviso_guardado(aviso_id)
        self.assertEqual(guardado["estado"], "pendiente")
        self.assertEqual(self.b.cuerpos(), [], "el corte antes no intenta envíos")
        ruta = self.b.base / "avisos" / f"{aviso_id}.json"
        viejo = T0 - 7 * 3600
        os.utime(ruta, (viejo, viejo))
        avisos = directores.drenar(self.b.reg, ahora=T0)["avisos"]
        self.assertEqual(self.b.aviso_guardado(aviso_id)["estado"], "descartado")
        self.assertFalse(avisos[0]["entregado"])
        self.assertEqual(self.b.cuerpos(), [], "un pendiente de ayer no se envía")

    def test_el_id_del_cierre_de_cancelado_no_depende_de_la_evidencia(self):
        ident = self.b.encargo("# Se cancela con evidencia\n\ntexto", "cancela-evid")
        self.b.cancelar(ident)
        enc = self.b.reg.fold(ident)
        con_evidencia = directores._aviso_de(
            replace(enc, evidencia=("huella-1", "huella-2"))
        )
        self.assertEqual(con_evidencia["id"], directores._aviso_de(enc)["id"])
        self.assertTrue(con_evidencia["id"].startswith("av_"))

    def test_una_base_existente_con_cancelados_no_sale_en_rafaga_al_abrirse(self):
        for n in range(2):
            ident = self.b.encargo(
                f"# Cancelado tras el corte {n}\n\ntexto", f"tras-corte-{n}"
            )
            self.b.cancelar(ident)
        self.b.olvidar_corte_de_cierres()
        self.b.reabrir()
        self.assertEqual(directores.drenar(self.b.reg, ahora=T0)["avisos"], [])
        self.assertEqual(
            self.b.cuerpos(), [], "los cancelados de antes no se envían al abrir"
        )
        ident = self.b.encargo(
            "# Cancelado después del despliegue\n\ntexto", "despliegue-uno"
        )
        self.b.cancelar(ident)
        avisos = directores.drenar(self.b.reg, ahora=T0 + 60)["avisos"]
        self.assertEqual([a["encargo"] for a in avisos if a["entregado"]], [ident])
        self.assertIn("Quedó cancelado", self.b.cuerpos()[0]["cuerpo"])
        directores.drenar(self.b.reg, ahora=T0 + 120)
        self.assertEqual(len(self.b.cuerpos()), 1, "y el cierre no se repite")

    def test_un_aviso_incierto_de_hace_7_horas_se_descarta_y_no_se_envia(self):
        self.encargo_terminado("viejo-uno")
        self.b.caer()
        primera = directores.drenar(self.b.reg, ahora=T0)["avisos"]
        self.assertFalse(primera[0]["entregado"])
        aviso_id = primera[0]["id"]
        self.assertEqual(len(self.b.cuerpos()), 1, "un intento con el canal caído")
        segunda = directores.drenar(self.b.reg, ahora=T0 + 7 * 3600)["avisos"]
        guardado = self.b.aviso_guardado(aviso_id)
        self.assertEqual(guardado["estado"], "descartado")
        self.assertEqual(
            guardado["motivo"],
            "canal caído demasiado tiempo; queda en el registro",
        )
        self.assertFalse(segunda[0]["entregado"])
        self.assertEqual(len(self.b.cuerpos()), 1, "el aviso vencido no se envía")
        directores.drenar(self.b.reg, ahora=T0 + 8 * 3600)
        self.assertEqual(len(self.b.cuerpos()), 1, "y un descartado no se recupera")

    def test_un_aviso_incierto_de_hace_1_hora_si_se_envia(self):
        self.encargo_terminado("reciente-uno")
        self.b.caer()
        directores.drenar(self.b.reg, ahora=T0)
        self.assertEqual(len(self.b.cuerpos()), 1)
        self.b.levantar()
        avisos = directores.drenar(self.b.reg, ahora=T0 + 3600)["avisos"]
        self.assertTrue(avisos[0]["entregado"])
        self.assertEqual(avisos[0]["estado"], "publicado")
        self.assertEqual(len(self.b.cuerpos()), 2, "un fallo y el reintento que sí salió")


if __name__ == "__main__":
    unittest.main()
