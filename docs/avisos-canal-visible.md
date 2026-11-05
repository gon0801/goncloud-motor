# Un canal de avisos caído se ve

Decisiones de la fase `canal-visible` (`motor/integraciones/canales.py`).

- **Qué cuenta como canal caído.** El transporte falla (no corre, no responde o sale con error). Una salida ilegible o
  sin recibo es un fallo de ese mensaje y no marca el canal. Un canal cuenta como **caído** cuando lleva al menos
  `UMBRAL_CAIDO_S` (10 minutos) fallando desde el primer fallo.
- **Dónde se guarda.** `base/canales/<canal>.json`, escrito al final de cada pasada de `drenar`:
  `fallando_desde`, `ultimo_intento`, `fallos` y `ultimo_error`. Cuando el canal vuelve a responder, el archivo queda con
  `ultimo_exito` y sin `fallando_desde`.
- **Dónde se ve.** `motor director ver <encargo>` agrega `canales_caidos` (solo si hay alguno). La señal a Kuma sale con
  `status=down` y un mensaje `N planes; canal <canal> caído desde <hora>: <último error>`; sin canales caídos sale
  `status=up` como siempre. Kuma avisa por sus propias notificaciones, no por el canal que está caído.
- **No depende del canal caído.** Los dos leen el archivo local y no usan el transporte.
- **El error se limpia antes de guardarse y de mostrarse.** Se quitan direcciones (`http://…`), valores de
  `Bearer`/`Basic`, de `token`, `api_key`, `password`, `clave`, `authorization` o `cookie` tras `=` o `:`, tokens con
  forma `id:secreto` y cualquier cadena opaca de 32 caracteres o más. Queda en una línea y acotado a 200 caracteres,
  porque un error de transporte puede traer la dirección o la clave del bot.
- **De dónde sale la dirección de Kuma (sin variable nueva).** El supervisor de encargos lee `MOTOR_KUMA_URL` del
  `secretos.env` de su propia base (`reg.base`; en la Mini, `~/.motor-encargos/secretos.env`, con permisos 600), como
  hoy. El daemon del modo planes lee el de `~/.motor/secretos.env`: cada proceso, su archivo y su monitor. Por eso
  `ops/senal-kuma.sh` da de alta el monitor de planes de las tres máquinas y escribe su token en `~/.motor`, y
  conserva la dirección propia del supervisor en `~/.motor-encargos` (el monitor de encargos que ya tiene la Mini y
  la PC). En la MacBook, donde faltaba, crea el monitor «Motor de encargos de la MacBook» y escribe su token. Nunca el
  mismo token en los dos: con el mismo monitor, el proceso vivo taparía la caída del otro. La Mac escribe en local, sin
  ssh a sí misma. El supervisor **nunca** lee `~/.motor` ni hay respaldo allí. No se creó ninguna variable ni archivo
  de secretos nuevo.
- **"Canal caído" usa esa misma señal.** Con un canal caído, la señal de vida sale con `status=down` y el motivo
  (`N planes; canal <canal> caído desde <hora>: <último error>`), por la misma dirección; sin canal caído, `status=up`.
- **Sin `MOTOR_KUMA_URL` en la base** (o con el archivo ilegible o con permisos abiertos), el supervisor no manda señal:
  lo dice en el log al arrancar ("sin MOTOR_KUMA_URL en el secretos.env de la base, el supervisor de encargos no manda
  señal a Kuma") y, mientras haya un canal caído, lo escribe en el log una vez por hora. `motor director ver` lo
  muestra siempre.
- **Un estado dañado no rompe nada.** Un archivo de estado ilegible se ignora y un `fallos` que no es número cuenta como 0.
- **Sin avisos pendientes el canal se sondea.** Si un canal está caído y no queda ningún aviso `incierto` ni `pendiente` que
  pruebe su transporte, `drenar` llama a su `conciliacion` con un id `sondeo-<canal>`, a lo sumo una vez por intervalo creciente
  (`espera_s`) guardado en `proximo_sondeo`. Un aviso de un encargo detenido, cancelado o terminado, de un encargo que ya no existe o un resumen
  con el envío apagado no prueba el transporte: esa pasada no lo reabre y el canal se sondea igual. Una respuesta `ausente` o `hallado` lo marca recuperado, igual que un envío con
  recibo; sin `conciliacion`, el canal se deja como está. El sondeo no manda mensajes: `ultimo_intento` dice cuándo fue la última
  prueba. Un aviso pendiente solo recupera el canal con una entrega probada (un envío con recibo o una conciliación `hallado`);
  con `ausente` decide el envío que sigue en la misma pasada, y una conciliación que contesta sin veredicto no marca ni fallo ni
  recuperación. Un canal sano cuyos pendientes solo concilian como incierto sigue figurando caído hasta el siguiente envío con
  recibo.
