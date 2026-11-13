"""El juez del canario: la tabla de un encargo terminado contra lo esperado.

`juzgar` se prueba con observaciones armadas a mano, una variación por fila.
`observar` se prueba sobre un encargo real del banco de la compuerta
(`tests/test_compuerta_e2e.py`): el motor lo recibe, lo trabaja con el agente
de ensayo, lo mergea en el git local y lo despliega; la prueba solo lo mira.
"""

from __future__ import annotations

import contextlib
import dataclasses
import io
import json
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from datetime import datetime, timedelta, timezone
from typing import get_args

from motor import plataforma
from motor.encargos.dominio import Encargo, EntregaAutorizada, Fase, VersionProducto
from motor.encargos.politica import CERRADOS_VENTANA_S
from ops.canario_encargos import (
    ESPERADO,
    LIMPIEZA,
    PR,
    TABLA,
    ArchivoDesplegado,
    Esperado,
    FaseVista,
    NoPliega,
    NombreFila,
    Observacion,
    Pliegue,
    Recibo,
    Resto,
    Topes,
    Version,
    juzgar,
    leer_esperado,
    main,
    observar,
    vista,
)
from tests import git_env_limpio
from tests.test_compuerta_e2e import (
    SEPARA_TRABAJADOR,
    Banco,
    _DeepSeekAprueba,
    _escenario_tres_fases,
    _git,
    _trabajador_contenido,
)

SHA = "a" * 40
OTRO = "b" * 40
RECIBIDO = "2026-01-10T10:00:00Z"
CIERRE = "2026-01-10T10:20:00Z"
HORA = "2026-01-10T10:30:00Z"
TOPES = Topes(minutos_termina=90, minutos=45, turnos_por_rol=2, rondas=1)
NO_MEDIDAS = ("no pierde", "no repite", "se detiene")


UNO = FaseVista("uno", "preparada", "desplegado", cumplido=True, desplegable=True)


def _pliegue(**cambios) -> Pliegue:
    base = Pliegue(
        estado="terminado",
        nivel_autorizado="desplegado",
        nivel_efectivo="desplegado",
        motivo_efectivo="",
        nivel_alcanzado="desplegado",
        fase_activa="uno",
        base="main",
        recibido=RECIBIDO,
        cierre=CIERRE,
        completo=True,
        fases=(UNO,),
        turnos=(("responsable", 1), ("revisor", 1)),
        rondas=1,
        violaciones=(),
    )
    return dataclasses.replace(base, **cambios)


# La tabla "Limpieza" de docs/modelo-centro-encargos.md con su plazo desde el
# cierre; la referencia vence con la actualización y tiene su propia prueba.
PLAZOS_DEL_MODELO = (
    ("rama", 0),
    ("pr", 0),
    ("espacio", 7 * 24 * 60 * 60),
    ("ejecucion", CERRADOS_VENTANA_S),
    ("runtime", 0),
    ("despliegue", 0),
    ("proceso", 0),
)


def _iso(hora: datetime) -> str:
    return hora.strftime("%Y-%m-%dT%H:%M:%SZ")


RESTOS_LIMPIOS = (
    Resto("rama", "la rama remota r", False),
    Resto("pr", "ningún PR que cerrar", False),
    Resto("espacio", "espacio/x", True),
    Resto("ejecucion", "ejecucion/x", True),
    Resto("runtime", "runtime/x", False),
    Resto("despliegue", "despliegues/k", False),
    Resto("referencia", "la referencia origin/r", False),
    Resto("proceso", "algún proceso", False),
)


def _obs(**cambios) -> Observacion:
    """Un encargo que cumple cada punto medible."""
    base = Observacion(
        hora=HORA,
        pliegue=_pliegue(),
        prs=(PR("uno", 7, "mergeado", "main", SHA),),
        recibos=(Recibo("uno", "aplicado", SHA),),
        restos=RESTOS_LIMPIOS,
        version=Version(SHA, SHA, SHA),
        actualizado=RECIBIDO,
        archivo=ArchivoDesplegado("corridas/m.txt", "marca", True),
    )
    return dataclasses.replace(base, **cambios)


def _esperado(**filas: str) -> Esperado:
    todas = {nombre: ("no medido" if nombre in NO_MEDIDAS else "pasa") for nombre, *_ in TABLA}
    todas.update({k.replace("_", " "): v for k, v in filas.items()})
    return Esperado(filas=todas, topes=TOPES)  # type: ignore[arg-type]


def _fila(obs: Observacion, nombre: str, esperado: Esperado | None = None):
    return next(f for f in juzgar(obs, esperado or _esperado()) if f.nombre == nombre)


