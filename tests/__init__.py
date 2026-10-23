"""El banco de pruebas no lee el crontab, los plist ni `schtasks` reales del equipo al desplegar."""

from motor import daemon

_init = daemon.Daemon.__init__


def _sin_programadas_del_sistema(self, *a, **k):
    _init(self, *a, **k)
    self.cx.programadas_sistema = False


daemon.Daemon.__init__ = _sin_programadas_del_sistema
