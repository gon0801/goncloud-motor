"""El banco de pruebas no lee el crontab, los plist ni `schtasks` reales del equipo al desplegar.

La variable la heredan los subprocesos (daemon, aparte) que lanzan los tests e2e."""

import os

from .guarda_home import instalar

os.environ["MOTOR_PROGRAMADAS_SISTEMA"] = "0"

# La config Git que el banco arma por ambiente, además de limpiar la heredada.
_CONFIGURACION_BANCO = {
    "GIT_CONFIG_COUNT": "1",
    "GIT_CONFIG_KEY_0": "core.longpaths",
    "GIT_CONFIG_VALUE_0": "true",
}


def git_env_limpio() -> dict[str, str]:
    """Ambiente con el git del banco: sin la configuración Git del proceso que
    lo corre y con `core.longpaths` armado.

    Los repos de ensayo crean y empujan entre sí bajo el temporal que da el
    comprobador; un `GIT_CONFIG_*` ambiental (por ejemplo un `core.hooksPath`
    con hooks que rechazan pushes) hacía fallar cada push de sandbox, y sin
    `core.longpaths` el receive-pack no puede escribir objetos cuando el
    temporal profundo del comprobador deja cada ruta a menos de 20 caracteres
    del tope de Windows ("Filename too long"). Con este ambiente el git de
    las pruebas se comporta igual en cualquier máquina y a cualquier
    profundidad.
    """
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env.update(_CONFIGURACION_BANCO)
    return env


for _clave in [k for k in os.environ if k.startswith("GIT_")]:
    del os.environ[_clave]
os.environ.update(_CONFIGURACION_BANCO)
instalar()