class LaTabla(unittest.TestCase):
    def test_la_tabla_es_cerrada_y_sigue_el_orden_del_diseno(self):
        self.assertEqual([nombre for nombre, *_ in TABLA], list(get_args(NombreFila)))
        filas = juzgar(_obs(), _esperado())
        self.assertEqual([f.nombre for f in filas], list(get_args(NombreFila)))
        self.assertTrue(all(f.observado and f.fuente for f in filas))

    def test_un_encargo_que_cumple_pasa_cada_fila_medible(self):
        resultados = {f.nombre: f.resultado for f in juzgar(_obs(), _esperado())}
        self.assertEqual(
            resultados,
            {n: ("no medido" if n in NO_MEDIDAS else "pasa") for n, *_ in TABLA},
        )

    def test_las_filas_sin_corte_dicen_no_medido_aunque_todo_falle(self):
        obs = _obs(pliegue=_pliegue(estado="detenido", rondas=9), prs=(), recibos=())
        for nombre in NO_MEDIDAS:
            self.assertEqual(_fila(obs, nombre).resultado, "no medido")


class Termina(unittest.TestCase):
    def test_un_encargo_detenido_no_termina(self):
        fila = _fila(_obs(pliegue=_pliegue(estado="detenido")), "termina")
        self.assertEqual(fila.resultado, "falla")
        self.assertIn("detenido", fila.observado)

    def test_terminar_despues_del_tope_falla(self):
        fila = _fila(_obs(pliegue=_pliegue(cierre="2026-01-10T12:00:00Z")), "termina")
        self.assertEqual(fila.resultado, "falla")
        self.assertIn("120 min", fila.observado)

    def test_sin_registro_no_se_mide(self):
        self.assertEqual(_fila(_obs(pliegue=None), "termina").resultado, "no medido")

    def test_una_entrega_parcial_no_termina_aunque_la_fase_activa_llegue(self):
        bloqueada = FaseVista("uno", "bloqueada", "integrado", False, True, "la receta falló")
        dos = FaseVista("dos", "preparada", "desplegado", True, True)
        obs = _obs(pliegue=_pliegue(completo=False, fases=(bloqueada, dos), fase_activa="dos"))
        fila = _fila(obs, "termina")
        self.assertEqual(fila.resultado, "falla")
        self.assertIn("entrega parcial: la fase uno quedó bloqueada (la receta falló)", fila.observado)

    def test_una_fase_por_debajo_de_lo_que_el_motor_exige_no_termina(self):
        uno = FaseVista("uno", "preparada", "integrado", cumplido=False, desplegable=True)
        fila = _fila(_obs(pliegue=_pliegue(fases=(uno, UNO))), "termina")
        self.assertEqual(fila.resultado, "falla")
        self.assertIn("la fase uno quedó en nivel integrado", fila.observado)

    def test_el_tope_compara_la_duracion_exacta(self):
        justo = _fila(_obs(pliegue=_pliegue(cierre="2026-01-10T11:30:00Z")), "termina")
        self.assertEqual(justo.resultado, "pasa", justo.observado)
        pasado = _fila(_obs(pliegue=_pliegue(cierre="2026-01-10T11:30:20Z")), "termina")
        self.assertEqual(pasado.resultado, "falla")
        self.assertIn("90 min 20 s", pasado.observado)


def _encargo(*fases: Fase, nivel: str = "desplegado", **cambios) -> Encargo:
    return Encargo(
        id="enc_x", clave="c", huella="h", director="d", texto="t", adjuntos=(),
        restricciones=(), canal="", estado="terminado", hora_cierre=CIERRE,
        entrega=EntregaAutorizada(nivel=nivel, repo="r", base="main"),
        fases=fases, fase_activa=fases[-1].id, **cambios,
    )


RECIBIDO_EV = [{"id": 1, "ts": RECIBIDO, "kind": "recibido", "data": {}}]


