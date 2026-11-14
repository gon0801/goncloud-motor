---- MODULE FlujoInspeccion ----
\* Una fase de un encargo con el revisor pidiendo evidencia por el buzón, y el
\* propietario parando, continuando, corrigiendo o respondiendo en cualquier momento.
\* Una herramienta asíncrona (ci.correr) puede tardar más que un turno: se lanza, el
\* turno del agente termina o lo matan, el intento cambia, y después termina.
\*
\* El motor ya no tiene un estado que espere a una persona: las preguntas y la
\* quietud son avisos visibles, y al tope del freno el destino es un relevo o la
\* entrega con pendientes. La pregunta por autorización queda en la solicitud
\* (`pregunta`), no bloquea y el revisor sigue con la evidencia disponible; un
\* pedido que la repite se cuenta como una revisión más. `continuar` no reabre la
\* cuenta del freno. La respuesta del propietario se ejecuta o deniega antes de
\* lanzar al revisor (cli.paso llama continuar_respuestas antes de barrer).
\*
\* Cada acción nombra la función del motor que la hace. Las diferencias entre
\* versiones del código son las CONSTANTES booleanas de abajo; cada una dice qué
\* rama del código representa. Cobertura y commit de sincronía: SKILL.md de
\* .claude/skills/verificar-inspeccion.
EXTENDS Naturals, FiniteSets

CONSTANTS
  MaxIds,              \* identidades distintas para el mismo pedido (ciclo._id_inspeccion
                       \* salta las "detenida" con :reintentoN)
  Tope,                \* dominio.TOPE_NO_CONCLUYENTES: revisiones no_concluyente seguidas,
                       \* sobre la misma versión y pidiendo lo mismo, que el motor deja pasar
  RegistraAlLigar,     \* inspeccion.ligar deja necesidades.pedida en el lote del juicio
  ReligaSinSolicitud,  \* inspeccion.ligar: el "ya ligada, no hago nada" exige
                       \* `previa is not None` (si no, vuelve a encolar)
  LigaHuerfanaBloquea, \* dominio.inspeccion_bloquea con `sol is None`. El código nuevo
                       \* devuelve False (una liga sin registro no puede bloquear).
  DetieneLaPendiente,  \* necesidades.atender, rama _detenida_ahora: anota la resolución
                       \* "detenida" de una previa "pendiente"
  AjusteAbre,          \* dominio.inspeccion_bloquea / inspeccion_consumida miran
                       \* ajuste_sin_ver_por_inspeccion
  AjusteSuelta,        \* reducción "ajuste" llama _soltar_impedimento_de_inspeccion
  RespuestaReabre,     \* dominio._respuesta a una solicitud que no es "pregunta": suma
                       \* `tardias` (marca nueva) y llama _soltar_impedimento_de_inspeccion
  RecuerdaCarpeta,     \* la Solicitud recuerda su carpeta (evento "herramienta") y
                       \* necesidades._recoger busca el resultado ahí. Sin ella el resultado
                       \* solo se busca en el directorio del intento vigente (_destino_host).
  SoloMataLoDelTurno,  \* ciclo.barrer con `decision["matar"]` llama
                       \* cerrar_herramientas(solo_del_turno=True), que salta ci.correr.
                       \* Sin ella mata también la corrida de CI.
  RespuestaVigente,    \* dominio._respuesta usa respuesta_vigente (basta la fase). Sin ella
                       \* (72c30a8) usa solicitud_vigente: la pregunta de un intento anterior
                       \* ya no recibe la respuesta.
  FrenoCuenta,         \* dominio._contar_no_concluyentes (y la guarda de
                       \* ciclo._cerrar_con_juicio): al llegar a Tope la inspección pendiente
                       \* se vacía y queda un pendiente visible o un relevo. Sin ella
                       \* (antes del freno, ac41797) la cuenta no existe como corte.
  AvisaPregunta,          \* Código anterior a #152: dominio.sin_nada_en_curso y _retomar
                          \* trataban una pregunta abierta como un aviso que la quietud
                          \* podía rescatar (TRUE) o como una espera que la sostenía
                          \* (FALSE, 72c30a8). En main TRUE; como AntesPreguntaCallada es
                          \* FALSE, la pregunta no bloquea y la quietud no hace falta.
  AntesPreguntaCallada,   \* Código anterior a #152: dominio.inspeccion_bloquea devolvía
                          \* True para una solicitud en "pregunta" y ciclo._salta_inspeccion
                          \* no consumía el pedido repetido. La pregunta quedaba callada
                          \* (sin proceso y sin paso del motor) hasta la quietud.
  AntesFrenoContinuar,    \* Código anterior a #152: dominio._retomar volvía a arrancar al
                          \* revisor con el encargo en el tope (parar y continuar costaba
                          \* un turno más, contra el freno).
  AntesRespuestaSinEjecutar \* Código anterior a #152: cli.paso llamaba a barrer antes de
                            \* continuar_respuestas, así que el revisor se lanzaba con un
                            \* sí o un no todavía sin ejecutar. Ahora es al revés.

PedAg == MaxIds + 2   \* el pedido de ci.correr que el agente hace por el buzón (no es de la inspección)
Todos == 1..PedAg
\* Encargo.estado: ya no existe "necesita_usuario". Las preguntas y la quietud
\* dejan avisos y pendientes, no una espera humana.
Estados == {"recibido", "ejecutando", "deteniendo", "detenido", "terminado"}
EstadosSol == {"ausente", "pendiente", "pregunta", "respuesta_si", "respuesta_no",
               "resuelta", "denegada", "detenida"}
Respondida == {"respuesta_si", "respuesta_no"}   \* Solicitud.estado == "respuesta", afirmativa o no
Herramientas == {"ninguna", "corre", "termino", "entregada"}

