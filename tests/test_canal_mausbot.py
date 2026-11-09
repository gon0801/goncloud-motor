"""Canal MausBot: alta del director, transporte, sonda, instalador y parada.

Todo corre con un `openmausbot` falso escrito en un temporal; no hay red ni
aplicación real.
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import textwrap
import time
import unittest
from pathlib import Path

from motor.encargos.registro import Registro
from motor.integraciones import cli as director_cli
from motor.integraciones import directores

RAIZ = Path(__file__).resolve().parents[1]
TRANSPORTE = RAIZ / "motor" / "integraciones" / "transporte_mausbot.py"
INSTALADOR = RAIZ / "ops" / "instala-canal-mausbot.py"

FAKE = textwrap.dedent(
    '''
    import json
    import os
    import sys
    import time
    from pathlib import Path

    AYUDA = """openmausbot-cli

    Uso:
      openmausbot-cli doctor
      openmausbot-cli bots
      openmausbot-cli run
      openmausbot-cli send BOT TEXTO
      openmausbot-cli wait
      openmausbot-cli messages BOT --json
      openmausbot-cli interrupt
      openmausbot-cli events
    Opciones:
      --json  --quiet
    """

    def main():
        args = sys.argv[1:]
        registro = os.environ.get("FAKE_LOG")
        if registro:
            with open(registro, "a", encoding="utf-8") as archivo:
                archivo.write(json.dumps(args) + "\\n")
        if args and args[0] == "--help":
            print(AYUDA)
            return 0
        if args[:1] == ["send"] and len(args) >= 3:
            estado = os.environ.get("FAKE_ESTADO")
            previos = []
            if estado and Path(estado).exists():
                previos = Path(estado).read_text(encoding="utf-8").splitlines()
            item = {
                "id": "msg%d" % (len(previos) + 1),
                "bot": args[1],
                "text": args[2],
            }
            if estado:
                with open(estado, "a", encoding="utf-8") as archivo:
                    archivo.write(json.dumps(item, ensure_ascii=False) + "\\n")
            print(json.dumps(item, ensure_ascii=False))
            return 0
        if args[:1] == ["messages"] and len(args) >= 2:
            if os.environ.get("FAKE_DORMIR") == "1":
                time.sleep(5)
            estado = os.environ.get("FAKE_ESTADO")
            items = []
            if estado and Path(estado).exists():
                for linea in Path(estado).read_text(encoding="utf-8").splitlines():
                    if not linea.strip():
                        continue
                    doc = json.loads(linea)
                    if doc.get("bot") == args[1]:
                        items.append(doc)
            print(json.dumps({"messages": items}, ensure_ascii=False))
            return 0
        if args[:1] == ["interrupt"]:
            print(json.dumps({"interrupted": args[1] if len(args) > 1 else ""}))
            return 0
        print("comando desconocido", file=sys.stderr)
        return 2

    if __name__ == "__main__":
        raise SystemExit(main())
    '''
)


class CanalMausbot(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="canal-mausbot-"))
        self.addCleanup(_limpiar, self.tmp)
        self.base = self.tmp / "base"
        self.base.mkdir()
        self.fake = self.tmp / "fake_openmausbot.py"
        self.fake.write_text(FAKE, encoding="utf-8")
        self.log = self.tmp / "fake.log"
        self.estado = self.tmp / "fake-estado.jsonl"

    def _identidad(self, encargo: str, canal: str) -> None:
        carpeta = self.base / "canal-identidad"
        carpeta.mkdir(exist_ok=True)
        (carpeta / f"{encargo}.json").write_text(
            json.dumps({"canal": canal, "director": "mausbot", "sesion": ""}),
            encoding="utf-8",
        )

    def _env(self, **extra: str) -> dict[str, str]:
        env = os.environ.copy()
        env.update(
            {
                "MOTOR_MAUSBOT_EXE": sys.executable,
                "MOTOR_MAUSBOT_JS": str(self.fake),
                "MOTOR_MAUSBOT_TIMEOUT": "5",
                "MOTOR_AVISO_ID": "av_canal",
                "MOTOR_AVISO_ENCARGO": "enc_canal",
                "MOTOR_AVISO_BASE": str(self.base),
                "MOTOR_AVISO_FORMATO": "texto",
                "FAKE_LOG": str(self.log),
                "FAKE_ESTADO": str(self.estado),
                "PYTHONIOENCODING": "utf-8",
            }
        )
        env.update(extra)
        return env

    def _correr(self, accion: str, cuerpo: str = "", **extra: str):
        return subprocess.run(
            [sys.executable, str(TRANSPORTE), accion],
            input=cuerpo.encode("utf-8"),
            capture_output=True,
            env=self._env(**extra),
            timeout=20,
        )

    def _log(self) -> list[list[str]]:
        if not self.log.exists():
            return []
        return [
            json.loads(linea)
            for linea in self.log.read_text(encoding="utf-8").splitlines()
            if linea.strip()
        ]

    def test_encargar_acepta_mausbot_y_registra_la_identidad(self):
        salida = io.StringIO()
        with contextlib.redirect_stdout(salida):
            codigo = director_cli.main(
                [
                    "encargar", "--base", str(self.base),
                    "--director", "mausbot", "--via", "durable",
                    "--canal", "mausbot", "--clave", "pedido-1",
                    "--texto", "Crea dato.txt con 42",
                ]
            )
        self.assertEqual(codigo, 0)
        self.assertIn("encargo", salida.getvalue())
        recibo = json.loads(salida.getvalue().strip())
        reg = Registro(self.base)
        self.addCleanup(reg.close)
        self.assertEqual(reg.fold(recibo["encargo"]).director, "mausbot")
        identidad = json.loads(
            (self.base / "canal-identidad" / f"{recibo['encargo']}.json")
            .read_text(encoding="utf-8")
        )
        self.assertEqual(identidad["canal"], "mausbot")
        self.assertEqual(identidad["director"], "mausbot")

    def test_un_director_desconocido_lo_rechaza_argparse(self):
        with contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit):
                director_cli.main(
                    [
                        "encargar", "--base", str(self.base),
                        "--director", "desconocido", "--via", "durable",
                        "--canal", "x", "--clave", "pedido-1",
                        "--texto", "x",
                    ]
                )

    def test_send_usa_el_canal_de_la_identidad_y_lleva_la_marca(self):
        self._identidad("enc_canal", "ingenieria")
        hecho = self._correr("send", "<b>Hola</b> &amp; adios", MOTOR_AVISO_FORMATO="html")
        self.assertEqual(hecho.returncode, 0, hecho.stderr.decode())
        self.assertEqual(json.loads(hecho.stdout), {"id": "av_canal"})
        item = json.loads(self.estado.read_text(encoding="utf-8").splitlines()[0])
        self.assertEqual(item["bot"], "ingenieria")
        self.assertTrue(item["text"].startswith("Hola & adios"))
        self.assertIn("[motor-aviso:av_canal]", item["text"])
        llamada = self._log()[0]
        self.assertEqual(llamada[:2], ["send", "ingenieria"])

    def test_send_sin_identidad_usa_mausbot(self):
        hecho = self._correr("send", "hola", MOTOR_AVISO_ENCARGO="enc_sin_identidad")
        self.assertEqual(hecho.returncode, 0, hecho.stderr.decode())
        item = json.loads(self.estado.read_text(encoding="utf-8").splitlines()[0])
        self.assertEqual(item["bot"], "MausBot")

    def test_check_halla_la_marca_y_otro_aviso_esta_ausente(self):
        self._identidad("enc_canal", "ingenieria")
        self._correr("send", "hola")
        hallado = self._correr("check")
        self.assertEqual(hallado.returncode, 0, hallado.stderr.decode())
        self.assertEqual(json.loads(hallado.stdout), {"id": "msg1"})
        ausente = self._correr("check", MOTOR_AVISO_ID="av_otro")
        self.assertEqual(ausente.returncode, 0, ausente.stderr.decode())
        self.assertEqual(json.loads(ausente.stdout), {"ausente": True})

    def test_una_consulta_colgada_sale_con_error_visible(self):
        self._identidad("enc_canal", "ingenieria")
        inicio = time.monotonic()
        hecho = self._correr(
            "check", MOTOR_MAUSBOT_TIMEOUT="1", FAKE_DORMIR="1"
        )
        transcurrido = time.monotonic() - inicio
        self.assertNotEqual(hecho.returncode, 0)
        self.assertLess(transcurrido, 4)
        self.assertIn(b"tiempo", hecho.stderr)

    def test_capacidades_mausbot_lee_la_ayuda(self):
        doc = directores.capacidades_mausbot([sys.executable, str(self.fake)])
        self.assertTrue(doc["entrega"])
        self.assertTrue(doc["consulta"])
        self.assertTrue(doc["parada"])

    def test_instalador_solo_agrega_mausbot_y_es_idempotente(self):
        base = self.tmp / "pc"
        base.mkdir()
        original = {
            "telegram": {"comando": ["tg", "send"]},
            "hermes": {"comando": ["hm", "send"]},
        }
        (base / "canal.json").write_text(
            json.dumps(original), encoding="utf-8"
        )
        comando = [
            sys.executable, str(INSTALADOR), "--base", str(base),
            "--python", "py", "--transporte", "tr",
        ]
        primera = subprocess.run(comando, capture_output=True, timeout=20)
        self.assertEqual(primera.returncode, 0, primera.stderr.decode())
        entrada = json.loads(primera.stdout)
        self.assertEqual(
            entrada,
            {
                "comando": ["py", "tr", "send"],
                "conciliacion": ["py", "tr", "check"],
                "idempotente": True,
            },
        )
        doc = json.loads((base / "canal.json").read_text(encoding="utf-8"))
        self.assertEqual(doc["telegram"], original["telegram"])
        self.assertEqual(doc["hermes"], original["hermes"])
        self.assertEqual(doc["mausbot"], entrada)
        antes = (base / "canal.json").read_bytes()
        segunda = subprocess.run(comando, capture_output=True, timeout=20)
        self.assertEqual(segunda.returncode, 0, segunda.stderr.decode())
        self.assertEqual((base / "canal.json").read_bytes(), antes)

    def test_parar_sin_parada_demostrada_rechaza_a_mausbot(self):
        reg = Registro(self.base)
        self.addCleanup(reg.close)
        recibo = directores.encargar(
            reg, "Haz algo", "clave-1", "2026-01-01T00:00:00Z",
            "mausbot", "mausbot", "durable",
        )
        salida = directores.relay(
            reg, recibo["encargo"], "parar", "parar-1",
            "2026-01-01T00:00:01Z", "mausbot",
        )
        self.assertFalse(salida["parada_ofrecida"])
        self.assertEqual(
            salida["motivo"],
            "MausBot no tiene parada demostrada; el trabajo sigue",
        )


def _limpiar(tmp: Path) -> None:
    import shutil

    shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
