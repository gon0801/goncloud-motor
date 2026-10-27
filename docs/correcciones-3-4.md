# Correcciones de las fases 3 y 4. Entrega para revisión de Codex

Ejecutor: Muse Code con muse-wonder-1.0 en la Mac, con dos trabajadores
poteto-agent de la misma suscripción autorizada. No se usó Cursor ni API de
pago. Codex revisa esta entrega. Sin push, sin merge y sin tocar servicios
vivos.

## Repos y SHA finales

| Repo | Ruta | Rama | Base revisada | SHA final |
| --- | --- | --- | --- | --- |
| Motor | /Users/dn/dev/goncloud-motor-encargos-3-4 | feat/encargos-3-4 | 235ef8a | c7a780e, más este informe |
| Hermes | /Users/dn/dev/goncloud-hermes-encargos-3-4 | feat/encargos-3-4 | f3bef23 | eb06573 |
| Conf | /Users/dn/dev/goncloud-motor-conf-encargos-3-4 | feat/encargos-3-4 | 45f6187 | f02bd82 |
| OpenClaw | /Users/dn/dev/goncloud-openclaw-encargos-3-4 | feat/encargos-3-4 | 75d5135 | 86a3dce |

El worktree de OpenClaw es nuevo y aislado. Plans.md con cambios ajenos quedó
intacto en /Users/dn/dev/goncloud-openclaw.

## Los siete hallazgos

1. Instrucciones distribuidas. La skill que instala Hermes ya no es la vieja.
   [skills/motor/SKILL.md de Hermes](hermes:skills/motor/SKILL.md) permite la
   tarea acotada directa, manda al motor lo durable, respeta la vía expresa y
   trata un plan externo como contenido del pedido. No exige plan, validación
   previa ni palabra de aceptación. Las tres copias de OpenClaw recibieron la
   misma adaptación. El paquete poteto citado es el completo de Hermes en
   shared/sistema-agentes/poteto, no el resumen de 16 líneas del motor.
2. Promoción y terminación canónicas. promover y resultado_directo viajan a
   la autoridad con la identidad estable existente. La autoridad aplica sin
   reenviar. Una respuesta perdida se concilia con la misma clave. Sin
   autoridad alcanzable queda un pendiente durable y no se concede la
   escritura local. Evidencia en
   evidence/correcciones-3-4/salida-promocion.txt: tras promover, trabajador
   y autoridad quedan en durable, y la retransmisión no duplica.
3. Cierre observado del ejecutor. La sesión directa guarda pid, inicio,
   boot_id, host y sesión. cerrar-sesion detiene y observa el árbol real.
   Promover rechaza con el ejecutor vivo y acepta tras verlo ausente. En la
   misma evidencia, un sleep real quedó muerto tras cerrar-sesion. Sin
   identidad no se acredita cierre ni terminación.
4. Canal de origen. drenar usa el transporte configurado en canal.json por
   canal, con comando de envío y comando de conciliación. El recibo es el
   externo que devuelve el canal. Sin transporte, el aviso queda local y
   pendiente, sin recibo fabricado. Un corte después del envío deja el aviso
   incierto y el siguiente drenado concilia antes de reenviar. El supervisor
   de encargos drena tras barrer, así el aviso sobrevive al cierre del turno
   y al reinicio del conector. Evidencia en salida-canal.txt, con un canal
   por subproceso real declarado como doble de transporte.
5. Proyección transaccional. publicar_proyeccion prepara y valida en un
   directorio temporal y sustituye solo tras comprobar. La reproducción de
   la revisión ahora conserva la generación y flota.json anteriores.
6. Reversa. aplicar registra como anterior la generación realmente aplicada.
   La reversa usa el recibo completo de instalación y, si no, el pendiente.
   No importa ni ejecuta el motor roto. Una candidata mala conserva la
   generación vigente. Hay prueba nueva de instalar, borrar y revertir por
   ser uno de los cuatro casos que la exigen.
7. Comprobadores reales. El controlador descubre comprobar.py o, en su
   defecto, el paquete real del componente. Para el motor ejecuta su CLI y
   compila el paquete. Aplicar un clon del propio repo ya no se rechaza por
   un comprobar.py inexistente. La generación asignada se anota al lanzar
   el encargo cuando la base declara su home de flota.

## Política v7

La fuente es model-routing.source.json de goncloud-hermes en c7f5bb1. Hermes
la incorpora tal cual en esta rama. Las cadenas coinciden puesto por puesto
con esa fuente, incluida DIRIGIR con GPT-6.1 Sol medium y DeepSeek Flash en
low. El manifiesto de Conf se sembró desde esa tabla con el generador del
motor. La prueba de perfiles de Hermes fallaba en la base por esperar la
cadena v6 de dirigir. Se corrigió esa expectativa y ahora pasa completa.

## Comprobaciones reales corridas