VARIABLES
  est,      \* Encargo.estado
  proc,     \* hay un revisor corriendo (Encargo.proc, árbol vivo)
  juicio,   \* outcome.json del revisor sin incorporar: "ninguno" | "aprueba" | "pide" | "pideOtra" (pide lo
            \* mismo con otra consulta: otra ruta, otra identidad, igual para dominio._repite_pedido)
  pend,     \* fase.inspeccion_pendiente no vacío
  liga,     \* fase.inspeccion_solicitud: 0 = ninguna
  sol,      \* estado de cada Solicitud en Encargo.solicitudes ("ausente" = no registrada)
  peticion, \* ids con .interno/buzon/peticiones/<id>.json sin archivar
  vista,    \* la marca vigente de la solicitud ya se entregó a un revisor
            \* (respuesta_consumida(fase, marca_inspeccion_entregada(sol)))
  ajuste,   \* correcciones_sin_ver: un ajuste que ningún intento vio
  imped,    \* impedimento "inspeccion:<id>" registrado
  carrera,  \* historia: se lanzó el revisor sin información nueva con su inspección
            \* todavía sin despachar en el buzón
  herr,     \* la herramienta asíncrona de cada solicitud: "ninguna" (no lanzada o cerrada)
            \* | "corre" | "termino" (dejó resultado.json) | "entregada" (resolución anotada)
  aqui,     \* la carpeta de su herramienta es la del intento vigente
            \* (_destino_host(directorio, sol.id) con el directorio actual)
  corridaMuerta, \* historia: una corrida viva murió porque murió el turno
  caso,     \* qué se explora (no cambia durante el encargo): "ci" = la herramienta asíncrona
            \* sin el freno ni autorización; "autoriza" = el recurso de la inspección tiene
            \* `autorizacion: "propietario"` (capacidades.cargar); "freno" = el revisor repite el
            \* pedido. Los dos últimos van sin ci.correr: ver "Lo que no cubre" en SKILL.md.
  reabierto, \* historia: el propietario paró y continuó con el encargo en el tope (solo el
             \* código anterior a #152 lo usaba para dejar correr una vuelta más)
  cuenta,   \* Encargo.no_concluyentes, saturada en Tope + 1: revisiones no_concluyente
            \* seguidas sobre la misma versión y pidiendo lo mismo
  ligaVigente, \* la solicitud ligada la registró el intento vigente (Solicitud.intento == Encargo.intento)
  pendiente \* Encargo.pendientes_entrega no vacío: una espera visible que el barrido
            \* entrega con lo que haya, sin esperar a una persona. Un ajuste nuevo la limpia.

vars == <<est, proc, juicio, pend, liga, sol, peticion, vista, ajuste, imped, carrera,
          herr, aqui, corridaMuerta, caso, cuenta, ligaVigente, reabierto, pendiente>>
nuevas == <<herr, aqui, corridaMuerta>>
fijas == <<caso>>
contadores == <<cuenta, ligaVigente, reabierto, pendiente>>
Ids == IF caso = "freno" THEN 1..(MaxIds + 1) ELSE 1..MaxIds   \* en "freno" una identidad más: el revisor cambia la consulta hasta Tope + 1 veces

Abierto == est \in {"recibido", "ejecutando"}
Sol(k) == IF k = 0 THEN "ausente" ELSE sol[k]
Terminal(s) == s \in {"resuelta", "denegada", "detenida"}
\* La solicitud ya dio algo que entregar al revisor (cualquier estado menos los que esperan).
ConResultado(s) == s \notin {"ausente", "pendiente", "pregunta"}
Parado == est \in {"deteniendo", "detenido"}   \* necesidades._detenida_ahora: última orden parar
Corriendo == \E k \in Todos : herr[k] = "corre"
SinIntentoAnterior == [k \in Todos |-> FALSE]    \* ningún resultado queda en el intento vigente
Exige == caso = "autoriza"
ConCi == caso = "ci"            \* hay corridas de CI (la del agente y las de la inspección)
ContandoFreno == caso /= "ci"   \* dominio._contar_no_concluyentes se explora fuera de "ci"
SinAutorizar(k) == ~Exige \/ k = PedAg   \* la herramienta del pedido corre sin esperar un sí

\* dominio.ajuste_sin_ver_por_inspeccion
AjusteSinVer == AjusteAbre /\ pend /\ ajuste

\* dominio.inspeccion_bloquea, con el estado explícito para poder preguntarlo del siguiente.
\* La pregunta por autorización ya no bloquea: el revisor sigue con la evidencia
\* disponible. Antes (AntesPreguntaCallada) sí bloqueaba y la dejaba callada.
BloqueaEn(p, l, s, v, a) ==
  IF ~p THEN FALSE
  ELSE IF AjusteAbre /\ a THEN FALSE
  ELSE IF l = 0 THEN TRUE
  ELSE IF s[l] = "ausente" THEN LigaHuerfanaBloquea
  ELSE IF s[l] = "pendiente" THEN TRUE
  ELSE IF s[l] = "pregunta" THEN AntesPreguntaCallada
  ELSE v[l]

Bloquea == BloqueaEn(pend, liga, sol, vista, ajuste)

\* La pregunta abierta es un aviso visible: la solicitud ligada está en `pregunta`.
\* No exige que bloquee (el código nuevo no la trata como espera).
PreguntaEn(p, l, s, v, a) == p /\ l /= 0 /\ s[l] = "pregunta"
PreguntaAbierta == PreguntaEn(pend, liga, sol, vista, ajuste)

\* dominio.inspeccion_consumida (alimenta espera_inspeccion_consumida)
Consumida ==
  pend /\ ~AjusteSinVer /\ liga /= 0 /\ ConResultado(Sol(liga)) /\ vista[liga]

\* ciclo._id_inspeccion: el mismo pedido; una "detenida" pasa al siguiente reintento
IdPedido == CHOOSE k \in Ids : sol[k] /= "detenida" /\ \A j \in 1..(k - 1) : sol[j] = "detenida"

\* El revisor pide lo mismo con otra consulta (traba 4: jaula-linux-a.txt, -b.txt, -c.txt): la
\* identidad cambia (ciclo._id_inspeccion incluye el pedido y la huella del recurso) pero
\* dominio._repite_pedido sigue viéndolo como repetido (mismo texto del faltante).
HayOtro == \E k \in Ids : sol[k] = "ausente" /\ k /= IdPedido
IdOtro == CHOOSE k \in Ids : sol[k] = "ausente" /\ k /= IdPedido
IdJ == IF juicio = "pideOtra" THEN IdOtro ELSE IdPedido   \* la identidad que nombra el juicio

\* necesidades._recoger puede registrar el resultado de la herramienta de k
HayQueRecoger(k) ==
  /\ est /= "terminado"
  /\ herr[k] = "termino" /\ sol[k] \in {"pendiente", "detenida"}
  /\ (RecuerdaCarpeta \/ aqui[k])

\* ciclo.barrer: atender corre antes de decidir el lanzamiento. Una respuesta del propietario
\* con su petición en el buzón (necesidades._reponer_peticion) se ejecuta o se deniega antes.
PorAtender == \E k \in peticion : sol[k] \in Respondida

\* Hay una respuesta registrada que todavía no se ejecutó ni denegó. cli.paso llama
\* continuar_respuestas antes de barrer (main), así que el revisor no se lanza hasta
\* atenderla. El código anterior (AntesRespuestaSinEjecutar) no lo garantizaba.
RespuestaSinEjecutar == \E k \in Todos : sol[k] \in Respondida

TypeOK ==
  /\ est \in Estados /\ proc \in BOOLEAN /\ juicio \in {"ninguno", "aprueba", "pide", "pideOtra"}
  /\ pend \in BOOLEAN /\ liga \in 0..(MaxIds + 1)
  /\ sol \in [Todos -> EstadosSol]
  /\ peticion \subseteq Todos /\ vista \in [Todos -> BOOLEAN]
  /\ ajuste \in BOOLEAN /\ imped \in BOOLEAN /\ carrera \in BOOLEAN
  /\ herr \in [Todos -> Herramientas] /\ aqui \in [Todos -> BOOLEAN] /\ corridaMuerta \in BOOLEAN
  /\ caso \in {"ci", "autoriza", "freno"} /\ cuenta \in 0..(Tope + 1) /\ ligaVigente \in BOOLEAN
  /\ reabierto \in BOOLEAN /\ pendiente \in BOOLEAN

Init ==
  /\ est = "ejecutando" /\ proc = TRUE /\ juicio = "ninguno"
  /\ pend = FALSE /\ liga = 0 /\ sol = [k \in Todos |-> "ausente"]
  /\ peticion = {} /\ vista = [k \in Todos |-> FALSE]
  /\ ajuste = FALSE /\ imped = FALSE /\ carrera = FALSE
  /\ herr = [k \in Todos |-> "ninguna"] /\ aqui = SinIntentoAnterior /\ corridaMuerta = FALSE
  /\ caso \in {"ci", "autoriza", "freno"} /\ cuenta = 0 /\ ligaVigente = FALSE
  /\ reabierto = FALSE /\ pendiente = FALSE

\* dominio.decision_proceso devuelve lanzar (sin impedimento, sin espera_inspeccion_consumida,
\* sin inspeccion_bloquea). El freno corta el lanzamiento al llegar a Tope: parar y
\* continuar no lo reabre (solo el código anterior a #152, AntesFrenoContinuar).
\* Tampoco se lanza con una respuesta del propietario sin ejecutar.
LanzarPosible ==
  /\ Abierto /\ ~proc /\ juicio = "ninguno" /\ ~imped /\ ~Consumida /\ ~Bloquea
  /\ ~pendiente
  /\ (cuenta < Tope \/ ~FrenoCuenta \/ (AntesFrenoContinuar /\ reabierto /\ cuenta = Tope))
  /\ \A k \in Todos : ~HayQueRecoger(k)   \* ciclo.barrer: atender (y su _recoger) corre antes de decidir el lanzamiento
  /\ ~PorAtender
  /\ (AntesRespuestaSinEjecutar \/ ~RespuestaSinEjecutar)

\* El supervisor lanza al revisor. Al abrir el intento el ajuste queda visto. La marca
\* inspeccion_entregada no se anota aquí sino al incorporar el juicio (ver Salta): un revisor
\* muerto sin juicio se relanza con el mismo dato. El intento nuevo deja atrás la carpeta de
\* las herramientas anteriores y deja las solicitudes de los intentos anteriores sin vigencia.
\* Una pregunta abierta no cuenta como carrera: es un aviso, no una inspección sin despachar.
Lanzar ==
  /\ LanzarPosible
  /\ proc' = TRUE /\ est' = "ejecutando" /\ ajuste' = FALSE
  /\ carrera' = (carrera \/ (pend /\ liga \in peticion /\ ~PreguntaAbierta
                             /\ ~Terminal(Sol(liga)) /\ ~AjusteSinVer))
  /\ aqui' = SinIntentoAnterior
  /\ ligaVigente' = FALSE
  /\ UNCHANGED <<juicio, pend, liga, sol, peticion, vista, imped, herr, corridaMuerta,
                 cuenta, reabierto, pendiente>>
  /\ UNCHANGED fijas

