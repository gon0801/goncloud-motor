---- MODULE FlujoInspeccion ----
\* Una fase de un encargo con el revisor pidiendo evidencia por el buzón, y el
\* propietario parando, continuando, corrigiendo o respondiendo en cualquier momento.
\* Una herramienta asíncrona (ci.correr) puede tardar más que un turno: se lanza, el
\* turno del agente termina o lo matan, el intento cambia, y después termina.
\* Un recurso con `autorizacion: "propietario"` hace que el pedido nazca como pregunta: el
\* encargo pasa a necesita_usuario y espera el sí o el no del propietario. Un revisor que
\* repite el mismo pedido sin información nueva topa con el freno de vueltas sin concluir.
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
  SoloMataLoDelTurno,  \* ciclo.barrer con `decision["matar"]` llama
                       \* cerrar_herramientas(solo_del_turno=True), que salta ci.correr.
                       \* Sin ella mata también la corrida de CI.
  AvisaPregunta,       \* una pregunta abierta no queda muda: dominio._retomar deja
                       \* necesita_usuario (pregunta_de_inspeccion) y dominio.sin_nada_en_curso
                       \* no la da por sostenida. Sin ella (72c30a8) _retomar deja recibido y
                       \* sin_nada_en_curso la da por sostenida por inspeccion_bloquea.
  RespuestaVigente,    \* dominio._respuesta usa respuesta_vigente (basta la fase). Sin ella
                       \* (72c30a8) usa solicitud_vigente: la pregunta de un intento anterior
                       \* ya no recibe la respuesta.
  FrenoCuenta          \* dominio._contar_no_concluyentes (y la guarda de
                       \* ciclo._cerrar_con_juicio): la Tope-ésima vuelta pasa a necesita_usuario.
                       \* Sin ella (antes del freno, ac41797) la cuenta no existe.

Ids == 1..MaxIds
PedAg == MaxIds + 1   \* el pedido de ci.correr que el agente hace por el buzón (no es de la inspección)
Todos == 1..PedAg
Estados == {"recibido", "ejecutando", "necesita_usuario", "deteniendo", "detenido", "terminado"}
EstadosSol == {"ausente", "pendiente", "pregunta", "respuesta_si", "respuesta_no",
               "resuelta", "denegada", "detenida"}
Respondida == {"respuesta_si", "respuesta_no"}   \* Solicitud.estado == "respuesta", afirmativa o no
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
  corridaMuerta, \* historia: una corrida viva murió porque murió el turno
  caso,     \* qué se explora (no cambia durante el encargo): "ci" = la herramienta asíncrona
            \* sin el freno ni autorización; "autoriza" = el recurso de la inspección tiene
            \* `autorizacion: "propietario"` (capacidades.cargar); "freno" = el revisor repite el
            \* pedido. Los dos últimos van sin ci.correr: ver "Lo que no cubre" en SKILL.md.
  cuenta,   \* Encargo.no_concluyentes, saturada en Tope + 1: revisiones no_concluyente
            \* seguidas sobre la misma versión y pidiendo lo mismo
  ligaVigente, \* la solicitud ligada la registró el intento vigente (Solicitud.intento == Encargo.intento)
  sinInfo   \* historia: revisiones no_concluyente consumidas desde la última información nueva
            \* (ajuste, respuesta o resolución de la solicitud ligada), saturada en Tope + 1

vars == <<est, proc, juicio, pend, liga, sol, peticion, vista, ajuste, imped, carrera,
          herr, aqui, corridaMuerta, caso, cuenta, ligaVigente, sinInfo>>
nuevas == <<herr, aqui, corridaMuerta>>
fijas == <<caso>>
contadores == <<cuenta, ligaVigente, sinInfo>>

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
BloqueaEn(p, l, s, v, a) ==
  IF ~p THEN FALSE
  ELSE IF AjusteAbre /\ a THEN FALSE
  ELSE IF l = 0 THEN TRUE
  ELSE IF s[l] = "ausente" THEN LigaHuerfanaBloquea
  ELSE IF s[l] \in {"pendiente", "pregunta"} THEN TRUE
  ELSE v[l]

