"""Trabas del 30 y 31 de octubre en respuestas y buzón: la respuesta del
propietario llega completa al revisor, un ajuste o una respuesta reabren su
turno, una pregunta abierta no deja un recibido mudo y una solicitud tardía no
cuelga la fase. Cada prueba reproduce el escenario medido."""

from __future__ import annotations

import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from motor.encargos import capacidades as cap
from motor.encargos import dominio as dom
from motor.encargos import necesidades as nec
from motor.encargos.aislamiento import SondaEntorno
from motor.encargos.ciclo import (
    _directorio,
    _id_inspeccion,
    _juicio_de_referencia,
    ahora_iso,
    barrer,
    corregir,
    ordenar,
)
from tests.test_flujo_revisiones import Recorrido

LARGA = "si, el CI esta en verde. " + "evidencia " * 90 + "FIN-DE-LA-RESPUESTA"


def _pedido(ruta: str = "indice.txt", recurso: str = "ci-github") -> dict:
    return {
        "veredicto": "no_concluyente",
        "evidencia": "falta el log del CI de Linux",
        "inspeccion_faltante": f"leer {ruta} del CI",
        "inspeccion": {
            "operacion": "remoto.leer",
            "recurso": recurso,
            "parametros": {"ruta": ruta},
        },
    }


class _Flujo(unittest.TestCase):
    def recorrido(self, clave: str, con_recurso: bool = False) -> Recorrido:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        r = Recorrido(tmp.name, clave, "Crea dato.txt con 42")
        self.addCleanup(r.reg.close)
        (r.espacio / "dato.txt").write_text("42")
        sonda = mock.patch(
            "motor.encargos.aislamiento.sondear", return_value=SondaEntorno(True)
        )
        sonda.start()
        self.addCleanup(sonda.stop)
        self.lanzamientos: list[tuple[str, str]] = []
        self.raiz_ci = Path(tmp.name) / "ci"
        if con_recurso:
            self.raiz_ci.mkdir()
            (self.raiz_ci / "indice.txt").write_text("v1")
            (r.base / "capacidades.json").write_text(
                json.dumps(
                    {
                        "recursos": {
                            "ci-github": {"tipo": "remoto", "raiz": str(self.raiz_ci)}
                        }
                    }
                )
            )
        r.incorporar()
        return r

    def lanzador(self, enc, prompt, espacio, sesion):
        self.lanzamientos.append((enc.rol, Path(prompt).read_text(encoding="utf-8")))
        return None

    def barrer(self, r: Recorrido) -> None:
        barrer(r.reg, ahora_iso(), time.time(), self.lanzador)

    def abrir_turno(self, r: Recorrido, rol: str = "revisor") -> str:
        """Abre el intento como el lanzador: reserva y intento_abierto."""
        enc = r.fold()
        self.turnos = getattr(self, "turnos", 0) + 1
        ident = f"{'rev' if rol == 'revisor' else 'resp'}-{self.turnos}"
        r.reg.anotar(
            r.enc,
            {
                "kind": "reserva",
                "data": {
                    "rol": rol,
                    "generacion": enc.generacion + 1,
                    "ajustes": len(enc.ajustes),
                    "hora": r.hora,
                },
            },
            f"t-reserva-turno-{self.turnos}",
        )
        r.reg.anotar(
            r.enc,
            {
                "kind": "intento_abierto",
                "data": {
                    "id": ident,
                    "pid": os.getpid(),
                    "inicio": "t",
                    "rol": rol,
                    "hora": r.hora,
                },
            },
            f"t-abierto-turno-{self.turnos}",
        )
        return ident

    def cerrar_turno(self, r: Recorrido) -> None:
        r.reg.anotar(
            r.enc,
            {"kind": "intento_cerrado", "data": {"hora": r.hora}},
            f"t-cerrado-turno-{self.turnos}",
        )

    def pedir(self, r: Recorrido, **kw) -> str:
        """Un turno completo del revisor: abre su intento, juzga y lo cierra."""
        self.abrir_turno(r)
        r.juicio(_pedido(**kw))
        r.incorporar()
        self.cerrar_turno(r)
        return r.fold().fases[0].inspeccion_solicitud

    def buzon(self, r: Recorrido, ident: str) -> dict:
        ruta = r.espacio / ".interno" / "buzon" / "respuestas" / f"{ident}.json"
        return json.loads(ruta.read_text(encoding="utf-8"))

    def responder(self, r: Recorrido, ident: str, texto: str, clave: str) -> dict:
        salida = nec.responder(
            r.reg, r.enc, ident, texto, "propietario", clave, ahora_iso()
        )
        if salida.get("aplicada"):
            salida["continuacion"] = nec.continuar_respuestas(
                r.reg,
                r.fold(),
                r.espacio,
                _directorio(r.base, r.enc),
                ahora_iso(),
            )
        return salida

    def herramienta_ok(self):
        llamadas: list[str] = []

        def ejecutar(registro, encargo, sol, recursos, esp, dir_, autorizado, hora):
            llamadas.append(sol.id)
            return {"estado": "ok", "datos": {"texto": "log"}, "evidencia": {}}

        parche = mock.patch.object(nec, "_ejecutar", side_effect=ejecutar)
        parche.start()
        self.addCleanup(parche.stop)
        return llamadas


