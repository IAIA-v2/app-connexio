#!/usr/bin/env bash
# Instalador interactivo del broker MQTT (Mosquitto + TLS + WebSocket) para Iaia en una instancia
# Ubuntu 22.04/24.04 (por ejemplo, EC2 de AWS). Ejecutar con sudo:
#
#   sudo bash setup-mosquitto.sh                       configuración inicial (+ primer dispositivo)
#   sudo bash setup-mosquitto.sh --add-device [CODIGO] da de alta una Iaia (crea su usuario de puente)
#   sudo bash setup-mosquitto.sh --add-app-user [NOM]  crea un usuario para la app (familia)
#   sudo bash setup-mosquitto.sh --grant USUARIO CODIGO  da a un usuario de la app acceso a una Iaia
#   sudo bash setup-mosquitto.sh --remove-device CODIGO  elimina una Iaia y su usuario de puente
#   sudo bash setup-mosquitto.sh --list                muestra dispositivos y accesos
#
# Modelo de seguridad: cada Iaia tiene su propio usuario "pi-<CODIGO>" que solo puede tocar sus temas;
# cada usuario de la app solo puede leer status/ack y escribir cmd de las Iaia a las que se le dio acceso.
# Las contraseñas se guardan con hash en /etc/mosquitto/passwd; el fichero de permisos (acl) se genera
# siempre desde /etc/mosquitto/iaia/, así que no lo edites a mano.
set -euo pipefail

ETC="${IAIA_ETC:-/etc/mosquitto}"
STATE="$ETC/iaia"
SETTINGS="$STATE/settings"
DEVICES="$STATE/devices"
ACCESS="$STATE/access"
PASSWD="$ETC/passwd"
ACL="$ETC/acl"
CERTS="$ETC/certs"
CONFD="$ETC/conf.d/iaia.conf"
STAMP="$(date +%Y%m%d-%H%M%S)"
TEST="${IAIA_TEST:-}"                      # solo para pruebas: no instala ni toca servicios

say()  { printf '\n\033[1m== %s\033[0m\n' "$*"; }
ok()   { printf '  \033[32m✓\033[0m %s\n' "$*"; }
warn() { printf '  \033[33m!\033[0m %s\n' "$*"; }
die()  { printf '\n\033[31mERROR:\033[0m %s\n' "$*" >&2; exit 1; }

[ "$(id -u)" -eq 0 ] || [ -n "$TEST" ] || die "Ejecuta el script con sudo."
TTY_SRC="${IAIA_TTY:-/dev/tty}"
( : <"$TTY_SRC" ) 2>/dev/null || die "No hay terminal interactivo. Descarga el script y ejecútalo con: sudo bash setup-mosquitto.sh"
exec 3<"$TTY_SRC"
ask() {  # ask "texto" "defecto" -> REPLY
  local def="${2:-}" ans=""
  if [ -n "$def" ]; then printf '  %s [%s]: ' "$1" "$def" >&2; else printf '  %s: ' "$1" >&2; fi
  IFS= read -r ans <&3 || true; REPLY="${ans:-$def}"
}
ask_secret() { local a=""; printf '  %s: ' "$1" >&2; IFS= read -r -s a <&3 || true; printf '\n' >&2; REPLY="$a"; }
yesno() { local d="${2:-n}"; ask "$1 (s/n)" "$d"; case "$REPLY" in s|S|si|Si|SI|y|Y) return 0 ;; *) return 1 ;; esac; }

CODE_RE='^IAIA-[A-Z0-9]{4}-[A-Z0-9]{4}$'
USER_RE='^[A-Za-z0-9_-]+$'
rand_pass() { openssl rand -base64 24 | tr -d '/+=\n' | cut -c1-20; }
backup() { [ -e "$1" ] && cp -p "$1" "$1.bak-$STAMP" || true; }

load_settings() {
  [ -f "$SETTINGS" ] || die "El broker no está configurado todavía. Ejecuta primero: sudo bash setup-mosquitto.sh"
  # shellcheck disable=SC1090
  . "$SETTINGS"
}

# Guarda/actualiza la contraseña de un usuario con hash (sin pasarla por la línea de comandos).
set_password() {  # set_password usuario contraseña
  local u="$1" p="$2" tmp; tmp="$(mktemp)"; chmod 600 "$tmp"
  printf '%s:%s\n' "$u" "$p" >"$tmp"
  mosquitto_passwd -U "$tmp"                 # convierte SOLO esta línea a hash
  touch "$PASSWD"; chmod 600 "$PASSWD"
  sed -i "/^$u:/d" "$PASSWD"
  cat "$tmp" >>"$PASSWD"; rm -f "$tmp"
  [ -n "$TEST" ] || chown mosquitto: "$PASSWD"
}

