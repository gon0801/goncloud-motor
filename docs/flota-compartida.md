# Una política, un publicador

La PC prepara y publica la política común mediante el controlador. El cron vigente de modelos se sustituirá por ese publicador al activar la fase5. La Mini importa la misma política y aplica las versiones fijadas. La autoridad de encargos y efectos permanece en la Mini; publicar configuración no concede autoridad para ejecutar efectos.

El publicador exporta un snapshot con el manifiesto TOML y el catálogo exacto de sus componentes preparados. No consulta latest otra vez al sincronizar una réplica. Cada componente debe coincidir con las herramientas de la política en nombre, canal, versión e integridad. Las fuentes admitidas son las oficiales del registro de distribuidores.

El snapshot incluye SHA-256 del TOML y de todo el contenido publicado. No contiene credenciales: sólo las referencias de cuenta ya presentes en la política y los metadatos públicos de los artefactos. El transporte entre máquinas utiliza el acceso autorizado existente; un digest comprueba integridad, no sustituye la autenticación del transporte.

## Publicación y réplica

motor.flota.compartida expone exportar(home, salida), importar(home, archivo) y leer_fuente(home). Los tres usan el candado común de flota.

El publicador mantiene su manifiesto normal. Una réplica tiene un único archivo canónico: fuente-compartida.json. Contiene el snapshot, modo replica, origen y hash. Su manifiesto.toml es una proyección: leer_fuente la regenera bajo el mismo candado antes de usarla. Si el proceso cae después de importar el snapshot y antes de escribir la proyección, la próxima lectura la recupera.

La importación valida estructura, hashes, política y catálogo antes de sustituir el archivo canónico. Una entrada inválida conserva los bytes vigentes. La réplica no puede exportar ni publicar cambios de modelos; sólo aplica las versiones importadas y registra sus observaciones. La activación de los escritores operativos sigue reservada a fase5.

## Comprobación realizada

En un temporal propio se exportó e importó una política con metadatos reales de Codex0.164.0 obtenidos del canal oficial. Cadenas, esfuerzos, referencias y hashes coincidieron. Un digest incorrecto conservó los bytes de la fuente y de la proyección. Una proyección incompleta se regeneró desde el archivo canónico. El intento de republicar desde la réplica fue rechazado. No se tocaron instalaciones operativas ni credenciales.