Bloquea == BloqueaEn(pend, liga, sol, vista, ajuste)

\* dominio.pregunta_de_inspeccion: la solicitud ligada que espera al propietario y bloquea.
PreguntaEn(p, l, s, v, a) == p /\ l /= 0 /\ s[l] = "pregunta" /\ BloqueaEn(p, l, s, v, a)
PreguntaAbierta == PreguntaEn(pend, liga, sol, vista, ajuste)

\* dominio.inspeccion_consumida (alimenta espera_inspeccion_consumida)
Consumida ==
  pend /\ ~AjusteSinVer /\ liga /= 0 /\ ConResultado(Sol(liga)) /\ vista[liga]

\* ciclo._id_inspeccion: el mismo pedido; una "detenida" pasa al siguiente reintento
IdPedido == CHOOSE k \in Ids : sol[k] /= "detenida" /\ \A j \in 1..(k - 1) : sol[j] = "detenida"

\* necesidades._recoger puede registrar el resultado de la herramienta de k
HayQueRecoger(k) ==
  /\ est /= "terminado"
  /\ herr[k] = "termino" /\ sol[k] \in {"pendiente", "detenida"}
  /\ (RecuerdaCarpeta \/ aqui[k])

\* ciclo.barrer: atender corre antes de decidir el lanzamiento. Una respuesta del propietario
\* con su petición en el buzón (necesidades._reponer_peticion) se ejecuta o se deniega antes.
PorAtender == \E k \in peticion : sol[k] \in Respondida

\* dominio._reanudar_inspeccion: con la solicitud ligada ya sin bloquear, el encargo que
\* esperaba al propietario vuelve a recibido. s2 y v2 son las solicitudes y marcas resultantes.
Reanuda(k, s2, v2) ==
  IF est = "necesita_usuario" /\ liga = k /\ ~BloqueaEn(pend, liga, s2, v2, ajuste)
    THEN "recibido" ELSE est

\* Información nueva para el revisor, según dominio.inspeccion_bloquea: la solicitud ligada
\* recibe un resultado (o cambia el que tenía: una detenida que pasa a resuelta). Reinicia
\* la cuenta de revisiones consumidas sin información (sinInfo); el contador del freno
\* (`cuenta`) no se reinicia así en el código, solo con un ajuste o una respuesta.
SinInfoTras(s2) ==
  IF liga /= 0 /\ s2[liga] /= sol[liga] /\ ConResultado(s2[liga]) THEN 0 ELSE sinInfo

TypeOK ==
  /\ est \in Estados /\ proc \in BOOLEAN /\ juicio \in {"ninguno", "aprueba", "pide"}
  /\ pend \in BOOLEAN /\ liga \in 0..MaxIds
  /\ sol \in [Todos -> EstadosSol]
  /\ peticion \subseteq Todos /\ vista \in [Todos -> BOOLEAN]
  /\ ajuste \in BOOLEAN /\ imped \in BOOLEAN /\ carrera \in BOOLEAN
  /\ herr \in [Todos -> Herramientas] /\ aqui \in [Todos -> BOOLEAN] /\ corridaMuerta \in BOOLEAN
  /\ caso \in {"ci", "autoriza", "freno"} /\ cuenta \in 0..(Tope + 1) /\ ligaVigente \in BOOLEAN
  /\ sinInfo \in 0..(Tope + 1)

Init ==
  /\ est = "ejecutando" /\ proc = TRUE /\ juicio = "ninguno"
  /\ pend = FALSE /\ liga = 0 /\ sol = [k \in Todos |-> "ausente"]
  /\ peticion = {} /\ vista = [k \in Todos |-> FALSE]
  /\ ajuste = FALSE /\ imped = FALSE /\ carrera = FALSE
  /\ herr = [k \in Todos |-> "ninguna"] /\ aqui = SinIntentoAnterior /\ corridaMuerta = FALSE
  /\ caso \in {"ci", "autoriza", "freno"} /\ cuenta = 0 /\ ligaVigente = FALSE /\ sinInfo = 0

