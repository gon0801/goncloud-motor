"""El resultado que recibe la autoridad se guarda entero o nada, con las ukeys de siempre."""

from __future__ import annotations

import hashlib
import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr
from dataclasses import replace
from pathlib import Path
from unittest import mock

from motor.encargos import registro as modulo_registro
from motor.encargos.ciclo import recibir_resultado
from motor.encargos.dominio import bloqueantes_abiertos
from motor.encargos.registro import Registro
from tests.arnes_corte import cortar_en_cada_commit, filas
from tests.dobles import github_con_hallazgos
from tests.test_flujo_revisiones import _entrega_pr, sonda_sin_aislamiento_del_host

HORA = "2026-11-13T12:00:00+00:00"
VERSION = "v-nueva"
EVIDENCIA = "el revisor leyó saludo.txt y descarta la fuga"
RAZON = "señala un cambio que el encargo no pide"


def setUpModule():
    sonda_sin_aislamiento_del_host()


def _escena(caso, raiz: Path):
    """Un PR abierto con dos hallazgos de DeepSeek y un conductor con su token."""
    vuelta, r = _entrega_pr(
        caso, str(raiz), "resultado-1",
        lambda d: github_con_hallazgos(d, ("Fuga", "Ajena")),
        texto="cambia el comando de encargos",
    )
    vuelta("hola")
    r.reg.anotar(
        r.enc,
        {"kind": "conductor", "data": {"token": "tok", "host": "host"}},
        "conductor-resultado",
    )
    return r, r.fold()


def _resultado() -> tuple[dict, dict]:
    """Un producto nuevo aprobado que descarta `ds_Fuga` y deja fuera `ds_Ajena`."""
    observaciones = {"version": VERSION, "manifiesto": [["saludo.txt", "x"]]}
    juicio = {
        "version": VERSION,
        "veredicto": "aprobado",
        "evidencia": EVIDENCIA,
        "resueltos": ["ds_Fuga"],
        "fuera_de_alcance": [{"hallazgo": "ds_Ajena", "razon": RAZON}],
    }
    return observaciones, juicio


def _recibir(registro, enc, observaciones: dict, juicio: dict) -> dict:
    return recibir_resultado(
        registro, enc.id, "tok", "host", enc.huella, observaciones, juicio, HORA
    )


