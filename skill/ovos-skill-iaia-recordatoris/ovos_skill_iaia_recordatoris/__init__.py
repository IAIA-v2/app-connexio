"""Skill de Iaia: recordatoris per data i hora dits per veu en català.

  «Yaya, recorda'm demà a les deu del matí que tinc visita al metge»
  «Yaya, posa'm un recordatori divendres a les cinc de la tarda»

Per què una skill pròpia (i no ovos-skill-alerts)
  Comprovat a la Pi (2026-11-25): alerts no desa el motiu en català («recorda'm el 30 de novembre ... que he de trucar a
  la Maria» el deixa com a «recordatori»), no reconeix «recorda'm demà a les deu que ...» (cau al LLM), confirma amb
  frases incorrectes («L'entranda recordatori començarà les disset...») i no permet so + frase pròpia en el moment de
  l'avís. Els temporitzadors i alarmes d'alerts no es toquen (només se li desactiven les frases de «recordatori»).

Com funciona
  * Els recordatoris es desen en un JSON local (sobreviu a reinicis d'OVOS i de la Pi; no usa MQTT, ni l'app, ni internet).
  * Un fil dins la skill mira cada 2 s si n'hi ha algun vençut. Abans de parlar el marca com a «fet» i ho escriu al disc:
    així cada avís se sent UNA sola vegada, encara que es reiniciï la skill just en aquell moment.
  * En avisar: primer sona un so curt (pw-play, un procés que s'acaba abans que Piper comenci a parlar) i després
    Iaia ho diu amb la seva veu catalana.
  * Si la Pi estava apagada o la skill parada en el moment de l'avís, s'avisa igualment si el retard és de menys de
    MAX_RETARD_S (2 h); si és més, queda com a «perdut» i no es diu.
  * Només es pregunta el que falta (dia, hora, o matí/tarda si l'hora és ambigua). Mai es pregunta pel motiu.
  * Sense avisos anticipats (decisió del projecte).
"""
import datetime as dt
import json
import os
import subprocess
import tempfile
import threading
import uuid
from zoneinfo import ZoneInfo

from ovos_bus_client.message import Message
from ovos_utils.log import LOG
from ovos_workshop.decorators import intent_handler
from ovos_workshop.skills import OVOSSkill

from . import parser as P

TZ = ZoneInfo("Europe/Madrid")
DB_PATH = os.environ.get("IAIA_RECORDATORIS_DB") or os.path.expanduser("~/.local/share/iaia-recordatoris/recordatoris.json")
SOUND = os.path.join(os.path.dirname(__file__), "res", "avis.wav")
MAX_RETARD_S = 2 * 3600            # si l'avís arriba amb més retard (Pi apagada), es dóna per perdut
KEEP_DAYS = 7                      # els fets/perduts s'esborren del fitxer passats 7 dies
CHECK_S = 2
MAX_ASKS = 2                       # preguntes màximes per cada dada que falta


def now_local():
    return dt.datetime.now(TZ)


