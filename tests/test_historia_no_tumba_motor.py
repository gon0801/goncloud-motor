"""Una historia guardada no vuelve a pasar por las reglas de propuesta nuevas.

El 7-nov la PC dejó de avanzar porque `enc_b4f3117f5a0e7531`, ya cancelado,
declaraba una fase fuera de la enumeración que un ajuste del director había
fijado. El evento se aceptó con las reglas de su día; una regla posterior lo
rechazó al plegar y el supervisor se cayó en cada vuelta.
"""

from __future__ import annotations

import argparse
import contextlib
import io
import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from motor.encargos import cli
from motor.encargos import ciclo as ciclo_encargos
from motor.encargos.ciclo import ahora_iso, recibir
from motor.encargos.registro import Registro
from motor.integraciones import directores

ENUMERACION = (
    "Correccion del director: las fases son exactamente 8 con ids B, C, D, E, "
    "F, G, H, I (las letras de los bloques del plan)."
)
FASES_FUERA = {
    "fases": [
        {
            "id": "a-spec-y-medicion",
            "requiere": [],
            "resultado": "La propuesta queda medida",
        },
        {
            "id": "b-receta-despliegue",
            "requiere": [],
            "resultado": "El motor despliega Orbit",
        },
    ],
    "hora": "2026-11-06T04:25:36Z",
}


def _sembrar(reg: Registro, encargo: str, kind: str, data: dict, ukey: str) -> None:
    reg.db.execute(
        "INSERT INTO events (ts, encargo, kind, data, ukey) VALUES (?,?,?,?,?)",
        (
            str(data.get("hora") or "2026-11-06T04:00:00Z"),
            encargo,
            kind,
            json.dumps(data, ensure_ascii=False, sort_keys=True),
            ukey,
        ),
    )


def _olvidar_plegados_ilegibles() -> None:
    getattr(ciclo_encargos, "_PLEGADOS_ILEGIBLES", set()).clear()


def _historial_de_la_pc(reg: Registro, encargo: str) -> None:
    """La forma de enc_b4f3117f5a0e7531, escrita como la dejó el código de entonces."""
    _sembrar(
        reg,
        encargo,
        "entrega",
        {
            "id": encargo,
            "clave": "clave-historia",
            "huella": "huella",
            "texto": "pedido con fases enumeradas",
            "director": "hermes",
            "adjuntos": [],
            "restricciones": [],
            "canal": "hermes",
            "via": "durable",
            "detenido": False,
            "entrega_nivel": "terminado",
            "repo": "",
            "rama": "",
            "base": "master",
            "modelos_solicitados": [],
            "recibo": "rec-1",
            "hora": "2026-11-06T03:50:10Z",
        },
        "historia:entrega",
    )
    _sembrar(
        reg,
        encargo,
        "ajuste",
        {
            "texto": ENUMERACION,
            "autor": "director:hermes",
            "recibo": "rec-2",
            "hora": "2026-11-06T04:09:44Z",
        },
        "historia:ajuste",
    )
    _sembrar(reg, encargo, "fases", FASES_FUERA, "historia:fases")
    _sembrar(
        reg,
        encargo,
        "orden",
        {
            "accion": "cancelar",
            "autor": "director:hermes",
            "encargo": encargo,
            "recibo": "rec-3",
            "resultado": "aplicada",
            "hora": "2026-11-06T04:56:51Z",
        },
        "historia:cancelar",
    )
    _sembrar(
        reg,
        encargo,
        "arbol",
        {"observacion": "ausente", "hora": "2026-11-06T04:56:57Z"},
        "historia:arbol",
    )


