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
- **Un monitor y una dirección propios.** `MOTOR_KUMA_URL` (la que escribe `ops/senal-kuma.sh` en
  `~/.motor/secretos.env`) es del daemon del modo planes: con ella compartida, los dos procesos mandarían al mismo
  monitor, el estado oscilaría entre `up` y `down` y cada uno taparía la caída del otro. El supervisor de encargos usa
  `MOTOR_KUMA_ENCARGOS_URL`, de un monitor push aparte, y nunca lee `MOTOR_KUMA_URL`. La busca primero en el
  `secretos.env` de su base y, si no está, en el de `~/.motor` (permisos 600 en los dos). Sin esa dirección, un canal
  caído se escribe en el log del proceso (stderr), una vez por hora, y `motor director ver` sigue mostrándolo.
- **Pendiente de operación (no lo hace este cambio).** Crear el monitor push del supervisor en Kuma y escribir su
  dirección como `MOTOR_KUMA_ENCARGOS_URL`. `ops/senal-kuma.sh` hoy solo da de alta los monitores `mini` y `pc` y
  `motor doctor` solo verifica `MOTOR_KUMA_URL`; extenderlos toca el servidor de Kuma y sus pruebas de ssh, y se deja
  para un cambio de ops que se pueda ensayar.
- **Un estado dañado no rompe nada.** Un archivo de estado ilegible se ignora y un `fallos` que no es número cuenta como 0.
- **Límite.** El estado se actualiza cuando hay un aviso que enviar. Si un canal falla y no quedan avisos pendientes,
  sigue figurando caído hasta el siguiente envío; `ultimo_intento` dice cuándo fue la última prueba.
