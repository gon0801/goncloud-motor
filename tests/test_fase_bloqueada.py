"""El pendiente de una fase la marca bloqueada sin terminar el encargo completo.

Fase fase-bloqueada del pedido (causa 3): «entregar con pendientes» ya no
cierra el encargo entero. La fase queda `bloqueada` con causa y evidencia; la
siguiente fase con dependencias válidas sigue; el cierre distingue objetivo
completo de entrega parcial y `reabrir` sirve también para los cierres
parciales nuevos.
"""

from __future__ import annotations

import json
import tempfile
import time
import unittest
from datetime import datetime, timedelta
from pathlib import Path

from motor.encargos import ciclo
from motor.encargos.ciclo import _entregar_con_pendientes, ahora_iso, barrer
from motor.encargos.dominio import (
    Encargo,
    EntregaAutorizada,
    Fase,
    TransicionIlegal,
    _fase_disponible,
    dependencia_bloqueada,
    reducir,
)
from motor.encargos.registro import Registro
from motor.github import FakeGitHub
from tests.test_flujo_revisiones import Recorrido, _bloqueante, _entrega_pr


HORA = "2026-11-07T10:00:00+00:00"


class _GhPorNumero(FakeGitHub):
    """Comentarios por PR; los que no están declarados se aprueban limpios."""

    textos: dict[int, str | None] = {}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.textos = dict(type(self).textos)

    def comentarios_deepseek(self, repo, numero, sha=""):
        texto = self.textos.get(numero, "DeepSeek: APPROVED")
        if texto is None:
            return []
        return [{"texto": texto, "autor": "DeepSeek", "sha": sha}]


def _encargo(fases: tuple[Fase, ...], **kw) -> Encargo:
    base = {
        "estado": "recibido",
        "fase_activa": fases[-1].id if fases else "",
        "entrega": EntregaAutorizada(nivel="terminado"),
    }
    base.update(kw)
    return Encargo(
        id="enc_prueba",
        clave="prueba",
        huella="huella",
        director="directo",
        texto="pedido de prueba",
        adjuntos=(),
        restricciones=(),
        canal="",
        fases=fases,
        **base,
    )


def _preparada(fid: str, requiere: tuple = ()) -> Fase:
    return Fase(
        id=fid,
        resultado=f"resultado {fid}",
        estado="preparada",
        requiere=requiere,
        evidencia=f"{fid} comprobado",
    )


def _bloqueada(fid: str, causa: str, requiere: tuple = ()) -> Fase:
    return Fase(
        id=fid,
        resultado=f"resultado {fid}",
        estado="bloqueada",
        requiere=requiere,
        evidencia=f"la fase {fid} se entregó con pendientes",
        bloqueo_causa=causa,
    )


def _tarde(enc, minutos: int = 31) -> str:
    hora = datetime.fromisoformat(enc.hora.replace("Z", "+00:00"))
    return (hora + timedelta(minutes=minutos)).strftime("%Y-%m-%dT%H:%M:%SZ")


class QueFaseSigueTrasUnaBloqueada(unittest.TestCase):
    """«Siguen solo las fases cuyas dependencias, directas y transitivas, no
    incluyen una bloqueada. Una lista vacía no acredita independencia.»"""

    def test_la_bloqueada_no_se_ofrece_ni_habilita_a_sus_dependientes(self):
        fases = (
            _preparada("a"),
            _bloqueada("b", "sin modelo", requiere=(("a", ""),)),
            Fase(id="c", resultado="C", estado="pendiente", requiere=(("b", ""),)),
            Fase(id="d", resultado="D", estado="pendiente", requiere=(("c", ""),)),
        )
        self.assertIsNone(_fase_disponible(fases))

    def test_una_fase_independiente_sigue_tras_una_bloqueada(self):
        fases = (
            _preparada("a"),
            _bloqueada("b", "sin modelo", requiere=(("a", ""),)),
            Fase(id="c", resultado="C", estado="pendiente", requiere=(("a", ""),)),
        )
        disponible = _fase_disponible(fases)
        self.assertIsNotNone(disponible)
        self.assertEqual(disponible.id, "c")

    def test_sin_requiere_la_antesora_manda_el_orden_secuencial(self):
        sin_plan = (
            _preparada("a"),
            _bloqueada("b", "sin modelo"),
            Fase(id="c", resultado="C", estado="pendiente"),
        )
        self.assertIsNone(_fase_disponible(sin_plan))
        con_plan = (
            _preparada("a"),
            _bloqueada("b", "sin modelo"),
            Fase(id="c", resultado="C", estado="pendiente", requiere=(("a", ""),)),
        )
        disponible = _fase_disponible(con_plan)
        self.assertIsNotNone(disponible)
        self.assertEqual(disponible.id, "c")


