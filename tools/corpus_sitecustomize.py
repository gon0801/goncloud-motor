import os

if "MOTOR_CORPUS_DIR" in os.environ:
    from tools.corpus_pytest import instalar

    instalar()