class RespuestaCompletaAlRevisor(_Flujo):
    def test_la_respuesta_larga_llega_entera_y_el_json_no_conserva_el_error(self):
        r = self.recorrido("resp-1")
        ident = self.pedir(r)
        self.assertEqual(r.fold().solicitudes[0].estado, "pregunta")

        salida = self.responder(r, ident, LARGA, "k1")

        self.assertTrue(salida["aplicada"])
        enc = r.fold()
        self.assertEqual(enc.solicitudes[0].estado, "error")
        doc = self.buzon(r, ident)
        self.assertEqual(doc["estado"], "respuesta")
        self.assertEqual(doc["respuesta"], LARGA)
        self.assertEqual(doc["anterior"]["estado"], "error")
        self.assertNotIn("operación no concedida", json.dumps(doc))

        self.barrer(r)
        self.assertEqual([rol for rol, _ in self.lanzamientos], ["revisor"])
        self.assertIn(LARGA, self.lanzamientos[0][1])

    def test_una_respuesta_a_una_solicitud_ya_resuelta_no_se_pierde(self):
        r = self.recorrido("resp-2")
        ident = self.pedir(r)
        self.responder(r, ident, "si, autorizo", "k1")
        self.assertEqual(r.fold().solicitudes[0].estado, "error")

        salida = self.responder(r, ident, LARGA, "k2")

        self.assertTrue(salida["aplicada"], salida)
        self.assertEqual(r.fold().solicitudes[0].tardias, (LARGA,))
        doc = self.buzon(r, ident)
        self.assertEqual(doc["estado"], "respuesta")
        self.assertIn(LARGA, doc["respuesta"])
        self.assertEqual(doc["respuestas_propietario"][-1], LARGA)
        self.barrer(r)
        self.assertIn(LARGA, self.lanzamientos[-1][1])

    def test_la_respuesta_despues_de_otra_vuelta_del_revisor_tambien_llega(self):
        r = self.recorrido("resp-4")
        ident = self.pedir(r)
        self.responder(r, ident, "si, autorizo", "k1")
        self.abrir_turno(r)
        self.cerrar_turno(r)
        self.assertNotEqual(r.fold().solicitudes[0].intento, r.fold().intento)

        salida = self.responder(r, ident, LARGA, "k2")

        self.assertTrue(salida["aplicada"], salida)
        self.assertEqual(self.buzon(r, ident)["respuestas_propietario"][-1], LARGA)
        self.barrer(r)
        self.assertIn(LARGA, self.lanzamientos[-1][1])

    def test_la_respuesta_a_otra_solicitud_de_la_fase_tambien_llega(self):
        r = self.recorrido("resp-3")
        ligada = self.pedir(r)
        enc = r.fold()
        r.reg.anotar(
            r.enc,
            {
                "kind": "solicitud",
                "data": {
                    "id": "otra-1",
                    "operacion": "remoto.leer",
                    "recurso": "ci-github",
                    "fase": enc.fase_activa,
                    "intento": enc.intento or "t1",
                    "generacion": enc.generacion,
                    "conductor": "",
                    "motivo": "otra consulta",
                    "estado": "pregunta",
                    "pregunta": "¿autorizas?",
                    "hora": ahora_iso(),
                },
            },
            "solicitud-otra",
        )
        salida = nec.responder(
            r.reg, r.enc, "otra-1", LARGA, "propietario", "k9", ahora_iso()
        )
        self.assertTrue(salida["aplicada"])
        self.responder(r, ligada, "no hay mas datos", "k10")
        self.barrer(r)
        revisor = [p for rol, p in self.lanzamientos if rol == "revisor"]
        self.assertTrue(revisor and LARGA in revisor[0])


