"""El aviso MODELOS del comprobador: cada agente de cada director contra la tabla de la flota."""

from __future__ import annotations

import ast
import io
import json
import os
import sqlite3
import sys
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from motor.flota.manifiesto import Politica, Puesto, emitir
from ops.modelos_por_agente import imprimir, informe

DIRIGIR = (
    ("anthropic/claude-opus-5-5", "high"),
    ("kimi/k3.1-pro", "high"),
    ("xai/grok-4.7", "high"),
    ("opencode-go/deepseek-v4.1-flash", "high"),
)
SIMPLES = (("opencode-go/deepseek-v4.1-flash", "high"), ("xai/grok-4.7", "low"))
TABLA = Politica(
    "v9",
    (Puesto("dirigir", DIRIGIR), Puesto("simples", SIMPLES)),
    (("alfa", "dirigir"), ("beta", "simples"), ("default", "simples")),
    (),
    (),
    None,
    (),
)
PLANTADO = "valor-plantado-que-no-sale"


def _maquina(tmp_path: Path, *directores: str) -> Path:
    home = tmp_path / "home"
    (home / ".motor-encargos").mkdir(parents=True)
    canal = {"telegram": {"token": PLANTADO}, **{d: {} for d in directores}}
    (home / ".motor-encargos" / "canal.json").write_text(json.dumps(canal), encoding="utf-8")
    manifiesto = home / "flota" / "actual" / "manifesto.toml"
    manifiesto.parent.mkdir(parents=True)
    manifiesto.write_text(emitir(TABLA), encoding="utf-8")
    return home


def _informe(home: Path) -> list[str]:
    return informe(home, home / "flota" / "actual" / "manifesto.toml")


def _claw(home: Path, agentes: dict[str, list[str]], defecto: list[str] | None = None) -> None:
    def modelo(cadena):
        return {"primary": cadena[0], "fallbacks": cadena[1:]}

    entradas = {a: {"model": modelo(c), "name": a} for a, c in agentes.items()}
    entradas["codex"] = {"name": "Codex (CLI)", "runtime": {"type": "acp"}}
    doc = {"gateway": {"auth": {"token": PLANTADO}}, "agents": {"entries": entradas}}
    if defecto:
        doc["agents"]["defaults"] = {"model": modelo(defecto)}
    ruta = home / ".openclaw" / "openclaw.json"
    ruta.parent.mkdir(parents=True)
    ruta.write_text(json.dumps(doc), encoding="utf-8")


def _hermes(home: Path, perfiles: dict[str, list[tuple[str, str]]]) -> None:
    """Perfiles de Hermes en JSON, que también es YAML, y un python de Hermes con un yaml mínimo."""
    base = home / ".hermes"
    for perfil, cadena in perfiles.items():
        ruta = base / "config.yaml" if perfil == "default" else base / "profiles" / perfil / "config.yaml"
        ruta.parent.mkdir(parents=True, exist_ok=True)
        cfg = {
            "model": {"default": cadena[0][0], "provider": "p0", "api_key": PLANTADO},
            "fallback_providers": [
                {"provider": "p%d" % i, "model": m, "api_key": PLANTADO}
                for i, (m, _) in enumerate(cadena[1:], 1)
            ],
            "agent": {"reasoning_overrides": dict(cadena)},
        }
        ruta.write_text(json.dumps(cfg), encoding="utf-8")
    stub = home.parent / "yaml-minimo"
    stub.mkdir(exist_ok=True)
    (stub / "yaml.py").write_text(
        "import json\n\ndef safe_load(archivo):\n    return json.load(archivo)\n", encoding="utf-8"
    )
    python = base / "hermes-agent" / "venv" / "bin" / "python"
    python.parent.mkdir(parents=True)
    python.write_text(
        "#!/bin/sh\nPYTHONPATH='%s' exec '%s' \"$@\"\n" % (stub, sys.executable), encoding="utf-8"
    )
    python.chmod(0o755)


def _openbot(home: Path, agentes: list[tuple[str, str, str]]) -> None:
    db = home / "Library" / "Application Support" / "OpenBot" / "openbot.db"
    db.parent.mkdir(parents=True)
    with closing(sqlite3.connect(db)) as con:
        con.execute("CREATE TABLE projection_agents (agent_id TEXT, agent_json TEXT)")
        for i, (nombre, modelo, esfuerzo) in enumerate(agentes):
            agente = {
                "name": nombre,
                "provider": "claude",
                "model": modelo,
                "reasoningEffort": esfuerzo,
                "apiKey": PLANTADO,
            }
            con.execute("INSERT INTO projection_agents VALUES (?, ?)", (str(i), json.dumps(agente)))
        con.commit()


