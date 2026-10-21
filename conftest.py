"""Conftest de la raíz: la batería se corre con `pytest` a secas (así lo llama el workflow de CI,
con el script de consola, sin PYTHONPATH ni CWD en sys.path); este archivo hace que pytest anteponga
la raíz del repo a sys.path y `import motor` resuelva SIEMPRE a esta copia, nunca a una instalación
externa que haya en el entorno (una copia sin .git hacía que `motor version` reportara "sin-git").
"""

import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parent
if str(RAIZ) in sys.path:
    sys.path.remove(str(RAIZ))
sys.path.insert(0, str(RAIZ))
