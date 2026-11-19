#!/usr/bin/env bash
# Instalador interactivo de iaia-bridge (MQTT <-> OVOS) para la Raspberry Pi de Iaia.
#
# Uso (como usuario "ovos", NO con sudo):
#   curl -fsSL https://raw.githubusercontent.com/IAIA-v2/app-connexio/main/install.sh | bash
#   (repositorio privado: añade  -H "Authorization: Bearer <TOKEN>"  al curl y  GITHUB_TOKEN=<TOKEN>  antes de bash)
#   ./install.sh --uninstall      quita el servicio y el puente (conserva la configuración)
#   ./install.sh --uninstall --purge   quita también la configuración y el código del dispositivo
#
# Qué hace: comprueba el entorno, instala paho-mqtt solo si es seguro (dry-run), descarga el puente,
# pregunta los datos del broker, crea ~/.config/iaia-bridge.json (permisos 600), activa el servicio
# systemd --user y, al final, muestra la wifi a la que está conectada la Pi y el código QR de emparejado.
set -euo pipefail

REPO_RAW="${IAIA_REPO_RAW:-https://raw.githubusercontent.com/IAIA-v2/app-connexio/main}"
VENV="${IAIA_VENV:-$HOME/.venvs/ovos}"
PY="$VENV/bin/python"
BRIDGE_DST="$HOME/iaia-bridge.py"
UNIT_DIR="$HOME/.config/systemd/user"
UNIT_DST="$UNIT_DIR/iaia-bridge.service"
CONF="$HOME/.config/iaia-bridge.json"
LOG="$HOME/.local/state/mycroft/iaia-bridge.log"
STAMP="$(date +%Y%m%d-%H%M%S)"
PROTECTED='ovos-workshop|ovos-bus-client|padacioso|ovos-core|ovos-utils|ovos-plugin-manager'

say()  { printf '\n\033[1m== %s\033[0m\n' "$*"; }
ok()   { printf '  \033[32m✓\033[0m %s\n' "$*"; }
warn() { printf '  \033[33m!\033[0m %s\n' "$*"; }
die()  { printf '\n\033[31mERROR:\033[0m %s\n' "$*" >&2; exit 1; }

# Las respuestas se leen del terminal aunque el script llegue por tubería (curl | bash).
TTY_SRC="${IAIA_TTY:-/dev/tty}"
if ! ( : <"$TTY_SRC" ) 2>/dev/null; then die "No hay terminal interactivo. Descarga el script y ejecútalo con: bash install.sh"; fi
exec 3<"$TTY_SRC"
ask() {  # ask "texto" "valor_por_defecto"  -> escribe la respuesta en REPLY
  local def="${2:-}" ans=""
  if [ -n "$def" ]; then printf '  %s [%s]: ' "$1" "$def" >&2; else printf '  %s: ' "$1" >&2; fi
  IFS= read -r ans <&3 || true
  REPLY="${ans:-$def}"
}
ask_secret() {
  local ans=""
  printf '  %s: ' "$1" >&2
  IFS= read -r -s ans <&3 || true; printf '\n' >&2
  REPLY="$ans"
}
yesno() {  # yesno "pregunta" "s|n" -> 0 si sí
  local def="${2:-n}" ans
  ask "$1 (s/n)" "$def"; ans="$REPLY"
  case "$ans" in s|S|si|SI|Si|y|Y) return 0 ;; *) return 1 ;; esac
}

