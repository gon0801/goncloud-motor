"""La observacion vigente manda: un script de `.interno/` que cambia no queda tapado.

El 12-nov, en `arreglos-01`, el comprobador corrigio solo `.interno/wsl-arreglos01-o.sh`
y volvio a observar en verde sobre la misma version, pero el motor siguio leyendo
el `observacion_supervisor` rojo guardado. La identidad de la observacion era la
version del producto mas el argv declarado: no veia el contenido del script que
el argv ejecuta. Estas pruebas fijan que la identidad incluye ese contenido, que
la observacion nueva del intento actual gana al evento viejo y que el verde
consumido en la entrega queda vigente para los intentos siguientes.
"""

from __future__ import annotations

import json
import os
import shlex
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from motor.encargos import comprobacion
from motor.encargos.aislamiento import ComandoAislado
from motor.encargos.ciclo import (
    _devolver,
    _espera_por_observacion,
    _paso_entrega,
    ahora_iso,
)
from motor.encargos.comprobacion import firma_producto
from motor.encargos.resultado import Espera
from tests.dobles import github_con_hallazgos
from tests.test_flujo_revisiones import (
    Recorrido,
    _entrega_pr,
    sonda_sin_aislamiento_del_host,
)


def setUpModule():
    sonda_sin_aislamiento_del_host()


def _sin_jaula(cmd, espacio, runtime, **kw):
    return ComandoAislado(list(cmd), dict(os.environ), "prueba")


def _declarar(r, argv: list[str]) -> list[dict]:
    interno = r.espacio / ".interno"
    interno.mkdir(exist_ok=True)
    declaradas = [{"objetivo": "O.2/O.3: el script del intento", "argv": argv}]
    (interno / "comprobaciones.json").write_text(
        json.dumps({"comprobaciones": declaradas}), encoding="utf-8"
    )
    return declaradas


def _observar(r) -> dict:
    """Corre las declaradas con el comprobador real sobre el espacio temporal."""
    comandos = comprobacion.leer_declaradas(comprobacion.interno_de(r.espacio))
    with mock.patch.object(comprobacion, "aislar", _sin_jaula):
        datos = comprobacion._ejecutar_declaradas(
            comandos, r.espacio, r.d, r.d / "comprobador-t"
        )
    (r.d / "observaciones.json").write_text(json.dumps(datos), encoding="utf-8")
    return datos


def _abiertos_del_supervisor(enc) -> list:
    return [
        h
        for h in (enc.fase_actual.hallazgos if enc.fase_actual else ())
        if h.origen == "supervisor" and h.estado == "abierto"
    ]


def _reobserva_al_cambiar(caso, clave, argv, ruta, inicial, final):
    """Abre el bloqueo con un rojo y comprueba que el script nuevo se vuelve a observar."""
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        r = Recorrido(tmp, clave, "crea saludo.txt")
        caso.addCleanup(r.reg.close)
        (r.espacio / "saludo.txt").write_text("hola\n", encoding="utf-8")
        script = r.espacio / ruta
        script.parent.mkdir(parents=True, exist_ok=True)
        script.write_text(inicial, encoding="utf-8")
        _declarar(r, argv)

        roja = _observar(r)
        caso.assertEqual(roja["ejecuciones"][0].get("rc"), 5, roja)
        r.incorporar()
        _espera_por_observacion(r.reg, r.fold(), r.espacio, r.d, ahora_iso())
        caso.assertEqual(
            len(_abiertos_del_supervisor(r.fold())),
            1,
            "el rc 5 abre el bloqueo del supervisor",
        )

        script.write_text(final, encoding="utf-8")
        with mock.patch(
            "motor.encargos.ciclo.iniciar_comprobacion"
        ) as iniciar:
            resultado = _espera_por_observacion(
                r.reg, r.fold(), r.espacio, r.d, ahora_iso()
            )
        iniciar.assert_called_once()
        caso.assertIsInstance(
            resultado[1] if resultado else None,
            Espera,
            "el cambio del script de .interno vuelve a observar antes de publicar",
        )