\* El revisor termina su turno y deja su juicio en outcome.json. Puede pasar también
\* con la parada ya pedida.
\* En el caso `freno` el juicio puede pedir lo mismo con otra consulta (`pideOtra`, traba 4).
Termina ==
  /\ proc /\ juicio = "ninguno"
  /\ proc' = FALSE /\ juicio' \in {"aprueba", "pide"} \cup (IF caso = "freno" /\ HayOtro THEN {"pideOtra"} ELSE {})
  /\ UNCHANGED <<est, pend, liga, sol, peticion, vista, ajuste, imped, carrera>>
  /\ UNCHANGED <<nuevas, fijas, contadores>>

\* ciclo.barrer -> _incorporar -> _incorporar_revisor (guarda en ciclo.barrer: estado
\* no deteniendo/detenido/terminado e intentos_nuevos). "pide" = no_concluyente
\* con inspección: _devolver(..., "inspeccionar") e inspeccion.ligar. Con un recurso que pide
\* autorización, inspeccion.ligar registra el pedido y lo deja como solicitud `pregunta`;
\* el encargo sigue en recibido, sin esperar a una persona.
LigarPide ==
  LET k == IdJ IN
  IF liga = k /\ (~ReligaSinSolicitud \/ sol[k] /= "ausente")
    \* Ya ligada: la pregunta sigue visible y no cambia el estado del encargo.
    THEN /\ est' = "recibido"
         /\ UNCHANGED <<liga, sol, peticion, ligaVigente>>
  ELSE IF sol[k] /= "ausente" /\ vista[k]
    THEN /\ est' = "recibido"
         /\ UNCHANGED <<liga, sol, peticion, ligaVigente>>
  ELSE IF sol[k] /= "ausente"
    THEN /\ est' = "recibido" /\ liga' = k
         /\ ligaVigente' = FALSE /\ UNCHANGED <<sol, peticion>>
  ELSE /\ liga' = k
       /\ peticion' = peticion \cup {k}
       /\ sol' = IF RegistraAlLigar THEN [sol EXCEPT ![k] = "pendiente"] ELSE sol
       /\ ligaVigente' = (~RespuestaVigente /\ RegistraAlLigar)
       /\ est' = "recibido"