class ElEventoDeBloqueoDeFase(unittest.TestCase):
    def _con_siguiente(self) -> Encargo:
        return _encargo(
            (
                _preparada("a"),
                Fase(
                    id="b",
                    resultado="B",
                    estado="entrega",
                    requiere=(("a", ""),),
                ),
                Fase(id="c", resultado="C", estado="pendiente", requiere=(("a", ""),)),
            ),
            fase_activa="b",
            pendientes_entrega=("El relevo agotó los modelos autorizados",),
        )

    def test_marca_la_fase_bloqueada_y_avanza_a_la_valida(self):
        e = reducir(
            self._con_siguiente(),
            {
                "kind": "bloqueo_fase",
                "data": {
                    "causa": "el relevo no avanza",
                    "evidencia": "la fase b se entrega con pendientes abiertos",
                    "hora": HORA,
                },
            },
        )
        b = next(f for f in e.fases if f.id == "b")
        self.assertEqual(b.estado, "bloqueada")
        self.assertEqual(b.bloqueo_causa, "el relevo no avanza")
        self.assertEqual(b.evidencia, "la fase b se entrega con pendientes abiertos")
        self.assertEqual(b.pendientes, ("El relevo agotó los modelos autorizados",))
        self.assertEqual(e.pendientes_entrega, ())
        self.assertEqual(e.estado, "recibido")
        self.assertEqual(e.fase_activa, "c")
        self.assertEqual(e.rol, "responsable")
        self.assertEqual(e.estrategia, "investigar")
        self.assertIsNotNone(e.cierre_forzado_fase)
        self.assertEqual(e.cierre_forzado_fase.id, "b")
        self.assertTrue(e.cierre_forzado_huella)

    def test_sin_fase_valida_el_encargo_termina_como_entrega_parcial(self):
        e = _encargo(
            (
                _preparada("a"),
                Fase(id="b", resultado="B", estado="entrega", requiere=(("a", ""),)),
                Fase(id="c", resultado="C", estado="pendiente", requiere=(("b", ""),)),
            ),
            fase_activa="b",
            pendientes_entrega=("el bloqueo sigue",),
        )
        e = reducir(
            e,
            {
                "kind": "bloqueo_fase",
                "data": {
                    "causa": "el bloqueo sigue",
                    "evidencia": "b con pendientes",
                    "hora": HORA,
                },
            },
        )
        self.assertEqual(e.estado, "terminado")
        self.assertFalse(e.objetivo_completo())
        self.assertIsNotNone(e.cierre_forzado_fase)
        self.assertTrue(e.cierre_forzado_huella)
        self.assertEqual(e.siguiente, "ninguna")

    def test_sin_evidencia_se_rechaza(self):
        with self.assertRaises(TransicionIlegal):
            reducir(self._con_siguiente(), {"kind": "bloqueo_fase", "data": {"hora": HORA}})

    def test_una_fase_ya_bloqueada_no_se_bloquea_otra_vez(self):
        e = _encargo(
            (_preparada("a"), _bloqueada("b", "sin modelo")),
            fase_activa="b",
        )
        with self.assertRaises(TransicionIlegal):
            reducir(
                e,
                {
                    "kind": "bloqueo_fase",
                    "data": {"causa": "x", "evidencia": "y", "hora": HORA},
                },
            )

    def test_la_ultima_fase_no_espera_una_dependencia_condenada(self):
        e = _encargo(
            (
                _preparada("a"),
                _bloqueada("b", "sin modelo", requiere=(("a", ""),)),
                Fase(id="c", resultado="C", estado="pendiente", requiere=(("b", ""),)),
            ),
            fase_activa="a",
        )
        e = reducir(
            e,
            {
                "kind": "comprobacion",
                "data": {
                    "clase": "confirmado",
                    "evidencia": "a terminó de verdad",
                    "hora": HORA,
                },
            },
        )
        self.assertEqual(e.estado, "terminado")
        self.assertFalse(e.objetivo_completo())


class BancoDeTresFases:
    """Banco compartido: tres fases sobre un repo bare y el ladder de dos."""

    def _banco(self, caso, tmp: str, clave: str):
        vuelta, r = _entrega_pr(caso, tmp, clave, FakeGitHub, nivel="pr")
        r.reg.anotar(
            r.enc,
            {
                "kind": "fases",
                "data": {
                    "fases": [
                        {"id": "uno", "resultado": "saludo.txt con uno"},
                        {
                            "id": "dos",
                            "resultado": "saludo.txt con dos",
                            "requiere": ["uno"],
                        },
                        {
                            "id": "tres",
                            "resultado": "saludo.txt con tres",
                            "requiere": ["uno"],
                        },
                    ],
                    "hora": r.hora,
                },
            },
            f"fases-{clave}",
        )
        return vuelta, r

    def _ladder_de_dos(self, r: Recorrido):
        self.assertEqual(
            ciclo._asegurar_repo(r.fold(), r.espacio, r.reg, r.hora), ""
        )
        (r.espacio / "malo.txt").write_text("X")
        e = r.incorporar()
        self.assertEqual(e.rol, "revisor")
        r.juicio(
            {
                "veredicto": "cambios",
                "evidencia": "lei malo.txt",
                "bloqueantes": [_bloqueante("malo.txt no contiene B")],
            }
        )
        for _ in range(4):
            e = r.incorporar()
            self.assertNotEqual(e.rol, "revisor")
        self.assertTrue(e.pendientes_entrega)
        return e

