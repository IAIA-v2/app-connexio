#!/usr/bin/env python3
"""iaia-bridge: puente MQTT <-> bus de mensajes de OVOS (Raspberry Pi de Iaia).

Qué hace
  * Publica el estado del dispositivo (retenido) en  <prefix>/<device_id>/status
      {"online": true, "ts": 1731930000, "ver": "0.1"}   cada 30 s (latido).
    Si el puente muere o se pierde la red, el broker publica {"online": false}
    (Last Will), así la app ve «Inactivo» en verde->gris.
  * Escucha  <prefix>/<device_id>/cmd  con {"id","type":"say","text"} y hace que
    Iaia lo diga en voz alta emitiendo un mensaje `speak` en el bus de OVOS.
  * Llamada: {"type":"ask","text":...} -> Iaia dice el texto, escucha UNA respuesta y la devuelve
    (ack stages: received, said, listening, reply{text}). Necesita la skill ovos-skill-iaia-familia.
  * Ping: {"type":"ping"} -> responde {"stage":"pong","ok":true,"bus":...,"ver":...} sin hablar.
  * Responde en  <prefix>/<device_id>/ack: primero {"stage":"received"} y, cuando
    termina de hablar, {"stage":"said","ok":true}.

Seguridad
  * NO expone el bus de OVOS a Internet: el puente solo hace conexión SALIENTE al broker.
  * Las credenciales viven en ~/.config/iaia-bridge.json (chmod 600), nunca en el repo.
    Plantilla:
      {"host": "<HOST_DEL_BROKER>", "port": 8883, "tls": true,
       "username": "<USUARIO>", "password": "<PASSWORD>",
       "device_id": "IAIA-XXXX-XXXX", "prefix": "iaia"}
"""
import json
import os
import queue
import re
import signal
import sys
import threading
import time

import paho.mqtt.client as mqtt
from ovos_bus_client import Message, MessageBusClient

VERSION = "0.1"
CONF_PATH = os.path.expanduser(os.environ.get("IAIA_BRIDGE_CONF", "~/.config/iaia-bridge.json"))
HEARTBEAT_S = 30
MAX_CHARS = 300
SPEAK_TIMEOUT_S = 45
ACCEPT_TIMEOUT_S = 4      # la skill iaia-familia debe responder en este tiempo
START_TIMEOUT_S = 8       # tras la frase disparadora, la skill debe empezar en este tiempo
TRIGGER_UTTERANCE = "inicia la trucada de la família"   # debe coincidir con locale/ca-ES/intents/trucada.intent de la skill
REPLY_TIMEOUT_S = 60      # tiempo máximo esperando la respuesta de la persona

with open(CONF_PATH) as f:
    CONF = json.load(f)
DEVICE_ID = CONF["device_id"]
PREFIX = CONF.get("prefix", "iaia")
T_STATUS = f"{PREFIX}/{DEVICE_ID}/status"
T_CMD = f"{PREFIX}/{DEVICE_ID}/cmd"
T_ACK = f"{PREFIX}/{DEVICE_ID}/ack"


def log(*a):
    print(time.strftime("%H:%M:%S"), *a, flush=True)


# ---- Texto -> frase apta para Piper (mismas reglas aprendidas en el proyecto) ----
def prepare_for_tts(text: str) -> str:
    text = re.sub(r"\s+", " ", text).strip()[:MAX_CHARS]
    text = re.sub(r"(\d+)\.(\d+)", r"\1 coma \2", text)   # 20.8 -> "20 coma 8" (el punto se lee como millar)
    if text and text[-1] not in ".!?":
        text += "."                                       # sin punto final Piper puede acabar con un "rrrr"
    return text


# ---- Bus de OVOS ----
bus = MessageBusClient()
bus.run_in_thread()
audio_done = threading.Event()
bus.on("recognizer_loop:audio_output_end", lambda m: audio_done.set())
speak_lock = threading.Lock()   # una frase cada vez


def bus_ok() -> bool:
    ev = getattr(bus, "connected_event", None)
    return bool(ev.is_set()) if ev is not None else True


def _speak(text: str) -> None:
    audio_done.clear()
    bus.emit(Message("speak", {"utterance": prepare_for_tts(text), "lang": "ca-es", "expect_response": False}))
    if not audio_done.wait(SPEAK_TIMEOUT_S):
        raise TimeoutError("Iaia no confirmó el final de la locución")


def say(text: str) -> None:
    with speak_lock:
        _speak(text)


# ---- llamada: decir, escuchar y devolver la respuesta (necesita la skill ovos-skill-iaia-familia) ----
asks = {}                      # id -> Queue de eventos de la skill


def _on_familia(name):
    def handler(m):
        q = asks.get(m.data.get("id"))
        if q is not None:
            q.put((name, m.data))
    return handler


for _n in ("accepted", "started", "spoken", "listening", "reply"):
    bus.on(f"iaia.familia.{_n}", _on_familia(_n))


def _wait_event(q, want, timeout):
    end = time.time() + timeout
    while True:
        left = end - time.time()
        if left <= 0:
            raise queue.Empty
        name, data = q.get(timeout=left)
        if name == want or (name == "reply" and not data.get("ok", True)):
            return name, data