\* dominio.decision_proceso devuelve lanzar (sin impedimento, sin espera_inspeccion_consumida,
\* sin inspeccion_bloquea).
LanzarPosible ==
  /\ Abierto /\ ~proc /\ juicio = "ninguno" /\ ~imped /\ ~Consumida /\ ~Bloquea
  /\ \A k \in Todos : ~HayQueRecoger(k)   \* ciclo.barrer: atender (y su _recoger) corre antes de decidir el lanzamiento
  /\ ~PorAtender

\* El supervisor lanza al revisor. Al abrir el intento el ajuste queda visto. La marca
\* inspeccion_entregada no se anota aquí sino al incorporar el juicio (ver Salta): un revisor
\* muerto sin juicio se relanza con el mismo dato. El intento nuevo deja atrás la carpeta de
\* las herramientas anteriores y deja las solicitudes de los intentos anteriores sin vigencia.
Lanzar ==
  /\ LanzarPosible
  /\ proc' = TRUE /\ est' = "ejecutando" /\ ajuste' = FALSE
  /\ carrera' = (carrera \/ (pend /\ liga \in peticion /\ ~Terminal(Sol(liga)) /\ ~AjusteSinVer))
  /\ aqui' = SinIntentoAnterior
  /\ ligaVigente' = FALSE
  /\ UNCHANGED <<juicio, pend, liga, sol, peticion, vista, imped, herr, corridaMuerta, cuenta, sinInfo>>
  /\ UNCHANGED fijas

\* El revisor termina su turno y deja su juicio en outcome.json. Puede pasar también
\* con la parada ya pedida.
Termina ==
  /\ proc /\ juicio = "ninguno"
  /\ proc' = FALSE /\ juicio' \in {"aprueba", "pide"}
  /\ UNCHANGED <<est, pend, liga, sol, peticion, vista, ajuste, imped, carrera>>
  /\ UNCHANGED <<nuevas, fijas, contadores>>

\* ciclo.barrer -> _incorporar -> _incorporar_revisor (guarda en ciclo.barrer: estado
\* no deteniendo/detenido/terminado/necesita_usuario e intentos_nuevos). "pide" = no_concluyente
\* con inspección: _devolver(..., "inspeccionar") y _ligar_inspeccion. Con un recurso que pide
\* autorización, _ligar_inspeccion registra el pedido y llama marcar_pregunta (bloquea): el
\* encargo queda en necesita_usuario con la pregunta, no en recibido.
LigarPide ==
  LET k == IdPedido IN
  IF liga = k /\ (~ReligaSinSolicitud \/ sol[k] /= "ausente")
    \* Ya ligada: con la previa en `pregunta` y el encargo abierto, marcar_pregunta otra vez.
    THEN /\ est' = IF sol[k] = "pregunta" THEN "necesita_usuario" ELSE "recibido"
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
       /\ est' = IF Exige THEN "necesita_usuario" ELSE "recibido"

\* ciclo._consumir_ligada: al cambiar de consulta (o al aprobar), la respuesta ya
\* entregada de la solicitud ligada queda consumida.
VistaAlCambiar ==
  IF liga /= 0 /\ ConResultado(Sol(liga)) THEN [vista EXCEPT ![liga] = TRUE] ELSE vista

\* ciclo._salta_inspeccion (antes de incorporar, solo con inspeccion_pendiente): un juicio
\* que vuelve a pedir la consulta ligada no se consume como revisión. Si la solicitud ya
\* tiene resultado (o una respuesta del propietario), acredita que se entregó (anota
\* inspeccion_entregada). Con la liga sin solicitud, el código de #139 sí lo consume para
\* volver a encolar (ReligaSinSolicitud).
Salta(v) ==
  /\ pend /\ juicio = "pide"
  /\ LET k == IdPedido IN
       IF k /= liga THEN sol[k] /= "ausente" /\ v[k]
       ELSE sol[k] /= "ausente" \/ ~ReligaSinSolicitud