def _guion_que_se_modifica_no_acredita(caso, clave, argv):
    """Un guion que se reescribe a `exit 5` no acredita ese contenido nuevo."""
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        r = Recorrido(tmp, clave, "crea saludo.txt")
        caso.addCleanup(r.reg.close)
        (r.espacio / "saludo.txt").write_text("hola\n", encoding="utf-8")
        r.incorporar()
        script = r.espacio / ".interno" / "script.py"
        script.parent.mkdir(exist_ok=True)
        script.write_text(
            "from pathlib import Path\n"
            "Path(__file__).write_text('import sys; sys.exit(5)\\n')\n"
            "import sys\n"
            "sys.exit(0)\n",
            encoding="utf-8",
        )
        _declarar(r, argv)

        verde = _observar(r)
        caso.assertEqual(verde["ejecuciones"][0].get("rc"), 0, verde)
        caso.assertEqual(
            script.read_text(encoding="utf-8"),
            "import sys; sys.exit(5)\n",
            "el script dejo un contenido que sale 5",
        )

        with mock.patch(
            "motor.encargos.ciclo.iniciar_comprobacion"
        ) as iniciar:
            resultado = _espera_por_observacion(
                r.reg, r.fold(), r.espacio, r.d, ahora_iso()
            )
        iniciar.assert_called_once()
        caso.assertIsInstance(
            resultado[1] if resultado else None,
            Espera,
            "el verde no acredita un contenido que no corrio",
        )