# Fuente de los ficheros: carpeta local (si se ejecuta desde un clon) o descarga.
SRC_DIR=""
if [ -n "${BASH_SOURCE[0]:-}" ] && [ -f "${BASH_SOURCE[0]}" ]; then SRC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"; fi
LOCAL=""; [ -n "$SRC_DIR" ] && [ -f "$SRC_DIR/bridge/iaia-bridge.py" ] && LOCAL=1
HDR=(); [ -n "${GITHUB_TOKEN:-}" ] && HDR=(-H "Authorization: Bearer $GITHUB_TOKEN")
fetch() {  # fetch ruta_en_repo destino
  if [ -n "$LOCAL" ]; then cp "$SRC_DIR/$1" "$2"
  else curl -fsSL "${HDR[@]}" "$REPO_RAW/$1" -o "$2" || die "No se pudo descargar $1 desde $REPO_RAW (¿repositorio privado? usa GITHUB_TOKEN)."; fi
}
backup() { [ -e "$1" ] && cp -p "$1" "$1.bak-$STAMP" && ok "Copia de seguridad: $1.bak-$STAMP" || true; }
export XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}"

# ---------------------------------------------------------------- desinstalar
if [ "${1:-}" = "--uninstall" ]; then
  say "Desinstalando iaia-bridge"
  if [ -z "${IAIA_NO_SERVICE:-}" ]; then
    systemctl --user disable --now iaia-bridge.service 2>/dev/null || true
  fi
  rm -f "$UNIT_DST" "$BRIDGE_DST"
  [ -z "${IAIA_NO_SERVICE:-}" ] && systemctl --user daemon-reload || true
  ok "Servicio y puente eliminados"
  if [ "${2:-}" = "--purge" ]; then rm -f "$CONF"; ok "Configuración eliminada (el código del dispositivo se perderá)"; else warn "Configuración conservada en $CONF"; fi
  exit 0
fi

# ---------------------------------------------------------------- 1. entorno
say "1/6 Comprobando el entorno"
[ "$(id -u)" -ne 0 ] || die "No ejecutes este script como root ni con sudo. Usa el usuario 'ovos'."
[ -x "$PY" ] || die "No encuentro el entorno de OVOS en $VENV (¿otro usuario o ruta? usa IAIA_VENV=...)."
ok "Python de OVOS: $($PY --version 2>&1)"
"$PY" -c "import ovos_bus_client" 2>/dev/null && ok "ovos_bus_client disponible" || die "ovos_bus_client no está instalado en el entorno de OVOS."
command -v curl >/dev/null || [ -n "$LOCAL" ] || die "Falta curl."
if [ -z "${IAIA_NO_SERVICE:-}" ]; then
  systemctl --user cat ovos-messagebus.service >/dev/null 2>&1 && ok "Servicio ovos-messagebus encontrado" \
    || warn "No veo ovos-messagebus.service como servicio de usuario; el puente arrancará pero no podrá hablar hasta que OVOS esté activo."
fi

# ---------------------------------------------------------------- 2. paho-mqtt
say "2/6 Dependencia paho-mqtt"
snapshot() { uv pip freeze --python "$PY" 2>/dev/null | grep -iE "^($PROTECTED)==" | sort || true; }
if "$PY" -c "import paho.mqtt" 2>/dev/null; then
  ok "paho-mqtt ya está instalado ($("$PY" -c 'import paho.mqtt as p;print(p.__version__)'))"
else
  command -v uv >/dev/null || die "Falta 'uv' para instalar paho-mqtt de forma segura."
  warn "paho-mqtt no está instalado. Simulo la instalación primero (--dry-run)..."
  DRY="$(mktemp)"; uv pip install --python "$PY" --dry-run paho-mqtt >"$DRY" 2>&1 || { cat "$DRY"; die "El dry-run falló."; }
  sed 's/^/    /' "$DRY"
  if grep -qiE "($PROTECTED)" "$DRY"; then
    rm -f "$DRY"; die "El dry-run toca paquetes protegidos de OVOS. NO se instala nada. Revisa la salida de arriba."
  fi
  ok "El dry-run no toca ovos-workshop, ovos-bus-client, padacioso, ovos-core, ovos-utils ni ovos-plugin-manager"
  rm -f "$DRY"
  yesno "¿Instalar paho-mqtt ahora?" s || die "Instalación cancelada."
  BEFORE="$(snapshot)"
  uv pip install --python "$PY" paho-mqtt
  AFTER="$(snapshot)"
  if [ "$BEFORE" != "$AFTER" ]; then
    printf '%s\n' "ANTES:" "$BEFORE" "DESPUÉS:" "$AFTER" >&2
    die "Han cambiado versiones de paquetes protegidos. Restaura con: uv pip install --python $PY ovos-workshop==<ver> ovos-bus-client==<ver> padacioso==<ver>"
  fi
  CHECK="$(uv pip check --python "$PY" 2>&1 || true)"
  if printf '%s\n' "$CHECK" | grep -iE "workshop|bus-client|padacioso"; then warn "pip check muestra avisos sobre paquetes de OVOS (arriba)"
  else ok "pip check sin avisos sobre workshop, bus-client ni padacioso"; fi