class TerminaConLasReglasDelMotor(unittest.TestCase):
    """`vista` juzga cada fase con `nivel_cumplido`, `nivel_objetivo` y
    `objetivo_completo` del dominio, no con una copia de sus reglas."""

    def termina(self, e: Encargo):
        return _fila(_obs(pliegue=vista(e, RECIBIDO_EV)), "termina")

    def test_una_ultima_fase_que_no_requiere_despliegue_no_tumba_el_nivel(self):
        e = _encargo(
            Fase("uno", "r", evidencia="e", estado="preparada", ultimo_paso_entrega="desplegado"),
            Fase("dos", "r", evidencia="e", estado="preparada", ultimo_paso_entrega="integrado",
                 requiere_despliegue=False, despliegue_fuente=".motor.toml"),
        )
        self.assertEqual(e.nivel_alcanzado, "integrado")
        fila = self.termina(e)
        self.assertEqual(fila.resultado, "pasa", fila.observado)
        self.assertIn("dos integrado (no requiere despliegue)", fila.observado)
        self.assertIn("el motor reporta nivel integrado porque mira solo su fase activa, dos",
                      fila.observado)

    def test_sin_cambios_termina_cuando_se_pidio_pr(self):
        e = _encargo(
            Fase("uno", "r", evidencia="e", estado="preparada", ultimo_paso_entrega="sin_cambios",
                 revisado=VersionProducto("f", ())),
            nivel="pr",
        )
        fila = self.termina(e)
        self.assertEqual(fila.resultado, "pasa", fila.observado)

    def test_sin_cambios_no_termina_cuando_se_pidio_desplegado(self):
        e = _encargo(
            Fase("uno", "r", evidencia="e", estado="preparada", ultimo_paso_entrega="sin_cambios",
                 revisado=VersionProducto("f", ())),
        )
        self.assertEqual(self.termina(e).resultado, "falla")

    def test_el_objetivo_que_el_motor_bajo_se_cumple_y_se_dice(self):
        e = _encargo(
            Fase("uno", "r", evidencia="e", estado="preparada", ultimo_paso_entrega="integrado"),
            nivel_efectivo="integrado", nivel_efectivo_motivo="la máquina no puede desplegar",
        )
        fila = self.termina(e)
        self.assertEqual(fila.resultado, "pasa", fila.observado)
        self.assertIn("bajó el objetivo de desplegado a integrado: la máquina no puede desplegar",
                      fila.observado)
        entregado = _fila(_obs(pliegue=vista(e, RECIBIDO_EV)), "entregado")
        self.assertEqual(entregado.resultado, "falla")

    def test_una_fase_bloqueada_es_entrega_parcial_aunque_la_activa_este_desplegada(self):
        e = _encargo(
            Fase("uno", "r", evidencia="e", estado="bloqueada", ultimo_paso_entrega="integrado",
                 bloqueo_causa="la receta falló"),
            Fase("dos", "r", evidencia="e", estado="preparada", ultimo_paso_entrega="desplegado"),
        )
        self.assertEqual(e.nivel_alcanzado, "desplegado")
        fila = self.termina(e)
        self.assertEqual(fila.resultado, "falla")
        self.assertIn("entrega parcial", fila.observado)

    def test_desplegado_cuyas_fases_no_requieren_despliegue_esta_entregado(self):
        e = _encargo(
            Fase("uno", "r", evidencia="e", estado="preparada", ultimo_paso_entrega="integrado",
                 requiere_despliegue=False, despliegue_fuente=".motor.toml"),
        )
        self.assertEqual(self.termina(e).resultado, "pasa")
        fila = _fila(_obs(pliegue=vista(e, RECIBIDO_EV), recibos=()), "entregado")
        self.assertEqual(fila.resultado, "pasa", fila.observado)

    def test_una_fase_que_no_dice_de_donde_saco_que_no_se_despliega_pide_su_recibo(self):
        e = _encargo(
            Fase("uno", "r", evidencia="e", estado="preparada", ultimo_paso_entrega="desplegado"),
            Fase("dos", "r", evidencia="e", estado="preparada", ultimo_paso_entrega="desplegado",
                 requiere_despliegue=False),
        )
        obs = _obs(
            pliegue=vista(e, RECIBIDO_EV),
            prs=(PR("uno", 7, "mergeado", "main", SHA), PR("dos", 8, "mergeado", "main", OTRO)),
            recibos=(Recibo("uno", "aplicado", SHA),),
        )
        self.assertEqual(_fila(obs, "termina").resultado, "pasa")
        fila = _fila(obs, "entregado")
        self.assertEqual(fila.resultado, "falla", fila.observado)
        self.assertIn("la fase dos no tiene recibo", fila.observado)

    def test_una_revision_sin_origen_cuenta_como_independiente(self):
        eventos = [
            *RECIBIDO_EV,
            {"id": 2, "ts": RECIBIDO, "kind": "revision", "data": {}},
            {"id": 3, "ts": RECIBIDO, "kind": "revision", "data": {"origen": "independiente"}},
            {"id": 4, "ts": RECIBIDO, "kind": "revision", "data": {"origen": "deepseek"}},
        ]
        e = _encargo(Fase("uno", "r", evidencia="e", estado="preparada", ultimo_paso_entrega="desplegado"))
        self.assertEqual(vista(e, eventos).rondas, 2)


