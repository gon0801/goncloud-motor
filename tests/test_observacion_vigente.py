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
            (r.espacio / ".runtime").mkdir(exist_ok=True)
            fuera = r.espacio / ".runtime" / "dato.txt"
            fuera.write_text("uno\n", encoding="utf-8")
            declaradas = [
                {
                    "objetivo": "lee fuera",
                    "argv": [sys.executable, ".interno/../.runtime/dato.txt"],
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


if __name__ == "__main__":
    unittest.main()