fi

# ---------------------------------------------------------------- 3. ficheros
say "3/6 Instalando el puente"
mkdir -p "$UNIT_DIR" "$(dirname "$LOG")" "$(dirname "$CONF")"
backup "$BRIDGE_DST"; backup "$UNIT_DST"
fetch bridge/iaia-bridge.py "$BRIDGE_DST"; chmod 755 "$BRIDGE_DST"
fetch bridge/iaia-bridge.service "$UNIT_DST"
"$PY" -c "import ast,sys;ast.parse(open(sys.argv[1]).read())" "$BRIDGE_DST" && ok "Puente instalado en $BRIDGE_DST" || die "El puente descargado no es Python válido."

# ---- 3b. skill de escucha (opcional): hace falta para «Llamar y esperar respuesta»
SKILL_DIR="$HOME/iaia-skill-familia"; SKILL_CHANGED=""
if yesno "¿Instalar también la skill de escucha (Iaia dice el mensaje, escucha y devuelve la respuesta)?" s; then
  mkdir -p "$SKILL_DIR/ovos_skill_iaia_familia"
  fetch skill/ovos-skill-iaia-familia/setup.py "$SKILL_DIR/setup.py"
  fetch skill/ovos-skill-iaia-familia/ovos_skill_iaia_familia/__init__.py "$SKILL_DIR/ovos_skill_iaia_familia/__init__.py"
  for f in "$SKILL_DIR/setup.py" "$SKILL_DIR/ovos_skill_iaia_familia/__init__.py"; do
    "$PY" -c "import ast,sys;ast.parse(open(sys.argv[1]).read())" "$f" || die "$f no es Python válido."
  done
  ok "Ficheros de la skill en $SKILL_DIR"
  if "$PY" -c "import importlib.metadata as m,sys;m.version('ovos-skill-iaia-familia')" 2>/dev/null; then
    ok "La skill ya estaba instalada (modo editable): quedan actualizados sus ficheros"
  else
    command -v uv >/dev/null || die "Falta 'uv' para instalar la skill de forma segura."
    warn "Simulo la instalación de la skill primero (--dry-run, sin dependencias)..."
    DRY="$(mktemp)"; uv pip install --python "$PY" --dry-run --no-deps -e "$SKILL_DIR" >"$DRY" 2>&1 || { cat "$DRY"; die "El dry-run de la skill falló."; }
    sed 's/^/    /' "$DRY"
    if grep -qiE "($PROTECTED)" "$DRY"; then rm -f "$DRY"; die "El dry-run de la skill toca paquetes protegidos de OVOS. NO se instala nada."; fi
    rm -f "$DRY"; ok "El dry-run solo añade la skill y no toca paquetes protegidos"
    BEFORE="$(snapshot)"
    uv pip install --python "$PY" --no-deps -e "$SKILL_DIR"
    AFTER="$(snapshot)"
    if [ "$BEFORE" != "$AFTER" ]; then printf '%s\n' "ANTES:" "$BEFORE" "DESPUÉS:" "$AFTER" >&2; die "Han cambiado versiones de paquetes protegidos."; fi
    CHECK="$(uv pip check --python "$PY" 2>&1 || true)"
    if printf '%s\n' "$CHECK" | grep -iE "workshop|bus-client|padacioso"; then warn "pip check muestra avisos sobre paquetes de OVOS (arriba)"
    else ok "pip check sin avisos sobre workshop, bus-client ni padacioso"; fi
  fi
  SKILL_CHANGED=1
  warn "Recuerda añadir $SKILL_DIR a DIRS en ~/iaia-sync.sh para que quede en el repositorio de copia."
