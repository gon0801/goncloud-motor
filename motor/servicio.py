"""Arranque al prender la máquina y supervisor que relanza el daemon.

- Mac y Mini: LaunchDaemon con UserName (corre aunque nadie inicie sesión). Credenciales por
  `secretos.env`, no por llavero. En la MacBook con FileVault, nada arranca hasta escribir la
  contraseña de arranque; en cuanto se escribe, esto sigue solo.
- PC: tarea programada al arrancar, aunque nadie inicie sesión (ver plataforma.windows.tarea_xml).
- El supervisor relanza el daemon si muere; código 75 = "me actualicé, reiníciame".
"""

from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

from .plataforma.windows import tarea_xml
from .version import espera_antes_de_relanzar


# El PATH con el que launchd lanza el servicio (y por tanto los agentes); `doctor --como-servicio` lo reutiliza.
PATH_SERVICIO = "/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin"


def plist_launchd(
    usuario: str,
    python: str,
    home: str,
    raiz: str,
    etiqueta: str = "com.goncloud.motor",
) -> str:
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<!-- Va en /Library/LaunchDaemons/{etiqueta}.plist: arranca al prender, sin sesión iniciada. -->
<plist version="1.0">
<dict>
  <key>Label</key><string>{etiqueta}</string>
  <key>UserName</key><string>{usuario}</string>
  <key>ProgramArguments</key>
  <array><string>{python}</string><string>-m</string><string>motor</string><string>servicio</string><string>--home</string><string>{home}</string></array>
  <key>WorkingDirectory</key><string>{raiz}</string>
  <key>EnvironmentVariables</key><dict><key>HOME</key><string>/Users/{usuario}</string><key>PATH</key><string>{PATH_SERVICIO}</string></dict>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>StandardOutPath</key><string>{home}/servicio.log</string>
  <key>StandardErrorPath</key><string>{home}/servicio.log</string>
</dict>
</plist>
"""


def instalable(maquina: str, usuario: str, python: str, home: str, raiz: str) -> str:
    if maquina.lower() == "pc":
        return tarea_xml(
            usuario, python, home, raiz
        )  # sin carpeta de trabajo, `-m motor` no encuentra el paquete
    return plist_launchd(usuario, python, home, raiz)


AGENTES_SERVICIO = ("codex", "grok")


def _ruta_servicio(agente: str, respaldos: tuple[str, ...]) -> list[str]:
    if agente not in AGENTES_SERVICIO:
        raise ValueError(
            "el servicio de encargos necesita una ruta de agentes "
            "autorizada (--agente codex|grok)"
        )
    for respaldo in respaldos:
        if respaldo not in AGENTES_SERVICIO:
            raise ValueError(f"respaldo de agentes no autorizado: {respaldo}")
    return [agente, *respaldos]


def plist_encargos(
    usuario: str,
    python: str,
    home: str,
    raiz: str,
    etiqueta: str = "com.goncloud.motor-encargos",
    agente: str = "",
    respaldos: tuple[str, ...] = (),
) -> str:
    ruta = _ruta_servicio(agente, respaldos)
    argumentos = "".join(
        f"<string>--agente</string><string>{ruta[0]}</string>"
        if i == 0
        else f"<string>--respaldo</string><string>{nombre}</string>"
        for i, nombre in enumerate(ruta)
    )
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<!-- Va en /Library/LaunchDaemons/{etiqueta}.plist: arranca al prender, sin sesión iniciada. -->
<plist version="1.0">
<dict>
  <key>Label</key><string>{etiqueta}</string>
  <key>UserName</key><string>{usuario}</string>
  <key>ProgramArguments</key>
  <array><string>{python}</string><string>-m</string><string>motor</string><string>supervisor</string><string>--base</string><string>{home}</string>{argumentos}</array>
  <key>WorkingDirectory</key><string>{raiz}</string>
  <key>EnvironmentVariables</key><dict><key>HOME</key><string>/Users/{usuario}</string><key>PATH</key><string>{PATH_SERVICIO}</string></dict>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>StandardOutPath</key><string>{home}/servicio-encargos.log</string>
  <key>StandardErrorPath</key><string>{home}/servicio-encargos.log</string>
</dict>
</plist>
"""


def instalable_encargos(
    maquina: str,
    usuario: str,
    python: str,
    home: str,
    raiz: str,
    agente: str = "",
    respaldos: tuple[str, ...] = (),
) -> str:
    ruta = _ruta_servicio(agente, respaldos)
    if maquina.lower() == "pc":
        sufijo = f" --agente {ruta[0]}" + "".join(
            f" --respaldo {nombre}" for nombre in ruta[1:]
        )
        return tarea_xml(
            usuario,
            python,
            home,
            raiz,
            argumentos=f'-m motor supervisor --base "{home}"{sufijo}',
        )
    return plist_encargos(
        usuario, python, home, raiz, agente=agente, respaldos=respaldos
    )


def vincular_base(base: Path, flota_home: Path, canal: Path | None = None) -> dict:
    import json

    base = Path(base)
    flota_home = Path(flota_home)
    if not (flota_home / "generaciones").is_dir():
        raise ValueError(f"la flota vinculada no existe en {flota_home}")
    base.mkdir(parents=True, exist_ok=True)
    (base / "flota-home.json").write_text(
        json.dumps({"home": str(flota_home)}, ensure_ascii=False),
        encoding="utf-8",
    )
    salida = {"flota_home": str(flota_home)}
    if canal is not None:
        doc = json.loads(Path(canal).read_text(encoding="utf-8"))
        if not isinstance(doc, dict) or not doc:
            raise ValueError("el canal vinculado debe ser un objeto con canales")
        (base / "canal.json").write_text(
            json.dumps(doc, ensure_ascii=False), encoding="utf-8"
        )
        salida["canales"] = sorted(doc)
    return salida


def supervisor(home: Path, intervalo: float = 10.0) -> int:
    from .control import leer

    hold = leer(home)
    if hold is not None and hold.cause:
        print(
            f"motor: la retención no se puede leer ({hold.cause}); el daemon arranca igual",
            file=sys.stderr,
        )
    while True:
        inicio = time.time()
        r = subprocess.run(
            [
                sys.executable,
                "-m",
                "motor",
                "daemon",
                "--home",
                str(home),
                "--intervalo",
                str(intervalo),
            ]
        )
        # 75 = "me actualicé, reiníciame": relanza ya (version.espera_antes_de_relanzar); si murió, en 5 s
        time.sleep(espera_antes_de_relanzar(r.returncode, time.time() - inicio))
