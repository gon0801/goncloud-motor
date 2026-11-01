"""Al canal solo llegan avisos para una persona no técnica, y un aviso entregado no se manda dos veces."""

import json
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
    "sys.stdin.read()\n"
    "modo=(base/'modo').read_text().strip()\n"
    "with open(base/'intentos.log','a') as f: f.write(ident+'\\n')\n"
    "if modo=='entrega_y_error':\n"
    " with open(base/'entregados.log','a') as f: f.write(ident+'\\n')\n"
    " sys.stderr.write('se cortó después de enviar'); sys.exit(1)\n"
    "if modo=='perdido':\n"
    " sys.stderr.write('no salió'); sys.exit(1)\n"
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


class Banco:
    def __init__(self, tmp: str, conciliacion: bool = True):
        self.base = Path(tmp) / "base"
        self.reg = Registro(self.base)
        envio = Path(tmp) / "envio.py"
        envio.write_text(TRANSPORTE, encoding="utf-8")
        cfg = {"comando": [sys.executable, "-B", str(envio)], "idempotente": True}
        if conciliacion:
            check = Path(tmp) / "check.py"
            check.write_text(CONCILIACION, encoding="utf-8")
            cfg["conciliacion"] = [sys.executable, "-B", str(check)]
        (self.base / "canal.json").write_text(json.dumps({"tg": cfg}), encoding="utf-8")
        self.modo("ok")

    def modo(self, valor: str) -> None:
        (self.base / "modo").write_text(valor)

    def lineas(self, nombre: str) -> list[str]:
        log = self.base / nombre
        return log.read_text().split() if log.exists() else []

    def encargo(self, clave: str = "uno") -> str:
        ident = directores.encargar(
            self.reg, f"pedido {clave}", clave, HORA, "hermes", "tg", "durable"
        )["encargo"]
        self.reg.anotar(ident, {"kind": "fases", "data": {
            "fases": [{"id": "A", "resultado": "fase comprobada"}], "hora": HORA,
        }}, f"fase-{clave}")
        return ident


class Base(unittest.TestCase):
    conciliacion = True

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.b = Banco(self._tmp.name, self.conciliacion)
        self.addCleanup(self.b.reg.close)