fi

# ---------------------------------------------------------------- 4. configuración
say "4/6 Configuración del broker MQTT"
CODE=""
if [ -f "$CONF" ]; then
  CODE="$("$PY" -c "import json,sys;print(json.load(open(sys.argv[1])).get('device_id',''))" "$CONF" 2>/dev/null || true)"
  warn "Ya existe una configuración ($CONF) con el código ${CODE:-desconocido}."
  if yesno "¿Conservarla y no preguntar de nuevo?" s; then KEEP=1; else KEEP=""; backup "$CONF"; fi
else KEEP=""; fi
if [ -z "$KEEP" ]; then
  echo "  Necesitas un broker MQTT con TLS (HiveMQ Cloud, EMQX Cloud o Mosquitto propio)."
  ask "Servidor del broker (host, sin https://)" "${IAIA_HOST:-}"; HOST="$REPLY"; [ -n "$HOST" ] || die "El servidor es obligatorio."
  ask "Puerto MQTT para la Raspberry (TLS)" "8883"; PORT="$REPLY"
  case "$PORT" in ''|*[!0-9]*) die "El puerto debe ser un número." ;; esac
  yesno "¿El broker usa TLS (recomendado)?" s && TLS=true || TLS=false
  ask "Usuario del puente" ""; USER_="$REPLY"
  ask_secret "Contraseña del puente (no se muestra)"; PASS="$REPLY"
  ask "Prefijo de los temas" "iaia"; PREFIX="$REPLY"
  if [ -z "$CODE" ]; then
    CODE="$("$PY" - <<'PYEOF'
import secrets
a = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
g = lambda: "".join(secrets.choice(a) for _ in range(4))
print(f"IAIA-{g()}-{g()}")
PYEOF
)"
    ok "Código de dispositivo generado: $CODE"
  fi
  ask "Código del dispositivo (Intro para aceptar)" "$CODE"; CODE="$REPLY"
  [[ "$CODE" =~ ^IAIA-[A-Z0-9]{4}-[A-Z0-9]{4}$ ]] || die "El código debe tener el formato IAIA-XXXX-XXXX (letras mayúsculas y números)."
  ( umask 077
    H="$HOST" P="$PORT" T="$TLS" U="$USER_" W="$PASS" X="$PREFIX" D="$CODE" "$PY" - >"$CONF" <<'PYEOF'
import json, os
print(json.dumps({"host": os.environ["H"], "port": int(os.environ["P"]), "tls": os.environ["T"] == "true",
                  "username": os.environ["U"], "password": os.environ["W"],
                  "device_id": os.environ["D"], "prefix": os.environ["X"]}, indent=2))
PYEOF
  )
  chmod 600 "$CONF"
  "$PY" -c "import json,sys;json.load(open(sys.argv[1]))" "$CONF" && ok "Configuración guardada en $CONF (permisos 600)" || die "La configuración no es un JSON válido."
fi
HOST="$("$PY" -c "import json,sys;print(json.load(open(sys.argv[1]))['host'])" "$CONF")"
PORT="$("$PY" -c "import json,sys;print(json.load(open(sys.argv[1]))['port'])" "$CONF")"
CODE="$("$PY" -c "import json,sys;print(json.load(open(sys.argv[1]))['device_id'])" "$CONF")"
PREFIX="$("$PY" -c "import json,sys;print(json.load(open(sys.argv[1])).get('prefix','iaia'))" "$CONF")"
if timeout 6 bash -c "exec 4<>/dev/tcp/$HOST/$PORT" 2>/dev/null; then ok "Se puede alcanzar $HOST:$PORT"
else warn "No se alcanza $HOST:$PORT desde la Pi (red, puerto o nombre incorrectos). El servicio se instalará igualmente."; fi

