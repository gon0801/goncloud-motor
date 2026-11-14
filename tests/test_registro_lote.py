"""Un lote del registro entra entero o no entra, también si el proceso muere a la mitad.

Los nombres nuevos de `registro` se leen dentro de cada prueba: con el código de
la base la prueba falla al correr, no al coleccionarse.
"""

from __future__ import annotations

import contextlib
import errno
import io
import json
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from motor.encargos import registro
from motor.encargos.dominio import reducir
from motor.encargos.registro import Registro

HORA = "2026-11-13T10:00:00Z"
OTRA_HORA = "2026-11-13T11:00:00Z"
ENCARGO = "enc_privado_123"
RAIZ = Path(__file__).resolve().parents[1]


def entrega(encargo: str = ENCARGO) -> dict:
    return {
        "kind": "entrega",
        "data": {
            "id": encargo,
            "clave": encargo,
            "huella": "h",
            "texto": "texto privado del pedido",
            "hora": HORA,
        },
    }


def nota(texto: str, hora: str = HORA, ukey: str | None = None) -> dict:
    return {
        "kind": "procedimiento",
        "data": {"nota": texto, "hora": hora},
        "ukey": ukey or f"nota:{ENCARGO}:{texto}",
    }


def lote(*eventos: dict) -> list:
    return [registro.Evento(**evento) for evento in eventos]


def plegado_de_lo_guardado(reg: Registro):
    """Lo guardado plegado como lo pliega `tools/plegado.py`, sin la caché del proceso."""
    estado = None
    for evento in reg.eventos(ENCARGO):
        estado = reducir(estado, evento, nuevo=False)
    return estado


class Base(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self._tmp.cleanup)
        self.base = Path(self._tmp.name) / "base"
        self.reg = Registro(self.base)
        self.addCleanup(self.reg.close)
        self.reg.anotar(ENCARGO, entrega(), f"entrega:{ENCARGO}")

    def reabierto(self) -> Registro:
        otro = Registro(self.base)
        self.addCleanup(otro.close)
        return otro

    def guardados(self, reg: Registro | None = None) -> list[tuple[str, dict]]:
        return [
            (ev["kind"], ev["data"])
            for ev in (reg or self.reg).eventos(ENCARGO)
            if ev["kind"] != "entrega"
        ]


