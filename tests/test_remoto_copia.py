"""Primer uso real de `remoto.leer` con un log grande (1-nov, enc_1b8916e8a0bac627):
el log de la batería pesaba 486 KB y al agente solo le llegaron los primeros 64 KB
en `texto`. La copia entera se quedó en la carpeta del host, que la jaula del
agente no lee, y el operador tuvo que cortar extractos a mano.

`ci.correr` ya publicaba su log completo en `.interno/entregas/<id>/`. Estas
pruebas piden lo mismo de `remoto.leer`: el archivo completo dentro del espacio,
la copia pública comprobada contra la del host, y un campo que diga si `texto`
se recortó."""

from __future__ import annotations

import http.server
import json
import os
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

from motor.encargos import capacidades as cap
from motor.encargos import necesidades as nec
from motor.encargos.dominio import Solicitud
from tests.test_respuestas_buzon import _Flujo

BATERIA = b"jaula-linux\tPrueba\t2026-11-01T10:00:00Z test_algo ... ok\n" * 9000
CORTO = b"v1\n"


def _solicitud(carpeta: Path) -> Solicitud:
    return Solicitud(
        id="rem-1", operacion=cap.REMOTO, recurso="ci-github", fase="f", intento="i",
        generacion=1, conductor="", carpeta=str(carpeta),
    )