class ElPendienteDeUnaFase(BancoDeTresFases, unittest.TestCase):
    """Recorrido real: la escalera de dos termina en pendientes y el
    supervisor entrega; dos queda bloqueada y tres, que no depende de dos,
    sigue."""

    def test_b_queda_bloqueada_y_tres_con_dependencias_validas_sigue(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            vuelta, r = self._banco(self, tmp, "fb-continua")
            e = vuelta("uno")
            e = ciclo._paso_entrega(r.reg, r.fold(), vuelta.gh, _tarde(e))
            self.assertEqual(e.fase_activa, "dos")

            e = self._ladder_de_dos(r)
            e = _entregar_con_pendientes(r.reg, r.fold(), vuelta.gh, r.hora)

            dos = next(f for f in e.fases if f.id == "dos")
            self.assertEqual(dos.estado, "bloqueada")
            self.assertTrue(dos.bloqueo_causa)
            self.assertTrue(dos.evidencia)
            self.assertEqual(dos.nivel_alcanzado, "pr")
            self.assertTrue(dos.pr_numero)
            self.assertTrue(dos.pendientes)
            self.assertEqual(e.pendientes_entrega, ())
            self.assertEqual(e.estado, "recibido")
            self.assertEqual(e.fase_activa, "tres")
            self.assertEqual(r.eventos("cierre_forzado"), [])
            self.assertEqual(len(r.eventos("bloqueo_fase")), 1)

            self.assertEqual(
                ciclo._asegurar_repo(r.fold(), r.espacio, r.reg, r.hora), ""
            )
            (r.espacio / "saludo.txt").write_text("tres")
            r.incorporar()
            r.juicio(
                {"veredicto": "aprobado", "evidencia": "lei saludo.txt: tres"}
            )
            r.incorporar()
            e = ciclo._paso_entrega(r.reg, r.fold(), vuelta.gh, r.hora)
            e = ciclo._paso_entrega(r.reg, r.fold(), vuelta.gh, _tarde(e))

            self.assertEqual(e.estado, "terminado")
            self.assertFalse(e.objetivo_completo())
            tres = next(f for f in e.fases if f.id == "tres")
            self.assertEqual(tres.estado, "preparada")
            cuerpo = ciclo._cuerpo_pr(e, r.espacio)
            self.assertNotIn("Pendientes abiertos", cuerpo)

    def test_los_cambios_sin_confirmar_de_b_no_llegan_a_tres(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            vuelta, r = self._banco(self, tmp, "fb-separa")
            e = vuelta("uno")
            e = ciclo._paso_entrega(r.reg, r.fold(), vuelta.gh, _tarde(e))
            e = self._ladder_de_dos(r)
            (r.espacio / "solo-b.txt").write_text("exclusivo de dos")

            e = _entregar_con_pendientes(r.reg, r.fold(), None, r.hora)

            dos = next(f for f in e.fases if f.id == "dos")
            self.assertEqual(dos.estado, "bloqueada")
            self.assertEqual(dos.nivel_alcanzado, "ninguno")
            self.assertEqual(e.fase_activa, "tres")

            self.assertEqual(
                ciclo._asegurar_repo(r.fold(), r.espacio, r.reg, r.hora), ""
            )
            self.assertFalse(
                (r.espacio / "solo-b.txt").exists(),
                "el archivo exclusivo de la fase bloqueada sobrevivió el cambio de fase",
            )

            (r.espacio / "saludo.txt").write_text("tres")
            r.incorporar()
            manifiesto = [x for x, _ in r.fold().fase_actual.producto.manifiesto]
            self.assertNotIn("solo-b.txt", manifiesto)

    def test_el_primer_fallo_se_corrige_y_la_fase_no_se_bloquea(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            vuelta, r = self._banco(self, tmp, "fb-corrigio")
            e = vuelta("uno")
            e = ciclo._paso_entrega(r.reg, r.fold(), vuelta.gh, _tarde(e))
            self.assertEqual(e.fase_activa, "dos")

            (r.espacio / "malo.txt").write_text("X")
            e = r.incorporar()
            r.juicio(
                {
                    "veredicto": "cambios",
                    "evidencia": "lei malo.txt",
                    "bloqueantes": [_bloqueante("malo.txt no contiene B")],
                }
            )
            (r.espacio / "malo.txt").write_text("B")
            r.incorporar()
            r.juicio(
                {
                    "veredicto": "aprobado",
                    "evidencia": "cat malo.txt -> B",
                    "resueltos": [
                        h.id
                        for h in r.fold().fase_actual.hallazgos
                        if h.estado == "abierto"
                    ],
                }
            )
            r.incorporar()
            e = ciclo._paso_entrega(r.reg, r.fold(), vuelta.gh, r.hora)
            e = ciclo._paso_entrega(r.reg, r.fold(), vuelta.gh, _tarde(e))

            self.assertEqual(r.eventos("bloqueo_fase"), [])
            dos = next(f for f in e.fases if f.id == "dos")
            self.assertEqual(dos.estado, "preparada")


class ReabrirCierrePorFase(unittest.TestCase):
    def _abrir_intento(self, r: Recorrido, serie: str) -> None:
        r.reg.anotar(
            r.enc,
            {
                "kind": "intento_abierto",
                "data": {
                    "id": f"intento-{serie}",
                    "pid": 1,
                    "inicio": r.hora,
                    "rol": r.fold().rol or "responsable",
                    "hora": r.hora,
                },
            },
            f"t-intento-{serie}",
        )

    def _cerrar_intento(self, r: Recorrido) -> None:
        r.reg.anotar(
            r.enc,
            {
                "kind": "intento_cerrado",
                "data": {"pid": 1, "inicio": r.hora, "hora": r.hora},
            },
            f"t-intento-cerrado:{r.fold().generacion}",
        )

    def _vueltas(self, r: Recorrido, n: int) -> None:
        for _ in range(n):
            r.incorporar()
            self._cerrar_intento(r)

    def _hasta_pendientes(self, vuelta, r: Recorrido):
        (r.espacio / "malo.txt").write_text("X")
        e = r.incorporar()
        self.assertEqual(e.rol, "revisor")
        r.juicio(
            {
                "veredicto": "cambios",
                "evidencia": "lei malo.txt",
                "bloqueantes": [_bloqueante("malo.txt no contiene B")],
            }
        )
        for _ in range(4):
            e = r.incorporar()
        self.assertTrue(e.pendientes_entrega)
        return e

    def test_un_cierre_nuevo_de_una_sola_fase_hasta_pr_se_reabre(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            vuelta, r = _entrega_pr(self, tmp, "fb-reabre", FakeGitHub, nivel="pr")
            e = self._hasta_pendientes(vuelta, r)
            e = _entregar_con_pendientes(r.reg, r.fold(), vuelta.gh, r.hora)

            self.assertEqual(e.estado, "terminado")
            self.assertTrue(e.pr_numero, "el PR con pendientes se publica igual")
            bloqueada = next(f for f in e.fases if f.id == e.fase_activa)
            self.assertEqual(bloqueada.estado, "bloqueada")
            self.assertTrue(bloqueada.pr_numero, "la fase bloqueada conserva su PR")
            fase = e.cierre_forzado_fase
            self.assertIsNotNone(fase)
            self.assertEqual(fase.id, bloqueada.id)
            huella_vigente = e.cierre_forzado_huella

            salida = ciclo.ordenar(
                r.reg, r.enc, "reabrir", "reabrir-parcial", r.hora,
                fase=fase.id, cierre=huella_vigente,
            )
            self.assertFalse(salida.get("rechazada"), salida.get("motivo"))
            nuevo = r.fold()
            self.assertEqual(nuevo.estado, "detenido")
            self.assertEqual(nuevo.fase_activa, fase.id)
            self.assertEqual(
                nuevo.fase_actual.pr_numero,
                e.pr_numero,
                "la reapertura conserva el PR",
            )

            ciclo.ordenar(r.reg, r.enc, "continuar", "continuar-parcial", r.hora)
            self.assertEqual(r.fold().estado, "recibido")
            self._abrir_intento(r, "post-continuar")

            (r.espacio / "malo.txt").write_text("B")
            r.incorporar()
            self._cerrar_intento(r)
            r.juicio(
                {
                    "veredicto": "aprobado",
                    "evidencia": "cat malo.txt -> B",
                    "resueltos": [
                        h.id
                        for h in r.fold().fase_actual.hallazgos
                        if h.estado == "abierto"
                    ],
                }
            )
            r.incorporar()
            e = ciclo._paso_entrega(r.reg, r.fold(), vuelta.gh, r.hora)
            e = ciclo._paso_entrega(r.reg, r.fold(), vuelta.gh, _tarde(e))
            self.assertEqual(e.estado, "terminado")
            self.assertTrue(e.objetivo_completo())

    def test_una_orden_de_un_cierre_anterior_se_rechaza(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            vuelta, r = _entrega_pr(self, tmp, "fb-orden-vieja", FakeGitHub, nivel="pr")
            e = self._hasta_pendientes(vuelta, r)
            e = _entregar_con_pendientes(r.reg, r.fold(), vuelta.gh, r.hora)
            vieja = e.cierre_forzado_huella
            fase = e.cierre_forzado_fase.id

            salida = ciclo.ordenar(
                r.reg, r.enc, "reabrir", "reabrir-vieja", r.hora,
                fase=fase, cierre=vieja,
            )
            self.assertFalse(salida.get("rechazada"), salida.get("motivo"))
            ciclo.ordenar(r.reg, r.enc, "continuar", "continuar-vieja", r.hora)
            self._abrir_intento(r, "segundo")
            self._vueltas(r, 4)
            self.assertTrue(r.fold().pendientes_entrega)
            e = _entregar_con_pendientes(r.reg, r.fold(), vuelta.gh, r.hora)
            self.assertEqual(e.estado, "terminado")
            nueva = e.cierre_forzado_huella
            self.assertNotEqual(nueva, vieja)

            salida = ciclo.ordenar(
                r.reg, r.enc, "reabrir", "orden-tardia", r.hora,
                fase=fase, cierre=vieja,
            )
            self.assertTrue(salida.get("rechazada"))
            self.assertEqual(r.fold().estado, "terminado")


class ElCierreDistingueObjetivoCompletoDeEntregaParcial(unittest.TestCase):
    def test_el_aviso_del_cierre_parcial_enumera_con_motivos(self):
        from motor.integraciones import directores

        enc = _encargo(
            (
                _preparada("uno"),
                _bloqueada("dos", "el relevo agotó los modelos autorizados"),
                Fase(
                    id="cuatro",
                    resultado="cuatro",
                    estado="pendiente",
                    requiere=(("dos", ""),),
                ),
            ),
            estado="terminado",
            fase_activa="dos",
            evidencia=("la fase dos se entregó con pendientes",),
        )
        cuerpo = directores._cuerpo_de_cierre(enc)
        self.assertIn("parcial", cuerpo.lower())
        self.assertIn("dos", cuerpo)
        self.assertIn("el relevo agotó", cuerpo)
        self.assertIn("cuatro", cuerpo)
        self.assertIn("no se ejecutó", cuerpo.lower())

    def test_el_aviso_de_un_cierre_completo_no_cambia(self):
        from motor.integraciones import directores

        enc = _encargo(
            (_preparada("uno"), _preparada("dos")),
            estado="terminado",
            fase_activa="dos",
            evidencia=("todo listo",),
        )
        cuerpo = directores._cuerpo_de_cierre(enc)
        self.assertNotIn("parcial", cuerpo.lower())

    def test_la_vista_cuenta_las_fases_bloqueadas(self):
        from motor.encargos.dominio import vista

        enc = _encargo(
            (
                _preparada("a"),
                _bloqueada("b", "sin modelo"),
            ),
            estado="terminado",
            fase_activa="b",
            evidencia=("la fase b se entregó con pendientes",),
        )
        self.assertEqual(vista(enc)["objetivo"]["bloqueadas"], 1)
        self.assertFalse(vista(enc)["objetivo"]["completo"])


class ElHallazgoTardioDeLaBloqueadaNoSePierde(unittest.TestCase):
    def test_un_medium_tardio_en_el_pr_de_la_fase_bloqueada_se_avisa(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            from datetime import datetime, timedelta, timezone
            from tests.test_flujo_revisiones import _paso_entrega

            vuelta, r = _entrega_pr(self, tmp, "fb-tardio", _GhPorNumero, nivel="pr")
            ahora = datetime.now(timezone.utc)
            # uno se cierra horas antes; dos se bloquea hace dos minutos: la
            # época del cierre del encargo es la del bloqueo de dos.
            r.hora = (ahora - timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M:%SZ")
            r.reg.anotar(
                r.enc,
                {
                    "kind": "fases",
                    "data": {
                        "fases": [
                            {"id": "uno", "resultado": "saludo.txt con uno"},
                            {
                                "id": "dos",
                                "resultado": "saludo.txt con dos",
                                "requiere": ["uno"],
                            },
                            {
                                "id": "cuatro",
                                "resultado": "saludo.txt con cuatro",
                                "requiere": ["dos"],
                            },
                        ],
                        "hora": r.hora,
                    },
                },
                "fases-tardio",
            )
            (r.espacio / "saludo.txt").write_text("uno")
            r.incorporar()
            r.juicio({"veredicto": "aprobado", "evidencia": "lei saludo.txt: uno"})
            r.incorporar()
            e = _paso_entrega(r.reg, r.fold(), vuelta.gh, r.hora)
            self.assertEqual(e.fase_activa, "dos")

            r.hora = (ahora - timedelta(minutes=2)).strftime("%Y-%m-%dT%H:%M:%SZ")
            (r.espacio / "malo.txt").write_text("X")
            e = r.incorporar()
            self.assertEqual(e.rol, "revisor")
            r.juicio(
                {
                    "veredicto": "cambios",
                    "evidencia": "lei malo.txt",
                    "bloqueantes": [_bloqueante("malo.txt no contiene B")],
                }
            )
            for _ in range(4):
                e = r.incorporar()
            self.assertTrue(e.pendientes_entrega)
            e = _entregar_con_pendientes(r.reg, r.fold(), vuelta.gh, r.hora)
            self.assertEqual(e.estado, "terminado")
            pr_dos = next(f for f in e.fases if f.id == "dos").pr_numero
            self.assertTrue(pr_dos)

            vuelta.gh.textos[pr_dos] = (
                "DeepSeek: CHANGES_REQUESTED\nai-review:findings="
                + json.dumps(
                    [
                        {
                            "id": "F9",
                            "severity": "medium",
                            "status": "open",
                            "title": "hallazgo tardio",
                            "file": "malo.txt",
                        }
                    ]
                )
            )
            barrer(
                r.reg, ahora_iso(), time.time(),
                lambda *a, **k: None, gh=vuelta.gh,
            )
            e = r.fold()
            self.assertEqual(e.estado, "terminado")
            self.assertTrue(
                any("F9" in n and "fase dos" in n for n in e.notas_pr), e.notas_pr
            )

    def test_la_epoca_del_cierre_parcial_es_la_del_bloqueo(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            from tests.test_flujo_revisiones import _paso_entrega

            vuelta, r = _entrega_pr(self, tmp, "fb-epoch", _GhPorNumero, nivel="pr")
            r.reg.anotar(
                r.enc,
                {
                    "kind": "fases",
                    "data": {
                        "fases": [
                            {"id": "uno", "resultado": "saludo.txt con uno"},
                            {
                                "id": "dos",
                                "resultado": "saludo.txt con dos",
                                "requiere": ["uno"],
                            },
                            {
                                "id": "cuatro",
                                "resultado": "saludo.txt con cuatro",
                                "requiere": ["dos"],
                            },
                        ],
                        "hora": r.hora,
                    },
                },
                "fases-epoch",
            )
            (r.espacio / "saludo.txt").write_text("uno")
            r.incorporar()
            r.juicio({"veredicto": "aprobado", "evidencia": "lei saludo.txt: uno"})
            r.incorporar()
            e = _paso_entrega(r.reg, r.fold(), vuelta.gh, r.hora)
            tarde = "2099-01-01T00:00:00Z"
            (r.espacio / "malo.txt").write_text("X")
            e = r.incorporar()
            r.juicio(
                {
                    "veredicto": "cambios",
                    "evidencia": "lei malo.txt",
                    "bloqueantes": [_bloqueante("malo.txt no contiene B")],
                }
            )
            for _ in range(4):
                r.incorporar()
            e = _entregar_con_pendientes(r.reg, r.fold(), vuelta.gh, tarde)
            self.assertEqual(e.estado, "terminado")
            epoch_bloqueo = ciclo._epoch_de_hora(tarde)
            self.assertEqual(ciclo._cierre_epoch(r.reg, e), epoch_bloqueo)


class LasNotasDelMotorVivenPorFase(BancoDeTresFases, unittest.TestCase):
    def test_el_pr_de_la_fase_siguiente_no_copia_notas_de_otras(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            from tests.test_flujo_revisiones import _paso_entrega

            vuelta, r = self._banco(self, tmp, "fb-notas")
            e = vuelta("uno")
            # uno cierra sin DeepSeek: el motor anota su nota en la fase uno.
            e = _paso_entrega(r.reg, r.fold(), vuelta.gh, r.hora)
            e = _paso_entrega(r.reg, r.fold(), vuelta.gh, _tarde(e))
            self.assertTrue(e.notas_pr, "la nota sin revisión quedó anotada")
            e = ElPendienteDeUnaFase._ladder_de_dos(self, r)
            # dos sin puerto: su nota de publicación fallida también es de dos.
            e = _entregar_con_pendientes(r.reg, r.fold(), None, r.hora)
            self.assertEqual(e.fase_activa, "tres")

            self.assertEqual(
                ciclo._asegurar_repo(r.fold(), r.espacio, r.reg, r.hora), ""
            )
            (r.espacio / "saludo.txt").write_text("tres")
            r.incorporar()
            cuerpo = ciclo._cuerpo_pr(r.fold(), r.espacio)
            self.assertNotIn("Notas del motor", cuerpo)
            self.assertNotIn("sin revisión de DeepSeek", cuerpo)
            self.assertNotIn("no se pudo publicar el PR", cuerpo)
            # Las notas de cada fase siguen en su fase: no se pierden.
            uno = next(f for f in r.fold().fases if f.id == "uno")
            dos = next(f for f in r.fold().fases if f.id == "dos")
            self.assertTrue(any("sin revisión de DeepSeek" in n for n in uno.notas))
            self.assertTrue(any("no se pudo publicar" in n for n in dos.notas))

    def test_el_pr_de_la_fase_bloqueada_conserva_sus_notas_y_pendientes(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            vuelta, r = self._banco(self, tmp, "fb-notas-b")
            e = vuelta("uno")
            e = ciclo._paso_entrega(r.reg, r.fold(), vuelta.gh, _tarde(e))
            e = ElPendienteDeUnaFase._ladder_de_dos(self, r)
            e = _entregar_con_pendientes(r.reg, r.fold(), vuelta.gh, r.hora)
            dos = next(f for f in e.fases if f.id == "dos")
            self.assertEqual(dos.estado, "bloqueada")
            self.assertTrue(dos.pendientes)
            self.assertTrue(dos.notas or e.notas_pr)
            cuerpo = ciclo._cuerpo_pr(e, r.espacio, "dos")
            self.assertIn("Pendientes abiertos", cuerpo)
            cuerpo_tres = ciclo._cuerpo_pr(e, r.espacio, "tres")
            self.assertNotIn("Pendientes abiertos", cuerpo_tres)


AJUSTE_DIRECTOR = (
    "Correccion del director: (1) El Bloque A ya esta integrado en master "
    "(PR #534, 3652f3d); no lo repitas ni crees fase para el. Las fases son "
    "exactamente 8 con ids B, C, D, E, F, G, H, I (las letras de los bloques "
    "del plan)."
)


class LaAdopcionConsultaLosAjustesDelDirector(unittest.TestCase):
    def _con_ajuste(self, fases: tuple[Fase, ...], ajuste: str = AJUSTE_DIRECTOR):
        return reducir(
            _encargo(fases),
            {
                "kind": "ajuste",
                "data": {
                    "autor": "director:hermes",
                    "texto": ajuste,
                    "hora": HORA,
                },
            },
        )

    def test_la_propuesta_con_fases_fuera_de_la_enumeracion_es_defecto(self):
        e = self._con_ajuste(())
        with self.assertRaises(TransicionIlegal) as ctx:
            reducir(
                e,
                {
                    "kind": "fases",
                    "data": {
                        "fases": [
                            {
                                "id": "a-spec-y-medicion",
                                "resultado": "la propuesta queda medida",
                            },
                            {
                                "id": "b-receta-despliegue",
                                "resultado": "la receta de despliegue",
                            },
                        ],
                        "hora": HORA,
                    },
                },
            )
        self.assertIn("a-spec-y-medicion", str(ctx.exception))

    def test_la_propuesta_dentro_de_la_enumeracion_se_adopta(self):
        e = self._con_ajuste(())
        e = reducir(
            e,
            {
                "kind": "fases",
                "data": {
                    "fases": [
                        {
                            "id": "b-receta-despliegue",
                            "resultado": "la receta de despliegue",
                        },
                        {
                            "id": "c-meta-calculada",
                            "resultado": "la meta calculada",
                            "requiere": ["b-receta-despliegue"],
                        },
                    ],
                    "hora": HORA,
                },
            },
        )
        self.assertEqual([f.id for f in e.fases], ["b-receta-despliegue", "c-meta-calculada"])

    def test_un_ajuste_sin_enumeracion_no_marca_ni_salta_fases(self):
        ajuste = (
            "Correccion del director: El Bloque A ya esta integrado en master "
            "(PR #534); no lo repitas ni crees fase para el."
        )
        e = self._con_ajuste((), ajuste)
        e = reducir(
            e,
            {
                "kind": "fases",
                "data": {
                    "fases": [
                        {
                            "id": "a-spec-y-medicion",
                            "resultado": "la propuesta queda medida",
                        },
                        {
                            "id": "b-receta-despliegue",
                            "resultado": "la receta de despliegue",
                            "requiere": ["a-spec-y-medicion"],
                        },
                    ],
                    "hora": HORA,
                },
            },
        )
        # La prosa del ajuste no muta el estado de las fases: sin enumeración
        # estructurada, el orden secuencial y sus compuertas mandan.
        a = next(f for f in e.fases if f.id == "a-spec-y-medicion")
        self.assertEqual(a.estado, "pendiente")
        self.assertEqual(a.nivel_alcanzado, "ninguno")
        disponible = _fase_disponible(e.fases)
        self.assertIsNotNone(disponible)
        self.assertEqual(disponible.id, "a-spec-y-medicion")

    def test_los_ajustes_ordinarios_no_marcan_la_fase_en_curso(self):
        frases = [
            "En la fase B no lo repitas con bash; usa python para la receta.",
            "La receta del bloque B falló en el ensayo: no la repitas igual, "
            "corrige el rollback primero.",
            "No crees fase nueva para los tests; la fase C los incluye.",
            "La fase B todavía no está integrada; no la repitas desde cero, "
            "parte de la rama anterior.",
        ]
        for texto in frases:
            with self.subTest(texto=texto):
                e = _encargo(
                    (
                        _preparada("a-spec"),
                        Fase(
                            id="b-receta",
                            resultado="B",
                            estado="trabajo",
                            requiere=(("a-spec", ""),),
                        ),
                        Fase(
                            id="c-dinero",
                            resultado="C",
                            estado="pendiente",
                            requiere=(("b-receta", ""),),
                        ),
                    ),
                    fase_activa="b-receta",
                )
                e = reducir(
                    e,
                    {
                        "kind": "ajuste",
                        "data": {
                            "autor": "director:hermes",
                            "texto": texto,
                            "hora": HORA,
                        },
                    },
                )
                b = next(f for f in e.fases if f.id == "b-receta")
                self.assertEqual(b.estado, "trabajo")
                self.assertEqual(b.nivel_alcanzado, "ninguno")
                c = next(f for f in e.fases if f.id == "c-dinero")
                self.assertEqual(c.estado, "pendiente")
                disponible = _fase_disponible(e.fases)
                self.assertIsNotNone(disponible)
                self.assertEqual(disponible.id, "b-receta")

    def test_una_instruccion_normal_con_comas_no_enumera(self):
        ajuste = (
            "Declara las fases en fases.json con ids cortos, claros, en "
            "minusculas y sin tildes."
        )
        e = self._con_ajuste((), ajuste)
        e = reducir(
            e,
            {
                "kind": "fases",
                "data": {
                    "fases": [
                        {"id": "b-receta", "resultado": "b"},
                        {
                            "id": "c-meta",
                            "resultado": "c",
                            "requiere": ["b-receta"],
                        },
                    ],
                    "hora": HORA,
                },
            },
        )
        self.assertEqual([f.id for f in e.fases], ["b-receta", "c-meta"])

    def test_una_prosa_con_ejemplo_de_ids_no_enumera(self):
        ajuste = (
            "Escribe las fases con ids legibles, por ejemplo b-receta, c-meta."
        )
        e = self._con_ajuste((), ajuste)
        e = reducir(
            e,
            {
                "kind": "fases",
                "data": {
                    "fases": [
                        {"id": "b-receta", "resultado": "b"},
                        {
                            "id": "c-meta",
                            "resultado": "c",
                            "requiere": ["b-receta"],
                        },
                    ],
                    "hora": HORA,
                },
            },
        )
        self.assertEqual([f.id for f in e.fases], ["b-receta", "c-meta"])

    def test_la_prosa_compuesta_con_guiones_no_enumera(self):
        ajuste = (
            "Escribe fases.json con ids auto-explicativos, bien-formados y "
            "faciles-de-leer."
        )
        e = self._con_ajuste((), ajuste)
        e = reducir(
            e,
            {
                "kind": "fases",
                "data": {
                    "fases": [
                        {"id": "b-receta", "resultado": "b"},
                        {
                            "id": "c-meta",
                            "resultado": "c",
                            "requiere": ["b-receta"],
                        },
                    ],
                    "hora": HORA,
                },
            },
        )
        self.assertEqual([f.id for f in e.fases], ["b-receta", "c-meta"])

    def test_un_ancla_con_conteo_mentiroso_no_ata(self):
        ajuste = (
            "Correccion del director: las fases son exactamente 5 con ids "
            "b-receta, c-meta, d-sombra."
        )
        e = self._con_ajuste((), ajuste)
        e = reducir(
            e,
            {
                "kind": "fases",
                "data": {
                    "fases": [
                        {"id": "b-receta", "resultado": "b"},
                        {
                            "id": "c-meta",
                            "resultado": "c",
                            "requiere": ["b-receta"],
                        },
                        {
                            "id": "d-sombra",
                            "resultado": "d",
                            "requiere": ["c-meta"],
                        },
                    ],
                    "hora": HORA,
                },
            },
        )
        self.assertEqual(
            [f.id for f in e.fases],
            ["b-receta", "c-meta", "d-sombra"],
        )

    def test_el_ancla_verdadera_at_a_y_rechaza_a_las_fuera(self):
        ajuste = (
            "Correccion del director: las fases son exactamente 3 con ids "
            "b-receta, c-meta, d-sombra."
        )
        e = self._con_ajuste((), ajuste)
        with self.assertRaises(TransicionIlegal):
            reducir(
                e,
                {
                    "kind": "fases",
                    "data": {
                        "fases": [
                            {"id": "b-receta", "resultado": "b"},
                            {"id": "x-fuera", "resultado": "x"},
                        ],
                        "hora": HORA,
                    },
                },
            )

    def test_el_ancla_no_certifica_otra_enumeracion_del_segmento(self):
        ajuste = (
            "Escribe las fases con ids bien-escritos, auto-explicativos y "
            "faciles-de-leer; el plan son exactamente 3 con ids b, c, d."
        )
        e = self._con_ajuste((), ajuste)
        e = reducir(
            e,
            {
                "kind": "fases",
                "data": {
                    "fases": [{"id": "d-sombra", "resultado": "d"}],
                    "hora": HORA,
                },
            },
        )
        self.assertEqual([f.id for f in e.fases], ["d-sombra"])

    def test_una_propuesta_totalmente_fuera_de_la_enumeracion_es_defecto(self):
        e = self._con_ajuste(())
        with self.assertRaises(TransicionIlegal) as ctx:
            reducir(
                e,
                {
                    "kind": "fases",
                    "data": {
                        "fases": [
                            {"id": "p-uno", "resultado": "uno"},
                            {"id": "p-dos", "resultado": "dos"},
                        ],
                        "hora": HORA,
                    },
                },
            )
        self.assertIn("p-uno", str(ctx.exception))
        self.assertIn("p-dos", str(ctx.exception))

    def test_la_enumeracion_con_y_e_sin_coma_completa(self):
        ajuste = (
            "Correccion del director: Las fases son exactamente 8 con ids "
            "b-receta, c-meta, d-sombra, e-pantalla, f-viva, g-retiro, h-api "
            "e i-datos."
        )
        e = self._con_ajuste((), ajuste)
        with self.assertRaises(TransicionIlegal) as ctx:
            reducir(
                e,
                {
                    "kind": "fases",
                    "data": {
                        "fases": [
                            {"id": "b-receta", "resultado": "b"},
                            {"id": "i-datos", "resultado": "i"},
                            {"id": "x-fuera", "resultado": "x"},
                        ],
                        "hora": HORA,
                    },
                },
            )
        self.assertIn("fuera de esa enumeración: x-fuera", str(ctx.exception))

    def test_una_prosa_casual_sin_frase_estructurada_no_enumera(self):
        e = self._con_ajuste(
            (),
            "Correccion: los ids de las fases son largos, revisa el formato "
            "del archivo.",
        )
        e = reducir(
            e,
            {
                "kind": "fases",
                "data": {
                    "fases": [
                        {"id": "p-uno", "resultado": "uno"},
                        {"id": "p-dos", "resultado": "dos"},
                    ],
                    "hora": HORA,
                },
            },
        )
        self.assertEqual([f.id for f in e.fases], ["p-uno", "p-dos"])

    def test_la_conjuncion_sin_coma_no_enumera(self):
        ajuste = "Correccion: fases.json con ids claros y cortos."
        e = self._con_ajuste((), ajuste)
        e = reducir(
            e,
            {
                "kind": "fases",
                "data": {
                    "fases": [
                        {"id": "p-uno", "resultado": "uno"},
                        {"id": "p-dos", "resultado": "dos"},
                    ],
                    "hora": HORA,
                },
            },
        )
        self.assertEqual([f.id for f in e.fases], ["p-uno", "p-dos"])

    def test_la_conjuncion_cierra_una_serie_con_comas(self):
        ajuste = (
            "Correccion del director: las fases son exactamente 8 con ids "
            "b-receta, c-meta, d-sombra, e-pantalla, f-viva, g-retiro, h-api "
            "e i-datos."
        )
        e = self._con_ajuste((), ajuste)
        with self.assertRaises(TransicionIlegal):
            reducir(
                e,
                {
                    "kind": "fases",
                    "data": {
                        "fases": [
                            {"id": "b-receta", "resultado": "b"},
                            {"id": "i-datos", "resultado": "i"},
                            {"id": "x-fuera", "resultado": "x"},
                        ],
                        "hora": HORA,
                    },
                },
            )

    def test_una_repropuesta_no_reordena_para_descapendar(self):
        e = _encargo(
            (
                _preparada("a"),
                _bloqueada("b", "sin modelo", requiere=(("a", ""),)),
                Fase(id="c", resultado="C", estado="pendiente"),
                Fase(
                    id="d",
                    resultado="D",
                    estado="trabajo",
                    requiere=(("a", ""),),
                ),
            ),
            fase_activa="d",
        )
        self.assertEqual(dependencia_bloqueada(e.fases, e.fases[2]), "b")
        with self.assertRaises(TransicionIlegal):
            reducir(
                e,
                {
                    "kind": "fases",
                    "data": {
                        "fases": [
                            {"id": "a", "resultado": "resultado a"},
                            {"id": "c", "resultado": "C"},
                            {
                                "id": "b",
                                "resultado": "resultado b",
                                "requiere": ["a"],
                            },
                            {
                                "id": "d",
                                "resultado": "D",
                                "requiere": ["a"],
                            },
                        ],
                        "hora": HORA,
                    },
                },
            )

    def test_una_repropuesta_no_inserta_para_descapendar(self):
        e = _encargo(
            (
                _preparada("a"),
                _bloqueada("b", "sin modelo", requiere=(("a", ""),)),
                Fase(id="c", resultado="C", estado="pendiente"),
                Fase(
                    id="d",
                    resultado="D",
                    estado="trabajo",
                    requiere=(("a", ""),),
                ),
            ),
            fase_activa="d",
        )
        with self.assertRaises(TransicionIlegal):
            reducir(
                e,
                {
                    "kind": "fases",
                    "data": {
                        "fases": [
                            {"id": "a", "resultado": "resultado a"},
                            {
                                "id": "b",
                                "resultado": "resultado b",
                                "requiere": ["a"],
                            },
                            {"id": "x", "resultado": "X nueva", "requiere": ["a"]},
                            {"id": "c", "resultado": "C"},
                            {
                                "id": "d",
                                "resultado": "D",
                                "requiere": ["a"],
                            },
                        ],
                        "hora": HORA,
                    },
                },
            )

    def test_una_repropuesta_que_conserva_la_condena_se_adopta(self):
        e = _encargo(
            (
                _preparada("a"),
                _bloqueada("b", "sin modelo", requiere=(("a", ""),)),
                Fase(id="c", resultado="C", estado="pendiente"),
                Fase(
                    id="d",
                    resultado="D",
                    estado="trabajo",
                    requiere=(("a", ""),),
                ),
            ),
            fase_activa="d",
        )
        e = reducir(
            e,
            {
                "kind": "fases",
                "data": {
                    "fases": [
                        {"id": "a", "resultado": "resultado a"},
                        {
                            "id": "d",
                            "resultado": "D",
                            "requiere": ["a"],
                        },
                        {
                            "id": "b",
                            "resultado": "resultado b",
                            "requiere": ["a"],
                        },
                        {"id": "c", "resultado": "C"},
                    ],
                    "hora": HORA,
                },
            },
        )
        c = next(f for f in e.fases if f.id == "c")
        self.assertEqual(dependencia_bloqueada(e.fases, c), "b")
        disponible = _fase_disponible(e.fases)
        self.assertIsNotNone(disponible)
        self.assertNotEqual(disponible.id, "c")

    def test_una_repropuesta_no_escapa_de_una_bloqueada_con_otras_dependencias(
        self,
    ):
        e = _encargo(
            (
                _preparada("a"),
                Fase(id="b", resultado="B", estado="pendiente", requiere=(("a", ""),)),
                Fase(id="c", resultado="C", estado="pendiente"),
            ),
            fase_activa="b",
        )
        e = reducir(
            e,
            {
                "kind": "bloqueo_fase",
                "data": {"causa": "sin modelo", "evidencia": "b con pendientes",
                         "hora": HORA},
            },
        )
        with self.assertRaises(TransicionIlegal):
            reducir(
                e,
                {
                    "kind": "fases",
                    "data": {
                        "fases": [
                            {"id": "a", "resultado": "A"},
                            {"id": "b", "resultado": "B", "requiere": ["a"]},
                            {"id": "c", "resultado": "C", "requiere": ["a"]},
                        ],
                        "hora": HORA,
                    },
                },
            )

    def test_una_repropuesta_puede_apretar_dependencias_sobre_una_bloqueada(
        self,
    ):
        e = _encargo(
            (
                _preparada("a"),
                Fase(id="b", resultado="B", estado="pendiente", requiere=(("a", ""),)),
                Fase(id="c", resultado="C", estado="pendiente"),
            ),
            fase_activa="b",
        )
        e = reducir(
            e,
            {
                "kind": "bloqueo_fase",
                "data": {"causa": "sin modelo", "evidencia": "b con pendientes",
                         "hora": HORA},
            },
        )
        e = reducir(
            e,
            {
                "kind": "fases",
                "data": {
                    "fases": [
                        {"id": "a", "resultado": "resultado a"},
                        {
                            "id": "b",
                            "resultado": "B",
                            "requiere": ["a"],
                        },
                        {"id": "c", "resultado": "C", "requiere": ["a", "b"]},
                    ],
                    "hora": HORA,
                },
            },
        )
        c = next(f for f in e.fases if f.id == "c")
        self.assertEqual(
            c.requiere,
            (("a", ""), ("b", "")),
        )


class ElBloqueoSinProductoTrasReabrir(unittest.TestCase):
    """F1: el ukey del bloqueo por falta de producto/commits debe incluir las
    reaperturas, o el segundo bloqueo tras `reabrir` no llega a aplicarse."""

    def _banco(self):
        from motor.encargos.ciclo import _RELEVO_AGOTADO, ahora_iso, recibir

        reg = Registro(Path(self._tmp.name) / "base")
        self.addCleanup(reg.close)
        ident = recibir(
            reg, "entrega lo que haya", "fb-sin-producto", ahora_iso()
        )["encargo"]

        def entregar():
            reg.anotar(
                ident,
                {
                    "kind": "pendiente_entrega",
                    "data": {
                        "texto": (
                            f"{_RELEVO_AGOTADO} del rol responsable. El motor "
                            "entrega lo que hay con los pendientes abiertos."
                        ),
                        "hora": ahora_iso(),
                    },
                },
                f"pendiente:{reg.fold(ident).reaperturas}",
            )
            return _entregar_con_pendientes(reg, reg.fold(ident), None, ahora_iso())

        return reg, ident, entregar

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)

    def test_el_segundo_bloqueo_sin_producto_aplica_tras_reabrir(self):
        from motor.encargos import ciclo

        reg, ident, entregar = self._banco()
        e = entregar()
        self.assertEqual(e.estado, "terminado")
        self.assertEqual(e.fases[0].estado, "bloqueada")
        fase, huella = e.cierre_forzado_fase.id, e.cierre_forzado_huella

        salida = ciclo.ordenar(
            reg, ident, "reabrir", "reabrir-sin-producto", ahora_iso(),
            fase=fase, cierre=huella,
        )
        self.assertFalse(salida.get("rechazada"), salida.get("motivo"))
        ciclo.ordenar(reg, ident, "continuar", "continuar-sin-producto", ahora_iso())

        e = entregar()
        self.assertEqual(e.estado, "terminado", "el segundo bloqueo debe aplicar")
        self.assertEqual(e.fases[0].estado, "bloqueada")
        self.assertEqual(
            len([ev for ev in reg.eventos(ident) if ev["kind"] == "bloqueo_fase"]),
            2,
        )


if __name__ == "__main__":
    unittest.main()
