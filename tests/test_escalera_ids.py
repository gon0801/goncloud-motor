"""La escalera de vueltas compara los ids devueltos y el despliegue que pierde
autoridad queda en espera explícita.

Regresiones obligatorias del pedido (punto 2): en las tres debe quedar una
espera o una decisión explícita, sin revisores nuevos.
"""

from __future__ import annotations

import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from motor.encargos import despliegue as ejecutor
from motor.encargos.ciclo import _incorporar, _paso_despliegue, ahora_iso, recibir
from motor.encargos.comprobacion import firma_producto
from motor.encargos.dominio import bloqueantes_abiertos, decision_proceso
from motor.encargos.registro import Registro

HORA = "2026-11-06T12:00:00+00:00"
HORA_TARDE = "2026-11-06T12:05:00+00:00"


class Recorrido:
    """Harness sin LLM: la prueba hace de agente entre incorporaciones."""

    def __init__(self, tmp: str, clave: str, texto: str):
        self.base = Path(tmp) / "base"
        self.reg = Registro(self.base)
        self.hora = ahora_iso()
        self.enc = recibir(self.reg, texto, clave, self.hora)["encargo"]
        self.espacio = self.base / "espacio" / self.enc
        self.espacio.mkdir(parents=True, exist_ok=True)
        self.d = self.base / "ejecucion" / self.enc
        self.d.mkdir(parents=True, exist_ok=True)
        self.resultado = self.d / "resultado.json"

    def fold(self):
        return self.reg.fold(self.enc)

    def incorporar(self):
        e = self.fold()
        n = e.generacion + 1
        self.reg.anotar(
            self.enc,
            {"kind": "reserva", "data": {"rol": e.rol or "responsable", "generacion": n}},
            f"t-reserva:{n}",
        )
        self.reg.anotar(
            self.enc, {"kind": "reserva_cerrada", "data": {}}, f"t-cerrada:{n}"
        )
        self.reg.anotar(
            self.enc,
            {
                "kind": "estrategia",
                "data": {
                    "rol": e.rol or "responsable",
                    "estrategia": e.estrategia,
                    "motivo": e.motivo,
                    "siguiente": e.siguiente,
                },
            },
            f"t-rol:{n}",
        )
        return _incorporar(self.reg, self.fold(), self.espacio, self.hora, self.d)

    def juicio(self, datos: dict) -> None:
        (self.d / "revision.json").write_text(
            json.dumps(
                {
                    "observaciones": str(self.d / "observaciones.json"),
                    "resultado": str(self.resultado),
                    "version": firma_producto(self.espacio),
                }
            )
        )
        datos.setdefault("version", firma_producto(self.espacio))
        self.resultado.write_text(json.dumps(datos))

    def eventos(self, kind: str) -> list[dict]:
        return [e for e in self.reg.eventos(self.enc) if e["kind"] == kind]


def _bloqueante(titulo: str) -> dict:
    return {
        "criterio": "comportamiento",
        "titulo": titulo,
        "reproduccion": "cat malo.txt -> X",
        "ancla": "malo.txt:1",
    }


def _interno(r: Recorrido) -> Path:
    p = r.espacio / ".interno"
    p.mkdir(exist_ok=True)
    return p


def _lanzaria(e) -> bool:
    return decision_proceso(e, "ausente", None, False, True, e.hora)["lanzar"]


def _despliegue_en_marcha(caso, clave: str):
    """Encargo pedido hasta desplegado con la fase ya integrada.

    Devuelve (registro, id, espacio). Sin receta declarada: el paso de
    despliegue llega a la comprobación de autoridad con un resultado pendiente,
    igual que cuando la receta sigue ejecutándose en el trabajador."""
    tmp = tempfile.TemporaryDirectory()
    caso.addCleanup(tmp.cleanup)
    raiz = Path(tmp.name)
    reg = Registro(raiz / "base")
    caso.addCleanup(reg.close)
    ident = ciclo_recibir(reg, clave)
    espacio = raiz / "espacio" / ident
    interno = espacio / ".interno"
    interno.mkdir(parents=True)
    (interno / "pedido-efecto.json").write_text(json.dumps({
        "id": "instalar", "operacion": "desplegar",
        "clave": "maquina-de-ensayo", "cuerpo": "version integrada",
    }))
    sha = "a" * 40
    reg.anotar(ident, {"kind": "entrega_externa", "data": {
        "paso": "integrado", "sha": sha, "recibo": sha, "hora": HORA,
    }}, "integrado:" + sha)
    return reg, ident, espacio


