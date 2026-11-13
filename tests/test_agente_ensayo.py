"""El escenario del agente de ensayo se valida entero antes del primer turno.

Un escenario mal escrito tiene que fallar al leerlo, con la ruta de la clave
que sobra o del valor que no tiene la forma, y no a media corrida del banco,
donde el error parece del motor.
"""

from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from pathlib import Path

from ops.agente_ensayo import leer_escenario


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


if __name__ == "__main__":
    unittest.main()
