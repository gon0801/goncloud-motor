export const title = "El revisor pide evidencia y tú paras, corriges o respondes";

const abierto = (v) => v.est === "recibido" || v.est === "ejecutando";
const terminal = (s) => s === "resuelta" || s === "detenida";
const pet = (v) => (Array.isArray(v.peticion) ? v.peticion : Object.values(v.peticion || {}));

export const state = (v) => {
  if (v.est === "terminado") return "Terminado";
  if (v.est === "deteniendo") return "Parando";
  if (v.est === "detenido") return "Detenido, esperando que continúes";
  if (v.imped) return "Impedimento: espera tu ajuste o tu respuesta";
  if (v.proc) return "Revisor trabajando";
  if (v.juicio !== "ninguno") return "El revisor terminó; falta leer su juicio";
  if (abierto(v) && v.pend && v.liga !== 0) {
    const s = v.sol[v.liga];
    if (s === "ausente" && !pet(v).includes(v.liga)) return "Atado a un pedido que no existe";
    if (!terminal(s)) return "Esperando la evidencia";
    if (v.vista[v.liga]) return v.ajuste ? "Pidió otra vez lo que ya recibió, con un ajuste tuyo sin ver" : "Pidió otra vez lo que ya recibió";
  }
  return "Listo para lanzar al revisor";
};

export const group = (v) => {
  if (v.est === "terminado") return "Cerrado";
  if (v.est === "deteniendo" || v.est === "detenido" || v.imped) return "Te toca a ti";
  if (v.proc) return "Gasta cuota";
  return "Motor, sin gastar";
};

export const colors = {
  "Gasta cuota": "orange",
  "Motor, sin gastar": "blue",
  "Te toca a ti": "purple",
  Cerrado: "teal",
};

export const step = (action, from, to) => {
  switch (action) {
    case "Termina":
      return to.juicio === "pide" ? "el revisor pide evidencia" : "el revisor aprueba";
    case "Incorporar":
      return "el motor lee el juicio";
    case "Lanzar":
      return "el motor lanza al revisor";
    case "Impedir":
      return "el motor registra un impedimento";
    case "Atender": {
      for (const k of Object.keys(to.herr)) {
        if (from.herr[k] !== to.herr[k] && to.herr[k] === "corre") return "el motor lanza la corrida de CI";
      }
      for (const k of Object.keys(to.sol)) {
        if (from.sol[k] !== to.sol[k]) {
          if (to.sol[k] === "pendiente") return "el motor registra el pedido";
          if (to.sol[k] === "resuelta") return "llega la evidencia";
          if (to.sol[k] === "detenida") return "el motor marca el pedido como detenido";
        }
      }
      return "el motor saca el pedido del buzón";
    }
    case "HerramientaTermina":
      return "la corrida de CI termina";
    case "Recoger":
      return "el motor recoge el resultado de la corrida";
    case "CambiaIntento":
      return "el turno del agente termina y abre otro intento";
    case "MatarTurno":
      return "el motor mata el turno del agente";
    case "CerrarHerramientas":
      return "el motor cierra la corrida de CI";
    case "Parar":
      return "lo paras";
    case "Matar":
      return "el motor cierra al revisor";
    case "Detener":
      return "no queda ningún proceso";
    case "Continuar":
      return "continúas";
    case "Ajustar":
      return "agregas un ajuste";
    case "Responder":
      return "respondes";
    case "Terminado":
      return null;
    default:
      return action;
  }
};
