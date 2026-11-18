# Iaia Remoto (PWA) — prototipo v0.1

App web instalable (iPhone y Android) para ver si una Iaia está **activa/inactiva**, añadirla con su **número de serie** (o Bluetooth donde exista) y enviarle un texto que dirá en voz alta.

## Estructura

```
index.html · app.css · app.js      La app (sin dependencias ni build)
manifest.webmanifest · sw.js       Instalación como PWA y arranque offline
icons/                             Iconos (192, 512, maskable, apple-touch)
bridge/iaia-bridge.py              Puente que corre en la Raspberry (MQTT <-> bus OVOS)
bridge/iaia-bridge.service         Unidad systemd --user
```

## Arquitectura (acceso desde fuera de casa)

```
 Móvil (PWA, 4G/otra wifi)                      Casa (wifi interna)
 ┌────────────┐  wss:// (TLS)  ┌──────────────┐  mqtts:// (saliente)  ┌──────────────────────┐
 │  app.js    │ ─────────────► │ Broker MQTT  │ ◄──────────────────── │ iaia-bridge.py       │
 │            │ ◄───────────── │ (en la nube) │ ────────────────────► │  └─► bus OVOS: speak │
 └────────────┘  status / ack  └──────────────┘  cmd (say)            └──────────────────────┘
```

* La Raspberry **solo hace conexiones salientes**: no hay que abrir puertos del router ni exponer el bus de OVOS.
* **Activo/inactivo**: el puente publica un latido cada 30 s en `iaia/<id>/status` (retenido). Si cae, el broker publica `online:false` (Last Will). La app marca «Inactivo» también si pasan más de 90 s sin señal.
* **Enviar texto**: la app publica en `iaia/<id>/cmd`; el puente emite `speak` en OVOS y responde en `iaia/<id>/ack` (`received` → `said`).

### Temas MQTT

| Tema | Sentido | Contenido |
|---|---|---|
| `iaia/<id>/status` | Pi → app (retenido) | `{"online":true,"ts":1731930000,"ver":"0.1"}` |
| `iaia/<id>/cmd` | app → Pi | `{"id":"a1b2","type":"say","text":"Hello World","ts":1731930000}` |
| `iaia/<id>/ack` | Pi → app | `{"id":"a1b2","stage":"received"}` y `{"id":"a1b2","stage":"said","ok":true}` |

## Bluetooth: qué se puede y qué no

| Plataforma | Web Bluetooth en una PWA | Vía de emparejado |
|---|---|---|
| Android (Chrome) | Sí | Bluetooth o código |
| iPhone/iPad (cualquier navegador) | **No** (WebKit no lo implementa) | **Código / QR** |

Por eso el prototipo ofrece el **código de serie como camino universal** y Bluetooth como extra en Android. La Raspberry Pi 5 sí incluye Bluetooth LE, pero falta (fase 2) un servicio GATT en la Pi (BlueZ) que anuncie `IAIA-XXXX` y entregue el código/credenciales; los UUID de `app.js` son provisionales. Si se quiere Bluetooth también en iPhone, habría que hacer una app nativa (p. ej. Capacitor), no una PWA.

## Probarlo ya (modo demo)

1. Abre la app → **Añadir** → «Con el código» → «Usar código de prueba» → Continuar.
2. Envía «Hello World»; en demo lo lee la voz del móvil.
3. **Ajustes → Simulador**: apaga la Iaia simulada y verás el estado gris «Inactivo».

## Publicar en GitHub Pages

```bash
git init && git add . && git commit -m "Iaia Remoto v0.1"
git branch -M main && git remote add origin https://github.com/<USUARIO>/<REPO>.git
git push -u origin main
# En GitHub: Settings → Pages → Deploy from a branch → main / (root)
```

Quedará en `https://<USUARIO>.github.io/<REPO>/` (HTTPS, requisito de PWA y de MQTT seguro). Para instalarla: iPhone → Compartir → «Añadir a pantalla de inicio»; Android → «Instalar app».

> No subas nunca credenciales: se escriben en Ajustes y quedan solo en el móvil.

## Conexión real (cuando haya broker)

1. **Broker MQTT con WebSocket seguro**: HiveMQ Cloud o EMQX Cloud (plan gratuito) o Mosquitto propio detrás de TLS. Crea un usuario para el puente y otro para la app, con permisos solo sobre `iaia/<id>/#` (la app: leer `status`/`ack`, escribir `cmd`).
2. **En la Pi** (`rasp5ovos`), siguiendo las reglas del proyecto:
   ```bash
   ~/.venvs/ovos/bin/python -c "import paho.mqtt; print(paho.mqtt.__version__)"   # ¿ya está?
   uv pip install --dry-run paho-mqtt     # revisa que NO baje ovos-workshop, ovos-bus-client, padacioso, ovos-core, ovos-utils ni ovos-plugin-manager
   ```
   Si el dry-run es limpio, instala, y después `pip check | grep -iE "workshop|bus-client|padacioso"`.
3. Copia `bridge/iaia-bridge.py` a `~/`, crea `~/.config/iaia-bridge.json` (plantilla en el docstring, `chmod 600`) y activa:
   ```bash
   cp bridge/iaia-bridge.service ~/.config/systemd/user/
   systemctl --user daemon-reload && systemctl --user enable --now iaia-bridge
   systemctl --user status iaia-bridge -n 30 --no-pager
   ```
4. Añade `~/iaia-bridge.py` y la unidad a `FILES` en `~/iaia-sync.sh` (el JSON de credenciales **no**).
5. En la app: **Ajustes → Conexión real**, URL `wss://…`, usuario, contraseña.

## Pendiente / siguientes pasos

* Pairing real: el código no debe ser el identificador y el secreto a la vez. Producción: código de un solo uso canjeado en la nube por credenciales propias del móvil.
* Servicio GATT Bluetooth en la Pi para aprovisionar (código y, si se quiere, wifi).
* Notificaciones push cuando una Iaia pase a inactiva (necesita servidor; en iPhone, solo con la PWA instalada).
* Escaneo de QR con cámara y varias personas con acceso al mismo dispositivo.