class ResultadoDeLaAutoridad(unittest.TestCase):
    def test_un_apagon_en_cualquier_commit_deja_el_resultado_entero_o_nada(self):
        with tempfile.TemporaryDirectory() as tmp:
            escena = Path(tmp) / "escena"
            escena.mkdir()
            r, enc = _escena(self, escena)
            r.reg.close()
            observaciones, juicio = _resultado()

            def paso(registro):
                salida = _recibir(registro, enc, observaciones, juicio)
                self.assertTrue(salida.get("registrado"), salida)

            cortar_en_cada_commit(self, escena, r.base, enc.id, paso)

    def test_un_cierre_que_falla_rechaza_sin_dejar_la_revision_aprobada(self):
        with tempfile.TemporaryDirectory() as tmp:
            r, enc = _escena(self, Path(tmp))
            previas = len(filas(r.base))
            observaciones, juicio = _resultado()
            observaciones["solicitudes_efecto"] = [
                {"id": "ef1", "operacion": "publicar", "clave": "k1", "cuerpo": "c"}
            ]
            juicio["efectos_pendientes"] = ["ef1"]

            salida = _recibir(r.reg, enc, observaciones, juicio)

            self.assertTrue(salida.get("rechazado"), salida)
            self.assertIn("ef1", salida["motivo"])
            self.assertEqual(
                [kind for kind, _ukey, _data in filas(r.base)[previas:]],
                ["conductor_fase"],
            )
            self.assertEqual(
                [h.id for h in bloqueantes_abiertos(r.fold(), "deepseek")],
                ["ds_Fuga", "ds_Ajena"],
            )

    def test_el_resultado_guarda_las_ukeys_y_los_datos_de_siempre(self):  # no-roja-en-base: fija lo que la base ya escribe; con otra ukey u otro dato un registro viejo a medias no daría igual
        with tempfile.TemporaryDirectory() as tmp:
            r, enc = _escena(self, Path(tmp))
            abiertos = {h.id: h for h in bloqueantes_abiertos(enc, "deepseek")}
            previas = len(filas(r.base))
            observaciones, juicio = _resultado()

            salida = _recibir(r.reg, enc, observaciones, juicio)

            e, fase, sha = enc.id, enc.fase_activa, enc.pr_sha
            recibo = f"resultado:{e}:{fase}:tok"
            revision_id = "rev_autoridad_" + VERSION[:12]
            self.assertEqual(
                salida,
                {"registrado": True, "encargo": e, "estado": "terminado", "fase": fase, "recibo": recibo},
            )
            escritos = filas(r.base)[previas:]
            kind, ukey, comprobacion = escritos.pop()
            self.assertEqual(escritos, [
                (
                    "conductor_fase",
                    f"conductor-fase:{e}:tok",
                    {"token": "tok", "fase": fase, "hora": HORA},
                ),
                (
                    "producto_listo",
                    f"producto-autoridad:{e}:{fase}:{VERSION}",
                    {
                        "version": VERSION, "manifiesto": [["saludo.txt", "x"]],
                        "fase": fase, "intento": enc.intento, "hora": HORA,
                    },
                ),
                (
                    "nota_pr",
                    f"disputa-resuelta-autoridad:{e}:ds_Fuga",
                    {
                        "texto": (
                            "hallazgo de DeepSeek descartado por el revisor "
                            f"independiente: ds_Fuga «Fuga» ({EVIDENCIA})"
                        ),
                        "hora": HORA,
                    },
                ),
                (
                    "excepcion",
                    f"excepcion:{e}:descarte:{sha}:ds_Fuga",
                    {
                        "tipo": "descarte", "sha": sha, "id": "ds_Fuga",
                        "motivo": EVIDENCIA, "hora": HORA, "ancla": "saludo.txt",
                        "region": hashlib.sha256("saludo.txt\0hola".encode()).hexdigest(),
                        "region_contexto": [],
                    },
                ),
                (
                    "hallazgo_fuera_de_alcance",
                    f"fuera-alcance:{e}:{fase}:ds_Ajena",
                    {"id": "ds_Ajena", "titulo": "Ajena", "razon": RAZON, "hora": HORA},
                ),
                (
                    "excepcion",
                    f"excepcion:{e}:fuera:{sha}:ds_Ajena",
                    {
                        "tipo": "fuera_alcance", "sha": sha, "id": "ds_Ajena",
                        "motivo": RAZON, "hora": HORA,
                    },
                ),
                (
                    "revision",
                    f"revision-autoridad:{e}:{fase}:{VERSION}",
                    {
                        "revision_id": revision_id,
                        "version": VERSION,
                        "base_version": enc.producto.firma,
                        "alcance": "correccion",
                        "veredicto": "aprobado",
                        "revisor": "autoridad",
                        "origen": "independiente",
                        "cubre": [],
                        "hallazgos": [
                            replace(
                                abiertos["ds_Fuga"], estado="resuelto",
                                cierre_evidencia=EVIDENCIA,
                            ).como_dict(),
                            replace(
                                abiertos["ds_Ajena"], gravedad="observacion",
                                estado="resuelto", cierre_evidencia=RAZON,
                            ).como_dict(),
                        ],
                        "aprobadas": [["saludo.txt", "x"]],
                        "evidencia": EVIDENCIA,
                        "efectos_pendientes": [],
                        "fase": fase,
                        "intento": enc.intento,
                        "hora": HORA,
                    },
                ),
            ])
            self.assertEqual((kind, ukey), ("comprobacion", recibo))
            huella = hashlib.sha256(json.dumps(
                {"observaciones": observaciones, "juicio": juicio},
                sort_keys=True, ensure_ascii=False, default=str,
            ).encode()).hexdigest()
            self.assertEqual(
                {clave: comprobacion.get(clave) for clave in (
                    "clase", "fase", "firma", "revision", "huella_resultado",
                    "observaciones", "host", "hora",
                )},
                {
                    "clase": "cierre_comprobado", "fase": fase, "firma": VERSION,
                    "revision": revision_id, "huella_resultado": huella,
                    "observaciones": observaciones, "host": "host", "hora": HORA,
                },
            )

    def test_el_mismo_resultado_otra_vez_es_una_retransmision_sin_filas_nuevas(self):  # no-roja-en-base: la base ya respondía la retransmisión sin escribir; fija que la asociación del lote no agregue filas
        with tempfile.TemporaryDirectory() as tmp:
            r, enc = _escena(self, Path(tmp))
            observaciones, juicio = _resultado()
            self.assertTrue(_recibir(r.reg, enc, observaciones, juicio).get("registrado"))
            guardadas = filas(r.base)

            salida = _recibir(r.reg, enc, observaciones, juicio)

            fase = enc.fase_activa
            self.assertEqual(salida, {
                "registrado": True, "encargo": enc.id, "estado": "terminado",
                "retransmision": True, "recibo": f"resultado:{enc.id}:{fase}:tok", "fase": fase,
            })
            self.assertEqual(filas(r.base), guardadas)

    def test_otro_resultado_con_el_mismo_token_es_un_conflicto_sin_filas_nuevas(self):  # no-roja-en-base: la base ya respondía el conflicto sin escribir; fija que la asociación del lote no agregue filas
        with tempfile.TemporaryDirectory() as tmp:
            r, enc = _escena(self, Path(tmp))
            observaciones, juicio = _resultado()
            self.assertTrue(_recibir(r.reg, enc, observaciones, juicio).get("registrado"))
            guardadas = filas(r.base)

            salida = _recibir(
                r.reg, enc, observaciones, dict(juicio, evidencia="otra lectura del revisor")
            )

            self.assertEqual(
                {clave: salida.get(clave) for clave in ("conflicto", "registrado", "fase")},
                {"conflicto": True, "registrado": None, "fase": enc.fase_activa},
            )
            self.assertEqual(filas(r.base), guardadas)

    def test_otro_escritor_antes_del_lote_rechaza_sin_escribir_y_el_reintento_lo_guarda(self):
        with tempfile.TemporaryDirectory() as tmp:
            r, enc = _escena(self, Path(tmp))
            ocupada = f"disputa-resuelta-autoridad:{enc.id}:ds_Fuga"
            escribir = r.reg.append_many

            def tras_otro_escritor(encargo, eventos):
                otro = Registro(r.base)
                try:
                    otro.anotar(
                        encargo,
                        {"kind": "nota_pr", "data": {"texto": "nota de otro escritor", "hora": HORA}},
                        ocupada,
                    )
                finally:
                    otro.close()
                return escribir(encargo, eventos)

            previas = len(filas(r.base))
            observaciones, juicio = _resultado()

            with mock.patch.object(r.reg, "append_many", tras_otro_escritor):
                salida = _recibir(r.reg, enc, observaciones, juicio)

            self.assertTrue(salida.get("rechazado"), salida)
            self.assertIn(ocupada, salida["motivo"])
            self.assertEqual([ukey for _kind, ukey, _data in filas(r.base)[previas:]], [ocupada])
            reintento = _recibir(r.reg, enc, observaciones, juicio)
            self.assertTrue(reintento.get("registrado"), reintento)
            self.assertEqual(
                [kind for kind, _ukey, _data in filas(r.base)[previas:]],
                [
                    "nota_pr", "conductor_fase", "producto_listo", "excepcion",
                    "hallazgo_fuera_de_alcance", "excepcion", "revision", "comprobacion",
                ],
            )

    def test_un_resultado_rechazado_cuenta_lo_que_el_registro_habria_rechazado(self):
        with tempfile.TemporaryDirectory() as tmp:
            r, enc = _escena(self, Path(tmp))
            r.reg.anotar(
                enc.id,
                {"kind": "nota_pr", "data": {"texto": "otro ds_Fuga de una fase anterior", "hora": HORA}},
                f"disputa-resuelta-autoridad:{enc.id}:ds_Fuga",
            )
            observaciones, juicio = _resultado()
            observaciones["solicitudes_efecto"] = [
                {"id": "ef1", "operacion": "publicar", "clave": "k1", "cuerpo": "c"}
            ]
            juicio["efectos_pendientes"] = ["ef1"]
            errores = io.StringIO()

            with mock.patch.object(modulo_registro, "_CONTEO", modulo_registro._Conteo()), \
                    redirect_stderr(errores):
                salida = _recibir(r.reg, enc, observaciones, juicio)

            self.assertTrue(salida.get("rechazado"), salida)
            self.assertIn(
                "registro: rechazo=ajena kind=nota_pr ukey=disputa-resuelta-autoridad:* campos=texto veces=1",
                errores.getvalue().splitlines(),
            )

    def test_un_descarte_con_su_ukey_ocupada_no_rechaza_el_resultado(self):
        with tempfile.TemporaryDirectory() as tmp:
            r, enc = _escena(self, Path(tmp))
            ocupada = f"disputa-resuelta-autoridad:{enc.id}:ds_Fuga"
            r.reg.anotar(
                enc.id,
                {"kind": "nota_pr", "data": {"texto": "otro ds_Fuga de una fase anterior", "hora": HORA}},
                ocupada,
            )
            enc = r.fold()
            previas = len(filas(r.base))
            observaciones, juicio = _resultado()
            errores = io.StringIO()

            with mock.patch.object(modulo_registro, "_CONTEO", modulo_registro._Conteo()), \
                    redirect_stderr(errores):
                salida = _recibir(r.reg, enc, observaciones, juicio)

            self.assertTrue(salida.get("registrado"), salida)
            self.assertEqual(
                [kind for kind, _ukey, _data in filas(r.base)[previas:]],
                [
                    "conductor_fase", "producto_listo", "excepcion",
                    "hallazgo_fuera_de_alcance", "excepcion", "revision", "comprobacion",
                ],
            )
            self.assertEqual(bloqueantes_abiertos(r.fold(), "deepseek"), ())
            self.assertIn(
                "registro: rechazo=ajena kind=nota_pr ukey=disputa-resuelta-autoridad:* campos=texto veces=1",
                errores.getvalue().splitlines(),
            )


if __name__ == "__main__":
    unittest.main()