\* inspeccion.consumir_ligada: al cambiar de consulta (o al aprobar), la respuesta ya
\* entregada de la solicitud ligada queda consumida.
VistaAlCambiar ==
  IF liga /= 0 /\ ConResultado(Sol(liga)) THEN [vista EXCEPT ![liga] = TRUE] ELSE vista

\* ciclo._salta_inspeccion (antes de incorporar, solo con inspeccion_pendiente).
\* El código nuevo lo consume como una revisión más cuando la solicitud ligada está
\* en `pregunta`: no queda callado y el freno cuenta la repetición. El código anterior
\* (AntesPreguntaCallada) lo saltaba y dejaba la pregunta sin marcar.
Salta(v) ==
  /\ pend /\ juicio \in {"pide", "pideOtra"}
  /\ LET k == IdJ IN
       IF k /= liga THEN sol[k] /= "ausente" /\ v[k]
       ELSE IF sol[k] = "pregunta" THEN AntesPreguntaCallada
       ELSE sol[k] /= "ausente" \/ ~ReligaSinSolicitud

\* dominio._contar_no_concluyentes: cada revisión consumida pidiendo lo mismo suma.
\* El código nuevo satura en Tope; el anterior al freno (FrenoCuenta = FALSE) seguía
\* contando, y AntesFrenoContinuar dejaba una vuelta más tras parar y continuar.
NuevaCuenta ==
  IF ~ContandoFreno \/ cuenta >= Tope + 1 THEN cuenta
  ELSE IF cuenta < Tope THEN cuenta + 1
  ELSE IF ~FrenoCuenta \/ (AntesFrenoContinuar /\ reabierto) THEN cuenta + 1
  ELSE cuenta

\* Una revisión no_concluyente que se consume cuenta para el freno: ciclo._incorporar_revisor
\* -> evento `revision` -> dominio._revision -> dominio._contar_no_concluyentes. Al llegar
\* a Tope el código nuevo vacía inspeccion_pendiente y deja la entrega con pendientes
\* (relevo o PR con la nota); no devuelve ni liga. Sin FrenoCuenta (antes del freno) la
\* cuenta crece y el encargo sigue con la inspección pendiente.
Incorporar ==
  /\ Abierto /\ ~proc /\ juicio /= "ninguno"
  /\ juicio' = "ninguno"
  /\ LET v == IF pend /\ IdJ /= liga THEN VistaAlCambiar ELSE vista IN
     IF Salta(v)
       THEN /\ vista' = IF IdJ = liga /\ ConResultado(Sol(liga))
                          THEN [vista EXCEPT ![liga] = TRUE] ELSE v
            /\ UNCHANGED <<est, pend, liga, sol, peticion, cuenta, ligaVigente, reabierto, pendiente>>
     ELSE IF juicio = "aprueba"
       THEN /\ est' = "terminado" /\ pend' = FALSE /\ vista' = v /\ cuenta' = 0
            /\ reabierto' = FALSE /\ pendiente' = FALSE
            /\ UNCHANGED <<liga, sol, peticion, ligaVigente>>
       ELSE /\ vista' = v
            /\ cuenta' = NuevaCuenta
            /\ reabierto' = reabierto
            /\ IF FrenoCuenta /\ ContandoFreno /\ NuevaCuenta >= Tope
                 /\ ~(AntesFrenoContinuar /\ reabierto /\ cuenta = Tope)
                 THEN /\ est' = est
                      /\ pend' = FALSE
                      /\ pendiente' = TRUE   \* _entregar_con_pendientes / relevo: espera visible
                      /\ UNCHANGED <<liga, sol, peticion, ligaVigente>>
                 ELSE /\ pend' = TRUE
                      /\ pendiente' = pendiente
                      /\ LigarPide
  /\ UNCHANGED <<proc, ajuste, imped, carrera>>
  /\ UNCHANGED <<nuevas, fijas>>

