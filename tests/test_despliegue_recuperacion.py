"""Una parada no pierde el comando vivo ni consume una receta nunca enviada."""

from __future__ import annotations

import json
import os
import shlex
import signal
import subprocess
import sys
import time
import types
import unittest
from pathlib import Path
from unittest import mock

from motor import plataforma
from motor.encargos import ciclo, despliegue as ejecutor, politica_despliegue
from motor.encargos.comprobacion import version_producto
from tests import test_encargos_despliegue_real as base


def _bloqueo_visible(enc) -> bool:
    fase = enc.fase_actual
    return bool(
        enc.pendientes_entrega
        or enc.impedimento
        or any(i.clase.startswith("despliegue:") for i in enc.impedimentos)
        or (fase is not None and fase.estado == "bloqueada")
    )


class RecuperacionDespliegue(unittest.TestCase):
    setUp = base.DespliegueReal.setUp

    def test_parada_antes_de_enviar_permite_desplegar_mismo_sha_al_continuar(self):
        reg, ident, espacio, destino, integrar = self.despliegue
        sha = integrar()
        preparar_real = ejecutor._git
        parada = False

        def preparar(repo, *args):
            nonlocal parada
            resultado = preparar_real(repo, *args)
            if args[:2] == ("worktree", "add") and not parada:
                parada = True
                ciclo.ordenar(reg, ident, "parar", "parar-preparacion", base.HORA)
            return resultado

        def ejecutar_en_proceso(cmd, cwd, env, log):
            ejecutor.ejecutar(Path(cmd[-1]))
            return {"pid": 99999999, "inicio": "", "boot_id": plataforma.boot_id(), "job": ""}

        with mock.patch.object(ejecutor, "_git", side_effect=preparar), mock.patch.object(
            plataforma, "lanzar_separado", side_effect=ejecutar_en_proceso,
        ):
            base.paso(self.despliegue)
            self.assertFalse((destino / "pasos").exists())
            ciclo.barrer(reg, base.HORA, time.time(), None)
            self.assertEqual(reg.fold(ident).estado, "detenido")
            ciclo.ordenar(reg, ident, "continuar", "seguir-preparacion", base.HORA)
            final = base.paso(self.despliegue)

        self.assertEqual(final.nivel_alcanzado, "desplegado")
        self.assertEqual(final.fase_actual.integrado_sha, sha)
        self.assertEqual((destino / "pasos").read_text(encoding="utf-8").splitlines(), [
            f"desplegar:{sha}", f"checklist:{sha}",
        ])
        recibos = list((reg.base / "despliegues").rglob("recibo.json"))
        self.assertEqual(len(recibos), 1)
        self.assertEqual(json.loads(recibos[0].read_text(encoding="utf-8"))["estado"], "aplicado")

    def test_worker_muerto_no_da_por_detenido_su_comando_vivo(self):
        reg, ident, espacio, destino, integrar = self.despliegue
        integrar()
        worker = plataforma.crear_proceso(
            [sys.executable, "-c", "import sys; sys.stdin.read()"],
            plataforma.Propiedad.COMANDO_CONTENIDO,
            stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        self.addCleanup(self.terminar, worker)
        worker.liberar()
        marca_worker = self.marca(worker)
        worker.proceso.communicate(timeout=5)
        self.assertEqual(plataforma.estado_proc(marca_worker), "muerto")

        comando = plataforma.crear_proceso(
            [sys.executable, "-c", "import time; time.sleep(60)"],
            plataforma.Propiedad.COMANDO_CONTENIDO,
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        comando.liberar()
        self.addCleanup(self.terminar, comando)
        marca_comando = self.marca(comando)
        with mock.patch.object(plataforma, "lanzar_separado", return_value=marca_worker):
            resultado = ejecutor.iniciar(
                reg, reg.fold(ident), espacio,
                politica_despliegue.resolver(espacio, reg.fold(ident)), base.HORA,
            )
        recibo = Path(resultado["recibo"])
        (recibo.parent / "comando.json").write_text(json.dumps(marca_comando), encoding="utf-8")
        ciclo.ordenar(reg, ident, "parar", "parar-huerfano", base.HORA)

        # Un cierre que aún no consigue matar el grupo no acredita la parada.
        with mock.patch.object(plataforma, "matar_grupo_pid"), mock.patch.object(
            plataforma, "cerrar_huerfanos",
        ):
            self.assertNotEqual(ejecutor.observacion(reg, reg.fold(ident)), "ausente")
            ciclo.barrer(reg, base.HORA, time.time(), None)
            self.assertIsNone(comando.proceso.poll())
            self.assertEqual(reg.fold(ident).estado, "deteniendo")

        self.terminar(comando)
        self.assertEqual(ejecutor.observacion(reg, reg.fold(ident)), "ausente")
        ciclo.barrer(reg, base.HORA, time.time(), None)
        self.assertEqual(reg.fold(ident).estado, "detenido")
        ciclo.ordenar(reg, ident, "continuar", "seguir-huerfano", base.HORA)
        final = base.paso(self.despliegue)
        self.assertNotEqual(final.estado, "necesita_usuario")
        self.assertTrue(_bloqueo_visible(final))
        self.assertEqual(final.nivel_alcanzado, "integrado")
        self.assertEqual(json.loads(recibo.read_text(encoding="utf-8"))["estado"], "incierto")
        self.assertFalse((destino / "pasos").exists())

    def test_el_worker_guarda_la_identidad_del_comando_mientras_esta_vivo(self):
        reg, ident, espacio, destino, integrar = self.despliegue
        liberar = destino / "liberar"
        with (espacio / "d.sh").open("a", encoding="utf-8") as script:
            script.write(f'while [ ! -f "{liberar.as_posix()}" ]; do sleep 0.05; done\n')
        integrar()
        try:
            ciclo._paso_despliegue(reg, reg.fold(ident), espacio, base.HORA)
            fin = time.monotonic() + 10
            while not (destino / "pasos").exists():
                self.assertLess(time.monotonic(), fin)
                time.sleep(0.05)
            recibo, = (reg.base / "despliegues").rglob("recibo.json")
            while True:
                marca = json.loads((recibo.parent / "comando.json").read_text(encoding="utf-8"))
                if marca.get("estado") != "preparando":
                    break
                self.assertLess(time.monotonic(), fin, "el worker no guardó la identidad")
                time.sleep(0.01)
            self.assertTrue(marca["inicio"])
            self.assertEqual(marca["boot_id"], plataforma.boot_id())
            self.assertEqual(plataforma.presencia_grupo(marca["pid"], marca["inicio"], marca["job"]), "vivo")
        finally:
            liberar.touch()
            base.paso(self.despliegue)

    def test_error_despues_de_un_efecto_queda_incierto_sin_reintentar(self):
        reg, ident, espacio, destino, integrar = self.despliegue
        integrar()
        self.aprobar_para_supervisor()
        efecto = destino / "efecto"

        def efecto_sin_respuesta(*args):
            with efecto.open("a", encoding="utf-8") as f:
                f.write("enviado\n")
            raise OSError("se perdió la lectura después del envío")

        def ejecutar_en_proceso(cmd, cwd, env, log):
            ejecutor.ejecutar(Path(cmd[-1]))
            return {"pid": 99999999, "inicio": "", "boot_id": plataforma.boot_id(), "job": ""}

        with mock.patch.object(ejecutor, "_correr", side_effect=efecto_sin_respuesta), mock.patch.object(
            plataforma, "lanzar_separado", side_effect=ejecutar_en_proceso,
        ):
            final = base.paso(self.despliegue)
            recibo, = (reg.base / "despliegues").rglob("recibo.json")
            datos = json.loads(recibo.read_text(encoding="utf-8"))
            self.assertEqual(datos["estado"], "incierto")
            self.assertIsNone(datos["pasos"][0]["enviado"])
            self.assertEqual(final.nivel_alcanzado, "integrado")
            self.assertNotEqual(final.estado, "necesita_usuario")
            self.assertTrue(_bloqueo_visible(final))
            self.comprobar_bloqueo_tras_continuar()
        self.assertEqual(json.loads(recibo.read_text(encoding="utf-8"))["estado"], "incierto")
        self.assertEqual(efecto.read_text(encoding="utf-8"), "enviado\n")
        self.assertFalse((destino / "pasos").exists())

    def test_continuar_reintenta_el_despliegue_pendiente(self):
        reg, ident, espacio, destino, integrar = self.despliegue
        corridas = destino / "corridas"
        (espacio / "d.sh").write_text(
            f'printf "desplegar:%s\\n" "$MOTOR_SHA" >> "{destino.as_posix()}/pasos"\n'
            f'n=$(cat "{corridas.as_posix()}" 2>/dev/null || echo 0); '
            f'n=$((n+1)); printf "%s" "$n" > "{corridas.as_posix()}"; '
            f'if [ "$n" -ge 2 ]; then exit 0; fi; exit 1\n'
        )
        sha = integrar()

        def ejecutar_en_proceso(cmd, cwd, env, log):
            ejecutor.ejecutar(Path(cmd[-1]))
            return {"pid": 99999999, "inicio": "", "boot_id": plataforma.boot_id(), "job": ""}

        with mock.patch.object(plataforma, "lanzar_separado", side_effect=ejecutar_en_proceso):
            final = base.paso(self.despliegue)
            self.assertNotEqual(final.estado, "necesita_usuario")
            self.assertTrue(_bloqueo_visible(final))
            recibo, = (reg.base / "despliegues").rglob("recibo.json")
            self.assertEqual(json.loads(recibo.read_text(encoding="utf-8"))["estado"], "fallido")
            self.assertEqual(corridas.read_text(encoding="utf-8"), "1")
            # Sin una orden de continuar el recibo fallido no se vuelve a correr.
            base.paso(self.despliegue)
            self.assertEqual(corridas.read_text(encoding="utf-8"), "1")
            ciclo.ordenar(reg, ident, "continuar", "reintentar-despliegue", base.HORA)
            final = reg.fold(ident)
            for _ in range(10):
                final = base.paso(self.despliegue)
                if final.nivel_alcanzado == "desplegado":
                    break
            else:
                self.fail(f"el despliegue no llegó a desplegado: {final.motivo}")

        self.assertEqual(final.nivel_alcanzado, "desplegado", final.motivo)
        self.assertEqual(corridas.read_text(encoding="utf-8"), "2")
        self.assertEqual(
            (destino / "pasos").read_text(encoding="utf-8").splitlines(),
            [f"desplegar:{sha}", f"rollback:{sha}", f"desplegar:{sha}", f"checklist:{sha}"],
        )

    def test_una_orden_anterior_al_fallo_no_reintenta(self):  # no-roja-en-base: la base ya conserva el camino fallido sin reintento; la prueba fija que una orden anterior al fallo tampoco lo autoriza
        reg, ident, espacio, destino, integrar = self.despliegue
        corridas = destino / "corridas"
        (espacio / "d.sh").write_text(
            f'printf "desplegar:%s\\n" "$MOTOR_SHA" >> "{destino.as_posix()}/pasos"\n'
            f'n=$(cat "{corridas.as_posix()}" 2>/dev/null || echo 0); '
            f'n=$((n+1)); printf "%s" "$n" > "{corridas.as_posix()}"; exit 1\n'
        )
        integrar()

        def ejecutar_en_proceso(cmd, cwd, env, log):
            ejecutor.ejecutar(Path(cmd[-1]))
            return {"pid": 99999999, "inicio": "", "boot_id": plataforma.boot_id(), "job": ""}

        with mock.patch.object(plataforma, "lanzar_separado", side_effect=ejecutar_en_proceso):
            ciclo.ordenar(reg, ident, "continuar", "orden-temprana", base.HORA)
            ciclo._paso_despliegue(reg, reg.fold(ident), espacio, base.HORA)
            ciclo._paso_despliegue(reg, reg.fold(ident), espacio, "2026-11-05T12:00:30+00:00")
        self.assertEqual(corridas.read_text(encoding="utf-8"), "1")
        self.assertTrue(_bloqueo_visible(reg.fold(ident)))
        recibo, = (reg.base / "despliegues").rglob("recibo.json")
        self.assertEqual(json.loads(recibo.read_text(encoding="utf-8"))["estado"], "fallido")

    def test_continuar_reabre_la_ventana_ocupado_agotada(self):
        reg, ident, espacio, destino, integrar = self.despliegue
        corridas = destino / "corridas"
        (espacio / "d.sh").write_text(
            f'printf "desplegar:%s\\n" "$MOTOR_SHA" >> "{destino.as_posix()}/pasos"\n'
            f'n=$(cat "{corridas.as_posix()}" 2>/dev/null || echo 0); '
            f'n=$((n+1)); printf "%s" "$n" > "{corridas.as_posix()}"; '
            f'if [ "$n" -ge 7 ]; then exit 0; fi; '
            f'printf "app.cli vivo (spapi-diario)\\n"; exit 75\n'
        )
        integrar()

        def ejecutar_en_proceso(cmd, cwd, env, log):
            ejecutor.ejecutar(Path(cmd[-1]))
            return {"pid": 99999999, "inicio": "", "boot_id": plataforma.boot_id(), "job": ""}

        horas = (
            "2026-11-05T12:00:00+00:00", "2026-11-05T12:15:00+00:00",
            "2026-11-05T12:45:00+00:00", "2026-11-05T13:45:00+00:00",
            "2026-11-05T15:45:00+00:00", "2026-11-05T19:45:00+00:00",
        )
        with mock.patch.object(plataforma, "lanzar_separado", side_effect=ejecutar_en_proceso):
            for hora in horas:
                ciclo._paso_despliegue(reg, reg.fold(ident), espacio, hora)
                self.assertIsNotNone(
                    next((reg.base / "despliegues").rglob("ocupado.json"), None), hora,
                )
            # La séptima pasada toca el tope de la ventana: impedimento en llano.
            ciclo._paso_despliegue(reg, reg.fold(ident), espacio, "2026-11-05T19:50:00+00:00")
            final = reg.fold(ident)
            causa = final.impedimentos[-1].causa if final.impedimentos else ""
            self.assertIn("ocupado 6 veces", causa)
            # La ventana agotada pide intervención: continuar la reabre una vez.
            ciclo.ordenar(reg, ident, "continuar", "otra-ventana", horas[-1])
            final = ciclo._paso_despliegue(
                reg, reg.fold(ident), espacio, "2026-11-05T20:00:00+00:00"
            )
            for _ in range(10):
                final = ciclo._paso_despliegue(
                    reg, reg.fold(ident), espacio, "2026-11-05T20:05:00+00:00"
                )
                if final.nivel_alcanzado == "desplegado":
                    break
            else:
                self.fail(f"el despliegue no llegó a desplegado: {final.motivo}")
        self.assertEqual(corridas.read_text(encoding="utf-8"), "7")

    def test_el_libro_ocupado_se_reinicia_al_desplegar(self):
        # F1: tras un éxito de la misma identidad la ventana arranca limpia;
        # el tope no bloquea para siempre.
        reg, ident, espacio, destino, integrar = self.despliegue
        corridas = destino / "corridas"
        guion = "\n".join([
            f'printf "desplegar:%s\\n" "$MOTOR_SHA" >> "{destino.as_posix()}/pasos"',
            f'n=$(cat "{corridas.as_posix()}" 2>/dev/null || echo 0)',
            "n=$((n+1))",
            f'printf "%s" "$n" > "{corridas.as_posix()}"',
            'if [ "$n" -ge 7 ]; then exit 0; fi',
            'printf "app.cli vivo (spapi-diario)"',
            "exit 75",
        ])
        (espacio / "d.sh").write_text(guion)
        integrar()

        def ejecutar_en_proceso(cmd, cwd, env, log):
            ejecutor.ejecutar(Path(cmd[-1]))
            return {"pid": 99999999, "inicio": "", "boot_id": plataforma.boot_id(), "job": ""}

        horas = (
            "2026-11-05T12:00:00+00:00", "2026-11-05T12:15:00+00:00",
            "2026-11-05T12:45:00+00:00", "2026-11-05T13:45:00+00:00",
            "2026-11-05T15:45:00+00:00", "2026-11-05T19:45:00+00:00",
        )
        with mock.patch.object(plataforma, "lanzar_separado", side_effect=ejecutar_en_proceso):
            for hora in horas:
                ciclo._paso_despliegue(reg, reg.fold(ident), espacio, hora)
            # La séptima pasada toca el tope: impedimento; continuar reabre.
            ciclo._paso_despliegue(reg, reg.fold(ident), espacio, "2026-11-05T19:46:00+00:00")
            ciclo.ordenar(reg, ident, "continuar", "otra-ventana", "2026-11-05T19:46:00+00:00")
            final = reg.fold(ident)
            for _ in range(10):
                final = ciclo._paso_despliegue(
                    reg, reg.fold(ident), espacio, "2026-11-05T20:00:00+00:00"
                )
                if final.nivel_alcanzado == "desplegado":
                    break
            else:
                self.fail(f"el despliegue no llegó a desplegado: {final.motivo}")
        libros = list((reg.base / "despliegues").rglob("ocupado.json"))
        self.assertEqual(libros, [], "el libro no se reinició al aplicar")
        self.assertEqual(ejecutor.ocupado(reg, reg.fold(ident)), {})

    def test_el_pendiente_de_despliegue_no_entrega_con_pendientes(self):
        # F4: el aviso del despliegue es transitorio y no convierte la pasada
        # en una entrega parcial.
        reg, ident, espacio, destino, integrar = self.despliegue
        integrar()
        entregas = mock.Mock(
            side_effect=AssertionError("el despliegue no entrega con pendientes")
        )
        with mock.patch.object(ciclo, "_entregar_con_pendientes", entregas):
            reg.anotar(ident, {"kind": "pendiente_entrega", "data": {
                "texto": "despliegue: causa transitoria del despliegue",
                "hora": base.HORA,
            }}, "pendiente-despliegue")
            self.assertTrue(reg.fold(ident).pendientes_entrega)
            ciclo.barrer(reg, base.HORA, time.time(), mock.Mock())
        entregas.assert_not_called()

    def test_otra_causa_de_pendiente_si_entrega_con_pendientes(self):  # no-roja-en-base: la base ya desvía la pasada con cualquier pendiente; la prueba fija que la excepción es solo del despliegue
        reg, ident, espacio, destino, integrar = self.despliegue
        integrar()
        entregas = mock.Mock(return_value=reg.fold(ident))
        with mock.patch.object(ciclo, "_entregar_con_pendientes", entregas):
            reg.anotar(ident, {"kind": "pendiente_entrega", "data": {
                "texto": "el revisor no concluyó tras el tope",
                "hora": base.HORA,
            }}, "pendiente-otro")
            ciclo.barrer(reg, base.HORA, time.time(), mock.Mock())
        entregas.assert_called()

    def test_una_orden_anterior_no_reabre_la_ventana_ocupada(self):
        # h_51c2637c (ronda 5): la orden previa al despliegue ya no consume
        # la ventana; el libro arranca con la fotografía del recibo.
        reg, ident, espacio, destino, integrar = self.despliegue
        corridas = destino / "corridas"
        guion = "\n".join([
            f'printf "desplegar:%s\\n" "$MOTOR_SHA" >> "{destino.as_posix()}/pasos"',
            f'n=$(cat "{corridas.as_posix()}" 2>/dev/null || echo 0)',
            "n=$((n+1))",
            f'printf "%s" "$n" > "{corridas.as_posix()}"',
            'printf "app.cli vivo (spapi-diario)"',
            "exit 75",
        ])
        (espacio / "d.sh").write_text(guion)
        integrar()

        def ejecutar_en_proceso(cmd, cwd, env, log):
            ejecutor.ejecutar(Path(cmd[-1]))
            return {"pid": 99999999, "inicio": "", "boot_id": plataforma.boot_id(), "job": ""}

        horas = (
            "2026-11-05T12:00:00+00:00", "2026-11-05T12:15:00+00:00",
            "2026-11-05T12:45:00+00:00", "2026-11-05T13:45:00+00:00",
            "2026-11-05T15:45:00+00:00", "2026-11-05T19:45:00+00:00",
        )
        with mock.patch.object(plataforma, "lanzar_separado", side_effect=ejecutar_en_proceso):
            ciclo.ordenar(reg, ident, "continuar", "orden-previa", base.HORA)
            for hora in horas:
                ciclo._paso_despliegue(reg, reg.fold(ident), espacio, hora)
            # El tope se toca sin ninguna orden nueva.
            ciclo._paso_despliegue(reg, reg.fold(ident), espacio, "2026-11-05T19:46:00+00:00")
        self.assertEqual(corridas.read_text(encoding="utf-8"), "6")
        final = reg.fold(ident)
        causa = final.impedimentos[-1].causa if final.impedimentos else ""
        self.assertIn("ocupado 6 veces", causa)

    def test_continuar_alcanza_el_despliegue_de_n_desde_el_barrido(self):
        # h_925eb491 (ronda 5): continuar llega a la fase bloqueada con
        # despliegue pendiente aunque el barrido esté trabajando otra fase.
        reg, ident, espacio, destino, integrar = self.despliegue
        corridas = destino / "corridas"
        guion = "\n".join([
            f'printf "desplegar:%s\\n" "$MOTOR_SHA" >> "{destino.as_posix()}/pasos"',
            f'n=$(cat "{corridas.as_posix()}" 2>/dev/null || echo 0)',
            "n=$((n+1))",
            f'printf "%s" "$n" > "{corridas.as_posix()}"',
            'if [ "$n" -eq 1 ]; then exit 0; fi',
            'if [ "$n" -ge 3 ]; then exit 0; fi',
            "exit 1",
        ])
        (espacio / "d.sh").write_text(guion)
        receta = {"desplegar": "d.sh", "checklist": "c.sh", "rollback": "r.sh"}
        reg.anotar(ident, {"kind": "fases", "data": {"fases": [
            {"id": "K", "resultado": "K lista", "requiere_despliegue": True, "despliegue": receta},
            {"id": "N", "resultado": "N lista", "requiere": ["K"], "requiere_despliegue": True, "despliegue": receta},
            {"id": "O", "resultado": "O lista", "requiere": ["K"], "requiere_despliegue": True, "despliegue": receta},
        ]}}, "fases")
        self.assertEqual(reg.fold(ident).fase_activa, "K")

        def ejecutar_en_proceso(cmd, cwd, env, log):
            ejecutor.ejecutar(Path(cmd[-1]))
            return {"pid": 99999999, "inicio": "", "boot_id": plataforma.boot_id(), "job": ""}

        with mock.patch.object(plataforma, "lanzar_separado", side_effect=ejecutar_en_proceso):
            integrar()
            base.paso(self.despliegue)
            reg.anotar(ident, {"kind": "comprobacion", "data": {
                "clase": "cierre_comprobado", "evidencia": "recibo aplicado de K",
                "hora": base.HORA,
            }}, "cierre-K")
            self.assertEqual(reg.fold(ident).fase_activa, "N", reg.fold(ident).motivo)
            (espacio / "cambio-n.txt").write_text("cambio de N", encoding="utf-8")
            integrar()
            base.paso(self.despliegue)
            enc = reg.fold(ident)
            causa = enc.impedimentos[-1].causa
            reg.anotar(ident, {"kind": "bloqueo_fase", "data": {
                "evidencia": "recibo fallido de N", "causa": causa,
            }}, "bloqueo-N")
            self.assertEqual(reg.fold(ident).fase_activa, "O", reg.fold(ident).motivo)
            sonda = mock.Mock(return_value=types.SimpleNamespace(
                ok=True, causa="", condicion="",
            ))
            with mock.patch.object(ciclo, "_sonda_entorno", sonda):
                ciclo.ordenar(reg, ident, "continuar", "reabrir-N-barrido", base.HORA)
                # Pasada 1: reactiva N y relanza su receta; pasada 2: concilia
                # el recibo aplicado y sella desplegado; pasada 3: vence la
                # espera de conciliación y avanza el nivel.
                ciclo.barrer(reg, "2026-11-05T12:01:00+00:00", time.time(), mock.Mock())
                ciclo.barrer(reg, "2026-11-05T12:02:00+00:00", time.time(), mock.Mock())
                ciclo.barrer(reg, "2026-11-05T12:32:00+00:00", time.time(), mock.Mock())
        self.assertEqual(corridas.read_text(encoding="utf-8"), "2")
        reactivadas = [
            ev for ev in reg.eventos(ident) if ev["kind"] == "fase_reactivada"
        ]
        self.assertTrue(reactivadas, "continuar no reabrió la fase bloqueada")
        # La fase N deja de estar bloqueada y su receta corrió la segunda vez.
        enc = reg.fold(ident)
        fases = {f.id: f for f in enc.fases}
        self.assertNotEqual(fases["N"].estado, "bloqueada")
        self.assertEqual(fases["N"].ultimo_paso_entrega, "integrado")

    def test_continuar_reintenta_el_despliegue_de_una_fase_bloqueada(self):
        reg, ident, espacio, destino, integrar = self.despliegue
        corridas = destino / "corridas"
        (espacio / "d.sh").write_text(
            f'printf "desplegar:%s\\n" "$MOTOR_SHA" >> "{destino.as_posix()}/pasos"\n'
            f'n=$(cat "{corridas.as_posix()}" 2>/dev/null || echo 0); '
            f'n=$((n+1)); printf "%s" "$n" > "{corridas.as_posix()}"; '
            f'if [ "$n" -eq 1 ]; then exit 0; fi; '
            f'if [ "$n" -ge 3 ]; then exit 0; fi; exit 1\n'
        )
        receta = {"desplegar": "d.sh", "checklist": "c.sh", "rollback": "r.sh"}
        reg.anotar(ident, {"kind": "fases", "data": {"fases": [
            {"id": "K", "resultado": "K lista", "requiere_despliegue": True, "despliegue": receta},
            {"id": "N", "resultado": "N lista", "requiere": ["K"], "requiere_despliegue": True, "despliegue": receta},
            {"id": "O", "resultado": "O lista", "requiere": ["K"], "requiere_despliegue": True, "despliegue": receta},
        ]}}, "fases")
        self.assertEqual(reg.fold(ident).fase_activa, "K")

        def ejecutar_en_proceso(cmd, cwd, env, log):
            ejecutor.ejecutar(Path(cmd[-1]))
            return {"pid": 99999999, "inicio": "", "boot_id": plataforma.boot_id(), "job": ""}

        with mock.patch.object(plataforma, "lanzar_separado", side_effect=ejecutar_en_proceso):
            integrar()
            base.paso(self.despliegue)
            self.assertEqual(reg.fold(ident).fase_actual.id, "K")
            reg.anotar(ident, {"kind": "comprobacion", "data": {
                "clase": "cierre_comprobado", "evidencia": "recibo aplicado de K",
                "hora": base.HORA,
            }}, "cierre-K")
            self.assertEqual(reg.fold(ident).fase_activa, "N", reg.fold(ident).motivo)
            (espacio / "cambio-n.txt").write_text("cambio de N", encoding="utf-8")
            integrar()
            base.paso(self.despliegue)
            self.assertEqual(corridas.read_text(encoding="utf-8"), "2")
            enc = reg.fold(ident)
            causa = enc.impedimentos[-1].causa
            reg.anotar(ident, {"kind": "bloqueo_fase", "data": {
                "evidencia": "recibo fallido de N", "causa": causa,
            }}, "bloqueo-N")
            self.assertEqual(reg.fold(ident).fase_activa, "O", reg.fold(ident).motivo)
            # Continuar reabre la fase bloqueada con su despliegue pendiente.
            ciclo.ordenar(reg, ident, "continuar", "reabrir-N", base.HORA)
            final = reg.fold(ident)
            for _ in range(12):
                final = ciclo._paso_despliegue(reg, reg.fold(ident), espacio, base.HORA)
                if final.fase_activa != "N":
                    break
            reg.anotar(ident, {"kind": "comprobacion", "data": {
                "clase": "cierre_comprobado", "evidencia": "recibo aplicado de N",
                "hora": base.HORA,
            }}, "cierre-N")
            final = reg.fold(ident)
        self.assertEqual(final.fase_activa, "O", final.motivo)
        self.assertEqual(corridas.read_text(encoding="utf-8"), "3")
        fases = {f.id: f for f in final.fases}
        self.assertEqual(fases["N"].estado, "preparada")
        self.assertIsNotNone(fases["N"].desplegado_recibo)

    def test_recibo_de_ensayo_vuelve_a_bloquear_tras_continuar_sin_lanzar_agente(self):
        reg, ident, _, destino, integrar = self.despliegue
        integrar()
        self.aprobar_para_supervisor()
        reg.anotar(ident, {"kind": "entrega_externa", "data": {
            "paso": "desplegado", "recibo": "dst_ensayo", "hora": base.HORA,
        }}, "despliegue-legado")
        final = ciclo._paso_entrega(reg, reg.fold(ident), None, base.HORA)
        self.assertNotEqual(final.estado, "necesita_usuario")
        self.assertTrue(_bloqueo_visible(final))
        self.comprobar_bloqueo_tras_continuar()
        self.assertFalse((destino / "pasos").exists())

    def aprobar_para_supervisor(self):
        reg, ident, espacio, *_ = self.despliegue
        version = version_producto(espacio)
        reg.anotar(ident, {"kind": "producto_listo", "data": {
            "version": version.firma, "manifiesto": [list(p) for p in version.manifiesto],
            "sha_git": reg.fold(ident).fase_actual.integrado_sha, "hora": base.HORA,
        }}, "producto-revisado")
        reg.anotar(ident, {"kind": "revision", "data": {
            "revision_id": "rev-receta", "version": version.firma,
            "veredicto": "aprobado", "origen": "independiente",
            "revisor": "revisor", "evidencia": "receta comprobada", "hora": base.HORA,
        }}, "revision-receta")
        self.assertTrue(ciclo._aplica_entrega(reg.fold(ident)))

    def comprobar_bloqueo_tras_continuar(self):
        reg, ident, *_ = self.despliegue
        lanzador = mock.Mock(side_effect=AssertionError("el bloqueo no requiere otro agente"))
        hora = ciclo.ahora_iso()
        if reg.fold(ident).estado != "detenido":
            ciclo.ordenar(reg, ident, "parar", "parar-bloqueo", hora)
            limite = time.monotonic() + 5
            while reg.fold(ident).estado != "detenido":
                self.assertLess(time.monotonic(), limite)
                ciclo.barrer(reg, hora, time.time(), lanzador)
                time.sleep(0.01)
        ciclo.ordenar(reg, ident, "continuar", "seguir-bloqueo", hora)
        self.assertEqual(reg.fold(ident).estado, "recibido")
        ciclo.barrer(reg, hora, time.time(), lanzador)
        final = reg.fold(ident)
        self.assertNotEqual(final.estado, "necesita_usuario")
        self.assertEqual(
            [ev for ev in reg.eventos(ident) if ev["kind"] == "pregunta"], []
        )
        lanzador.assert_not_called()

    @unittest.skipIf(os.name == "nt", "la supervivencia a SIGTERM se comprueba en POSIX")
    def test_hijo_que_ignora_term_muere_antes_del_checklist(self):
        reg, ident, espacio, destino, integrar = self.despliegue
        pidfile = destino / "hijo.pid"
        (espacio / "hijo.py").write_text(
            "import os, signal, time\nfrom pathlib import Path\n"
            "signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
            f"Path({str(pidfile)!r}).write_text(str(os.getpid()), encoding='utf-8')\n"
            "time.sleep(60)\n", encoding="utf-8",
        )
        with (espacio / "d.sh").open("a", encoding="utf-8") as script:
            script.write(
                f"{shlex.quote(sys.executable)} hijo.py &\n"
                f'while [ ! -s "{pidfile.as_posix()}" ]; do sleep 0.01; done\n'
            )
        integrar()
        correr_real = ejecutor._correr
        hijo = {}
        checklists = []

        def correr(registro, encargo, paso, *args):
            if paso == "checklist":
                self.assertFalse(plataforma.pid_vivo(hijo["pid"]))
                anterior = json.loads((args[1].parent / "comando.json").read_text(encoding="utf-8"))
                self.assertEqual(plataforma.presencia_grupo(
                    anterior["pid"], anterior["inicio"], anterior["job"],
                ), "ausente")
                checklists.append(paso)
            try:
                return correr_real(registro, encargo, paso, *args)
            finally:
                if paso == "desplegar" and pidfile.exists():
                    hijo["pid"] = int(pidfile.read_text(encoding="utf-8"))
                    hijo["inicio"] = plataforma.hora_inicio(hijo["pid"])

        def ejecutar_en_proceso(cmd, cwd, env, log):
            ejecutor.ejecutar(Path(cmd[-1]))
            return {"pid": 99999999, "inicio": "", "boot_id": plataforma.boot_id(), "job": ""}

        try:
            with mock.patch.object(ejecutor, "_correr", side_effect=correr), mock.patch.object(
                plataforma, "lanzar_separado", side_effect=ejecutar_en_proceso,
            ), mock.patch.object(ejecutor, "TOPE_S", 5):
                final = base.paso(self.despliegue)
            self.assertTrue(hijo)
            self.assertFalse(plataforma.pid_vivo(hijo["pid"]))
            self.assertEqual(final.nivel_alcanzado, "desplegado")
            self.assertEqual(checklists, ["checklist"])
        finally:
            if (hijo.get("inicio") and plataforma.pid_vivo(hijo["pid"])
                    and plataforma.hora_inicio(hijo["pid"]) == hijo["inicio"]):
                try:
                    os.kill(hijo["pid"], signal.SIGKILL)
                except ProcessLookupError:
                    pass

    @staticmethod
    def marca(creado):
        return {"pid": creado.proceso.pid, "inicio": plataforma.hora_inicio(creado.proceso.pid),
                "boot_id": plataforma.boot_id(), "job": creado.job}

    @staticmethod
    def terminar(creado):
        if os.name == "nt" or creado.proceso.poll() is None:
            plataforma.matar_grupo(creado.proceso)
        creado.proceso.wait(timeout=5)