class SoloAvisosDeUsuarioFinal(Base):
    def test_una_decision_una_espera_y_una_revision_no_generan_aviso(self):
        ident = self.b.encargo()
        self.b.reg.anotar(ident, {"kind": "decision", "data": {
            "id": "d1", "eleccion": "Declarar en comprobaciones.json solo python -m verify",
            "motivo": "nueva evidencia", "hora": HORA,
        }}, "dec")
        self.b.reg.anotar(ident, {"kind": "espera", "data": {
            "motivo": "Persiste la falta de un entorno macOS", "reanuda_en": "2026-11-01T11:00:00Z",
            "hora": HORA,
        }}, "espera")
        enc = self.b.reg.fold(ident)
        self.assertEqual(enc.estado, "esperando_reintento")
        self.assertEqual([h["tipo"] for h in directores.hechos_de_avance(enc)], ["inicio"])
        directores.drenar(self.b.reg, ahora=T0)
        self.assertEqual(len(self.b.lineas("intentos.log")), 1)

    def test_el_inicio_se_avisa_una_sola_vez_aunque_cambie_la_fase(self):
        ident = self.b.encargo()
        directores.drenar(self.b.reg, ahora=T0)
        self.b.reg.anotar(ident, {"kind": "fases", "data": {
            "fases": [{"id": "A", "resultado": "otro resultado"}], "hora": HORA,
        }}, "fase-b")
        directores.drenar(self.b.reg, ahora=T0 + 60)
        self.assertEqual(len(self.b.lineas("entregados.log")), 1)

    def test_los_avisos_que_van_se_escriben_en_espanol_llano(self):
        ident = self.b.encargo()
        enc = self.b.reg.fold(ident)
        fase = replace(enc.fases[0], pr_numero=7, pr_sha="a" * 40, nivel_alcanzado="pr")
        enc = replace(enc, fases=(fase,))
        self.b.reg.anotar(ident, {"kind": "impedimento", "data": {
            "clase": "comprobacion", "causa": "falta un entorno macOS en motor/x.py",
            "condicion": "correr `python -m verify.reopen_check` en un Mac, ver .interno/buzon/r.json "
                         "y el aviso av_9a27a464d07eacfe",
            "hora": HORA,
        }}, "imp")
        hechos = {h["tipo"]: h["texto"] for h in directores.hechos_de_avance(
            replace(self.b.reg.fold(ident), fases=enc.fases))}
        self.assertEqual(hechos["inicio"], "Empecé a trabajar en tu encargo.")
        self.assertEqual(hechos["pr"], "El PR #7 está listo para que decidas si lo integras.")
        self.assertEqual(
            hechos["trabado"],
            "Tu encargo se trabó y no puede avanzar solo. Hace falta: correr un comando en un Mac, "
            "ver un archivo y el aviso un identificador interno. "
            "El detalle técnico está en el estado del encargo.",
        )
        for texto in hechos.values():
            for prohibido in ("/", "`", "enc_", "av_", ".json", ".py", "reopen_check"):
                self.assertNotIn(prohibido, texto)

    def _preguntas(self, texto: str) -> list[str]:
        ident = self.b.encargo()
        self.b.reg.anotar(ident, {"kind": "pregunta", "data": {
            "texto": texto, "bloquea_todo": True, "hora": HORA,
        }}, "pregunta")
        enc = self.b.reg.fold(ident)
        return [h["texto"] for h in directores.hechos_de_avance(enc) if h["tipo"] == "pregunta"]

    def test_una_pregunta_limpia_llega_tal_cual(self):
        self.assertEqual(
            self._preguntas("¿Publico la versión final del informe?"),
            ["Necesito tu decisión para seguir. ¿Publico la versión final del informe?"],
        )

    def test_una_pregunta_con_detalle_tecnico_conserva_su_objeto_y_remite_al_estado(self):
        self.assertEqual(
            self._preguntas("¿Uso la cuenta de `ops/cuenta.json` para publicar?"),
            ["Necesito tu decisión para seguir. ¿Uso la cuenta de un archivo para publicar? "
             "El detalle técnico está en el estado del encargo."],
        )

    def test_el_ejemplo_del_pedido_no_pierde_lo_que_se_decide(self):
        textos = self._preguntas(
            "¿Declaro en comprobaciones.json solo `python -m verify.reopen_check`?"
        )
        self.assertEqual(
            textos,
            ["Necesito tu decisión para seguir. ¿Declaro en un archivo solo un comando? "
             "El detalle técnico está en el estado del encargo."],
        )

    def test_la_remision_se_agrega_solo_cuando_hubo_detalle_tecnico(self):
        self.assertEqual(
            directores._con_remision("Tu encargo se trabó y no puede avanzar solo.", True),
            "Tu encargo se trabó y no puede avanzar solo. El detalle técnico está en el estado del encargo.",
        )
        self.assertEqual(directores._llano("conseguir el equipo 0."), ("conseguir el equipo 0", False))
        self.assertEqual(directores._llano(""), ("", False))
        self.assertEqual(directores._con_remision("Hola.", False), "Hola.")

    def test_un_impedimento_que_cambia_de_texto_no_repite_el_aviso(self):
        ident = self.b.encargo()
        for n in range(3):
            self.b.reg.anotar(ident, {"kind": "impedimento", "data": {
                "clase": "comprobacion", "causa": f"falta un entorno {n}",
                "condicion": f"conseguir el equipo {n}", "hora": HORA,
            }}, f"imp-{n}")
            directores.drenar(self.b.reg, ahora=T0 + 60 * n)
        self.assertEqual(len(self.b.lineas("entregados.log")), 2, "inicio y un solo aviso de trabado")


class SinDuplicados(Base):
    def test_un_transporte_que_entrega_pero_responde_error_no_genera_un_segundo_mensaje(self):
        self.b.encargo()
        self.b.modo("entrega_y_error")
        avisos = directores.drenar(self.b.reg, ahora=T0)["avisos"]
        self.assertFalse(avisos[0]["entregado"])
        self.b.modo("ok")
        avisos = directores.drenar(self.b.reg, ahora=T0 + 61)["avisos"]
        self.assertTrue(avisos[0]["entregado"])
        self.assertEqual(avisos[0]["recibo"].split("-")[0], "conciliado")
        directores.drenar(self.b.reg, ahora=T0 + 10_000)
        self.assertEqual(len(self.b.lineas("entregados.log")), 1, "el mensaje llegó una sola vez")
        self.assertEqual(len(self.b.lineas("intentos.log")), 1, "el reintento no volvió a enviar")

    def test_un_mensaje_que_no_salio_se_reenvia_una_vez(self):
        self.b.encargo()
        self.b.modo("perdido")
        directores.drenar(self.b.reg, ahora=T0)
        self.b.modo("ok")
        avisos = directores.drenar(self.b.reg, ahora=T0 + 61)["avisos"]
        self.assertTrue(avisos[0]["entregado"])
        directores.drenar(self.b.reg, ahora=T0 + 10_000)
        self.assertEqual(len(self.b.lineas("entregados.log")), 1)
        self.assertEqual(len(self.b.lineas("intentos.log")), 2)


class SinConciliacion(Base):
    conciliacion = False

    def test_un_canal_idempotente_sin_conciliacion_reintenta_con_el_mismo_id(self):
        self.b.encargo()
        self.b.modo("perdido")
        directores.drenar(self.b.reg, ahora=T0)
        self.b.modo("ok")
        avisos = directores.drenar(self.b.reg, ahora=T0 + 61)["avisos"]
        self.assertTrue(avisos[0]["entregado"])
        self.assertEqual(len(set(self.b.lineas("intentos.log"))), 1, "mismo id de aviso en cada intento")


if __name__ == "__main__":
    unittest.main()
