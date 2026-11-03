# Catálogos oficiales de modelos

El controlador lee catálogos sin ejecutar inferencias. La configuración deseada no acredita que una cuenta pueda usar un modelo: cada observación identifica su origen y alcance.

Fuentes implementadas:

| Proveedor | Origen | Alcance |
| --- | --- | --- |
| OpenAI | Codex app-server, operación model/list con la sesión local | Suscripción local |
| xAI | grok models con la sesión local | Suscripción local |
| Anthropic | Fila Claude API ID del catálogo oficial en Markdown | Modelo publicado, cuenta sin comprobar |
| Z.AI | Enlaces de modelos del índice oficial llms.txt | Modelo publicado, cuenta sin comprobar |
| OpenCode Go | Endpoint oficial de modelos | Modelo publicado, cuenta sin comprobar |
| Kimi | Sin lista oficial legible mediante la CLI instalada | Desconocido; no se interpreta como ausente |

No se utiliza models.dev como fuente oficial. La CLI de Kimi ofrece un catálogo de terceros y una lista de configuración; ninguno demuestra la disponibilidad de la cuenta. Su endpoint público coding/v1/models devolvió 401 en la comprobación. No se extrajeron credenciales para sustituir ese acceso.

## Integración

motor.flota.catalogos expone:

- descubrir_modelos(tabla, ejecutables=None, obtener=None, leer_texto=None, run=None): fuentes, modelos utilizados y candidatas. Los fallos de lectura quedan desconocidos; las capacidades legibles siguen disponibles.
- preparar_politica_modelos(tabla, descubrimiento, comprobar=None, hermes_repo=None): propuesta y recibos. Sin callback usa scripts/probe_route.py del plugin Hermes con la ruta de suscripción y un espacio temporal, sin escribir su cuarentena.
- publicar_politica_modelos(manifiesto, tabla, descubrimiento, comprobar=None, hermes_repo=None): valida que la tabla derive de la política vigente y actualiza esa política mediante cargar/guardar. Conserva herramientas, cuentas, skills, herencia, orden y esfuerzos.

Una candidata sólo reemplaza una versión posterior de la misma línea y proveedor. Se comprueban los esfuerzos usados mediante el adaptador real. La publicación requiere respuesta exacta, éxito, modelo y esfuerzo solicitados y un adaptador de suscripción conocido. Una cuota agotada o una sonda no disponible deja la candidata pendiente; conserva la versión válida. No se preparan comandos de prueba para el usuario.

Las dos proyecciones deben derivar de la tabla devuelta por la política publicada. El descubrimiento guardado es evidencia, no otra configuración deseada.

## Comprobaciones de esta entrega

En la Mac: catálogo Go con 38 modelos; Claude con 4 IDs; Z.AI con 3 IDs; Codex con 10 modelos y esfuerzos; Grok con 4 modelos. No hubo versiones posteriores de las líneas vigentes: v7 quedó intacta.

Se ejecutó la sonda predeterminada de xai/grok-4.7 en low usando el plugin y la suscripción existentes: confirmado, adaptador grok, respuesta exacta, 6.6 s. Ese recorrido sí gastó unos pocos tokens de la suscripción; la lectura de catálogos no los requiere. El espacio de sonda fue eliminado por probe_route.py.

Comprobación local con dobles declarados: sonda rechazada conserva tabla; sonda aprobada conserva esfuerzos y cadenas iguales; la publicación genera un manifiesto válido en un temporal. No sustituye la sonda real anterior ni la evidencia de los directores.

Fuentes primarias: [Codex app server](https://developers.openai.com/codex/app-server), [modelos de Claude](https://platform.claude.com/docs/en/about-claude/models/overview.md), [índice Z.AI](https://docs.z.ai/llms.txt) y [OpenCode Go](https://opencode.ai/docs/go/).