- Motor: python3 -m unittest tests.test_reversa_flota
  tests.test_encargo_motor06 -q. Resultado: 7 pruebas, OK.
- Motor: python3 ops/ensayo_fases_3_4.py. Resultado: rc 0, sin procesos
  restos. El ensayo ya no siembra el manifiesto dentro del repo de Conf.
- Hermes: python3 -m pytest tests/test_configure_team_tabla_v6.py -q.
  Resultado: 22 pasadas y 130 subpruebas. En la base f3bef23 fallaban 5.
- Reproducciones de la revisión para los hallazgos 2, 5, 6 y 7 en
  evidence/correcciones-3-4. Todas dan el resultado corregido.
- Mini por SSH, solo lectura: /Users/gon/.local/bin/python3 es 3.12.15,
  OpenClaw 2026.10.7 y Muse 1.4.13 presentes. No se concluyó falta de
  tomllib por el Python 3.9 del sistema.
- PC por SSH, solo lectura: Hermes Agent v0.22.5, codex, claude y grok
  presentes en sus rutas instaladas.
- Mac: muse exec respondió en contexto headless con la credencial local.
  OpenBot real respondió su ayuda. Su list dio exit 5 porque la app no está
  corriendo y no hay agente con Local scripts. No se arrancó la app viva.

## Dobles declarados y pendientes

El ensayo de fases usa un agente de ensayo, un API HTTP local y comandos de
canal por subproceso. Esos dobles prueban el código del motor. Los turnos
vivos de Hermes, Claw y OpenBot con las skills nuevas se demuestran en la
sección de recorridos de la ronda 3, con Grok como respaldo declarado por
falta de cuota de Anthropic y ChatGPT. Instalar las skills en los perfiles
vivos sigue siendo activación de fase 5. El modelo efectivo dentro de un
plugin de un director vivo no se demostró. OpenBot sigue sin ofrecer
parada. Un timeout suyo no cancela.

## Ronda 2. Correcciones a la segunda revisión

- El aviso incierto se persiste antes de enviar. Un fallo de envío o de
  conciliación nunca acredita ausencia. Solo una ausencia explícita o un
  recibo permiten cerrar el aviso. La reproducción de la ronda da una sola
  entrega tras el reintento.
- Una identidad nueva invalida el cierre de la anterior. La sesión queda
  activa sin cierre observado y promover rechaza con el ejecutor vivo.
- El supervisor consume pendientes-autoridad en cada paso. Tras reiniciarlo,
  trabajador y autoridad quedan en durable sin pendientes. director ver
  sincroniza y muestra durable también desde otra base.
- El publicador consume el paquete poteto completo con poteto.v1.json y
  skills/poteto-mode/SKILL.md. Valida cada JSON distribuido antes de
  sustituir. Un runtime corrupto conservó la generación A.
- La generación se fija antes de construir el lanzamiento. El trabajo
  queda anotado con su proceso real. Aplicar otra generación con ese
  trabajo vivo devuelve ocupada y el proceso siguió leyendo buena. Una
  anotación sin proceso vivo no bloquea la aplicación.
- Aplicar verifica que un worktree reutilizado corresponde a su SHA y
  está limpio. Los AGENTS.md de implementer, reviewer, adversary y
  verifier de Claw ya no ordenan motor valida ni motor entrega.
- El primer intento aislado de OpenClaw no corrió por autenticación y
  quedó superado por el recorrido real de la ronda 3.

## Ronda 3. Recorridos reales aislados intentados

- Claw en Mini. Recorrido real completado en perfil aislado propio
  ensayo34d con la skill nueva. Claude quedó sin cuota semanal en la
  Mini, así que el turno corrió con el respaldo autorizado xai/grok-4.7
  y el login OAuth quedó solo en el perfil de ensayo. El modelo
  observado por la traza fue xai/grok-4.7 con ruta terminal
  xai/grok-4.7-build. La herramienta observada fue read. En una tarea
  acotada de lectura el agente eligió la vía directa citando la sección
  Qué vía usar de la skill. En un trabajo sin conversación y con plan
  externo eligió la vía durable y entregó el plan tal cual, sin bloques
  ni campos de aceptación. Evidencia en
  evidence/correcciones-3-4/salida-claw-grok.txt. El perfil de ensayo
  se borró. La política deseada v7 no cambió por usar el respaldo.
- Hermes en PC. Recorrido real completado en home aislado propio
  ensayo34d con la skill nueva instalada. Anthropic sin cuota semanal
  y ChatGPT/Codex con límite de uso por 497 horas, así que el turno
  corrió con xai-oauth y grok-4.7. La credencial de Grok ya existía en
  el pool principal de Hermes de la misma PC y solo se copió su
  entrada al home de ensayo, dentro de la misma máquina. En una tarea
  acotada de lectura el agente eligió la vía directa citando la
  sección Qué vía usar. En un trabajo sin conversación y con plan
  externo eligió la vía durable y encargó el plan tal cual, sin
  bloques ni campos de aceptación. Evidencia en
  evidence/correcciones-3-4/salida-hermes-grok.txt. Copilot rechazó
  antes sus modelos con HTTP 400 por entitlement. El home ensayo34
  se borró. El ensayo34d sigue bloqueado por su gateway temporal,
  que sale solo a los 600 s, y se puede borrar después.
