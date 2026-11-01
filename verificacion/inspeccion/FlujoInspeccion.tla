---- MODULE FlujoInspeccion ----
\* Una fase de un encargo con el revisor pidiendo evidencia por el buzón, y el
\* propietario parando, continuando, corrigiendo o respondiendo en cualquier momento.
\* Una herramienta asíncrona (ci.correr) puede tardar más que un turno: se lanza, el
\* turno del agente termina o lo matan, el intento cambia, y después termina.
\* Cada acción nombra la función del motor que la hace. Las diferencias entre
\* versiones del código son las CONSTANTES booleanas de abajo; cada una dice qué
\* rama del código representa. Cobertura y commit de sincronía: SKILL.md de
\* .claude/skills/verificar-inspeccion.
EXTENDS Naturals, FiniteSets

CONSTANTS
  MaxIds,              \* identidades distintas para el mismo pedido (ciclo._id_inspeccion
                       \* salta las "detenida" con :reintentoN)
  RegistraAlLigar,     \* ciclo._ligar_inspeccion llama necesidades.registrar_pedida
  ReligaSinSolicitud,  \* ciclo._ligar_inspeccion: el "ya ligada, no hago nada" exige
                       \* `previa is not None` (si no, vuelve a encolar)
  LigaHuerfanaBloquea, \* dominio.inspeccion_bloquea devuelve True con `sol is None`
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
  SoloMataLoDelTurno   \* ciclo.barrer con `decision["matar"]` llama
                       \* cerrar_herramientas(solo_del_turno=True), que salta ci.correr.
                       \* Sin ella mata también la corrida de CI.

Ids == 1..MaxIds
Estados == {"recibido", "ejecutando", "deteniendo", "detenido", "terminado"}
Herramientas == {"ninguna", "corre", "termino", "entregada"}

VARIABLES
  est,      \* Encargo.estado
  proc,     \* hay un revisor corriendo (Encargo.proc, árbol vivo)
  juicio,   \* outcome.json del revisor sin incorporar: "ninguno" | "aprueba" | "pide"
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
  corridaMuerta \* historia: una corrida viva murió porque murió el turno

vars == <<est, proc, juicio, pend, liga, sol, peticion, vista, ajuste, imped, carrera,
          herr, aqui, corridaMuerta>>
nuevas == <<herr, aqui, corridaMuerta>>

Abierto == est \in {"recibido", "ejecutando"}
Sol(k) == IF k = 0 THEN "ausente" ELSE sol[k]
Terminal(s) == s \in {"resuelta", "detenida"}
Parado == est \in {"deteniendo", "detenido"}   \* necesidades._detenida_ahora: última orden parar
Corriendo == \E k \in Ids : herr[k] = "corre"
SinIntentoAnterior == [k \in Ids |-> FALSE]    \* ningún resultado queda en el intento vigente

\* dominio.ajuste_sin_ver_por_inspeccion
AjusteSinVer == AjusteAbre /\ pend /\ ajuste

\* dominio.inspeccion_bloquea
Bloquea ==
  IF ~pend THEN FALSE
  ELSE IF AjusteSinVer THEN FALSE
  ELSE IF liga = 0 THEN TRUE
  ELSE IF Sol(liga) = "ausente" THEN LigaHuerfanaBloquea
  ELSE IF Sol(liga) = "pendiente" THEN TRUE
  ELSE vista[liga]

\* dominio.inspeccion_consumida (alimenta espera_inspeccion_consumida)
Consumida ==
  pend /\ ~AjusteSinVer /\ liga /= 0 /\ Terminal(Sol(liga)) /\ vista[liga]

\* ciclo._id_inspeccion: el mismo pedido; una "detenida" pasa al siguiente reintento
IdPedido == CHOOSE k \in Ids : sol[k] /= "detenida" /\ \A j \in 1..(k - 1) : sol[j] = "detenida"

