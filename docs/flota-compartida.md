# Una política, un publicador

La PC prepara y publica la política común mediante el controlador. El cron vigente de modelos se sustituirá por ese publicador al activar la fase5. La Mini importa la misma política y aplica las versiones fijadas. La autoridad de encargos y efectos permanece en la Mini; publicar configuración no concede autoridad para ejecutar efectos.

El publicador exporta un snapshot con el manifiesto TOML y el catálogo exacto de sus componentes preparados. No consulta latest otra vez al sincronizar una réplica. Cada componente debe coincidir con las herramientas de la política en nombre, canal, versión e integridad. Las fuentes admitidas son las oficiales del registro de distribuidores.

El snapshot incluye SHA-256 del TOML y de todo el contenido publicado. No contiene credenciales: sólo las referencias de cuenta ya presentes en la política y los metadatos públicos de los artefactos. El transporte entre máquinas utiliza el acceso autorizado existente; un digest comprueba integridad, no sustituye la autenticación del transporte.

## Qué lleva el snapshot

Esquema v1: manifiesto, su SHA-256, catálogo, generación y publicador, más `snapshot_sha256` sobre todo eso. Es el snapshot de siempre y se publica así cuando no hay checkout de goncloud-hermes.

Esquema v2: v1 más `claw_routing`, con `contenido` (el texto exacto de `claw-routing.json`), `sha256` de esos bytes y `revision` (el HEAD del checkout de donde se leyó). `snapshot_sha256` también lo cubre. El archivo se lee de la revisión confirmada del checkout, no del árbol de trabajo: lo que no se confirmó no se publica.

La réplica necesita el `claw-routing.json` porque `_reutilizar_config` copia `config/` de la generación vigente y de ahí derivaba `perfiles/claw/openclaw.json`: un cambio en goncloud-hermes nunca llegaba. El `claw_models.py` ya viene en esa copia, así que no se publica.

Esquema v3: v2 más `publicacion`, el número de la publicación. Lo asigna el publicador y solo crece. `claw_routing` va siempre y es nulo cuando no hubo checkout. `exportar` publica solo v3.

El código que entiende v2 sigue aceptando v1 y se comporta como antes. El código anterior a v2 rechaza un v2 entero (estructura inválida), no lo aplica a medias. Igual el anterior a v3 con un v3: se despliegan primero las réplicas o toda la flota a la vez.

## Orden de las publicaciones

`generacion` no ordena: confirmar un modelo la vuelve un hash, y el catálogo o el routing cambian sin tocarla. El orden lo da `publicacion`.

El contenido canónico es todo lo que la réplica aplica más quién lo publica: `publicador`, `generacion`, `manifiesto`, `sha256`, `catalogo` (con la versión, el canal y la integridad de cada herramienta) y `claw_routing`. No incluye `schema` ni `publicacion`. `snapshot_sha256` cubre el contenido canónico, `schema` y `publicacion`.

El publicador guarda en `ultima-publicacion.json` de su home el último número y el SHA-256 del contenido canónico. Cada `exportar` reusa el número si el contenido es el mismo y lo sube en uno si cambió. Escribe ese archivo antes que el snapshot, para que una caída entre los dos no le dé un número ya entregado a otro contenido.

La réplica compara contra el `publicacion` de su `fuente-compartida.json` antes de escribir:

| Snapshot recibido | Réplica sin número | Réplica con número N |
| --- | --- | --- |
| v1 o v2, sin número | se aplica | se rechaza: el publicador no está actualizado |
| v3 con número menor que N | se aplica | se rechaza sin escribir |
| v3 con N y el mismo `snapshot_sha256` | se aplica | `sin_cambios`, no reescribe nada |
| v3 con N y otro `snapshot_sha256` | se aplica | se rechaza: dos contenidos con el mismo número |
| v3 con número mayor que N | se aplica | se aplica |

El número vigente se lee sin validar la fuente, así una fuente que una versión nueva ya no acepta sigue ordenando y la cura la siguiente publicación. Un rechazo lanza `PublicacionRechazada` con la causa, y `ops/flota_ciclo.py --replica` sale 1 sin preparar nada.

Si el publicador pierde su home, vuelve a numerar desde 1 y las réplicas lo rechazan. Nada se resetea solo. El rechazo trae la orden para seguir, que se corre en el publicador: `python -m motor flota publicar --home <flota> --salida <snapshot> --desde <N+1>`. Las corridas siguientes continúan desde ahí.

## Orden de trabajo

Publicadora:

1. `git pull` en el checkout de goncloud-hermes.
2. `motor flota publicar --home <flota> --salida <archivo> [--repo-hermes <checkout>]`.

Sin `--repo-hermes` busca `goncloud-hermes` y `goncloud-hermes-encargos-3-4` junto al motor y `<padre>/motor-config/hermes`. Si no encuentra ninguno publica v1 y la salida JSON lo dice en `claw_routing.motivo`. Un `--repo-hermes` explícito que no es un checkout git, o que no tiene `claw-routing.json` en HEAD, falla. La salida lleva `claw_routing` con `incluido`, `revision` y `sha256`.

Réplica:

1. `motor flota sincronizar --home <flota> --archivo <archivo>`.
2. `motor flota preparar --home <flota>`.

`sincronizar` valida el campo (estructura, SHA-256, JSON con `agents`) y lo guarda en `fuente-compartida.json`. Un contenido alterado se rechaza y los bytes vigentes quedan. `preparar` copia `config/` de la vigente, escribe el `claw-routing.json` publicado en `config/hermes/` de la generación nueva y recién entonces deriva `perfiles/claw`. La revisión de origen queda en `runtime.json` y en el recibo de preparación (`claw_routing`). El hash del archivo entra en `runtime["archivos"]`. La identidad de la generación incluye el `sha256` del routing: un routing nuevo con el mismo manifiesto es otra generación, no una reutilizada.

## Publicación y réplica

motor.flota.compartida expone exportar(home, salida), importar(home, archivo) y leer_fuente(home). Los tres usan el candado común de flota.

El publicador mantiene su manifiesto normal. Una réplica tiene un único archivo canónico: fuente-compartida.json. Contiene el snapshot, modo replica, origen y hash. Su manifiesto.toml es una proyección: leer_fuente la regenera bajo el mismo candado antes de usarla. Si el proceso cae después de importar el snapshot y antes de escribir la proyección, la próxima lectura la recupera.

La importación valida estructura, hashes, política y catálogo antes de sustituir el archivo canónico. Una entrada inválida conserva los bytes vigentes. La réplica no puede exportar ni publicar cambios de modelos; sólo aplica las versiones importadas y registra sus observaciones. La activación de los escritores operativos sigue reservada a fase5.

## Comprobación realizada

En un temporal propio se exportó e importó una política con metadatos reales de Codex0.164.0 obtenidos del canal oficial. Cadenas, esfuerzos, referencias y hashes coincidieron. Un digest incorrecto conservó los bytes de la fuente y de la proyección. Una proyección incompleta se regeneró desde el archivo canónico. El intento de republicar desde la réplica fue rechazado. No se tocaron instalaciones operativas ni credenciales.
