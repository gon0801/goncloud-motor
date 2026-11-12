"""Continuar un despliegue pendiente corre solo su receta, no reabre la fase.

Una fase integrada a la que solo le falta el despliegue no vuelve al
responsable ni al revisor por una orden de continuar: la receta del mismo SHA
se reintenta, la fase en curso conserva su turno, y lo declarado por otra fase
no se cobra a esta.
"""

from __future__ import annotations

import json
import time
import types
import unittest
from pathlib import Path
from unittest import mock

from motor import plataforma
from motor.encargos import ciclo, despliegue as ejecutor
from motor.io import escribir_texto
from tests import test_encargos_despliegue_real as base


RECETA = {"desplegar": "d.sh", "checklist": "c.sh", "rollback": "r.sh"}


class BancoReintento(unittest.TestCase):
    setUp = base.DespliegueReal.setUp

    def _guion(self, retry: str) -> None:
        reg, ident, espacio, destino, integrar = self.despliegue
        corridas = destino / "corridas"
        (espacio / "d.sh").write_text(
            f'printf "desplegar:%s\\n" "$MOTOR_SHA" >> "{destino.as_posix()}/pasos"\n'
            f'n=$(cat "{corridas.as_posix()}" 2>/dev/null || echo 0)\n'
            f'n=$((n+1))\n'
            f'printf "%s" "$n" > "{corridas.as_posix()}"\n'
            'if [ "$n" -eq 1 ]; then exit 0; fi\n'
            'if [ "$n" -eq 2 ]; then exit 1; fi\n'
            f"{retry}\n"
        )

    def _ejecutar_en_proceso(self, cmd, cwd, env, log):
        ejecutor.ejecutar(Path(cmd[-1]))
        return {
            "pid": 99999999, "inicio": "",
            "boot_id": plataforma.boot_id(), "job": "",
        }

    def banco(self, retry: str):
        """K desplegada, A bloqueada por su despliegue y B en curso."""
        reg, ident, espacio, destino, integrar = self.despliegue
        self._guion(retry)
        corridas = destino / "corridas"
        reg.anotar(ident, {"kind": "fases", "data": {"fases": [
            {"id": "K", "resultado": "K lista", "requiere_despliegue": True,
             "despliegue": RECETA},
            {"id": "A", "resultado": "A lista", "requiere": ["K"],
             "requiere_despliegue": True, "despliegue": RECETA},
            {"id": "B", "resultado": "B lista", "requiere": ["K"],
             "requiere_despliegue": True, "despliegue": RECETA},
        ]}}, "fases")

        def sweeps(horas):
            with mock.patch.object(
                plataforma, "lanzar_separado", side_effect=self._ejecutar_en_proceso
            ), mock.patch.object(ciclo, "_lanzar_uno", mock.Mock()), mock.patch.object(
                ciclo, "_sonda_entorno", mock.Mock(return_value=types.SimpleNamespace(
                    ok=True, causa="", condicion="",
                )),
            ), mock.patch.object(
                ciclo, "_reanuda_en", mock.Mock(return_value=base.HORA),
            ):
                for hora in horas:
                    ciclo.barrer(reg, hora, time.time(), mock.Mock())

        # K se integra y despliega.
        integrar()
        base.paso(self.despliegue)
        reg.anotar(ident, {"kind": "comprobacion", "data": {
            "clase": "cierre_comprobado", "evidencia": "recibo aplicado de K",
            "hora": base.HORA,
        }}, "cierre-K")
        self.assertEqual(reg.fold(ident).fase_activa, "A")

        # A se integra y su primera receta falla; la fase se bloquea.
        (espacio / "cambio-a.txt").write_text("cambio de A", encoding="utf-8")
        integrar()
        base.paso(self.despliegue)
        enc = reg.fold(ident)
        self.assertEqual(corridas.read_text(encoding="utf-8"), "2")
        causa = enc.impedimentos[-1].causa
        reg.anotar(ident, {"kind": "bloqueo_fase", "data": {
            "evidencia": "recibo fallido de A", "causa": causa,
        }}, "bloqueo-A")
        enc = reg.fold(ident)
        self.assertEqual(enc.fase_activa, "B", enc.motivo)
        self.assertEqual(next(f for f in enc.fases if f.id == "A").estado,
                         "bloqueada")
        return reg, ident, espacio, destino, integrar, corridas, sweeps

    def orden(self, reg, ident, clave):
        ciclo.ordenar(reg, ident, "continuar", clave, base.HORA)