TypeOK ==
  /\ est \in Estados /\ proc \in BOOLEAN /\ juicio \in {"ninguno", "aprueba", "pide"}
  /\ pend \in BOOLEAN /\ liga \in 0..MaxIds
  /\ sol \in [Ids -> {"ausente", "pendiente", "resuelta", "detenida"}]
  /\ peticion \subseteq Ids /\ vista \in [Ids -> BOOLEAN]
  /\ ajuste \in BOOLEAN /\ imped \in BOOLEAN /\ carrera \in BOOLEAN
  /\ herr \in [Ids -> Herramientas] /\ aqui \in [Ids -> BOOLEAN] /\ corridaMuerta \in BOOLEAN

Init ==
  /\ est = "ejecutando" /\ proc = TRUE /\ juicio = "ninguno"
  /\ pend = FALSE /\ liga = 0 /\ sol = [k \in Ids |-> "ausente"]
  /\ peticion = {} /\ vista = [k \in Ids |-> FALSE]
  /\ ajuste = FALSE /\ imped = FALSE /\ carrera = FALSE
  /\ herr = [k \in Ids |-> "ninguna"] /\ aqui = SinIntentoAnterior /\ corridaMuerta = FALSE

\* El supervisor lanza al revisor: dominio.decision_proceso devuelve lanzar (sin
\* impedimento, sin espera_inspeccion_consumida, sin inspeccion_bloquea). Al abrir el
\* intento el ajuste queda visto. La marca inspeccion_entregada no se anota aquí sino al
\* incorporar el juicio (ver SaltaInspeccion): un revisor muerto sin juicio se relanza
\* con el mismo dato. El intento nuevo deja atrás la carpeta de las herramientas anteriores.
Lanzar ==
  /\ Abierto /\ ~proc /\ juicio = "ninguno" /\ ~imped /\ ~Consumida /\ ~Bloquea
  /\ proc' = TRUE /\ est' = "ejecutando" /\ ajuste' = FALSE
  /\ carrera' = (carrera \/ (pend /\ liga \in peticion /\ ~Terminal(Sol(liga)) /\ ~AjusteSinVer))
  /\ aqui' = SinIntentoAnterior
  /\ UNCHANGED <<juicio, pend, liga, sol, peticion, vista, imped, herr, corridaMuerta>>

\* El revisor termina su turno y deja su juicio en outcome.json. Puede pasar también
\* con la parada ya pedida.
Termina ==
  /\ proc /\ juicio = "ninguno"
  /\ proc' = FALSE /\ juicio' \in {"aprueba", "pide"}
  /\ UNCHANGED <<est, pend, liga, sol, peticion, vista, ajuste, imped, carrera>>
  /\ UNCHANGED nuevas

\* ciclo.barrer -> _incorporar -> _incorporar_revisor (guarda en ciclo.barrer: estado
\* no deteniendo/detenido/terminado e intentos_nuevos). "pide" = no_concluyente con
\* inspección: _devolver(..., "inspeccionar") y _ligar_inspeccion.
LigarPide ==
  LET k == IdPedido IN
  IF liga = k /\ (~ReligaSinSolicitud \/ sol[k] /= "ausente")
    THEN UNCHANGED <<liga, sol, peticion>>
  ELSE IF sol[k] /= "ausente" /\ vista[k]
    THEN UNCHANGED <<liga, sol, peticion>>
  ELSE IF sol[k] /= "ausente"
    THEN liga' = k /\ UNCHANGED <<sol, peticion>>
  ELSE /\ liga' = k
       /\ peticion' = peticion \cup {k}
       /\ sol' = IF RegistraAlLigar THEN [sol EXCEPT ![k] = "pendiente"] ELSE sol

\* ciclo._consumir_ligada: al cambiar de consulta (o al aprobar), la respuesta ya
\* entregada de la solicitud ligada queda consumida.
VistaAlCambiar ==
  IF liga /= 0 /\ Terminal(Sol(liga)) THEN [vista EXCEPT ![liga] = TRUE] ELSE vista

\* ciclo._salta_inspeccion (antes de incorporar, solo con inspeccion_pendiente): un juicio
\* que vuelve a pedir la consulta ligada no se consume como revisión. Si la solicitud ya
\* tiene resultado, acredita que se entregó (anota inspeccion_entregada). Con la liga sin
\* solicitud, el código de #139 sí lo consume para volver a encolar (ReligaSinSolicitud).
Salta(v) ==
  /\ pend /\ juicio = "pide"
  /\ LET k == IdPedido IN
       IF k /= liga THEN sol[k] /= "ausente" /\ v[k]
       ELSE sol[k] /= "ausente" \/ ~ReligaSinSolicitud

