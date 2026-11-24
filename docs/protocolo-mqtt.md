# Protocolo MQTT de Iaia Remoto (v0.1)

Define los tópicos y el formato de los mensajes entre la app (PWA) y el puente de la Raspberry (`bridge/iaia-bridge.py`). Está contrastado con el código de `bridge/iaia-bridge.py`, `app.js` y `skill/ovos-skill-iaia-familia`.

## 1. Convenciones

| Elemento | Valor |
|---|---|
| Prefijo | `iaia` (configurable en el puente con `prefix`) |
| `<id>` | Código del dispositivo, formato `IAIA-XXXX-XXXX` (mayúsculas y números). Lo genera `install.sh`. |
| Codificación | JSON en UTF-8 |
| QoS | 1 en todos los mensajes |
| Retenido | Solo `status`. `cmd` y `ack` no se retienen. |
| Conexión | TLS por defecto, puerto 8883, keepalive de 30 s |
| Tiempos (`ts`) | Segundos Unix (no milisegundos) |

Tópicos:

| Tópico | Sentido | Uso |
|---|---|---|
| `iaia/<id>/status` | Pi → app | Estado y latido |
| `iaia/<id>/cmd` | app → Pi | Órdenes |
| `iaia/<id>/ack` | Pi → app | Confirmaciones y respuestas |

La app se suscribe a `status` y `ack`. El puente se suscribe a `cmd`.

## 2. `iaia/<id>/status` (retenido)

```json
{"online": true, "ts": 1731930000, "ver": "0.1"}
```

| Campo | Tipo | Descripción |
|---|---|---|
| `online` | bool | `true` si el puente está vivo **y** llega al bus de OVOS. Si el puente está vivo pero no llega a OVOS, es `false`. |
| `ts` | entero | Segundos Unix del momento de publicar |
| `ver` | texto | Versión del puente (solo en el latido normal) |

Cuándo se publica:

* Al conectar y después cada 30 s (latido).
* Al apagar el servicio de forma limpia: `{"online": false, "ts": ...}`.
* Si el puente cae sin avisar, el broker publica el Last Will: `{"online": false, "ts": ...}`. El `ts` es el de cuando se registró el Last Will, no el de la caída, y no lleva `ver`.

La app marca el dispositivo como **Inactivo** si `online` es `false` o si pasan más de 90 s desde el último `ts`.

## 3. `iaia/<id>/cmd`

Campos comunes:

| Campo | Obligatorio | Descripción |
|---|---|---|
| `id` | Sí | Identificador del comando, único por envío. Se repite en todos los `ack` de ese comando. |
| `type` | Sí | `say`, `ask` o `ping` |
| `ts` | Recomendado | Segundos Unix del envío. Si está presente y difiere más de 120 s de la hora de la Pi, el comando se rechaza. Si falta, se acepta. |

### 3.1 `say`: Iaia dice un texto

```json
{"id": "a1b2", "type": "say", "text": "Hola, bon dia", "ts": 1731930000}
```

`text` es obligatorio y no puede estar vacío. Se recorta a 300 caracteres (la app ya limita el campo a 300).

### 3.2 `ask`: decir un texto y escuchar una respuesta

```json
{"id": "c3d4", "type": "ask", "text": "Has pres la medicació?", "ts": 1731930000}
```

Mismos campos que `say`. Iaia dice el texto y escucha **una sola respuesta**, sin «yaya». Necesita la skill `ovos-skill-iaia-familia`.

### 3.3 `ping`: comprobar la conexión sin hablar

```json
{"id": "e5f6", "type": "ping", "ts": 1731930000}
```

No lleva `text`. No exige que el dispositivo figure como activo.

## 4. `iaia/<id>/ack`

Todos los `ack` llevan el `id` del comando al que responden. Pueden llevar:

| Campo | Descripción |
|---|---|
| `stage` | Fase del comando (ver tablas) |
| `ok` | `true` o `false`. Ausente en fases intermedias. |
| `error` | Texto del error, solo cuando `ok` es `false` |

### 4.1 Respuestas a `say`

| Secuencia | Mensaje |
|---|---|
| 1 | `{"id":"a1b2","stage":"received"}` |
| 2 | `{"id":"a1b2","stage":"said","ok":true}` |
| Error al hablar | `{"id":"a1b2","stage":"said","ok":false,"error":"..."}` |

### 4.2 Respuestas a `ask`

| Secuencia | Mensaje |
|---|---|
| 1 | `{"id":"c3d4","stage":"received"}` |
| 2 | `{"id":"c3d4","stage":"said","ok":true}` (ha terminado de hablar) |
| 3 | `{"id":"c3d4","stage":"listening","ok":true}` (escuchando) |
| 4 | `{"id":"c3d4","stage":"reply","ok":true,"text":"Sí, ja l'he presa"}` |