class AjusteYRespuestaReabrenElTurno(_Flujo):
    def _impedida_por_repeticion(self, clave: str):
        r = self.recorrido(clave, con_recurso=True)
        llamadas = self.herramienta_ok()
        ident = self.pedir(r)
        self.barrer(r)
        self.assertEqual(llamadas, [ident])
        self.assertEqual(r.fold().solicitudes[0].estado, "resuelta")
        self.lanzamientos.clear()
        self.abrir_turno(r)
        r.juicio(_pedido())
        r.incorporar()
        self.cerrar_turno(r)
        self.barrer(r)
        self.assertEqual(self.lanzamientos, [])
        self.assertTrue(r.fold().impedimento.startswith("inspeccion:"))
        return r, ident, llamadas

    def test_el_impedimento_de_inspeccion_se_levanta_con_un_ajuste(self):
        r, _ident, _llamadas = self._impedida_por_repeticion("imp-1")
        corregir(r.reg, r.enc, "El CI ya esta en verde, mira el run 77.", "aj-1", ahora_iso())
        self.barrer(r)
        self.assertEqual([rol for rol, _ in self.lanzamientos], ["revisor"])
        self.assertIn("run 77", self.lanzamientos[0][1])
        self.assertEqual(r.fold().impedimento, "")

    def test_el_impedimento_de_inspeccion_se_levanta_con_una_respuesta(self):
        r, ident, _llamadas = self._impedida_por_repeticion("imp-2")
        self.assertNotEqual(r.fold().solicitudes[0].intento, r.fold().intento)
        salida = self.responder(r, ident, "El CI esta en verde: run 77.", "k1")
        self.assertTrue(salida["aplicada"], salida)
        self.assertEqual(salida["estado"], "resuelta")
        self.assertEqual(self.buzon(r, ident)["respuesta"], "El CI esta en verde: run 77.")
        self.barrer(r)
        self.assertEqual([rol for rol, _ in self.lanzamientos], ["revisor"])
        self.assertIn("run 77", self.lanzamientos[0][1])
        self.assertEqual(r.fold().impedimento, "")

    def test_la_pregunta_de_una_vuelta_anterior_se_contesta_con_responder(self):
        r = self.recorrido("imp-4")
        ident = self.pedir(r)
        self.assertEqual(r.fold().solicitudes[0].estado, "pregunta")
        corregir(r.reg, r.enc, "Mira otra vez.", "aj-1", ahora_iso())
        self.barrer(r)
        self.assertEqual([rol for rol, _ in self.lanzamientos], ["revisor"])
        self.abrir_turno(r)
        r.juicio({**_pedido(), "evidencia": "sigue faltando el log del CI de Linux"})
        r.incorporar()
        self.cerrar_turno(r)
        r.reg.anotar(
            r.enc,
            {
                "kind": "quietud",
                "data": {"causa": r.fold().solicitudes[0].pregunta, "hora": r.hora},
            },
            "t-quietud-turno",
        )
        enc = r.fold()
        self.assertEqual(enc.estado, "necesita_usuario")
        self.assertEqual(enc.solicitudes[0].estado, "pregunta")
        self.assertEqual(enc.solicitudes[0].intento, "rev-1")
        self.assertEqual(enc.intento, "rev-2")
        self.lanzamientos.clear()

        salida = self.responder(r, ident, "Autorizado: el CI esta en verde, run 77.", "k1")

        self.assertTrue(salida["aplicada"], salida)
        self.assertIn("run 77", self.buzon(r, ident)["respuesta"])
        self.barrer(r)
        self.assertEqual([rol for rol, _ in self.lanzamientos], ["revisor"])
        self.assertIn("run 77", self.lanzamientos[0][1])

    def test_el_ajuste_abre_el_turno_una_sola_vez(self):
        r, _ident, _llamadas = self._impedida_por_repeticion("imp-3")
        corregir(r.reg, r.enc, "Probar de nuevo.", "aj-1", ahora_iso())
        self.barrer(r)
        self.assertEqual(len(self.lanzamientos), 1)
        enc = r.fold()
        r.reg.anotar(
            r.enc,
            {
                "kind": "reserva",
                "data": {
                    "rol": "revisor",
                    "generacion": enc.generacion + 1,
                    "ajustes": len(enc.ajustes),
                    "hora": r.hora,
                },
            },
            "t-reserva-revisor",
        )
        for kind, datos in (
            ("intento_abierto", {"id": "rev-1", "pid": os.getpid(), "inicio": "t", "rol": "revisor"}),
            ("intento_cerrado", {}),
        ):
            r.reg.anotar(r.enc, {"kind": kind, "data": {**datos, "hora": r.hora}}, f"t-{kind}")
        r.juicio(_pedido())
        r.incorporar()
        enc = r.fold()
        self.assertFalse(dom.ajuste_sin_ver_por_inspeccion(enc))
        self.assertIsNotNone(dom.espera_inspeccion_consumida(enc))


