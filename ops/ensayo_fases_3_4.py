#!/usr/bin/env python3

from __future__ import annotations

import json
import os
import shutil
import signal
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
HERMES = Path("/Users/dn/dev/goncloud-hermes-encargos-3-4")
CONF = Path("/Users/dn/dev/goncloud-motor-conf-encargos-3-4")
OPENCLAW = Path("/Users/dn/dev/goncloud-openclaw")
OPENBOT = Path("/Applications/OpenBot.app/Contents/Resources/cli/openbot-cli.mjs")
TABLA = HERMES / "model-routing.source.json"
EVIDENCIA = RAIZ / "docs" / "evidence" / "fase-3-4.json"
PEDIDO = (
    "Resume este pedido en una línea. Conserva el texto tal cual. "
    "No lo conviertas en bloques ni pidas un campo Acepta.\n"
)
TOKEN_ENSAYO = "ensayo-local"
AGENTE = {"id": "dir-ensayo", "name": "Director ensayo"}
RESPUESTA = "resumen de ensayo listo"


class Fallo(RuntimeError):
    pass


def _ambiente() -> dict[str, str]:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(RAIZ)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    return env


def correr(argv: list[str], **kwargs) -> subprocess.CompletedProcess[str]:
    return subprocess.run(argv, capture_output=True, text=True, **kwargs)


def motor(*args: str, env: dict[str, str] | None = None, cwd: Path | None = None) -> dict:
    hecho = correr(
        [sys.executable, "-m", "motor", *args],
        env=env or _ambiente(),
        cwd=cwd,
    )
    if hecho.returncode != 0:
        raise Fallo(
            " ".join(args[:4]) + " salió " + str(hecho.returncode) + "\n" + hecho.stderr
        )
    return json.loads(hecho.stdout)


def exigir(cond: bool, mensaje: str) -> None:
    if not cond:
        raise Fallo(mensaje)


def versiones(argv: list[str], limite: int = 20) -> dict:
    try:
        hecho = correr(argv, timeout=limite)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"estado": "ausente", "detalle": str(exc)[:200]}
    texto = "\n".join(parte for parte in (hecho.stdout, hecho.stderr) if parte).strip()
    linea = texto.splitlines()[0] if texto else ""
    if "@" in linea or any(m in linea.lower() for m in ("token", "secret", "password")):
        linea = "presente"
    return {"estado": "presente" if hecho.returncode == 0 else "fallo", "version": linea[:200], "rc": hecho.returncode}


def ssh(host: str, remoto: str, limite: int = 25) -> dict:
    try:
        hecho = correr(
            [
                "ssh",
                "-o",
                "BatchMode=yes",
                "-o",
                "ConnectTimeout=8",
                host,
                remoto,
            ],
            timeout=limite,
        )
    except subprocess.TimeoutExpired:
        return {"estado": "timeout"}
    texto = "\n".join(parte for parte in (hecho.stdout, hecho.stderr) if parte)
    lineas = []
    for linea in texto.splitlines():
        if "@" in linea or any(m in linea.lower() for m in ("token", "secret", "password")):
            continue
        lineas.append(linea[:200])
    return {"rc": hecho.returncode, "texto": "\n".join(lineas[:12])}


def entregas(base: Path, encargo: str) -> int:
    db = sqlite3.connect(base / "registro.sqlite")
    try:
        fila = db.execute(
            "select count(*) from events where encargo = ? and kind = 'entrega'",
            (encargo,),
        ).fetchone()
    finally:
        db.close()
    return int(fila[0])


def procesos(base: Path) -> list[str]:
    hecho = correr(["ps", "-ax", "-o", "pid=,command="])
    marca = str(base)
    return [linea.strip()[:180] for linea in hecho.stdout.splitlines() if marca in linea]


