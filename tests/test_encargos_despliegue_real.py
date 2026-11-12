"""El sello de producción exige ejecutar la receta del commit integrado."""

import json
import os
import subprocess
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from motor import plataforma
from motor.encargos import ciclo
from motor.encargos.dominio import relevos_de
from motor.encargos.registro import Registro
from tests.entorno import deja_breakaway

HORA = "2026-11-05T12:00:00+00:00"
SEPARA_TRABAJADOR = os.name != "nt" or deja_breakaway()


def cerrar_trabajador(creado):
    try:
        creado.proceso.wait(timeout=10)
    except subprocess.TimeoutExpired:
        pass
    if os.name == "nt" or creado.proceso.poll() is None:
        # En Windows cierra el handle del Job después de terminar el worker.
        plataforma.matar_grupo(creado.proceso)
    creado.proceso.wait(timeout=5)


def trabajador_contenido(caso):
    """El CI sin breakaway prueba recetas reales, sin probar supervivencia al padre.

    La PC con breakaway conserva el lanzador de producción y la separación real.
    El Job del worker contenido se mantiene abierto hasta terminar cada prueba.
    """
    def lanzar(cmd, cwd, env, log):
        with log.open("ab") as out:
            creado = plataforma.crear_proceso(
                cmd, plataforma.Propiedad.COMANDO_CONTENIDO,
                cwd=cwd, env=env, stdin=subprocess.DEVNULL,
                stdout=out, stderr=subprocess.STDOUT,
            )
        caso.addCleanup(cerrar_trabajador, creado)
        marca = {
            "pid": creado.proceso.pid, "boot_id": plataforma.boot_id(),
            "inicio": plataforma.hora_inicio(creado.proceso.pid),
            "job": creado.job, "separacion": creado.separacion,
        }
        creado.liberar()
        return marca

    caso.enterContext(mock.patch.object(plataforma, "lanzar_separado", side_effect=lanzar))


def git(raiz, *args):
    return subprocess.run(
        ["git", "-C", str(raiz), *args], check=True,
        capture_output=True, text=True,
    ).stdout.strip()


def paso(caso):
    reg, ident, espacio, *_ = caso
    # 30 s: en un runner hospedado cargado el trabajador tarda en publicar el
    # recibo; el modulo entero corrio 7 min en esa condicion y los 15 s
    # anteriores salieron apretados.
    limite = time.monotonic() + 30
    while True:
        final = ciclo._paso_despliegue(reg, reg.fold(ident), espacio, HORA)
        if "ejecutando receta" not in final.motivo or final.nivel_alcanzado == "desplegado":
            return final
        assert time.monotonic() < limite, final.motivo
        time.sleep(0.05)


def recibo_final(reg, limite=30):
    """Espera el recibo que el trabajador publica al terminar, no el parcial.

    El recibo apunta el paso antes de ejecutarlo y escribe su código después.
    Un supervisor que lee justo entre esas dos escrituras ve el paso sin
    `codigo`; esperar el estado final es lo que hace determinista la prueba.
    """
    fin = time.monotonic() + limite
    while True:
        recibos = list((reg.base / "despliegues").rglob("recibo.json"))
        if recibos:
            datos = json.loads(recibos[0].read_text())
            pasos = datos.get("pasos", [])
            if (
                datos.get("estado")
                in ("aplicado", "fallido", "incierto", "esperando", "ocupado")
                and all("codigo" in p for p in pasos)
            ):
                return datos
        assert time.monotonic() < fin, "el recibo no llegó a su estado final"
        time.sleep(0.05)