def ciclo_recibir(reg, clave: str):
    return recibir(
        reg, "desplegar la version integrada", clave, HORA,
        entrega_nivel="desplegado", repo="ejemplo/repo", base_rama="main",
    )["encargo"]


def _autoridad(reg, ident, alcanzable: bool, hora: str) -> None:
    reg.anotar(
        ident,
        {"kind": "autoridad", "data": {"alcanzable": alcanzable, "hora": hora}},
        f"autoridad-ensayo:{ident}:{alcanzable}:{hora}",
    )


class ElDespliegueQuePierdeAutoridadQuedaEnEsperaExplicita(unittest.TestCase):
    """El borde vigente de la causa 2: si la autoridad se pierde durante el
    paso de despliegue, el paso retorna sin registrar una espera y el
    planificador puede volver a lanzar un turno sobre el mismo paso."""

    def test_la_perdida_durante_el_paso_deja_espera_y_no_lanza_turnos(self):
        reg, ident, espacio = _despliegue_en_marcha(self, "autoridad-despliegue-1")

        def iniciar_con_caida(registro, enc, espacio, politica, hora):
            _autoridad(registro, enc.id, False, hora)
            return {"estado": "esperando", "motivo": "ejecutando receta"}

        with mock.patch.object(ejecutor, "iniciar", side_effect=iniciar_con_caida):
            final = _paso_despliegue(reg, reg.fold(ident), espacio, HORA)
        self.assertEqual(final.estado, "esperando_reintento")
        self.assertIn("autoridad", final.motivo)
        self.assertTrue(final.reanuda_en)
        self.assertFalse(final.escrituras)
        self.assertFalse(_lanzaria(final))
        self.assertEqual(final.relevos, 0)
        self.assertEqual(
            [e for e in reg.eventos(ident)
             if e["kind"] == "entrega_externa" and e["data"].get("paso") == "desplegado"],
            [],
        )

    def test_la_perdida_tras_aplicar_concilia_el_recibo_al_reanudar(self):
        reg, ident, espacio = _despliegue_en_marcha(self, "autoridad-despliegue-2")
        recibo = str(Path(reg.base) / "despliegues" / "recibo.json")

        def iniciar_con_caida(registro, enc, espacio, politica, hora):
            _autoridad(registro, enc.id, False, hora)
            return {"estado": "aplicado", "recibo": recibo}

        with mock.patch.object(ejecutor, "iniciar", side_effect=iniciar_con_caida):
            en_espera = _paso_despliegue(reg, reg.fold(ident), espacio, HORA)
        self.assertEqual(en_espera.estado, "esperando_reintento")
        self.assertEqual(
            [e for e in reg.eventos(ident)
             if e["kind"] == "entrega_externa" and e["data"].get("paso") == "desplegado"],
            [],
        )
        _autoridad(reg, ident, True, HORA_TARDE)
        self.assertTrue(reg.fold(ident).escrituras)
        reg.anotar(
            ident, {"kind": "plazo", "data": {"hora": HORA_TARDE}},
            f"plazo:{ident}:{en_espera.reanuda_en}",
        )
        self.assertEqual(reg.fold(ident).estado, "recibido")

        def iniciar_aplica(registro, enc, espacio, politica, hora):
            return {"estado": "aplicado", "recibo": recibo}

        with mock.patch.object(ejecutor, "iniciar", side_effect=iniciar_aplica):
            final = _paso_despliegue(reg, reg.fold(ident), espacio, HORA_TARDE)
        self.assertEqual(final.nivel_alcanzado, "desplegado")
        self.assertEqual(final.relevos, 0)
        desplegados = [
            e for e in reg.eventos(ident)
            if e["kind"] == "entrega_externa" and e["data"].get("paso") == "desplegado"
        ]
        self.assertEqual(len(desplegados), 1)
        self.assertEqual(desplegados[0]["data"]["recibo"], recibo)

    def test_una_parada_ordenada_no_queda_en_espera_de_autoridad(self):
        reg, ident, espacio = _despliegue_en_marcha(self, "autoridad-despliegue-3")
        from motor.encargos.ciclo import ordenar

        ordenar(reg, ident, "parar", "parada-de-ensayo", HORA)
        fresco = reg.fold(ident)
        self.assertFalse(fresco.intentos_nuevos)
        _paso_despliegue(reg, fresco, espacio, HORA)
        final = reg.fold(ident)
        self.assertNotEqual(final.estado, "esperando_reintento")
        self.assertEqual(
            [e for e in reg.eventos(ident) if e["kind"] == "espera"], [],
        )