class UnVerdeNuevoNoQuedaTapado(unittest.TestCase):
    def test_el_script_de_interno_cambia_la_observacion_y_cierra_el_bloqueo(self):
        """Cambiar solo el script reabre la observacion y cierra el rojo viejo.

        La prueba recorre el caso de `arreglos-01`: el script sale 5, el
        comprobador abre un bloqueo; se cambia solo el script a 0, el producto
        conserva su firma y el motor lee la observacion nueva, registra el
        verde y sigue la entrega sin `sin_progreso` por ese bloqueo.
        """
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            vuelta, r = _entrega_pr(self, tmp, "obs-vigente", github_con_hallazgos)
            self.addCleanup(r.reg.close)
            (r.espacio / "saludo.txt").write_text("hola\n", encoding="utf-8")
            r.incorporar()
            r.juicio({"veredicto": "aprobado", "evidencia": "lei saludo.txt"})
            r.incorporar()

            script = r.espacio / ".interno" / "script.py"
            script.parent.mkdir(exist_ok=True)
            argv = [sys.executable, ".interno/script.py"]
            _declarar(r, argv)
            version = firma_producto(r.espacio)

            script.write_text("import sys\nsys.exit(5)\n", encoding="utf-8")
            roja = _observar(r)
            self.assertEqual(roja["ejecuciones"][0].get("rc"), 5, roja)
            r.reg.anotar(
                r.enc,
                {"kind": "observacion_supervisor", "data": roja},
                f"t-obs-roja:{r.enc}:{version}",
            )

            _paso_entrega(r.reg, r.fold(), vuelta.gh, ahora_iso())
            e = r.fold()
            self.assertEqual(
                len(_abiertos_del_supervisor(e)),
                1,
                "el rc 5 de la comprobacion declarada abre su bloqueo",
            )
            self.assertEqual(
                e.producto.firma, version, "el rojo no cambia el producto"
            )

            script.write_text("import sys\nsys.exit(0)\n", encoding="utf-8")
            self.assertEqual(
                firma_producto(r.espacio),
                version,
                "el script vive en .interno y no cambia el producto",
            )
            verde = _observar(r)
            self.assertEqual(verde["ejecuciones"][0].get("rc"), 0, verde)
            antes = len(r.reg.eventos(r.enc))

            with mock.patch(
                "motor.encargos.ciclo.iniciar_comprobacion"
            ) as iniciar:
                _paso_entrega(r.reg, r.fold(), vuelta.gh, ahora_iso())
            iniciar.assert_not_called()

            e = r.fold()
            self.assertEqual(
                _abiertos_del_supervisor(e),
                [],
                "el verde del mismo comando cierra el bloqueo aunque cambiara "
                "solo el script de .interno",
            )
            nuevos = r.reg.eventos(r.enc)[antes:]
            self.assertFalse(
                any(ev["kind"] == "sin_progreso" for ev in nuevos),
                "un verde vigente no cuenta como una vuelta sin progreso",
            )
            self.assertFalse(
                any(ev["kind"] == "pendiente_entrega" for ev in nuevos),
                "un verde vigente no entrega la fase con pendientes",
            )
            self.assertTrue(
                any(
                    ev["kind"] == "entrega_externa"
                    and ev["data"].get("paso") in ("commit", "push")
                    for ev in nuevos
                ),
                "con el bloqueo cerrado la entrega sigue",
            )
            self.assertTrue(
                any(
                    ev["kind"] == "comprobaciones_supervisor"
                    and ev["data"].get("verdes")
                    for ev in nuevos
                ),
                "el verde queda registrado con su comprobacion en verdes",
            )

    def test_un_verde_del_intento_actual_gana_a_un_rojo_del_registro(self):
        """La observacion nueva del intento actual se lee antes que el evento viejo."""
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            r = Recorrido(tmp, "verde-gana", "crea saludo.txt")
            self.addCleanup(r.reg.close)
            (r.espacio / "saludo.txt").write_text("hola\n", encoding="utf-8")
            r.incorporar()
            declaradas = _declarar(
                r, [sys.executable, "-c", "import sys; sys.exit(1)"]
            )
            version = firma_producto(r.espacio)
            r.reg.anotar(
                r.enc,
                {
                    "kind": "observacion_supervisor",
                    "data": {
                        "version": version,
                        "comprobaciones": declaradas,
                        "ejecuciones": [{**declaradas[0], "rc": 1, "stderr": "viejo"}],
                        "hora": ahora_iso(),
                    },
                },
                f"t-rojo:{r.enc}:{version}",
            )
            (r.d / "observaciones.json").write_text(
                json.dumps(
                    {
                        "version": version,
                        "producto": version,
                        "comprobaciones": declaradas,
                        "ejecuciones": [{**declaradas[0], "rc": 0, "stdout": "OK"}],
                    }
                ),
                encoding="utf-8",
            )
            e = r.fold()

            resultado = _espera_por_observacion(
                r.reg, e, r.espacio, r.d, ahora_iso()
            )

            self.assertIsNone(
                resultado,
                "el verde nuevo del intento actual gana al rojo guardado en el "
                "registro; no vuelve a devolver la comprobacion vieja",
            )

    def test_el_mismo_script_se_reusa_y_un_script_nuevo_vuelve_a_observar(self):
        """La identidad del procedimiento distingue el script y conserva el reuso.

        Con el mismo comando y el mismo script, la observacion guardada se reusa
        sin volver a correr. Si cambia el contenido del script de `.interno/`,
        la observacion vieja ya no corresponde y el comprobador vuelve a correr.
        """
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            r = Recorrido(tmp, "reuso-script", "crea saludo.txt")
            self.addCleanup(r.reg.close)
            (r.espacio / "saludo.txt").write_text("hola\n", encoding="utf-8")
            script = r.espacio / ".interno" / "script.py"
            script.parent.mkdir(exist_ok=True)
            script.write_text("print('ok')\n", encoding="utf-8")
            _declarar(r, [sys.executable, ".interno/script.py"])

            verde = _observar(r)
            self.assertEqual(verde["ejecuciones"][0].get("rc"), 0, verde)
            r.incorporar()
            e = r.fold()
            (r.d / "observaciones.json").unlink()

            with mock.patch(
                "motor.encargos.ciclo.iniciar_comprobacion"
            ) as iniciar:
                resultado = _espera_por_observacion(
                    r.reg, e, r.espacio, r.d, ahora_iso()
                )
            iniciar.assert_not_called()
            self.assertIsNone(
                resultado,
                "misma version, mismo comando y mismo script reusan la "
                "observacion sin volver a correr",
            )

            script.write_text("print('otro contenido')\n", encoding="utf-8")
            with mock.patch(
                "motor.encargos.ciclo.iniciar_comprobacion"
            ) as iniciar:
                resultado = _espera_por_observacion(
                    r.reg, r.fold(), r.espacio, r.d, ahora_iso()
                )
            iniciar.assert_called_once()
            self.assertIsInstance(
                resultado[1] if resultado else None,
                Espera,
                "un script nuevo no reusa la observacion vieja: vuelve a "
                "observar antes de publicar",
            )

    def test_el_verde_de_la_misma_identidad_queda_vigente_entre_intentos(self):
        """Un verde nuevo se registra aunque la identidad reutilizable no cambie.

        La version, las declaradas y el script son los mismos; solo aparece
        `.interno/ok`, que el script consulta. El resultado pasa de rc 5 a rc 0
        y la observacion nueva debe quedar en el registro. Desde un intento
        nuevo, sin `observaciones.json`, el motor lee ese verde, no el rojo
        guardado, y no vuelve a alimentar la escalera.
        """
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            r = Recorrido(tmp, "verde-misma-identidad", "crea saludo.txt")
            self.addCleanup(r.reg.close)
            (r.espacio / "saludo.txt").write_text("hola\n", encoding="utf-8")

            script = r.espacio / ".interno" / "script.py"
            script.parent.mkdir(exist_ok=True)
            script.write_text(
                "from pathlib import Path\n"
                "import sys\n"
                "sys.exit(0 if Path('.interno/ok').exists() else 5)\n",
                encoding="utf-8",
            )
            _declarar(r, [sys.executable, ".interno/script.py"])
            version = firma_producto(r.espacio)

            roja = _observar(r)
            self.assertEqual(roja["ejecuciones"][0].get("rc"), 5, roja)
            r.incorporar()
            _espera_por_observacion(r.reg, r.fold(), r.espacio, r.d, ahora_iso())
            self.assertEqual(
                len(_abiertos_del_supervisor(r.fold())),
                1,
                "el rc 5 abre el bloqueo del supervisor",
            )

            (r.espacio / ".interno" / "ok").write_text("", encoding="utf-8")
            self.assertEqual(
                firma_producto(r.espacio),
                version,
                "el archivo que consulta el script vive en .interno y no cambia "
                "el producto",
            )
            verde = _observar(r)
            self.assertEqual(verde["ejecuciones"][0].get("rc"), 0, verde)
            r.incorporar()

            observaciones = [
                ev
                for ev in r.reg.eventos(r.enc)
                if ev["kind"] == "observacion_supervisor"
                and ev["data"].get("version") == version
            ]
            self.assertEqual(
                observaciones[-1]["data"]["ejecuciones"][0].get("rc"),
                0,
                "el verde de la misma identidad se registra y queda vigente; el "
                "rojo viejo no lo descarta",
            )

            nuevo = r.reg.base / "ejecucion" / r.enc / "intento-nuevo"
            nuevo.mkdir()
            antes = len(r.reg.eventos(r.enc))
            with mock.patch(
                "motor.encargos.ciclo.iniciar_comprobacion"
            ) as iniciar:
                resultado = _espera_por_observacion(
                    r.reg, r.fold(), r.espacio, nuevo, ahora_iso()
                )
            iniciar.assert_not_called()
            self.assertIsNone(
                resultado,
                "desde un intento nuevo sin observaciones.json gana el verde "
                "registrado, no el rojo viejo",
            )
            e = r.fold()
            self.assertEqual(_abiertos_del_supervisor(e), [], "el bloqueo se cierra")
            nuevos = r.reg.eventos(r.enc)[antes:]
            self.assertFalse(
                any(ev["kind"] == "sin_progreso" for ev in nuevos),
                "el verde vigente no vuelve a alimentar la escalera",
            )

    def test_el_verde_consumido_en_la_entrega_queda_vigente(self):
        """El verde que la entrega consume sin incorporar tambien se registra.

        `_espera_por_observacion` cierra el bloqueo con el archivo del intento,
        pero si no guarda la observacion, el siguiente intento vuelve al rojo
        del registro. Aqui la entrega consume el verde y un intento nuevo lo lee.
        """
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            vuelta, r = _entrega_pr(self, tmp, "verde-entrega", github_con_hallazgos)
            self.addCleanup(r.reg.close)
            (r.espacio / "saludo.txt").write_text("hola\n", encoding="utf-8")
            r.incorporar()
            r.juicio({"veredicto": "aprobado", "evidencia": "lei saludo.txt"})
            r.incorporar()

            script = r.espacio / ".interno" / "script.py"
            script.parent.mkdir(exist_ok=True)
            script.write_text(
                "from pathlib import Path\n"
                "import sys\n"
                "sys.exit(0 if Path('.interno/ok').exists() else 5)\n",
                encoding="utf-8",
            )
            _declarar(r, [sys.executable, ".interno/script.py"])
            version = firma_producto(r.espacio)

            roja = _observar(r)
            self.assertEqual(roja["ejecuciones"][0].get("rc"), 5, roja)
            r.incorporar()
            _paso_entrega(r.reg, r.fold(), vuelta.gh, ahora_iso())
            self.assertEqual(
                len(_abiertos_del_supervisor(r.fold())),
                1,
                "el rc 5 abre el bloqueo del supervisor",
            )

            (r.espacio / ".interno" / "ok").write_text("", encoding="utf-8")
            verde = _observar(r)
            self.assertEqual(verde["ejecuciones"][0].get("rc"), 0, verde)
            antes = len(r.reg.eventos(r.enc))
            _paso_entrega(r.reg, r.fold(), vuelta.gh, ahora_iso())
            self.assertEqual(
                _abiertos_del_supervisor(r.fold()),
                [],
                "el verde consumido en la entrega cierra el bloqueo",
            )

            observaciones = [
                ev
                for ev in r.reg.eventos(r.enc)
                if ev["kind"] == "observacion_supervisor"
                and ev["data"].get("version") == version
            ]
            self.assertTrue(
                observaciones,
                "la entrega registra la observacion que consumio",
            )
            self.assertEqual(
                observaciones[-1]["data"]["ejecuciones"][0].get("rc"),
                0,
                "el verde consumido en la entrega queda registrado",
            )

            nuevo = r.reg.base / "ejecucion" / r.enc / "intento-nuevo"
            nuevo.mkdir()
            with mock.patch(
                "motor.encargos.ciclo.iniciar_comprobacion"
            ) as iniciar:
                resultado = _espera_por_observacion(
                    r.reg, r.fold(), r.espacio, nuevo, ahora_iso()
                )
            iniciar.assert_not_called()
            self.assertIsNone(
                resultado,
                "desde un intento nuevo gana el verde consumido, no el rojo viejo",
            )
            nuevos = r.reg.eventos(r.enc)[antes:]
            self.assertFalse(
                any(ev["kind"] == "sin_progreso" for ev in nuevos),
                "el verde consumido no vuelve a alimentar la escalera",
            )

    def test_un_script_que_se_modifica_al_correr_no_acredita_su_contenido_nuevo(self):
        """Un verde no se guarda con el contenido que el script dejo despues.

        El script se reescribe a un contenido que sale 5 y termina su primera
        corrida en 0. La huella guardada es la del contenido que corrio, no la
        del que quedo; por eso el motor vuelve a observar y no acepta el verde.
        """
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            r = Recorrido(tmp, "auto-modifica", "crea saludo.txt")
            self.addCleanup(r.reg.close)
            (r.espacio / "saludo.txt").write_text("hola\n", encoding="utf-8")
            r.incorporar()

            script = r.espacio / ".interno" / "script.py"
            script.parent.mkdir(exist_ok=True)
            script.write_text(
                "from pathlib import Path\n"
                "Path(__file__).write_text('import sys; sys.exit(5)\\n')\n"
                "import sys\n"
                "sys.exit(0)\n",
                encoding="utf-8",
            )
            _declarar(r, [sys.executable, ".interno/script.py"])

            verde = _observar(r)
            self.assertEqual(verde["ejecuciones"][0].get("rc"), 0, verde)
            self.assertEqual(
                script.read_text(encoding="utf-8"),
                "import sys; sys.exit(5)\n",
                "el script dejo un contenido que sale 5",
            )

            with mock.patch(
                "motor.encargos.ciclo.iniciar_comprobacion"
            ) as iniciar:
                resultado = _espera_por_observacion(
                    r.reg, r.fold(), r.espacio, r.d, ahora_iso()
                )
            iniciar.assert_called_once()
            self.assertIsInstance(
                resultado[1] if resultado else None,
                Espera,
                "el verde no acredita un contenido que no corrio",
            )


    def test_un_script_que_se_modifica_dentro_de_sh_c_no_acredita_su_contenido(self):
        """El interprete dentro de un `sh -c` tambien se clasifica como ejecutado."""
        _guion_que_se_modifica_no_acredita(
            self,
            "auto-modifica-sh-c",
            ["/bin/sh", "-c", shlex.join([sys.executable, ".interno/script.py"])],
        )

    def test_un_script_que_se_modifica_tras_env_no_acredita_su_contenido(self):
        """El interprete detras de `env` tambien se clasifica como ejecutado."""
        env = shutil.which("env")
        if env is None:
            self.skipTest("no hay env en esta plataforma")
        _guion_que_se_modifica_no_acredita(
            self,
            "auto-modifica-env",
            [env, sys.executable, ".interno/script.py"],
        )

    def test_un_script_bajo_runtime_nombrado_en_argv_pesa_y_reobserva(self):
        """Una ruta de `.interno/.runtime/` nombrada por el argv se pesa igual."""
        _reobserva_al_cambiar(
            self,
            "runtime-nombrado",
            [sys.executable, ".interno/.runtime/script.py"],
            ".interno/.runtime/script.py",
            "import sys\nsys.exit(5)\n",
            "import sys\nsys.exit(0)\n",
        )

    def test_un_script_nombrado_dentro_de_sh_c_cambia_la_observacion(self):
        """La ruta dentro de `sh -c` cuenta aunque comparta token con el comando.

        `['/bin/sh', '-c', '. .interno/script.sh']` ejecuta el script. Cambiar
        solo su contenido debe cambiar la huella del procedimiento y obligar a
        observar de nuevo, igual que cuando la ruta es un argumento aparte.
        """
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            r = Recorrido(tmp, "sh-c-interno", "crea saludo.txt")
            self.addCleanup(r.reg.close)
            (r.espacio / "saludo.txt").write_text("hola\n", encoding="utf-8")

            script = r.espacio / ".interno" / "script.sh"
            script.parent.mkdir(exist_ok=True)
            script.write_text("exit 5\n", encoding="utf-8")
            _declarar(r, ["/bin/sh", "-c", ". .interno/script.sh"])

            roja = _observar(r)
            self.assertEqual(roja["ejecuciones"][0].get("rc"), 5, roja)
            r.incorporar()
            _espera_por_observacion(r.reg, r.fold(), r.espacio, r.d, ahora_iso())

            script.write_text("exit 0\n", encoding="utf-8")

            with mock.patch(
                "motor.encargos.ciclo.iniciar_comprobacion"
            ) as iniciar:
                resultado = _espera_por_observacion(
                    r.reg, r.fold(), r.espacio, r.d, ahora_iso()
                )
            iniciar.assert_called_once()
            self.assertIsInstance(
                resultado[1] if resultado else None,
                Espera,
                "el script nuevo nombrado dentro de sh -c vuelve a observar antes "
                "de publicar",
            )

    def test_un_script_de_sh_c_con_argumentos_vuelve_a_observar(self):
        """El script de un `sh -c` con argumentos pesa sin tragarse la linea."""
        _reobserva_al_cambiar(
            self,
            "sh-c-flag",
            ["/bin/sh", "-c", ". .interno/script.sh --rapido"],
            ".interno/script.sh",
            "exit 5\n",
            "exit 0\n",
        )

    def test_una_ruta_al_inicio_de_una_linea_multilinea_vuelve_a_observar(self):
        """Un salto de línea también separa el componente `.interno`."""
        _reobserva_al_cambiar(
            self,
            "sh-c-multilinea",
            ["/bin/sh", "-c", "set -e\n. .interno/script.sh"],
            ".interno/script.sh",
            "exit 5\n",
            "exit 0\n",
        )

    def test_una_ruta_con_espacios_citada_en_sh_c_vuelve_a_observar(self):
        """Las comillas de la línea de shell agrupan la ruta con espacios."""
        _reobserva_al_cambiar(
            self,
            "sh-c-cita",
            ["/bin/sh", "-c", '. ".interno/mis pruebas/esperado.txt"'],
            ".interno/mis pruebas/esperado.txt",
            "exit 5\n",
            "exit 0\n",
        )

    def test_una_ruta_con_espacios_en_un_argumento_vuelve_a_observar(self):
        """Un argumento de ruta conserva sus espacios; no se corta en el primero."""
        _reobserva_al_cambiar(
            self,
            "ruta-espacios",
            [sys.executable, ".interno/mis pruebas/run.py"],
            ".interno/mis pruebas/run.py",
            "import sys\nsys.exit(5)\n",
            "import sys\nsys.exit(0)\n",
        )

    def test_una_asignacion_de_entorno_pesa_el_directorio_de_interno(self):
        """`PATH=.interno/bin:$PATH` pesa `.interno/bin`, no el valor del PATH."""
        _reobserva_al_cambiar(
            self,
            "sh-c-path",
            ["/bin/sh", "-c", "PATH=.interno/bin:$PATH exit 5"],
            ".interno/bin/run.sh",
            "exit 5\n",
            "exit 0\n",
        )

    def test_un_delimitador_pegado_a_la_ruta_vuelve_a_observar(self):
        """Un `;` pegado al nombre del script no forma parte de la ruta."""
        _reobserva_al_cambiar(
            self,
            "sh-c-pegado",
            ["/bin/sh", "-c", ". .interno/a.sh;"],
            ".interno/a.sh",
            "exit 5\n",
            "exit 0\n",
        )

    def test_un_nombre_citado_con_metacaracter_vuelve_a_observar(self):
        """Las comillas protegen un nombre que contiene `;`: no se trunca."""
        _reobserva_al_cambiar(
            self,
            "sh-c-cita-meta",
            ["/bin/sh", "-c", '. ".interno/a;b.sh"'],
            ".interno/a;b.sh",
            "exit 5\n",
            "exit 0\n",
        )

    def test_un_guion_que_escribe_su_log_converge(self):  # no-roja-en-base: la base no pesa el contenido de .interno; la prueba fija que el log posterior no deja la observacion sin corresponder
        """El log que el guion escribe no rompe la correspondencia de la observacion."""
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            r = Recorrido(tmp, "guion-con-log", "crea saludo.txt")
            self.addCleanup(r.reg.close)
            (r.espacio / "saludo.txt").write_text("hola\n", encoding="utf-8")
            r.incorporar()
            script = r.espacio / ".interno" / "script.py"
            script.parent.mkdir(exist_ok=True)
            script.write_text(
                "import sys\n"
                "from pathlib import Path\n"
                "Path(sys.argv[1]).write_text('hecho\\n')\n"
                "sys.exit(0)\n",
                encoding="utf-8",
            )
            _declarar(
                r,
                [sys.executable, ".interno/script.py", ".interno/log.txt"],
            )

            verde = _observar(r)
            self.assertEqual(verde["ejecuciones"][0].get("rc"), 0, verde)
            self.assertTrue((r.espacio / ".interno" / "log.txt").is_file())
            r.incorporar()

            with mock.patch(
                "motor.encargos.ciclo.iniciar_comprobacion"
            ) as iniciar:
                resultado = _espera_por_observacion(
                    r.reg, r.fold(), r.espacio, r.d, ahora_iso()
                )
            iniciar.assert_not_called()
            self.assertIsNone(
                resultado,
                "el log que el guion escribe no deja la observacion sin corresponder",
            )

    def test_un_arbol_que_genera_cache_no_se_reobserva(self):  # no-roja-en-base: la base no pesa el arbol de .interno; la prueba fija que las caches y las salidas no rompen el reuso
        """Una comprobacion que deja `__pycache__` y una salida no se reobserva."""
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            r = Recorrido(tmp, "arbol-cache", "crea saludo.txt")
            self.addCleanup(r.reg.close)
            (r.espacio / "saludo.txt").write_text("hola\n", encoding="utf-8")
            r.incorporar()
            carpeta = r.espacio / ".interno" / "pruebas"
            carpeta.mkdir(parents=True, exist_ok=True)
            _declarar(
                r,
                [
                    "/bin/sh",
                    "-c",
                    "cd .interno/pruebas && mkdir -p __pycache__ && "
                    "echo x > __pycache__/x.pyc && echo y > generado.txt && exit 0",
                ],
            )

            verde = _observar(r)
            self.assertEqual(verde["ejecuciones"][0].get("rc"), 0, verde)
            self.assertTrue((carpeta / "__pycache__").is_dir())
            r.incorporar()

            with mock.patch(
                "motor.encargos.ciclo.iniciar_comprobacion"
            ) as iniciar:
                resultado = _espera_por_observacion(
                    r.reg, r.fold(), r.espacio, r.d, ahora_iso()
                )
            iniciar.assert_not_called()
            self.assertIsNone(
                resultado,
                "una cache y una salida bajo el arbol no obligan a reobservar",
            )

    def test_un_guion_ilegible_no_tumba_la_observacion(self):
        """Una lectura ilegible deja marca y no impide volver a observar."""
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            r = Recorrido(tmp, "guion-ilegible", "crea saludo.txt")
            self.addCleanup(r.reg.close)
            (r.espacio / "saludo.txt").write_text("hola\n", encoding="utf-8")
            r.incorporar()
            script = r.espacio / ".interno" / "script.py"
            script.parent.mkdir(exist_ok=True)
            script.write_text("import sys\nsys.exit(0)\n", encoding="utf-8")
            _declarar(r, [sys.executable, ".interno/script.py"])

            real = Path.read_bytes

            def falla(path):
                if str(path).endswith("script.py"):
                    raise PermissionError("sin permiso")
                return real(path)

            with mock.patch.object(Path, "read_bytes", falla):
                verde = _observar(r)
            self.assertEqual(verde["ejecuciones"][0].get("rc"), 0, verde)

            with mock.patch(
                "motor.encargos.ciclo.iniciar_comprobacion"
            ) as iniciar:
                resultado = _espera_por_observacion(
                    r.reg, r.fold(), r.espacio, r.d, ahora_iso()
                )
            iniciar.assert_called_once()
            self.assertIsInstance(
                resultado[1] if resultado else None,
                Espera,
                "la huella ilegible no se confunde con el contenido legible",
            )

    def test_un_enlace_que_sale_de_interno_no_entra_en_la_identidad(self):  # no-roja-en-base: la base no pesa .interno; la prueba fija que un enlace que sale del arbol no se cuenta
        """Un enlace que sale de `.interno/` no pesa en la identidad."""
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            r = Recorrido(tmp, "enlace-fuera", "crea saludo.txt")
            self.addCleanup(r.reg.close)
            (r.espacio / "saludo.txt").write_text("hola\n", encoding="utf-8")
            (r.espacio / ".runtime").mkdir(exist_ok=True)
            fuera = r.espacio / ".runtime" / "guion.py"
            fuera.write_text("import sys\nsys.exit(5)\n", encoding="utf-8")
            interno = r.espacio / ".interno"
            interno.mkdir(exist_ok=True)
            enlace = interno / "script.py"
            try:
                enlace.symlink_to(fuera)
            except (OSError, NotImplementedError):
                self.skipTest("la plataforma no permite crear enlaces simbólicos")
            _declarar(r, [sys.executable, ".interno/script.py"])

            roja = _observar(r)
            self.assertEqual(roja["ejecuciones"][0].get("rc"), 5, roja)
            r.incorporar()
            _espera_por_observacion(r.reg, r.fold(), r.espacio, r.d, ahora_iso())

            fuera.write_text("import sys\nsys.exit(0)\n", encoding="utf-8")
            with mock.patch(
                "motor.encargos.ciclo.iniciar_comprobacion"
            ) as iniciar:
                resultado = _espera_por_observacion(
                    r.reg, r.fold(), r.espacio, r.d, ahora_iso()
                )
            iniciar.assert_not_called()
            self.assertIsNotNone(
                resultado,
                "el contenido fuera de .interno no entra en la identidad",
            )

    def test_una_linea_sh_c_mal_citada_no_tumba_la_observacion(self):  # no-roja-en-base: la base no parsea la linea de shell; la prueba fija que la huella nueva no levanta
        """Una comilla sin cerrar en `sh -c` no impide observar."""
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            r = Recorrido(tmp, "sh-c-mal-citada", "crea saludo.txt")
            self.addCleanup(r.reg.close)
            (r.espacio / "saludo.txt").write_text("hola\n", encoding="utf-8")
            r.incorporar()
            script = r.espacio / ".interno" / "script.sh"
            script.parent.mkdir(exist_ok=True)
            script.write_text("exit 0\n", encoding="utf-8")
            _declarar(r, ["/bin/sh", "-c", "echo '.interno/script.sh"])

            datos = _observar(r)
            self.assertIsNotNone(
                datos["ejecuciones"][0].get("rc"),
                "la linea mal citada se ejecuta o falla, pero no tumba la observacion",
            )

    def test_un_rojo_que_vuelve_gana_al_verde_anterior(self):  # no-roja-en-base: la clave con el resultado nace en la primera vuelta; en 5e83c75 el verde ni se registra y el rojo que vuelve no queda tapado por ese camino
        """El rojo verdadero sigue rojo aunque antes haya habido un verde.

        Rojo A, verde B y otra vez rojo A: el último resultado es el vigente.
        La clave del evento no puede colapsar el rojo que vuelve con el rojo
        anterior y dejar el verde en el medio.
        """
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            r = Recorrido(tmp, "rojo-vuelve", "crea saludo.txt")
            self.addCleanup(r.reg.close)
            (r.espacio / "saludo.txt").write_text("hola\n", encoding="utf-8")

            script = r.espacio / ".interno" / "script.py"
            script.parent.mkdir(exist_ok=True)
            script.write_text(
                "from pathlib import Path\n"
                "import sys\n"
                "sys.exit(0 if Path('.interno/ok').exists() else 5)\n",
                encoding="utf-8",
            )
            _declarar(r, [sys.executable, ".interno/script.py"])
            version = firma_producto(r.espacio)

            def observar_y_registrar() -> dict:
                datos = _observar(r)
                r.incorporar()
                return datos

            roja = observar_y_registrar()
            self.assertEqual(roja["ejecuciones"][0].get("rc"), 5, roja)
            _espera_por_observacion(r.reg, r.fold(), r.espacio, r.d, ahora_iso())

            (r.espacio / ".interno" / "ok").write_text("", encoding="utf-8")
            verde = observar_y_registrar()
            self.assertEqual(verde["ejecuciones"][0].get("rc"), 0, verde)
            _espera_por_observacion(r.reg, r.fold(), r.espacio, r.d, ahora_iso())

            _devolver(
                r.reg,
                r.fold(),
                "investigar",
                "el revisor devuelve el trabajo",
                "corregir",
                ahora_iso(),
                "test-rechazo",
            )
            (r.espacio / ".interno" / "ok").unlink()
            vuelve_roja = observar_y_registrar()
            self.assertEqual(vuelve_roja["ejecuciones"][0].get("rc"), 5, vuelve_roja)

            observaciones = [
                ev
                for ev in r.reg.eventos(r.enc)
                if ev["kind"] == "observacion_supervisor"
                and ev["data"].get("version") == version
            ]
            self.assertEqual(
                observaciones[-1]["data"]["ejecuciones"][0].get("rc"),
                5,
                "el rojo que vuelve queda vigente; el verde anterior no lo tapa",
            )

            nuevo = r.reg.base / "ejecucion" / r.enc / "intento-nuevo"
            nuevo.mkdir()
            resultado = _espera_por_observacion(
                r.reg, r.fold(), r.espacio, nuevo, ahora_iso()
            )
            self.assertIsNotNone(
                resultado,
                "el rojo vigente se devuelve aunque un verde anterior exista",
            )
            self.assertEqual(
                len(_abiertos_del_supervisor(r.fold())),
                1,
                "el rojo que vuelve deja su bloqueo abierto",
            )


if __name__ == "__main__":
    unittest.main()