\* Una revisión no_concluyente que se consume cuenta para el freno: ciclo._incorporar_revisor
\* -> evento `revision` -> dominio._revision -> dominio._contar_no_concluyentes. La
\* Tope-ésima deja inspeccion_pendiente vacío y el encargo en necesita_usuario; entonces
\* ciclo._cerrar_con_juicio no devuelve ni liga nada (rama `pass`). Sin FrenoCuenta (antes del
\* freno) la cuenta crece y el encargo sigue en recibido.
Incorporar ==
  /\ Abierto /\ ~proc /\ juicio /= "ninguno"
  /\ juicio' = "ninguno"
  /\ LET v == IF pend /\ IdPedido /= liga THEN VistaAlCambiar ELSE vista IN
     IF Salta(v)
       THEN /\ vista' = IF IdPedido = liga /\ ConResultado(Sol(liga))
                          THEN [vista EXCEPT ![liga] = TRUE] ELSE v
            /\ UNCHANGED <<est, pend, liga, sol, peticion, cuenta, ligaVigente, sinInfo>>
     ELSE IF juicio = "aprueba"
       THEN /\ est' = "terminado" /\ pend' = FALSE /\ vista' = v /\ cuenta' = 0 /\ sinInfo' = 0
            /\ UNCHANGED <<liga, sol, peticion, ligaVigente>>
       ELSE /\ vista' = v
            /\ cuenta' = IF ~ContandoFreno \/ cuenta >= Tope + 1 THEN cuenta ELSE cuenta + 1
            /\ sinInfo' = IF ~ContandoFreno \/ sinInfo >= Tope + 1 THEN sinInfo ELSE sinInfo + 1
            /\ IF FrenoCuenta /\ ContandoFreno /\ cuenta + 1 >= Tope
                 THEN /\ est' = "necesita_usuario" /\ pend' = FALSE
                      /\ UNCHANGED <<liga, sol, peticion, ligaVigente>>
                 ELSE /\ pend' = TRUE
                      /\ LigarPide
  /\ UNCHANGED <<proc, ajuste, imped, carrera>>
  /\ UNCHANGED <<nuevas, fijas>>