class LaCorreccionVaciaTerminaEnDecisionExplicitaSinRevisores(unittest.TestCase):
    """La corrección vacía: cada vuelta entrega la misma versión sin atender el
    bloqueante. La escalera cuenta vueltas sin avance por los ids devueltos y
    termina en una decisión explícita (entrega con pendientes). El revisor no
    vuelve a correr sobre la versión que ya juzgó: un solo turno en toda la
    serie, el primero."""

    def test_las_vueltas_sin_avance_llegan_a_pendientes_sin_revisor_nuevo(self):
        with tempfile.TemporaryDirectory() as tmp:
            r = Recorrido(tmp, "escalera-1", "malo.txt con B")
            self.addCleanup(r.reg.close)
            (r.espacio / "malo.txt").write_text("X")

            # Primera revisión: el único turno del revisor de toda la serie.
            e = r.incorporar()
            self.assertEqual(e.estrategia, "revisar_evidencia")
            self.assertEqual(e.rol, "revisor")
            r.juicio(
                {
                    "veredicto": "cambios",
                    "evidencia": "lei malo.txt",
                    "bloqueantes": [_bloqueante("malo.txt no contiene B")],
                }
            )
            e = r.incorporar()
            self.assertEqual(e.estrategia, "investigar")
            self.assertNotEqual(e.rol, "revisor")

            def entrega_la_misma_version():
                e = r.incorporar()
                self.assertNotEqual(e.rol, "revisor")
                self.assertNotEqual(e.estrategia, "revisar_evidencia")
                return e

            e = entrega_la_misma_version()
            self.assertEqual(e.estrategia, "diagnosticar_bloqueo")
            self.assertEqual(e.sin_progreso, 1)
            e = entrega_la_misma_version()
            self.assertEqual(e.relevos, 1)
            self.assertEqual(dict(e.relevos_por_rol).get("responsable"), 1)
            self.assertNotEqual(e.estado, "necesita_usuario")
            e = entrega_la_misma_version()
            self.assertTrue(e.pendientes_entrega)
            self.assertFalse(_lanzaria(e))
            self.assertNotEqual(e.estado, "necesita_usuario")
            turnos_revisor = [
                ev for ev in r.eventos("estrategia")
                if (ev["data"].get("rol") or "responsable") == "revisor"
            ]
            self.assertEqual(len(turnos_revisor), 2)
            for ev in turnos_revisor:
                self.assertEqual(ev["data"].get("estrategia"), "revisar_evidencia")


class LaSerieNoConcluyenteUsaSuTopeSinEscalera(unittest.TestCase):
    """Las revisiones no_concluyente no pasan por la escalera de los bloqueos:
    el pedido del revisor vuelve al responsable y la repetición la cuenta y la
    corta el freno propio (`TOPE_NO_CONCLUYENTES`)."""

    def test_ningun_evento_de_escalera_y_salida_por_el_tope(self):
        with tempfile.TemporaryDirectory() as tmp:
            r = Recorrido(tmp, "escalera-3", "dato.txt con 42")
            self.addCleanup(r.reg.close)
            (r.espacio / "dato.txt").write_text("41")

            def vuelta(i: int):
                (r.espacio / "dato.txt").write_text(f"intento-{i}")
                e = r.incorporar()
                self.assertEqual(e.rol, "revisor")
                r.juicio(
                    {
                        "veredicto": "no_concluyente",
                        "evidencia": f"no pude decidir, vuelta {i}",
                    }
                )
                return r.incorporar()

            e = vuelta(1)
            self.assertEqual(e.estrategia, "investigar")
            self.assertEqual(e.sin_progreso, 0)
            self.assertNotEqual(e.estado, "necesita_usuario")
            e = vuelta(2)
            self.assertEqual(e.estrategia, "investigar")
            self.assertEqual(e.relevos, 0)
            e = vuelta(3)
            self.assertEqual(dict(e.relevos_por_rol).get("revisor"), 1)
            self.assertEqual(e.pendientes_entrega, ())
            e = vuelta(4)
            self.assertTrue(e.pendientes_entrega)
            self.assertEqual(r.eventos("sin_progreso"), [])