class Contrato(Base):
    def test_un_lote_nuevo_entra_completo_en_orden_y_con_el_estado_proyectado(self):
        eventos = lote(nota("uno"), nota("dos"), nota("tres"))
        proyectado = self.reg.proyectar(ENCARGO, eventos)
        self.assertEqual(self.guardados(self.reabierto()), [], "proyectar no escribe")

        self.assertEqual(
            self.reg.append_many(ENCARGO, eventos), ("nuevo", "nuevo", "nuevo")
        )

        self.assertEqual(
            self.guardados(self.reabierto()),
            [("procedimiento", {"nota": t, "hora": HORA}) for t in ("uno", "dos", "tres")],
        )
        self.assertEqual(plegado_de_lo_guardado(self.reabierto()), proyectado)
        self.assertEqual(self.reg.fold(ENCARGO), proyectado)

    def test_el_mismo_lote_repetido_da_igual_y_no_escribe(self):
        self.reg.append_many(ENCARGO, lote(nota("uno"), nota("dos")))
        guardado = self.guardados()

        repetido = lote(nota("uno", OTRA_HORA), nota("dos", OTRA_HORA))

        self.assertEqual(self.reg.append_many(ENCARGO, repetido), ("igual", "igual"))
        self.assertEqual(self.guardados(self.reabierto()), guardado)

    def test_una_ukey_que_guarda_otro_hecho_lanza_y_no_escribe_nada(self):
        self.reg.append_many(ENCARGO, lote(nota("uno")))
        guardado = self.guardados()
        ocupada = nota("otro hecho", ukey=f"nota:{ENCARGO}:uno")

        with self.assertRaises(registro.ClaveOcupada) as rechazo:
            self.reg.append_many(ENCARGO, lote(nota("antes"), ocupada, nota("despues")))

        self.assertIsInstance(rechazo.exception, registro.Rechazo)
        self.assertEqual(rechazo.exception.indice, 1)
        self.assertEqual(rechazo.exception.ukey, f"nota:{ENCARGO}:uno")
        self.assertEqual(self.guardados(self.reabierto()), guardado)

    def test_un_evento_que_el_reductor_rechaza_lanza_con_su_indice_y_no_escribe_nada(self):
        segunda_entrega = {**entrega(), "ukey": f"entrega:{ENCARGO}:otra"}

        with self.assertRaises(registro.EventoIlegal) as rechazo:
            self.reg.append_many(
                ENCARGO, lote(nota("antes"), segunda_entrega, nota("despues"))
            )

        self.assertIsInstance(rechazo.exception, registro.Rechazo)
        self.assertEqual(rechazo.exception.indice, 1)
        self.assertEqual(rechazo.exception.kind, "entrega")
        self.assertEqual(self.guardados(self.reabierto()), [])

    def test_el_indice_del_evento_ilegal_es_el_del_lote_aunque_otros_ya_esten(self):
        self.reg.append_many(ENCARGO, lote(nota("uno")))
        segunda_entrega = {**entrega(), "ukey": f"entrega:{ENCARGO}:otra"}

        with self.assertRaises(registro.EventoIlegal) as rechazo:
            self.reg.append_many(ENCARGO, lote(nota("uno"), segunda_entrega))

        self.assertEqual(rechazo.exception.indice, 1)

    def test_un_lote_viejo_a_medias_inserta_solo_los_ausentes_en_orden(self):
        self.reg.append_many(ENCARGO, lote(nota("dos")))
        eventos = lote(nota("uno"), nota("dos"), nota("tres"))
        proyectado = self.reg.proyectar(ENCARGO, eventos)

        resultado = self.reg.append_many(ENCARGO, eventos)

        self.assertEqual(resultado, ("nuevo", "igual", "nuevo"))
        self.assertEqual(
            [d["nota"] for _k, d in self.guardados(self.reabierto())],
            ["dos", "uno", "tres"],
        )
        guardado = plegado_de_lo_guardado(self.reabierto())
        self.assertEqual(proyectado, guardado, "lo que el paso decide es lo que queda")
        self.assertEqual(self.reg.fold(ENCARGO), guardado)

    def test_proyectar_lanza_el_mismo_rechazo_que_la_escritura_y_no_escribe(self):
        self.reg.append_many(ENCARGO, lote(nota("uno")))
        ocupada = nota("otro hecho", ukey=f"nota:{ENCARGO}:uno")
        segunda_entrega = {**entrega(), "ukey": f"entrega:{ENCARGO}:otra"}

        for eventos, rechazo in (
            (lote(nota("antes"), ocupada), registro.ClaveOcupada),
            (lote(nota("uno"), segunda_entrega), registro.EventoIlegal),
        ):
            with self.subTest(rechazo=rechazo.__name__):
                with self.assertRaises(rechazo) as proyectado:
                    self.reg.proyectar(ENCARGO, eventos)
                with self.assertRaises(rechazo) as escrito:
                    self.reg.append_many(ENCARGO, eventos)
                self.assertEqual(proyectado.exception.indice, 1)
                self.assertEqual(escrito.exception.indice, 1)

        self.assertEqual([d["nota"] for _k, d in self.guardados(self.reabierto())], ["uno"])

    def test_con_el_disco_lleno_sale_el_error_de_sqlite_y_no_queda_nada(self):
        paginas = self.reg.db.execute("PRAGMA page_count").fetchone()[0]
        self.reg.db.execute(f"PRAGMA max_page_count = {paginas}")

        with self.assertRaisesRegex(sqlite3.OperationalError, "full"):
            self.reg.append_many(ENCARGO, lote(nota("uno"), nota("x" * 200_000)))

        self.assertEqual(self.guardados(self.reabierto()), [])
        self.reg.db.execute("PRAGMA max_page_count = 1073741823")
        self.assertEqual(self.reg.append_many(ENCARGO, lote(nota("uno"))), ("nuevo",))

    def test_una_ukey_repetida_dentro_del_lote_es_un_error_del_llamador(self):
        repetida = nota("otra", ukey=f"nota:{ENCARGO}:uno")

        with self.assertRaises(ValueError):
            self.reg.append_many(ENCARGO, lote(nota("uno"), repetida))

        self.assertEqual(self.guardados(self.reabierto()), [])


_HIJO = """
import json, sys, time
from pathlib import Path
from motor.encargos import registro

base, modo, encargo, eventos = sys.argv[1], sys.argv[2], sys.argv[3], json.loads(sys.argv[4])
reg = registro.Registro(Path(base))
inserts = 0

def traza(sql):
    global inserts
    if sql.startswith("INSERT INTO events"):
        inserts += 1
        if inserts == 2:
            print("entre-inserts", flush=True)
            time.sleep(120)

reg.db.set_trace_callback(traza)
if modo == "lote":
    reg.append_many(encargo, [registro.Evento(**e) for e in eventos])
else:
    for e in eventos:
        reg.anotar(encargo, {"kind": e["kind"], "data": e["data"]}, e["ukey"])
print("sin-corte", flush=True)
"""