\* necesidades.atender, una petición del buzón por paso.
Atender ==
  /\ est /= "terminado"
  /\ \E k \in peticion :
       \/ /\ Terminal(sol[k])                    \* previa con resultado: responde y archiva
          /\ peticion' = peticion \ {k}
          /\ UNCHANGED <<est, sol, vista, herr, aqui, ligaVigente, pendiente>>
       \/ /\ ~Terminal(sol[k]) /\ Parado         \* rama _detenida_ahora
          /\ sol' = IF DetieneLaPendiente /\ sol[k] = "pendiente"
                      THEN [sol EXCEPT ![k] = "detenida"] ELSE sol
          /\ vista' = [vista EXCEPT ![k] = FALSE]
          /\ peticion' = peticion \ {k}
          /\ UNCHANGED <<est, herr, aqui, ligaVigente, pendiente>>
       \/ /\ sol[k] = "ausente" /\ ~Parado       \* _registrar_solicitud
          /\ sol' = [sol EXCEPT ![k] = "pendiente"]
          /\ ligaVigente' = (~RespuestaVigente /\ (ligaVigente \/ k = liga))
          /\ UNCHANGED <<est, peticion, vista, herr, aqui, pendiente>>
       \/ /\ sol[k] = "pendiente" /\ ~Parado /\ herr[k] = "ninguna" /\ SinAutorizar(k)
          \* una herramienta que termina en el acto: resolucion (_cerrar_con_resultado ->
          \* _anotar_resolucion -> dominio._resolucion; _reanudar_inspeccion ya no
          \* cambia de estado, el barrido sigue)
          /\ sol' = [sol EXCEPT ![k] = "resuelta"]
          /\ vista' = [vista EXCEPT ![k] = FALSE]
          /\ peticion' = peticion \ {k}
          /\ UNCHANGED <<est, herr, aqui, ligaVigente, pendiente>>
       \/ /\ sol[k] = "pendiente" /\ ~Parado /\ herr[k] = "ninguna" /\ SinAutorizar(k) /\ ConCi
          \* ci.correr: _recordar_carpeta y _ejecutar la lanzan; la petición sigue en el
          \* buzón y la solicitud queda pendiente. Su carpeta es la del intento vigente.
          /\ herr' = [herr EXCEPT ![k] = "corre"]
          /\ aqui' = [aqui EXCEPT ![k] = TRUE]
          /\ UNCHANGED <<est, sol, vista, peticion, ligaVigente, pendiente>>
       \/ /\ sol[k] = "pendiente" /\ ~Parado /\ herr[k] = "ninguna" /\ Exige /\ k \in Ids
          \* rama `pregunta` de necesidades.atender: capacidades.ejecutar devuelve "pregunta"
          \* sin `autorizado`; _cerrar_con_resultado anota la resolución `pregunta`. La
          \* petición sigue en el buzón y la pregunta queda visible, sin cambiar de estado.
          \* En el código anterior a #152 la pregunta abría la espera visible de
          \* necesita_usuario (AvisaPregunta); aquí la representa pendiente.
          /\ sol' = [sol EXCEPT ![k] = "pregunta"]
          /\ pendiente' = IF AvisaPregunta /\ AntesPreguntaCallada THEN TRUE ELSE pendiente
          /\ UNCHANGED <<est, vista, peticion, herr, aqui, ligaVigente>>
       \/ /\ sol[k] = "respuesta_si" /\ ~Parado
          \* camino `autorizado`: previa en `respuesta` y _afirmativa. _ejecutar corre la
          \* herramienta (aquí en el acto), _anotar_resolucion `resuelta`; se atiende antes
          \* de lanzar (ver LanzarPosible).
          /\ sol' = [sol EXCEPT ![k] = "resuelta"]
          /\ vista' = [vista EXCEPT ![k] = FALSE]
          /\ peticion' = peticion \ {k}
          /\ UNCHANGED <<est, herr, aqui, ligaVigente, pendiente>>
       \/ /\ sol[k] = "respuesta_no" /\ ~Parado
          \* camino `denegada`: previa en `respuesta` no afirmativa. _anotar_resolucion
          \* `denegada` ("el propietario respondió: ..."); el revisor recibe la denegación.
          /\ sol' = [sol EXCEPT ![k] = "denegada"]
          /\ vista' = [vista EXCEPT ![k] = FALSE]
          /\ peticion' = peticion \ {k}
          /\ UNCHANGED <<est, herr, aqui, ligaVigente, pendiente>>
  /\ UNCHANGED <<proc, juicio, pend, liga, ajuste, imped, carrera, corridaMuerta, cuenta,
                 reabierto>>
  /\ UNCHANGED fijas

\* La herramienta de CI termina por su cuenta (capacidades.main deja resultado.json en
\* su carpeta). No depende del turno ni del estado del encargo: puede ser después de que
\* el turno murió, de que cambió el intento o con el encargo detenido.
HerramientaTermina ==
  /\ \E k \in Todos :
       /\ herr[k] = "corre"
       /\ herr' = [herr EXCEPT ![k] = "termino"]
  /\ UNCHANGED <<est, proc, juicio, pend, liga, sol, peticion, vista, ajuste, imped, carrera>>
  /\ UNCHANGED <<aqui, corridaMuerta>>
  /\ UNCHANGED <<fijas, contadores>>

\* necesidades._recoger (primer paso de atender): la solicitud pendiente, o detenida, cuya
\* herramienta terminó se resuelve con ese resultado (un `ok`; uno que no lo es deja la
\* detenida como está) y le llega a la fase. Con RecuerdaCarpeta lo busca donde se lanzó;
\* sin ella solo lo encuentra si el intento vigente es el que la lanzó (HayQueRecoger).
\* El código de e0f06a2 además exigía la petición sin archivar; el modelo no (más favorable).
RecogerDe(k) ==
  /\ HayQueRecoger(k)
  /\ sol' = [sol EXCEPT ![k] = "resuelta"]
  /\ vista' = [vista EXCEPT ![k] = FALSE]
  /\ herr' = [herr EXCEPT ![k] = "entregada"]
  /\ peticion' = peticion \ {k}
  /\ UNCHANGED <<est, proc, juicio, pend, liga, ajuste, imped, carrera>>
  /\ UNCHANGED <<aqui, corridaMuerta>>
  /\ UNCHANGED <<fijas, cuenta, ligaVigente, reabierto, pendiente>>

Recoger == \E k \in Todos : RecogerDe(k)