def ask(mid: str, text: str) -> None:
    """Dice el mensaje, escucha una respuesta y la publica. Mantiene el bloqueo de voz durante toda la llamada."""
    with speak_lock:
        q = queue.Queue(); asks[mid] = q
        try:
            bus.emit(Message("iaia.familia.ask", {"id": mid, "text": prepare_for_tts(text)}))
            try:
                name, d = _wait_event(q, "accepted", ACCEPT_TIMEOUT_S)
                if name == "reply":
                    return ack(mid, stage="reply", ok=False, error=d.get("error", "Error en la skill"))
            except queue.Empty:
                log("La skill iaia-familia no respondió: digo el mensaje sin escuchar")
                _speak(text)
                ack(mid, stage="said", ok=True)
                return ack(mid, stage="reply", ok=False, error="Iaia ha dicho el mensaje, pero no puede escuchar: falta instalar la skill iaia-familia.")
            # La skill solo puede capturar la respuesta si ovos-core le despacha una intención:
            # enviamos la frase disparadora como si fuera una orden de voz.
            bus.emit(Message("recognizer_loop:utterance", {"utterances": [TRIGGER_UTTERANCE], "lang": "ca-ES"},
                             {"source": "audio", "destination": "skills", "client_name": "iaia-bridge"}))
            try:
                _wait_event(q, "started", START_TIMEOUT_S)
            except queue.Empty:
                bus.emit(Message("iaia.familia.cancel", {"id": mid}))
                log("La skill no recibió la intención disparadora: digo el mensaje sin escuchar")
                _speak(text)
                ack(mid, stage="said", ok=True)
                return ack(mid, stage="reply", ok=False, error="Iaia ha dicho el mensaje, pero no pudo ponerse a escuchar (la orden interna no llegó a la skill).")
            name, d = _wait_event(q, "spoken", SPEAK_TIMEOUT_S)
            if name == "reply":
                return ack(mid, stage="reply", ok=False, error=d.get("error", "Error en la skill"))
            ack(mid, stage="said", ok=True)
            name, d = _wait_event(q, "listening", 5)
            if name == "reply":
                return ack(mid, stage="reply", ok=False, error=d.get("error", "Error en la skill"))
            ack(mid, stage="listening", ok=True)
            name, d = _wait_event(q, "reply", REPLY_TIMEOUT_S)
            if d.get("ok", True):
                ack(mid, stage="reply", ok=True, text=str(d.get("text", "")))
            else:
                ack(mid, stage="reply", ok=False, error=d.get("error", "Error en la skill"))
        except queue.Empty:
            ack(mid, stage="reply", ok=False, error="La Iaia no terminó la llamada a tiempo.")
        finally:
            asks.pop(mid, None)


# ---- MQTT ----
def make_client() -> mqtt.Client:
    cid = f"iaia-bridge-{DEVICE_ID}"
    try:
        return mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=cid)   # paho-mqtt >= 2
    except AttributeError:
        return mqtt.Client(client_id=cid)                                      # paho-mqtt 1.x


client = make_client()
if CONF.get("username"):
    client.username_pw_set(CONF["username"], CONF.get("password"))
if CONF.get("tls", True):
    client.tls_set()   # valida el certificado del broker con las CA del sistema
client.will_set(T_STATUS, json.dumps({"online": False, "ts": int(time.time())}), qos=1, retain=True)


def publish_status():
    client.publish(T_STATUS, json.dumps({"online": bus_ok(), "ts": int(time.time()), "ver": VERSION}), qos=1, retain=True)


def ack(mid, **kw):
    client.publish(T_ACK, json.dumps({"id": mid, **kw}), qos=1)


def on_connect(c, userdata, flags, rc, *extra):
    failed = getattr(rc, "is_failure", None)
    if (failed if failed is not None else rc != 0):
        log("Conexión rechazada:", rc)
        return
    log("Conectado al broker")
    c.subscribe(T_CMD, qos=1)
    publish_status()


def handle_cmd(payload: bytes):
    try:
        cmd = json.loads(payload)
        mid = cmd["id"]
    except Exception:
        return log("Comando ilegible, ignorado")
    if cmd.get("type") == "ping":                                  # ping/pong: responde al instante y no habla
        if abs(time.time() - cmd.get("ts", time.time())) > 120:
            return ack(mid, stage="pong", ok=False, error="Ping caducado")
        log("Ping recibido")
        return ack(mid, stage="pong", ok=True, bus=bus_ok(), ver=VERSION, ts=int(time.time()))
    if cmd.get("type") == "ask" and str(cmd.get("text", "")).strip():
        if abs(time.time() - cmd.get("ts", time.time())) > 120:
            return ack(mid, stage="reply", ok=False, error="Mensaje caducado")
        ack(mid, stage="received")
        try:
            ask(mid, str(cmd["text"]))
            log("Llamada terminada:", str(cmd["text"])[:60])
        except Exception as e:
            log("Error en la llamada:", e)
            ack(mid, stage="reply", ok=False, error=str(e))
        return
    if cmd.get("type") != "say" or not str(cmd.get("text", "")).strip():
        return ack(mid, ok=False, error="Comando no válido")
    if abs(time.time() - cmd.get("ts", time.time())) > 120:      # evita repeticiones de mensajes viejos
        return ack(mid, ok=False, error="Mensaje caducado")
    ack(mid, stage="received")
    try:
        say(str(cmd["text"]))
        ack(mid, stage="said", ok=True)
        log("Dicho:", str(cmd["text"])[:60])
    except Exception as e:
        log("Error al hablar:", e)
        ack(mid, stage="said", ok=False, error=str(e))


def on_message(c, userdata, msg):
    threading.Thread(target=handle_cmd, args=(msg.payload,), daemon=True).start()


client.on_connect = on_connect
client.on_message = on_message


def stop(*_):
    try:
        client.publish(T_STATUS, json.dumps({"online": False, "ts": int(time.time())}), qos=1, retain=True).wait_for_publish(3)
    finally:
        client.disconnect()
        sys.exit(0)


signal.signal(signal.SIGTERM, stop)
signal.signal(signal.SIGINT, stop)

client.connect_async(CONF["host"], int(CONF.get("port", 8883)), keepalive=30)
client.loop_start()
while True:
    time.sleep(HEARTBEAT_S)
    publish_status()
