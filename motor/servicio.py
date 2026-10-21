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
PATH_SERVICIO = "/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin"


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


def supervisor(home: Path, intervalo: float = 10.0) -> int:
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