regen_acl() {
  load_settings
  local t c u; t="$(mktemp)"
  {
    echo "# Generado por setup-mosquitto.sh ($STAMP). No editar a mano: ver /etc/mosquitto/iaia/"
    if [ -s "$DEVICES" ]; then while read -r c; do [ -n "$c" ] || continue
      printf '\nuser pi-%s\ntopic readwrite %s/%s/#\n' "$c" "$PREFIX" "$c"; done <"$DEVICES"; fi
    if [ -s "$ACCESS" ]; then while read -r u c; do [ -n "$u" ] && [ -n "$c" ] || continue
      printf '\nuser %s\ntopic read %s/%s/status\ntopic read %s/%s/ack\ntopic read %s/%s/messages\ntopic read %s/%s/history\ntopic write %s/%s/cmd\n' "$u" "$PREFIX" "$c" "$PREFIX" "$c" "$PREFIX" "$c" "$PREFIX" "$c" "$PREFIX" "$c"; done <"$ACCESS"; fi
  } >"$t"
  backup "$ACL"
  install -m 600 "$t" "$ACL"; rm -f "$t"
  [ -n "$TEST" ] || { chown mosquitto: "$ACL"; systemctl reload mosquitto 2>/dev/null || systemctl restart mosquitto; }
}

app_users() { [ -f "$PASSWD" ] && cut -d: -f1 "$PASSWD" | grep -v '^pi-' || true; }

# ------------------------------------------------------------------ acciones
add_app_user() {  # add_app_user [nombre]
  load_settings
  local name="${1:-}"; GENERATED_APP_PASS=""
  [ -n "$name" ] || { ask "Nombre del usuario de la app (por ejemplo familia-garcia)" ""; name="$REPLY"; }
  [[ "$name" =~ $USER_RE ]] || die "El nombre solo puede tener letras, números, guion y guion bajo."
  [[ "$name" != pi-* ]] || die "Los nombres que empiezan por pi- están reservados para las Iaia."
  ask_secret "Contraseña para $name (Intro = generar una)"; local p="$REPLY"
  [ -n "$p" ] || { p="$(rand_pass)"; GENERATED_APP_PASS="$p"; }
  set_password "$name" "$p"
  ok "Usuario de app creado: $name"
  [ -z "${GENERATED_APP_PASS:-}" ] || printf '    Contraseña generada (guárdala, no se vuelve a mostrar): %s\n' "$GENERATED_APP_PASS"
  APP_USER_CREATED="$name"
}

grant() {  # grant usuario codigo
  load_settings
  local u="$1" c="$2"
  [[ "$c" =~ $CODE_RE ]] || die "Código no válido: $c"
  grep -qx "$c" "$DEVICES" 2>/dev/null || die "El dispositivo $c no existe. Dalo de alta con --add-device."
  grep -q "^$u:" "$PASSWD" 2>/dev/null || die "El usuario $u no existe. Créalo con --add-app-user."
  grep -qx "$u $c" "$ACCESS" 2>/dev/null || echo "$u $c" >>"$ACCESS"
  regen_acl; ok "$u puede ver y enviar mensajes a $c"
}

add_device() {  # add_device [codigo]
  load_settings
  local c="${1:-}"
  [ -n "$c" ] || { ask "Código de la Iaia (el que salió en install.sh, IAIA-XXXX-XXXX)" ""; c="$REPLY"; }
  c="$(printf '%s' "$c" | tr '[:lower:]' '[:upper:]')"
  [[ "$c" =~ $CODE_RE ]] || die "El código debe tener el formato IAIA-XXXX-XXXX."
  grep -qx "$c" "$DEVICES" 2>/dev/null && die "$c ya está dado de alta."
  local p; p="$(rand_pass)"
  echo "$c" >>"$DEVICES"
  set_password "pi-$c" "$p"
  regen_acl
  ok "Dispositivo $c dado de alta"
  printf '\n  \033[1mDatos para install.sh en la Raspberry (se muestran una sola vez):\033[0m\n'
  printf '    Servidor:    %s\n    Puerto:      %s (TLS: sí)\n    Usuario:     pi-%s\n    Contraseña:  %s\n    Prefijo:     %s\n    Código:      %s\n' "$DOMAIN" "$PORT_MQTT" "$c" "$p" "$PREFIX" "$c"
  LAST_DEVICE="$c"; LAST_DEVICE_PASS="$p"
  if yesno "¿Dar acceso a esta Iaia a un usuario de la app?" s; then
    local existing; existing="$(app_users | tr '\n' ' ')"
    [ -z "$existing" ] || echo "    Usuarios de app existentes: $existing" >&2
    ask "Nombre del usuario de la app (existente o nuevo)" ""; local u="$REPLY"
    [[ "$u" =~ $USER_RE ]] || die "Nombre no válido."
    grep -q "^$u:" "$PASSWD" 2>/dev/null || add_app_user "$u"
    grant "$u" "$c"
  fi
}

