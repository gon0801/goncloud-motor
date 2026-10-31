"""Qué aislamiento tiene el host que corre la batería. El motor solo aísla con AppContainer
(Windows) o con sandbox-exec (macOS): en Linux `aislar` levanta RuntimeError y las pruebas
que lanzan un trabajador real no pueden correr. La jaula de bubblewrap es de WSL y la prueba
`tests.test_jaula_linux`."""

import os
from pathlib import Path

HAY_AISLAMIENTO = os.name == "nt" or Path("/usr/bin/sandbox-exec").is_file()
SIN_AISLAMIENTO = "el motor solo aísla con AppContainer o sandbox-exec; este host no tiene ninguno"
