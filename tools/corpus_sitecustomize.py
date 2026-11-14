import os

if "MOTOR_CORPUS_DIR" in os.environ:
    import builtins
    import sys

    _importar = builtins.__import__
    _registro = os.path.realpath(
        os.path.join(
            os.environ.get("MOTOR_CORPUS_REPO", os.path.dirname(__file__)),
            "motor", "encargos", "registro.py",
        )
    )
    _instalado = False

    def _capturar(name, globals=None, locals=None, fromlist=(), level=0):
        global _instalado
        modulo = _importar(name, globals, locals, fromlist, level)
        if _instalado or level or not (
            name == "motor.encargos.registro"
            or name == "motor.encargos" and fromlist and "registro" in fromlist
        ):
            return modulo
        real = sys.modules.get("motor.encargos.registro")
        plugin = sys.modules.get("tools.corpus_pytest")
        if (real is not None
            and os.path.realpath(getattr(real, "__file__", "")) == _registro
            and (plugin is None or hasattr(plugin, "instalar"))):
            _instalado = True
            from tools.corpus_pytest import instalar

            instalar()
        return modulo

    builtins.__import__ = _capturar