class ElHeadDesviadoRepetidoQuedaEnEspera(unittest.TestCase):
    """La conciliación del head no pasa por la escalera: el primer aviso vuelve
    al responsable y la repetición queda en espera visible hasta que el head
    concilie; un head distinto vuelve a abrir el aviso."""

    def test_espera_visible_sin_escalera_y_otro_head_reabre_el_aviso(self):
        with tempfile.TemporaryDirectory() as tmp:
            from motor.encargos.ciclo import _head_desviado, ahora_iso

            reg = Registro(Path(tmp) / "base")
            self.addCleanup(reg.close)
            ident = recibir(
                reg, "publica el PR", "escalera-head", ahora_iso()
            )["encargo"]
            reg.anotar(
                ident,
                {"kind": "fases", "data": {"fases": [
                    {"id": "a", "resultado": "algo concreto"},
                ]}},
                "fases-ensayo",
            )
            hora = ahora_iso()
            e = _head_desviado(reg, reg.fold(ident), hora, 7, "a" * 40, "f" * 40)
            self.assertEqual(e.estrategia, "investigar")
            self.assertEqual(e.rol, "responsable")
            e = _head_desviado(reg, reg.fold(ident), hora, 7, "a" * 40, "f" * 40)
            self.assertEqual(e.estado, "esperando_reintento")
            self.assertIn("head", e.motivo)
            self.assertTrue(e.reanuda_en)
            self.assertFalse(_lanzaria(e))
            # El plazo vence (lo hace el barrido) y llega un head distinto:
            # el aviso se vuelve a abrir para el responsable.
            reg.anotar(
                ident, {"kind": "plazo", "data": {"hora": hora}},
                f"plazo-ensayo:{ident}:{e.reanuda_en}",
            )
            self.assertEqual(reg.fold(ident).estado, "recibido")
            e = _head_desviado(reg, reg.fold(ident), hora, 7, "a" * 40, "b" * 40)
            self.assertEqual(e.estrategia, "investigar")
            self.assertNotEqual(e.estado, "esperando_reintento")
            self.assertEqual(e.estrategia, "investigar")
            avisos = [
                ev for ev in reg.eventos(ident)
                if ev["kind"] == "entrega_externa"
                and ev["data"].get("paso") == "head_vigente"
            ]
            self.assertEqual(len(avisos), 2)
            self.assertEqual(
                [ev for ev in reg.eventos(ident) if ev["kind"] == "sin_progreso"],
                [],
            )


class LaCorreccionEnInternoLlegaAlRevisor(unittest.TestCase):
    """h_014bb472: una corrección hecha en `.interno` (que la firma del
    producto no ve) no es una vuelta vacía. La escalera de la misma versión
    solo cuenta cuando la entrega tampoco cambió dentro de `.interno`; si
    cambió, la corrección llega al revisor."""

    def test_el_revisor_ve_la_correccion_de_interno(self):
        with tempfile.TemporaryDirectory() as tmp:
            r = Recorrido(tmp, "escalera-4", "malo.txt con B")
            self.addCleanup(r.reg.close)
            (r.espacio / "malo.txt").write_text("X")
            interno = _interno(r)
            bloqueante = {
                "criterio": "comportamiento",
                "titulo": "cuerpo-pr.md sin la tabla antes y despues",
                "reproduccion": "cat .interno/cuerpo-pr.md",
                "ancla": ".interno/cuerpo-pr.md:1",
            }

            e = r.incorporar()
            self.assertEqual(e.estrategia, "revisar_evidencia")
            r.juicio(
                {
                    "veredicto": "cambios",
                    "evidencia": "falta la tabla antes y despues en cuerpo-pr.md",
                    "bloqueantes": [bloqueante],
                }
            )
            e = r.incorporar()
            self.assertEqual(e.estrategia, "investigar")

            # El responsable corrige .interno: la firma del producto no
            # cambia, pero la entrega no es vacía y el revisor la ve.
            (interno / "cuerpo-pr.md").write_text("con la tabla antes y despues")
            e = r.incorporar()
            self.assertEqual(e.rol, "revisor")
            self.assertEqual(e.estrategia, "revisar_evidencia")
            self.assertEqual(r.eventos("sin_progreso"), [])

            # El revisor vuelve a pedir cambios y el responsable entrega de
            # nuevo sin tocar nada: ahí sí cuenta la vuelta sin avance, sin
            # otro turno del revisor.
            r.juicio(
                {
                    "veredicto": "cambios",
                    "evidencia": "la tabla sigue incompleta",
                    "bloqueantes": [bloqueante],
                }
            )
            e = r.incorporar()
            self.assertEqual(e.estrategia, "diagnosticar_bloqueo")
            self.assertEqual(e.sin_progreso, 1)
            e = r.incorporar()
            self.assertNotEqual(e.rol, "revisor")
            self.assertNotEqual(e.estrategia, "revisar_evidencia")
            self.assertEqual(e.relevos, 1)
            turnos_revisor = [
                ev for ev in r.eventos("estrategia")
                if (ev["data"].get("rol") or "responsable") == "revisor"
            ]
            self.assertEqual(len(turnos_revisor), 4)


