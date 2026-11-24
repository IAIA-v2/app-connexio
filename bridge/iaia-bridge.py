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
T_HISTORY = f"{PREFIX}/{DEVICE_ID}/history"        # Pi -> app, retenido (historial común)
T_MESSAGES = f"{PREFIX}/{DEVICE_ID}/messages"     # Pi -> app: mensajes voluntarios (issue #7)
APP_ACK_WAIT_S = 5        # tiempo esperando que la app confirme la recepción de un mensaje voluntario
DUP_WINDOW_S = 30         # un mismo texto no se reenvía dentro de esta ventana
MSG_MAX_CHARS = 500
# Historial compartido (issue #7). La Pi es la única que escribe; se guarda en un fichero local (600) y,
# solo si "history_publish" es true en la config, se publica retenido en iaia/<id>/history.
HISTORY_MAX = 30                          # últimos 30 mensajes
HISTORY_TTL_S = 7 * 24 * 3600             # caducidad: 7 días


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
    transport = CONF.get("transport", "tcp")           # "tcp" (8883/1883) o "websockets" (p. ej. 443 tras un proxy)
    try:
        return mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=cid, transport=transport)   # paho-mqtt >= 2
    except AttributeError:
        return mqtt.Client(client_id=cid, transport=transport)                                      # paho-mqtt 1.x


client = make_client()
if CONF.get("username"):
    client.username_pw_set(CONF["username"], CONF.get("password"))
if CONF.get("tls", True):
    client.tls_set()   # valida el certificado del broker con las CA del sistema
if CONF.get("transport") == "websockets":
    client.ws_set_options(path=CONF.get("ws_path", "/mqtt"))
client.reconnect_delay_set(min_delay=1, max_delay=int(CONF.get("reconnect_max_s", 120)))
client.will_set(T_STATUS, json.dumps({"online": False, "ts": int(time.time())}), qos=1, retain=True)


def publish_status():
    client.publish(T_STATUS, json.dumps({"online": bus_ok(), "ts": int(time.time()), "ver": VERSION}), qos=1, retain=True)


def ack(mid, **kw):
    client.publish(T_ACK, json.dumps({"id": mid, **kw}), qos=1)
    hist_on_ack(mid, kw)


# ---- Historial compartido (issue #7) ----
HISTORY_PATH = os.path.expanduser(CONF.get("history_path", "~/.local/state/iaia-bridge/history.json"))
HISTORY_PUBLISH = bool(CONF.get("history_publish", False))   # False = solo se guarda en la Pi, NO se publica en el broker
hist_lock = threading.Lock()
hist_items = []           # más nuevo primero: {id, kind: call|say|voluntary, text, t, from, state, reply, source, err}


def _hist_prune_locked():
    limit = time.time() - HISTORY_TTL_S
    n0 = len(hist_items)
    hist_items[:] = [i for i in hist_items if i.get("t", 0) >= limit][:HISTORY_MAX]
    return len(hist_items) != n0


def hist_load():
    try:
        with open(HISTORY_PATH) as f:
            data = json.load(f)
        with hist_lock:
            hist_items[:] = [i for i in data.get("items", []) if isinstance(i, dict) and i.get("id")]
            hist_items.sort(key=lambda i: i.get("t", 0), reverse=True)
            _hist_prune_locked()
        log("Historial cargado:", len(hist_items), "mensajes")
    except FileNotFoundError:
        pass
    except Exception as e:
        log("Historial ilegible, empiezo vacío:", e)


def _hist_save_locked():
    os.makedirs(os.path.dirname(HISTORY_PATH), exist_ok=True)
    tmp = HISTORY_PATH + ".tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump({"v": 1, "items": hist_items}, f, ensure_ascii=False)
    os.replace(tmp, HISTORY_PATH)


def hist_publish():
    """Publica el historial retenido (QoS 1). Solo si history_publish está activado en la config."""
    if not HISTORY_PUBLISH or not client.is_connected():
        return
    with hist_lock:
        _hist_prune_locked()
        payload = json.dumps({"v": 1, "ts": int(time.time()), "items": hist_items}, ensure_ascii=False)
    client.publish(T_HISTORY, payload, qos=1, retain=True)


