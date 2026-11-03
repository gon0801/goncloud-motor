"""Instrumento de ensayo aislado para promoción desde directores reales.

Se ejecuta en una copia propia de motor; no toca servicios ni perfiles operativos.
Los turnos reales del director invocan preparar-directa y después los comandos
públicos cerrar-sesion/promover/ver. Los resultados no se fabrican en este archivo.
"""

from __future__ import annotations

import argparse
import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ))


def preparar_directa(base: Path, director: str, canal: str) -> dict:
    from motor import plataforma
    from motor.encargos.registro import Registro, exigir_base
    from motor.integraciones.directores import encargar, hora

    base = exigir_base(base)
    workspace = base.parent / (director + "-directa")
    workspace.mkdir(exist_ok=True)
    anterior = workspace / "encargo.json"
    if anterior.exists():
        resultado = json.loads(anterior.read_text(encoding="utf-8"))
        resultado["reutilizado"] = True
        return resultado
    worker = workspace / "escritor.py"
    worker.write_text(
        "from pathlib import Path\nimport time\n"
        "while True:\n Path('actividad-directa.txt').write_text(str(time.time()))\n time.sleep(.1)\n",
        encoding="utf-8",
    )
    proc = subprocess.Popen(
        [sys.executable, str(worker)],
        cwd=workspace,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        **plataforma.kwargs_grupo(),
    )
    ident = {
        "pid": proc.pid,
        "inicio": plataforma.hora_inicio(proc.pid),
        "boot_id": plataforma.boot_id(),
        "host": socket.gethostname(),
        "sesion": director + "-final34",
    }
    registro = Registro(base)
    try:
        resultado = encargar(
            registro,
            "Escribe terminado.txt con la frase entrega final comprobada.",
            director + "-promocion-final34",
            hora(),
            director,
            canal,
            "directa",
            identidad=ident,
        )
    except BaseException:
        plataforma.matar_proc(ident)
        raise
    finally:
        registro.close()
    for _ in range(100):
        if (workspace / "actividad-directa.txt").exists():
            break
        time.sleep(0.03)
    resultado["identidad"] = ident
    resultado["actividad_real"] = (workspace / "actividad-directa.txt").exists()
    (workspace / "encargo.json").write_text(
        json.dumps(resultado, ensure_ascii=False), encoding="utf-8"
    )
    return resultado


def _json_event(raw: str | None, compressed: bytes | None) -> dict:
    if raw is None:
        node = Path.home() / ".openclaw/tools/node/bin/node"
        result = subprocess.run(
            [
                str(node),
                "-e",
                "const z=require('node:zlib');const fs=require('node:fs');process.stdout.write(z.zstdDecompressSync(fs.readFileSync(0)))",
            ],
            input=compressed,
            capture_output=True,
            check=True,
        )
        raw = result.stdout.decode("utf-8")
    return json.loads(raw)


def recibo_nativo(director: str, aviso: str) -> str:
    import sqlite3

    raiz = Path.home() / "ensayo-final34"
    marker = "motor-aviso:" + aviso
    if director == "hermes":
        path = raiz / "hermes-home/state.db"
        conn = sqlite3.connect("file:" + str(path) + "?mode=ro", uri=True)
        try:
            row = conn.execute(
                "select session_id,id,message_uid from messages where role='assistant' and content like ? order by id desc limit 1",
                ("%" + marker + "%",),
            ).fetchone()
            return (
                ("hermes:" + str(row[0]) + ":" + str(row[2] or row[1])) if row else ""
            )
        finally:
            conn.close()
    path = (
        Path.home() / ".openclaw-ensayo-final34/agents/main/agent/openclaw-agent.sqlite"
    )
    conn = sqlite3.connect("file:" + str(path) + "?mode=ro", uri=True)
    try:
        rows = conn.execute(
            "select event_json,event_zstd from transcript_events where session_id=? order by seq desc",
            ("ensayo-final34-promocion-2",),
        )
        for raw, compressed in rows:
            event = _json_event(raw, compressed)
            message = event.get("message") or {}
            if message.get("role") == "assistant" and marker in json.dumps(message):
                return "claw:ensayo-final34-promocion-2:" + str(event["id"])
        return ""
    finally:
        conn.close()