Incorporar ==
  /\ Abierto /\ ~proc /\ juicio /= "ninguno"
  /\ juicio' = "ninguno"
  /\ LET v == IF pend /\ IdPedido /= liga THEN VistaAlCambiar ELSE vista IN
     IF Salta(v)
       THEN /\ vista' = IF IdPedido = liga /\ Terminal(Sol(liga))
                          THEN [vista EXCEPT ![liga] = TRUE] ELSE v
            /\ UNCHANGED <<est, pend, liga, sol, peticion>>
     ELSE IF juicio = "aprueba"
       THEN /\ est' = "terminado" /\ pend' = FALSE /\ vista' = v
            /\ UNCHANGED <<liga, sol, peticion>>
       ELSE /\ est' = "recibido" /\ pend' = TRUE /\ vista' = v
            /\ LigarPide
  /\ UNCHANGED <<proc, ajuste, imped, carrera>>
  /\ UNCHANGED nuevas

\* necesidades.atender, una petición del buzón por paso.
Atender ==
  /\ est /= "terminado"
  /\ \E k \in peticion :
       \/ /\ Terminal(sol[k])                    \* previa con resultado: responde y archiva
          /\ peticion' = peticion \ {k}
          /\ UNCHANGED <<sol, vista, herr, aqui>>
       \/ /\ ~Terminal(sol[k]) /\ Parado         \* rama _detenida_ahora
          /\ sol' = IF DetieneLaPendiente /\ sol[k] = "pendiente"
                      THEN [sol EXCEPT ![k] = "detenida"] ELSE sol
          /\ vista' = [vista EXCEPT ![k] = FALSE]
          /\ peticion' = peticion \ {k}
          /\ UNCHANGED <<herr, aqui>>
       \/ /\ sol[k] = "ausente" /\ ~Parado       \* _registrar_solicitud
          /\ sol' = [sol EXCEPT ![k] = "pendiente"]
          /\ UNCHANGED <<peticion, vista, herr, aqui>>
       \/ /\ sol[k] = "pendiente" /\ ~Parado /\ herr[k] = "ninguna"
          \* una herramienta que termina en el acto: resolucion
          /\ sol' = [sol EXCEPT ![k] = "resuelta"]
          /\ vista' = [vista EXCEPT ![k] = FALSE]
          /\ peticion' = peticion \ {k}
          /\ UNCHANGED <<herr, aqui>>
       \/ /\ sol[k] = "pendiente" /\ ~Parado /\ herr[k] = "ninguna"
          \* ci.correr: _recordar_carpeta y _ejecutar la lanzan; la petición sigue en el
          \* buzón y la solicitud queda pendiente. Su carpeta es la del intento vigente.
          /\ herr' = [herr EXCEPT ![k] = "corre"]
          /\ aqui' = [aqui EXCEPT ![k] = TRUE]
          /\ UNCHANGED <<sol, vista, peticion>>
  /\ UNCHANGED <<est, proc, juicio, pend, liga, ajuste, imped, carrera, corridaMuerta>>

\* La herramienta de CI termina por su cuenta (capacidades.main deja resultado.json en
\* su carpeta). No depende del turno ni del estado del encargo: puede ser después de que
\* el turno murió, de que cambió el intento o con el encargo detenido.
HerramientaTermina ==
  /\ \E k \in Ids :
       /\ herr[k] = "corre"
       /\ herr' = [herr EXCEPT ![k] = "termino"]
  /\ UNCHANGED <<est, proc, juicio, pend, liga, sol, peticion, vista, ajuste, imped, carrera>>
  /\ UNCHANGED <<aqui, corridaMuerta>>

