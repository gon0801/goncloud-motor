---- MODULE FlujoInspeccion ----
\* Una fase de un encargo con el revisor pidiendo evidencia por el buzón, y el
\* propietario parando, continuando, corrigiendo o respondiendo en cualquier momento.
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
  RespuestaReabre      \* dominio._respuesta a una solicitud que no es "pregunta": suma
                       \* `tardias` (marca nueva) y llama _soltar_impedimento_de_inspeccion

Ids == 1..MaxIds
Estados == {"recibido", "ejecutando", "deteniendo", "detenido", "terminado"}

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
  carrera   \* historia: se lanzó el revisor sin información nueva con su inspección
            \* todavía sin despachar en el buzón

vars == <<est, proc, juicio, pend, liga, sol, peticion, vista, ajuste, imped, carrera>>

Abierto == est \in {"recibido", "ejecutando"}
Sol(k) == IF k = 0 THEN "ausente" ELSE sol[k]
Terminal(s) == s \in {"resuelta", "detenida"}
Parado == est \in {"deteniendo", "detenido"}   \* necesidades._detenida_ahora: última orden parar

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

Init ==
  /\ est = "ejecutando" /\ proc = TRUE /\ juicio = "ninguno"
  /\ pend = FALSE /\ liga = 0 /\ sol = [k \in Ids |-> "ausente"]
  /\ peticion = {} /\ vista = [k \in Ids |-> FALSE]
  /\ ajuste = FALSE /\ imped = FALSE /\ carrera = FALSE

\* El supervisor lanza al revisor: dominio.decision_proceso devuelve lanzar (sin
\* impedimento, sin espera_inspeccion_consumida, sin inspeccion_bloquea). Al abrir el
\* intento el ajuste queda visto. La marca inspeccion_entregada no se anota aquí sino al
\* incorporar el juicio (ver SaltaInspeccion): un revisor muerto sin juicio se relanza
\* con el mismo dato.
Lanzar ==
  /\ Abierto /\ ~proc /\ juicio = "ninguno" /\ ~imped /\ ~Consumida /\ ~Bloquea
  /\ proc' = TRUE /\ est' = "ejecutando" /\ ajuste' = FALSE
  /\ carrera' = (carrera \/ (pend /\ liga \in peticion /\ ~Terminal(Sol(liga)) /\ ~AjusteSinVer))
  /\ UNCHANGED <<juicio, pend, liga, sol, peticion, vista, imped>>

\* El revisor termina su turno y deja su juicio en outcome.json. Puede pasar también
\* con la parada ya pedida.
Termina ==
  /\ proc /\ juicio = "ninguno"
  /\ proc' = FALSE /\ juicio' \in {"aprueba", "pide"}
  /\ UNCHANGED <<est, pend, liga, sol, peticion, vista, ajuste, imped, carrera>>

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

\* necesidades.atender, una petición del buzón por paso.
Atender ==
  /\ est /= "terminado"
  /\ \E k \in peticion :
       \/ /\ Terminal(sol[k])                    \* previa con resultado: responde y archiva
          /\ peticion' = peticion \ {k}
          /\ UNCHANGED <<sol, vista>>
       \/ /\ ~Terminal(sol[k]) /\ Parado         \* rama _detenida_ahora
          /\ sol' = IF DetieneLaPendiente /\ sol[k] = "pendiente"
                      THEN [sol EXCEPT ![k] = "detenida"] ELSE sol
          /\ vista' = [vista EXCEPT ![k] = FALSE]
          /\ peticion' = peticion \ {k}
       \/ /\ sol[k] = "ausente" /\ ~Parado       \* _registrar_solicitud
          /\ sol' = [sol EXCEPT ![k] = "pendiente"]
          /\ UNCHANGED <<peticion, vista>>
       \/ /\ sol[k] = "pendiente" /\ ~Parado     \* la herramienta termina: resolucion
          /\ sol' = [sol EXCEPT ![k] = "resuelta"]
          /\ vista' = [vista EXCEPT ![k] = FALSE]
          /\ peticion' = peticion \ {k}
  /\ UNCHANGED <<est, proc, juicio, pend, liga, ajuste, imped, carrera>>