def hist_add(item):
    item.setdefault("t", int(time.time()))
    with hist_lock:
        hist_items[:] = [i for i in hist_items if i.get("id") != item["id"]]
        hist_items.insert(0, item)
        _hist_prune_locked()
        _hist_save_locked()
    hist_publish()


def hist_update(mid, **kw):
    with hist_lock:
        it = next((i for i in hist_items if i.get("id") == mid), None)
        if not it:
            return False
        it.update({k: v for k, v in kw.items() if v is not None})
        _hist_save_locked()
    hist_publish()
    return True


def hist_on_ack(mid, kw):
    """Convierte los ack finales de las órdenes de la app en estado del historial."""
    st = kw.get("stage")
    with hist_lock:
        it = next((i for i in hist_items if i.get("id") == mid), None)
        kind = it.get("kind") if it else None
    if kind == "call" and st == "reply":
        if kw.get("ok") and str(kw.get("text", "")).strip():
            hist_update(mid, state="replied", reply=str(kw["text"])[:MSG_MAX_CHARS])
        elif kw.get("ok"):
            hist_update(mid, state="noreply")
        else:
            hist_update(mid, state="failed", err=str(kw.get("error", ""))[:200])
    elif kind == "say" and st == "said":
        hist_update(mid, state="said" if kw.get("ok") else "failed", err=None if kw.get("ok") else str(kw.get("error", ""))[:200])


def _from(cmd):
    return " ".join(str(cmd.get("from", "")).split())[:40]


hist_load()


# ---- Mensajes voluntarios de la persona mayor hacia la app (issue #7) ----
# Estados reales del envío (nunca se da por recibido solo porque el broker lo aceptó):
#   published = el broker confirmó la publicación (PUBACK)    delivered = además la app confirmó la recepción
app_waiters = {}          # id del mensaje -> Event, lo activa el ack de la app (cmd {"type":"msg_ack","id":...})
recent_sent = {}          # texto normalizado -> hora del último envío (anti-duplicados)
send_lock = threading.Lock()


def send_to_app(texto, source="iaia_voluntary", mid=None, tipo="message"):
    """Publica el mensaje en iaia/<id>/messages. Devuelve (publicado_en_broker, id, error)."""
    mid = mid or f"m{int(time.time() * 1000):x}"
    if not client.is_connected():
        log("ERROR send_to_app: sin conexión MQTT")
        return False, mid, "Sin conexión con el broker"
    payload = {"id": mid, "type": tipo, "source": source, "text": str(texto), "ts": int(time.time())}
    try:
        res = client.publish(T_MESSAGES, json.dumps(payload, ensure_ascii=False), qos=1, retain=False)
        if res.rc != mqtt.MQTT_ERR_SUCCESS:
            log("ERROR send_to_app: rc", res.rc)
            return False, mid, f"Error MQTT {res.rc}"
        res.wait_for_publish(timeout=5)
        if not res.is_published():
            log("ERROR send_to_app: el broker no confirmó la publicación")
            return False, mid, "El broker no confirmó la publicación"
        log("send_to_app: publicado y confirmado por el broker", mid, source)
        return True, mid, ""
    except Exception as e:
        log("ERROR send_to_app:", e)
        return False, mid, str(e)