\* necesidades._recoger (primer paso de atender): la solicitud pendiente, o detenida, cuya
\* herramienta terminó se resuelve con ese resultado (un `ok`; uno que no lo es deja la
\* detenida como está) y le llega a la fase. Con RecuerdaCarpeta lo busca donde se lanzó;
\* sin ella solo lo encuentra si el intento vigente es el que la lanzó. El código de
\* e0f06a2 además exigía la petición sin archivar; el modelo no (más favorable a hoy).
RecogerDe(k) ==
  /\ est /= "terminado"
  /\ herr[k] = "termino" /\ sol[k] \in {"pendiente", "detenida"}
  /\ RecuerdaCarpeta \/ aqui[k]
  /\ sol' = [sol EXCEPT ![k] = "resuelta"]
  /\ vista' = [vista EXCEPT ![k] = FALSE]
  /\ herr' = [herr EXCEPT ![k] = "entregada"]
  /\ peticion' = peticion \ {k}
  /\ UNCHANGED <<est, proc, juicio, pend, liga, ajuste, imped, carrera>>
  /\ UNCHANGED <<aqui, corridaMuerta>>

Recoger == \E k \in Ids : RecogerDe(k)

\* El turno del agente termina (o se vence) y el siguiente abre otro intento:
\* ciclo._directorio apunta a la carpeta nueva y las herramientas lanzadas antes quedan
\* en la del intento anterior.
CambiaIntento ==
  /\ Abierto
  /\ aqui' = SinIntentoAnterior
  /\ UNCHANGED <<est, proc, juicio, pend, liga, sol, peticion, vista, ajuste, imped, carrera>>
  /\ UNCHANGED <<herr, corridaMuerta>>

\* ciclo.barrer con `decision["matar"]` sin parada (la sesión se marcó colgada): mata el
\* turno y cierra las herramientas. Con SoloMataLoDelTurno la corrida de CI sigue; sin ella
\* muere con el turno (en el código su solicitud acaba en error; aquí, sin resultado).
MatarTurno ==
  /\ Abierto
  /\ proc' = FALSE
  /\ aqui' = SinIntentoAnterior
  /\ IF SoloMataLoDelTurno
       THEN UNCHANGED <<sol, vista, herr, corridaMuerta>>
       ELSE /\ herr' = [k \in Ids |-> IF herr[k] = "corre" THEN "ninguna" ELSE herr[k]]
            /\ sol' = [k \in Ids |->
                         IF herr[k] = "corre" /\ sol[k] = "pendiente" THEN "detenida" ELSE sol[k]]
            /\ vista' = [k \in Ids |->
                           IF herr[k] = "corre" /\ sol[k] = "pendiente" THEN FALSE ELSE vista[k]]
            /\ corridaMuerta' = (corridaMuerta \/ Corriendo)
  /\ UNCHANGED <<est, juicio, pend, liga, peticion, ajuste, imped, carrera>>

\* ciclo.barrer con la parada o la orden parar/cancelar vigente:
\* necesidades.cerrar_herramientas_del_encargo cierra las herramientas de todos los
\* intentos, con la limpieza de rama y PR de la de CI. Su solicitud queda detenida.
CerrarHerramientas ==
  /\ Parado /\ Corriendo
  /\ herr' = [k \in Ids |-> IF herr[k] = "corre" THEN "ninguna" ELSE herr[k]]
  /\ sol' = [k \in Ids |->
               IF herr[k] = "corre" /\ sol[k] = "pendiente" THEN "detenida" ELSE sol[k]]
  /\ vista' = [k \in Ids |->
                 IF herr[k] = "corre" /\ sol[k] = "pendiente" THEN FALSE ELSE vista[k]]
  /\ UNCHANGED <<est, proc, juicio, pend, liga, peticion, ajuste, imped, carrera>>
  /\ UNCHANGED <<aqui, corridaMuerta>>

\* dominio.decision_proceso: espera_inspeccion_consumida -> evento impedimento
Impedir ==
  /\ Abierto /\ ~proc /\ juicio = "ninguno" /\ ~imped /\ Consumida
  /\ imped' = TRUE
  /\ UNCHANGED <<est, proc, juicio, pend, liga, sol, peticion, vista, ajuste, carrera>>
  /\ UNCHANGED nuevas