class PreguntaAbiertaNoDejaRecibidoMudo(_Flujo):
    def test_un_ajuste_con_la_pregunta_abierta_cuenta_como_respuesta(self):
        r = self.recorrido("preg-1")
        self.pedir(r)
        self.assertEqual(r.fold().estado, "necesita_usuario")
        self.barrer(r)
        self.assertEqual(self.lanzamientos, [])

        corregir(r.reg, r.enc, "Sin CI propio: usa el log que adjunte.", "aj-1", ahora_iso())
        self.barrer(r)

        self.assertEqual([rol for rol, _ in self.lanzamientos], ["revisor"])
        self.assertIn("Sin CI propio", self.lanzamientos[0][1])

    def test_un_ajuste_con_el_encargo_detenido_arranca_al_continuar(self):
        r = self.recorrido("preg-2")
        self.pedir(r)
        ordenar(r.reg, r.enc, "parar", "p-1", ahora_iso())
        self.barrer(r)
        self.assertEqual(r.fold().estado, "detenido")
        corregir(r.reg, r.enc, "Usa el log que adjunte.", "aj-1", ahora_iso())
        self.assertEqual(r.fold().estado, "detenido")

        salida = ordenar(r.reg, r.enc, "continuar", "c-1", ahora_iso())
        self.barrer(r)

        self.assertEqual(salida["estado"], "recibido")
        self.assertEqual([rol for rol, _ in self.lanzamientos], ["revisor"])

    def test_continuar_sin_respuesta_avisa_la_pregunta_abierta(self):
        r = self.recorrido("preg-3")
        ident = self.pedir(r)
        pregunta = r.fold().solicitudes[0].pregunta
        ordenar(r.reg, r.enc, "parar", "p-1", ahora_iso())
        self.barrer(r)
        self.assertEqual(r.fold().estado, "detenido")

        salida = ordenar(r.reg, r.enc, "continuar", "c-1", ahora_iso())

        self.assertEqual(salida["estado"], "necesita_usuario")
        self.assertIn(pregunta, salida["motivo"])
        self.assertIn(pregunta, salida["preguntas"])
        self.barrer(r)
        self.assertEqual(self.lanzamientos, [])
        self.assertTrue(ident)