\* cli.paso: continuar_respuestas (que repone la petición de una respuesta) corre antes
\* del barrido. _reponer_peticion devuelve al buzón la petición que una parada archivó, y
\* atender la ejecuta (un sí), la deniega (un no) o, con el encargo parado, la archiva otra
\* vez. Con main el revisor no se lanza con una respuesta sin ejecutar.
ReponePeticion ==
  /\ est /= "terminado"
  /\ \E k \in Todos :
       /\ sol[k] \in Respondida /\ k \notin peticion
       /\ peticion' = peticion \cup {k}
  /\ UNCHANGED <<est, proc, juicio, pend, liga, sol, vista, ajuste, imped, carrera>>
  /\ UNCHANGED <<nuevas, fijas, contadores>>

\* El agente, con su turno vivo, pide ci.correr por el buzón (cliente.py pedir) y espera:
\* la petición queda en peticiones/ y necesidades.atender la registra y la lanza. No es la
\* inspección del revisor: no la liga ni bloquea al revisor. Un solo pedido de este tipo.
PedirCi ==
  /\ ConCi /\ Abierto /\ proc /\ sol[PedAg] = "ausente" /\ PedAg \notin peticion
  /\ peticion' = peticion \cup {PedAg}
  /\ UNCHANGED <<est, proc, juicio, pend, liga, sol, vista, ajuste, imped, carrera>>
  /\ UNCHANGED <<nuevas, fijas, contadores>>

\* ciclo.barrer con `decision["matar"]` sin parada: dominio.decision_proceso llega a la rama
\* "colgada" (árbol vivo, sin herramienta ni hecho nuevo) solo si no hay impedimento, ni
\* espera_inspeccion_consumida, ni inspeccion_bloquea. Mata el turno (cerrar) y cierra las
\* herramientas del intento vigente (las con `aqui`, e0f06a2 solo cerraba `directorio`).
\* Con SoloMataLoDelTurno la corrida de CI sigue; sin ella muere con el turno (en el código
\* su solicitud acaba en error; aquí, detenida: sin resultado). El intento no cambia aquí
\* sino cuando el motor lanza el turno siguiente (Lanzar).
MatarTurno ==
  /\ Abierto /\ proc /\ juicio = "ninguno" /\ ~imped /\ ~Consumida /\ ~Bloquea
  /\ ~pendiente
  /\ proc' = FALSE
  /\ IF SoloMataLoDelTurno
       THEN /\ UNCHANGED <<sol, vista, herr, corridaMuerta>>
       ELSE /\ herr' = [k \in Todos |-> IF herr[k] = "corre" /\ aqui[k] THEN "ninguna" ELSE herr[k]]
            /\ sol' = [k \in Todos |->
                         IF herr[k] = "corre" /\ aqui[k] /\ sol[k] = "pendiente"
                           THEN "detenida" ELSE sol[k]]
            /\ vista' = [k \in Todos |->
                           IF herr[k] = "corre" /\ aqui[k] /\ sol[k] = "pendiente"
                             THEN FALSE ELSE vista[k]]
            /\ corridaMuerta' = (corridaMuerta \/ \E k \in Todos : herr[k] = "corre" /\ aqui[k])
  /\ UNCHANGED <<est, juicio, pend, liga, peticion, ajuste, imped, carrera, aqui>>
  /\ UNCHANGED <<fijas, cuenta, ligaVigente, reabierto, pendiente>>

\* ciclo.barrer con la parada o la orden parar/cancelar vigente:
\* necesidades.cerrar_herramientas_del_encargo cierra las herramientas de todos los
\* intentos, con la limpieza de rama y PR de la de CI. Su solicitud queda detenida.
CerrarHerramientas ==
  /\ Parado /\ Corriendo
  /\ herr' = [k \in Todos |-> IF herr[k] = "corre" THEN "ninguna" ELSE herr[k]]
  /\ sol' = [k \in Todos |->
               IF herr[k] = "corre" /\ sol[k] = "pendiente" THEN "detenida" ELSE sol[k]]
  /\ vista' = [k \in Todos |->
                 IF herr[k] = "corre" /\ sol[k] = "pendiente" THEN FALSE ELSE vista[k]]
  /\ UNCHANGED <<est, proc, juicio, pend, liga, peticion, ajuste, imped, carrera>>
  /\ UNCHANGED <<aqui, corridaMuerta>>
  /\ UNCHANGED <<fijas, cuenta, ligaVigente, reabierto, pendiente>>

\* dominio.decision_proceso: espera_inspeccion_consumida -> evento impedimento
Impedir ==
  /\ Abierto /\ ~proc /\ juicio = "ninguno" /\ ~imped /\ Consumida
  /\ imped' = TRUE
  /\ UNCHANGED <<est, proc, juicio, pend, liga, sol, peticion, vista, ajuste, carrera>>
  /\ UNCHANGED <<nuevas, fijas, contadores>>

\* dominio.sin_nada_en_curso: encargo abierto que no explica por qué no avanza. Con el
\* código nuevo una pregunta abierta es un aviso y la solicitud abierta impide la
\* quietud (AntesPreguntaCallada = FALSE). En el código anterior a #152 una pregunta
\* bloqueada se dejaba rescatar por la quietud (AvisaPregunta = TRUE); en 72c30a8 la
\* pregunta la sostenía y la quietud no llegaba (AvisaPregunta = FALSE).
SinNadaEnCurso ==
  /\ Abierto /\ ~proc /\ ~imped /\ ~pendiente
  /\ ~(Bloquea /\ ~(AvisaPregunta /\ PreguntaAbierta))
  /\ \A k \in Todos :
       Sol(k) \in {"pendiente", "pregunta"} =>
         (AvisaPregunta /\ AntesPreguntaCallada /\ PreguntaAbierta /\ k = liga)