class Entregado(unittest.TestCase):
    def falla(self, obs: Observacion, texto: str) -> None:
        fila = _fila(obs, "entregado")
        self.assertEqual(fila.resultado, "falla", fila.observado)
        self.assertIn(texto, fila.observado)

    def test_un_pr_abierto_no_esta_entregado(self):
        self.falla(_obs(prs=(PR("uno", 7, "abierto", "main"),)), "#7 está abierto")

    def test_un_pr_mergeado_en_otra_base_no_esta_entregado(self):
        self.falla(_obs(prs=(PR("uno", 7, "mergeado", "otra", SHA),)), "se mergeó en otra")

    def test_una_fase_sin_pr_no_esta_entregada(self):
        self.falla(_obs(prs=(PR("uno", 0, None),)), "no abrió PR")

    def test_un_recibo_de_otro_commit_no_cuenta_como_desplegado(self):
        self.falla(_obs(recibos=(Recibo("uno", "aplicado", OTRO),)), "recibo aplicado de su commit")

    def test_un_recibo_fallido_no_cuenta_como_desplegado(self):
        self.falla(_obs(recibos=(Recibo("uno", "fallido", SHA),)), "recibo aplicado de su commit")

    def test_un_recibo_que_el_motor_rechaza_dice_por_que(self):
        motivo = "el recibo no demuestra despliegue y checklist con salida 0"
        self.falla(_obs(recibos=(Recibo("uno", motivo, SHA),)), motivo)

    def test_un_encargo_pedido_hasta_pr_no_tiene_merge_que_medir(self):
        obs = _obs(pliegue=_pliegue(nivel_autorizado="pr", nivel_efectivo="pr", nivel_alcanzado="pr"))
        self.assertEqual(_fila(obs, "entregado").resultado, "no medido")

    def test_el_archivo_desplegado_sin_la_marca_falla(self):
        archivo = ArchivoDesplegado("corridas/m.txt", "marca", False)
        self.falla(_obs(archivo=archivo), "no trae la marca")

    def test_el_archivo_desplegado_que_no_existe_falla(self):
        archivo = ArchivoDesplegado("corridas/m.txt", "marca", None)
        self.falla(_obs(archivo=archivo), "no existe")

    def test_si_github_no_responde_no_se_mide(self):
        fila = _fila(_obs(prs=(PR("uno", 7, None),)), "entregado")
        self.assertEqual(fila.resultado, "no medido")

    def test_un_merge_commit_vacio_no_coincide_con_un_recibo_sin_sha(self):
        obs = _obs(prs=(PR("uno", 7, "mergeado", "main", ""),), recibos=(Recibo("uno", "aplicado", ""),))
        fila = _fila(obs, "entregado")
        self.assertEqual(fila.resultado, "no medido", fila.observado)
        self.assertIn("commit de merge del PR #7", fila.observado)

    def test_dos_bases_vacias_no_coinciden(self):
        obs = _obs(pliegue=_pliegue(base=""), prs=(PR("uno", 7, "mergeado", "", SHA),))
        fila = _fila(obs, "entregado")
        self.assertEqual(fila.resultado, "no medido", fila.observado)
        self.assertIn("base pedida", fila.observado)

    def test_un_pr_sin_base_no_se_da_por_mergeado_en_la_pedida(self):
        fila = _fila(_obs(prs=(PR("uno", 7, "mergeado", "", SHA),)), "entregado")
        self.assertEqual(fila.resultado, "no medido", fila.observado)

    def test_un_encargo_cancelado_o_detenido_no_esta_entregado(self):
        for estado in ("cancelado", "detenido"):
            self.falla(_obs(pliegue=_pliegue(estado=estado)), f"quedó {estado}")

    def test_un_recibo_de_otra_fase_no_cuenta(self):
        self.falla(_obs(recibos=(Recibo("dos", "aplicado", SHA),)), "la fase uno no tiene recibo")

    def test_sin_ningun_pr_no_esta_entregado(self):
        self.falla(_obs(prs=()), "ninguna fase tiene PR")

    def test_un_encargo_integrado_no_pide_recibo(self):
        e = _encargo(
            Fase("uno", "r", evidencia="e", estado="preparada", ultimo_paso_entrega="integrado"),
            nivel="integrado",
        )
        obs = _obs(pliegue=vista(e, RECIBIDO_EV), recibos=())
        self.assertEqual(_fila(obs, "entregado").resultado, "pasa")


