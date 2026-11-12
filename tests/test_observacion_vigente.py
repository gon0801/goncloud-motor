"""La observacion vigente manda: un script de `.interno/` que cambia no queda tapado.

El 12-nov, en `arreglos-01`, el comprobador corrigio solo `.interno/wsl-arreglos01-o.sh`
y volvio a observar en verde sobre la misma version, pero el motor siguio leyendo
el `observacion_supervisor` rojo guardado. La identidad de la observacion era la
version del producto mas el argv declarado: no veia el contenido del script que
el argv ejecuta. Estas pruebas fijan que la identidad incluye ese contenido y
que la observacion nueva del intento actual gana al evento viejo.
"""

from __future__ import annotations

import json
import os
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

    def test_un_script_de_sh_c_con_argumentos_pesa_la_ruta_sin_los_argumentos(self):
        """La ruta de un `sh -c` con argumentos se corta en el espacio, no se traga la cola."""
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            r = Recorrido(tmp, "sh-c-flag", "crea saludo.txt")
            self.addCleanup(r.reg.close)
            interno = comprobacion.interno_de(r.espacio, crear=True)
            script = interno / "script.sh"
            script.write_text("exit 5\n", encoding="utf-8")
            declaradas = [
                {"objetivo": "x", "argv": ["/bin/sh", "-c", ".interno/script.sh --rapido"]}
            ]

            antes = comprobacion.huella_procedimiento(r.espacio, declaradas)
            script.write_text("exit 0\n", encoding="utf-8")
            despues = comprobacion.huella_procedimiento(r.espacio, declaradas)

            self.assertIsNotNone(antes)
            self.assertNotEqual(
                antes,
                despues,
                "la ruta del script pesa; los argumentos de la linea no se "
                "confunden con la ruta",
            )

    def test_una_ruta_al_inicio_de_una_linea_multilinea_pesa(self):
        """Un salto de línea también separa el componente `.interno`."""
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            r = Recorrido(tmp, "sh-c-multilinea", "crea saludo.txt")
            self.addCleanup(r.reg.close)
            interno = comprobacion.interno_de(r.espacio, crear=True)
            script = interno / "script.sh"
            script.write_text("exit 5\n", encoding="utf-8")
            declaradas = [
                {
                    "objetivo": "x",
                    "argv": ["/bin/sh", "-c", "set -e\n.interno/script.sh --rapido"],
                }
            ]

            antes = comprobacion.huella_procedimiento(r.espacio, declaradas)
            script.write_text("exit 0\n", encoding="utf-8")
            despues = comprobacion.huella_procedimiento(r.espacio, declaradas)

            self.assertIsNotNone(antes)
            self.assertNotEqual(
                antes,
                despues,
                "la ruta tras un salto de linea de shell pesa entera",
            )

    def test_una_bandera_c_que_no_es_de_shell_no_corta_la_ruta(self):
        """`diff -c` no convierte su argumento en una línea de shell."""
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            r = Recorrido(tmp, "diff-c", "crea saludo.txt")
            self.addCleanup(r.reg.close)
            interno = comprobacion.interno_de(r.espacio, crear=True)
            carpeta = interno / "mis pruebas"
            carpeta.mkdir()
            esperado = carpeta / "esperado.txt"
            esperado.write_text("uno\n", encoding="utf-8")
            declaradas = [
                {"objetivo": "x", "argv": ["diff", "-c", ".interno/mis pruebas/esperado.txt"]}
            ]

            antes = comprobacion.huella_procedimiento(r.espacio, declaradas)
            esperado.write_text("dos\n", encoding="utf-8")
            despues = comprobacion.huella_procedimiento(r.espacio, declaradas)

            self.assertIsNotNone(antes)
            self.assertNotEqual(
                antes,
                despues,
                "la ruta con espacios pesa entera aunque la bandera sea -c",
            )

    def test_una_ruta_con_espacios_citada_en_sh_c_pesa_entera(self):
        """Las comillas de la línea de shell agrupan la ruta con espacios."""
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            r = Recorrido(tmp, "sh-c-cita", "crea saludo.txt")
            self.addCleanup(r.reg.close)
            interno = comprobacion.interno_de(r.espacio, crear=True)
            carpeta = interno / "mis pruebas"
            carpeta.mkdir()
            esperado = carpeta / "esperado.txt"
            esperado.write_text("uno\n", encoding="utf-8")
            declaradas = [
                {
                    "objetivo": "x",
                    "argv": ["/bin/sh", "-c", 'cat ".interno/mis pruebas/esperado.txt"'],
                }
            ]

            antes = comprobacion.huella_procedimiento(r.espacio, declaradas)
            esperado.write_text("dos\n", encoding="utf-8")
            despues = comprobacion.huella_procedimiento(r.espacio, declaradas)

            self.assertIsNotNone(antes)
            self.assertNotEqual(
                antes,
                despues,
                "la ruta citada con espacios pesa entera",
            )

    def test_una_asignacion_de_entorno_corta_en_los_dos_puntos(self):
        """`PATH=.interno/bin:$PATH` pesa `.interno/bin`, no el valor del PATH."""
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            r = Recorrido(tmp, "sh-c-path", "crea saludo.txt")
            self.addCleanup(r.reg.close)
            interno = comprobacion.interno_de(r.espacio, crear=True)
            bin_dir = interno / "bin"
            bin_dir.mkdir()
            herramienta = bin_dir / "run.sh"
            herramienta.write_text("exit 5\n", encoding="utf-8")
            declaradas = [
                {
                    "objetivo": "x",
                    "argv": ["/bin/sh", "-c", "PATH=.interno/bin:$PATH exit 0"],
                }
            ]

            antes = comprobacion.huella_procedimiento(r.espacio, declaradas)
            herramienta.write_text("exit 0\n", encoding="utf-8")
            despues = comprobacion.huella_procedimiento(r.espacio, declaradas)

            self.assertIsNotNone(antes)
            self.assertNotEqual(
                antes,
                despues,
                "la asignacion de PATH pesa el directorio de .interno",
            )

    def test_un_delimitador_pegado_a_la_ruta_no_entra_en_la_huella(self):
        """Un `;` pegado al nombre del script no forma parte de la ruta."""
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            r = Recorrido(tmp, "sh-c-pegado", "crea saludo.txt")
            self.addCleanup(r.reg.close)
            interno = comprobacion.interno_de(r.espacio, crear=True)
            script = interno / "a.sh"
            script.write_text("exit 5\n", encoding="utf-8")
            declaradas = [
                {"objetivo": "x", "argv": ["/bin/sh", "-c", "cat .interno/a.sh;exit 0"]}
            ]

            antes = comprobacion.huella_procedimiento(r.espacio, declaradas)
            script.write_text("exit 0\n", encoding="utf-8")
            despues = comprobacion.huella_procedimiento(r.espacio, declaradas)

            self.assertIsNotNone(antes)
            self.assertNotEqual(
                antes,
                despues,
                "el delimitador pegado no se confunde con la ruta",
            )

    def test_un_nombre_citado_con_metacaracter_pesa_entero(self):
        """Las comillas protegen un nombre que contiene `;`: no se trunca."""
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            r = Recorrido(tmp, "sh-c-cita-meta", "crea saludo.txt")
            self.addCleanup(r.reg.close)
            interno = comprobacion.interno_de(r.espacio, crear=True)
            script = interno / "a;b.sh"
            script.write_text("exit 5\n", encoding="utf-8")
            declaradas = [
                {"objetivo": "x", "argv": ["/bin/sh", "-c", 'cat ".interno/a;b.sh"']}
            ]

            antes = comprobacion.huella_procedimiento(r.espacio, declaradas)
            script.write_text("exit 0\n", encoding="utf-8")
            despues = comprobacion.huella_procedimiento(r.espacio, declaradas)

            self.assertIsNotNone(antes)
            self.assertNotEqual(
                antes,
                despues,
                "un nombre citado con un metacaracter pesa entero",
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

    def test_la_huella_no_lee_lo_que_sale_de_interno(self):
        """Una ruta con `..` no mete en la huella un archivo de fuera de `.interno/`."""
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            r = Recorrido(tmp, "confinamiento", "crea saludo.txt")
            self.addCleanup(r.reg.close)
            interno = comprobacion.interno_de(r.espacio, crear=True)
            (interno / "script.sh").write_text("exit 0\n", encoding="utf-8")
            fuera = r.espacio / "fuera.txt"
            fuera.write_text("uno\n", encoding="utf-8")
            declaradas = [
                {
                    "objetivo": "lee fuera",
                    "argv": [sys.executable, ".interno/../fuera.txt"],
                }
            ]

            antes = comprobacion.huella_procedimiento(r.espacio, declaradas)
            fuera.write_text("dos\n", encoding="utf-8")
            despues = comprobacion.huella_procedimiento(r.espacio, declaradas)

            self.assertIsNotNone(
                antes, "la ruta de .interno sigue entrando en la huella"
            )
            self.assertEqual(
                antes,
                despues,
                "un archivo que sale de .interno no entra en la huella",
            )

    def test_un_enlace_dentro_de_un_directorio_no_entra_en_la_huella(self):
        """Un enlace dentro del árbol no mete contenido de fuera de `.interno/`."""
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            r = Recorrido(tmp, "enlace-dir", "crea saludo.txt")
            self.addCleanup(r.reg.close)
            interno = comprobacion.interno_de(r.espacio, crear=True)
            carpeta = interno / "carpeta"
            carpeta.mkdir()
            (carpeta / "propio.sh").write_text("exit 0\n", encoding="utf-8")
            (r.espacio / ".runtime").mkdir(exist_ok=True)
            fuera = r.espacio / ".runtime" / "dato.txt"
            fuera.write_text("uno\n", encoding="utf-8")
            try:
                (carpeta / "enlace").symlink_to(fuera)
            except (OSError, NotImplementedError):
                self.skipTest("la plataforma no permite crear enlaces simbólicos")

            declaradas = [
                {"objetivo": "lee un arbol", "argv": [sys.executable, ".interno/carpeta"]}
            ]
            antes = comprobacion.huella_procedimiento(r.espacio, declaradas)
            fuera.write_text("dos\n", encoding="utf-8")
            despues = comprobacion.huella_procedimiento(r.espacio, declaradas)

            self.assertIsNotNone(antes)
            self.assertEqual(
                antes,
                despues,
                "un enlace dentro del arbol no mete contenido de fuera en la huella",
            )

    def test_una_ruta_ilegible_deja_su_marca_sin_tumbar_la_huella(self):
        """Un archivo ilegible no levanta: deja una marca distinta de ausente."""
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            r = Recorrido(tmp, "ilegible", "crea saludo.txt")
            self.addCleanup(r.reg.close)
            interno = comprobacion.interno_de(r.espacio, crear=True)
            script = interno / "script.sh"
            script.write_text("exit 0\n", encoding="utf-8")
            declaradas = [
                {"objetivo": "x", "argv": [sys.executable, ".interno/script.sh"]}
            ]

            legible = comprobacion.huella_procedimiento(r.espacio, declaradas)
            with mock.patch.object(
                Path, "read_bytes", side_effect=PermissionError("sin permiso")
            ):
                ilegible = comprobacion.huella_procedimiento(r.espacio, declaradas)
            script.unlink()
            ausente = comprobacion.huella_procedimiento(r.espacio, declaradas)

            self.assertIsNotNone(ilegible)
            self.assertNotEqual(ilegible, legible)
            self.assertNotEqual(ilegible, ausente)


    def test_una_ruta_con_espacios_en_un_argumento_pesa_entera(self):
        """Un argumento de ruta conserva sus espacios; no se corta en el primero."""
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            r = Recorrido(tmp, "ruta-espacios", "crea saludo.txt")
            self.addCleanup(r.reg.close)
            interno = comprobacion.interno_de(r.espacio, crear=True)
            carpeta = interno / "mis pruebas"
            carpeta.mkdir()
            script = carpeta / "run.sh"
            script.write_text("exit 5\n", encoding="utf-8")
            declaradas = [
                {
                    "objetivo": "x",
                    "argv": ["/bin/sh", ".interno/mis pruebas/run.sh"],
                }
            ]

            huella_roja = comprobacion.huella_procedimiento(r.espacio, declaradas)
            script.write_text("exit 0\n", encoding="utf-8")
            huella_verde = comprobacion.huella_procedimiento(r.espacio, declaradas)

            self.assertIsNotNone(huella_roja)
            self.assertNotEqual(
                huella_roja,
                huella_verde,
                "la ruta con espacios pesa entera en la huella",
            )

    def test_la_huella_se_toma_despues_y_no_cuenta_caches(self):
        """La huella de un árbol es la de después de ejecutar y sin `__pycache__`."""
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            r = Recorrido(tmp, "huella-despues", "crea saludo.txt")
            self.addCleanup(r.reg.close)
            interno = comprobacion.interno_de(r.espacio, crear=True)
            carpeta = interno / "pruebas"
            carpeta.mkdir()
            _declarar(
                r,
                [
                    "/bin/sh",
                    "-c",
                    "cd .interno/pruebas && mkdir -p __pycache__ && "
                    "echo x > __pycache__/x.pyc && "
                    "echo y > generado.txt && exit 0",
                ],
            )

            declaradas = comprobacion.leer_declaradas(
                comprobacion.interno_de(r.espacio)
            )
            vacio = comprobacion.huella_procedimiento(r.espacio, declaradas)

            datos = _observar(r)
            self.assertEqual(datos["ejecuciones"][0].get("rc"), 0, datos)
            self.assertTrue((carpeta / "__pycache__").is_dir())
            self.assertTrue((carpeta / "generado.txt").is_file())

            actual = comprobacion.huella_procedimiento(r.espacio, declaradas)
            self.assertEqual(
                datos.get("procedimiento"),
                actual,
                "la huella guardada es la que se recalcula despues de ejecutar",
            )
            self.assertNotEqual(
                datos.get("procedimiento"),
                vacio,
                "el archivo que deja la comprobacion pesa; la huella no es la "
                "del arbol vacio de antes de ejecutar",
            )
            (carpeta / "__pycache__" / "y.pyc").write_text("y\n", encoding="utf-8")
            mas_cache = comprobacion.huella_procedimiento(r.espacio, declaradas)
            self.assertEqual(
                actual,
                mas_cache,
                "una cache nueva bajo el arbol no cambia la huella",
            )


if __name__ == "__main__":
    unittest.main()
