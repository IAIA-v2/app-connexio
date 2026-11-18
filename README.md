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

## Instalar el puente en la Raspberry (instalador interactivo)

Como usuario `ovos` (sin sudo), en la Pi:

```bash
curl -fsSL https://raw.githubusercontent.com/IAIA-v2/app-connexio/main/install.sh | bash
```

Si el repositorio es privado, hace falta un token con permiso de lectura de contenido:

```bash
curl -fsSL -H "Authorization: Bearer <TOKEN>" https://raw.githubusercontent.com/IAIA-v2/app-connexio/main/install.sh | GITHUB_TOKEN=<TOKEN> bash
```

El instalador pregunta los datos del broker (servidor, puerto, TLS, usuario, contraseña y prefijo), genera el código `IAIA-XXXX-XXXX` del equipo y, al terminar, muestra la **wifi (SSID)** a la que está conectada la Pi, su IP, el **código** y su **código QR**.

Qué comprueba y protege:

* Instala `paho-mqtt` solo si falta, y antes hace un `--dry-run`: si toca `ovos-workshop`, `ovos-bus-client`, `padacioso`, `ovos-core`, `ovos-utils` o `ovos-plugin-manager`, se detiene sin instalar. Después compara las versiones y ejecuta `pip check`.
* Hace copia de seguridad (`.bak-FECHA`) de lo que reemplaza.
* La configuración queda en `~/.config/iaia-bridge.json` con permisos 600; la contraseña no se muestra al escribirla.
* Es repetible: si ya existe configuración, ofrece conservarla.
* Para quitarlo: `bash install.sh --uninstall` (y `--purge` para borrar también la configuración).

Después, en el broker: permisos para el usuario del puente sobre `iaia/<CODIGO>/#` y, para la app, lectura de `status` y `ack` y escritura de `cmd`. En la app: **Ajustes → Conexión real** y **Añadir** con el código.

El log del puente está en `~/.local/state/mycroft/iaia-bridge.log`. Añade `~/iaia-bridge.py` y la unidad a `FILES` en `~/iaia-sync.sh` (nunca el JSON de credenciales).

## Servidor MQTT en una instancia (EC2 de AWS u otra)

`broker/setup-mosquitto.sh` monta un broker Mosquitto con TLS (Let's Encrypt) y WebSocket seguro en un Ubuntu 22.04/24.04. Requisitos previos: una instancia con IP fija, un nombre DNS (`mqtt.<TU_DOMINIO>`) que apunte a ella y el Security Group abierto en los puertos 22 (solo tu IP), 80, 8883 y 8884.

```bash
curl -fsSL -O https://raw.githubusercontent.com/IAIA-v2/app-connexio/main/broker/setup-mosquitto.sh
sudo bash setup-mosquitto.sh
```

El script pregunta el dominio, el prefijo y los puertos, instala Mosquitto y certbot, configura los dos listeners y da de alta la primera Iaia. Crea un usuario de puente por Iaia (`pi-<CODIGO>`, solo sus temas) y usuarios de la app que solo pueden leer `status` y `ack` y escribir `cmd` de las Iaias a las que se les da acceso. Al terminar hace una prueba de publicar y suscribirse con TLS.

Otras opciones: `--add-device`, `--add-app-user`, `--grant USUARIO CODIGO`, `--remove-device CODIGO` y `--list`. El fichero de permisos (`/etc/mosquitto/acl`) se regenera desde `/etc/mosquitto/iaia/`; no se edita a mano.

## Pendiente / siguientes pasos

* Pairing real: el código no debe ser el identificador y el secreto a la vez. Producción: código de un solo uso canjeado en la nube por credenciales propias del móvil.
* Servicio GATT Bluetooth en la Pi para aprovisionar (código y, si se quiere, wifi).
* Notificaciones push cuando una Iaia pase a inactiva (necesita servidor; en iPhone, solo con la PWA instalada).
* Escaneo de QR con cámara y varias personas con acceso al mismo dispositivo.
