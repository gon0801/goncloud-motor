"""El escenario del agente de ensayo se valida entero antes del primer turno.

Un escenario mal escrito tiene que fallar al leerlo, con la ruta de la clave
que sobra o del valor que no tiene la forma, y no a media corrida del banco,
donde el error parece del motor.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

from ops.agente_ensayo import APAGON, INVOCACIONES, esperando, leer_escenario, suelta

AGENTE = Path(__file__).resolve().parents[1] / "ops" / "agente_ensayo.py"
ESCENARIOS = "canario-escenarios"


def _base() -> dict:
    return {
        "fases": [{"id": "uno", "resultado": "saludo.txt con hola"}],
        "producto": {"uno": {"saludo.txt": "hola\n"}},
    }


class LeerEscenario(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="escenario-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def leer(self, datos: object):
        ruta = self.tmp / "escenario.json"
        ruta.write_text(json.dumps(datos), encoding="utf-8")
        return leer_escenario(ruta)

    def rechaza(self, datos: object, donde: str) -> None:
        with self.assertRaises(ValueError) as caso:
            self.leer(datos)
        self.assertIn(donde, str(caso.exception))

    def test_fases_como_texto_se_rechaza(self):
        self.rechaza({**_base(), "fases": "uno"}, "fases")

    def test_producto_como_lista_se_rechaza(self):
        self.rechaza({**_base(), "producto": []}, "producto")

    def test_resuelve_como_texto_se_rechaza_aunque_sea_verdadero(self):
        self.rechaza({**_base(), "resuelve": "no"}, "resuelve")

    def test_una_fase_sin_fases_se_rechaza(self):
        self.rechaza({**_base(), "fases": []}, "fases")

    def test_una_clave_de_fase_que_el_motor_no_acepta_se_rechaza(self):
        datos = _base()
        datos["fases"][0]["titulo"] = "x"
        self.rechaza(datos, "fases[0]")

    def test_una_fase_sin_resultado_se_rechaza(self):
        datos = _base()
        del datos["fases"][0]["resultado"]
        self.rechaza(datos, "fases[0]")

    def test_un_id_de_fase_que_no_es_texto_se_rechaza(self):
        datos = _base()
        datos["fases"][0]["id"] = 1
        self.rechaza(datos, "fases[0].id")

    def test_un_id_de_fase_que_el_motor_rechaza_se_rechaza(self):
        for fid in ("u no", " uno", "uno/dos", ""):
            datos = _base()
            datos["fases"][0]["id"] = fid
            with self.subTest(fid=fid):
                self.rechaza(datos, "fases[0].id")

    def test_una_lane_desconocida_se_rechaza(self):
        datos = _base()
        datos["fases"][0]["lane"] = "rapida"
        self.rechaza(datos, "fases[0].lane")

    def test_requiere_despliegue_como_texto_se_rechaza(self):
        datos = _base()
        datos["fases"][0]["requiere_despliegue"] = "true"
        self.rechaza(datos, "fases[0].requiere_despliegue")

    def test_una_dependencia_que_no_es_texto_ni_objeto_se_rechaza(self):
        datos = _base()
        datos["fases"][0]["requiere"] = [1]
        self.rechaza(datos, "fases[0].requiere[0]")

    def test_una_dependencia_con_claves_de_mas_se_rechaza(self):
        datos = _base()
        datos["fases"][0]["requiere"] = [{"fase": "cero", "rama": "x"}]
        self.rechaza(datos, "fases[0].requiere[0]")

    def test_una_receta_con_un_paso_que_no_es_ruta_se_rechaza(self):
        datos = _base()
        datos["fases"][0]["despliegue"] = {"desplegar": 1}
        self.rechaza(datos, "fases[0].despliegue.desplegar")

    def test_un_archivo_del_producto_que_no_es_texto_se_rechaza(self):
        self.rechaza({**_base(), "producto": {"uno": {"saludo.txt": 1}}}, "producto.uno")

    def test_una_comprobacion_con_tope_booleano_se_rechaza(self):
        comprobacion = {"objetivo": "x", "argv": ["true"], "tope": True}
        self.rechaza(
            {**_base(), "comprobaciones": {"uno": [comprobacion]}},
            "comprobaciones.uno[0].tope",
        )

    def test_una_comprobacion_sin_argv_se_rechaza(self):
        self.rechaza(
            {**_base(), "comprobaciones": {"uno": [{"objetivo": "x", "tope": 60}]}},
            "comprobaciones.uno[0]",
        )

    def test_un_arreglo_que_no_es_objeto_se_rechaza(self):
        self.rechaza({**_base(), "arreglos": {"no cumple": "hola"}}, "arreglos.no cumple")

    def test_el_titulo_de_bloqueo_que_no_es_texto_se_rechaza(self):
        self.rechaza({**_base(), "titulo_bloqueo": ["x"]}, "titulo_bloqueo")

    def test_acepta_cada_clave_de_fase_que_el_motor_acepta(self):  # no-roja-en-base: la base no valida lo anidado y también lo acepta; la prueba guarda que la validación nueva no rechace lo que el motor admite
        datos = {
            "fases": [
                {"id": "uno", "resultado": "r1", "lane": "release",
                 "despliegue": {"desplegar": "d.sh", "checklist": "c.sh", "rollback": "r.sh"}},
                {"id": "dos", "resultado": "r2", "requiere_despliegue": None,
                 "requiere": ["uno", {"fase": "uno", "version": "v1"}, {"fase": "uno"}]},
                {"id": "tres", "resultado": "r3", "requiere_despliegue": False, "lane": "gate"},
            ],
            "producto": {"uno": {"a.txt": "a"}, "dos": {}, "tres": {"b.txt": "b"}},
            "exigido": {"uno": {"a.txt": "a"}},
            "comprobaciones": {"uno": [{"objetivo": "a", "argv": ["true"], "tope": 60}]},
            "arreglos": {"no cumple": {"a.txt": "a"}},
            "resuelve": False,
            "titulo_bloqueo": "falta a",
        }
        self.assertEqual(self.leer(datos), datos)


class EscenarioPropio(unittest.TestCase):
    """El canario deja en la base el escenario de un encargo; el de trabajo.sh queda para los demás."""

    def setUp(self):
        self.base = Path(tempfile.mkdtemp(prefix="agente-"))
        self.addCleanup(shutil.rmtree, self.base, True)

    def turno(self, encargo: str) -> Path:
        """El primer turno del responsable de `encargo`, con el escenario común en argv."""
        espacio = self.base / "espacio" / encargo
        (espacio / ".interno").mkdir(parents=True)
        intento = self.base / "ejecucion" / encargo / "intento-1"
        intento.mkdir(parents=True)
        (intento.parent / "actual").write_text("intento-1", encoding="utf-8")
        (intento / "arranque.json").write_text(json.dumps({"token": "t"}), encoding="utf-8")
        prompt = self.base / "runtime" / encargo / "t" / "prompt.md"
        prompt.parent.mkdir(parents=True)
        prompt.write_text("primer turno\n", encoding="utf-8")
        comun = self.base / "comun.json"
        comun.write_text(json.dumps(_base()), encoding="utf-8")
        subprocess.run([sys.executable, str(AGENTE), str(comun)], cwd=espacio, check=True)
        return espacio

    def test_el_escenario_propio_del_encargo_manda_sobre_el_comun(self):
        propio = {
            "fases": [{"id": "falla", "resultado": "falla.txt"}],
            "producto": {"falla": {"falla.txt": "falla\n"}},
        }
        (self.base / ESCENARIOS).mkdir()
        (self.base / ESCENARIOS / "enc_propio.json").write_text(json.dumps(propio), encoding="utf-8")

        espacio = self.turno("enc_propio")
        otro = self.turno("enc_otro")

        fases = json.loads((espacio / ".interno" / "fases.json").read_text(encoding="utf-8"))
        self.assertEqual(fases["fases"], propio["fases"])
        self.assertEqual((espacio / "falla.txt").read_text(encoding="utf-8"), "falla\n")
        self.assertFalse((espacio / "saludo.txt").exists(), "escribió el producto del escenario común")
        self.assertEqual((otro / "saludo.txt").read_text(encoding="utf-8"), "hola\n")


class CompuertaDelApagon(unittest.TestCase):
    """Con `canario-apagon/<encargo>/` en la base, el agente anota cada vez que corre y el
    responsable espera, con su producto ya escrito, a que el canario suelte el turno."""

    def setUp(self):
        self.base = Path(tempfile.mkdtemp(prefix="agente-")).resolve()
        self.addCleanup(shutil.rmtree, self.base, True)
        self.comun = self.base / "comun.json"
        self.comun.write_text(json.dumps(_base()), encoding="utf-8")

    def lanzar(self, encargo: str, *, revisor: bool = False) -> tuple[subprocess.Popen, Path]:
        espacio = self.base / "espacio" / encargo
        (espacio / ".interno").mkdir(parents=True, exist_ok=True)
        intento = self.base / "ejecucion" / encargo / ("intento-2" if revisor else "intento-1")
        intento.mkdir(parents=True)
        (intento.parent / "actual").write_text(intento.name, encoding="utf-8")
        (intento / "arranque.json").write_text(json.dumps({"token": intento.name}), encoding="utf-8")
        prompt = self.base / "runtime" / encargo / intento.name / "prompt.md"
        prompt.parent.mkdir(parents=True)
        if revisor:
            prompt.write_text("Revisa: cubre solo la fase uno:\n", encoding="utf-8")
            (intento / "revision.json").write_text(
                json.dumps({"version": 1, "resultado": str(intento / "juicio.json")}), encoding="utf-8",
            )
        else:
            prompt.write_text("primer turno\n", encoding="utf-8")
        proceso = subprocess.Popen([sys.executable, str(AGENTE), str(self.comun)], cwd=espacio)
        self.addCleanup(proceso.kill)
        return proceso, espacio

    def test_el_responsable_espera_la_compuerta_del_turno_con_su_producto_escrito(self):
        carpeta = self.base / APAGON / "enc_corte"
        carpeta.mkdir(parents=True)

        proceso, espacio = self.lanzar("enc_corte")
        marca = carpeta / esperando("turno")
        vence = time.monotonic() + 20
        while not marca.is_file() and time.monotonic() < vence:
            time.sleep(0.05)

        self.assertEqual(marca.read_text(encoding="utf-8"), str(proceso.pid))
        self.assertIsNone(proceso.poll(), "el responsable no esperó la compuerta")
        self.assertEqual((espacio / "saludo.txt").read_text(encoding="utf-8"), "hola\n")
        (carpeta / suelta("turno")).touch()
        self.assertEqual(proceso.wait(timeout=20), 0)
        self.assertEqual((carpeta / INVOCACIONES).read_text(encoding="utf-8"), "responsable\n")

        revisor, _ = self.lanzar("enc_corte", revisor=True)
        self.assertEqual(revisor.wait(timeout=20), 0)
        self.assertEqual((carpeta / INVOCACIONES).read_text(encoding="utf-8"), "responsable\nrevisor\n")

    def test_sin_su_carpeta_el_agente_ni_anota_ni_espera(self):
        (self.base / APAGON / "enc_otro").mkdir(parents=True)

        proceso, espacio = self.lanzar("enc_propio")

        self.assertEqual(proceso.wait(timeout=20), 0)
        self.assertEqual((espacio / "saludo.txt").read_text(encoding="utf-8"), "hola\n")
        self.assertFalse((self.base / APAGON / "enc_propio").exists())
        self.assertEqual(list((self.base / APAGON / "enc_otro").iterdir()), [])


if __name__ == "__main__":
    unittest.main()