class SolicitudTardiaNoCuelgaLaFase(_Flujo):
    def test_una_liga_a_una_solicitud_inexistente_no_bloquea(self):
        r = self.recorrido("tard-1")
        enc = r.fold()
        r.reg.anotar(
            r.enc,
            {
                "kind": "revision",
                "data": {
                    "revision_id": "rev-1",
                    "version": enc.producto.firma,
                    "base_version": "",
                    "alcance": "completa",
                    "veredicto": "no_concluyente",
                    "revisor": "x",
                    "origen": "independiente",
                    "cubre": [],
                    "hallazgos": [],
                    "aprobadas": [],
                    "evidencia": "falta",
                    "efectos_pendientes": [],
                    "inspeccion_faltante": "leer indice.txt",
                    "fase": enc.fase_activa,
                    "hora": ahora_iso(),
                },
            },
            "rev-1",
        )
        r.reg.anotar(
            r.enc,
            {
                "kind": "inspeccion_solicitud",
                "data": {
                    "fase": enc.fase_activa,
                    "intento": enc.intento,
                    "solicitud": "insp-fantasma",
                    "hora": ahora_iso(),
                },
            },
            "liga-fantasma",
        )
        self.assertFalse(dom.inspeccion_bloquea(r.fold()))
        self.barrer(r)
        self.assertEqual([rol for rol, _ in self.lanzamientos], ["revisor"])

    def test_parar_justo_al_pedir_la_inspeccion_no_deja_la_fase_colgada(self):
        r = self.recorrido("tard-2", con_recurso=True)
        llamadas = self.herramienta_ok()
        ident = self.pedir(r)
        ordenar(r.reg, r.enc, "parar", "p-1", ahora_iso())
        self.barrer(r)
        self.assertEqual(llamadas, [])
        self.assertEqual(r.fold().estado, "detenido")
        self.assertEqual(self.buzon(r, ident)["estado"], "detenida")

        ordenar(r.reg, r.enc, "continuar", "c-1", ahora_iso())
        self.barrer(r)
        self.assertEqual([rol for rol, _ in self.lanzamientos], ["revisor"])

        r.juicio(_pedido())
        r.incorporar()
        self.barrer(r)
        nueva = r.fold().fases[0].inspeccion_solicitud
        self.assertNotEqual(nueva, ident)
        self.assertEqual(llamadas, [nueva])
        self.assertEqual(self.buzon(r, nueva)["estado"], "resuelta")

    def test_el_juicio_que_vuelve_a_pedir_la_inspeccion_huerfana_no_se_descarta(self):
        r = self.recorrido("tard-3", con_recurso=True)
        llamadas = self.herramienta_ok()
        self.abrir_turno(r)
        r.juicio(_pedido())
        enc = r.fold()
        ident = _id_inspeccion(enc, _juicio_de_referencia(enc, r.d), r.base)
        r.reg.anotar(
            r.enc,
            {
                "kind": "revision",
                "data": {
                    "revision_id": "rev-huerfana",
                    "version": enc.producto.firma,
                    "base_version": "",
                    "alcance": "completa",
                    "veredicto": "no_concluyente",
                    "revisor": "x",
                    "origen": "independiente",
                    "cubre": [],
                    "hallazgos": [],
                    "aprobadas": [],
                    "evidencia": "falta",
                    "efectos_pendientes": [],
                    "inspeccion_faltante": "leer indice.txt del CI",
                    "fase": enc.fase_activa,
                    "hora": ahora_iso(),
                },
            },
            "rev-huerfana",
        )
        r.reg.anotar(
            r.enc,
            {
                "kind": "inspeccion_solicitud",
                "data": {
                    "fase": enc.fase_activa,
                    "intento": enc.intento,
                    "solicitud": ident,
                    "hora": ahora_iso(),
                },
            },
            "liga-huerfana",
        )
        self.cerrar_turno(r)
        self.assertEqual(r.fold().solicitudes, ())
        self.barrer(r)
        self.assertEqual([rol for rol, _ in self.lanzamientos], ["revisor"])

        self.abrir_turno(r)
        r.juicio(_pedido())
        r.incorporar()
        self.cerrar_turno(r)
        self.assertEqual([s.id for s in r.fold().solicitudes], [ident])
        self.lanzamientos.clear()
        self.barrer(r)

        self.assertEqual(llamadas, [ident])
        self.assertEqual(self.buzon(r, ident)["estado"], "resuelta")
        self.assertEqual(r.fold().solicitudes[0].estado, "resuelta")