\* necesidades.atender, una petición del buzón por paso.
Atender ==
  /\ est /= "terminado"
  /\ \E k \in peticion :
       \/ /\ Terminal(sol[k])                    \* previa con resultado: responde y archiva
          /\ peticion' = peticion \ {k}
          /\ UNCHANGED <<est, sol, vista, herr, aqui, ligaVigente, sinInfo>>
       \/ /\ ~Terminal(sol[k]) /\ Parado         \* rama _detenida_ahora
          /\ sol' = IF DetieneLaPendiente /\ sol[k] = "pendiente"
                      THEN [sol EXCEPT ![k] = "detenida"] ELSE sol
          /\ vista' = [vista EXCEPT ![k] = FALSE]
          /\ peticion' = peticion \ {k}
          /\ sinInfo' = SinInfoTras(IF DetieneLaPendiente /\ sol[k] = "pendiente"
                                      THEN [sol EXCEPT ![k] = "detenida"] ELSE sol)
          /\ UNCHANGED <<est, herr, aqui, ligaVigente>>
       \/ /\ sol[k] = "ausente" /\ ~Parado       \* _registrar_solicitud
          /\ sol' = [sol EXCEPT ![k] = "pendiente"]
          /\ ligaVigente' = (~RespuestaVigente /\ (ligaVigente \/ k = liga))
          /\ UNCHANGED <<est, peticion, vista, herr, aqui, sinInfo>>
       \/ /\ sol[k] = "pendiente" /\ ~Parado /\ herr[k] = "ninguna" /\ SinAutorizar(k)
          \* una herramienta que termina en el acto: resolucion (_cerrar_con_resultado ->
          \* _anotar_resolucion -> dominio._resolucion -> _reanudar_inspeccion)
          /\ sol' = [sol EXCEPT ![k] = "resuelta"]
          /\ vista' = [vista EXCEPT ![k] = FALSE]
          /\ peticion' = peticion \ {k}
          /\ est' = Reanuda(k, [sol EXCEPT ![k] = "resuelta"], [vista EXCEPT ![k] = FALSE])
          /\ sinInfo' = SinInfoTras([sol EXCEPT ![k] = "resuelta"])
          /\ UNCHANGED <<herr, aqui, ligaVigente>>
       \/ /\ sol[k] = "pendiente" /\ ~Parado /\ herr[k] = "ninguna" /\ SinAutorizar(k) /\ ConCi
          \* ci.correr: _recordar_carpeta y _ejecutar la lanzan; la petición sigue en el
          \* buzón y la solicitud queda pendiente. Su carpeta es la del intento vigente.
          /\ herr' = [herr EXCEPT ![k] = "corre"]
          /\ aqui' = [aqui EXCEPT ![k] = TRUE]
          /\ UNCHANGED <<est, sol, vista, peticion, ligaVigente, sinInfo>>
       \/ /\ sol[k] = "pendiente" /\ ~Parado /\ herr[k] = "ninguna" /\ Exige /\ k \in Ids
          \* rama `pregunta` de necesidades.atender: capacidades.ejecutar devuelve "pregunta"
          \* sin `autorizado`; _cerrar_con_resultado anota la resolución `pregunta`. La
          \* petición sigue en el buzón y el encargo ya está en necesita_usuario (marcar_pregunta).
          /\ sol' = [sol EXCEPT ![k] = "pregunta"]
          /\ UNCHANGED <<est, vista, peticion, herr, aqui, ligaVigente, sinInfo>>
       \/ /\ sol[k] = "respuesta_si" /\ ~Parado
          \* camino `autorizado`: previa en `respuesta` y _afirmativa. _ejecutar corre la
          \* herramienta (aquí en el acto), _anotar_resolucion `resuelta` y _reanudar_inspeccion.
          /\ sol' = [sol EXCEPT ![k] = "resuelta"]
          /\ vista' = [vista EXCEPT ![k] = FALSE]
          /\ peticion' = peticion \ {k}
          /\ est' = Reanuda(k, [sol EXCEPT ![k] = "resuelta"], [vista EXCEPT ![k] = FALSE])
          /\ sinInfo' = SinInfoTras([sol EXCEPT ![k] = "resuelta"])
          /\ UNCHANGED <<herr, aqui, ligaVigente>>
       \/ /\ sol[k] = "respuesta_no" /\ ~Parado
          \* camino `denegada`: previa en `respuesta` no afirmativa. _anotar_resolucion
          \* `denegada` ("el propietario respondió: ...") y _reanudar_inspeccion: el revisor
          \* recibe la denegación como resultado.
          /\ sol' = [sol EXCEPT ![k] = "denegada"]
          /\ vista' = [vista EXCEPT ![k] = FALSE]
          /\ peticion' = peticion \ {k}
          /\ est' = Reanuda(k, [sol EXCEPT ![k] = "denegada"], [vista EXCEPT ![k] = FALSE])
          /\ sinInfo' = SinInfoTras([sol EXCEPT ![k] = "denegada"])
          /\ UNCHANGED <<herr, aqui, ligaVigente>>
  /\ UNCHANGED <<proc, juicio, pend, liga, ajuste, imped, carrera, corridaMuerta, cuenta>>
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
\* detenida como está) y le llega a la fase (_reanudar_inspeccion). Con RecuerdaCarpeta lo
\* busca donde se lanzó; sin ella solo lo encuentra si el intento vigente es el que la lanzó
\* (HayQueRecoger). El código de e0f06a2 además exigía la petición sin archivar; el modelo no
\* (más favorable a hoy).
RecogerDe(k) ==
  /\ HayQueRecoger(k)
  /\ sol' = [sol EXCEPT ![k] = "resuelta"]
  /\ vista' = [vista EXCEPT ![k] = FALSE]
  /\ est' = Reanuda(k, [sol EXCEPT ![k] = "resuelta"], [vista EXCEPT ![k] = FALSE])
  /\ herr' = [herr EXCEPT ![k] = "entregada"]
  /\ peticion' = peticion \ {k}
  /\ UNCHANGED <<proc, juicio, pend, liga, ajuste, imped, carrera>>
  /\ UNCHANGED <<aqui, corridaMuerta>>
  /\ sinInfo' = SinInfoTras([sol EXCEPT ![k] = "resuelta"])
  /\ UNCHANGED <<fijas, cuenta, ligaVigente>>