def _mausbot(home: Path, bots: list[tuple[str, dict]]) -> None:
    ruta = home / ".openmausbot" / "bots.json"
    ruta.parent.mkdir(parents=True)
    doc = [{"name": n, "modelSelection": s, "telegramToken": PLANTADO} for n, s in bots]
    ruta.write_text(json.dumps(doc), encoding="utf-8")



ALFA_HERMES = [
    ("claude-opus-5-5", "high"),
    ("k3.1-pro", "high"),
    ("grok-4.7", "high"),
    ("deepseek-v4.1-flash", "high"),
]



FUENTE = Path(__file__).resolve().parent.parent / "ops" / "modelos_por_agente.py"


class ModelosPorAgente(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="modelos-por-agente-")
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        entorno = patch.dict(os.environ)
        entorno.start()
        self.addCleanup(entorno.stop)
        os.environ.pop("LOCALAPPDATA", None)

    def test_claw_como_la_tabla_cuenta_cada_agente_y_su_modelo_por_omision(self):
        home = _maquina(self.base, "claw")
        _claw(
            home,
            {"alfa": [m for m, _ in DIRIGIR], "beta": [m for m, _ in SIMPLES]},
            defecto=[m for m, _ in SIMPLES],
        )

        self.assertEqual(_informe(home), ["claw: 3 de 3 agentes como la tabla"])

    def test_claw_toma_de_claw_routing_la_fila_del_agente_que_la_herencia_no_nombra(self):
        home = _maquina(self.base, "claw")
        _claw(home, {"main": [m for m, _ in DIRIGIR], "otro": [m for m, _ in DIRIGIR]})
        ruteo = home / "flota" / "actual" / "config" / "hermes" / "claw-routing.json"
        ruteo.parent.mkdir(parents=True)
        ruteo.write_text(
            json.dumps({"agents": {"main": {"row": "dirigir", "thinking": True}, "otro": {"row": "simples"}}}),
            encoding="utf-8",
        )

        self.assertEqual(_informe(home), [
            "claw: 1 de 2 agentes como la tabla; difieren: otro (claude-opus-5-5 > k3.1-pro > grok-4.7"
            " > deepseek-v4.1-flash; tabla deepseek-v4.1-flash > grok-4.7)"
        ])

    def test_hermes_en_otro_orden_es_otra_cadena(self):
        home = _maquina(self.base, "hermes")
        _hermes(
            home,
            {
                "alfa": [("claude-opus-5-5", "high"), ("k3.1-pro", "high"), ("grok-4.7", "high"),
                         ("deepseek-v4.1-flash", "high")],
                "beta": [("grok-4.7", "low"), ("deepseek-v4.1-flash", "high")],
            },
        )

        self.assertEqual(_informe(home), [
            "hermes: 1 de 2 agentes como la tabla; difieren: beta (grok-4.7 low > deepseek-v4.1-flash"
            " high; tabla deepseek-v4.1-flash high > grok-4.7 low)"
        ])

    def test_hermes_con_otro_esfuerzo_difiere_y_claw_no_mira_esfuerzos(self):
        home = _maquina(self.base, "hermes", "claw")
        _hermes(home, {"beta": [("deepseek-v4.1-flash", "medium"), ("grok-4.7", "low")]})
        _claw(home, {"beta": [m for m, _ in SIMPLES]})

        self.assertEqual(_informe(home), [
            "hermes: 0 de 1 agentes como la tabla; difieren: beta (deepseek-v4.1-flash medium >"
            " grok-4.7 low; tabla deepseek-v4.1-flash high > grok-4.7 low)",
            "claw: 1 de 1 agentes como la tabla",
        ])

    def test_el_proveedor_no_cuenta(self):
        for i, kimi in enumerate(
                ["kimi/k3.1-pro", "kimi-code/k3.1-pro", "kimi-coding/k3.1-pro", "k3.1-pro"]
        ):
              with self.subTest(kimi=kimi):
                home = _maquina(self.base / str(i), "claw", "openbot")
                _claw(
                    home,
                    {
                        "alfa": ["claude-opus-5-5", kimi, "grok-4.7", "opencode-go-2/deepseek-v4.1-flash"],
                        "beta": ["opencode-go/deepseek-v4.1-flash", "xai/grok-4.7"],
                    },
                )
                _openbot(home, [("alfa", "claude-opus-5-5", "high"), ("beta", "deepseek-v4.1-flash", "high")])

                self.assertEqual(_informe(home), [
                    "claw: 2 de 2 agentes como la tabla",
                    "openbot: 2 de 2 agentes como la tabla",
                ])

    def test_otro_modelo_con_nombre_parecido_difiere(self):
        casos = [
                ("deepseek/deepseek-flash", "deepseek-flash"),
                ("alibaba-token-plan/deepseek-v4.1-flash-1017", "deepseek-v4.1-flash-1017"),
        ]
        for i, (modelo, visto) in enumerate(casos):
              with self.subTest(modelo=modelo):
                home = _maquina(self.base / str(i), "claw", "openbot")
                _claw(home, {"beta": [modelo, "xai/grok-4.7"]})
                _openbot(home, [("beta", modelo, "high")])

                self.assertEqual(_informe(home), [
                    "claw: 0 de 1 agentes como la tabla; difieren: beta (%s > grok-4.7; tabla"
                    " deepseek-v4.1-flash > grok-4.7)" % visto,
                    "openbot: 0 de 1 agentes como la tabla; difieren: beta (%s high; tabla"
                    " deepseek-v4.1-flash high)" % visto,
                ])

    def test_un_agente_sin_fila_se_nombra_aparte_y_el_nombre_va_en_minusculas(self):
        home = _maquina(self.base, "openbot")
        _openbot(home, [("Alfa", "claude-opus-5-5", "high"), ("Gamma Uno", "grok-4.7", "high")])

        self.assertEqual(_informe(home), [
            "openbot: 1 de 2 agentes como la tabla; sin fila en la tabla: Gamma Uno"
        ])

    def test_un_bot_de_un_modelo_se_compara_con_el_primero_de_la_cadena(self):
        home = _maquina(self.base, "openbot", "mausbot")
        _openbot(home, [("alfa", "claude-opus-5-5", "high"), ("beta", "deepseek-v4.1-flash", "low")])
        _mausbot(
            home,
            [
                ("alfa", {"instanceId": "claude", "model": "anthropic/claude-opus-5-5", "effort": "high"}),
                ("beta", {"instanceId": "go", "model": "deepseek-v4.1-flash", "variant": "high"}),
                ("Beta", {"instanceId": "claude", "model": "claude-opus-5-5"}),
            ],
        )

        self.assertEqual(_informe(home), [
            "openbot: 1 de 2 agentes como la tabla; difieren: beta (deepseek-v4.1-flash low; tabla"
            " deepseek-v4.1-flash high)",
            "mausbot: 2 de 3 agentes como la tabla; difieren: Beta (claude-opus-5-5; tabla"
            " deepseek-v4.1-flash high)",
        ])

    def test_el_bot_con_el_nombre_de_su_app_usa_la_fila_del_director(self):
        home = _maquina(self.base, "mausbot")
        manifiesto = home / "flota" / "actual" / "manifesto.toml"
        manifiesto.write_text(
            manifiesto.read_text(encoding="utf-8") + 'claw = "dirigir"\n', encoding="utf-8"
        )
        _mausbot(
            home,
            [
                ("MausBot", {"instanceId": "claude", "model": "claude-opus-5-5", "effort": "high"}),
                ("Claw", {"instanceId": "claude", "model": "claude-sonnet-5-5", "effort": "high"}),
            ],
        )

        self.assertEqual(_informe(home), [
            "mausbot: 1 de 2 agentes como la tabla; difieren: Claw (claude-sonnet-5-5 high; tabla"
            " claude-opus-5-5 high)",
        ])

    def test_si_la_herencia_nombra_al_bot_de_la_app_manda_la_herencia(self):
        home = _maquina(self.base, "mausbot")
        manifiesto = home / "flota" / "actual" / "manifesto.toml"
        manifiesto.write_text(
            manifiesto.read_text(encoding="utf-8") + 'claw = "dirigir"\nmausbot = "simples"\n',
            encoding="utf-8",
        )
        _mausbot(home, [("MausBot", {"instanceId": "go", "model": "deepseek-v4.1-flash", "variant": "high"})])

        self.assertEqual(_informe(home), ["mausbot: 1 de 1 agentes como la tabla"])

    def test_lo_que_no_se_puede_leer_se_dice_por_director_y_sigue(self):
        home = _maquina(self.base, "hermes", "claw", "openbot", "mausbot")
        _claw(home, {"alfa": [m for m, _ in DIRIGIR]})
        (home / ".openmausbot").mkdir()
        (home / ".openmausbot" / "bots.json").write_text("{no es json", encoding="utf-8")

        self.assertEqual(_informe(home), [
            "hermes: no pude leer (sin el python de Hermes)",
            "claw: 1 de 1 agentes como la tabla",
            "openbot: no pude leer (sin openbot.db)",
            "mausbot: no pude leer (bots.json ilegible)",
        ])

    def test_sin_manifiesto_cada_director_lo_dice(self):
        home = _maquina(self.base, "claw", "mausbot")
        _claw(home, {"alfa": [m for m, _ in DIRIGIR]})
        (home / "flota" / "actual" / "manifesto.toml").unlink()

        self.assertEqual(_informe(home), [
            "claw: no pude leer (sin manifiesto)",
            "mausbot: no pude leer (sin manifiesto)",
        ])

    def test_un_config_de_hermes_roto_dice_su_tipo_y_no_su_texto(self):
        home = _maquina(self.base, "hermes")
        _hermes(home, {"alfa": list(DIRIGIR)})
        (home / ".hermes" / "profiles" / "alfa" / "config.yaml").write_text(
            '{"model": "%s"' % PLANTADO, encoding="utf-8"
        )

        self.assertEqual(_informe(home), ["hermes: no pude leer (config.yaml ilegible: JSONDecodeError)"])

    def test_una_maquina_sin_canal_no_tiene_directores_que_revisar(self):
        home = _maquina(self.base, "claw")
        (home / ".motor-encargos" / "canal.json").unlink()

        self.assertEqual(_informe(home), [])

    def test_ninguna_linea_lleva_llaves_ni_rutas(self):
        home = _maquina(self.base, "hermes", "claw", "openbot", "mausbot")
        _hermes(home, {"alfa": [("claude-opus-5-5", "low")]})
        _claw(home, {"alfa": ["xai/grok-4.7"]})
        _openbot(home, [("alfa", "grok-4.7", "high")])
        _mausbot(home, [("alfa", {"model": "grok-4.7", "effort": "high"})])

        salida = "\n".join(_informe(home))

        self.assertEqual(salida.count("difieren: alfa"), 4)
        self.assertNotIn(PLANTADO, salida)
        self.assertNotIn(str(self.base), salida)



    def test_una_mutacion_sembrada_en_una_cadena_igual_sale_como_diferencia(self):
        mutadas = {
                "orden": [ALFA_HERMES[1], ALFA_HERMES[0], *ALFA_HERMES[2:]],
                "esfuerzo": [("claude-opus-5-5", "xhigh"), *ALFA_HERMES[1:]],
                "modelo": [ALFA_HERMES[0], ("glm-5.3", "high"), *ALFA_HERMES[2:]],
                "falta-un-respaldo": ALFA_HERMES[:-1],
                "sobra-un-respaldo": [*ALFA_HERMES, ("glm-5.3", "high")],
        }
        for i, (clave, mutada) in enumerate(mutadas.items()):
              with self.subTest(clave):
                igual = _maquina(self.base / ("igual%d" % i), "hermes")
                _hermes(igual, {"alfa": ALFA_HERMES})
                self.assertEqual(_informe(igual), ["hermes: 1 de 1 agentes como la tabla"])

                sembrada = _maquina(self.base / ("sembrada%d" % i), "hermes")
                _hermes(sembrada, {"alfa": mutada})
                [linea] = _informe(sembrada)

                self.assertTrue(linea.startswith("hermes: 0 de 1 agentes como la tabla; difieren: alfa ("))

    def test_un_agente_con_emoji_sale_escapado_en_una_salida_cp1252(self):
        salida = io.TextIOWrapper(io.BytesIO(), encoding="cp1252")
        lineas = ["claw: 0 de 1 agentes como la tabla; difieren: beta \U0001f600 (x; tabla y)", "hermes: ok"]

        imprimir(lineas, salida)
        salida.flush()

        self.assertEqual(
            salida.buffer.getvalue().decode("cp1252"),
            "modelos claw: 0 de 1 agentes como la tabla; difieren: beta \\U0001f600 (x; tabla y)\n"
            "modelos hermes: ok\n",
        )

    def test_el_modulo_corre_en_python_3_9(self):
        texto = FUENTE.read_text(encoding="utf-8")
        arbol = ast.parse(texto, feature_version=(3, 9))

        anotaciones = []
        for nodo in ast.walk(arbol):
            if isinstance(nodo, ast.AnnAssign):
                anotaciones.append(nodo.annotation)
            elif isinstance(nodo, (ast.FunctionDef, ast.AsyncFunctionDef)):
                anotaciones.append(nodo.returns)
                argumentos = nodo.args
                todos = [*argumentos.posonlyargs, *argumentos.args, *argumentos.kwonlyargs]
                todos += [a for a in (argumentos.vararg, argumentos.kwarg) if a]
                anotaciones += [a.annotation for a in todos]
        a_futuro = any(
            isinstance(n, ast.ImportFrom) and n.module == "__future__"
            and any(alias.name == "annotations" for alias in n.names)
            for n in arbol.body
        )
        con_barra = [
            a.lineno
            for a in anotaciones
            if a is not None and any(isinstance(n, ast.BinOp) and isinstance(n.op, ast.BitOr) for n in ast.walk(a))
        ]

        self.assertTrue(a_futuro or not con_barra, "X | Y evaluado en anotaciones, líneas %s" % con_barra)