\* dominio.decision_proceso: espera_inspeccion_consumida -> evento impedimento
Impedir ==
  /\ Abierto /\ ~proc /\ juicio = "ninguno" /\ ~imped /\ Consumida
  /\ imped' = TRUE
  /\ UNCHANGED <<est, proc, juicio, pend, liga, sol, peticion, vista, ajuste, carrera>>

\* director parar -> dominio._orden "parar"
Parar ==
  /\ est \in {"recibido", "ejecutando"}
  /\ est' = "deteniendo"
  /\ UNCHANGED <<proc, juicio, pend, liga, sol, peticion, vista, ajuste, imped, carrera>>

\* ciclo.barrer con la parada: mata el árbol vivo; el revisor muere sin juicio
Matar ==
  /\ est = "deteniendo" /\ proc
  /\ proc' = FALSE
  /\ UNCHANGED <<est, juicio, pend, liga, sol, peticion, vista, ajuste, imped, carrera>>

\* ciclo.barrer: deteniendo con el árbol ausente -> detenido
Detener ==
  /\ est = "deteniendo" /\ ~proc
  /\ est' = "detenido"
  /\ UNCHANGED <<proc, juicio, pend, liga, sol, peticion, vista, ajuste, imped, carrera>>

\* director continuar -> dominio._orden "continuar" -> _retomar
Continuar ==
  /\ est = "detenido"
  /\ est' = "recibido"
  /\ UNCHANGED <<proc, juicio, pend, liga, sol, peticion, vista, ajuste, imped, carrera>>

\* motor corregir -> reducción "ajuste"
Ajustar ==
  /\ est /= "terminado"
  /\ ajuste' = TRUE
  /\ imped' = IF AjusteSuelta THEN FALSE ELSE imped
  /\ UNCHANGED <<est, proc, juicio, pend, liga, sol, peticion, vista, carrera>>

\* director responder -> dominio._respuesta, a la solicitud ligada
Responder ==
  /\ est /= "terminado" /\ liga /= 0 /\ sol[liga] /= "ausente" /\ RespuestaReabre
  /\ vista' = [vista EXCEPT ![liga] = FALSE]
  /\ imped' = FALSE
  /\ UNCHANGED <<est, proc, juicio, pend, liga, sol, peticion, ajuste, carrera>>

Terminado ==
  /\ est = "terminado"
  /\ UNCHANGED vars

Motor == Lanzar \/ Termina \/ Incorporar \/ Atender \/ Impedir \/ Matar \/ Detener
Propietario == Parar \/ Continuar \/ Ajustar \/ Responder
Next == Motor \/ Propietario \/ Terminado

Spec == Init /\ [][Next]_vars

\* Regla 1. Un encargo abierto sin una espera visible siempre tiene algo que el motor
\* hace solo: un revisor corriendo, o un paso del motor posible.
SinAtascoMudo ==
  (Abierto /\ ~imped) => (proc \/ ENABLED (Lanzar \/ Incorporar \/ Atender \/ Impedir))

\* Regla 2. El revisor no vuelve a correr, sin un ajuste nuevo, mientras la inspección
\* que pidió sigue sin despachar en el buzón.
SinCarrera == ~carrera

\* Regla 3. Un impedimento de inspección siempre tiene salida por el propietario: un
\* ajuste o una respuesta lo levanta sin cancelar el encargo.
ImpedimentoConSalida == imped => ENABLED (Propietario /\ ~imped')

\* No se exploran más reintentos que identidades hay en el modelo.
DentroDeLaCota == Cardinality({k \in Ids : sol[k] = "detenida"}) < MaxIds
====
