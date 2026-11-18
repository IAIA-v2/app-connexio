'use strict';
/* Iaia Remoto — prototipo PWA v0.1
   Dos "transportes" con la misma interfaz:
   - Demo: dispositivos simulados en el propio navegador (para probar la interfaz).
   - Mqtt: broker MQTT por WebSocket seguro (wss://) -> puente iaia-bridge.py en la Raspberry. */

const BT_SERVICE = '6e400001-b5a3-f393-e0a9-e50e24dcca9e';   // UUID provisional (hay que definir el definitivo)
const BT_PAIR_CHAR = '6e400002-b5a3-f393-e0a9-e50e24dcca9e';
const CODE_RE = /^IAIA-[A-Z0-9]{4}-[A-Z0-9]{4}$/;
const STALE_MS = 90000;           // sin latido en 90 s => inactivo
const PRESETS = ['Hello World', 'Hola, bon dia!', "És hora de prendre la medicació.", "Truco d'aquí a una estona."];

/* ---------- utilidades ---------- */
const $ = (s, el = document) => el.querySelector(s);
const esc = s => String(s).replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const uid = () => Math.random().toString(36).slice(2, 10);
const sleep = ms => new Promise(r => setTimeout(r, ms));
const store = {
  load(k, d) { try { const v = JSON.parse(localStorage.getItem('iaia.' + k)); return v ?? d; } catch { return d; } },
  save(k, v) { try { localStorage.setItem('iaia.' + k, JSON.stringify(v)); } catch { /* sin almacenamiento */ } }
};
function ago(ts) {
  if (!ts) return 'nunca';
  const s = Math.max(0, Math.round((Date.now() - ts) / 1000));
  if (s < 10) return 'ahora mismo';
  if (s < 60) return `hace ${s} s`;
  const m = Math.round(s / 60); if (m < 60) return `hace ${m} min`;
  const h = Math.round(m / 60); if (h < 24) return `hace ${h} h`;
  return `hace ${Math.round(h / 24)} d`;
}
function hhmm(ts) { return new Date(ts).toLocaleTimeString('es', { hour: '2-digit', minute: '2-digit' }); }
let toastT;
function toast(msg) { const t = $('#toast'); t.textContent = msg; t.classList.add('show'); clearTimeout(toastT); toastT = setTimeout(() => t.classList.remove('show'), 2600); }

/* ---------- estado ---------- */
const S = {
  devices: store.load('devices', []),                 // [{id, name}]
  logs: store.load('logs', {}),                       // {id: [{mid,text,t,state,err}]}
  settings: Object.assign({ mode: 'demo', url: '', user: '', pass: '', prefix: 'iaia', sound: true }, store.load('settings', {})),
  status: {},                                         // {id: {online, ts}}
  conn: { kind: 'demo', msg: '' },
  add: { step: 1, method: null, code: '', name: 'Iaia', busy: false, err: '', found: '' },
  tr: null
};
const dev = id => S.devices.find(d => d.id === id);
const isOnline = id => { const s = S.status[id]; return !!(s && s.online && Date.now() - s.ts < STALE_MS); };
function setStatus(id, s) { S.status[id] = Object.assign({}, S.status[id], s); refresh(); }
function setConn(kind, msg = '') { S.conn = { kind, msg }; refresh(); }
const saveDevices = () => store.save('devices', S.devices);
const saveLogs = () => store.save('logs', S.logs);

/* ---------- transporte DEMO ---------- */
function demoSpeak(text) {
  if (!S.settings.sound || !('speechSynthesis' in window)) return;
  try {
    const u = new SpeechSynthesisUtterance(text); u.lang = 'ca-ES'; u.rate = .9;
    const v = speechSynthesis.getVoices().find(v => /^ca/i.test(v.lang)); if (v) u.voice = v;
    speechSynthesis.cancel(); speechSynthesis.speak(u);
  } catch { /* sin voz */ }
}
class Demo {
  constructor() { this.sim = store.load('sim', {}); this.t = null; }
  isOn(id) { return this.sim[id] !== false; }
  start() { this.stop(); this.push(); this.t = setInterval(() => this.push(), 5000); setConn('demo'); }
  stop() { clearInterval(this.t); }
  push() { S.devices.forEach(d => { const on = this.isOn(d.id); setStatus(d.id, on ? { online: true, ts: Date.now() } : { online: false }); }); }
  setPower(id, on) { this.sim[id] = on; store.save('sim', this.sim); this.push(); }
  watch() { this.push(); }
  unwatch() { }
  async pair(code) {
    await sleep(700);
    if (!CODE_RE.test(code)) throw new Error('El código no es válido. Tiene el formato IAIA-XXXX-XXXX.');
    return { id: code };
  }
  async send(id, text, cb) {
    if (!this.isOn(id)) throw new Error('El dispositivo está inactivo.');
    cb('sent'); await sleep(350);
    cb('delivered'); await sleep(900 + text.length * 35);
    if (!this.isOn(id)) throw new Error('El dispositivo se desconectó antes de hablar.');
    cb('said'); demoSpeak(text);
  }
}