`text` es la transcripción del STT. Si la persona no dice nada, `text` llega vacío. Si algo falla, el último mensaje es `{"id":"c3d4","stage":"reply","ok":false,"error":"..."}`.

Errores de `ask` que conviene conocer:

| `error` | Cuándo |
|---|---|
| `Iaia ha dicho el mensaje, pero no puede escuchar: falta instalar la skill iaia-familia.` | La skill no respondió en 4 s. Antes se envía `said` con `ok: true`. |
| `La Iaia no terminó la llamada a tiempo.` | No llegó la respuesta a tiempo |
| `Mensaje caducado` | `ts` fuera de ±120 s. No se envía `received`. |
| `Iaia ha dicho el mensaje, pero no pudo ponerse a escuchar (la orden interna no llegó a la skill).` | La skill aceptó el texto pero no recibió la intención disparadora en 8 s. Antes se envía `said` con `ok: true`. |
| Error de la skill (p. ej. `Mensaje vacío`) | La skill lo devuelve |

### 4.3 Respuesta a `ping`

```json
{"id": "e5f6", "stage": "pong", "ok": true, "bus": true, "ver": "0.1", "ts": 1731930001}
```

`bus: false` indica que el puente está vivo pero no llega a OVOS. Si el `ts` del ping está caducado: `{"id":"e5f6","stage":"pong","ok":false,"error":"Ping caducado"}`.

### 4.4 Errores sin fase (solo `say`)

Un `say` con `type` desconocido, `text` vacío o `ts` caducado recibe un `ack` **sin `stage`**:

```json
{"id": "a1b2", "ok": false, "error": "Comando no válido"}
{"id": "a1b2", "ok": false, "error": "Mensaje caducado"}
```

## 5. Tiempos

| Quién | Tiempo | Qué |
|---|---|---|
| App | 8 s | Esperar el `pong` de un `ping` |
| App | 45 s | Esperar el `ack` final de un `say` |
| App | 150 s | Esperar el `reply` de un `ask` |
| Puente | 4 s | La skill debe aceptar el `ask` |
| Puente | 8 s | La skill debe empezar (`started`) tras la frase disparadora |
| Puente | 45 s | Terminar de hablar |
| Puente | 60 s | Esperar la respuesta de la persona |
| Puente | 120 s | Antigüedad máxima admitida de un comando (`ts`) |
| Ambos | 30 s / 90 s | Latido / umbral de inactividad |

## 6. Protocolo interno puente ↔ skill (bus de OVOS)

Solo para `ask`. No pasa por MQTT.

| Mensaje del bus | Sentido | Datos |
|---|---|---|
| `iaia.familia.ask` | puente → skill | `id`, `text` |
| `iaia.familia.accepted` | skill → puente | `id` (texto guardado, aún no se dice) |
| *(frase disparadora)* | puente → ovos-core | `recognizer_loop:utterance` con «inicia la trucada de la família» |
| `iaia.familia.started` | skill → puente | `id` (la intención llegó a la skill) |
| `iaia.familia.cancel` | puente → skill | `id` (olvidar el texto pendiente) |
| `iaia.familia.spoken` | skill → puente | `id` |
| `iaia.familia.listening` | skill → puente | `id` |
| `iaia.familia.reply` | skill → puente | `id`, `ok`, `text` (vacío si no hubo respuesta), `error` |

El puente traduce cada uno al `ack` correspondiente (`accepted` y `started` no se reenvían a la app).

**Por qué la frase disparadora:** `get_response()` solo captura la respuesta si ovos-core considera a la skill dueña de la conversación, y eso solo ocurre cuando le despacha una intención (un evento del bus no basta). Tras `accepted`, el puente envía la frase como si fuera una orden de voz; la skill la recibe en `trucada.intent`, y desde ahí dice el texto y escucha. La frase (`TRIGGER_UTTERANCE` en el puente) y la de `locale/ca-ES/intents/trucada.intent` deben coincidir.

## 7. Límites conocidos de la v0.1

* Un comando sin `id` o ilegible se ignora, sin ningún `ack`.
* No hay deduplicación: dos comandos con el mismo `id` se ejecutan los dos.
* Las respuestas de `ask` solo llegan a la app si está abierta: `ack` no se retiene.
* El código `<id>` es a la vez identificador y secreto de acceso. Es provisional; el emparejado real está en «Pendiente» del README.
* `ver` no aparece en el Last Will.