class LaSerieVaciaConBuzonDelLanzadorCuentaSinRevisores(unittest.TestCase):
    """h_dabc2524: el lanzador prepara el buzón en cada turno y reescribe
    sesion.json, el catálogo y el cliente (`necesidades.preparar`). Esa
    escritura del host no es entrega del responsable: la huella de `.interno`
    la excluye y la corrección vacía sigue contando por la escalera, con el
    único turno del revisor de la serie."""

    def test_la_serie_vacia_cuenta_con_el_buzon_reescrito_en_cada_turno(self):
        from motor.encargos import necesidades

        with tempfile.TemporaryDirectory() as tmp:
            r = Recorrido(tmp, "escalera-5", "malo.txt con B")
            self.addCleanup(r.reg.close)
            (r.espacio / "malo.txt").write_text("X")

            def preparar_buzon():
                e = r.fold()
                necesidades.preparar(
                    r.espacio, e, f"intento-{e.generacion + 1}", {}
                )

            def entrega_del_responsable():
                preparar_buzon()
                e = r.incorporar()
                self.assertNotEqual(e.rol, "revisor")
                self.assertNotEqual(e.estrategia, "revisar_evidencia")
                return e

            preparar_buzon()
            e = r.incorporar()
            self.assertEqual(e.rol, "revisor")
            r.juicio(
                {
                    "veredicto": "cambios",
                    "evidencia": "lei malo.txt",
                    "bloqueantes": [_bloqueante("malo.txt no contiene B")],
                }
            )
            preparar_buzon()
            e = r.incorporar()
            self.assertEqual(e.estrategia, "investigar")

            e = entrega_del_responsable()
            self.assertEqual(e.estrategia, "diagnosticar_bloqueo")
            self.assertEqual(e.sin_progreso, 1)
            e = entrega_del_responsable()
            self.assertEqual(e.relevos, 1)
            e = entrega_del_responsable()
            self.assertTrue(e.pendientes_entrega)
            self.assertFalse(_lanzaria(e))
            self.assertNotEqual(e.estado, "necesita_usuario")
            turnos_revisor = [
                ev for ev in r.eventos("estrategia")
                if (ev["data"].get("rol") or "responsable") == "revisor"
            ]
            self.assertEqual(len(turnos_revisor), 2)


