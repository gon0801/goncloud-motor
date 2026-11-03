# Modelo real en los SDK candidatos

Completado. Se ejecutaron los prefijos publicados en runtime.json de los candidatos, con perfiles y espacios de ensayo clonados fuera de las generaciones. No se instalaron otros runtimes ni se modificaron los recibos de instalación.

| SDK candidato | Generación | Resultado observado |
| --- | --- | --- |
| OpenClaw2026.10.8, Node24.22.1, Mini | 4bd9dac6f0a299f555f8fbfd80b7f0e6fc212372 | CANDIDATO_OK; dos llamadas read sin fallos; 5.302s |
| Hermes0.22.5, PMvenv y sourcecopiada, PC | b8596c2a347edaff7a034ec0f670b51ed5ffa77d | CANDIDATO_OK; dos llamadas read_file sin fallos; 23.688s |

Las duraciones son las registradas por cada SDK, no una comparación de rendimiento. Cada turno leyó poteto-mode y principle-prove-it-works desde su copia privada y respondió sin modificar archivos. Claw registró modelo solicitado grok-4.7 y responseModel efectivo grok-4.7-build. Hermes registró grok-4.7 y proveedor xai-oauth en su sesión nativa.

Grok4.7 fue un respaldo temporal autorizado para la sonda. La fuente v7 y sus cadenas deseadas se conservaron. Los SHA256 de las dos configuraciones fuente coinciden antes y después de la ejecución.

La cuenta OAuth existente se copió sólo dentro de cada máquina al almacén privado de ensayo. En Claw10.8, el JSON legacy exigió migración antes de aceptar una petición de modelo. Se corrigió el instrumento usando la API del SDK candidato plugin-sdk/provider-auth.js, función upsertAuthProfile con agentDir propio. Esa inicialización no llamó al modelo; después se obtuvo una sola respuesta acreditada. No se ejecutó doctor sobre la instalación operativa ni se inventó un esquema SQLite.

Hermes usó literalmente el Python de su PMvenv publicado, -m hermes_cli.main y PYTHONPATH apuntando a la sourcecopiada de esa generación. Los perfiles clonados deshabilitaron canales y automatismos operativos. La sonda de Hermes deshabilitó además plugins/MCP y limitó los turnos; acredita SDK, proveedor, cuenta y lectura de herramientas, sin repetir las cadenas de ingeniería ya evidenciadas en fase3.

Se eliminaron los perfiles, sus copias de autenticación y sus espacios temporales. No quedó gateway propio abierto. Los metadatos seleccionados, argumentos, recibos de runtime, modelos observados y comprobación de configuración fuente intacta están en candidatos-modelo-real.json. Ninguna credencial está en estos archivos.