\* ciclo._vigilar_quietud: tras _QUIETUD_VUELTAS vueltas y _QUIETUD_S segundos sin que el
\* barrido lance nada ni el registro se mueva, con sin_nada_en_curso anota `quietud` y deja
\* un pendiente_entrega visible con la causa. Ya no cambia el encargo a una espera humana:
\* el barrido entrega con lo que haya. El modelo no tiene reloj: la acción está habilitada
\* cuando el encargo está quieto.
Quietud ==
  /\ SinNadaEnCurso /\ juicio = "ninguno" /\ ~Consumida /\ ~LanzarPosible
  /\ \A k \in Todos : ~HayQueRecoger(k)
  /\ \A k \in peticion : sol[k] = "pregunta"
  /\ pendiente' = TRUE
  /\ UNCHANGED <<est, proc, juicio, pend, liga, sol, peticion, vista, ajuste, imped, carrera>>
  /\ UNCHANGED <<nuevas, fijas, cuenta, ligaVigente, reabierto>>

\* director parar -> dominio._orden "parar" (vale desde cualquier estado no terminal)
Parar ==
  /\ est \in {"recibido", "ejecutando"}
  /\ est' = "deteniendo"
  /\ UNCHANGED <<proc, juicio, pend, liga, sol, peticion, vista, ajuste, imped, carrera>>
  /\ UNCHANGED <<nuevas, fijas, contadores>>

\* ciclo.barrer con la parada: mata el árbol vivo; el revisor muere sin juicio
Matar ==
  /\ est = "deteniendo" /\ proc
  /\ proc' = FALSE
  /\ UNCHANGED <<est, juicio, pend, liga, sol, peticion, vista, ajuste, imped, carrera>>
  /\ UNCHANGED <<nuevas, fijas, contadores>>

\* ciclo.barrer: deteniendo con el árbol ausente -> detenido. En el mismo barrido atender corre
\* antes (también con la parada): las peticiones del buzón ya se archivaron o se detuvieron.
Detener ==
  /\ est = "deteniendo" /\ ~proc /\ peticion = {}
  /\ est' = "detenido"
  /\ UNCHANGED <<proc, juicio, pend, liga, sol, peticion, vista, ajuste, imped, carrera>>
  /\ UNCHANGED <<nuevas, fijas, contadores>>

\* director continuar -> dominio._orden "continuar" -> _retomar, con _limpiar_para_continuar.
\* Con el tope alcanzado NO reabre la cuenta ni relanza al revisor: conserva los
\* pendientes y el barrido entrega con pendientes. Por debajo del tope la cuenta
\* empieza de nuevo. El código anterior a #152 (AntesFrenoContinuar) sí relanzaba.
Continuar ==
  /\ est = "detenido"
  /\ est' = "recibido"
  /\ IF AntesFrenoContinuar
       THEN /\ pendiente' = FALSE
            /\ reabierto' = (reabierto \/ cuenta >= Tope)
            /\ UNCHANGED cuenta
       ELSE IF cuenta >= Tope
            THEN /\ pendiente' = TRUE
                 /\ reabierto' = reabierto
                 /\ UNCHANGED cuenta
            ELSE /\ pendiente' = FALSE
                 /\ reabierto' = FALSE
                 /\ cuenta' = 0
  /\ UNCHANGED <<proc, juicio, pend, liga, sol, peticion, vista, ajuste, imped, carrera>>
  /\ UNCHANGED <<nuevas, fijas, ligaVigente>>

\* motor corregir -> reducción "ajuste": suma el ajuste, _soltar_impedimento_de_inspeccion
\* y _sin_repeticiones/_reiniciar_escalera (la cuenta del freno y los pendientes empiezan
\* de nuevo). No hay espera humana que reabrir: el barrido sigue con la información nueva.
Ajustar ==
  /\ est /= "terminado"
  /\ ajuste' = TRUE
  /\ imped' = IF AjusteSuelta THEN FALSE ELSE imped
  /\ cuenta' = 0 /\ reabierto' = FALSE /\ pendiente' = FALSE
  /\ UNCHANGED <<est, proc, juicio, pend, liga, sol, peticion, vista, carrera>>
  /\ UNCHANGED <<nuevas, fijas>>
  /\ UNCHANGED ligaVigente

\* director responder -> necesidades.responder -> dominio._respuesta, a la solicitud ligada.
\* Una pregunta pasa a `respuesta` (afirmativa o no); necesidades.continuar_respuestas
\* repone la petición y atender la ejecuta o la deniega antes de lanzar. Una solicitud
\* que no es pregunta suma `tardias` (marca nueva). En los dos casos: se suelta el
\* impedimento, la cuenta del freno se reinicia y se limpian los pendientes.
Responder ==
  /\ est /= "terminado" /\ liga /= 0 /\ sol[liga] /= "ausente"
  /\ IF sol[liga] = "pregunta"
       THEN IF RespuestaVigente \/ ligaVigente
              THEN /\ \E si \in BOOLEAN :
                        LET nueva == [sol EXCEPT ![liga] = IF si THEN "respuesta_si" ELSE "respuesta_no"]
                        IN /\ sol' = nueva
                           /\ peticion' = peticion \cup {liga}
                    /\ imped' = FALSE /\ cuenta' = 0 /\ reabierto' = FALSE
                    /\ pendiente' = FALSE
                    /\ UNCHANGED vista
              ELSE UNCHANGED <<sol, peticion, vista, imped, cuenta, reabierto, pendiente>>
       ELSE /\ RespuestaReabre
            /\ vista' = [vista EXCEPT ![liga] = FALSE]
            /\ imped' = FALSE /\ cuenta' = 0 /\ reabierto' = FALSE
            /\ pendiente' = FALSE
            /\ UNCHANGED <<sol, peticion>>
  /\ UNCHANGED <<est, proc, juicio, pend, liga, ajuste, carrera, ligaVigente>>
  /\ UNCHANGED <<nuevas, fijas>>

Terminado ==
  /\ est = "terminado"
  /\ UNCHANGED vars

Motor == Lanzar \/ Termina \/ Incorporar \/ Atender \/ Impedir \/ Matar \/ Detener
         \/ HerramientaTermina \/ Recoger \/ PedirCi \/ MatarTurno \/ CerrarHerramientas
         \/ Quietud \/ ReponePeticion