remove_device() {
  load_settings
  local c="$1"
  grep -qx "$c" "$DEVICES" 2>/dev/null || die "$c no está dado de alta."
  yesno "¿Eliminar $c y su usuario pi-$c?" n || die "Cancelado."
  sed -i "/^$c\$/d" "$DEVICES"; sed -i "/ $c\$/d" "$ACCESS" 2>/dev/null || true
  sed -i "/^pi-$c:/d" "$PASSWD"
  regen_acl; ok "$c eliminado"
}

list_all() {
  load_settings
  echo "  Broker: $DOMAIN  (MQTT TLS $PORT_MQTT, WebSocket TLS $PORT_WS)   Prefijo: $PREFIX"
  echo "  Dispositivos:"; [ -s "$DEVICES" ] && sed 's/^/    /' "$DEVICES" || echo "    (ninguno)"
  echo "  Accesos de la app (usuario código):"; [ -s "$ACCESS" ] && sed 's/^/    /' "$ACCESS" || echo "    (ninguno)"
}

selftest() {  # selftest codigo contraseña_pi
  local c="$1" p="$2" out; out="$(mktemp)"
  say "Prueba de extremo a extremo (TLS + usuario + permisos)"
  mosquitto_sub -h "$DOMAIN" -p "$PORT_MQTT" --capath /etc/ssl/certs -u "pi-$c" -P "$p" \
    -t "$PREFIX/$c/status" -C 1 -W 10 >"$out" 2>&1 &
  local pid=$!; sleep 2
  mosquitto_pub -h "$DOMAIN" -p "$PORT_MQTT" --capath /etc/ssl/certs -u "pi-$c" -P "$p" \
    -t "$PREFIX/$c/status" -m "{\"online\":true,\"ts\":$(date +%s)}" 2>&1 | sed 's/^/    /' || true
  wait "$pid" 2>/dev/null || true
  if grep -q '"online"' "$out"; then ok "El broker acepta TLS, el usuario pi-$c y publica/suscribe en sus temas"
  else warn "La prueba no recibió el mensaje. Salida: $(head -c 300 "$out")"; fi
  rm -f "$out"
}

# ------------------------------------------------------------------ configuración inicial
initial_setup() {
  if [ -f "$SETTINGS" ]; then
    warn "El broker ya está configurado. Para añadir Iaias usa --add-device; para ver el estado, --list."
    yesno "¿Repetir la configuración inicial de todos modos?" n || exit 0
  fi
  say "1/5 Datos del servidor"
  echo "  Necesitas un nombre DNS (ej. mqtt.tudominio.com) apuntando a la IP de esta máquina."
  ask "Nombre DNS del broker" ""; DOMAIN="$REPLY"; [[ "$DOMAIN" =~ ^[A-Za-z0-9.-]+\.[A-Za-z]{2,}$ ]] || die "Nombre DNS no válido."
  ask "Email para avisos de caducidad del certificado (opcional)" ""; EMAIL="$REPLY"
  ask "Prefijo de los temas" "iaia"; PREFIX="$REPLY"; [[ "$PREFIX" =~ $USER_RE ]] || die "Prefijo no válido."
  ask "Puerto MQTT con TLS (Raspberry)" "8883"; PORT_MQTT="$REPLY"
  ask "Puerto WebSocket con TLS (app)" "8884"; PORT_WS="$REPLY"
  for pt in "$PORT_MQTT" "$PORT_WS"; do case "$pt" in ''|*[!0-9]*) die "Los puertos deben ser números." ;; esac; done

  if [ -z "$TEST" ]; then
    local pub resolved
    pub="$(curl -fsS --max-time 5 https://checkip.amazonaws.com 2>/dev/null | tr -d '[:space:]' || true)"
    resolved="$(getent hosts "$DOMAIN" | awk '{print $1; exit}' || true)"
    echo "  IP pública de esta máquina: ${pub:-desconocida}   |   $DOMAIN apunta a: ${resolved:-nada}"
    if [ -n "$pub" ] && [ "$pub" != "$resolved" ]; then
      warn "El DNS no apunta todavía a esta máquina. Let's Encrypt fallará hasta que se propague."
      yesno "¿Continuar de todos modos?" n || die "Cancelado. Crea el registro A y vuelve a ejecutar."
    fi
  fi

  say "2/5 Instalando Mosquitto y certbot"
  if [ -z "$TEST" ]; then
    export DEBIAN_FRONTEND=noninteractive
    apt-get update -qq && apt-get install -y -qq mosquitto mosquitto-clients certbot >/dev/null
    ok "Paquetes instalados ($(mosquitto -h 2>&1 | head -1))"
  else warn "IAIA_TEST: no se instalan paquetes"; fi
  mkdir -p "$STATE" "$CERTS" "$ETC/conf.d"; touch "$DEVICES" "$ACCESS"

  say "3/5 Certificado TLS (Let's Encrypt)"
  local hook=/etc/letsencrypt/renewal-hooks/deploy/mosquitto.sh
  if [ -z "$TEST" ]; then
    mkdir -p "$(dirname "$hook")"
    cat >"$hook" <<EOF