def matar(base: Path) -> None:
    for _ in range(4):
        vivos = procesos(base)
        if not vivos:
            return
        for linea in vivos:
            pid = linea.split(None, 1)[0]
            if pid.isdigit() and int(pid) != os.getpid():
                try:
                    os.kill(int(pid), signal.SIGKILL)
                except OSError:
                    pass
        time.sleep(0.2)


def hasta(base: Path, encargo: str, estados: set[str]) -> dict:
    ultimo = {}
    for _ in range(8):
        motor(
            "supervisor",
            "--base",
            str(base),
            "--una-vez",
            "--agente",
            "sleep",
            "--umbral",
            "300",
        )
        ultimo = motor("director", "ver", encargo, "--base", str(base))
        if ultimo.get("estado") in estados and not procesos(base):
            return ultimo
        time.sleep(0.3)
    return ultimo


def git(repo: Path, *args: str) -> str:
    hecho = correr(["git", "-C", str(repo), *args])
    if hecho.returncode != 0:
        raise Fallo((hecho.stderr or hecho.stdout or "git salió " + str(hecho.returncode)).strip())
    return hecho.stdout.strip()


def commit(repo: Path, archivos: dict[str, str], mensaje: str) -> str:
    for nombre, texto in archivos.items():
        path = repo / nombre
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(texto, encoding="utf-8")
    git(repo, "add", ".")
    git(repo, "commit", "-m", mensaje)
    return git(repo, "rev-parse", "HEAD")


