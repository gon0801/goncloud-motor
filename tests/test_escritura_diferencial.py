"""La escritura diferencial detecta cambios que el corpus de plegado no ve."""

from __future__ import annotations

import json
import io
import itertools
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from types import SimpleNamespace
from unittest import mock


RAIZ = Path(__file__).resolve().parents[1]
HERRAMIENTA = RAIZ / "tools" / "escritura_diferencial.py"


class EscrituraDiferencial(unittest.TestCase):
    def setUp(self) -> None:
        temporal = tempfile.TemporaryDirectory()
        self.addCleanup(temporal.cleanup)
        self.raiz = Path(temporal.name)
        self.corpus = self.raiz / "corpus"
        registros = self.corpus / "registros"
        registros.mkdir(parents=True)
        eventos = [
            ("entrega", {"id": "ejemplo", "clave": "k", "huella": "h", "texto": "pedido"}),
            ("fases", {"fases": [
                {"id": "a", "resultado": "primero"},
                {"id": "b", "resultado": "segundo"},
            ]}),
            ("ajuste", {"texto": "corrección secreta"}),
            ("comprobacion", {
                "clase": "confirmado", "evidencia": "evidencia secreta",
                "fase": "a", "ajustes_vistos": 0,
            }),
            ("solicitud", {
                "id": "s", "fase": "b", "intento": "i", "operacion": "leer",
            }),
        ]
        with sqlite3.connect(registros / "uno.sqlite") as db:
            db.execute(
                "CREATE TABLE events (id INTEGER PRIMARY KEY, ts TEXT,"
                " encargo TEXT, kind TEXT, data TEXT, ukey TEXT UNIQUE)"
            )
            db.executemany(
                "INSERT INTO events VALUES (?, '', 'ejemplo', ?, ?, ?)",
                [(i, kind, json.dumps(data), f"{kind}:{i}")
                 for i, (kind, data) in enumerate(eventos, 1)],
            )
        (self.corpus / "meta.json").write_text(
            json.dumps({"eventos": len(eventos)}), encoding="utf-8",
        )

    def correr(self, *opciones: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(HERRAMIENTA), "--base", "HEAD",
             "--corpus", str(self.corpus), "--max-casos", "900",
             "--estado-legado", *opciones],
            cwd=RAIZ, capture_output=True, text=True, check=False,
        )

    def correr_con_tope_normal(self, *opciones: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(HERRAMIENTA), "--base", "HEAD",
             "--corpus", str(self.corpus), *opciones],
            cwd=RAIZ, capture_output=True, text=True, check=False,
        )

    def mutar(self, original: str, mutado: str, modulo: str) -> Path:
        arbol = self.raiz / "mutado"
        shutil.copytree(RAIZ / "motor", arbol / "motor")
        archivo = arbol / "motor" / "encargos" / "reductor" / modulo
        contenido = archivo.read_text(encoding="utf-8")
        self.assertIn(original, contenido)
        archivo.write_text(contenido.replace(original, mutado, 1), encoding="utf-8")
        return arbol

    def test_misma_escritura_pasa_con_casos_acotados(self) -> None:
        hecho = self.correr("--kinds", "comprobacion,solicitud")
        self.assertEqual(hecho.returncode, 0, hecho.stderr + hecho.stdout)
        self.assertIn("comprobacion", hecho.stdout)
        self.assertIn("solicitud", hecho.stdout)
        self.assertRegex(hecho.stdout, r"cubiertos \d+ de \d+ pares firma×kind")
        self.assertIn("diferencias: 0", hecho.stdout)
        self.assertNotIn("corrección secreta", hecho.stdout)
        self.assertNotIn("evidencia secreta", hecho.stdout)

    def test_estado_legado_ignora_solo_la_representacion_interna(self) -> None:
        arbol = self.raiz / "mutado"
        shutil.copytree(RAIZ / "motor", arbol / "motor")
        archivo = arbol / "motor/encargos/encargo.py"
        contenido = archivo.read_text(encoding="utf-8")
        original = "@dataclass(frozen=True)\nclass Encargo:"
        self.assertIn(original, contenido)
        archivo.write_text(
            contenido.replace(
                original, "@dataclass(frozen=True, repr=False)\nclass Encargo:", 1,
            ),
            encoding="utf-8",
        )
        opciones = ("--kinds", "entrega", "--arbol", str(arbol))
        legado = self.correr(*opciones)
        self.assertEqual(legado.returncode, 0, legado.stderr + legado.stdout)
        self.assertIn("diferencias: 0", legado.stdout)

        crudo = self.correr_con_tope_normal("--max-casos", "900", *opciones)
        self.assertEqual(crudo.returncode, 1, crudo.stderr + crudo.stdout)
        self.assertIn("estado=", crudo.stdout)

    def test_el_tope_primero_cubre_todos_los_pares(self) -> None:
        from tools.escritura_diferencial import _firma_estado
        from tools.plegado import cargar_reductor, leer_encargos, ultimo_evento

        reductor = cargar_reductor(RAIZ)
        firmas = set()
        for ruta in (self.corpus / "registros").glob("*.sqlite"):
            for _, eventos in leer_encargos(ruta, ultimo_evento(ruta)):
                estado = None
                for evento in eventos:
                    estado = reductor.aplicar(estado, evento)
                    firmas.add(_firma_estado(estado))
        pares = len(firmas) * 2
        hecho = self.correr(
            "--kinds", "comprobacion,solicitud", "--max-casos", str(pares),
        )
        self.assertEqual(hecho.returncode, 0, hecho.stderr + hecho.stdout)
        self.assertIn(f"cubiertos {pares} de {pares} pares firma×kind", hecho.stdout)

    def test_segunda_solicitud_duplicada_se_detecta_por_omision(self) -> None:
        with sqlite3.connect(self.corpus / "registros/uno.sqlite") as db:
            db.execute("UPDATE events SET id = 6 WHERE id = 5")
            db.execute(
                "INSERT INTO events VALUES (5, '', 'ejemplo', 'intento_abierto',"
                " '{\"id\":\"i\",\"pid\":1,\"inicio\":\"t\"}', 'intento:5')",
            )
            db.execute(
                "INSERT INTO events VALUES (7, '', 'ejemplo', 'solicitud', ?, 'solicitud:7')",
                (json.dumps({
                    "id": "s2", "fase": "b", "intento": "i", "operacion": "leer",
                }),),
            )
        (self.corpus / "meta.json").write_text('{"eventos": 7}', encoding="utf-8")
        arbol = self.mutar(
            "if any(s.id == ident for s in e.solicitudes):",
            "if e.solicitudes and e.solicitudes[0].id == ident:",
            "validacion_necesidades.py",
        )
        hecho = self.correr_con_tope_normal(
            "--kinds", "solicitud", "--arbol", str(arbol),
        )
        self.assertEqual(hecho.returncode, 1, hecho.stderr + hecho.stdout)
        self.assertIn("base_rechaza_arbol_acepta=", hecho.stdout)
        self.assertNotIn("s2", hecho.stdout)

    def test_cambio_del_orden_de_guardias_detecta_escritura_aceptada(self) -> None:
        arbol = self.mutar(
            "if (disponible is not None or pendientes and queda_trabajo(fases)",
            "if False and (disponible is not None or pendientes and queda_trabajo(fases)",
            "validacion_esperas.py",
        )
        hecho = self.correr("--kinds", "comprobacion", "--arbol", str(arbol))
        self.assertEqual(hecho.returncode, 1, hecho.stderr + hecho.stdout)
        self.assertIn("base_acepta_arbol_rechaza", hecho.stdout)
        self.assertIn("comprobacion", hecho.stdout)
        self.assertNotIn("corrección secreta", hecho.stdout)

    def test_cambio_de_mensaje_detecta_rechazo_sin_publicarlo(self) -> None:
        arbol = self.mutar(
            "la solicitud necesita id", "mensaje privado cambiado",
            "validacion_necesidades.py",
        )
        hecho = self.correr("--kinds", "solicitud", "--arbol", str(arbol))
        self.assertEqual(hecho.returncode, 1, hecho.stderr + hecho.stdout)
        self.assertIn("mensaje_transicion", hecho.stdout)
        self.assertNotIn("mensaje privado cambiado", hecho.stdout)

    def test_entrega_se_prueba_con_estado_vacio(self) -> None:
        arbol = self.raiz / "mutado"
        shutil.copytree(RAIZ / "motor", arbol / "motor")
        archivo = arbol / "motor" / "encargos" / "dominio.py"
        contenido = archivo.read_text(encoding="utf-8")
        original = 'if kind == "entrega":\n        if estado is not None:'
        self.assertIn(original, contenido)
        archivo.write_text(
            contenido.replace(original, original.replace("not None", "not None or nuevo"), 1),
            encoding="utf-8",
        )
        hecho = self.correr("--kinds", "entrega", "--arbol", str(arbol))
        self.assertEqual(hecho.returncode, 1, hecho.stderr + hecho.stdout)
        self.assertIn("base_acepta_arbol_rechaza", hecho.stdout)

    def test_entrega_vacia_prueba_cambios_de_campo(self) -> None:
        arbol = self.raiz / "mutado"
        shutil.copytree(RAIZ / "motor", arbol / "motor")
        archivo = arbol / "motor/encargos/dominio.py"
        contenido = archivo.read_text(encoding="utf-8")
        self.assertIn('id=d["id"]', contenido)
        archivo.write_text(contenido.replace('id=d["id"]', 'id=d.get("id")'),
                           encoding="utf-8")
        hecho = self.correr("--kinds", "entrega", "--arbol", str(arbol))
        self.assertEqual(hecho.returncode, 1, hecho.stderr + hecho.stdout)
        self.assertIn("base_rechaza_arbol_acepta=", hecho.stdout)

    def test_cambio_del_numero_de_firmas_no_se_confunde_con_cargas(self) -> None:
        archivo = self.raiz / "mutado/motor/encargos/reductor/pedido.py"
        shutil.copytree(RAIZ / "motor", self.raiz / "mutado/motor")
        contenido = archivo.read_text(encoding="utf-8")
        original = "def ajuste(e: Encargo, d: dict, hora: str, nuevo: bool) -> Encargo:\n"
        self.assertIn(original, contenido)
        archivo.write_text(contenido.replace(original, original + "    return e\n", 1),
                           encoding="utf-8")
        hecho = self.correr("--kinds", "ajuste,comprobacion", "--arbol",
                           str(self.raiz / "mutado"))
        self.assertEqual(hecho.returncode, 1, hecho.stderr + hecho.stdout)
        self.assertIn("firmas_estado_distintas=", hecho.stdout)
        self.assertNotIn("corrección secreta", hecho.stdout)

    def test_un_tope_sin_casos_para_todos_los_kinds_es_invalido(self) -> None:
        hecho = self.correr("--kinds", "comprobacion,solicitud", "--max-casos", "2")
        self.assertEqual(hecho.returncode, 2, hecho.stderr + hecho.stdout)
        self.assertIn("tope", hecho.stderr)

    def test_detecta_un_rechazo_que_ahora_se_acepta(self) -> None:
        arbol = self.mutar(
            'if not ident:\n        raise TransicionIlegal("la solicitud necesita id")',
            "if not ident:\n        return",
            "validacion_necesidades.py",
        )
        hecho = self.correr("--kinds", "solicitud", "--arbol", str(arbol))
        self.assertEqual(hecho.returncode, 1, hecho.stderr + hecho.stdout)
        self.assertIn("base_rechaza_arbol_acepta", hecho.stdout)

    def test_el_hijo_emite_json_ascii_aun_si_el_estado_no_cabe_en_la_consola(self) -> None:
        from tools.escritura_diferencial import _escribir_caso

        flujo = io.BytesIO()
        with io.TextIOWrapper(flujo, encoding="ascii") as consola:
            with mock.patch.object(sys, "stdout", consola):
                _escribir_caso(SimpleNamespace(reducir=lambda *_a, **_k: "estado 中文"),
                               None, "entrega", "real", {"texto": "pedido 中文"}, False)
            consola.flush()
            self.assertIn("entrega", flujo.getvalue().decode("ascii"))

    def test_meta_ilegible_no_se_confunde_con_tope_insuficiente(self) -> None:
        (self.corpus / "meta.json").write_text("{", encoding="utf-8")
        hecho = self.correr("--kinds", "entrega")
        self.assertEqual(hecho.returncode, 2, hecho.stderr)
        self.assertIn("JSONDecodeError", hecho.stderr)
        self.assertNotIn("tope de casos insuficiente", hecho.stderr)

    def test_meta_sin_eventos_se_reporta_como_error_de_entrada(self) -> None:
        (self.corpus / "meta.json").write_text("{}", encoding="utf-8")
        hecho = self.correr("--kinds", "entrega")
        self.assertEqual(hecho.returncode, 2, hecho.stderr + hecho.stdout)
        self.assertIn("ValueError", hecho.stderr)
        self.assertNotIn("Traceback", hecho.stderr)

    def test_una_escritura_aceptada_con_otro_estado_se_detecta(self) -> None:  # no-roja-en-base: mide la cobertura del comparador ya existente
        arbol = self.mutar(
            'ajustes=e.ajustes + (d["texto"],)',
            'ajustes=e.ajustes + ("CAMBIO",)',
            "pedido.py",
        )
        hecho = self.correr("--kinds", "ajuste", "--arbol", str(arbol))
        self.assertEqual(hecho.returncode, 1, hecho.stderr + hecho.stdout)
        self.assertIn("estado=", hecho.stdout)
        self.assertNotIn("corrección secreta", hecho.stdout)

    def test_identidad_y_clase_de_rechazo_no_se_esconden(self) -> None:  # no-roja-en-base: caracteriza las categorías independientes del arreglo de UTF-8
        from tools.escritura_diferencial import _clase

        base = ["solicitud", "real", "huella", "rechaza", "KeyError", "x"]
        self.assertEqual(_clase(base, ["solicitud", "real", "otra", *base[3:]],
                                "solicitud", set()), "carga_diferente")
        self.assertEqual(_clase(base, [*base[:4], "TypeError", "x"],
                                "solicitud", set()), "tipo_rechazo")

    def test_mensaje_crudo_y_solo_transicion_declarada(self) -> None:
        from tools.escritura_diferencial import _clase

        base = ["solicitud", "real", "huella", "rechaza", "OverflowError", "antes"]
        self.assertEqual(_clase(base, [*base[:5], "despues"],
                                "solicitud", set()), "mensaje_rechazo")
        self.assertEqual(_clase(base, [*base[:4], "KeyError", "antes"],
                                "solicitud", {"solicitud"}), "tipo_rechazo")
        self.assertEqual(_clase(base, [*base[:4], "TransicionIlegal", "otro"],
                                "solicitud", {"solicitud"}), "")
        base[4] = "TransicionIlegal"
        self.assertEqual(_clase(base, [*base[:4], "KeyError", "antes"],
                                "solicitud", {"solicitud"}), "tipo_transicion")

    def test_variantes_numericas_y_pares_de_campos(self) -> None:
        from tools.escritura_diferencial import _casos_por_estado, _variantes

        estado = SimpleNamespace(
            ajustes=(), solicitudes=(), efectos=(), fase_activa="F", intento="",
        )
        casos = list(_variantes("reserva", {"generacion": 1, "fase": "F"}, estado))
        for valor in (float("inf"), float("-inf"), -1, [], "no numerico"):
            self.assertTrue(any(c.get("generacion") == valor for _, c in casos))
        self.assertTrue(any(
            isinstance(c.get("generacion"), float)
            and c["generacion"] != c["generacion"] for _, c in casos
        ))
        pares = list(_variantes(
            "comprobacion", {"clase": "confirmado", "fase": "F",
                            "intento": "i", "evidencia": "comprobado"}, estado,
        ))
        self.assertTrue(any(c.get("evidencia") == "" and c.get("fase") == "otra_fase"
                            for _, c in pares))
        self.assertTrue(any(c.get("evidencia") == "" and c.get("intento") == "otro_intento"
                            for _, c in pares))
        muestras = {"reserva": [{"generacion": 1, "fase": "F"}]}
        programadas = {
            variante
            for indice in range(120)
            for _, variante, _ in itertools.islice(
                _casos_por_estado(["reserva"], muestras, estado, indice, indice), 2,
            )
        }
        self.assertTrue({
            "generacion:infinito", "generacion:nan", "par:fase:generacion",
        } <= programadas)

    def test_tope_rota_variantes_tardias_entre_firmas(self) -> None:
        from tools.escritura_diferencial import _casos_por_estado

        estado = SimpleNamespace(
            ajustes=(), solicitudes=(), efectos=(), fase_activa="F", intento="",
        )
        muestras = {"reserva": [{
            "generacion": 1, "fase": "F", "ajustes_vistos": 0, "id": "s",
        }]}
        vistos = set()
        for indice in range(120):
            casos = itertools.islice(
                _casos_por_estado(["reserva"], muestras, estado, indice, 120), 3,
            )
            vistos.update(variante for _, variante, _ in casos)
        self.assertIn("falta:generacion", vistos)
        self.assertIn("fase:ajeno", vistos)
        self.assertIn("ajustes_vistos:mas", vistos)

    def test_compara_por_identidad_aun_si_cambia_el_orden_de_las_salidas(self) -> None:
        from tools.escritura_diferencial import _comparar

        base = self.raiz / "base.jsonl"
        arbol = self.raiz / "arbol.jsonl"
        cabecera = json.dumps(["_firmas", 2]) + "\n"
        primero = ["solicitud", "1:real", "huella-a", "rechaza", "KeyError", "secreto"]
        segundo = ["comprobacion", "2:vacio", "huella-b", "rechaza", "TypeError", "secreto"]
        base.write_text(cabecera + json.dumps(primero) + "\n" + json.dumps(segundo) + "\n",
                        encoding="utf-8")
        arbol.write_text(cabecera + json.dumps(segundo) + "\n" + json.dumps(primero) + "\n",
                         encoding="utf-8")
        salida = StringIO()
        with redirect_stdout(salida):
            self.assertEqual(_comparar(base, arbol, ["solicitud", "comprobacion"], set()), 0)
        self.assertIn("diferencias: 0", salida.getvalue())
        self.assertNotIn("secreto", salida.getvalue())

        arbol.write_text(cabecera + json.dumps(segundo) + "\n"
                         + json.dumps(["solicitud", "3:real", *primero[2:]]) + "\n",
                         encoding="utf-8")
        salida = StringIO()
        with redirect_stdout(salida):
            self.assertEqual(_comparar(base, arbol, ["solicitud", "comprobacion"], set()), 1)
        self.assertIn("casos_distintos=", salida.getvalue())
        self.assertNotIn("carga_diferente=", salida.getvalue())

    def test_mensaje_de_solicitud_duplicada_se_detecta_con_tope_normal(self) -> None:
        with sqlite3.connect(self.corpus / "registros/uno.sqlite") as db:
            db.execute("UPDATE events SET id = 3006 WHERE id = 5")
            db.executemany(
                "INSERT INTO events VALUES (?, '', 'ejemplo', 'proveedor_disponible',"
                " '{\"agente\":\"nadie\"}', ?)",
                [(i, f"relleno:{i}") for i in range(5, 3006)],
            )
        (self.corpus / "meta.json").write_text(
            json.dumps({"eventos": 3006}), encoding="utf-8",
        )
        arbol = self.mutar(
            "la solicitud {ident} ya existe", "mensaje privado diferente",
            "validacion_necesidades.py",
        )
        hecho = self.correr_con_tope_normal(
            "--kinds", "comprobacion,solicitud", "--arbol", str(arbol),
        )
        self.assertEqual(hecho.returncode, 1, hecho.stderr + hecho.stdout)
        self.assertIn("mensaje_transicion=", hecho.stdout)
        self.assertNotIn("mensaje privado diferente", hecho.stdout)

    def test_prioridad_de_evidencia_detecta_dos_campos_rotos(self) -> None:
        archivo = (RAIZ / "motor/encargos/reductor/validacion_esperas.py").read_text(
            encoding="utf-8",
        )
        guardia = ('    if not str(d.get("evidencia") or ""):\n'
                   '        raise TransicionIlegal("confirmado sin evidencia")\n')
        self.assertIn(guardia, archivo)
        seccion = archivo[archivo.index("def validar_comprobacion("):]
        original = seccion[:seccion.index("    if e.fase_actual is None:")]
        cambiado = original.replace(guardia, "").replace(
            '    if intento and e.intento and intento != e.intento:\n'
            '        raise TransicionIlegal("el cierre pertenece a otro intento")\n',
            '    if intento and e.intento and intento != e.intento:\n'
            '        raise TransicionIlegal("el cierre pertenece a otro intento")\n' + guardia,
        )
        arbol = self.mutar(original, cambiado, "validacion_esperas.py")
        hecho = self.correr_con_tope_normal(
            "--kinds", "comprobacion,solicitud", "--arbol", str(arbol),
        )
        self.assertEqual(hecho.returncode, 1, hecho.stderr + hecho.stdout)
        self.assertIn("mensaje_transicion=", hecho.stdout)


if __name__ == "__main__":
    unittest.main()
