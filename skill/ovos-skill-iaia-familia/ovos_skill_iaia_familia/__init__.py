"""Skill de Iaia para las «llamadas» de la familia.

El puente (iaia-bridge.py) recibe por MQTT un mensaje de la familia y lo pasa a esta skill
por el bus de OVOS. La skill:
  1. dice el mensaje en voz alta,
  2. se queda escuchando UNA sola respuesta (sin pronunciar «yaya»),
  3. devuelve lo que ha entendido el STT.

Usa get_response() de OVOS, que captura la respuesta por el mecanismo `converse`, así que lo que
diga la persona NO llega al LLM ni a las demás skills.

Mensajes del bus (todos con data {"id": ...}):
  iaia.familia.ask       (puente -> skill)   data: id, text
  iaia.familia.accepted  (skill -> puente)   la skill ha recibido la petición
  iaia.familia.spoken    (skill -> puente)   ha terminado de decir el mensaje
  iaia.familia.listening (skill -> puente)   escuchando la respuesta
  iaia.familia.reply     (skill -> puente)   data: ok, text ("" si no hubo respuesta), error
"""
from ovos_bus_client.message import Message
from ovos_workshop.skills import OVOSSkill


class IaiaFamiliaSkill(OVOSSkill):
    def initialize(self):
        self.add_event("iaia.familia.ask", self.handle_ask)

    def _emit(self, name, **data):
        self.bus.emit(Message(name, data))

    def handle_ask(self, message):
        mid = message.data.get("id", "")
        text = str(message.data.get("text", "")).strip()
        if not text:
            return self._emit("iaia.familia.reply", id=mid, ok=False, error="Mensaje vacío")
        self._emit("iaia.familia.accepted", id=mid)
        try:
            self.speak(text, wait=True)
            self._emit("iaia.familia.spoken", id=mid)
            self._emit("iaia.familia.listening", id=mid)
            try:
                self.activate()                  # para que el mecanismo converse nos ceda la respuesta
            except Exception:
                pass
            # Diálogo vacío = solo abre el micrófono. num_retries=0: un único intento, sin reinsistir.
            reply = self.get_response(num_retries=0, message=message)
            reply = (reply or "").strip()
            self.log.info("Respuesta a la familia %s: %s", mid, reply[:80] if reply else "(ninguna)")
            self._emit("iaia.familia.reply", id=mid, ok=True, text=reply)
        except Exception as e:
            self.log.exception("Error en la llamada de la familia")
            self._emit("iaia.familia.reply", id=mid, ok=False, error=str(e))