class _Base(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.raiz = Path(tmp.name).resolve()
        self.origen = self.raiz / "ci"
        self.origen.mkdir()
        self.destino = self.raiz / "ejecucion" / "herramientas" / "rem-1"
        self.espacio = self.raiz / "base" / "espacio" / "enc_x"
        self.espacio.mkdir(parents=True)
        self.directorio = self.raiz / "ejecucion"
        self.recursos = {"ci-github": {"tipo": "remoto", "raiz": str(self.origen)}}

    def leer(self, nombre: str, contenido: bytes) -> dict:
        (self.origen / nombre).write_bytes(contenido)
        return cap.ejecutar(
            self.recursos, cap.REMOTO, "ci-github", {"ruta": nombre}, self.destino
        )


class LaLecturaDiceSiRecorto(_Base):
    def test_un_archivo_de_mas_de_64_kb_trae_64_kb_en_texto_y_lo_declara(self):
        self.assertGreater(len(BATERIA), 486 * 1024)
        r = self.leer("bateria.log", BATERIA)
        datos = r["datos"]
        self.assertEqual(r["estado"], "ok")
        self.assertEqual(datos["bytes"], len(BATERIA))
        self.assertEqual(len(datos["texto"]), cap.MAX_TEXTO)
        self.assertEqual(datos["texto"], BATERIA.decode()[: cap.MAX_TEXTO])
        self.assertIs(datos["texto_recortado"], True)
        self.assertEqual(Path(datos["copia_host"]).read_bytes(), BATERIA)

    def test_un_archivo_corto_llega_entero_y_no_se_declara_recortado(self):
        datos = self.leer("indice.txt", CORTO)["datos"]
        self.assertEqual(datos["texto"], CORTO.decode())
        self.assertIs(datos["texto_recortado"], False)

    def test_justo_en_el_limite_no_se_recorta(self):
        datos = self.leer("justo.txt", b"a" * cap.MAX_TEXTO)["datos"]
        self.assertEqual(len(datos["texto"]), cap.MAX_TEXTO)
        self.assertIs(datos["texto_recortado"], False)

    def test_lo_que_no_es_texto_no_llega_en_texto_y_cuenta_como_recortado(self):
        datos = self.leer("binario.bin", b"\xff\xfe\x00" * 10)["datos"]
        self.assertEqual(datos["texto"], "")
        self.assertIs(datos["texto_recortado"], True)

    def test_el_tope_de_bytes_sigue_siendo_el_de_hoy(self):
        (self.origen / "enorme.log").write_bytes(b"x" * (cap.MAX_BYTES + 1))
        r = cap.ejecutar(
            self.recursos, cap.REMOTO, "ci-github", {"ruta": "enorme.log"}, self.destino
        )
        self.assertEqual(r["estado"], "error")
        self.assertIn(str(cap.MAX_BYTES), r["motivo"])
        self.assertEqual(cap.MAX_BYTES, 8 * 1024 * 1024)
        self.assertEqual(cap.MAX_TEXTO, 64 * 1024)

    def test_por_http_tambien_declara_el_recorte(self):
        class Manejador(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(200)
                self.send_header("Content-Type", "text/plain")
                self.send_header("Content-Length", str(len(BATERIA)))
                self.end_headers()
                self.wfile.write(BATERIA)

            def log_message(self, *args):
                pass

        sin_proxy = mock.patch.dict(os.environ, {"NO_PROXY": "127.0.0.1", "no_proxy": "127.0.0.1"})
        sin_proxy.start()
        self.addCleanup(sin_proxy.stop)
        servidor = http.server.HTTPServer(("127.0.0.1", 0), Manejador)
        hilo = threading.Thread(target=servidor.serve_forever, daemon=True)
        hilo.start()
        self.addCleanup(hilo.join, 5)
        self.addCleanup(servidor.shutdown)
        recursos = {
            "ci-http": {
                "tipo": "remoto",
                "transporte": "http",
                "url": f"http://127.0.0.1:{servidor.server_port}/api",
            }
        }
        datos = cap.ejecutar(
            recursos, cap.REMOTO, "ci-http", {"ruta": "bateria.log"}, self.destino
        )["datos"]
        self.assertIs(datos["texto_recortado"], True)
        self.assertEqual(len(datos["texto"]), cap.MAX_TEXTO)
        self.assertEqual(Path(datos["copia_host"]).read_bytes(), BATERIA)


class ElSupervisorPublicaElArchivoCompleto(_Base):
    def publicar(self, nombre: str = "bateria.log", contenido: bytes = BATERIA):
        r = self.leer(nombre, contenido)
        sol = _solicitud(self.destino)
        return r, sol, nec._publicar_snapshot(self.espacio, sol, r, self.directorio)

    def test_un_archivo_remoto_de_mas_de_64_kb_llega_completo_al_espacio(self):
        r, _, publicado = self.publicar()
        copia = Path(publicado["datos"]["copia"])
        esperada = self.espacio / ".interno" / "entregas" / "rem-1" / "bateria.log"
        self.assertEqual(copia, esperada)
        self.assertTrue(copia.is_relative_to(self.espacio))
        self.assertEqual(copia.read_bytes(), BATERIA)
        self.assertEqual(publicado["datos"]["copia_host"], r["datos"]["copia_host"])
        self.assertNotEqual(publicado["datos"]["copia"], publicado["datos"]["copia_host"])
        self.assertEqual(len(publicado["datos"]["texto"]), cap.MAX_TEXTO)
        self.assertIs(publicado["datos"]["texto_recortado"], True)

    def test_un_archivo_corto_tambien_se_publica(self):
        _, _, publicado = self.publicar("indice.txt", CORTO)
        self.assertEqual(Path(publicado["datos"]["copia"]).read_bytes(), CORTO)
        self.assertIs(publicado["datos"]["texto_recortado"], False)

    def test_una_copia_publica_alterada_se_repara_con_la_del_host(self):
        r, sol, publicado = self.publicar()
        copia = Path(publicado["datos"]["copia"])
        copia.write_bytes(BATERIA[:-1] + b"X")
        self.assertEqual(len(copia.read_bytes()), len(BATERIA))

        reparado = nec._publicar_snapshot(self.espacio, sol, r, self.directorio)

        self.assertEqual(reparado["estado"], "ok")
        self.assertEqual(Path(reparado["datos"]["copia"]).read_bytes(), BATERIA)

    def test_una_copia_publica_parcial_se_repara(self):
        r, sol, publicado = self.publicar()
        Path(publicado["datos"]["copia"]).write_bytes(BATERIA[:1000])

        reparado = nec._publicar_snapshot(self.espacio, sol, r, self.directorio)

        self.assertEqual(Path(reparado["datos"]["copia"]).read_bytes(), BATERIA)

    def test_una_copia_alterada_se_detecta_al_reconstruir_la_respuesta(self):
        r, sol, publicado = self.publicar()
        (self.destino / "resultado.json").write_text(json.dumps(publicado))
        copia = Path(publicado["datos"]["copia"])
        copia.write_bytes(BATERIA[:-1] + b"X")

        doc = nec._respuesta_de_registro(self.espacio, sol, self.directorio)

        self.assertEqual(doc["datos"]["copia"], "")
        self.assertEqual(
            doc["datos"]["copia_invalido"], "la copia pública no coincide con la del host"
        )

    def test_una_copia_intacta_se_entrega_tal_cual_al_reconstruir_la_respuesta(self):
        _, sol, publicado = self.publicar()
        (self.destino / "resultado.json").write_text(json.dumps(publicado))

        doc = nec._respuesta_de_registro(self.espacio, sol, self.directorio)

        self.assertEqual(doc["datos"]["copia"], publicado["datos"]["copia"])
        self.assertNotIn("copia_invalido", doc["datos"])

    def test_sin_copia_del_host_no_se_da_por_buena_la_del_espacio(self):
        r = self.leer("bateria.log", BATERIA)
        sol = _solicitud(self.destino)
        publicado = nec._publicar_snapshot(self.espacio, sol, r, self.directorio)
        publico = Path(publicado["datos"]["copia"])
        sin_host = {
            **publicado,
            "datos": {**publicado["datos"], "copia_host": "", "copia": str(publico)},
        }

        r2 = nec._publicar_snapshot(self.espacio, sol, sin_host, self.directorio)

        self.assertEqual(r2["estado"], "error")

    def test_un_archivo_llamado_datos_json_no_choca_con_la_respuesta_larga(self):
        r, sol, publicado = self.publicar("datos.json", b"{}" * 10)
        self.assertEqual(Path(publicado["datos"]["copia"]).name, "remoto-datos.json")

    def test_la_operacion_publica_su_archivo_como_las_otras_dos(self):
        self.assertEqual(set(cap.CON_ARCHIVO), {cap.SNAPSHOT, cap.CI, cap.REMOTO})


class ElRevisorPideUnLogGrandeYLoRecibeCompleto(_Flujo):
    def test_el_pedido_acaba_con_el_archivo_completo_dentro_del_espacio(self):
        r = self.recorrido("remoto-grande", con_recurso=True)
        (self.raiz_ci / "bateria.log").write_bytes(BATERIA)

        def como_la_herramienta(registro, enc, sol, recursos, espacio, directorio, autorizado, hora):
            destino = nec._destino_host(directorio, sol.id)
            resultado = cap.ejecutar(
                recursos, sol.operacion, sol.recurso, dict(sol.parametros), destino,
                autorizado, espacio=espacio,
            )
            return nec._completar_entrega(espacio, sol, resultado, directorio)

        with mock.patch.object(nec, "_ejecutar", side_effect=como_la_herramienta):
            ident = self.pedir(r, ruta="bateria.log")
            self.barrer(r)

        sol = next(s for s in r.fold().solicitudes if s.id == ident)
        self.assertEqual(sol.estado, "resuelta")
        datos = self.buzon(r, ident)["datos"]
        publico = Path(datos["copia"])
        self.assertTrue(publico.resolve().is_relative_to(r.espacio.resolve()))
        self.assertEqual(publico.read_bytes(), BATERIA)
        self.assertIs(datos["texto_recortado"], True)
        self.assertEqual(len(datos["texto"]), cap.MAX_TEXTO)


if __name__ == "__main__":
    unittest.main()