def _do_app_send(data):
    sid = str(data.get("id", ""))
    text = str(data.get("text", "")).strip()[:MSG_MAX_CHARS]
    source = str(data.get("source", "iaia_voluntary"))

    def done(**kw):
        bus.emit(Message("iaia.app.sent", {"id": sid, **kw}))

    if not text or not any(ch.isalnum() for ch in text):
        return done(ok=False, stage="rejected", error="Mensaje vacío")
    key = " ".join(text.lower().split())
    with send_lock:
        now = time.time()
        for k in [k for k, t in recent_sent.items() if now - t > DUP_WINDOW_S]:
            recent_sent.pop(k, None)
        if key in recent_sent:
            log("Mensaje duplicado ignorado")
            return done(ok=False, stage="rejected", error="duplicat")
        recent_sent[key] = now
        ev = threading.Event(); app_waiters[sid] = ev
    try:
        published, mid, err = send_to_app(text, source=source, mid=sid or None)
        if not published:
            with send_lock:
                recent_sent.pop(key, None)      # el intento falló: permite reintentar
            return done(ok=False, stage="failed", error=err)
        if ev.wait(APP_ACK_WAIT_S):
            log("La app confirmó la recepción", mid)
            hist_add({"id": mid, "kind": "voluntary", "source": source, "text": text, "from": "", "state": "delivered"})
            return done(ok=True, stage="delivered")
        log("Publicado, pero la app no ha confirmado la recepción en", APP_ACK_WAIT_S, "s")
        hist_add({"id": mid, "kind": "voluntary", "source": source, "text": text, "from": "", "state": "published"})
        return done(ok=True, stage="published")
    finally:
        app_waiters.pop(sid, None)


def on_app_send(message):
    threading.Thread(target=_do_app_send, args=(dict(message.data),), daemon=True).start()


bus.on("iaia.app.send", on_app_send)


def on_disconnect(c, userdata, *args):
    log("Desconectado del broker", args[-2] if len(args) >= 2 else "")   # así un fallo de red ya no es silencioso


def on_connect_fail(c, userdata, *args):
    log("No se pudo conectar con el broker", CONF.get("host"), CONF.get("port"), "transporte:", CONF.get("transport", "tcp"))


def on_connect(c, userdata, flags, rc, *extra):
    failed = getattr(rc, "is_failure", None)
    if (failed if failed is not None else rc != 0):
        log("Conexión rechazada:", rc)
        return
    log("Conectado al broker")
    c.subscribe(T_CMD, qos=1)
    publish_status()
    hist_publish()                                     # restituye el historial retenido si el broker lo hubiera perdido


def handle_cmd(payload: bytes):
    try:
        cmd = json.loads(payload)
        mid = cmd["id"]
    except Exception:
        return log("Comando ilegible, ignorado")
    if cmd.get("type") == "msg_ack":                               # la app confirma que recibió un mensaje voluntario
        ev = app_waiters.get(str(mid))
        if ev:
            ev.set()
        return
    if cmd.get("type") == "ping":                                  # ping/pong: responde al instante y no habla
        if abs(time.time() - cmd.get("ts", time.time())) > 120:
            return ack(mid, stage="pong", ok=False, error="Ping caducado")
        log("Ping recibido")
        return ack(mid, stage="pong", ok=True, bus=bus_ok(), ver=VERSION, ts=int(time.time()))
    if cmd.get("type") == "ask" and str(cmd.get("text", "")).strip():
        if abs(time.time() - cmd.get("ts", time.time())) > 120:
            return ack(mid, stage="reply", ok=False, error="Mensaje caducado")
        hist_add({"id": mid, "kind": "call", "text": str(cmd["text"])[:MAX_CHARS], "from": _from(cmd), "state": "received"})
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
    hist_add({"id": mid, "kind": "say", "text": str(cmd["text"])[:MAX_CHARS], "from": _from(cmd), "state": "received"})
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
client.on_disconnect = on_disconnect
client.on_connect_fail = on_connect_fail
client.on_message = on_message


def stop(*_):
    try:
        client.publish(T_STATUS, json.dumps({"online": False, "ts": int(time.time())}), qos=1, retain=True).wait_for_publish(3)
    finally:
        client.disconnect()
        sys.exit(0)


signal.signal(signal.SIGTERM, stop)
signal.signal(signal.SIGINT, stop)

client.connect_async(CONF["host"], int(CONF.get("port", 8883)), keepalive=int(CONF.get("keepalive", 30)))
client.loop_start()
while True:
    time.sleep(HEARTBEAT_S)
    publish_status()
    with hist_lock:
        changed = _hist_prune_locked()
        if changed:
            _hist_save_locked()
    if changed:
        hist_publish()                                 # algún mensaje ha caducado (7 días)