class DobleOpenBot:
    def __init__(self) -> None:
        self.entregas: dict[str, dict] = {}
        self.completar = False

        exterior = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, fmt: str, *args) -> None:
                del fmt, args

            def _auth(self) -> bool:
                if self.headers.get("authorization") != f"Bearer {TOKEN_ENSAYO}":
                    self.send_response(401)
                    self.end_headers()
                    return False
                return True

            def _json(self, codigo: int, cuerpo) -> None:
                raw = json.dumps(cuerpo).encode()
                self.send_response(codigo)
                self.send_header("content-type", "application/json")
                self.send_header("content-length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def do_GET(self) -> None:
                if not self._auth():
                    return
                if self.path == "/v1/agents":
                    self._json(200, [AGENTE])
                    return
                if self.path.endswith("/pending"):
                    self._json(200, {"prompts": [], "approvals": [], "browserTakeovers": []})
                    return
                if "/deliveries/" in self.path:
                    ident = self.path.rsplit("/", 1)[-1]
                    doc = dict(exterior.entregas[ident])
                    if exterior.completar:
                        doc["status"] = "completed"
                        doc["answer"] = RESPUESTA
                    self._json(200, doc)
                    return
                self._json(404, {"error": "no"})

            def do_POST(self) -> None:
                if not self._auth():
                    return
                largo = int(self.headers.get("content-length") or 0)
                cuerpo = json.loads(self.rfile.read(largo) or b"{}")
                ident = "del-ensayo"
                exterior.entregas[ident] = {
                    "id": ident,
                    "status": "running",
                    "answer": None,
                    "clientMessageId": cuerpo.get("clientMessageId"),
                }
                self._json(
                    200,
                    {
                        "messageId": "msg-ensayo",
                        "deliveries": [
                            {
                                "id": ident,
                                "recipientAgentId": AGENTE["id"],
                                "status": "running",
                            }
                        ],
                    },
                )

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.hilo = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.hilo.start()

    @property
    def url(self) -> str:
        host, puerto = self.server.server_address[:2]
        return f"http://{host}:{puerto}"

    def cerrar(self) -> None:
        self.server.shutdown()
        self.server.server_close()


def preparar_flota(home: Path) -> dict:
    motor_repo = home / "motor"
    motor_repo.mkdir(parents=True)
    git(motor_repo, "init", "-b", "main")
    git(motor_repo, "config", "user.email", "ensayo@local")
    git(motor_repo, "config", "user.name", "ensayo")
    buena = commit(
        motor_repo,
        {
            "comprobar.py": "import sys\nraise SystemExit(0 if sys.version_info[0] >= 3 else 1)\n",
            "marca.txt": "buena\n",
        },
        "buena",
    )
    mala = commit(motor_repo, {"comprobar.py": "raise SystemExit(1)\n"}, "mala")
    nueva = commit(
        motor_repo,
        {
            "comprobar.py": "import sys\nraise SystemExit(0 if sys.version_info[0] >= 3 else 1)\n",
            "marca.txt": "nueva\n",
        },
        "nueva",
    )
    git(motor_repo, "checkout", "-q", "--detach", buena)
    aplicada = motor("flota", "aplicar", "--home", str(home), "--destino", buena)
    trabajo = motor(
        "flota",
        "trabajo",
        "--home",
        str(home),
        "--id",
        "activo",
        "--generacion",
        buena,
        "--herramienta",
        "codex",
    )
    otra = motor("flota", "aplicar", "--home", str(home), "--destino", nueva)
    asignada = motor("flota", "asignar", "--home", str(home))
    rechazada = motor("flota", "aplicar", "--home", str(home), "--destino", mala)
    extra = commit(
        motor_repo,
        {
            "comprobar.py": "import sys\nraise SystemExit(0 if sys.version_info[0] >= 3 else 1)\n",
            "marca.txt": "extra\n",
        },
        "extra",
    )
    git(motor_repo, "checkout", "-q", "--detach", nueva)
    cortada = motor(
        "flota", "aplicar", "--home", str(home), "--destino", extra, "--corte", "worktree"
    )
    recuperada = motor("flota", "recuperar", "--home", str(home))
    nada = motor("flota", "recuperar", "--home", str(home))
    marca = home / "arranque-del-motor"
    rota = commit(
        motor_repo,
        {
            "comprobar.py": "raise SystemExit(0)\n",
            "motor/__init__.py": "",
            "motor/__main__.py": (
                "import os\nfrom pathlib import Path\n"
                "Path(os.environ['MOTOR_MARCA']).write_text('arranque')\n"
                "raise SystemExit(1)\n"
            ),
        },
        "rota",
    )
    (home / "instalacion-pendiente.json").write_text(
        json.dumps(
            {
                "anterior": {"codigo": nueva},
                "destino": {"codigo": rota},
                "etapa": "codigo",
            }
        ),
        encoding="utf-8",
    )
    control = correr(
        [sys.executable, "-m", "motor"],
        cwd=motor_repo,
        env={"MOTOR_MARCA": str(marca), "PATH": "/usr/bin:/bin", "PYTHONDONTWRITEBYTECODE": "1"},
    )
    exigir(marca.is_file(), "el motor de la candidata no escribió su marca de control")
    marca.unlink()
    reversa = correr(
        ["sh", str(RAIZ / "ops" / "reversa_ensayo.sh"), "--home", str(home)],
        env={"MOTOR_MARCA": str(marca), "PATH": "/usr/bin:/bin:/opt/homebrew/bin"},
    )
    exigir(reversa.returncode == 0, reversa.stderr)
    exigir("revertido" in reversa.stdout, reversa.stdout)
    exigir(not marca.exists(), "la reversa arrancó el motor nuevo")
    exigir(git(motor_repo, "rev-parse", "HEAD") == nueva, "la reversa no volvió a la generación anterior")
    segunda = correr(
        ["sh", str(RAIZ / "ops" / "reversa_ensayo.sh"), "--home", str(home)],
        env={"PATH": "/usr/bin:/bin:/opt/homebrew/bin"},
    )
    return {
        "aplicada": aplicada["accion"],
        "trabajo": trabajo["generacion"],
        "segunda": otra["accion"],
        "asignada": asignada["generacion"],
        "rechazada": rechazada["accion"],
        "cortada": cortada["accion"],
        "recuperada": recuperada["accion"],
        "segunda_recuperacion": nada["accion"],
        "reversa": reversa.stdout.strip(),
        "reversa_otra": segunda.stdout.strip(),
        "marca_reaparecio": marca.exists(),
        "cabeza": git(motor_repo, "rev-parse", "HEAD"),
        "anterior": nueva,
    }


def main() -> int:
    evidencia: dict = {"dobles": []}
    base = Path(tempfile.mkdtemp(prefix="encargos-3-4-base-"))
    home = Path(tempfile.mkdtemp(prefix="encargos-3-4-flota-"))
    api: DobleOpenBot | None = None
    try:
        evidencia["maquinas"] = {
            "mac": {
                "python3": versiones([sys.executable, "--version"]),
                "codex": versiones(["codex", "--version"]),
                "grok": versiones(["grok", "--version"]),
                "openclaw": versiones(["openclaw", "--version"]),
                "hermes": versiones(["hermes", "--version"]),
            },
            "mini": ssh("mini", "hostname; python3 --version; /Users/gon/.openclaw/bin/openclaw --version"),
            "pc": ssh(
                "pc",
                "python --version & C:\\Users\\ehven\\AppData\\Local\\hermes\\bin\\hermes.cmd --version",
                45,
            ),
        }
        for nombre in ("python3", "codex", "grok"):
            argv = [sys.executable, "--version"] if nombre == "python3" else [nombre, "--version"]
            doc = motor("flota", "observar", "--nombre", nombre, "--", *argv)
            evidencia["maquinas"]["mac"][nombre]["observado"] = {
                "estado": doc.get("estado"),
                "version": doc.get("version"),
                "integridad": bool(doc.get("integridad", "").startswith("sha256:")),
            }

        plan = base / "plan-externo.txt"
        plan.write_text(PEDIDO, encoding="utf-8")
        hermes_directa = motor(
            "director", "encargar", "--base", str(base), "--director", "hermes",
            "--via", "directa", "--canal", "canal-hermes", "--clave", "h-directa",
            "--texto", PEDIDO,
        )
        claw_directa = motor(
            "director", "encargar", "--base", str(base), "--director", "claw",
            "--via", "directa", "--canal", "canal-claw", "--clave", "c-directa",
            "--texto", PEDIDO,
        )
        exigir(hermes_directa["texto"] == PEDIDO and claw_directa["texto"] == PEDIDO, "el pedido guardado no es el texto entregado")
        exigir(hermes_directa["via"] == "directa" and claw_directa["via"] == "directa", "la vía directa no se respetó")
        repetido = motor(
            "director", "encargar", "--base", str(base), "--director", "hermes",
            "--via", "directa", "--canal", "canal-hermes", "--clave", "h-directa",
            "--texto", PEDIDO,
        )
        exigir(repetido["encargo"] == hermes_directa["encargo"], "retransmitir creó otro encargo")
        evidencia["pedido"] = {
            "hermes_directa": hermes_directa["encargo"],
            "claw_directa": claw_directa["encargo"],
            "mismo_texto": True,
        }

        encargo = hermes_directa["encargo"]
        rechazada = motor("director", "promover", encargo, "--base", str(base), "--clave", "promover-1")
        exigir(rechazada.get("rechazada") is True, "promover no esperó el cierre de la vía directa")
        exigir(not (base / "ejecucion" / encargo).exists(), "promover abrió un escritor")
        motor("director", "cerrar-sesion", encargo, "--base", str(base))
        promovido = motor("director", "promover", encargo, "--base", str(base), "--clave", "promover-1")
        exigir(promovido["encargo"] == encargo and promovido["via"] == "durable", "promover no conservó el encargo")
        exigir(entregas(base, encargo) == 1, "promover creó otra entrega")
        shutil.copytree(RAIZ / "paquetes" / "poteto", base / "politica" / "poteto")
        motor(
            "supervisor", "--base", str(base), "--una-vez", "--agente", "sleep", "--umbral", "300",
        )
        time.sleep(0.4)
        visto = motor("director", "ver", encargo, "--base", str(base))
        exigir(visto["estado"] == "ejecutando", "el director no vio la ejecución: " + visto.get("estado", ""))
        parado = motor("director", "parar", encargo, "--base", str(base), "--clave", "parar-1")
        exigir(parado["estado"] == "deteniendo", "parar no se aceptó")
        detenido = hasta(base, encargo, {"detenido"})
        exigir(detenido.get("estado") == "detenido", "la parada no cerró el árbol: " + str(detenido.get("estado")))
        seguido = motor("director", "continuar", encargo, "--base", str(base), "--clave", "seguir-1")
        exigir(seguido["encargo"] == encargo and seguido["estado"] == "recibido", "continuar no reanudó el mismo encargo")
        motor("director", "cancelar", encargo, "--base", str(base), "--clave", "cancelar-1")
        cancelado = hasta(base, encargo, {"cancelado"})
        exigir(cancelado.get("estado") == "cancelado", "cancelar dejó el encargo abierto")
        evidencia["promover"] = {
            "encargo": encargo,
            "rechazo_con_sesion": rechazada["motivo"],
            "via": promovido["via"],
            "parado": detenido["estado"],
            "continuado": seguido["estado"],
            "cancelado": cancelado["estado"],
            "entregas": entregas(base, encargo),
        }

        poteto_id = motor(
            "director", "encargar", "--base", str(base), "--director", "claw",
            "--via", "durable", "--canal", "canal-claw", "--clave", "poteto",
            "--texto", PEDIDO,
        )["encargo"]
        espacio = base / "espacio" / poteto_id
        espacio.mkdir(parents=True, exist_ok=True)
        (espacio / "trabajo.sh").write_text(
            "#!/bin/bash\n"
            "set -eu\n"
            'espacio=$(CDPATH= cd -- "$(dirname "$0")" && pwd)\n'
            'base=$(CDPATH= cd -- "$espacio/../.." && pwd)\n'
            'id=$(basename "$espacio")\n'
            'marca="No conviertas el pedido en bloques"\n'
            "encontrado=\n"
            'for f in "$base/runtime/$id"/*/poteto-mode.md; do\n'
            '  if [ -f "$f" ] && grep -q "$marca" "$f"; then encontrado=$f; fi\n'
            "done\n"
            'if [ -z "$encontrado" ]; then echo sin-poteto > "$espacio/fallo.txt"; exit 1; fi\n'
            'printf "poteto cargado\\n" > "$espacio/producto.txt"\n',
            encoding="utf-8",
        )
        motor(
            "supervisor", "--base", str(base), "--una-vez", "--agente", "ensayo", "--umbral", "300",
        )
        producto = espacio / "producto.txt"
        for _ in range(20):
            if producto.is_file():
                break
            time.sleep(0.25)
        exigir(producto.is_file() and producto.read_text(encoding="utf-8") == "poteto cargado\n", "el agente de ensayo no usó poteto")
        copias = list((base / "runtime" / poteto_id).glob("*/poteto-mode.md"))
        exigir(len(copias) == 1, "poteto no se copió al runtime del intento")
        exigir("No conviertas el pedido en bloques" in copias[0].read_text(encoding="utf-8"), "la copia no es el paquete")
        motor("director", "parar", poteto_id, "--base", str(base), "--clave", "parar-poteto")
        fin_poteto = hasta(base, poteto_id, {"detenido", "cancelado"})
        hermes_durable = motor(
            "director", "encargar", "--base", str(base), "--director", "hermes",
            "--via", "durable", "--canal", "canal-hermes", "--clave", "h-durable",
            "--texto", PEDIDO,
        )
        claw_plan = motor(
            "director", "encargar", "--base", str(base), "--director", "claw",
            "--via", "durable", "--canal", "canal-claw", "--clave", "c-plan",
            "--archivo", str(plan),
        )
        ids = {
            hermes_directa["encargo"],
            claw_directa["encargo"],
            hermes_durable["encargo"],
            claw_plan["encargo"],
            poteto_id,
        }
        exigir(len(ids) == 5, "los pedidos no quedaron separados")
        exigir(hermes_durable["texto"] == PEDIDO and claw_plan["texto"] == PEDIDO, "el plan externo no se guardó tal cual")
        exigir(hermes_durable["via"] == "durable" and claw_plan["via"] == "durable", "la vía durable no se respetó")
        evidencia["pedido"].update({
            "hermes_durable": hermes_durable["encargo"],
            "claw_plan": claw_plan["encargo"],
            "plan_sin_acepta": True,
        })
        evidencia["poteto"] = {
            "encargo": poteto_id,
            "producto": producto.read_text(encoding="utf-8").strip(),
            "estado": fin_poteto.get("estado"),
            "doble": "el agente ensayo ejecuta trabajo.sh. No es un modelo ni acredita Hermes o Claw.",
        }
        evidencia["dobles"].append(evidencia["poteto"]["doble"])

        aviso_id = motor(
            "director", "encargar", "--base", str(base), "--director", "hermes",
            "--via", "directa", "--canal", "canal-hermes", "--clave", "aviso",
            "--texto", PEDIDO,
        )["encargo"]
        motor("director", "cerrar-sesion", aviso_id, "--base", str(base))
        cerrado = motor(
            "director", "resultado", aviso_id, "--base", str(base), "--texto", "listo el resumen\n",
        )
        exigir(cerrado["estado"] == "terminado", "el resultado directo no cerró")
        antes = entregas(base, aviso_id)
        cortado = motor("director", "avisar", "--base", str(base), "--cortar", "despues")
        aviso = next(item for item in cortado["avisos"] if item["encargo"] == aviso_id)
        canal = base / "canal" / "canal-hermes"
        archivos = list(canal.iterdir()) if canal.exists() else []
        exigir(len(archivos) == 1 and aviso["estado"] == "incierto", "el corte no dejó el aviso pendiente de conciliar")
        recuperado = motor("director", "avisar", "--base", str(base))
        aviso2 = next(item for item in recuperado["avisos"] if item["encargo"] == aviso_id)
        exigir(aviso2["estado"] == "publicado" and aviso2["recibo"].startswith("pub_"), "el aviso no se recuperó")
        exigir(list(canal.iterdir()) == archivos, "recuperar el aviso duplicó el canal")
        exigir(entregas(base, aviso_id) == antes, "recuperar el aviso volvió a ejecutar la entrega")
        evidencia["aviso"] = {
            "encargo": aviso_id,
            "corte": aviso["estado"],
            "despues": aviso2["estado"],
            "archivos": 1,
            "entregas": antes,
        }

        cli = ["node", str(OPENBOT)]
        caps = motor(
            "director", "capacidades-openbot", "--base", str(base),
            "--cli", " ".join(cli),
        )
        exigir(caps["entrega"] and caps["consulta"], "el CLI real no ofrece entrega y consulta")
        exigir(caps["parada"] is False, "se ofreció parada sin demostrarla")
        exigir(caps["timeout_cancela"] is False, "el CLI trata el timeout como cancelación")
        exigir(caps["skills"] is False and caps["publicacion_de_sesion"] is False, "se ofrecieron skills o publicación no demostradas")
        api = DobleOpenBot()
        automatico = base / "openbot-ensayo"
        automatico.mkdir()
        (automatico / "url").write_text(api.url + "\n", encoding="utf-8")
        (automatico / "token").write_text(TOKEN_ENSAYO + "\n", encoding="utf-8")
        entorno = _ambiente()
        entorno["OPENBOT_AUTOMATION_DIR"] = str(automatico)
        corrida = correr(
            [*cli, "run", AGENTE["id"], PEDIDO.strip(), "--wait", "--timeout", "2s", "--id", "ob-1", "--json"],
            env=entorno,
            timeout=20,
        )
        exigir(corrida.returncode == 4, "el timeout del CLI no salió 4: " + corrida.stdout + corrida.stderr)
        timeout_doc = json.loads(corrida.stdout)
        exigir(
            timeout_doc.get("state") == "timed-out" and timeout_doc.get("status") == "running",
            "el timeout canceló la entrega: " + corrida.stdout[:400],
        )
        api.completar = True
        estado = correr(
            [*cli, "status", AGENTE["id"], "del-ensayo", "--json"],
            env=entorno,
            timeout=20,
        )
        exigir(estado.returncode == 0 and RESPUESTA in estado.stdout, "status no recuperó el mismo resultado")
        ob = motor(
            "director", "encargar", "--base", str(base), "--director", "openbot",
            "--via", "directa", "--canal", "canal-openbot", "--clave", "ob-1",
            "--texto", PEDIDO,
        )
        motor("director", "cerrar-sesion", ob["encargo"], "--base", str(base))
        resultado = motor(
            "director", "resultado", ob["encargo"], "--base", str(base), "--texto", RESPUESTA + "\n",
        )
        exigir(resultado["estado"] == "terminado", "OpenBot no cerró con el resultado consultado")
        publicado = motor("director", "avisar", "--base", str(base))
        ob_aviso = next(item for item in publicado["avisos"] if item["encargo"] == ob["encargo"])
        parada = motor("director", "parar", ob["encargo"], "--base", str(base), "--clave", "parar-ob")
        exigir(parada.get("parada_ofrecida") is False, "parar de OpenBot se ofreció")
        evidencia["openbot"] = {
            "capacidades": {k: caps[k] for k in ("entrega", "consulta", "parada", "timeout_cancela", "skills", "publicacion_de_sesion")},
            "timeout_rc": corrida.returncode,
            "status_rc": estado.returncode,
            "aviso": ob_aviso["estado"],
            "parada": parada.get("motivo"),
            "doble": "el API HTTP es local y de ensayo. El CLI es el de la app. No acredita los agentes vivos de David.",
        }
        evidencia["dobles"].append(evidencia["openbot"]["doble"])

        salida = CONF / "flota" / "manifesto.toml"
        sembrado = motor(
            "flota", "sembrar", "--tabla", str(TABLA), "--salida", str(salida), "--generacion", "v6",
        )
        comparar = motor(
            "flota", "comparar", "--manifiesto", str(salida),
            "--izquierda", "dirigir", "--derecha", "dirigir-claw",
        )
        exigir(comparar["iguales"] is True, "Hermes y Claw no comparten la cadena de dirigir")
        proy = motor(
            "flota", "proyectar", "--manifiesto", str(salida),
            "--paquete", str(RAIZ / "paquetes" / "poteto"), "--home", str(home),
        )
        origen = json.loads(TABLA.read_text(encoding="utf-8"))
        proyectada = json.loads((home / "proyeccion" / "model-routing.source.json").read_text(encoding="utf-8"))
        exigir(proyectada["chains"] == origen["chains"], "la proyección cambió las cadenas")
        exigir(proyectada["profile_inheritance"] == origen["profile_inheritance"], "la proyección cambió la herencia")
        desired_repo = correr([sys.executable, "scripts/claw_models.py", "desired"], cwd=HERMES)
        desired_proy = correr(
            [sys.executable, "scripts/claw_models.py", "--source", str(home / "proyeccion" / "model-routing.source.json"), "desired"],
            cwd=HERMES,
        )
        exigir(desired_repo.returncode == 0 and desired_proy.returncode == 0, desired_proy.stderr)
        exigir(
            json.loads(desired_repo.stdout) == json.loads(desired_proy.stdout),
            "claw_models desired cambió con la proyección",
        )
        sys.path.insert(0, str(OPENCLAW / "scripts"))
        from sistema_agentes.contrato import validar

        flota = json.loads((home / "proyeccion" / "flota.json").read_text(encoding="utf-8"))
        hallazgos = validar(flota)
        exigir(hallazgos == [], "la proyección no cumple el contrato de flota: " + str(hallazgos))
        config_modelo = origen["chains"]["dirigir"][0]
        cli_modelo = evidencia["maquinas"]["mac"]["codex"].get("version") or "ausente"
        diferencia = motor("flota", "discrepancia", "--config", config_modelo, "--cli", cli_modelo)
        exigir(bool(diferencia["discrepancia"]), "la discrepancia del CLI no se vio")
        respaldo = motor(
            "flota", "respaldo", "--home", str(home), "--manifiesto", str(salida),
            "--funcion", "dirigir", "--modelo", "xai/grok-4.7", "--esfuerzo", "high",
            "--hasta", "2026-10-27T00:00:00Z", "--motivo", "cuota temporal de ensayo",
        )
        recargada = json.loads(
            correr(
                [sys.executable, "-c",
                 "import json,tomllib,sys; d=tomllib.load(open(sys.argv[1],'rb')); "
                 "fila=next(p for p in d['puesto'] if p['funcion']=='dirigir'); "
                 "print(json.dumps(fila['modelo']))",
                 str(home / "manifiesto.toml")],
            ).stdout
        )
        exigir(recargada[0] == origen["chains"]["dirigir"][0].split(" (", 1)[0], "el respaldo reescribió la cadena deseada")
        evidencia["politica"] = {
            "puestos": sembrado["puestos"],
            "dirigir_igual_claw": True,
            "poteto": proy["poteto"],
            "contrato": "valido",
            "discrepancia": diferencia["discrepancia"],
            "respaldo": respaldo["respaldo"],
            "doble_discrepancia": "codex --version es el CLI real de esta Mac. No es un proceso del plugin de Hermes o Claw.",
        }
        evidencia["dobles"].append(evidencia["politica"]["doble_discrepancia"])

        flota_home = home / "ensayo"
        flota_home.mkdir()
        evidencia["flota"] = preparar_flota(flota_home)
        exigir(evidencia["flota"]["trabajo"] != evidencia["flota"]["asignada"], "el trabajo activo cambió de generación")
        exigir(evidencia["flota"]["cortada"] == "cortado", "el corte no dejó la aplicación pendiente")
        exigir(evidencia["flota"]["rechazada"] == "rechazada", "la candidata inválida se aplicó")
        exigir(evidencia["flota"]["recuperada"] == "completo", "la recuperación no completó lo comprobado")
        exigir(evidencia["flota"]["reversa_otra"] == "nada", "la segunda reversa no fue vacía")
        exigir(evidencia["flota"]["marca_reaparecio"] is False, "la reversa arrancó el motor")
        return 0
    except (Fallo, OSError, json.JSONDecodeError, KeyError, subprocess.CalledProcessError) as exc:
        evidencia["fallo"] = str(exc)
        return 1
    finally:
        if api is not None:
            api.cerrar()
        matar(base)
        matar(home)
        restos = procesos(base) + procesos(home)
        evidencia["limpieza"] = {"procesos": restos}
        EVIDENCIA.parent.mkdir(parents=True, exist_ok=True)
        EVIDENCIA.write_text(json.dumps(evidencia, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        shutil.rmtree(base, ignore_errors=True)
        for gen in (home / "ensayo" / "generaciones").glob("*") if (home / "ensayo" / "generaciones").exists() else []:
            correr(["git", "-C", str(home / "ensayo" / "motor"), "worktree", "remove", "--force", str(gen)])
        shutil.rmtree(home, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
