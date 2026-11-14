"""El juicio del revisor se guarda entero o nada, y el cierre que sigue lo termina el barrido."""

from __future__ import annotations

import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr
from dataclasses import replace
from pathlib import Path
from unittest import mock

import tests.test_flujo_revisiones as flujo
from motor.encargos import ciclo
from motor.encargos import registro as modulo_registro
from motor.encargos.aislamiento import SondaEntorno
from motor.encargos.dominio import bloqueantes_abiertos
from tests.arnes_corte import cortar_en_cada_commit, filas
from tests.dobles import github_con_hallazgos

HORA = "2026-11-13T12:00:00Z"
EPOCH = 1794571200.0
DESCARTE = "el revisor leyó saludo.txt y descarta la fuga"
RAZON = "señala un cambio que el encargo no pide"


def setUpModule():
    flujo.sonda_sin_aislamiento_del_host()


def _reservar_turno(r: flujo.Recorrido) -> None:
    """El turno siguiente queda reservado sin incorporar su resultado."""
    with mock.patch.object(flujo, "_incorporar"):
        r.incorporar()


def _incorporar(r: flujo.Recorrido):
    def paso(registro):
        return ciclo._incorporar(registro, registro.fold(r.enc), r.espacio, HORA, r.d)

    return paso


def _barrer(registro) -> None:
    with mock.patch(
        "motor.encargos.aislamiento.sondear", return_value=SondaEntorno(True)
    ), mock.patch(
        "motor.flota.runtime.exigir_poteto",
        return_value=(Path(registro.base) / "poteto.md", "digest"),
    ), mock.patch("motor.encargos.ciclo.observar", return_value="ausente"):
        ciclo.barrer(registro, HORA, EPOCH, lambda *_args: None)


def _cambios_que_cierran_un_bloqueante(raiz: Path) -> flujo.Recorrido:
    """El revisor cierra el bloqueante de la vuelta anterior y abre otro."""
    r = flujo.Recorrido(str(raiz), "juicio-avance", "malo.txt con B")
    malo = r.espacio / "malo.txt"
    malo.write_text("X")
    r.incorporar()
    r.juicio({
        "veredicto": "cambios",
        "evidencia": "cat malo.txt -> X",
        "bloqueantes": [flujo._bloqueante("malo.txt no contiene B")],
    })
    cerrado = bloqueantes_abiertos(r.incorporar())[0].id
    malo.write_text("B?")
    r.incorporar()
    r.juicio({
        "veredicto": "cambios",
        "evidencia": "cat malo.txt -> B?",
        "resueltos": [cerrado],
        "bloqueantes": [flujo._bloqueante("malo.txt trae un signo de más", "cat malo.txt -> B?")],
        "causa": "quitar el signo",
    })
    _reservar_turno(r)
    return r


def _aprobado(raiz: Path) -> flujo.Recorrido:
    """El revisor aprueba un encargo de nivel terminado: el paso sigue con el cierre."""
    r = flujo.Recorrido(str(raiz), "juicio-aprobado", "Crea dato.txt con 42")
    (r.espacio / "dato.txt").write_text("42")
    (flujo._interno(r) / "evidencia.txt").write_text("dato.txt contiene 42")
    r.incorporar()
    r.juicio({"veredicto": "aprobado", "evidencia": "lei dato.txt: 42"})
    _reservar_turno(r)
    (r.d / "outcome.json").write_text("{}")
    return r


def _disputa_de_deepseek(raiz: Path, clave: str, juicio: dict) -> flujo.Recorrido:
    """Dos hallazgos de DeepSeek en disputa y el juicio del revisor que la decide."""
    vuelta, r = flujo._entrega_pr(
        unittest.TestCase(), str(raiz), clave,
        lambda d: github_con_hallazgos(d, ("Fuga", "Ajena")),
        texto="cambia el comando de encargos",
    )
    vuelta("hola")
    (flujo._interno(r) / "disputa.json").write_text(json.dumps({"disputas": [
        {"hallazgo": "ds_Fuga", "evidencia": "el log no imprime el token"},
        {"hallazgo": "ds_Ajena", "evidencia": RAZON},
    ]}))
    r.incorporar()
    r.juicio(juicio)
    _reservar_turno(r)
    return r


