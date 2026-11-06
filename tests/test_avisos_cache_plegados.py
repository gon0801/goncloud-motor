"""La caché de plegados sobrevive a las pasadas del hilo de avisos."""

from __future__ import annotations

import json
import shutil
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

from motor.encargos.ciclo import recibir
from motor.encargos.dominio import reducir
from motor.encargos.registro import Registro
from motor.integraciones import directores

HORA = "2026-11-06T10:00:00Z"

TRANSPORTE = (
    "import json,sys\n"
    "sys.stdin.read()\n"
    "print(json.dumps({'recibo': 'ok'}))\n"
)


class Base(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.raiz = Path(self._tmp.name)
        self.base = self.raiz / "base"
        self.reg = Registro(self.base)
        self.addCleanup(self.reg.close)

    def encargo(self, clave: str, canal: str = "tg") -> str:
        return self.crear(self.reg, clave, HORA, canal)

    def crear(
        self, registro: Registro, clave: str, hora: str, canal: str = "tg"
    ) -> str:
        ident = recibir(
            registro, f"pedido {clave}", clave, hora, "hermes",
            canal=canal, via="directa",
        )["encargo"]
        registro.anotar(
            ident,
            {"kind": "fases", "data": {
                "fases": [{"id": "A", "resultado": "fase comprobada"}], "hora": hora,
            }},
            f"fase-{clave}",
        )
        return ident

    def transporte(self) -> None:
        guion = self.raiz / "transporte.py"
        guion.write_text(TRANSPORTE, encoding="utf-8")
        (self.base / "canal.json").write_text(
            json.dumps({
                "tg": {
                    "comando": [sys.executable, "-B", str(guion)],
                    "idempotente": True,
                }
            }),
            encoding="utf-8",
        )

    def dos_pasadas(self, contar: mock.Mock) -> int:
        self.transporte()
        self.encargo("uno")
        self.encargo("dos")
        drenador = directores.DrenadoAparte(self.base, tope_s=10.0)
        self.assertTrue(drenador.lanzar())
        drenador.esperar(20)
        self.assertEqual(drenador.ultimo_error, "")
        primera = contar.call_count
        self.assertGreater(primera, 0, "la primera pasada pliega los encargos")
        contar.reset_mock()
        self.assertTrue(drenador.lanzar(), "terminada la pasada se puede lanzar otra")
        drenador.esperar(20)
        self.assertEqual(drenador.ultimo_error, "")
        return primera


class CacheEntrePasadas(Base):
    def test_la_segunda_pasada_sin_eventos_nuevos_no_vuelve_a_plegar(self):
        with mock.patch.object(
            Registro, "_plegar", autospec=True, wraps=Registro._plegar
        ) as plegar:
            self.dos_pasadas(plegar)
            self.assertEqual(
                plegar.call_count, 0,
                "la segunda pasada reutiliza los plegados de la primera",
            )


class EventoDeOtraInstancia(Base):
    def test_lo_escrito_por_otra_instancia_entre_pasadas_aparece_en_la_siguiente(self):
        self.transporte()
        ident = self.encargo("uno")
        drenador = directores.DrenadoAparte(self.base, tope_s=10.0)
        self.assertTrue(drenador.lanzar())
        drenador.esperar(20)
        self.assertEqual(drenador.ultimo_error, "")

        otro = Registro(self.base)
        self.addCleanup(otro.close)
        otro.anotar(
            ident,
            {"kind": "procedimiento", "data": {"nota": "nueva", "hora": HORA}},
            "nota-externa",
        )

        vistos: dict[str, object] = {}
        original = Registro.fold

        def espia(self, encargo, hasta=None):
            estado = original(self, encargo, hasta)
            if encargo == ident and hasta is None:
                vistos["estado"] = estado
            return estado

        with mock.patch.object(Registro, "fold", espia):
            self.assertTrue(drenador.lanzar())
            drenador.esperar(20)
        self.assertEqual(drenador.ultimo_error, "")
        self.assertEqual(vistos["estado"].procedimiento, ("nueva",))


class FoldHastaCacheado(Base):
    def test_hasta_cacheado_entre_instancias_no_devuelve_eventos_posteriores(self):
        ident = self.encargo("uno")
        corte = self.reg.ultimo_evento(ident)
        antes = self.reg.fold(ident, hasta=corte)

        self.reg.anotar(
            ident,
            {"kind": "ajuste", "data": {"texto": "posterior", "hora": HORA}},
            "ajuste-posterior",
        )

        otro = Registro(self.base)
        self.addCleanup(otro.close)
        with mock.patch.object(
            Registro, "_plegar", autospec=True, wraps=Registro._plegar
        ) as plegar:
            despues = otro.fold(ident, hasta=corte)
            self.assertEqual(
                plegar.call_count, 0,
                "la caché de (encargo, hasta) se comparte entre instancias",
            )
        self.assertEqual(despues, antes)
        self.assertEqual(despues.ajustes, ())


class BaseRecreada(Base):
    def test_recrear_la_base_en_la_misma_ruta_no_reutiliza_la_cache(self):
        # Se fuerza la misma identidad de fichero: en Linux un inodo borrado se
        # reutiliza y la prueba debe depender de la firma del primer evento.
        with mock.patch(
            "motor.encargos.registro._identidad_del_fichero",
            return_value=(1, 1),
        ):
            ident = self.encargo("uno")
            viejo = self.reg.fold(ident)
            self.reg.close()
            shutil.rmtree(self.base)

            nuevo = Registro(self.base)
            self.addCleanup(nuevo.close)
            otra_hora = "2026-11-06T11:00:00Z"
            entrega = {
                "id": ident,
                "clave": "uno",
                "huella": "otra-huella",
                "texto": "pedido uno",
                "director": "hermes",
                "adjuntos": [],
                "restricciones": [],
                "canal": "tg",
                "via": "directa",
                "detenido": False,
                "entrega_nivel": "terminado",
                "repo": "",
                "rama": "",
                "base": "",
                "modelos_solicitados": [],
                "recibo": "rec_otro",
                "hora": otra_hora,
            }
            # Se escribe directo para no refrescar la cache al crear el encargo.
            nuevo.append(ident, "entrega", entrega, "entrega:uno", otra_hora)
            nuevo.append(
                ident,
                "fases",
                {"fases": [{"id": "A", "resultado": "fase nueva"}], "hora": otra_hora},
                "fase-uno",
                otra_hora,
            )

            actual = nuevo.fold(ident)
            self.assertEqual(actual.hora, otra_hora)
            self.assertEqual(actual.fases[0].resultado, "fase nueva")
            self.assertNotEqual(actual, viejo)


class TopeDeLaCache(Base):
    def test_la_cache_se_capa_a_la_cantidad_de_bases(self):
        import motor.encargos.registro as registro_mod

        with mock.patch.object(registro_mod, "_CACHE_MAX", 2):
            with registro_mod._CACHES_CANDADO:
                previo = registro_mod._CACHES.copy()
                registro_mod._CACHES.clear()
            try:
                for i in range(3):
                    reg = Registro(self.raiz / f"base-{i}")
                    try:
                        self.crear(reg, f"c{i}", HORA)
                    finally:
                        reg.close()
                with registro_mod._CACHES_CANDADO:
                    self.assertLessEqual(len(registro_mod._CACHES), 2)
            finally:
                with registro_mod._CACHES_CANDADO:
                    registro_mod._CACHES.clear()
                    registro_mod._CACHES.update(previo)


class FoldConcurrente(Base):
    def test_lectores_y_escritura_concurrentes_convergen(self):
        ident = self.encargo("uno")
        total = 25
        listo = threading.Barrier(3)
        fin = threading.Event()
        errores: list[BaseException] = []
        finales: list[object] = []

        def lector():
            try:
                reg = Registro(self.base)
                try:
                    listo.wait(10)
                    for _ in range(500):
                        reg.fold(ident)
                    fin.wait(10)
                    finales.append(reg.fold(ident))
                finally:
                    reg.close()
            except BaseException as exc:  # noqa: BLE001  la prueba reporta el error
                errores.append(exc)

        def escritor():
            try:
                reg = Registro(self.base)
                try:
                    listo.wait(10)
                    for i in range(total):
                        reg.anotar(
                            ident,
                            {"kind": "procedimiento", "data": {
                                "nota": f"n{i}", "hora": HORA,
                            }},
                            f"nota-concurrente-{i}",
                        )
                finally:
                    reg.close()
            except BaseException as exc:  # noqa: BLE001  la prueba reporta el error
                errores.append(exc)
            finally:
                fin.set()

        hilos = [
            threading.Thread(target=lector),
            threading.Thread(target=lector),
            threading.Thread(target=escritor),
        ]
        for hilo in hilos:
            hilo.start()
        for hilo in hilos:
            hilo.join(20)

        self.assertEqual(errores, [])
        self.assertFalse(any(hilo.is_alive() for hilo in hilos), "un hilo no terminó")

        verificador = Registro(self.base)
        self.addCleanup(verificador.close)
        esperado = None
        for evento in verificador.eventos(ident):
            esperado = reducir(esperado, evento)
        self.assertEqual(verificador.fold(ident), esperado)
        self.assertEqual(len(finales), 2)
        for estado in finales:
            self.assertEqual(estado, esperado)


if __name__ == "__main__":
    unittest.main()
