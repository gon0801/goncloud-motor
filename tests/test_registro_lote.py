"""Un lote del registro entra entero o no entra, también si el proceso muere a la mitad.

Los nombres nuevos de `registro` se leen dentro de cada prueba: con el código de
la base la prueba falla al correr, no al coleccionarse.
"""

from __future__ import annotations

import contextlib
import io
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from motor.encargos import registro
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
        antes = self.reg.fold(ENCARGO)
        eventos = lote(nota("uno"), nota("dos"), nota("tres"))

        self.assertEqual(
            self.reg.append_many(ENCARGO, eventos), ("nuevo", "nuevo", "nuevo")
        )

        self.assertEqual(
            self.guardados(self.reabierto()),
            [("procedimiento", {"nota": t, "hora": HORA}) for t in ("uno", "dos", "tres")],
        )
        proyectado = registro.proyectar(antes, eventos)
        self.assertEqual(self.reabierto().fold(ENCARGO), proyectado)
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

        resultado = self.reg.append_many(
            ENCARGO, lote(nota("uno"), nota("dos"), nota("tres"))
        )

        self.assertEqual(resultado, ("nuevo", "igual", "nuevo"))
        self.assertEqual(
            [d["nota"] for _k, d in self.guardados(self.reabierto())],
            ["dos", "uno", "tres"],
        )
        self.assertEqual(self.reabierto().fold(ENCARGO), self.reg.fold(ENCARGO))

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


class ConteoDeRechazos(Base):
    def test_cada_rechazo_callado_deja_una_linea_sin_datos_del_encargo(self):
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            primera = nota("uno")
            self.assertEqual(
                self.reg.anotar(ENCARGO, primera, primera["ukey"]), "nuevo"
            )
            self.assertEqual(
                self.reg.anotar(ENCARGO, nota("uno", OTRA_HORA), primera["ukey"]),
                "igual",
            )
            self.assertEqual(
                self.reg.anotar(ENCARGO, nota("otro hecho"), primera["ukey"]), "ajena"
            )
            for ukey in (f"entrega:{ENCARGO}:otra", f"{ENCARGO}:otra", "otraentrega"):
                self.assertEqual(self.reg.anotar(ENCARGO, entrega(), ukey), "ilegal")

        self.assertEqual(
            err.getvalue().splitlines(),
            [
                "registro: rechazo=ajena kind=procedimiento ukey=nota:*",
                "registro: rechazo=ilegal kind=entrega ukey=entrega:*",
                "registro: rechazo=ilegal kind=entrega ukey=?",
                "registro: rechazo=ilegal kind=entrega ukey=?",
            ],
        )
        for privado in (ENCARGO, "privado", "otro hecho", "uno", "otra"):
            self.assertNotIn(privado, err.getvalue())


if __name__ == "__main__":
    unittest.main()