Recoger == \E k \in Todos : RecogerDe(k)

\* cli.paso (barrer y después, para cada encargo con una solicitud en `respuesta`) llama
\* necesidades.continuar_respuestas: _reponer_peticion devuelve al buzón la petición que una
\* parada archivó, y atender la ejecuta (un sí), la deniega (un no) o, con el encargo
\* parado, la vuelve a archivar. Corre después del barrido: el revisor puede lanzarse antes.
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
  /\ proc' = FALSE
  /\ IF SoloMataLoDelTurno
       THEN /\ UNCHANGED <<sol, vista, herr, corridaMuerta>>
            /\ UNCHANGED sinInfo
       ELSE /\ herr' = [k \in Todos |-> IF herr[k] = "corre" /\ aqui[k] THEN "ninguna" ELSE herr[k]]
            /\ sol' = [k \in Todos |->
                         IF herr[k] = "corre" /\ aqui[k] /\ sol[k] = "pendiente"
                           THEN "detenida" ELSE sol[k]]
            /\ vista' = [k \in Todos |->
                           IF herr[k] = "corre" /\ aqui[k] /\ sol[k] = "pendiente"
                             THEN FALSE ELSE vista[k]]
            /\ corridaMuerta' = (corridaMuerta \/ \E k \in Todos : herr[k] = "corre" /\ aqui[k])
            /\ sinInfo' = SinInfoTras([k \in Todos |->
                                         IF herr[k] = "corre" /\ aqui[k] /\ sol[k] = "pendiente"
                                           THEN "detenida" ELSE sol[k]])
  /\ UNCHANGED <<est, juicio, pend, liga, peticion, ajuste, imped, carrera, aqui>>
  /\ UNCHANGED <<fijas, cuenta, ligaVigente>>

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
  /\ sinInfo' = SinInfoTras([k \in Todos |->
                               IF herr[k] = "corre" /\ sol[k] = "pendiente" THEN "detenida" ELSE sol[k]])
  /\ UNCHANGED <<est, proc, juicio, pend, liga, peticion, ajuste, imped, carrera>>
  /\ UNCHANGED <<aqui, corridaMuerta>>
  /\ UNCHANGED <<fijas, cuenta, ligaVigente>>

\* dominio.decision_proceso: espera_inspeccion_consumida -> evento impedimento
Impedir ==
  /\ Abierto /\ ~proc /\ juicio = "ninguno" /\ ~imped /\ Consumida
  /\ imped' = TRUE
  /\ UNCHANGED <<est, proc, juicio, pend, liga, sol, peticion, vista, ajuste, carrera>>
  /\ UNCHANGED <<nuevas, fijas, contadores>>

\* dominio.sin_nada_en_curso: encargo abierto que no explica por qué no avanza. No cuenta como
\* sostenido por una inspección que bloquea si esa inspección es una pregunta del propietario
\* (pregunta_de_inspeccion), ni por esa pregunta como solicitud abierta. Sin AvisaPregunta
\* (72c30a8) la pregunta sí lo sostenía (`inspeccion_bloquea(e)` y `pregunta`/`pendiente`).
SinNadaEnCurso ==
  /\ Abierto /\ ~proc /\ ~imped
  /\ ~(Bloquea /\ ~(AvisaPregunta /\ PreguntaAbierta))
  /\ \A k \in Todos : Sol(k) \in {"pendiente", "pregunta"}
                        => (AvisaPregunta /\ PreguntaAbierta /\ k = liga)

\* ciclo._vigilar_quietud: tras _QUIETUD_VUELTAS vueltas y _QUIETUD_S segundos sin que el barrido
\* lance nada ni el registro se mueva, con sin_nada_en_curso, anota `quietud` y deja el encargo
\* en necesita_usuario con la causa. El modelo no tiene reloj: la acción está habilitada cuando
\* el encargo está quieto, y la regla PreguntaVisible pide que lo esté.
Quietud ==
  /\ SinNadaEnCurso /\ juicio = "ninguno" /\ ~Consumida /\ ~LanzarPosible
  /\ \A k \in Todos : ~HayQueRecoger(k)
  /\ \A k \in peticion : sol[k] = "pregunta"
  /\ est' = "necesita_usuario"
  /\ UNCHANGED <<proc, juicio, pend, liga, sol, peticion, vista, ajuste, imped, carrera>>
  /\ UNCHANGED <<nuevas, fijas, contadores>>