#!/bin/sh
# Copia el certificado renovado para Mosquitto y reinicia el broker.
D=/etc/letsencrypt/live/$DOMAIN
cp \$D/fullchain.pem $CERTS/fullchain.pem
cp \$D/privkey.pem   $CERTS/privkey.pem
chown mosquitto: $CERTS/*.pem && chmod 640 $CERTS/*.pem
systemctl restart mosquitto
EOF
    chmod +x "$hook"
    if [ -n "$EMAIL" ]; then M=(-m "$EMAIL"); else M=(--register-unsafely-without-email); fi
    systemctl stop mosquitto 2>/dev/null || true
    certbot certonly --standalone -d "$DOMAIN" "${M[@]}" --agree-tos -n || die "certbot falló. Comprueba que el puerto 80 está abierto en el Security Group y que el DNS apunta aquí."
    "$hook"; ok "Certificado instalado; se renueva solo (certbot.timer)"
  else warn "IAIA_TEST: sin certificado"; fi

  say "4/5 Configuración del broker"
  backup "$CONFD"
  cat >"$CONFD" <<EOF
# Generado por setup-mosquitto.sh ($STAMP)
per_listener_settings true

# Raspberry Pi (MQTT + TLS)
listener $PORT_MQTT
protocol mqtt
certfile $CERTS/fullchain.pem
keyfile  $CERTS/privkey.pem
allow_anonymous false
password_file $PASSWD
acl_file $ACL

# App web (WebSocket + TLS)
listener $PORT_WS
protocol websockets
certfile $CERTS/fullchain.pem
keyfile  $CERTS/privkey.pem
allow_anonymous false
password_file $PASSWD
acl_file $ACL
EOF
  printf 'DOMAIN=%q\nEMAIL=%q\nPREFIX=%q\nPORT_MQTT=%q\nPORT_WS=%q\n' "$DOMAIN" "$EMAIL" "$PREFIX" "$PORT_MQTT" "$PORT_WS" >"$SETTINGS"
  touch "$PASSWD"; chmod 600 "$PASSWD"
  regen_acl
  if [ -z "$TEST" ]; then
    systemctl enable mosquitto >/dev/null 2>&1 || true
    systemctl restart mosquitto
    sleep 2
    systemctl is-active --quiet mosquitto && ok "Mosquitto activo" || { systemctl status mosquitto --no-pager -n 15; die "Mosquitto no arrancó."; }
    for pt in "$PORT_MQTT" "$PORT_WS"; do ss -ltn 2>/dev/null | grep -q ":$pt " && ok "Escuchando en el puerto $pt" || warn "No veo el puerto $pt a la escucha"; done
  fi

  say "5/5 Primera Iaia y usuario de la app"
  add_device ""
  if [ -z "$TEST" ] && [ -n "${LAST_DEVICE:-}" ]; then selftest "$LAST_DEVICE" "$LAST_DEVICE_PASS"; fi
  summary
}

summary() {
  load_settings
  say "Resumen"
  echo "  Para la app (Ajustes → Conexión real):  wss://$DOMAIN:$PORT_WS   (si no conecta, prueba con /mqtt al final)"
  echo "  Para install.sh en la Pi:               servidor $DOMAIN, puerto $PORT_MQTT, TLS sí"
  echo "  Recuerda abrir en el Security Group:    80 (certificado), $PORT_MQTT y $PORT_WS"
  echo "  Más Iaias:      sudo bash setup-mosquitto.sh --add-device"
  echo "  Más usuarios:   sudo bash setup-mosquitto.sh --add-app-user"
  echo "  Estado:         sudo bash setup-mosquitto.sh --list"
  echo "  Guarda copia de $PASSWD, $ACL y $STATE/"
  echo
}

case "${1:-}" in
  "")               initial_setup ;;
  --add-device)     add_device "${2:-}" ;;
  --add-app-user)   add_app_user "${2:-}" ;;
  --grant)          [ $# -eq 3 ] || die "Uso: --grant USUARIO CODIGO"; grant "$2" "$3" ;;
  --remove-device)  [ $# -eq 2 ] || die "Uso: --remove-device CODIGO"; remove_device "$2" ;;
  --list)           list_all ;;
  *)                die "Opción desconocida: $1" ;;
esac