# ---------------------------------------------------------------- 5. servicio
say "5/6 Activando el servicio"
if [ -n "${IAIA_NO_SERVICE:-}" ]; then
  warn "IAIA_NO_SERVICE definido: no se activa systemd (modo prueba)."
else
  : >"$LOG"
  systemctl --user daemon-reload
  systemctl --user enable --now iaia-bridge.service || die "No se pudo activar el servicio. Si estás por SSH prueba: export XDG_RUNTIME_DIR=/run/user/\$(id -u)"
  for _ in $(seq 1 20); do grep -aq "Conectado al broker" "$LOG" 2>/dev/null && break; sleep 1; done
  if grep -aq "Conectado al broker" "$LOG" 2>/dev/null; then ok "El puente se ha conectado al broker"
  else
    warn "Todavía no veo la conexión al broker. Estado del servicio:"
    systemctl --user status iaia-bridge.service -n 15 --no-pager 2>&1 | sed 's/^/    /' || true
    tail -n 8 "$LOG" 2>/dev/null | sed 's/^/    /' || true
  fi
  if [ "$(loginctl show-user "$USER" -p Linger --value 2>/dev/null || echo no)" != "yes" ]; then
    warn "El arranque automático sin sesión abierta necesita 'linger'."
    yesno "¿Activarlo ahora con sudo?" n && sudo loginctl enable-linger "$USER" && ok "linger activado"
  fi
fi

# ---------------------------------------------------------------- 6. resumen
if [ -n "$SKILL_CHANGED" ] && [ -z "${IAIA_NO_SERVICE:-}" ]; then
  warn "OVOS carga las skills al arrancar: hay que reiniciar ovos-skills para activar la skill de escucha."
  if yesno "¿Reiniciar ovos-skills ahora? (Iaia no responderá durante 40-60 s)" n; then
    systemctl --user restart ovos-skills && ok "ovos-skills reiniciado; espera 40-60 s antes de probar" || warn "No pude reiniciarlo. Hazlo tú: systemctl --user restart ovos-skills"
  else warn "Hazlo cuando quieras: systemctl --user restart ovos-skills"; fi
fi

say "6/6 Resumen"
SSID=""
if command -v nmcli >/dev/null 2>&1; then SSID="$(nmcli -t -f active,ssid dev wifi 2>/dev/null | awk -F: '$1=="yes"{print $2; exit}')"; fi
if [ -z "$SSID" ] && command -v iwgetid >/dev/null 2>&1; then SSID="$(iwgetid -r 2>/dev/null || true)"; fi
IP="$(hostname -I 2>/dev/null | awk '{print $1}')"
echo
echo "  Wifi (SSID):        ${SSID:-ninguna (la Pi está por cable o sin wifi)}"
echo "  IP de la Pi:        ${IP:-desconocida}"
echo "  Código del equipo:  $CODE"
echo
if ! command -v qrencode >/dev/null 2>&1; then
  warn "Para dibujar el QR hace falta el paquete 'qrencode'."
  yesno "¿Instalarlo con sudo apt?" s && sudo apt-get install -y qrencode >/dev/null && ok "qrencode instalado" || true
fi
if command -v qrencode >/dev/null 2>&1; then
  echo "  Código QR (contiene solo el código del equipo):"; echo
  qrencode -t ANSIUTF8 -m 2 "$CODE" | sed 's/^/    /'
else
  warn "Sin 'qrencode' no se puede mostrar el QR. Instálalo con: sudo apt install qrencode"
fi
echo
echo "  Siguientes pasos:"
echo "   1. En el broker, da permiso al usuario del puente sobre $PREFIX/$CODE/# y a la app solo lectura de status/ack y escritura de cmd."
echo "   2. En la app: Añadir dispositivo -> escribe el código $CODE (la lectura del QR con la cámara aún no está en la app)."
echo "   3. Log del puente: tail -f $LOG"
echo