class CorteReal(Base):
    """Un proceso que muere por `SIGKILL` justo antes del segundo `INSERT`.

    El gancho es la traza de sqlite, que corre al empezar cada sentencia: el
    primer `INSERT` ya se ejecutó y el segundo todavía no."""

    EVENTOS = [nota("uno"), nota("dos"), nota("tres")]

    def matar_entre_inserts(self, modo: str) -> None:
        hijo = subprocess.Popen(
            [sys.executable, "-c", _HIJO, str(self.base), modo, ENCARGO, json.dumps(self.EVENTOS)],
            cwd=RAIZ,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        try:
            assert hijo.stdout is not None and hijo.stderr is not None
            linea = hijo.stdout.readline().strip()
            hijo.kill()
            hijo.wait(timeout=30)
            self.assertEqual(linea, "entre-inserts", hijo.stderr.read()[-2000:])
            self.assertNotEqual(hijo.returncode, 0)
        finally:
            if hijo.poll() is None:
                hijo.kill()
                hijo.wait(timeout=30)
            for flujo in (hijo.stdout, hijo.stderr):
                if flujo is not None:
                    flujo.close()

    def test_el_arnes_ve_el_primer_evento_cuando_se_escriben_de_a_uno(self):  # no-roja-en-base: control del arnés, demuestra que el corte llega después de un INSERT ya escrito
        self.matar_entre_inserts("de-a-uno")

        self.assertEqual(
            [d["nota"] for _k, d in self.guardados(self.reabierto())], ["uno"]
        )

    def test_un_sigkill_entre_dos_insert_no_deja_ningun_evento_del_lote(self):
        self.matar_entre_inserts("lote")

        reabierto = self.reabierto()
        self.assertEqual(self.guardados(reabierto), [])
        self.assertEqual(
            reabierto.append_many(ENCARGO, lote(*self.EVENTOS)),
            ("nuevo", "nuevo", "nuevo"),
        )
        self.assertEqual(
            [d["nota"] for _k, d in self.guardados(self.reabierto())],
            ["uno", "dos", "tres"],
        )


_HIJO_CONTEO = """
import json, sys
from pathlib import Path
from motor.encargos.registro import Registro

base, encargo, anotaciones = sys.argv[1], sys.argv[2], json.loads(sys.argv[3])
reg = Registro(Path(base))
print(json.dumps([
    reg.anotar(encargo, {"kind": a["kind"], "data": a["data"]}, a["ukey"])
    for a in anotaciones
]))
"""


class ConteoEnUnProcesoNuevo(Base):
    """El conteo es del proceso: cada caso corre en uno nuevo que termina normal."""

    def anotar_en_otro_proceso(self, *anotaciones: dict) -> tuple[list[str], list[str]]:
        hijo = subprocess.run(
            [sys.executable, "-c", _HIJO_CONTEO, str(self.base), ENCARGO, json.dumps(anotaciones)],
            cwd=RAIZ,
            capture_output=True,
            text=True,
            timeout=60,
        )
        self.assertEqual(hijo.returncode, 0, hijo.stderr[-2000:])
        lineas = [linea for linea in hijo.stderr.splitlines() if linea.startswith("registro:")]
        return json.loads(hijo.stdout), lineas

    def test_cada_rechazo_callado_deja_una_linea_sin_datos_del_encargo(self):
        primera = nota("uno")
        segundas_entregas = [
            {**entrega(), "ukey": ukey}
            for ukey in (f"entrega:{ENCARGO}:otra", f"{ENCARGO}:otra", "otraentrega")
        ]

        resultados, lineas = self.anotar_en_otro_proceso(
            primera,
            nota("uno", OTRA_HORA),
            nota("otro hecho", ukey=primera["ukey"]),
            *segundas_entregas,
        )

        self.assertEqual(resultados, ["nuevo", "igual", "ajena", "ilegal", "ilegal", "ilegal"])
        self.assertEqual(
            lineas,
            [
                "registro: rechazo=ajena kind=procedimiento ukey=nota:* campos=nota veces=1",
                "registro: rechazo=ilegal kind=entrega ukey=entrega:* veces=1",
                "registro: rechazo=ilegal kind=entrega ukey=? veces=1",
                "registro: rechazo=ilegal kind=entrega ukey=? veces=1",
            ],
        )
        for privado in (ENCARGO, "privado", "otro hecho", "uno", "otra"):
            self.assertNotIn(privado, "\n".join(lineas))

    def test_un_rechazo_repetido_sale_una_vez_y_al_terminar_el_proceso_dice_las_veces_restantes(self):
        repetida = {**entrega(), "ukey": f"otra-entrega:{ENCARGO}"}

        resultados, lineas = self.anotar_en_otro_proceso(*[repetida] * 5)

        self.assertEqual(resultados, ["ilegal"] * 5)
        self.assertEqual(
            lineas,
            [
                "registro: rechazo=ilegal kind=entrega ukey=otra-entrega:* veces=1",
                "registro: rechazo=ilegal kind=entrega ukey=otra-entrega:* veces=4",
            ],
        )

    def test_un_kind_o_una_clase_de_ukey_sin_tope_sale_como_signo(self):
        larga = nota("uno", ukey="b" * 5000 + ":1")

        resultados, lineas = self.anotar_en_otro_proceso(
            {"kind": "a" * 5000, "data": {"hora": HORA}, "ukey": "corta:1"},
            larga,
            nota("otro hecho", ukey=larga["ukey"]),
        )

        self.assertEqual(resultados, ["ilegal", "nuevo", "ajena"])
        self.assertEqual(
            lineas,
            [
                "registro: rechazo=ilegal kind=? ukey=corta:* veces=1",
                "registro: rechazo=ajena kind=procedimiento ukey=? campos=nota veces=1",
            ],
        )


class _FlujoRoto(io.TextIOBase):
    def __init__(self) -> None:
        self.intentos = 0

    def write(self, texto: str) -> int:
        self.intentos += 1
        raise OSError(errno.EIO, "stderr roto")


class ConteoDeMejorEsfuerzo(Base):
    """Las clases de ukey son solo de esta prueba: ningún rechazo anterior del
    proceso las calla, así que el conteo sí intenta escribir."""

    def test_un_stderr_roto_o_cerrado_no_cambia_lo_que_responde_append(self):
        primera = nota("uno", ukey="conteo-flujo-roto:1")
        self.assertEqual(self.reg.anotar(ENCARGO, primera, primera["ukey"]), "nuevo")
        roto = _FlujoRoto()
        cerrado = io.StringIO()
        cerrado.close()

        with contextlib.redirect_stderr(roto):
            self.assertEqual(
                self.reg.anotar(ENCARGO, nota("otro hecho"), primera["ukey"]), "ajena"
            )
        with contextlib.redirect_stderr(cerrado):
            self.assertEqual(
                self.reg.anotar(ENCARGO, entrega(), "conteo-flujo-cerrado:1"), "ilegal"
            )

        self.assertEqual(roto.intentos, 1, "el conteo intentó escribir su línea")


class IntervaloDelConteo(Base):
    """El reloj del conteo se reemplaza para recorrer diez minutos sin esperarlos."""

    def setUp(self):
        super().setUp()
        self.ahora = 1000.0
        parche = mock.patch.object(
            registro, "_CONTEO", registro._Conteo(reloj=lambda: self.ahora)
        )
        parche.start()
        self.addCleanup(parche.stop)

    def test_un_rechazo_de_cada_vuelta_sale_una_vez_cada_diez_minutos_con_las_veces_desde_la_anterior(self):
        repetida = f"otra-entrega:{ENCARGO}"
        err = io.StringIO()

        with contextlib.redirect_stderr(err):
            for _vuelta in range(31):
                self.assertEqual(self.reg.anotar(ENCARGO, entrega(), repetida), "ilegal")
                self.ahora += 20

        self.assertEqual(
            err.getvalue().splitlines(),
            [
                "registro: rechazo=ilegal kind=entrega ukey=otra-entrega:* veces=1",
                "registro: rechazo=ilegal kind=entrega ukey=otra-entrega:* veces=30",
            ],
        )


class CamposDelRechazo(IntervaloDelConteo):
    """Una ukey ocupada dice qué claves de `data` difieren, nunca sus valores.
    Lo guardado es un `procedimiento` porque el reductor lo acepta con cualquier
    otra clave junto a `nota`."""

    def guardar(self, ukey: str, data: dict) -> None:
        self.assertEqual(
            self.reg.append(ENCARGO, "procedimiento", {"nota": "n", **data}, ukey, HORA), "nuevo"
        )

    def rechazar(self, ukey: str, data: dict, encargo: str = ENCARGO, kind: str = "procedimiento"):
        self.assertEqual(self.reg.append(encargo, kind, {"nota": "n", **data}, ukey, HORA), "ajena")

    def lineas(self, accion) -> list[str]:
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            accion()
        return err.getvalue().splitlines()

    def test_nombra_las_claves_que_difieren_sin_hora_y_sin_valores(self):
        ukey = f"producto:{ENCARGO}:fase"
        self.guardar(ukey, {"fase": "f1", "sha_git": "a" * 40, "ruta": "/privada/uno", "hora": HORA})

        lineas = self.lineas(
            lambda: self.rechazar(
                ukey, {"fase": "f1", "sha_git": "b" * 40, "repo": "dueno/privado", "hora": OTRA_HORA}
            )
        )

        self.assertEqual(
            lineas,
            ["registro: rechazo=ajena kind=procedimiento ukey=producto:* campos=repo,ruta,sha_git veces=1"],
        )
        for privado in ("a" * 40, "b" * 40, "privad", "f1", ENCARGO, HORA, OTRA_HORA, "hora"):
            self.assertNotIn(privado, lineas[0])

    def test_otro_encargo_u_otro_tipo_en_la_ukey_se_nombran_aparte_de_data(self):
        ukey = f"producto:{ENCARGO}:fase"
        self.guardar(ukey, {"fase": "f1"})

        lineas = self.lineas(
            lambda: (
                self.rechazar(ukey, {"fase": "f1"}, encargo="enc_otro"),
                self.rechazar(ukey, {"fase": "f1"}, kind="producto_listo"),
            )
        )

        self.assertEqual(
            lineas,
            [
                "registro: rechazo=ajena kind=procedimiento ukey=producto:* campos=@encargo veces=1",
                "registro: rechazo=ajena kind=producto_listo ukey=producto:* campos=@kind veces=1",
            ],
        )

    def test_mas_de_cinco_claves_distintas_salen_las_cinco_primeras_y_cuantas_faltan(self):
        ukey = f"entrega:{ENCARGO}:externa"
        nombres = ("uno", "dos", "tres", "cuatro", "cinco", "seis", "siete")
        self.guardar(ukey, {nombre: 1 for nombre in nombres})

        lineas = self.lineas(lambda: self.rechazar(ukey, {nombre: 2 for nombre in nombres}))

        self.assertEqual(
            lineas,
            ["registro: rechazo=ajena kind=procedimiento ukey=entrega:* campos=cinco,cuatro,dos,seis,siete,+2 veces=1"],
        )

    def test_cada_combinacion_de_campos_cuenta_aparte_dentro_del_intervalo(self):
        ukey = f"entrega:{ENCARGO}:externa"
        self.guardar(ukey, {"intento": 1, "sha_git": "a"})

        def rechazar(data: dict, veces: int) -> None:
            for _vez in range(veces):
                self.rechazar(ukey, data)
                self.ahora += 20

        lineas = self.lineas(
            lambda: (
                rechazar({"intento": 2, "sha_git": "a"}, 3),
                rechazar({"intento": 1, "sha_git": "b"}, 2),
                registro._CONTEO.vaciar(),
            )
        )

        self.assertEqual(
            lineas,
            [
                "registro: rechazo=ajena kind=procedimiento ukey=entrega:* campos=intento veces=1",
                "registro: rechazo=ajena kind=procedimiento ukey=entrega:* campos=sha_git veces=1",
                "registro: rechazo=ajena kind=procedimiento ukey=entrega:* campos=intento veces=2",
                "registro: rechazo=ajena kind=procedimiento ukey=entrega:* campos=sha_git veces=1",
            ],
        )

    def test_un_lote_que_omite_el_evento_cuenta_la_misma_linea_que_append(self):
        ukey = f"entrega:{ENCARGO}:externa"
        self.guardar(ukey, {"intento": 1, "hora": HORA})
        evento = registro.Evento("procedimiento", {"nota": "n", "intento": 2, "hora": OTRA_HORA}, ukey)
        with self.assertRaises(registro.Rechazo) as rechazo:
            self.reg.proyectar(ENCARGO, [evento])

        lineas = self.lineas(lambda: registro.contar_omitidos([(evento, rechazo.exception)]))

        self.assertEqual(
            lineas,
            ["registro: rechazo=ajena kind=procedimiento ukey=entrega:* campos=intento veces=1"],
        )


if __name__ == "__main__":
    unittest.main()