/* ---------- transporte MQTT (real) ---------- */
function loadMqttLib() {
  if (window.mqtt) return Promise.resolve();
  return new Promise((ok, ko) => {
    const s = document.createElement('script');
    s.src = 'https://cdn.jsdelivr.net/npm/mqtt@5/dist/mqtt.min.js';
    s.onload = ok; s.onerror = () => ko(new Error('No se pudo cargar la librería MQTT.'));
    document.head.appendChild(s);
  });
}
class Mqtt {
  constructor() { this.c = null; this.pending = new Map(); this.connected = false; }
  get p() { return S.settings.prefix || 'iaia'; }
  async start() {
    this.stop(); setConn('off', 'Conectando…');
    const s = S.settings;
    if (!/^wss:\/\//i.test(s.url)) { setConn('err', 'Indica una URL wss:// del broker (Ajustes).'); return; }
    try { await loadMqttLib(); } catch (e) { setConn('err', e.message); return; }
    this.c = window.mqtt.connect(s.url, { username: s.user || undefined, password: s.pass || undefined,
      clientId: 'iaia-pwa-' + uid(), reconnectPeriod: 3000, connectTimeout: 8000, clean: true });
    this.c.on('connect', () => { this.connected = true; setConn('ok'); S.devices.forEach(d => this.watch(d.id)); });
    this.c.on('close', () => { this.connected = false; setConn('off', 'Sin conexión con el broker'); });
    this.c.on('error', e => setConn('err', e.message || 'Error de conexión'));
    this.c.on('message', (topic, payload) => this.onMsg(topic, payload));
  }
  stop() { if (this.c) { try { this.c.end(true); } catch { } this.c = null; } this.connected = false; }
  watch(id) { if (this.c && this.connected) this.c.subscribe([`${this.p}/${id}/status`, `${this.p}/${id}/ack`], { qos: 1 }); }
  unwatch(id) { if (this.c) this.c.unsubscribe([`${this.p}/${id}/status`, `${this.p}/${id}/ack`]); }
  onMsg(topic, payload) {
    let j; try { j = JSON.parse(payload.toString()); } catch { return; }
    const [, id, kind] = topic.split('/');
    if (kind === 'status') setStatus(id, { online: !!j.online, ts: j.ts ? j.ts * 1000 : Date.now() });
    if (kind === 'ack' && this.pending.has(j.id)) this.pending.get(j.id)(j);
  }
  async pair(code) {
    if (!CODE_RE.test(code)) throw new Error('El código no es válido. Tiene el formato IAIA-XXXX-XXXX.');
    return { id: code };   // v0.1: el código es el identificador. En producción: canjearlo en la nube por credenciales propias.
  }
  send(id, text, cb) {
    if (!this.connected) return Promise.reject(new Error('Sin conexión con el broker.'));
    if (!isOnline(id)) return Promise.reject(new Error('El dispositivo está inactivo.'));
    const mid = uid();
    return new Promise((res, rej) => {
      const to = setTimeout(() => { this.pending.delete(mid); rej(new Error('El dispositivo no confirmó a tiempo.')); }, 45000);
      this.pending.set(mid, j => {
        if (j.stage === 'received') return cb('delivered');
        clearTimeout(to); this.pending.delete(mid);
        if (j.ok) { cb('said'); res(); } else rej(new Error(j.error || 'Error en el dispositivo.'));
      });
      this.c.publish(`${this.p}/${id}/cmd`, JSON.stringify({ id: mid, type: 'say', text, ts: Math.floor(Date.now() / 1000) }),
        { qos: 1 }, err => { if (err) { clearTimeout(to); rej(err); } else cb('sent'); });
    });
  }
}

function useTransport() {
  if (S.tr) S.tr.stop();
  S.tr = S.settings.mode === 'mqtt' ? new Mqtt() : new Demo();
  S.status = {};
  S.tr.start();
}

/* ---------- Bluetooth ---------- */
const btSupported = () => !!navigator.bluetooth;
async function scanBT() {
  if (S.settings.mode === 'demo') { await sleep(2000); return { name: 'IAIA-DEMO', code: 'IAIA-DEMO-0001' }; }
  if (!btSupported()) throw new Error('Este navegador no permite Bluetooth web. Usa el código de la etiqueta.');
  const d = await navigator.bluetooth.requestDevice({ filters: [{ namePrefix: 'IAIA-' }], optionalServices: [BT_SERVICE] });
  try {
    const g = await d.gatt.connect(); const srv = await g.getPrimaryService(BT_SERVICE);
    const v = await (await srv.getCharacteristic(BT_PAIR_CHAR)).readValue();
    g.disconnect(); return { name: d.name, code: new TextDecoder().decode(v).trim().toUpperCase() };
  } catch { return { name: d.name, code: '' }; }
}

/* ---------- vistas ---------- */
const ICONS = {
  home: '<svg viewBox="0 0 24 24"><path d="M3 11l9-8 9 8M5 10v10h14V10"/></svg>',
  plus: '<svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="9"/><path d="M12 8v8M8 12h8"/></svg>',
  gear: '<svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.7 1.7 0 00.3 1.8l.1.1a2 2 0 11-2.8 2.8l-.1-.1a1.7 1.7 0 00-1.8-.3 1.7 1.7 0 00-1 1.5V21a2 2 0 11-4 0v-.1a1.7 1.7 0 00-1.1-1.5 1.7 1.7 0 00-1.8.3l-.1.1a2 2 0 11-2.8-2.8l.1-.1a1.7 1.7 0 00.3-1.8 1.7 1.7 0 00-1.5-1H3a2 2 0 110-4h.1a1.7 1.7 0 001.5-1.1 1.7 1.7 0 00-.3-1.8l-.1-.1a2 2 0 112.8-2.8l.1.1a1.7 1.7 0 001.8.3H9a1.7 1.7 0 001-1.5V3a2 2 0 114 0v.1a1.7 1.7 0 001 1.5 1.7 1.7 0 001.8-.3l.1-.1a2 2 0 112.8 2.8l-.1.1a1.7 1.7 0 00-.3 1.8V9a1.7 1.7 0 001.5 1H21a2 2 0 110 4h-.1a1.7 1.7 0 00-1.5 1z"/></svg>'
};
function tabbar(h) {
  const t = (href, key, label, on) => `<a href="#${href}" class="${on ? 'act' : ''}">${ICONS[key]}${label}</a>`;
  return `<nav class="tabbar">${t('inicio', 'home', 'Inicio', !['nuevo', 'ajustes'].includes(h))}${t('nuevo', 'plus', 'Añadir', h === 'nuevo')}${t('ajustes', 'gear', 'Ajustes', h === 'ajustes')}</nav>`;
}
function connBadge() {
  const { kind, msg } = S.conn;
  if (kind === 'demo') return '<span class="badge demo">Modo demo</span>';
  if (kind === 'ok') return '<span class="badge ok">Conectado</span>';
  return `<span class="badge ${kind}" title="${esc(msg)}">${kind === 'err' ? 'Error' : 'Sin conexión'}</span>`;
}
const isIOS = /iphone|ipad|ipod/i.test(navigator.userAgent);
const standalone = () => window.matchMedia('(display-mode: standalone)').matches || navigator.standalone;
let deferredInstall = null;
window.addEventListener('beforeinstallprompt', e => { e.preventDefault(); deferredInstall = e; refresh(); });
function installBanner() {
  if (standalone() || store.load('hideInstall', false)) return '';
  if (isIOS) return `<div class="install"><span>📲 Para instalarla: pulsa <b>Compartir</b> y luego <b>«Añadir a pantalla de inicio»</b>.</span><button data-act="hideinstall" aria-label="Cerrar">×</button></div>`;
  if (deferredInstall) return `<div class="install"><span>📲 Instala la app en tu móvil.</span><button class="btn small" style="margin-left:auto" data-act="install">Instalar</button><button data-act="hideinstall" style="margin-left:0" aria-label="Cerrar">×</button></div>`;
  return '';
}

function vHome() {
  const on = S.devices.filter(d => isOnline(d.id)).length;
  const cards = S.devices.map(d => {
    const o = isOnline(d.id), st = S.status[d.id];
    return `<a class="card ${o ? 'on' : ''}" href="#d-${esc(d.id)}">
      <div class="ico">🗣️</div>
      <div class="meta"><div class="name">${esc(d.name)}</div><div class="small">${o ? 'Última señal ' + ago(st.ts) : 'Visto ' + ago(st && st.ts)}</div></div>
      <span class="pill ${o ? 'on' : 'off'}"><i class="dot"></i>${o ? 'Activo' : 'Inactivo'}</span></a>`;
  }).join('');
  const empty = `<div class="empty"><div class="big">🔌</div><h2 style="margin:6px 0">Aún no tienes dispositivos</h2>
    <p>Enchufa la Iaia, y añádela con el código de su etiqueta o por Bluetooth.</p>
    <a class="btn" href="#nuevo">Añadir mi primera Iaia</a></div>`;
  return `<header class="top"><div><h1>Iaia</h1><p class="sub">${S.devices.length ? `${on} de ${S.devices.length} activos` : 'Control remoto'}</p></div>${connBadge()}</header>
    ${installBanner()}${S.devices.length ? cards : empty}`;
}

function stateLabel(st) {
  return { sending: ['Enviando…', 'wait'], sent: ['Enviado', 'wait'], delivered: ['Recibido', 'wait'], said: ['✓ Dicho', 'ok'], failed: ['✗ Falló', 'fail'] }[st] || ['', ''];
}
function logHtml(id) {
  const l = (S.logs[id] || []).slice(0, 12);
  if (!l.length) return '<p class="hint">Todavía no has enviado mensajes.</p>';
  return `<ul class="log">${l.map(m => { const [lab, cls] = stateLabel(m.state);
    return `<li><div><div>${esc(m.text)}</div><div class="t">${hhmm(m.t)}${m.err ? ' · ' + esc(m.err) : ''}</div></div><span class="st ${cls}">${lab}</span></li>`; }).join('')}</ul>`;
}
function statusHtml(d) {
  const o = isOnline(d.id), st = S.status[d.id];
  return `<span class="pill ${o ? 'on' : 'off'}"><i class="dot"></i>${o ? 'Activo' : 'Inactivo'}</span><div class="small sub" style="text-align:right;margin-top:6px">${o ? 'Señal ' + ago(st.ts) : 'Visto ' + ago(st && st.ts)}</div>`;
}
function vDev(d) {
  const o = isOnline(d.id);
  return `<a class="back" href="#inicio">‹ Mis dispositivos</a>
    <div class="panel hero"><div><div class="name" id="dname">${esc(d.name)}</div><div class="sub">${esc(d.id)}</div></div><div id="dst">${statusHtml(d)}</div></div>
    <div class="panel"><label for="msg" style="margin-top:0">Mensaje para que lo diga en voz alta</label>
      <textarea id="msg" maxlength="300">Hello World</textarea>
      <div class="row"><span class="hint" style="margin:0">Lo dirá con la voz de Iaia, tal cual lo escribas. Mejor en catalán.</span><span class="count" id="cnt">11/300</span></div>
      <div class="chips">${PRESETS.map(p => `<button class="chip" data-act="preset" data-t="${esc(p)}">${esc(p)}</button>`).join('')}</div>
      <button class="btn block" id="send" data-act="send" ${o ? '' : 'disabled'}>🔊 Enviar y que lo diga</button>
      <p class="hint" id="sendhint">${o ? '' : 'Inactiva: no se puede enviar hasta que vuelva a conectarse.'}</p></div>
    <h2>Mensajes recientes</h2><div class="panel" id="log">${logHtml(d.id)}</div>
    <h2>Dispositivo</h2><div class="panel">
      <label for="rn" style="margin-top:0">Nombre</label>
      <div class="row"><input type="text" id="rn" value="${esc(d.name)}" maxlength="30"><button class="btn small ghost" data-act="rename">Guardar</button></div>
      <div style="margin-top:14px"><button class="btn small danger" data-act="del">Eliminar dispositivo</button></div></div>`;
}

function vAdd() {
  const a = S.add;
  const bar = `<div class="steps">${[1, 2, 3].map(i => `<i class="${i <= a.step ? 'on' : ''}"></i>`).join('')}</div>`;
  const head = `<header class="top"><div><h1>Añadir Iaia</h1><p class="sub">Paso ${a.step} de 3</p></div>${connBadge()}</header>${bar}`;
  const err = a.err ? `<div class="note err">${esc(a.err)}</div>` : '';
  if (a.step === 1) {
    const bt = btSupported() ? 'Disponible en este navegador.' : (S.settings.mode === 'demo' ? 'Aquí se simula. En iPhone no existe Bluetooth web.' : 'No disponible en este navegador (iPhone/Safari no lo permite).');
    return head + `<p class="sub" style="margin-bottom:14px">Enchufa la Iaia a la corriente y espera a que diga que está lista.</p>
      <button class="opt" data-act="method" data-m="code"><span class="ico">🏷️</span><div><b>Con el código de la etiqueta</b><span>Funciona en iPhone y Android. Escribe el número de serie IAIA-XXXX-XXXX.</span></div></button>
      <button class="opt" data-act="method" data-m="bt"><span class="ico">📡</span><div><b>Con Bluetooth</b><span>${bt}</span></div></button>`;
  }
  if (a.step === 2 && a.method === 'code') {
    return head + `<div class="panel"><label for="code" style="margin-top:0">Número de serie</label>
      <input type="text" id="code" class="code" value="${esc(a.code)}" placeholder="IAIA-XXXX-XXXX" inputmode="text" autocomplete="off" autocapitalize="characters" spellcheck="false">
      <p class="hint">Está en la etiqueta de la base de la Iaia.</p>${err}
      ${S.settings.mode === 'demo' ? '<button class="chip" data-act="democode">Usar código de prueba</button>' : ''}</div>
      <button class="btn block" id="pairbtn" data-act="pair" ${CODE_RE.test(a.code) && !a.busy ? '' : 'disabled'}>${a.busy ? 'Comprobando…' : 'Continuar'}</button>
      <p style="text-align:center;margin-top:12px"><button class="chip" data-act="back1">‹ Cambiar método</button></p>`;
  }
  if (a.step === 2 && a.method === 'bt') {
    return head + `<div class="panel" style="text-align:center">${a.busy ? '<div class="radar">📡</div><b>Buscando Iaia cerca…</b><p class="hint">Acerca el móvil a la Iaia.</p>' : '<div class="radar" style="animation:none">📡</div><p class="hint">Busca dispositivos «IAIA-…» por Bluetooth.</p>'}${err}</div>
      <button class="btn block" data-act="scan" ${a.busy ? 'disabled' : ''}>${a.busy ? 'Buscando…' : 'Buscar por Bluetooth'}</button>
      <p style="text-align:center;margin-top:12px"><button class="chip" data-act="back1">‹ Cambiar método</button></p>`;
  }
  return head + `<div class="note ok">✓ Dispositivo encontrado: <b>${esc(a.code)}</b></div>
    <div class="panel"><label for="nm" style="margin-top:0">¿Cómo la llamamos?</label>
    <input type="text" id="nm" value="${esc(a.name)}" maxlength="30" placeholder="Iaia de la abuela"></div>
    <button class="btn block" data-act="save">Añadir a mi lista</button>`;
}

function vSet() {
  const s = S.settings, demo = s.mode === 'demo';
  const sim = demo ? `<h2>Simulador</h2><div class="panel"><p class="hint" style="margin:0 0 6px">Apaga una Iaia simulada para ver el estado «Inactivo».</p>
    ${S.devices.length ? S.devices.map(d => `<div class="sw"><span>${esc(d.name)}</span><input type="checkbox" class="tog" data-act="power" data-id="${esc(d.id)}" ${S.tr.isOn(d.id) ? 'checked' : ''}></div>`).join('') : '<p class="hint">Añade un dispositivo primero.</p>'}
    <div class="sw"><span>Voz en el móvil al enviar</span><input type="checkbox" class="tog" data-act="sound" ${s.sound ? 'checked' : ''}></div></div>` : '';
  const real = demo ? '' : `<div class="panel"><label for="url" style="margin-top:0">Broker MQTT (WebSocket seguro)</label>
      <input type="url" id="url" value="${esc(s.url)}" placeholder="wss://broker.ejemplo.com:8884/mqtt" autocapitalize="off">
      <label for="usr">Usuario</label><input type="text" id="usr" value="${esc(s.user)}" autocapitalize="off" autocomplete="off">
      <label for="pw">Contraseña</label><input type="password" id="pw" value="${esc(s.pass)}" autocomplete="off">
      <label for="pfx">Prefijo de temas</label><input type="text" id="pfx" value="${esc(s.prefix)}" autocapitalize="off">
      <p class="hint">Se guardan solo en este móvil. Usa un usuario con permisos limitados a tus temas.</p>
      <div style="margin-top:12px"><button class="btn small" data-act="saveconn">Guardar y conectar</button></div></div>`;
  return `<header class="top"><div><h1>Ajustes</h1><p class="sub">Versión 0.1 (prototipo)</p></div>${connBadge()}</header>
    <div class="seg"><button data-act="mode" data-m="demo" class="${demo ? 'act' : ''}">Demo</button><button data-act="mode" data-m="mqtt" class="${demo ? '' : 'act'}">Conexión real</button></div>
    <div style="height:14px"></div>${real}${sim}
    <h2>Datos</h2><div class="panel"><button class="btn small danger" data-act="reset">Borrar todo en este móvil</button></div>`;
}

/* ---------- render ---------- */
const app = $('#app');
const hash = () => location.hash.slice(1) || 'inicio';
function render(keep) {
  const h = hash(); let html;
  if (h === 'nuevo') html = vAdd();
  else if (h === 'ajustes') html = vSet();
  else if (h.startsWith('d-') && dev(h.slice(2))) html = vDev(dev(h.slice(2)));
  else html = vHome();
  app.innerHTML = html + tabbar(h);
  if (!keep) window.scrollTo(0, 0);
}
function refresh() {   // actualizaciones en vivo sin tocar lo que el usuario está escribiendo
  const h = hash();
  if (h === 'nuevo') { const b = $('.top .badge'); if (b) b.outerHTML = connBadge(); return; }
  if (h === 'ajustes') { const b = $('.top .badge'); if (b) b.outerHTML = connBadge(); return; }
  if (h.startsWith('d-') && dev(h.slice(2))) {
    const d = dev(h.slice(2)), o = isOnline(d.id);
    const st = $('#dst'); if (st) st.innerHTML = statusHtml(d);
    const lg = $('#log'); if (lg) lg.innerHTML = logHtml(d.id);
    const sb = $('#send'); if (sb && !sb.dataset.busy) sb.disabled = !o;
    const hint = $('#sendhint'); if (hint) hint.textContent = o ? '' : 'Inactiva: no se puede enviar hasta que vuelva a conectarse.';
    return;
  }
  render(true);
}

/* ---------- acciones ---------- */
function addLog(id, e) { (S.logs[id] = S.logs[id] || []).unshift(e); S.logs[id] = S.logs[id].slice(0, 30); saveLogs(); }
async function doSend(id) {
  const ta = $('#msg'), text = ta.value.trim();
  if (!text) return toast('Escribe un mensaje primero.');
  const e = { mid: uid(), text, t: Date.now(), state: 'sending' }; addLog(id, e); refresh();
  const sb = $('#send'); sb.disabled = true; sb.dataset.busy = '1';
  try {
    await S.tr.send(id, text, st => { e.state = st; saveLogs(); refresh(); });
    e.state = 'said'; toast('La Iaia ha hablado ✓');
  } catch (err) { e.state = 'failed'; e.err = err.message; toast(err.message); }
  saveLogs(); delete sb.dataset.busy; refresh();
}
function finishPair(id) {
  S.add.code = id; S.add.step = 3; S.add.busy = false; S.add.err = '';
  render();
}
const actions = {
  hideinstall() { store.save('hideInstall', true); render(); },
  async install() { if (deferredInstall) { deferredInstall.prompt(); deferredInstall = null; render(); } },
  preset(el) { const ta = $('#msg'); ta.value = el.dataset.t; $('#cnt').textContent = ta.value.length + '/300'; },
  send() { doSend(hash().slice(2)); },
  rename() {
    const id = hash().slice(2), v = $('#rn').value.trim(); if (!v) return toast('El nombre no puede estar vacío.');
    dev(id).name = v; saveDevices(); $('#dname').textContent = v; toast('Nombre guardado');
  },
  del(el) {
    if (!el.dataset.sure) { el.dataset.sure = 1; el.textContent = 'Pulsa otra vez para confirmar'; return; }
    const id = hash().slice(2); S.tr.unwatch(id);
    S.devices = S.devices.filter(d => d.id !== id); delete S.logs[id]; saveDevices(); saveLogs();
    location.hash = 'inicio'; toast('Dispositivo eliminado');
  },
  method(el) { S.add = Object.assign(S.add, { method: el.dataset.m, step: 2, err: '', code: '', busy: false }); render(); },
  back1() { S.add.step = 1; S.add.err = ''; render(); },
  democode() { S.add.code = 'IAIA-DEMO-0001'; render(); },
  async pair() {
    S.add.busy = true; S.add.err = ''; render();
    try { const r = await S.tr.pair(S.add.code); if (dev(r.id)) throw new Error('Ese dispositivo ya está en tu lista.'); finishPair(r.id); }
    catch (e) { S.add.busy = false; S.add.err = e.message; render(); }
  },
  async scan() {
    S.add.busy = true; S.add.err = ''; render();
    try {
      const r = await scanBT();
      if (CODE_RE.test(r.code)) { const p = await S.tr.pair(r.code); if (dev(p.id)) throw new Error('Ese dispositivo ya está en tu lista.'); finishPair(p.id); }
      else { S.add.method = 'code'; S.add.busy = false; S.add.err = `Encontrada «${r.name}», pero falta confirmar: escribe el código de la etiqueta.`; render(); }
    } catch (e) { S.add.busy = false; S.add.err = e.name === 'NotFoundError' ? 'No se seleccionó ningún dispositivo.' : e.message; render(); }
  },
  save() {
    const name = ($('#nm').value || '').trim() || 'Iaia', id = S.add.code;
    S.devices.push({ id, name }); saveDevices(); S.tr.watch(id);
    S.add = { step: 1, method: null, code: '', name: 'Iaia', busy: false, err: '', found: '' };
    location.hash = 'd-' + id; toast('Dispositivo añadido ✓');
  },
  mode(el) { S.settings.mode = el.dataset.m; store.save('settings', S.settings); useTransport(); render(); },
  saveconn() {
    Object.assign(S.settings, { url: $('#url').value.trim(), user: $('#usr').value.trim(), pass: $('#pw').value, prefix: $('#pfx').value.trim() || 'iaia' });
    store.save('settings', S.settings); useTransport(); toast('Conectando…');
  },
  power(el) { S.tr.setPower(el.dataset.id, el.checked); toast(el.checked ? 'Iaia encendida' : 'Iaia apagada'); },
  sound(el) { S.settings.sound = el.checked; store.save('settings', S.settings); },
  reset(el) {
    if (!el.dataset.sure) { el.dataset.sure = 1; el.textContent = 'Pulsa otra vez para confirmar'; return; }
    try { Object.keys(localStorage).filter(k => k.startsWith('iaia.')).forEach(k => localStorage.removeItem(k)); } catch { }
    location.hash = 'inicio'; location.reload();
  }
};
app.addEventListener('click', e => { const el = e.target.closest('[data-act]'); if (el && el.type !== 'checkbox' && actions[el.dataset.act]) actions[el.dataset.act](el); });
app.addEventListener('change', e => { const el = e.target.closest('[data-act]'); if (el && el.type === 'checkbox' && actions[el.dataset.act]) actions[el.dataset.act](el); });
app.addEventListener('input', e => {
  if (e.target.id === 'msg') $('#cnt').textContent = e.target.value.length + '/300';
  if (e.target.id === 'code') {
    const raw = e.target.value.toUpperCase().replace(/[^A-Z0-9]/g, '').slice(0, 12);
    const f = [raw.slice(0, 4), raw.slice(4, 8), raw.slice(8, 12)].filter(Boolean).join('-');
    e.target.value = f; S.add.code = f; S.add.err = '';
    const b = $('#pairbtn'); if (b) b.disabled = !CODE_RE.test(f);
  }
});
window.addEventListener('hashchange', () => render());

/* ---------- arranque ---------- */
useTransport();
render();
setInterval(() => { if (hash() !== 'nuevo' && hash() !== 'ajustes') refresh(); }, 10000);
if ('serviceWorker' in navigator && location.protocol.startsWith('http')) {
  navigator.serviceWorker.register('sw.js').catch(() => { /* en entornos sin SW (vista previa) la app funciona igual */ });
}