class IaiaRecordatorisSkill(OVOSSkill):
    def initialize(self):
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self.items = self._load()
        self._prune()
        self._thread = threading.Thread(target=self._loop, name="iaia-recordatoris", daemon=True)
        self._thread.start()
        LOG.info("iaia-recordatoris: %d pendents (%s)", len([i for i in self.items if i["state"] == "pending"]), DB_PATH)

    def shutdown(self):
        self._stop.set()
        super().shutdown()

    # ---------- disc ----------
    def _load(self):
        try:
            with open(DB_PATH, encoding="utf-8") as f:
                d = json.load(f)
            return d["items"] if isinstance(d, dict) else d
        except FileNotFoundError:
            return []
        except Exception as e:      # fitxer malmès: es conserva una còpia i es comença net
            LOG.error("iaia-recordatoris: no puc llegir %s (%s); en guardo una còpia", DB_PATH, e)
            try:
                os.replace(DB_PATH, DB_PATH + ".malmes-%d" % int(now_local().timestamp()))
            except Exception:
                pass
            return []

    def _save(self):
        os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=os.path.dirname(DB_PATH), prefix=".rec-")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump({"v": 1, "items": self.items}, f, ensure_ascii=False, indent=1)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, DB_PATH)      # escriptura atòmica: o queda el fitxer vell o el nou, mai a mitges
        os.chmod(DB_PATH, 0o600)

    def _prune(self):
        lim = now_local() - dt.timedelta(days=KEEP_DAYS)
        with self._lock:
            n = len(self.items)
            self.items = [i for i in self.items if i["state"] == "pending" or dt.datetime.fromisoformat(i["when"]) > lim]
            if len(self.items) != n:
                self._save()

    # ---------- vigilant d'avisos ----------
    def _loop(self):
        while not self._stop.wait(CHECK_S):
            try:
                self._check()
            except Exception:
                LOG.exception("iaia-recordatoris: error al vigilar els recordatoris")

    def _check(self):
        now = now_local()
        due = []
        with self._lock:
            changed = False
            for it in self.items:
                if it["state"] != "pending":
                    continue
                when = dt.datetime.fromisoformat(it["when"])
                if when > now:
                    continue
                if (now - when).total_seconds() > MAX_RETARD_S:
                    it["state"] = "missed"
                    LOG.warning("iaia-recordatoris: recordatori perdut (retard massa gran): %s", it["id"])
                else:
                    it["state"] = "done"           # primer es marca i es desa, després es parla: mai dos cops
                    it["fired_at"] = now.isoformat()
                    due.append(dict(it))
                changed = True
            if changed:
                self._save()
        for it in due:
            self._avisa(it)

    def _avisa(self, it):
        LOG.info("iaia-recordatoris: avís %s", it["id"])
        self._so()
        try:
            self.speak(P.reminder_sentence(it.get("reason")), wait=True)
        except Exception:
            LOG.exception("iaia-recordatoris: no he pogut dir l'avís %s", it["id"])

    def _so(self):
        """So curt, bloquejant: quan acaba el procés, Piper ja pot parlar sense trepitjar-lo."""
        for cmd in (["pw-play", SOUND], ["aplay", "-q", SOUND]):
            try:
                subprocess.run(cmd, timeout=10, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                return
            except Exception as e:
                LOG.warning("iaia-recordatoris: el so ha fallat amb %s: %s", cmd[0], e)

    # ---------- converses ----------
    def _ask(self, question, message):
        self.speak(question, wait=True)
        a = (self.get_response(num_retries=0, message=message) or "").strip()
        return a

    @staticmethod
    def _merge_date(sp, sp2):
        for a in ("rel_days", "weekday", "weekday_next", "explicit_dm", "day_only", "delta"):
            if getattr(sp2, a) is not None and getattr(sp2, a) is not False:
                setattr(sp, a, getattr(sp2, a))
        if sp.hour is None and sp2.hour is not None:
            sp.hour, sp.minute, sp.hour_ambiguous, sp.part = sp2.hour, sp2.minute, sp2.hour_ambiguous, sp2.part

    @staticmethod
    def _merge_time(sp, sp2):
        if sp2.hour is not None:
            sp.hour, sp.minute, sp.hour_ambiguous, sp.part = sp2.hour, sp2.minute, sp2.hour_ambiguous, sp2.part

    @intent_handler("recordatori.intent")
    def handle_recordatori(self, message):
        text = message.data.get("utterance") or ""
        if not text:
            utts = message.data.get("utterances") or []
            text = utts[0] if utts else ""
        sp = P.extract(text)
        now = now_local()
        res = P.resolve(sp, now)
        asks = {"data": 0, "hora": 0, "franja": 0, "passat": 0}
        while True:
            now = now_local()
            if res.past:
                if asks["passat"] >= 1:
                    self.speak("Aquesta data ja ha passat, així que no he guardat el recordatori.", wait=True)
                    return
                asks["passat"] += 1
                a = self._ask("Aquesta data o hora ja ha passat. Digues-me quan vols que t'ho recordi.", message)
                if not a:
                    self.speak("No t'he sentit, així que no he guardat el recordatori.", wait=True)
                    return
                sp2 = P.extract(a, bare_time=False)
                reason = sp.reason
                sp = sp2
                sp.reason = reason
            elif res.need == "data":
                if asks["data"] >= MAX_ASKS:
                    self.speak("No he entès el dia, així que no he guardat el recordatori.", wait=True)
                    return
                asks["data"] += 1
                a = self._ask("Per a quin dia vols el recordatori?", message)
                if not a:
                    self.speak("No t'he sentit, així que no he guardat el recordatori.", wait=True)
                    return
                self._merge_date(sp, P.extract(a, bare_time=True))
            elif res.need == "hora":
                if asks["hora"] >= MAX_ASKS:
                    self.speak("No he entès l'hora, així que no he guardat el recordatori.", wait=True)
                    return
                asks["hora"] += 1
                a = self._ask("A quina hora?", message)
                if not a:
                    self.speak("No t'he sentit, així que no he guardat el recordatori.", wait=True)
                    return
                self._merge_time(sp, P.extract(a, bare_time=True))
            elif res.need == "franja":
                if asks["franja"] >= MAX_ASKS:
                    self.speak("No he entès l'hora, així que no he guardat el recordatori.", wait=True)
                    return
                asks["franja"] += 1
                o1, o2 = res.options[0], res.options[1]
                a = self._ask("Vols dir %s o %s?" % (P.time_text(o1.hour, o1.minute, True), P.time_text(o2.hour, o2.minute, True)), message)
                na = P.norm(a)
                if any(w in na.split() for w in ("mati", "matinada")):
                    sp.hour, sp.hour_ambiguous = o1.hour, False
                elif any(w in na.split() for w in ("tarda", "vesprada", "vespre", "nit")):
                    sp.hour, sp.hour_ambiguous = o2.hour, False
                elif not a:
                    self.speak("No t'he sentit, així que no he guardat el recordatori.", wait=True)
                    return
            else:
                break
            res = P.resolve(sp, now_local())
        when = res.when
        item = {"id": uuid.uuid4().hex[:12], "when": when.isoformat(), "reason": sp.reason, "created": now_local().isoformat(),
                "state": "pending", "text": text}
        with self._lock:
            self.items.append(item)
            self._save()
        quan = P.quan_text(when, now_local(), sp.delta, weekday=sp.weekday is not None,
                           force_date=sp.explicit_dm is not None or sp.day_only is not None)
        LOG.info("iaia-recordatoris: nou %s per %s (%s)", item["id"], item["when"], sp.reason)
        self.speak(P.confirm_sentence(quan, sp.reason), wait=True)