\* director parar -> dominio._orden "parar" (vale desde cualquier estado no terminal,
\* también desde necesita_usuario)
Parar ==
  /\ est \in {"recibido", "ejecutando", "necesita_usuario"}
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

\* director continuar -> dominio._orden "continuar" -> _retomar. _retomar_trabajo deja el
\* encargo en recibido; con AvisaPregunta, si pregunta_de_inspeccion sigue abierta queda en
\* necesita_usuario con esa pregunta. No toca la cuenta del freno.
Continuar ==
  /\ est = "detenido"
  /\ est' = IF AvisaPregunta /\ PreguntaAbierta THEN "necesita_usuario" ELSE "recibido"
  /\ UNCHANGED <<proc, juicio, pend, liga, sol, peticion, vista, ajuste, imped, carrera>>
  /\ UNCHANGED <<nuevas, fijas, contadores>>

\* motor corregir -> reducción "ajuste": suma el ajuste, _soltar_impedimento_de_inspeccion
\* y _sin_repeticiones (la cuenta del freno empieza de nuevo). Con el encargo en
\* necesita_usuario llama _retomar: recibido, salvo que pregunta_de_inspeccion siga abierta
\* aun con el ajuste (sin AjusteAbre).
Ajustar ==
  /\ est /= "terminado"
  /\ ajuste' = TRUE
  /\ imped' = IF AjusteSuelta THEN FALSE ELSE imped
  /\ cuenta' = 0 /\ sinInfo' = 0
  /\ est' = IF est = "necesita_usuario"
              THEN (IF AvisaPregunta /\ PreguntaEn(pend, liga, sol, vista, TRUE)
                      THEN "necesita_usuario" ELSE "recibido")
              ELSE est
  /\ UNCHANGED <<proc, juicio, pend, liga, sol, peticion, vista, carrera>>
  /\ UNCHANGED <<nuevas, fijas>>
  /\ UNCHANGED ligaVigente

\* director responder -> necesidades.responder -> dominio._respuesta, a la solicitud ligada.
\* Una pregunta pasa a `respuesta` (afirmativa o no); necesidades.continuar_respuestas
\* repone la petición para que atender la ejecute o la deniegue. Con RespuestaVigente basta
\* la fase; sin ella (solicitud_vigente de 72c30a8) una pregunta de un intento anterior no
\* recibe la respuesta y queda una nota de procedimiento. Una solicitud que no es pregunta
\* suma `tardias` (marca nueva). En los dos casos: _soltar_impedimento_de_inspeccion,
\* _sin_repeticiones y _reanudar_inspeccion.
Responder ==
  /\ est /= "terminado" /\ liga /= 0 /\ sol[liga] /= "ausente"
  /\ IF sol[liga] = "pregunta"
       THEN IF RespuestaVigente \/ ligaVigente
              THEN /\ \E si \in BOOLEAN :
                        LET nueva == [sol EXCEPT ![liga] = IF si THEN "respuesta_si" ELSE "respuesta_no"]
                        IN /\ sol' = nueva
                           /\ est' = Reanuda(liga, nueva, vista)
                   /\ peticion' = peticion \cup {liga}
                   /\ imped' = FALSE /\ cuenta' = 0 /\ sinInfo' = 0
                   /\ UNCHANGED vista
              ELSE UNCHANGED <<est, sol, peticion, vista, imped, cuenta, sinInfo>>
       ELSE /\ RespuestaReabre
            /\ vista' = [vista EXCEPT ![liga] = FALSE]
            /\ est' = Reanuda(liga, sol, [vista EXCEPT ![liga] = FALSE])
            /\ imped' = FALSE /\ cuenta' = 0 /\ sinInfo' = 0
            /\ UNCHANGED <<sol, peticion>>
  /\ UNCHANGED <<proc, juicio, pend, liga, ajuste, carrera, ligaVigente>>
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
\* Las preguntas abiertas tienen su propia regla (PreguntaVisible).
SinAtascoMudo ==
  (Abierto /\ ~imped /\ ~PreguntaAbierta) =>
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
  \A k \in Todos : (herr[k] = "termino" /\ est /= "terminado") => ENABLED RecogerDe(k)