class LaSerieVaciaConCanalesDelHostCuentaSinRevisores(unittest.TestCase):
    """F1 (DeepSeek): el host escribe en `.interno` fuera del buzón
    (`entregas` con los datos de las herramientas, `logs` con los logs del CI)
    y reescribe el buzón en cada lanzamiento. Esa escritura no es entrega del
    responsable: la huella la excluye y la corrección vacía sigue contando por
    la escalera con el único turno del revisor de la serie."""

    def test_la_serie_vacia_cuenta_con_los_canales_del_host_activos(self):
        from motor.encargos import necesidades

        with tempfile.TemporaryDirectory() as tmp:
            r = Recorrido(tmp, "escalera-6", "malo.txt con B")
            self.addCleanup(r.reg.close)
            (r.espacio / "malo.txt").write_text("X")

            def escribir_canales_del_host():
                preparar_buzon()
                entregas = r.espacio / ".interno" / "entregas" / "sol-1"
                entregas.mkdir(parents=True, exist_ok=True)
                (entregas / "datos.json").write_text(
                    json.dumps({"datos": f"corrida-{time.time()}"})
                )
                logs = r.espacio / ".interno" / "logs"
                logs.mkdir(parents=True, exist_ok=True)
                (logs / "job-bateria.log").write_text("salida del CI\n")

            def preparar_buzon():
                e = r.fold()
                necesidades.preparar(
                    r.espacio, e, f"intento-{e.generacion + 1}", {}
                )

            def entrega_del_responsable():
                escribir_canales_del_host()
                e = r.incorporar()
                self.assertNotEqual(e.rol, "revisor")
                self.assertNotEqual(e.estrategia, "revisar_evidencia")
                return e

            escribir_canales_del_host()
            e = r.incorporar()
            self.assertEqual(e.rol, "revisor")
            r.juicio(
                {
                    "veredicto": "cambios",
                    "evidencia": "lei malo.txt",
                    "bloqueantes": [_bloqueante("malo.txt no contiene B")],
                }
            )
            escribir_canales_del_host()
            e = r.incorporar()
            self.assertEqual(e.estrategia, "investigar")

            e = entrega_del_responsable()
            self.assertEqual(e.estrategia, "diagnosticar_bloqueo")
            self.assertEqual(e.sin_progreso, 1)
            e = entrega_del_responsable()
            self.assertEqual(e.relevos, 1)
            e = entrega_del_responsable()
            self.assertTrue(e.pendientes_entrega)
            self.assertFalse(_lanzaria(e))
            turnos_revisor = [
                ev for ev in r.eventos("estrategia")
                if (ev["data"].get("rol") or "responsable") == "revisor"
            ]
            self.assertEqual(len(turnos_revisor), 2)


class LaHuellaInternaSoloCuentaArchivosRegulares(unittest.TestCase):
    """F2 (DeepSeek): `_digest_interno` no debe leer entradas de `.interno`
    que no sean archivos regulares: un enlace roto no puede tumbar la
    incorporación ni cambiar la identidad de la entrega."""

    def test_un_enlace_roto_no_cambia_la_huella_ni_cae(self):
        from motor.encargos.ciclo import _digest_interno

        with tempfile.TemporaryDirectory() as tmp:
            r = Recorrido(tmp, "escalera-7", "malo.txt con B")
            self.addCleanup(r.reg.close)
            (r.espacio / "malo.txt").write_text("X")
            interno = _interno(r)
            (interno / "cuerpo-pr.md").write_text("con la tabla")
            antes = _digest_interno(r.espacio)
            self.assertTrue(antes)

            enlace = interno / "roto"
            try:
                enlace.symlink_to(interno / "inexistente")
            except (OSError, NotImplementedError):
                self.skipTest("el host no permite crear enlaces")
            self.assertFalse(enlace.exists())
            self.assertEqual(_digest_interno(r.espacio), antes)
            e = r.incorporar()
            self.assertEqual(e.estrategia, "revisar_evidencia")


class LosIdsDevueltosGobiernanLaEscalera(unittest.TestCase):
    """Cerrar un bloqueante o recibir uno nuevo reinicia la escalera, aunque
    queden otros abiertos: la comparación es por los ids devueltos."""

    def test_cerrar_uno_de_varios_reinicia_la_escalera(self):
        with tempfile.TemporaryDirectory() as tmp:
            r = Recorrido(tmp, "escalera-2", "malo.txt con B")
            self.addCleanup(r.reg.close)
            (r.espacio / "malo.txt").write_text("X")

            def vuelta(bloqueantes):
                r.incorporar()
                r.juicio(
                    {
                        "veredicto": "cambios",
                        "evidencia": "lei malo.txt",
                        "bloqueantes": bloqueantes,
                    }
                )
                return r.incorporar()

            e = vuelta([_bloqueante("malo.txt no contiene B")])
            self.assertEqual(e.estrategia, "investigar")
            e = vuelta([_bloqueante("malo.txt no contiene B")])
            self.assertEqual(e.sin_progreso, 1)
            # El revisor cierra el bloqueante y abre otro: información nueva,
            # la escalera empieza de cero aunque el producto cambie o no.
            (r.espacio / "malo.txt").write_text("B")
            e = vuelta([_bloqueante("malo.txt falla en el paso 2")])
            self.assertEqual(e.sin_progreso, 0)
            self.assertEqual(e.estrategia, "investigar")
            ids_devueltos = set(e.devuelto_con)
            self.assertEqual(
                ids_devueltos,
                {h.id for h in bloqueantes_abiertos(e)},
            )
