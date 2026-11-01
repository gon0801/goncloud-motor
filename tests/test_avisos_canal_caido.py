"""Un canal de avisos caído no frena al motor: espera creciente, corte por canal y encargos terminados."""

import json
import sys
import tempfile
import unittest
from pathlib import Path

from motor.encargos.registro import Registro
from motor.integraciones import directores

HORA = "2026-11-01T10:00:00Z"
T0 = 1_000_000.0

TRANSPORTE = (
    "import os,sys,json\n"
    "from pathlib import Path\n"
    "base=Path(os.environ['MOTOR_AVISO_BASE'])\n"
    "sys.stdin.read()\n"
    "with open(base/'llamadas.log','a') as f: f.write(os.environ['MOTOR_AVISO_ID']+'\\n')\n"
    "if (base/'caido').exists():\n"
    " sys.stderr.write('goncloud no permite leer el bot'); sys.exit(1)\n"
    "print(json.dumps({'recibo':'ok-'+os.environ['MOTOR_AVISO_ID']}))\n"
)


class Banco:
    def __init__(self, tmp: str):
        self.base = Path(tmp) / "base"
        self.reg = Registro(self.base)
        script = Path(tmp) / "transporte.py"
        script.write_text(TRANSPORTE, encoding="utf-8")
        (self.base / "canal.json").write_text(
            json.dumps({"tg": {"comando": [sys.executable, "-B", str(script)], "idempotente": True}}),
            encoding="utf-8",
        )
        self.caer()

    def caer(self) -> None:
        (self.base / "caido").write_text("x")

    def levantar(self) -> None:
        (self.base / "caido").unlink()

    def llamadas(self) -> list[str]:
        log = self.base / "llamadas.log"
        return log.read_text().split() if log.exists() else []

    def encargo(self, clave: str, avances: int = 2) -> str:
        ident = directores.encargar(
            self.reg, f"pedido {clave}", clave, HORA, "hermes", "tg", "directa"
        )["encargo"]
        self.reg.anotar(ident, {"kind": "fases", "data": {
            "fases": [{"id": "A", "resultado": "fase comprobada"}], "hora": HORA,
        }}, f"fase-{clave}")
        for n in range(avances - 1):
            self.reg.anotar(ident, {"kind": "impedimento", "data": {
                "clase": f"c{n}", "causa": "falta un entorno", "condicion": "conseguir el equipo",
                "hora": HORA,
            }}, f"imp-{clave}-{n}")
        return ident

    def terminar(self, ident: str) -> None:
        self.reg.anotar(ident, {"kind": "resultado_directo", "data": {
            "evidencia": "listo", "hora": HORA,
        }}, f"fin-{ident}")

    def aviso(self, ident: str) -> dict:
        return json.loads((self.base / "avisos" / f"{ident}.json").read_text())