def canal_nativo(
    base: Path, director: str, aviso: str, encargo: str, consulta: bool
) -> dict:
    if director == "hermes" and os.name != "nt":
        command = [
            "ssh",
            "-o",
            "BatchMode=yes",
            "pc",
            "py",
            "-3",
            "C:/Users/ehven/ensayo-final34/src/ops/ensayo_directores_final.py",
            "conciliar-canal" if consulta else "canal",
            "--base",
            "C:/Users/ehven/ensayo-final34/hermes-base",
            "--director",
            "hermes",
            "--aviso",
            aviso,
            "--encargo",
            encargo,
        ]
        result = subprocess.run(
            command,
            input=sys.stdin.read(),
            capture_output=True,
            text=True,
            timeout=25,
            check=True,
        )
        return json.loads(result.stdout)
    recibo = recibo_nativo(director, aviso)
    if recibo:
        return {"recibo": recibo}
    if consulta:
        if director == "hermes":
            import sqlite3

            from motor import plataforma

            raiz = Path.home() / "ensayo-final34"
            envio = raiz / "avisos-nativos" / (aviso + ".envio.json")
            if envio.exists():
                anterior = json.loads(envio.read_text(encoding="utf-8"))
                rechazo_cli = False
                log = raiz / "avisos-nativos" / (aviso + ".log")
                if log.exists():
                    for linea in log.read_text(encoding="utf-8").splitlines():
                        try:
                            evento = json.loads(linea)
                        except ValueError:
                            continue
                        if (
                            evento.get("type") == "result"
                            and evento.get("exit_code") == 2
                            and not evento.get("session_id")
                        ):
                            rechazo_cli = True
                conn = sqlite3.connect(
                    "file:" + str(raiz / "hermes-home/state.db") + "?mode=ro", uri=True
                )
                try:
                    recibidos = conn.execute(
                        "select count(*) from messages where role='user' and content like ?",
                        ("%motor-aviso:" + aviso + "%",),
                    ).fetchone()[0]
                finally:
                    conn.close()
                if (
                    not recibidos
                    and not plataforma.pid_vivo(int(anterior["pid"]))
                    and (rechazo_cli or not (raiz / "hermes-home/gateway.pid").exists())
                ):
                    return {"ausente": True}
        return {"estado": "incierto"}
    raiz = Path.home() / "ensayo-final34"
    canal = raiz / "avisos-nativos"
    canal.mkdir(exist_ok=True)
    prompt = canal / (aviso + ".txt")
    prompt.write_text(
        "El trabajo ya termino. Este es el aviso al mismo chat de origen. "
        "No ejecutes herramientas ni repitas el trabajo. Responde solamente: "
        "Recibido motor-aviso:" + aviso + "\nResultado: " + sys.stdin.read(),
        encoding="utf-8",
    )
    env = os.environ.copy()
    if director == "hermes":
        env["HERMES_HOME"] = str(raiz / "hermes-home")
        env["HERMES_PROFILES_DIR"] = str(raiz / "hermes-home/profiles")
        env["PYTHONIOENCODING"] = "utf-8"
        session = json.loads((raiz / "sesion-hermes.json").read_text())["session_id"]
        command = [
            str(Path.home() / "AppData/Local/hermes/bin/hermes.cmd"),
            "chat",
            "--query-file",
            str(prompt),
            "--resume",
            session,
            "--format",
            "stream-json",
        ]
    else:
        command = [
            str(Path.home() / ".openclaw/bin/openclaw"),
            "--profile",
            "ensayo-final34",
            "agent",
            "--local",
            "--agent",
            "main",
            "--message-file",
            str(prompt),
            "--model",
            "xai/grok-4.7",
            "--session-id",
            "ensayo-final34-promocion-2",
            "--timeout",
            "100",
            "--json",
        ]
    with (canal / (aviso + ".log")).open("w", encoding="utf-8") as out:
        child = subprocess.Popen(
            command,
            env=env,
            stdout=out,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            start_new_session=os.name != "nt",
        )
    (canal / (aviso + ".envio.json")).write_text(
        json.dumps({"pid": child.pid, "encargo": encargo}), encoding="utf-8"
    )
    if os.name == "nt":
        # El cliente debe seguir dentro de la sesión SSH hasta que el gateway lo acepte.
        # Al soltarlo en background, Windows cerraba el proceso sin entregar el aviso.
        child.wait(timeout=240)
    # La respuesta se pierde deliberadamente: la conciliación deberá leer el chat real.
    return {"estado": "incierto"}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("accion", choices=("preparar-directa", "canal", "conciliar-canal"))
    ap.add_argument("--base", required=True, type=Path)
    ap.add_argument("--director", required=True, choices=("hermes", "claw", "auto"))
    ap.add_argument("--canal", default="ensayo-final34")
    ap.add_argument("--aviso", default="")
    ap.add_argument("--encargo", default="")
    ap.add_argument("aviso_posicional", nargs="?")
    args = ap.parse_args()
    if args.accion == "preparar-directa":
        result = preparar_directa(args.base, args.director, args.canal)
    else:
        aviso = args.aviso or args.aviso_posicional or os.environ["MOTOR_AVISO_ID"]
        encargo = args.encargo or os.environ.get("MOTOR_AVISO_ENCARGO", "")
        if args.director == "auto":
            from motor.encargos.registro import Registro

            registro = Registro(args.base)
            try:
                args.director = registro.fold(encargo).director
            finally:
                registro.close()
        result = canal_nativo(
            args.base, args.director, aviso, encargo, args.accion == "conciliar-canal"
        )
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