\* director parar -> dominio._orden "parar"
Parar ==
  /\ est \in {"recibido", "ejecutando"}
  /\ est' = "deteniendo"
  /\ UNCHANGED <<proc, juicio, pend, liga, sol, peticion, vista, ajuste, imped, carrera>>
  /\ UNCHANGED nuevas

\* ciclo.barrer con la parada: mata el árbol vivo; el revisor muere sin juicio
Matar ==
  /\ est = "deteniendo" /\ proc
  /\ proc' = FALSE
  /\ UNCHANGED <<est, juicio, pend, liga, sol, peticion, vista, ajuste, imped, carrera>>
  /\ UNCHANGED nuevas

\* ciclo.barrer: deteniendo con el árbol ausente -> detenido
Detener ==
  /\ est = "deteniendo" /\ ~proc
  /\ est' = "detenido"
  /\ UNCHANGED <<proc, juicio, pend, liga, sol, peticion, vista, ajuste, imped, carrera>>
  /\ UNCHANGED nuevas

\* director continuar -> dominio._orden "continuar" -> _retomar
Continuar ==
  /\ est = "detenido"
  /\ est' = "recibido"
  /\ UNCHANGED <<proc, juicio, pend, liga, sol, peticion, vista, ajuste, imped, carrera>>
  /\ UNCHANGED nuevas

\* motor corregir -> reducción "ajuste"
Ajustar ==
  /\ est /= "terminado"
  /\ ajuste' = TRUE
  /\ imped' = IF AjusteSuelta THEN FALSE ELSE imped
  /\ UNCHANGED <<est, proc, juicio, pend, liga, sol, peticion, vista, carrera>>
  /\ UNCHANGED nuevas

\* director responder -> dominio._respuesta, a la solicitud ligada
Responder ==
  /\ est /= "terminado" /\ liga /= 0 /\ sol[liga] /= "ausente" /\ RespuestaReabre
  /\ vista' = [vista EXCEPT ![liga] = FALSE]
  /\ imped' = FALSE
  /\ UNCHANGED <<est, proc, juicio, pend, liga, sol, peticion, ajuste, carrera>>
  /\ UNCHANGED nuevas

Terminado ==
  /\ est = "terminado"
  /\ UNCHANGED vars

Motor == Lanzar \/ Termina \/ Incorporar \/ Atender \/ Impedir \/ Matar \/ Detener
         \/ HerramientaTermina \/ Recoger \/ CambiaIntento \/ MatarTurno \/ CerrarHerramientas
Propietario == Parar \/ Continuar \/ Ajustar \/ Responder
Next == Motor \/ Propietario \/ Terminado

Spec == Init /\ [][Next]_vars

\* Regla 1. Un encargo abierto sin una espera visible siempre tiene algo que el motor
\* hace solo: un revisor corriendo, una corrida de CI en marcha, o un paso del motor posible.
SinAtascoMudo ==
  (Abierto /\ ~imped) =>
    (proc \/ Corriendo \/ ENABLED (Lanzar \/ Incorporar \/ Atender \/ Impedir \/ Recoger))

\* Regla 2. El revisor no vuelve a correr, sin un ajuste nuevo, mientras la inspección
\* que pidió sigue sin despachar en el buzón.
SinCarrera == ~carrera

\* Regla 3. Un impedimento de inspección siempre tiene salida por el propietario: un
\* ajuste o una respuesta lo levanta sin cancelar el encargo.
ImpedimentoConSalida == imped => ENABLED (Propietario /\ ~imped')

\* Regla 4. Una herramienta que terminó siempre entrega su resultado a la fase: el
\* motor puede registrarlo en su solicitud, sea cual sea el intento vigente.
ResultadoEntregado ==
  \A k \in Ids : (herr[k] = "termino" /\ est /= "terminado") => ENABLED RecogerDe(k)

\* Regla 5. Una corrida de CI en marcha no muere porque muera el turno del agente: solo
\* la detienen parar o cancelar el encargo.
CorridaSobreviveAlTurno == ~corridaMuerta

\* No se exploran más reintentos que identidades hay en el modelo.
DentroDeLaCota == Cardinality({k \in Ids : sol[k] = "detenida"}) < MaxIds
====