class CanalCaido(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.b = Banco(self._tmp.name)
        self.addCleanup(self.b.reg.close)

    def test_un_transporte_que_falla_corta_el_canal_en_esa_vuelta(self):
        self.b.encargo("uno", avances=2)
        self.b.encargo("dos", avances=2)
        directores.drenar(self.b.reg, ahora=T0)
        self.assertEqual(len(self.b.llamadas()), 1)

    def test_un_mensaje_rechazado_no_corta_el_canal(self):
        script = Path(self._tmp.name) / "rechaza_el_primero.py"
        script.write_text(
            "import os,sys,json\n"
            "from pathlib import Path\n"
            "log=Path(os.environ['MOTOR_AVISO_BASE'])/'llamadas.log'\n"
            "sys.stdin.read()\n"
            "primero=not log.exists()\n"
            "with open(log,'a') as f: f.write(os.environ['MOTOR_AVISO_ID']+'\\n')\n"
            "if primero: sys.stdout.buffer.write(b'{\"recibo\":\"\\xf3\"}'); sys.exit(0)\n"
            "print(json.dumps({'recibo':'ok'}))\n",
            encoding="utf-8",
        )
        self.b.levantar()
        (self.b.base / "canal.json").write_text(json.dumps(
            {"tg": {"comando": [sys.executable, "-B", str(script)], "idempotente": True}}))
        self.b.encargo("uno", avances=2)
        avisos = directores.drenar(self.b.reg, ahora=T0)["avisos"]
        self.assertEqual(len(self.b.llamadas()), 2)
        self.assertEqual([a["entregado"] for a in avisos], [False, True])

    def test_el_corte_no_toca_a_otro_canal(self):
        otro = Path(self._tmp.name) / "otro.py"
        otro.write_text("import sys,json\nsys.stdin.read()\nprint(json.dumps({'recibo':'otro'}))\n")
        canal = json.loads((self.b.base / "canal.json").read_text())
        canal["wa"] = {"comando": [sys.executable, "-B", str(otro)], "idempotente": True}
        (self.b.base / "canal.json").write_text(json.dumps(canal))
        self.b.encargo("tg", avances=2)
        ident = directores.encargar(
            self.b.reg, "pedido wa", "wa", HORA, "hermes", "wa", "durable"
        )["encargo"]
        self.b.reg.anotar(ident, {"kind": "fases", "data": {
            "fases": [{"id": "A", "resultado": "r"}], "hora": HORA}}, "fase-wa")
        avisos = directores.drenar(self.b.reg, ahora=T0)["avisos"]
        entregados = [a for a in avisos if a["entregado"]]
        self.assertEqual([a["canal"] for a in entregados], ["wa"])
        self.assertEqual(len(self.b.llamadas()), 1)

    def test_el_reintento_respeta_la_espera_creciente(self):
        ident = self.b.encargo("uno", avances=1)
        avisos = directores.drenar(self.b.reg, ahora=T0)["avisos"]
        aviso_id = avisos[0]["id"]
        self.assertEqual(len(self.b.llamadas()), 1)
        self.assertEqual(self.b.aviso(aviso_id)["proximo_intento"], directores._iso(T0 + 60))

        directores.drenar(self.b.reg, ahora=T0 + 59)
        self.assertEqual(len(self.b.llamadas()), 1, "antes de la espera no se reintenta")

        directores.drenar(self.b.reg, ahora=T0 + 61)
        self.assertEqual(len(self.b.llamadas()), 2)
        self.assertEqual(self.b.aviso(aviso_id)["proximo_intento"], directores._iso(T0 + 61 + 120))

        directores.drenar(self.b.reg, ahora=T0 + 61 + 119)
        self.assertEqual(len(self.b.llamadas()), 2)
        directores.drenar(self.b.reg, ahora=T0 + 61 + 121)
        self.assertEqual(len(self.b.llamadas()), 3)

    def test_la_espera_tiene_tope_de_una_hora(self):
        self.assertEqual([directores.espera_s(n) for n in (1, 2, 3, 7, 8, 100)],
                         [60, 120, 240, 3600, 3600, 3600])

    def test_un_aviso_viejo_sin_espera_guardada_hereda_los_intentos(self):
        self.b.encargo("uno", avances=1)
        aviso_id = directores.drenar(self.b.reg, ahora=T0)["avisos"][0]["id"]
        ruta = self.b.base / "avisos" / f"{aviso_id}.json"
        doc = json.loads(ruta.read_text())
        doc.pop("proximo_intento")
        doc.pop("fallos")
        doc["intentos"] = 40
        ruta.write_text(json.dumps(doc))
        directores.drenar(self.b.reg, ahora=T0 + 1)
        self.assertEqual(self.b.aviso(aviso_id)["proximo_intento"], directores._iso(T0 + 1 + 3600))

    def test_al_volver_el_canal_el_aviso_pendiente_llega_una_vez(self):
        self.b.encargo("uno", avances=1)
        directores.drenar(self.b.reg, ahora=T0)
        self.b.levantar()
        directores.drenar(self.b.reg, ahora=T0 + 30)
        self.assertEqual(len(self.b.llamadas()), 1)
        avisos = directores.drenar(self.b.reg, ahora=T0 + 61)["avisos"]
        self.assertTrue(all(a["entregado"] for a in avisos))
        self.assertEqual(len(self.b.llamadas()), 2)
        directores.drenar(self.b.reg, ahora=T0 + 10_000)
        self.assertEqual(len(self.b.llamadas()), 2)

    def test_un_encargo_terminado_no_reenvia_avisos_de_avance(self):
        ident = self.b.encargo("uno", avances=2)
        primeros = directores.drenar(self.b.reg, ahora=T0)["avisos"]
        self.assertEqual(len(primeros), 2)
        self.b.terminar(ident)
        self.b.levantar()
        avisos = directores.drenar(self.b.reg, ahora=T0 + 10_000)["avisos"]
        self.assertEqual([a.get("tipo") for a in avisos], [None])
        self.assertTrue(avisos[0]["entregado"])
        self.assertEqual(len(self.b.llamadas()), 2, "un intento fallido de avance y el cierre")
        descartados = [
            json.loads(p.read_text()) for p in (self.b.base / "avisos").glob("*.json")
            if json.loads(p.read_text()).get("tipo")
        ]
        self.assertEqual({d["estado"] for d in descartados}, {"descartado"})
        directores.drenar(self.b.reg, ahora=T0 + 20_000)
        self.assertEqual(len(self.b.llamadas()), 2)

    def test_un_encargo_terminado_no_genera_avances_nuevos(self):
        ident = self.b.encargo("uno", avances=1)
        self.b.terminar(ident)
        self.b.levantar()
        directores.drenar(self.b.reg, ahora=T0)
        self.assertEqual(len(self.b.llamadas()), 1, "solo el cierre")
        self.assertFalse(directores.hechos_de_avance(self.b.reg.fold(ident)))


if __name__ == "__main__":
    unittest.main()