class Limpio(unittest.TestCase):
    def test_una_rama_que_queda_no_esta_limpio(self):
        restos = (Resto("rama", "la rama remota r", True),)
        fila = _fila(_obs(restos=restos), "limpio")
        self.assertEqual(fila.resultado, "falla")
        self.assertIn("la rama remota r", fila.observado)

    def test_una_rama_que_queda_falla_aunque_el_reloj_vaya_atras_del_cierre(self):
        restos = (Resto("rama", "la rama remota r", True),)
        fila = _fila(_obs(restos=restos, hora="2026-01-10T10:10:00Z"), "limpio")
        self.assertEqual(fila.resultado, "falla")

    def test_un_proceso_vivo_no_esta_limpio(self):
        fila = _fila(_obs(restos=(Resto("proceso", "algún proceso", True),)), "limpio")
        self.assertEqual(fila.resultado, "falla")

    def test_la_copia_dentro_de_su_plazo_esta_limpia_y_se_nombra(self):
        fila = _fila(_obs(), "limpio")
        self.assertEqual(fila.resultado, "pasa")
        self.assertIn("espacio/x hasta 2026-01-17", fila.observado)

    def test_la_copia_vencida_no_esta_limpia(self):
        fila = _fila(_obs(hora="2026-01-18T10:30:00Z"), "limpio")
        self.assertEqual(fila.resultado, "falla")
        self.assertIn("espacio/x", fila.observado)

    def test_lo_que_no_se_pudo_mirar_no_se_mide(self):
        restos = (*RESTOS_LIMPIOS, Resto("rama", "la rama remota s", None))
        fila = _fila(_obs(restos=restos), "limpio")
        self.assertEqual(fila.resultado, "no medido")
        self.assertIn("la rama remota s", fila.observado)

    def test_cada_regla_del_modelo_vence_en_su_plazo(self):
        nada = tuple(dataclasses.replace(r, queda=False) for r in RESTOS_LIMPIOS)
        cierre = datetime.fromisoformat(CIERRE.replace("Z", "+00:00"))
        for tipo, plazo in PLAZOS_DEL_MODELO:
            restos = (*nada, Resto(tipo, f"el resto {tipo}", True))
            vence = cierre + timedelta(seconds=plazo)
            with self.subTest(tipo=tipo):
                fila = _fila(_obs(restos=restos, hora=_iso(vence)), "limpio")
                self.assertEqual(fila.resultado, "falla", fila.observado)
                self.assertIn(f"el resto {tipo}", fila.observado)
                if plazo:
                    antes = _fila(_obs(restos=restos, hora=_iso(vence - timedelta(seconds=1))), "limpio")
                    self.assertEqual(antes.resultado, "pasa", antes.observado)

    def test_sin_restos_revisados_no_se_mide(self):
        fila = _fila(_obs(restos=()), "limpio")
        self.assertEqual(fila.resultado, "no medido")
        for _tipo, regla, _plazo in LIMPIEZA:
            self.assertIn(regla, fila.observado)

    def test_una_regla_sin_revisar_no_se_mide(self):
        restos = tuple(r for r in RESTOS_LIMPIOS if r.tipo != "despliegue")
        fila = _fila(_obs(restos=restos), "limpio")
        self.assertEqual(fila.resultado, "no medido")
        self.assertIn("despliegues/<clave>", fila.observado)

    def test_lo_que_queda_de_un_despliegue_no_esta_limpio(self):
        restos = (*RESTOS_LIMPIOS, Resto("despliegue", "despliegues/k (pedido.json)", True))
        fila = _fila(_obs(restos=restos), "limpio")
        self.assertEqual(fila.resultado, "falla")
        self.assertIn("despliegues/k (pedido.json)", fila.observado)

    def test_la_copia_vence_justo_a_los_siete_dias(self):
        antes = _fila(_obs(hora="2026-01-17T10:19:59Z"), "limpio")
        self.assertEqual(antes.resultado, "pasa", antes.observado)
        justo = _fila(_obs(hora="2026-01-17T10:20:00Z"), "limpio")
        self.assertEqual(justo.resultado, "falla")

    def test_la_referencia_vence_con_la_primera_actualizacion_tras_el_cierre(self):
        restos = (*RESTOS_LIMPIOS, Resto("referencia", "la referencia origin/r", True))
        antes = _fila(_obs(restos=restos, actualizado=RECIBIDO), "limpio")
        self.assertEqual(antes.resultado, "pasa", antes.observado)
        self.assertIn("la próxima actualización", antes.observado)
        despues = _fila(_obs(restos=restos, actualizado="2026-01-10T10:25:00Z"), "limpio")
        self.assertEqual(despues.resultado, "falla")
        sin_hora = _fila(_obs(restos=restos, actualizado=""), "limpio")
        self.assertEqual(sin_hora.resultado, "no medido")

    def test_un_encargo_sin_cerrar_no_se_mide(self):
        fila = _fila(_obs(pliegue=_pliegue(estado="ejecutando", cierre="")), "limpio")
        self.assertEqual(fila.resultado, "no medido")


