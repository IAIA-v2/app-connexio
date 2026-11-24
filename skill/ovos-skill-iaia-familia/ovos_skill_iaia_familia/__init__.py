"""Skill de Iaia para las «llamadas» de la familia.

El puente (iaia-bridge.py) recibe por MQTT un mensaje de la familia y lo pasa a esta skill.
La skill:
  1. dice el mensaje en voz alta,
  2. se queda escuchando UNA sola respuesta (sin pronunciar «yaya»),
  3. devuelve lo que ha entendido el STT.

Por qué hay una frase «disparadora»
  get_response() solo captura la respuesta si ovos-core considera a la skill «dueña de la conversación»,
  y ovos-core solo marca así a una skill cuando le despacha una INTENCIÓN. Un evento del bus no basta
  (comprobado en los logs de la Pi: la respuesta acababa en el LLM). Por eso el puente, después de
  entregar el texto, envía la frase fija «inicia la trucada de la família» como si fuera una orden de
  voz: ovos-core la despacha a trucada.intent, y ahí dentro get_response() ya funciona.
  La respuesta de la persona no llega al LLM ni a las demás skills.

Mensajes del bus (todos con data {"id": ...}):
  iaia.familia.ask       (puente -> skill)   data: id, text   (se guarda, aún no se dice)
  iaia.familia.accepted  (skill -> puente)   texto recibido
  iaia.familia.cancel    (puente -> skill)   data: id         (olvida el texto pendiente)
  iaia.familia.started   (skill -> puente)   la intención ha llegado y la skill empieza
  iaia.familia.spoken    (skill -> puente)   ha terminado de decir el mensaje
  iaia.familia.listening (skill -> puente)   escuchando la respuesta
  iaia.familia.reply     (skill -> puente)   data: ok, text ("" si no hubo respuesta), error
"""
import collections
import time

from ovos_bus_client.message import Message
from ovos_workshop.decorators import intent_handler
from ovos_workshop.skills import OVOSSkill

PENDING_TTL_S = 60


class IaiaFamiliaSkill(OVOSSkill):
    def initialize(self):
        self._pending = collections.OrderedDict()      # id -> (texto, hora)
        self.add_event("iaia.familia.ask", self.handle_ask)
        self.add_event("iaia.familia.cancel", self.handle_cancel)

    def _emit(self, name, **data):
        self.bus.emit(Message(name, data))

    def _expire(self):
        limit = time.time() - PENDING_TTL_S
        for k in [k for k, (_, t) in self._pending.items() if t < limit]:
            self._pending.pop(k, None)

    def handle_ask(self, message):
        mid = message.data.get("id", "")
        text = str(message.data.get("text", "")).strip()
        if not text:
            return self._emit("iaia.familia.reply", id=mid, ok=False, error="Mensaje vacío")
        self._expire()
        self._pending[mid] = (text, time.time())
        self._emit("iaia.familia.accepted", id=mid)

    def handle_cancel(self, message):
        self._pending.pop(message.data.get("id", ""), None)

    @intent_handler("trucada.intent")
    def handle_trucada(self, message):
        self._expire()
        if not self._pending:
            return                       # alguien dijo la frase sin que haya una llamada pendiente: no hacer nada
        mid, (text, _) = self._pending.popitem(last=False)
        self._emit("iaia.familia.started", id=mid)
        try:
            self.speak(text, wait=True)
            self._emit("iaia.familia.spoken", id=mid)
            self._emit("iaia.familia.listening", id=mid)
            # Diálogo vacío = solo abre el micrófono. num_retries=0: un único intento, sin reinsistir.
            reply = self.get_response(num_retries=0, message=message)
            reply = (reply or "").strip()
            self.log.info("Respuesta a la familia %s: %s", mid, reply[:80] if reply else "(ninguna)")
            self._emit("iaia.familia.reply", id=mid, ok=True, text=reply)
        except Exception as e:
            self.log.exception("Error en la llamada de la familia")
            self._emit("iaia.familia.reply", id=mid, ok=False, error=str(e))