Propietario == Parar \/ Continuar \/ Ajustar \/ Responder
Next == Motor \/ Propietario \/ Terminado

Spec == Init /\ [][Next]_vars

\* Regla 1. Un encargo abierto sin una espera visible siempre tiene algo que el motor
\* hace solo: un revisor corriendo, una corrida de CI en marcha, o un paso del motor posible.
\* Una pregunta abierta tiene su propia regla (PreguntaVisible).
SinAtascoMudo ==
  (Abierto /\ ~imped /\ ~pendiente /\ ~PreguntaAbierta) =>
    (proc \/ Corriendo \/ ENABLED (Lanzar \/ Incorporar \/ Atender \/ Impedir \/ Recoger \/ ReponePeticion))

\* Regla 2. El revisor no vuelve a correr, sin un ajuste nuevo, mientras la inspección
\* que pidió sigue sin despachar en el buzón. Una pregunta abierta es un aviso, no una
\* inspección sin despachar (no cuenta como carrera).
SinCarrera == ~carrera

\* Regla 3. Un impedimento de inspección siempre tiene salida por el propietario: un
\* ajuste o una respuesta lo levanta sin cancelar el encargo.
ImpedimentoConSalida == imped => ENABLED (Propietario /\ ~imped')

\* Regla 4. Una herramienta que terminó siempre entrega su resultado a la fase: el
\* motor puede registrarlo en su solicitud, sea cual sea el intento vigente.
ResultadoEntregado ==
  \A k \in Todos : (herr[k] = "termino" /\ est /= "terminado") => ENABLED RecogerDe(k)

\* Regla 5. Una corrida de CI en marcha no muere porque muera el turno del agente: solo
\* la detienen parar o cancelar el encargo.
CorridaSobreviveAlTurno == ~corridaMuerta

\* Regla 6. Una pregunta abierta siempre se ve: el encargo no queda en un recibido mudo. O
\* hay un revisor o el motor tiene un paso posible (incluida la quietud, que deja la
\* entrega con pendientes). La pregunta vive en la solicitud, sin esperar a una persona.
PreguntaVisible ==
  (Abierto /\ PreguntaAbierta /\ ~pendiente) =>
    (proc \/ Corriendo \/ ENABLED (Lanzar \/ Incorporar \/ Atender \/ Impedir \/ Recoger \/ ReponePeticion \/ Quietud))

\* Regla 7. Contestar una pregunta siempre la mueve: el propietario puede responderla y
\* esa respuesta la saca de `pregunta`; un sí se ejecuta y un no se deniega (hay un paso
\* de atender mientras la respuesta espera en el buzón).
RespuestaMueve ==
  /\ PreguntaAbierta => ENABLED (Responder /\ sol[liga]' /= "pregunta")
  /\ \A k \in Todos :
       (sol[k] \in Respondida /\ est /= "terminado") =>
         \* un sí fuera del buzón vuelve a él (algún paso de Next lo repone); con la petición en el buzón y
         \* el encargo en marcha, el sí se ejecuta (resuelta) y el no se deniega (denegada)
         IF k \notin peticion THEN ENABLED (Next /\ k \in peticion')
         ELSE IF Parado THEN ENABLED (Next /\ k \notin peticion')
         ELSE ENABLED (Next /\ sol'[k] = IF sol[k] = "respuesta_si" THEN "resuelta" ELSE "denegada")

\* Regla 8. Al llegar al tope el encargo deja una espera visible: la vuelta que alcanza
\* Tope (AlTopeEspera) deja un pendiente de entrega o una inspección pendiente, y mientras
\* la cuenta esté en el tope no queda una inspección pendiente esperando evidencia
\* (FrenoVisible).
AlTopeEspera ==
  [][(cuenta < Tope /\ cuenta' >= Tope) => (pendiente' \/ pend')]_vars

FrenoVisible == (cuenta >= Tope) => ~pend

\* Regla 9. Toda espera visible por entrega con pendientes tiene salida por un ajuste del
\* propietario: la cuenta y los pendientes empiezan de nuevo.
EsperaConSalida ==
  pendiente => ENABLED (Ajustar /\ ~pendiente' /\ cuenta' = 0)

\* Regla 10. El revisor no corre más de Tope veces seguidas sin información nueva. Para el
\* freno (dominio._contar_no_concluyentes) la información nueva es un ajuste o una respuesta
\* del propietario: cada revisión consumida pidiendo lo mismo, aunque sea con otra consulta
\* (traba 4), suma a `cuenta`. Con main parar y continuar en el tope no vuelve a lanzar
\* (FrenoSinReabrir); el código anterior lo hacía (reabierto).
FrenoRevisor == cuenta <= Tope \/ reabierto
FrenoSinReabrir == cuenta <= Tope

\* Regla 11. Una pregunta abierta nunca queda callada: si no hay un revisor ni una
\* herramienta, el motor tiene un paso (sin contar la quietud) para seguir. Antes de #152
\* la pregunta bloqueaba y el pedido repetido no se consumía: el encargo quedaba sin
\* proceso y sin paso hasta la quietud (hallazgo-pregunta-callada).
PreguntaNuncaCallada ==
  (Abierto /\ PreguntaAbierta /\ ~pendiente) =>
    (proc \/ Corriendo \/ ENABLED (Lanzar \/ Incorporar \/ Atender \/ Impedir \/ Recoger \/ ReponePeticion))

\* Regla 12. El revisor no se lanza con un sí o un no del propietario todavía sin ejecutar.
\* cli.paso llama continuar_respuestas antes de barrer; el revisor recibe en su primer
\* turno la respuesta ya ejecutada o denegada. El código anterior invertía el orden
\* (hallazgo-respuesta-sin-ejecutar).
RevisorEsperaLaRespuesta ==
  [][Lanzar => \A k \in Todos : sol[k] \notin Respondida]_vars

\* No se exploran más reintentos que identidades hay en el modelo.
DentroDeLaCota == Cardinality({k \in Ids : sol[k] = "detenida"}) < MaxIds
====