\* Regla 5. Una corrida de CI en marcha no muere porque muera el turno del agente: solo
\* la detienen parar o cancelar el encargo.
CorridaSobreviveAlTurno == ~corridaMuerta

\* Regla 6. Una pregunta abierta siempre se ve: el encargo no queda en un recibido mudo. O
\* está en necesita_usuario con la pregunta, o hay un revisor, o el motor tiene un paso
\* posible, o la quietud (dominio.sin_nada_en_curso) lo saca a necesita_usuario.
PreguntaVisible ==
  (Abierto /\ PreguntaAbierta) =>
    (proc \/ Corriendo \/ ENABLED (Lanzar \/ Incorporar \/ Atender \/ Impedir \/ Recoger \/ Quietud))

\* Regla 7. Contestar una pregunta siempre la mueve: el propietario puede responderla y
\* esa respuesta la saca de `pregunta`; un sí se ejecuta y un no se deniega (hay un paso
\* de atender mientras la respuesta espera en el buzón).
RespuestaMueve ==
  /\ PreguntaAbierta => ENABLED (Responder /\ ~PreguntaAbierta')
  /\ \A k \in Todos :
       (sol[k] \in Respondida /\ est /= "terminado") =>
         \* un sí fuera del buzón vuelve a él (algún paso de Next lo repone); con la petición en el buzón y
         \* el encargo en marcha, el sí se ejecuta (resuelta) y el no se deniega (denegada)
         IF k \notin peticion THEN ENABLED (Next /\ k \in peticion')
         ELSE IF Parado THEN ENABLED (Next /\ k \notin peticion')
         ELSE ENABLED (Next /\ sol'[k] = IF sol[k] = "respuesta_si" THEN "resuelta" ELSE "denegada")

\* Variante estricta de la regla 7: el revisor no se lanza con un sí o un no sin ejecutar. En el
\* código sí ocurre: cli.paso llama a barrer (que lanza) antes que a continuar_respuestas.
\* Ver hallazgo-respuesta-sin-ejecutar.cfg.
RevisorEsperaLaRespuesta ==
  [][Lanzar => \A k \in Todos : sol[k] \notin Respondida]_vars

\* Regla 8. Al llegar al tope el encargo queda en una espera visible: la vuelta que alcanza
\* Tope (AlTopeEspera) lo deja en necesita_usuario, y mientras la cuenta esté en el tope no
\* queda una inspección pendiente esperando evidencia (FrenoVisible).
AlTopeEspera ==
  [][(cuenta < Tope /\ cuenta' >= Tope) => est' = "necesita_usuario"]_vars

FrenoVisible == (cuenta >= Tope) => ~pend

\* Regla 9. Toda espera en necesita_usuario tiene salida por un ajuste o una respuesta del
\* propietario.
EsperaConSalida ==
  (est = "necesita_usuario") => ENABLED ((Ajustar \/ Responder) /\ est' = "recibido")

\* Regla 10. El revisor no corre más de Tope veces seguidas sin información nueva (un ajuste,
\* una respuesta o la resolución de la solicitud ligada empiezan la cuenta de nuevo).
\* Parar y continuar no cuentan como información: ver hallazgo-freno-continuar.cfg.
FrenoRevisor == sinInfo <= Tope

\* Variante estricta de la regla 6, sin contar la quietud: una pregunta abierta con el encargo
\* en recibido o ejecutando y nadie corriendo ni esperando una herramienta. Ver
\* hallazgo-pregunta-callada.cfg.
PreguntaNuncaCallada == ~(Abierto /\ PreguntaAbierta /\ ~proc /\ juicio = "ninguno" /\ ~Corriendo)

\* No se exploran más reintentos que identidades hay en el modelo.
DentroDeLaCota == Cardinality({k \in Ids : sol[k] = "detenida"}) < MaxIds
====
