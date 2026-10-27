---
name: poteto-mode
description: Paquete común de poteto para Hermes, Claw y OpenBot. Adapta las herramientas al runtime y no convierte el pedido en bloques.
---

# Poteto en el director

Lee las instrucciones del usuario antes que este paquete. Adapta los nombres de herramienta al archivo `runtimes/<runtime>.json` de este paquete.

No conviertas el pedido en bloques. No exijas campos Acepta ni un plan ejecutable para empezar. El texto y los archivos de David son el pedido.

Una tarea acotada se hace con los agentes del director. El motor entra cuando el trabajo debe continuar sin la conversación, cruzar máquinas o entregar en etapas. La vía que David nombró se respeta.

Al pasar de trabajo directo a durable, espera el cierre del ejecutor anterior y conserva un solo encargo. La vía directa también pasa las escrituras por la autorización del motor.

Si falla una comprobación, arregla el instrumento. No pidas al usuario que escriba el protocolo interno.