class ReintentoCorreSoloLaReceta(BancoReintento):
    def test_a_llega_a_desplegado_sin_tocar_el_turno_de_b(self):
        reg, ident, espacio, destino, integrar, corridas, sweeps = self.banco(
            "exit 0"
        )
        antes = reg.fold(ident)
        b_antes = next(f for f in antes.fases if f.id == "B")
        self.orden(reg, ident, "reintento-a")
        sweeps((
            "2026-11-05T12:01:00+00:00", "2026-11-05T12:02:00+00:00",
        ))
        final = reg.fold(ident)
        a = next(f for f in final.fases if f.id == "A")
        b = next(f for f in final.fases if f.id == "B")
        self.assertEqual(corridas.read_text(encoding="utf-8"), "3")
        self.assertEqual(a.estado, "preparada")
        self.assertEqual(a.nivel_alcanzado, "desplegado")
        self.assertTrue(a.desplegado_recibo)
        self.assertEqual(final.fase_activa, "B")
        self.assertEqual(b.estado, b_antes.estado)
        self.assertEqual(
            [ev for ev in reg.eventos(ident) if ev["kind"] == "fase_reactivada"],
            [],
        )
        self.assertEqual(
            [
                ev for ev in reg.eventos(ident)
                if ev["kind"] == "intento_abierto"
                and ev["data"].get("rol") in ("responsable", "revisor")
            ],
            [],
        )

    def test_la_receta_que_ya_contiene_el_sha_tambien_despliega(self):
        reg, ident, espacio, destino, integrar, corridas, sweeps = self.banco(
            'printf "ESTADO=sin_cambios\\n"; printf "produccion ya contiene el SHA\\n"; exit 0'
        )
        self.orden(reg, ident, "reintento-ya-contiene")
        sweeps((
            "2026-11-05T12:01:00+00:00", "2026-11-05T12:02:00+00:00",
        ))
        final = reg.fold(ident)
        a = next(f for f in final.fases if f.id == "A")
        self.assertEqual(a.estado, "preparada")
        self.assertEqual(a.nivel_alcanzado, "desplegado")
        self.assertEqual(corridas.read_text(encoding="utf-8"), "3")


class LaOrdenAtendidaNoReabre(BancoReintento):
    def test_un_impedimento_resuelto_despues_de_la_orden_la_consume(self):
        reg, ident, espacio, destino, integrar, corridas, sweeps = self.banco(
            "exit 0"
        )
        enc = reg.fold(ident)
        clase = enc.impedimentos[-1].clase
        self.orden(reg, ident, "orden-atendida")
        ciclo._resolver_impedimento(reg, reg.fold(ident), clase, base.HORA)
        sweeps((
            "2026-11-05T12:01:00+00:00", "2026-11-05T12:02:00+00:00",
            "2026-11-05T12:32:00+00:00",
        ))
        final = reg.fold(ident)
        a = next(f for f in final.fases if f.id == "A")
        self.assertEqual(corridas.read_text(encoding="utf-8"), "2")
        self.assertEqual(a.estado, "bloqueada")
        self.assertEqual(final.fase_activa, "B")
        self.assertEqual(
            [ev for ev in reg.eventos(ident) if ev["kind"] == "fase_reactivada"],
            [],
        )
        self.assertEqual(
            [ev for ev in reg.eventos(ident) if ev["kind"] == "fase_desplegada"],
            [],
        )


class LasComprobacionesDeUnaFaseNoSeCobranAOtra(BancoReintento):
    def test_al_reintentar_a_no_aparecen_bloqueos_de_b_en_a(self):
        from motor.encargos import comprobacion

        reg, ident, espacio, destino, integrar, corridas, sweeps = self.banco(
            "exit 0"
        )
        interno = comprobacion.interno_de(espacio, crear=True)
        escribir_texto(
            interno / "comprobaciones.json",
            json.dumps({"comprobaciones": [{
                "objetivo": "lo declaro la fase B",
                "argv": ["false"],
                "tope": 5,
            }]}),
        )
        guardar = getattr(comprobacion, "guardar_declaradas_de_fase", None)
        if guardar is not None:
            guardar(interno, "B")
        sin_progreso_antes = len([
            ev for ev in reg.eventos(ident) if ev["kind"] == "sin_progreso"
        ])
        self.orden(reg, ident, "reintento-con-declaradas")
        sweeps((
            "2026-11-05T12:01:00+00:00", "2026-11-05T12:02:00+00:00",
        ))
        final = reg.fold(ident)
        a = next(f for f in final.fases if f.id == "A")
        self.assertEqual(a.estado, "preparada")
        self.assertFalse(
            [h for h in a.hallazgos if h.origen == "supervisor"],
            "los bloqueos de B se anotaron en A",
        )
        self.assertEqual(
            len([ev for ev in reg.eventos(ident) if ev["kind"] == "sin_progreso"]),
            sin_progreso_antes,
            "el reintento de A conto un sin_progreso por las comprobaciones de B",
        )
        # Las declaradas de B no quedan visibles para A cuando A cambia de fase.
        cambiar = getattr(comprobacion, "sincronizar_declaradas_de_fase", None)
        if cambiar is not None:
            cambiar(interno, "A")
        self.assertIsNone(
            comprobacion.leer_declaradas(interno),
            "las declaradas de B se cobran a la fase A",
        )


if __name__ == "__main__":
    unittest.main()
