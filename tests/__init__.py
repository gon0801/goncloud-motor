"""El banco de pruebas no lee el crontab, los plist ni `schtasks` reales del equipo al desplegar.

La variable la heredan los subprocesos (daemon, aparte) que lanzan los tests e2e."""

import os

os.environ["MOTOR_PROGRAMADAS_SISTEMA"] = "0"