class CuestaPoco(unittest.TestCase):
    def test_un_turno_de_mas_por_rol_cuesta(self):
        obs = _obs(pliegue=_pliegue(turnos=(("responsable", 3), ("revisor", 1))))
        self.assertEqual(_fila(obs, "cuesta poco").resultado, "falla")

    def test_los_turnos_iguales_al_tope_no_cuestan(self):
        obs = _obs(pliegue=_pliegue(turnos=(("responsable", 2), ("revisor", 2))))
        self.assertEqual(_fila(obs, "cuesta poco").resultado, "pasa")

    def test_una_ronda_de_mas_cuesta(self):
        self.assertEqual(_fila(_obs(pliegue=_pliegue(rondas=2)), "cuesta poco").resultado, "falla")

    def test_el_tope_de_minutos_no_redondea(self):
        justo = _fila(_obs(pliegue=_pliegue(cierre="2026-01-10T10:45:00Z")), "cuesta poco")
        self.assertEqual(justo.resultado, "pasa")
        pasado = _fila(_obs(pliegue=_pliegue(cierre="2026-01-10T10:45:20Z")), "cuesta poco")
        self.assertEqual(pasado.resultado, "falla")
        self.assertIn("45 min 20 s (tope 45)", pasado.observado)

    def test_pasar_los_minutos_cuesta(self):
        obs = _obs(pliegue=_pliegue(cierre="2026-01-10T10:50:00Z"))
        fila = _fila(obs, "cuesta poco")
        self.assertEqual(fila.resultado, "falla")
        self.assertIn("50 min (tope 45)", fila.observado)


class VersionYLegible(unittest.TestCase):
    def test_un_arbol_en_otro_sha_falla(self):
        fila = _fila(_obs(version=Version(SHA, OTRO, SHA)), "versión")
        self.assertEqual(fila.resultado, "falla")
        self.assertIn(OTRO[:12], fila.observado)

    def test_una_pasada_vieja_falla(self):
        self.assertEqual(_fila(_obs(version=Version(SHA, SHA, OTRO)), "versión").resultado, "falla")

    def test_sin_recibo_de_instalacion_no_se_mide(self):
        fila = _fila(_obs(version=Version("", SHA, SHA)), "versión")
        self.assertEqual(fila.resultado, "no medido")

    def test_un_registro_que_no_pliega_no_es_legible(self):
        obs = _obs(pliegue=NoPliega(3, "revision", "TransicionIlegal"))
        fila = _fila(obs, "legible")
        self.assertEqual(fila.resultado, "falla")
        self.assertIn("evento 3", fila.observado)
        self.assertEqual(_fila(obs, "termina").resultado, "no medido")

    def test_una_invariante_rota_no_es_legible(self):
        obs = _obs(pliegue=_pliegue(violaciones=("mismo_estado_al_replegar",)))
        self.assertEqual(_fila(obs, "legible").resultado, "falla")


class LoEsperado(unittest.TestCase):
    def test_coincide_cuando_cada_fila_da_lo_esperado(self):
        obs = _obs(restos=(Resto("rama", "la rama remota r", True),))
        filas = juzgar(obs, _esperado(limpio="falla"))
        self.assertEqual([f.nombre for f in filas if f.discrepancia], [])

    def test_una_fila_que_pasaba_y_falla_es_regresion(self):
        filas = juzgar(_obs(pliegue=_pliegue(rondas=3)), _esperado())
        self.assertEqual(
            [(f.nombre, f.discrepancia) for f in filas if f.discrepancia],
            [("cuesta poco", "regresión")],
        )

    def test_una_fila_que_empieza_a_pasar_pide_actualizar_el_esperado(self):
        filas = juzgar(_obs(), _esperado(limpio="falla"))
        self.assertEqual(
            [(f.nombre, f.discrepancia) for f in filas if f.discrepancia],
            [("limpio", "mejora: actualiza el esperado")],
        )

    def test_una_fila_que_deja_de_medirse_no_coincide(self):
        filas = juzgar(_obs(version=Version("", SHA, SHA)), _esperado())
        self.assertEqual(
            [(f.nombre, f.discrepancia) for f in filas if f.discrepancia],
            [("versión", "dejó de medirse")],
        )

    def test_cada_discrepancia_se_nombra(self):
        casos = (
            (_obs(version=Version("", SHA, SHA)), "falla", "dejó de medirse"),
            (_obs(), "no medido", "ya se mide: actualiza el esperado"),
            (_obs(version=Version(SHA, OTRO, SHA)), "no medido", "ya se mide: actualiza el esperado"),
        )
        for obs, esperado, discrepancia in casos:
            fila = _fila(obs, "versión", _esperado(versión=esperado))
            self.assertEqual(fila.discrepancia, discrepancia, (esperado, fila.resultado))

    def test_el_esperado_del_repo_nombra_cada_fila(self):
        esperado = leer_esperado(ESPERADO)
        self.assertEqual(set(esperado.filas), set(get_args(NombreFila)))
        self.assertEqual(esperado.filas["limpio"], "falla")
        for nombre in NO_MEDIDAS:
            self.assertEqual(esperado.filas[nombre], "no medido")

    def test_un_esperado_al_que_le_falta_una_fila_se_rechaza(self):
        tmp = Path(tempfile.mkdtemp(prefix="esperado-"))
        self.addCleanup(shutil.rmtree, tmp, True)
        datos = json.loads(ESPERADO.read_text(encoding="utf-8"))
        del datos["filas"]["limpio"]
        ruta = tmp / "esperado.json"
        ruta.write_text(json.dumps(datos), encoding="utf-8")
        with self.assertRaises(ValueError):
            leer_esperado(ruta)

    def test_un_tope_que_no_es_entero_se_rechaza(self):
        tmp = Path(tempfile.mkdtemp(prefix="esperado-"))
        self.addCleanup(shutil.rmtree, tmp, True)
        datos = json.loads(ESPERADO.read_text(encoding="utf-8"))
        ruta = tmp / "esperado.json"
        for valor in ("45", 45.5, True):
            datos["topes"]["minutos"] = valor
            ruta.write_text(json.dumps(datos), encoding="utf-8")
            with self.subTest(valor=valor), self.assertRaises(ValueError):
                leer_esperado(ruta)