class HistoriaQueNoPasaUnaReglaNueva(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.reg = Registro(Path(self.tmp) / "base")
        self.addCleanup(self.reg.close)

    def test_la_historia_guardada_pliega_a_cancelado(self):
        encargo = "enc_b4f3117f5a0e7531"
        _historial_de_la_pc(self.reg, encargo)

        enc = self.reg.fold(encargo)

        self.assertEqual(enc.estado, "cancelado")
        self.assertEqual(
            [f.id for f in enc.fases], ["a-spec-y-medicion", "b-receta-despliegue"]
        )

    def test_el_mismo_fases_escrito_hoy_se_rechaza(self):  # no-roja-en-base: la regla ya rechazaba la propuesta; la prueba fija que el arreglo no la afloja
        encargo = recibir(
            self.reg, "pedido nuevo", "clave-nueva", ahora_iso()
        )["encargo"]
        self.reg.append(
            encargo,
            "ajuste",
            {"texto": ENUMERACION, "hora": ahora_iso()},
            "ajuste:nuevo",
            ahora_iso(),
        )

        resultado = self.reg.append(
            encargo, "fases", FASES_FUERA, "fases:nueva", ahora_iso()
        )

        self.assertEqual(resultado, "ilegal")


class SupervisorQueAisla(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.reg = Registro(Path(self.tmp) / "base")
        self.addCleanup(self.reg.close)
        hora = ahora_iso()
        self.sano = recibir(self.reg, "pedido sano", "clave-sana", hora)["encargo"]
        self.roto = "enc_roto"
        _sembrar(
            self.reg,
            self.roto,
            "entrega",
            {
                "id": self.roto,
                "clave": "clave-rota",
                "huella": "huella",
                "texto": "historia incoherente",
                "via": "durable",
                "entrega_nivel": "terminado",
                "recibo": "rec-roto",
                "hora": "2026-11-06T03:50:10Z",
            },
            "roto:entrega",
        )
        _sembrar(
            self.reg,
            self.roto,
            "fases",
            {
                "fases": [
                    {"id": "a", "requiere": [{"fase": "b", "version": ""}], "resultado": "A"},
                    {"id": "b", "requiere": [{"fase": "a", "version": ""}], "resultado": "B"},
                ],
                "hora": "2026-11-06T04:00:00Z",
            },
            "roto:fases",
        )
        self.aviso_incierto = "av_incierto_del_roto"
        avisos = self.reg.base / "avisos"
        avisos.mkdir(parents=True, exist_ok=True)
        (avisos / f"{self.aviso_incierto}.json").write_text(
            json.dumps(
                {
                    "id": self.aviso_incierto,
                    "encargo": self.roto,
                    "canal": "tg",
                    "evidencia": "avance:inicio:inicio",
                    "estado": "incierto",
                    "tipo": "avance",
                    "recibo": "",
                    "fallos": 1,
                    "proximo_intento": "1970-01-01T00:00:00Z",
                }
            ),
            encoding="utf-8",
        )
        _olvidar_plegados_ilegibles()
        self.addCleanup(_olvidar_plegados_ilegibles)

    def _supervisor(self, agente: str) -> None:
        args = argparse.Namespace(
            base=self.reg.base,
            una_vez=True,
            intervalo=0.0,
            umbral=30.0,
            agente=agente,
            respaldo=[],
        )
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(cli._supervisor(self.reg, args), 0)

    def test_el_sano_avanza_y_el_roto_deja_un_solo_aviso(self):
        with mock.patch.object(cli, "barrer", return_value=[]) as barrer:
            with contextlib.redirect_stderr(io.StringIO()) as err:
                self._supervisor("claude")
                self._supervisor("")

        self.assertEqual(barrer.call_count, 2)
        rutas = [
            e for e in self.reg.eventos(self.sano) if e["kind"] == "ruta_agentes"
        ]
        self.assertEqual(len(rutas), 1)
        self.assertEqual(rutas[0]["data"]["agentes"], ["claude"])
        avisos = [linea for linea in err.getvalue().splitlines() if self.roto in linea]
        self.assertEqual(len(avisos), 1)
        self.assertIn("dependencia circular", avisos[0])
        self.assertEqual(
            [e for e in self.reg.eventos(self.roto) if e["kind"] == "instrumento_fallo"],
            [],
        )
        guardado = json.loads(
            (
                self.reg.base / "avisos" / f"{self.aviso_incierto}.json"
            ).read_text(encoding="utf-8")
        )
        self.assertEqual(
            guardado["estado"],
            "incierto",
            "un aviso de un encargo ilegible no se da por cerrado",
        )
        self.assertFalse(
            (
                self.reg.base / "avisos" / "archivo" / f"{self.aviso_incierto}.json"
            ).exists()
        )


class DrenajeQueAisla(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.reg = Registro(Path(self.tmp) / "base")
        self.addCleanup(self.reg.close)
        self.sano = recibir(
            self.reg, "pedido sano", "clave-sana", ahora_iso(), canal="tg"
        )["encargo"]
        _sembrar(
            self.reg,
            self.sano,
            "fases",
            {
                "fases": [{"id": "A", "resultado": "fase comprobada"}],
                "hora": "2026-11-06T04:00:00Z",
            },
            "drenaje:sano:fases",
        )
        self.roto = "enc_roto_drenaje"
        _sembrar(
            self.reg,
            self.roto,
            "entrega",
            {
                "id": self.roto,
                "clave": "clave-rota",
                "huella": "huella",
                "texto": "historia incoherente",
                "via": "durable",
                "entrega_nivel": "terminado",
                "recibo": "rec-roto",
                "hora": "2026-11-06T03:50:10Z",
            },
            "drenaje:roto:entrega",
        )
        _sembrar(
            self.reg,
            self.roto,
            "fases",
            {
                "fases": [
                    {"id": "a", "requiere": [{"fase": "b", "version": ""}], "resultado": "A"},
                    {"id": "b", "requiere": [{"fase": "a", "version": ""}], "resultado": "B"},
                ],
                "hora": "2026-11-06T04:00:00Z",
            },
            "drenaje:roto:fases",
        )
        self.aviso_incierto = "av_incierto_drenaje"
        avisos = self.reg.base / "avisos"
        avisos.mkdir(parents=True, exist_ok=True)
        (avisos / f"{self.aviso_incierto}.json").write_text(
            json.dumps(
                {
                    "id": self.aviso_incierto,
                    "encargo": self.roto,
                    "canal": "tg",
                    "evidencia": "avance:inicio:inicio",
                    "estado": "incierto",
                    "tipo": "avance",
                    "recibo": "",
                    "fallos": 1,
                    "proximo_intento": "1970-01-01T00:00:00Z",
                }
            ),
            encoding="utf-8",
        )
        _olvidar_plegados_ilegibles()
        self.addCleanup(_olvidar_plegados_ilegibles)

    def test_el_drenaje_sigue_tras_el_encargo_que_no_pliega(self):
        salida = directores.drenar(self.reg)

        publicados = {str(a.get("encargo") or "") for a in salida["avisos"]}
        self.assertIn(
            self.sano,
            publicados,
            "el drenaje aisló el fallo y siguió hasta el encargo sano",
        )
        guardado = json.loads(
            (
                self.reg.base / "avisos" / f"{self.aviso_incierto}.json"
            ).read_text(encoding="utf-8")
        )
        self.assertEqual(guardado["estado"], "incierto")


if __name__ == "__main__":
    unittest.main()