class PromptDelRevisorSoloPrometeLoObservable(_Flujo):
    def _prompt(self, recursos: dict) -> str:
        r = self.recorrido("prompt-1")
        (r.base / "capacidades.json").write_text(json.dumps({"recursos": recursos}))
        self.barrer(r)
        revisor = [p for rol, p in self.lanzamientos if rol == "revisor"]
        self.assertTrue(revisor)
        return revisor[-1]

    def test_un_recurso_por_fichero_se_puede_pedir_de_nuevo(self):
        prompt = self._prompt({"ci-github": {"tipo": "remoto", "raiz": "/tmp/ci"}})
        self.assertIn("recurso observable del catalogo (ci-github)", prompt)

    def test_un_recurso_http_no_promete_nada(self):
        prompt = self._prompt(
            {
                "api": {
                    "tipo": "remoto",
                    "transporte": "http",
                    "base": "https://ejemplo.invalid",
                }
            }
        )
        self.assertNotIn("pedirlo de nuevo no es repetir", prompt)
        self.assertNotIn("(api)", prompt)

    def test_recursos_observables_separa_http_de_fichero(self):
        recursos = {
            "a": {"tipo": "remoto", "raiz": "/x"},
            "b": {"tipo": "remoto", "transporte": "http"},
            "c": {"tipo": "sqlite", "ruta": "/y.db"},
        }
        self.assertEqual(cap.recursos_observables(recursos), ("a", "c"))


class RecursoCambiadoNoEsRepetir(_Flujo):
    def test_pedir_de_nuevo_un_archivo_cambiado_ejecuta_otra_lectura(self):
        r = self.recorrido("cambio-1", con_recurso=True)
        llamadas = self.herramienta_ok()
        primera = self.pedir(r)
        self.barrer(r)
        self.assertEqual(llamadas, [primera])

        (self.raiz_ci / "indice.txt").write_text("version nueva y mas larga")
        self.pedir(r)
        self.barrer(r)

        enc = r.fold()
        segunda = enc.fases[0].inspeccion_solicitud
        self.assertNotEqual(segunda, primera)
        self.assertEqual(llamadas, [primera, segunda])
        self.assertEqual(enc.impedimento, "")

    def test_pedir_de_nuevo_un_archivo_igual_sigue_siendo_repetir(self):
        r = self.recorrido("cambio-2", con_recurso=True)
        llamadas = self.herramienta_ok()
        primera = self.pedir(r)
        self.barrer(r)
        self.pedir(r)
        self.barrer(r)
        self.assertEqual(llamadas, [primera])
        self.assertTrue(r.fold().impedimento.startswith("inspeccion:"))

    def test_la_huella_cambia_con_tamano_hash_o_fecha(self):
        with tempfile.TemporaryDirectory() as tmp:
            raiz = Path(tmp)
            archivo = raiz / "indice.txt"
            archivo.write_text("aaaa")
            recursos = {"ci": {"tipo": "remoto", "raiz": str(raiz)}}
            args = (recursos, cap.REMOTO, "ci", {"ruta": "indice.txt"})
            base = cap.huella_recurso(*args)
            self.assertEqual(base, cap.huella_recurso(*args))
            archivo.write_text("bbbb")
            mismo_tamano = cap.huella_recurso(*args)
            self.assertNotEqual(base, mismo_tamano)
            archivo.write_text("bbbbb")
            self.assertNotEqual(mismo_tamano, cap.huella_recurso(*args))
            self.assertEqual(cap.huella_recurso(recursos, cap.REMOTO, "ci", {"ruta": "no.txt"}), "")


if __name__ == "__main__":
    unittest.main()