class DespliegueReal(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        tmp_path = Path(tmp.name)
        reg = Registro(tmp_path / "base")
        self.addCleanup(reg.close)
        ident = ciclo.recibir(
            reg, "desplegar la versión revisada", "receta-real", HORA,
            entrega_nivel="desplegado", repo="ejemplo/repo", base_rama="main",
        )["encargo"]
        espacio = reg.base / "espacio" / ident
        espacio.mkdir(parents=True)
        git(espacio, "init", "-q", "-b", "main")
        (espacio / ".motor.toml").write_text(
            '[deploy]\ndesplegar="d.sh"\nchecklist="c.sh"\nrollback="r.sh"\n'
            'requiere=["ssh-goncloud"]\n'
        )
        (reg.base / "maquina.toml").write_text(
            'nombre="ensayo local"\nconf="."\ntiene=["ssh-goncloud"]\n'
        )
        # Los scripts escriben fuera del checkout para observar el efecto real.
        destino = tmp_path / "servidor"
        destino.mkdir()
        for nombre, paso in (("d.sh", "desplegar"), ("c.sh", "checklist"), ("r.sh", "rollback")):
            (espacio / nombre).write_text(
                f'printf "{paso}:%s\\n" "$MOTOR_SHA" >> "{destino.as_posix()}/pasos"\n'
                f'printf "salida de {paso}\\n"\n'
            )

        def integrar():
            git(espacio, "add", ".")
            git(espacio, "-c", "user.name=t", "-c", "user.email=t@local",
                "commit", "-qm", "receta integrada")
            sha = git(espacio, "rev-parse", "HEAD")
            reg.anotar(ident, {"kind": "entrega_externa", "data": {
                "paso": "integrado", "sha": sha, "recibo": sha, "hora": HORA,
            }}, "integrado:" + sha)
            return sha

        self.despliegue = reg, ident, espacio, destino, integrar
        if not SEPARA_TRABAJADOR:
            trabajador_contenido(self)

    def parche(self, objeto, atributo, valor):
        parche = mock.patch.object(objeto, atributo, valor)
        self.addCleanup(parche.stop)
        parche.start()

    def test_ejecuta_sha_mergeado_y_guarda_salidas_sin_repetir(self):  # no-roja-en-base: sobre la base el despliegue feliz tambien llega a desplegado; la prueba caracteriza el camino que la fase conserva y agrega el motivo al fallo
        despliegue = self.despliegue
        reg, ident, espacio, destino, integrar = despliegue
        sha = integrar()
        # Una edición local posterior nunca es la receta que llega a producción.
        (espacio / "d.sh").write_text("exit 91\n")
        final = paso(despliegue)
        assert final.nivel_alcanzado == "desplegado", final.motivo
        assert (destino / "pasos").read_text().splitlines() == [
            f"desplegar:{sha}", f"checklist:{sha}",
        ]
        recibo = next(ev["data"]["recibo"] for ev in reg.eventos(ident)
                      if ev["kind"] == "entrega_externa" and ev["data"]["paso"] == "desplegado")
        datos = json.loads(Path(recibo).read_text())
        assert datos["sha"] == sha
        assert [p["codigo"] for p in datos["pasos"]] == [0, 0]
        assert datos["pasos"][0]["stdout"] == "salida de desplegar\n"
        paso(despliegue)
        assert len((destino / "pasos").read_text().splitlines()) == 2
        assert not (reg.base / "destino").exists()

    def comprobar_falla(self, script, codigo, reversa=0):
        despliegue = self.despliegue
        reg, _, espacio, destino, integrar = despliegue
        with (espacio / script).open("a") as f:
            f.write(f"exit {codigo}\n")
        if reversa:
            with (espacio / "r.sh").open("a") as f:
                f.write(f"exit {reversa}\n")
        sha = integrar()
        final = paso(despliegue)
        assert final.nivel_alcanzado == "integrado"
        assert (destino / "pasos").read_text().splitlines()[-1] == f"rollback:{sha}"
        recibos = list((reg.base / "despliegues").rglob("recibo.json"))
        assert len(recibos) == 1
        datos = json.loads(recibos[0].read_text())
        assert datos["estado"] == "fallido"
        assert datos["pasos"][-2]["codigo"] == codigo
        antes = (destino / "pasos").read_text()
        paso(despliegue)
        assert (destino / "pasos").read_text() == antes

    def test_falla_despliegue_9_ejecuta_rollback_y_nunca_sella(self):
        self.comprobar_falla("d.sh", 9)

    def test_falla_checklist_3_ejecuta_rollback_y_nunca_sella(self):
        self.comprobar_falla("c.sh", 3)

    def test_75_con_reversa_que_falla_sigue_como_hoy(self):  # no-roja-en-base: caracteriza el camino que la fase conserva; la base ya deja fallido con impedimento
        self.comprobar_falla("d.sh", 75, reversa=1)

    def test_75_del_checklist_no_es_ocupado(self):  # no-roja-en-base: la base ya deja fallido cualquier salida distinta de 0; la prueba fija el límite del camino ocupado nuevo
        despliegue = self.despliegue
        reg, ident, espacio, destino, integrar = despliegue
        with (espacio / "c.sh").open("a") as f:
            f.write('printf "ocupado: app.cli vivo\\n"; exit 75\n')
        integrar()
        self._ejecucion_en_proceso()
        final = paso(despliegue)
        datos = recibo_final(reg)
        assert datos["estado"] == "fallido", datos
        assert [p["codigo"] for p in datos["pasos"]] == [0, 75, 0], datos["pasos"]
        causa = final.impedimentos[-1].causa if final.impedimentos else ""
        assert "revisar el recibo" in causa, causa
        assert not list((reg.base / "despliegues").rglob("ocupado.json"))

    def test_75_sin_diagnostico_de_ocupacion_no_es_ocupado(self):  # no-roja-en-base: la base ya deja fallido un 75; la prueba fija que el ocupado exige el diagnóstico que la receta declara
        despliegue = self.despliegue
        reg, ident, espacio, destino, integrar = despliegue
        (espacio / "d.sh").write_text('printf "error de datos\\n"; exit 75\n')
        integrar()
        self._ejecucion_en_proceso()
        final = paso(despliegue)
        datos = recibo_final(reg)
        assert datos["estado"] == "fallido", datos
        assert [p["codigo"] for p in datos["pasos"]] == [75, 0], datos["pasos"]
        causa = final.impedimentos[-1].causa if final.impedimentos else ""
        assert "revisar el recibo" in causa, causa
        assert not list((reg.base / "despliegues").rglob("ocupado.json"))
        antes = (destino / "pasos").read_text()
        paso(despliegue)
        assert (destino / "pasos").read_text() == antes

    def test_el_diagnostico_de_ocupacion_viene_de_la_salida_real(self):
        despliegue = self.despliegue
        reg, ident, espacio, destino, integrar = despliegue
        (espacio / "d.sh").write_text(
            'printf "preparacion completa\\n"; '
            'printf "ocupado: app.cli vivo (spapi-diario)\\n" >&2; exit 75\n'
        )
        integrar()
        self._ejecucion_en_proceso()
        ciclo._paso_despliegue(reg, reg.fold(ident), espacio, HORA)
        datos = recibo_final(reg)
        assert datos["estado"] == "ocupado", datos
        libro = next((reg.base / "despliegues").rglob("ocupado.json"))
        contenido = json.loads(libro.read_text())
        assert "app.cli vivo (spapi-diario)" in contenido["proceso"], contenido
        # Ventana agotada: el impedimento cita el proceso que la receta
        # reportó en su salida real, no la última línea de resumen. La
        # primera corrida ya consumió el intento 1: quedan 5 de la ventana.
        for _ in range(5):
            contenido["ultimo"] = "2026-11-04T11:00:00Z"
            libro.write_text(json.dumps(contenido))
            ciclo._paso_despliegue(reg, reg.fold(ident), espacio, HORA)
            libro = next((reg.base / "despliegues").rglob("ocupado.json"))
            contenido = json.loads(libro.read_text())
        enc = ciclo._paso_despliegue(reg, reg.fold(ident), espacio, HORA)
        causa = enc.impedimentos[-1].causa if enc.impedimentos else ""
        assert "ocupado 6 veces" in causa, causa
        assert "app.cli vivo (spapi-diario)" in causa, causa
        assert "preparacion completa" not in causa, causa

    def test_el_diagnostico_no_lo_reemplaza_una_linea_de_resumen(self):
        despliegue = self.despliegue
        reg, ident, espacio, destino, integrar = despliegue
        (espacio / "d.sh").write_text(
            'printf "ocupado: app.cli vivo (spapi-diario)\\n'
            'ESTADO=abortado_sin_cambios\\n"; exit 75\n'
        )
        integrar()
        self._ejecucion_en_proceso()
        ciclo._paso_despliegue(reg, reg.fold(ident), espacio, HORA)
        datos = recibo_final(reg)
        assert datos["estado"] == "ocupado", datos
        libro = next((reg.base / "despliegues").rglob("ocupado.json"))
        contenido = json.loads(libro.read_text())
        assert contenido["proceso"] == "ocupado: app.cli vivo (spapi-diario)", contenido

    def test_75_con_reversa_limpia_espera_y_reintenta_solo(self):
        despliegue = self.despliegue
        reg, ident, espacio, destino, integrar = despliegue
        corridas = destino / "corridas"
        (espacio / "d.sh").write_text(
            f'printf "desplegar:%s\\n" "$MOTOR_SHA" >> "{destino.as_posix()}/pasos"\n'
            f'n=$(cat "{corridas.as_posix()}" 2>/dev/null || echo 0); '
            f'n=$((n+1)); printf "%s" "$n" > "{corridas.as_posix()}"; '
            f'if [ "$n" -ge 2 ]; then exit 0; fi; '
            f'printf "ocupado: app.cli vivo\\n"; exit 75\n'
        )
        sha = integrar()
        self._ejecucion_en_proceso()
        ciclo._paso_despliegue(reg, reg.fold(ident), espacio, HORA)
        datos = recibo_final(reg)
        assert datos["estado"] == "ocupado", datos
        assert [p["codigo"] for p in datos["pasos"]] == [75, 0]
        enc = ciclo._paso_despliegue(reg, reg.fold(ident), espacio, HORA)
        assert enc.estado == "esperando_reintento", (enc.estado, enc.motivo)
        assert (
            "se reintenta solo a las 2026-11-05T12:15:00Z" in enc.motivo
        ), enc.motivo
        assert "se trabó" not in enc.motivo
        assert "app.cli vivo" in enc.motivo
        assert not enc.impedimentos, enc.impedimentos
        assert relevos_de(enc, "responsable") == 0
        assert relevos_de(enc, "revisor") == 0
        libro = next((reg.base / "despliegues").rglob("ocupado.json"))
        datos = json.loads(libro.read_text())
        assert datos["intentos"] == 1
        # Vence la espera: el desenlace ocupado queda en el pasado y el mismo
        # SHA se reintenta solo hasta llegar a desplegado.
        datos["ultimo"] = "2026-11-05T11:00:00Z"
        libro.write_text(json.dumps(datos))
        final = enc
        for _ in range(10):
            final = ciclo._paso_despliegue(reg, reg.fold(ident), espacio, HORA)
            if final.nivel_alcanzado == "desplegado":
                break
        assert final.nivel_alcanzado == "desplegado", (
            final.nivel_alcanzado,
            final.motivo,
        )
        assert (destino / "pasos").read_text().splitlines() == [
            f"desplegar:{sha}", f"rollback:{sha}", f"desplegar:{sha}", f"checklist:{sha}",
        ]

    def test_75_repetido_agota_la_ventana_y_deja_impedimento_en_llano(self):
        despliegue = self.despliegue
        reg, ident, espacio, destino, integrar = despliegue
        (espacio / "d.sh").write_text('printf "ocupado: app.cli vivo (spapi-diario)\\n"; exit 75\n')
        integrar()
        self._ejecucion_en_proceso()
        for _ in range(6):
            ciclo._paso_despliegue(reg, reg.fold(ident), espacio, HORA)
            libro = next((reg.base / "despliegues").rglob("ocupado.json"), None)
            if libro is not None:
                datos = json.loads(libro.read_text())
                # Un día entero en el pasado: vence hasta la espera más larga
                # de la ventana creciente y deja que el siguiente reintento
                # corra en el mismo minuto de la prueba.
                datos["ultimo"] = "2026-11-04T11:00:00Z"
                libro.write_text(json.dumps(datos))
        enc = ciclo._paso_despliegue(reg, reg.fold(ident), espacio, HORA)
        assert enc.nivel_alcanzado == "integrado"
        causa = enc.impedimentos[-1].causa if enc.impedimentos else ""
        assert "ocupado 6 veces" in causa, causa
        assert "app.cli vivo (spapi-diario)" in causa
        assert "revisar el recibo" not in causa
        assert relevos_de(enc, "responsable") == 0
        assert relevos_de(enc, "revisor") == 0
        datos = recibo_final(reg)
        assert datos["estado"] == "ocupado", datos

    def test_sin_capacidad_espera_sin_consumir_revisor(self):  # no-roja-en-base: la base ya espera sin consumir revisor; la prueba migra del contador a relevos_de
        despliegue = self.despliegue
        reg, _, _, destino, integrar = despliegue
        integrar()
        (reg.base / "maquina.toml").write_text('nombre="sin ssh"\nconf="."\ntiene=[]\n')
        final = paso(despliegue)
        assert final.nivel_alcanzado == "integrado"
        assert final.estado == "esperando_reintento"
        assert "ssh-goncloud" in final.motivo
        assert relevos_de(final, "responsable") == 0
        assert not (destino / "pasos").exists()

    def test_pedido_ensayo_no_acredita_despliegue_sin_receta(self):
        despliegue = self.despliegue
        reg, _, espacio, destino, integrar = despliegue
        (espacio / ".motor.toml").unlink()
        integrar()
        interno = espacio / ".interno"
        interno.mkdir()
        (interno / "pedido-efecto.json").write_text(json.dumps({
            "id": "instalar", "operacion": "desplegar", "clave": "goncloud",
            "cuerpo": "versión integrada",
        }))
        final = paso(despliegue)
        assert final.nivel_alcanzado == "integrado"
        assert final.estado == "esperando_reintento"
        assert "receta" in final.motivo
        assert not (reg.base / "destino").exists()
        assert not (destino / "pasos").exists()

    def comprobar_corte(self, estado):
        despliegue = self.despliegue
        from motor.encargos import despliegue as ejecutor, politica_despliegue
        reg, ident, espacio, destino, integrar = despliegue
        integrar()
        paso(despliegue)
        enc = reg.fold(ident)
        recibo = Path(enc.fase_actual.desplegado_recibo)
        datos = json.loads(recibo.read_text())
        datos["estado"] = "ejecutando"
        recibo.write_text(json.dumps(datos))
        self.parche(plataforma, "estado_proc", lambda _: estado)
        antes = (destino / "pasos").read_text()
        resultado = ejecutor.iniciar(reg, enc, espacio, politica_despliegue.resolver(espacio, enc), HORA)
        assert resultado["estado"] == "incierto"
        assert (destino / "pasos").read_text() == antes

    def test_proceso_muerto_sin_recibo_final_queda_incierto_sin_repetir(self):
        self.comprobar_corte("muerto")

    def test_proceso_corte_sin_recibo_final_queda_incierto_sin_repetir(self):
        self.comprobar_corte("corte")

    def _ejecucion_en_proceso(self):
        from motor.encargos import despliegue as ejecutor

        def lanzar(cmd, cwd, env, log):
            ejecutor.ejecutar(Path(cmd[-1]))
            return {"pid": 99999999, "boot_id": plataforma.boot_id(), "inicio": ""}

        self.parche(plataforma, "lanzar_separado", lanzar)
        return ejecutor

    def test_timeout_mata_script_y_ejecuta_rollback(self):
        despliegue = self.despliegue
        reg, ident, espacio, destino, integrar = despliegue
        with (espacio / "d.sh").open("a") as f:
            f.write("exec sleep 20\n")
        integrar()
        ejecutor = self._ejecucion_en_proceso()
        self.parche(ejecutor, "TOPE_S", 0.2)
        # La prueba comprueba el camino 124 -> rollback, no la implementación
        # del Job de Windows del runner. El cierre determinista del proceso
        # aparta esa capacidad variable de la receta.
        def matar(proceso):
            try:
                proceso.kill()
            except OSError:
                pass
            try:
                proceso.wait(timeout=10)
            except subprocess.TimeoutExpired:
                pass

        self.parche(plataforma, "matar_grupo", matar)
        self.parche(plataforma, "presencia_grupo", lambda *a, **k: "ausente")
        final = paso(despliegue)
        assert final.nivel_alcanzado == "integrado"
        datos = recibo_final(reg)
        assert [p["codigo"] for p in datos["pasos"]] == [124, 0]
        assert (destino / "pasos").read_text().splitlines()[-1].startswith("rollback:")

    def test_el_cierre_transitorio_del_job_no_aborta_la_receta(self):
        # En Windows el Job responde acceso denegado mientras se cierra, y
        # `presencia_grupo` lo informa como «incierto». Ese cierre transitorio no
        # es un grupo vivo: si se toma por fallo, la receta se queda sin su
        # reversa y una parada espera para siempre.
        from motor.encargos import despliegue as ejecutor

        reg, ident, _, _, _ = self.despliegue
        copia = reg.base / "copia-falsa"
        copia.mkdir()
        (copia / "r.sh").write_text("exit 0\n", encoding="utf-8")

        class Proceso:
            pid = 424242
            returncode = 0

            def poll(self):
                return 0

            def wait(self, timeout=None):
                return 0

        class Creado:
            proceso = Proceso()
            job = "Local\\goncloud-motor-prueba"
            separacion = "sin_breakaway"

            def liberar(self):
                pass

        vistas = iter(["incierto", "incierto", "ausente"])
        self.parche(ejecutor, "_posix", lambda cmd, bash="": (["true"], True, None))
        self.parche(plataforma, "crear_proceso", lambda *a, **k: Creado())
        self.parche(plataforma, "hora_inicio", lambda pid: "100")
        self.parche(plataforma, "boot_id", lambda: "boot")
        self.parche(plataforma, "matar_grupo", lambda p: None)
        self.parche(plataforma, "matar_grupo_pid", lambda *a: None)
        self.parche(plataforma, "presencia_grupo", lambda *a, **k: next(vistas))
        try:
            proceso, enviado = ejecutor._correr(reg, ident, "rollback", "r.sh", copia, {}, "")
        except RuntimeError as exc:
            self.fail(f"un cierre transitorio del grupo no es un fallo: {exc}")
        self.assertTrue(enviado)
        self.assertEqual(proceso.returncode, 0)

    def test_un_grupo_que_no_confirma_su_salida_falla_cerrado(self):  # no-roja-en-base: caracterización del fallo cerrado; la base ya lanza al primer «vivo» y el arreglo conserva que no se dé por detenido
        # El reintento del cierre transitorio no puede volverse un «dar por
        # ausente»: si el grupo sigue vivo o no se puede mirar, _correr falla
        # cerrado y no encadena el paso siguiente.
        from motor.encargos import despliegue as ejecutor

        reg, ident, _, _, _ = self.despliegue
        copia = reg.base / "copia-falsa-falla"
        copia.mkdir()
        (copia / "r.sh").write_text("exit 0\n", encoding="utf-8")

        class Proceso:
            pid = 424243
            returncode = 0

            def poll(self):
                return 0

            def wait(self, timeout=None):
                return 0

        class Creado:
            proceso = Proceso()
            job = "Local\\goncloud-motor-prueba-falla"
            separacion = "sin_breakaway"

            def liberar(self):
                pass

        actual = {"valor": "vivo"}
        self.parche(ejecutor, "_posix", lambda cmd, bash="": (["true"], True, None))
        self.parche(plataforma, "crear_proceso", lambda *a, **k: Creado())
        self.parche(plataforma, "hora_inicio", lambda pid: "100")
        self.parche(plataforma, "boot_id", lambda: "boot")
        self.parche(plataforma, "matar_grupo", lambda p: None)
        self.parche(plataforma, "matar_grupo_pid", lambda *a: None)
        self.parche(plataforma, "presencia_grupo", lambda *a, **k: actual["valor"])
        for actual["valor"] in ("vivo", "incierto"):
            with self.subTest(presencia=actual["valor"]):
                with self.assertRaisesRegex(RuntimeError, "no confirma su salida"):
                    ejecutor._correr(reg, ident, "rollback", "r.sh", copia, {}, "")

    def test_parada_durante_preparacion_no_inicia_escritura(self):
        despliegue = self.despliegue
        reg, ident, espacio, destino, integrar = despliegue
        integrar()
        ejecutor = self._ejecucion_en_proceso()
        original = ejecutor._git

        def preparar(repo, *args):
            resultado = original(repo, *args)
            if args[:2] == ("worktree", "add"):
                ciclo.ordenar(reg, ident, "parar", "parar-preparacion", HORA)
            return resultado

        self.parche(ejecutor, "_git", preparar)
        paso(despliegue)
        # No hubo escritura que revertir: ni despliegue ni rollback deben arrancar.
        assert not (destino / "pasos").exists()

    def test_recibo_ensayo_previo_no_cierra_como_desplegado(self):
        despliegue = self.despliegue
        from motor.encargos.cierre import componer
        reg, ident, _, _, integrar = despliegue
        integrar()
        reg.anotar(ident, {"kind": "entrega_externa", "data": {
            "paso": "desplegado", "recibo": "dst_ensayo", "hora": HORA,
        }}, "despliegue-legado")
        with self.assertRaisesRegex(ValueError, "ensayo"):
            componer(reg.fold(ident), [], "aprobado")

    def test_recibo_sin_checklist_exitoso_no_acredita(self):
        despliegue = self.despliegue
        from motor.encargos.despliegue import comprobar_recibo
        _, _, _, _, integrar = despliegue
        integrar()
        final = paso(despliegue)
        recibo = Path(final.fase_actual.desplegado_recibo)
        datos = json.loads(recibo.read_text())
        datos["pasos"][1]["codigo"] = 1
        recibo.write_text(json.dumps(datos))
        with self.assertRaisesRegex(ValueError, "checklist"):
            comprobar_recibo(final)

    def test_una_violacion_transitoria_al_publicar_recibo_no_aborta_la_receta(self):
        despliegue = self.despliegue
        reg, ident, espacio, destino, integrar = despliegue
        integrar()
        real = os.replace
        faltan = {"n": 1}
        dentro = {"en_trabajador": False}

        def lanzar_en_proceso(cmd, cwd, env, log):
            dentro["en_trabajador"] = True
            try:
                from motor.encargos import despliegue as ejecutor

                ejecutor.ejecutar(Path(cmd[-1]))
            finally:
                dentro["en_trabajador"] = False
            return {"pid": 99999999, "boot_id": plataforma.boot_id(),
                    "inicio": "", "job": "", "separacion": False}

        self.parche(plataforma, "lanzar_separado", lanzar_en_proceso)

        def replace_con_violacion(src, dst, *args, **kwargs):
            # Windows: mientras un lector tiene el recibo abierto, el replace
            # del trabajador falla con PermissionError (violación compartida).
            if (dentro["en_trabajador"] and Path(dst).name == "recibo.json"
                    and faltan["n"] > 0):
                faltan["n"] -= 1
                raise PermissionError(13, "Permission denied")
            return real(src, dst, *args, **kwargs)

        self.parche(os, "replace", replace_con_violacion)
        final = paso(despliegue)
        recibos = list((reg.base / "despliegues").rglob("recibo.json"))
        assert len(recibos) == 1
        datos = json.loads(recibos[0].read_text())
        assert faltan["n"] == 0, "la violación inyectada no llegó a dispararse"
        assert datos["estado"] == "aplicado", datos
        assert [paso["codigo"] for paso in datos["pasos"]] == [0, 0]
        assert final.nivel_alcanzado == "desplegado", final.motivo

    def test_script_ausente_espera_sin_sello(self):
        despliegue = self.despliegue
        _, _, espacio, destino, integrar = despliegue
        (espacio / "c.sh").unlink()
        integrar()
        final = paso(despliegue)
        assert final.estado == "esperando_reintento"
        assert "c.sh" in final.motivo
        assert final.nivel_alcanzado == "integrado"
        assert not (destino / "pasos").exists()

    def test_parada_espera_al_rollback_y_no_sella(self):  # no-roja-en-base: amplía ventanas de espera del test (10->30->120 s y 5->15 s) por el coste del runner de Windows; el comportamiento afirmado es el de la base
        despliegue = self.despliegue
        from motor.encargos.despliegue import observacion
        reg, ident, espacio, destino, integrar = despliegue
        with (espacio / "d.sh").open("a") as f:
            f.write("sleep 20\n")
        liberar = destino / "liberar"
        with (espacio / "r.sh").open("a") as f:
            f.write(f'while [ ! -f "{liberar.as_posix()}" ]; do sleep 0.05; done\n')
        integrar()

        def esperar(condicion):
            # 120 s: la espera es de vivacidad (el estado se alcanza en
            # segundos sin carga), no de latencia. El camino del producto
            # tras la parada puede gastar ~6 s (sondeo + grupo muerto en
            # Windows) mas el arranque del bash de la reversa, y en un
            # runner cargado eso ya derroto ventanas de 10 y de 30 s.
            fin = time.monotonic() + 120
            while not condicion():
                assert time.monotonic() < fin
                time.sleep(0.05)

        try:
            inicio = time.monotonic()
            enc = ciclo._paso_despliegue(reg, reg.fold(ident), espacio, HORA)
            assert enc.nivel_alcanzado == "integrado"
            # 15 s: lo que se afirma es que el paso no bloquea los 20 s del
            # script; sin carga vuelve en ~1 s y en un runner cargado el
            # arranque del bash y del trabajador se comio el tope de 5 s.
            assert time.monotonic() - inicio < 15  # la receta larga no bloquea al supervisor
            esperar(lambda: (destino / "pasos").exists())
            ciclo.ordenar(reg, ident, "parar", "parar-durante-script", HORA)
            esperar(lambda: "rollback:" in (destino / "pasos").read_text())
            ciclo.barrer(reg, HORA, time.time(), None)
            assert reg.fold(ident).estado == "deteniendo"
            assert reg.fold(ident).nivel_alcanzado == "integrado"
        finally:
            liberar.touch()
            esperar(lambda: observacion(reg, reg.fold(ident)) == "ausente")
        ciclo.barrer(reg, HORA, time.time(), None)
        assert reg.fold(ident).estado == "detenido"
        assert "checklist:" not in (destino / "pasos").read_text()