# --------------------------------------------------------------------------- observar


def _registro_vacio(base: Path) -> None:
    db = sqlite3.connect(base / "registro.sqlite")
    db.execute(
        "CREATE TABLE events (id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT NOT NULL,"
        " encargo TEXT NOT NULL, kind TEXT NOT NULL, data TEXT NOT NULL, ukey TEXT UNIQUE)"
    )
    db.commit()
    db.close()


def _ahora() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class _GitHubCaido:
    def ver(self, repo, numero):
        raise RuntimeError("gh pr view: sin red")


class ObservarLaBase(unittest.TestCase):
    """Lo que no necesita un encargo trabajado: versión y registro roto."""

    def setUp(self):
        self.base = Path(tempfile.mkdtemp(prefix="canario-base-"))
        self.addCleanup(shutil.rmtree, self.base, True)
        _registro_vacio(self.base)

    def test_la_version_sale_del_recibo_de_la_pasada_y_del_arbol(self):
        arbol = self.base / "arbol"
        arbol.mkdir()
        (arbol / "a.txt").write_text("a", encoding="utf-8")
        _git(arbol, "init", "-q", "-b", "main")
        _git(arbol, "add", "-A")
        _git(arbol, "-c", "user.name=t", "-c", "user.email=t@local", "commit", "-q", "-m", "a")
        head = _git(arbol, "rev-parse", "HEAD")
        (self.base / "instalacion.json").write_text(
            json.dumps({"destino": {"codigo": SHA}}), encoding="utf-8"
        )
        (self.base / "supervisor-pasada.json").write_text(
            json.dumps({"sha": OTRO, "ruta": str(arbol)}), encoding="utf-8"
        )
        obs = observar(self.base, "enc_x", _GitHubCaido(), hora=HORA)
        self.assertEqual(obs.version, Version(SHA, head, OTRO))
        obs = observar(self.base, "enc_x", _GitHubCaido(), hora=HORA, sha_desplegado=head)
        self.assertEqual(obs.version.desplegado, head)

    def test_sin_recibos_la_version_queda_vacia(self):
        obs = observar(self.base, "enc_x", _GitHubCaido(), hora=HORA)
        self.assertIsNone(obs.pliegue)
        self.assertEqual(obs.version, Version("", "", ""))

    def test_un_registro_que_no_pliega_se_observa_como_roto(self):
        db = sqlite3.connect(self.base / "registro.sqlite")
        db.execute(
            "INSERT INTO events (ts, encargo, kind, data) VALUES (?, ?, ?, ?)",
            (RECIBIDO, "enc_x", "revision", "{}"),
        )
        db.commit()
        db.close()
        obs = observar(self.base, "enc_x", _GitHubCaido(), hora=HORA)
        self.assertEqual(obs.pliegue, NoPliega(1, "revision", "TransicionIlegal"))

    def test_main_sale_con_2_si_el_encargo_no_esta(self):
        self.assertEqual(main(["enc_x", "--base", str(self.base)], gh=_GitHubCaido()), 2)

    def test_main_sale_con_2_si_lo_esperado_no_tiene_la_forma(self):
        ruta = self.base / "esperado.json"
        ruta.write_text(json.dumps({"filas": {}, "topes": {}}), encoding="utf-8")
        argv = ["enc_x", "--base", str(self.base), "--esperado", str(ruta)]
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(main(argv, gh=_GitHubCaido()), 2)

    def test_main_sale_con_2_si_la_base_no_tiene_registro(self):
        vacia = self.base / "vacia"
        vacia.mkdir()
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(main(["enc_x", "--base", str(vacia)], gh=_GitHubCaido()), 2)

    def test_observar_no_crea_nada_en_una_base_sin_registro(self):
        vacia = self.base / "vacia"
        vacia.mkdir()
        with contextlib.suppress(sqlite3.Error):
            observar(vacia, "enc_x", _GitHubCaido(), hora=HORA)
        self.assertEqual(list(vacia.iterdir()), [])


