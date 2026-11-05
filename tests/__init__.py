"""El banco de pruebas no lee el crontab, los plist ni `schtasks` reales del equipo al desplegar.

La variable la heredan los subprocesos (daemon, aparte) que lanzan los tests e2e."""

import os

from .guarda_home import instalar

os.environ["MOTOR_PROGRAMADAS_SISTEMA"] = "0"


def git_env_limpio() -> dict[str, str]:
    """Ambiente sin la configuración Git del proceso que corre el banco.

    Los repos de ensayo crean y empujan entre sí; un `GIT_CONFIG_*` ambiental
    (por ejemplo un `core.hooksPath` con hooks de gobernanza que rechazan
    pushes a destinos no registrados) hacía fallar cada push de sandbox
    (init y commit pasaban, push salía 1). Sin `GIT_*` heredado, el git de
    las pruebas se comporta igual en cualquier máquina.
    """
    return {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}


for _clave in [k for k in os.environ if k.startswith("GIT_")]:
    del os.environ[_clave]
instalar()