- OpenBot en Mac. Recorrido real completado con la app corriendo y el
  agente DG con Scripts locales activado. La entrega de ensayo con clave
  estable terminó completed y respondió por la vía directa sin crear un
  plan para el motor. La consulta con status devolvió el mismo turno.
  La retransmisión con la misma clave devolvió el mismo deliveryId y
  turnId, sin duplicar el trabajo. Evidencia en
  evidence/correcciones-3-4/salida-openbot.txt. La parada sigue sin
  ofrecerse porque no se demostró. Un timeout no cancela.

Los tres recorridos de directores quedaron completados con dobles
declarados solo en el modelo de respaldo. Anthropic y ChatGPT estaban
sin cuota y ambos directores corrieron con Grok autorizado.

## Ronda 3. Dos fallos corregidos y encargo durable real

- Un aviso incierto ya no se degrada a local-pendiente cuando el canal
  está ilegible o sin comando. Al restaurar el transporte concilia antes
  de reenviar. La reproducción de la ronda da entregas 1 tras restaurar.
- La generación queda protegida desde la reserva previa al lanzamiento.
  Aplicar en esa ventana devuelve ocupada. Con el líder muerto y un hijo
  vivo, el árbol manda y aplicar también devuelve ocupada. El proceso
  siguió leyendo buena en ambos caminos.
- Encargo durable real en esta Mac desde un plan externo en archivo.
  Codex escribió saludo.txt y procedimiento.json. El supervisor se
  reinició en cada paso y el estado persistió. El juicio del revisor
  confirmó el producto y el encargo terminó. El aviso salió al canal
  OpenBot del agente DG. El primer envío quedó incierto por timeout y
  la conciliación por clave lo publicó con recibo externo, sin repetir
  el trabajo. Evidencia en salida-durable-real.txt.
- Actualización y reversa por host con componentes reales siguen sin
  instalarse sobre PC y Mini en esta ronda. Tocar los componentes
  vivos es activación de fase 5. Las instalaciones aisladas locales
  con el propio motor y su reversa están probadas desde la ronda 1.

## Ronda 4. Carrera cerrada y recorridos desde el director

- La carrera arranque contra actualización quedó cerrada con
  revalidación posterior al enlace y asignación estabilizada antes de
  lanzar. La reproducción REAL_R4 da ocupada y marca buena. La segunda
  ventana, con pausa tras asignar y aplicación en otro hilo, queda
  registrada en la generación que el trabajador lee. Hay dos pruebas
  mantenidas nuevas de esos intercalados. Nueve pruebas OK en total.
- Hermes ejecutó desde su propia terminal, en un home aislado con
  Grok. Creó un archivo con su herramienta, encargó el durable desde
  un plan externo, consultó, paró, esperó el cierre del supervisor y
  continuó el mismo encargo con clave nueva. La clave repetida
  devolvió el mismo rechazo guardado. Evidencia en salida-ronda-4.txt.
- Actualización y reversa aisladas con el motor real se corrieron en
  Mini y en PC. Aplicar la primera, ocupada con un trabajo vivo,
  aplicada sin trabajo, candidata mala rechazada conservando la
  vigente y reversa a la anterior. Mismo resultado en los dos hosts.
- Claw ejecutó el mismo recorrido desde sus herramientas en la
  Mini, en el perfil aislado clawr4 con Grok. Las herramientas
  observadas fueron read, write y exec, sin fallos. Creó el archivo
  directo, encargó el durable desde el plan externo, consultó, paró,
  esperó el cierre del supervisor y continuó el mismo encargo en un
  segundo turno. El perfil de ensayo se borró al cerrar.
- El home de ensayo de Hermes quedó bloqueado por su gateway
  temporal y se puede borrar cuando salga solo.

## Receta de fase 5

Activar por máquina en una ventana con los directores detenidos. Instalar la
skill de Hermes con configure_team en la PC y las copias de Claw en la Mini
desde estas ramas. Sembrar el manifiesto v7 de Conf y publicar la proyección
con el controlador. Sustituir hermes_autoupdate.py, claw_autoupdate.py y
vigila_modelos.py por el controlador de flota como único escritor, en ese
orden y de uno en uno. Cada sustitución conserva el actualizador viejo
detenido hasta comprobar el primero. La reversa de cada paso es
ops/reversa_ensayo.sh --home del home de ensayo o productivo
correspondiente, que vuelve a la generación realmente aplicada sin arrancar
el motor nuevo. No activar OpenBot con parada hasta demostrarla en la app
real.