class ObservarUnEncargoDelBanco(unittest.TestCase):
    """El motor trabaja, mergea y despliega un encargo de una fase; la prueba lo observa."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="canario-banco-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        if not SEPARA_TRABAJADOR:
            _trabajador_contenido(self)

    def test_observa_pr_recibo_restos_y_procesos_del_encargo(self):
        escenario = _escenario_tres_fases(True, self.tmp / "destino")
        escenario["fases"] = escenario["fases"][:1]
        escenario["producto"] = {"uno": escenario["producto"]["uno"]}
        banco = Banco(self.tmp, escenario=escenario, gh_cls=_DeepSeekAprueba)
        self.addCleanup(banco.reg.close)
        banco.correr(lambda: banco.fold().estado == "terminado")

        (pr_fake,) = json.loads(
            (self.tmp / "fake" / "github.json").read_text(encoding="utf-8")
        )["prs"].values()
        merge = pr_fake["merge_commit"]
        archivo = (banco.destino / "pasos", merge)
        obs = observar(banco.base, banco.ident, banco.gh, hora=_ahora(), archivo=archivo)

        self.assertIsInstance(obs.pliegue, Pliegue)
        assert isinstance(obs.pliegue, Pliegue)
        self.assertEqual(obs.pliegue.estado, "terminado")
        self.assertEqual(obs.pliegue.nivel_alcanzado, "desplegado")
        self.assertEqual(obs.pliegue.violaciones, ())
        self.assertEqual(
            {rol for rol, _ in obs.pliegue.turnos}, {"responsable", "revisor"}
        )
        self.assertEqual(obs.prs, (PR("uno", pr_fake["numero"], "mergeado", "main", merge),))
        self.assertIn(Recibo("uno", "aplicado", merge), obs.recibos)
        self.assertEqual(obs.archivo, ArchivoDesplegado(str(archivo[0]), merge, True))

        restos = {r.nombre: r for r in obs.restos}
        rama = pr_fake["rama"]
        self.assertEqual(
            restos[f"la rama remota {rama}"].queda,
            bool(_git(self.tmp / "repo.git", "branch", "--list", rama)),
        )
        espacio = f"espacio/{banco.ident}"
        self.assertEqual(restos[espacio].queda, (banco.base / espacio).exists())
        self.assertFalse(any(r.queda for r in obs.restos if r.tipo == "proceso"))

        esperado = _esperado(limpio="falla")
        self.assertEqual(_fila(obs, "entregado", esperado).resultado, "pasa")
        self.assertEqual(_fila(obs, "legible", esperado).resultado, "pasa")
        self.assertEqual(_fila(obs, "limpio", esperado).resultado, "falla")

        # Lo que el canario borra o deja vivo, la observación lo ve al volver a mirar.
        _git(self.tmp / "repo.git", "branch", "-D", rama)
        shutil.rmtree(banco.base / espacio)
        trabajador = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(60)"], env=git_env_limpio()
        )
        self.addCleanup(trabajador.wait)
        self.addCleanup(trabajador.kill)
        clave = banco.base / "despliegues" / "otro-intento"
        clave.mkdir(parents=True)
        (clave / "pedido.json").write_text(
            json.dumps({"identidad": {"encargo": banco.ident}}), encoding="utf-8"
        )
        (clave / "proceso.json").write_text(json.dumps({
            "pid": trabajador.pid,
            "inicio": plataforma.hora_inicio(trabajador.pid),
            "boot_id": plataforma.boot_id(),
        }), encoding="utf-8")
        otra = {r.nombre: r for r in observar(banco.base, banco.ident, banco.gh, hora=_ahora()).restos}
        self.assertFalse(otra[f"la rama remota {rama}"].queda)
        self.assertFalse(otra[espacio].queda)
        self.assertTrue(otra["algún trabajador de despliegue"].queda)

        caido = observar(banco.base, banco.ident, _GitHubCaido(), hora=_ahora())
        self.assertEqual(caido.prs, (PR("uno", pr_fake["numero"], None),))
        self.assertEqual(_fila(caido, "entregado", esperado).resultado, "no medido")


if __name__ == "__main__":
    unittest.main()