class CerrarConJuicio(unittest.TestCase):
    def test_un_apagon_en_cualquier_commit_deja_el_juicio_entero_y_devuelve_el_trabajo(self):
        with tempfile.TemporaryDirectory() as tmp:
            escena = Path(tmp) / "escena"
            escena.mkdir()
            r = _cambios_que_cierran_un_bloqueante(escena)
            r.reg.close()

            cortar_en_cada_commit(self, escena, r.base, r.enc, _incorporar(r))

            registro = modulo_registro.Registro(r.base)
            self.addCleanup(registro.close)
            self.assertEqual(
                [e for e in registro.eventos(r.enc) if e["kind"] == "sin_progreso"], []
            )
            self.assertEqual(registro.fold(r.enc).estrategia, "investigar")

    def test_un_apagon_en_el_cierre_lo_termina_el_barrido_sin_repetir_el_juicio(self):
        with tempfile.TemporaryDirectory() as tmp:
            escena = Path(tmp) / "escena"
            escena.mkdir()
            r = _aprobado(escena)
            r.reg.close()

            commits = cortar_en_cada_commit(
                self, escena, r.base, r.enc, _incorporar(r),
                reintento=_barrer, cierra_el_lote="incorporado",
            )

            self.assertGreater(commits, 1, "el aprobado sigue con el cierre tras su lote")
            registro = modulo_registro.Registro(r.base)
            self.addCleanup(registro.close)
            self.assertEqual(registro.fold(r.enc).estado, "terminado")

    def test_el_juicio_guarda_las_ukeys_y_los_datos_de_siempre(self):  # no-roja-en-base: fija lo que la base ya escribe en este camino; con otra ukey u otro dato un registro viejo a medias no daría igual
        with tempfile.TemporaryDirectory() as tmp:
            r = _cambios_que_cierran_un_bloqueante(Path(tmp))
            enc = r.fold()
            previas = len(filas(r.base))

            _incorporar(r)(r.reg)

            e, fase, gen = enc.id, enc.fase_activa, enc.generacion
            escritos = filas(r.base)[previas:]
            kind, ukey, revision = escritos[0]
            self.assertEqual((kind, ukey), ("revision", f"revision-dato:{e}:{fase}:{gen}:{revision['version']}"))
            self.assertEqual(
                {k: v for k, v in revision.items() if k not in ("hallazgos", "aprobadas", "version", "base_version", "revision_id")},
                {
                    "alcance": "correccion", "veredicto": "cambios", "revisor": "",
                    "origen": "independiente", "cubre": [h.id for h in bloqueantes_abiertos(enc)],
                    "evidencia": "cat malo.txt -> B?", "efectos_pendientes": [],
                    "inspeccion_faltante": "", "pedido": {}, "fase": fase,
                    "intento": enc.intento, "hora": HORA,
                },
            )
            nuevo = bloqueantes_abiertos(r.fold(), "independiente")[0]
            self.assertEqual(escritos[1:], [
                (
                    "procedimiento", f"reparacion:{e}:{gen}",
                    {"nota": "quitar el signo", "hora": HORA},
                ),
                (
                    "estrategia", f"devolucion:{e}:{gen}:cambios",
                    {
                        "estrategia": "investigar", "rol": "responsable",
                        "motivo": nuevo.titulo,
                        "siguiente": f"corregir {nuevo.id}; lo aprobado que no cambio se conserva",
                        "hora": HORA,
                    },
                ),
                ("incorporado", f"incorporado:{e}:{gen}", {"generacion": gen, "hora": HORA}),
            ])

    def test_los_descartes_del_juicio_guardan_las_ukeys_y_los_datos_de_siempre(self):  # no-roja-en-base: fija lo que la base ya escribe en los descartes; con otra ukey u otro dato un registro viejo a medias no daría igual
        with tempfile.TemporaryDirectory() as tmp:
            r = _disputa_de_deepseek(Path(tmp), "juicio-descartes", {
                "veredicto": "aprobado",
                "evidencia": DESCARTE,
                "resueltos": ["ds_Fuga"],
                "fuera_de_alcance": [{"hallazgo": "ds_Ajena", "razon": RAZON}],
            })
            enc = r.fold()
            abiertos = {h.id: h for h in bloqueantes_abiertos(enc, "deepseek")}
            previas = len(filas(r.base))

            _incorporar(r)(r.reg)

            e, fase, sha, gen = enc.id, enc.fase_activa, enc.pr_sha, enc.generacion
            escritos = filas(r.base)[previas:]
            self.assertEqual(escritos[:4], [
                (
                    "nota_pr", f"disputa-resuelta:{e}:ds_Fuga",
                    {
                        "texto": (
                            "hallazgo de DeepSeek descartado por el revisor "
                            f"independiente: ds_Fuga «Fuga» ({DESCARTE})"
                        ),
                        "hora": HORA,
                    },
                ),
                (
                    "excepcion", f"excepcion:{e}:descarte:{sha}:ds_Fuga",
                    {
                        "tipo": "descarte", "sha": sha, "id": "ds_Fuga", "motivo": DESCARTE,
                        "hora": HORA, "ancla": "saludo.txt",
                        "region": escritos[1][2]["region"], "region_contexto": [],
                    },
                ),
                (
                    "hallazgo_fuera_de_alcance", f"fuera-alcance:{e}:{fase}:ds_Ajena",
                    {"id": "ds_Ajena", "titulo": "Ajena", "razon": RAZON, "hora": HORA},
                ),
                (
                    "excepcion", f"excepcion:{e}:fuera:{sha}:ds_Ajena",
                    {"tipo": "fuera_alcance", "sha": sha, "id": "ds_Ajena", "motivo": RAZON, "hora": HORA},
                ),
            ])
            self.assertTrue(escritos[1][2]["region"])
            kind, ukey, revision = escritos[4]
            self.assertEqual(
                (kind, ukey), ("revision", f"revision-dato:{e}:{fase}:{gen}:{revision['version']}")
            )
            self.assertEqual(revision["hallazgos"][-2:], [
                replace(abiertos["ds_Fuga"], estado="resuelto", cierre_evidencia=DESCARTE).como_dict(),
                replace(
                    abiertos["ds_Ajena"], gravedad="observacion", estado="resuelto",
                    cierre_evidencia=RAZON,
                ).como_dict(),
            ])
            self.assertEqual(escritos[5:], [
                ("incorporado", f"incorporado:{e}:{gen}", {"generacion": gen, "hora": HORA}),
            ])

    def test_un_hallazgo_fuera_de_alcance_repetido_guarda_el_primero_y_cuenta_el_otro(self):  # no-roja-en-base: la base ya callaba y contaba el segundo con anotar; fija que el lote no lo vuelva un error
        with tempfile.TemporaryDirectory() as tmp:
            r = _disputa_de_deepseek(Path(tmp), "juicio-repetido", {
                "veredicto": "aprobado",
                "evidencia": DESCARTE,
                "resueltos": ["ds_Fuga"],
                "fuera_de_alcance": [
                    {"hallazgo": "ds_Ajena", "razon": RAZON},
                    {"hallazgo": "ds_Ajena", "razon": "otra razón para el mismo hallazgo"},
                ],
            })
            previas = len(filas(r.base))
            errores = io.StringIO()

            with mock.patch.object(modulo_registro, "_CONTEO", modulo_registro._Conteo()), \
                    redirect_stderr(errores):
                _incorporar(r)(r.reg)

            escritos = filas(r.base)[previas:]
            self.assertEqual(
                [kind for kind, _ukey, _data in escritos],
                ["nota_pr", "excepcion", "hallazgo_fuera_de_alcance", "excepcion", "revision", "incorporado"],
            )
            self.assertEqual(escritos[2][2]["razon"], RAZON)
            self.assertEqual(
                [h["cierre_evidencia"] for h in escritos[4][2]["hallazgos"] if h["id"] == "ds_Ajena"],
                [RAZON, "otra razón para el mismo hallazgo"],
            )
            lineas = errores.getvalue().splitlines()
            self.assertIn(
                "registro: rechazo=ajena kind=hallazgo_fuera_de_alcance ukey=fuera-alcance:* campos=razon veces=1",
                lineas,
            )
            self.assertIn(
                "registro: rechazo=ajena kind=excepcion ukey=excepcion:* campos=motivo veces=1", lineas
            )


if __name__ == "__main__":
    unittest.main()
