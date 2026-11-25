"""Analizador de fecha, hora y motivo de un recordatorio dicho en catalán.

Funciones puras (sin OVOS, sin red) para poder probarlas con texto:
  extract(text)              -> Spec  (qué ha dicho la persona: fecha, hora, parte del día, motivo)
  resolve(spec, now)         -> Result (datetime final, o qué falta / qué es ambiguo / si es pasado)
  quan_text(dt, now)         -> «demà a les deu del matí» (para hablar)
  reason_second_person(txt)  -> «tinc visita» -> «tens visita»

Todas las comparaciones de texto se hacen sobre una copia «normalizada» (minúsculas, sin acentos) que conserva
exactamente la misma longitud que el original: así las posiciones de los trozos reconocidos sirven para recortarlos
del texto original y dejar solo el motivo.
"""
import datetime as dt
import re
import unicodedata
from dataclasses import dataclass, field
from typing import Optional

MONTHS = ["gener", "febrer", "marc", "abril", "maig", "juny", "juliol", "agost", "setembre", "octubre", "novembre", "desembre"]
MONTHS_SPOKEN = ["gener", "febrer", "març", "abril", "maig", "juny", "juliol", "agost", "setembre", "octubre", "novembre", "desembre"]
WEEKDAYS = ["dilluns", "dimarts", "dimecres", "dijous", "divendres", "dissabte", "diumenge"]   # lunes=0
ONES = ["", "un", "dos", "tres", "quatre", "cinc", "sis", "set", "vuit", "nou", "deu", "onze", "dotze", "tretze",
        "catorze", "quinze", "setze", "disset", "divuit", "dinou"]
TENS = {20: "vint", 30: "trenta", 40: "quaranta", 50: "cinquanta"}

GRACE_S = 20          # un recordatorio para «ara mateix» no se considera pasado hasta pasados unos segundos


def norm(s):
    """minúsculas y sin acentos, misma longitud que el original"""
    out = []
    for c in (s or "").lower().replace("’", "'").replace("`", "'"):
        d = unicodedata.normalize("NFD", c)
        out.append(d[0] if d else c)
    return "".join(out)


# ---------- números ----------
_W = {"un": 1, "una": 1, "dos": 2, "dues": 2, "tres": 3, "quatre": 4, "cinc": 5, "sis": 6, "set": 7, "vuit": 8, "nou": 9,
      "deu": 10, "onze": 11, "dotze": 12, "tretze": 13, "catorze": 14, "quinze": 15, "setze": 16, "disset": 17,
      "divuit": 18, "dinou": 19, "vint": 20, "trenta": 30, "quaranta": 40, "cinquanta": 50}
_UNITS = "un|una|dos|dues|tres|quatre|cinc|sis|set|vuit|nou"
NUMW = (r"(?:vint(?:[- ]i[- ](?:%s))?|(?:trenta|quaranta|cinquanta)(?:[- ](?:%s))?|dinou|divuit|disset|setze|quinze|"
        r"catorze|tretze|dotze|onze|deu|nou|vuit|set|sis|cinc|quatre|tres|dues|dos|una|un)") % (_UNITS, _UNITS)
NUM = r"(?:\d{1,4}|%s)" % NUMW


def to_int(tok):
    tok = tok.strip()
    if tok.isdigit():
        return int(tok)
    parts = [p for p in re.split(r"[- ]", tok) if p and p != "i"]
    return sum(_W[p] for p in parts)


def num_words(n, feminine=False):
    """1..59 en lletra (per a la veu: Piper llegeix malament les xifres)"""
    if n == 1 and feminine:
        return "una"
    if n < 20:
        return ONES[n] or "zero"
    t, u = divmod(n, 10)
    if u == 0:
        return TENS[t * 10]
    return TENS[t * 10] + ("-i-" if t == 2 else "-") + ONES[u]


# ---------- especificación ----------
@dataclass
class Spec:
    date: Optional[dt.date] = None          # fecha concreta ya resuelta (hoy/demà/30 de novembre…)
    weekday: Optional[int] = None           # 0..6 si se dijo «divendres»
    weekday_next: bool = False              # «que ve», «vinent», «proper»
    day_only: Optional[int] = None          # «el dia 30» sin mes
    rel_days: Optional[int] = None          # avui=0, demà=1, demà passat=2
    explicit_dm: Optional[tuple] = None     # (dia, mes, año|None)
    delta: Optional[dt.timedelta] = None    # «d'aquí a cinc minuts»
    hour: Optional[int] = None              # 0..23 si es inequívoca, 1..12 si hay que decidir matí/tarda
    minute: int = 0
    hour_ambiguous: bool = False
    part: Optional[str] = None              # mati / tarda / nit / migdia / matinada
    reason: Optional[str] = None
    reason_prefix: str = ""                 # nada: reservado


@dataclass
class Result:
    when: Optional[dt.datetime] = None
    need: Optional[str] = None              # "data" | "hora" | "franja" (matí/tarda) | None
    past: bool = False
    options: list = field(default_factory=list)   # para «franja»: datetimes candidatos
    spec: Optional[Spec] = None


_TRIGGER = re.compile(
    r"^\s*(?:(?:si us plau|per favor|sisplau)[ ,]*)?"
    r"(?:recorda'?m|recorda'?ns|recorda-me|recordam|recordem|recordar-me|recorda(?:\s+que)?|"
    r"(?:posa|posa'm|posam|programa|programa'm|crea|crea'm|fes|fes-me|fesme|vull|vull programar|afegeix|afegeix-me)"
    r"(?:'m| me)?\s+(?:un|el)\s+recordatori)\b[ ,]*", re.I)

_PART_RE = re.compile(r"\b(?:del|de la|al|a la|pel|per la|d'aquest|d'aquesta|aquest|aquesta)\s+(mati|matinada|migdia|tarda|vesprada|vespre|nit)\b")
_PART_MAP = {"mati": "mati", "matinada": "matinada", "migdia": "migdia", "tarda": "tarda", "vesprada": "tarda", "vespre": "tarda", "nit": "nit"}


def _cut(s, spans):
    """trea de s els trossos (ini, fi) i deixa espais"""
    chars = list(s)
    for a, b in spans:
        for i in range(a, b):
            chars[i] = " "
    return "".join(chars)


def extract(text, bare_time=False):
    """Analiza una frase. bare_time=True: la frase es solo una hora («deu», «les deu del matí»), para las respuestas."""
    orig = text or ""
    n = norm(orig)
    spans = []
    sp = Spec()

    m = _TRIGGER.match(n)
    if m:
        spans.append((0, m.end()))

    def take(rx, flags=0):
        mm = re.search(rx, n, flags)
        if mm:
            spans.append(mm.span())
        return mm

    # --- relativo: d'aquí a ... ---
    mm = take(r"\b(?:(?:d')?(?:aqui|aci)\s+(?:a\s+)?|dins\s+de\s+|en\s+)(mitja\s+hora|un\s+quart\s+d'hora|(" + NUM + r")\s+(minuts?|hor(?:a|es)|dies?|setman(?:a|es))(?:\s+i\s+(mitja|quart))?)\b")
    if mm and (mm.group(0).startswith(("d'", "dins", "aqui", "aci")) or mm.group(3)):
        g = mm.group(1)
        if g.startswith("mitja"):
            sp.delta = dt.timedelta(minutes=30)
        elif g.startswith("un quart"):
            sp.delta = dt.timedelta(minutes=15)
        else:
            k, u = to_int(mm.group(2)), mm.group(3)
            if u.startswith("minut"):
                sp.delta = dt.timedelta(minutes=k)
            elif u.startswith("hor"):
                sp.delta = dt.timedelta(hours=k)
                if mm.group(4) == "mitja":
                    sp.delta += dt.timedelta(minutes=30)
                elif mm.group(4) == "quart":
                    sp.delta += dt.timedelta(minutes=15)
            elif u.startswith("di"):
                sp.delta = dt.timedelta(days=k)
            else:
                sp.delta = dt.timedelta(weeks=k)
    elif mm:
        spans.pop()   # «en» suelto dentro del motivo: no es un relativo

    if sp.delta is None:
        # --- fecha ---
        mm = take(r"\b(?:el\s+)?(?:dia\s+)?(" + NUM + r")\s+(?:de\s+|d')(" + "|".join(MONTHS) + r")(?:\s+(?:de|del)\s+(\d{4}))?\b")
        if mm:
            sp.explicit_dm = (to_int(mm.group(1)), MONTHS.index(mm.group(2)) + 1, int(mm.group(3)) if mm.group(3) else None)
        else:
            mm = take(r"\b(\d{1,2})[/-](\d{1,2})(?:[/-](\d{2,4}))?\b")
            if mm:
                y = int(mm.group(3)) if mm.group(3) else None
                if y is not None and y < 100:
                    y += 2000
                sp.explicit_dm = (int(mm.group(1)), int(mm.group(2)), y)
            else:
                mm = take(r"\b(?:passat\s+dema|dema\s+passat)\b")
                if mm:
                    sp.rel_days = 2
                else:
                    mm = take(r"\bdema\b")
                    if mm:
                        sp.rel_days = 1
                    else:
                        mm = take(r"\bavui\b")
                        if mm:
                            sp.rel_days = 0
                        else:
                            mm = take(r"\b(?:el\s+)?(?:proper\s+)?(" + "|".join(WEEKDAYS) + r")(?:\s+(que\s+ve|vinent|proper))?\b")
                            if mm:
                                sp.weekday = WEEKDAYS.index(mm.group(1))
                                sp.weekday_next = bool(mm.group(2)) or "proper" in mm.group(0)
                            else:
                                mm = take(r"\b(?:el\s+)?dia\s+(" + NUM + r")\b")
                                if mm:
                                    sp.day_only = to_int(mm.group(1))
        if sp.explicit_dm is None and re.search(r"\baquest[a]?\s+(?:mati|tarda|vesprada|vespre|nit)\b", n) and sp.rel_days is None \
                and sp.weekday is None and sp.day_only is None:
            sp.rel_days = 0

        # --- parte del día ---
        mp = _PART_RE.search(n)
        if mp:
            sp.part = _PART_MAP[mp.group(1)]
            spans.append(mp.span())
        else:
            mp = re.search(r"\baquest[a]?\s+(mati|tarda|vesprada|vespre|nit)\b", n)
            if mp:
                sp.part = _PART_MAP[mp.group(1)]
                spans.append(mp.span())

        # --- hora ---
        H = r"(" + NUM + r")"
        MIN = r"(" + NUM + r"|quart|mitja)"
        done = False
        mm = take(r"\b(?:a\s+|cap\s+a\s+|sobre\s+)?(?:les\s+|la\s+|l')?(un|dos|tres)\s+quarts?\s+(?:de\s+|d')(?:les\s+|la\s+)?" + H + r"\b")
        if mm:
            q, h = to_int(mm.group(1)), to_int(mm.group(2))
            sp.hour, sp.minute = (h - 1) % 12 or 12, 15 * q          # «dos quarts de cinc» = 4:30
            if h == 1:
                sp.hour = 12
            done = True
        if not done:
            mm = take(r"\b(?:a\s+|cap\s+a\s+|sobre\s+)?(?:les\s+|la\s+|l')" + H + r"(?:\s*[:.h]\s*(\d{2})|\s+(i|menys)\s+" + MIN + r")?\b")
            if mm:
                h = to_int(mm.group(1))
                mi = 0
                if mm.group(2):
                    mi = int(mm.group(2))
                elif mm.group(3):
                    w = mm.group(4)
                    k = 15 if w == "quart" else 30 if w == "mitja" else to_int(w)
                    if mm.group(3) == "menys":
                        h, mi = (h - 1) or 12, 60 - k
                    else:
                        mi = k
                sp.hour, sp.minute = h, mi
                done = True
        if not done:
            mm = take(r"\b(\d{1,2})\s*[:.h]\s*(\d{2})\b")
            if mm:
                sp.hour, sp.minute = int(mm.group(1)), int(mm.group(2))
                done = True
        if not done:
            mm = take(r"\b(?:a|al)\s+migdia\b")
            if mm:
                sp.hour, sp.minute = 12, 0
                done = True
            else:
                mm = take(r"\b(?:a|a la)\s+mitjanit\b")
                if mm:
                    sp.hour, sp.minute = 0, 0
                    done = True
        if not done and bare_time:
            mm = re.fullmatch(r"\s*(?:a\s+|cap\s+a\s+)?(?:les\s+|la\s+)?" + H + r"(?:\s+i\s+(quart|mitja|" + NUM + r"))?\s*[.!]?\s*", re.sub(_PART_RE, " ", n))
            if mm:
                h = to_int(mm.group(1))
                mi = 0
                if mm.group(2):
                    w = mm.group(2)
                    mi = 15 if w == "quart" else 30 if w == "mitja" else to_int(w)
                sp.hour, sp.minute = h, mi
                done = True
        if done and sp.hour is not None:
            h = sp.hour
            if h == 24:
                sp.hour = 0
            if sp.part and 1 <= sp.hour <= 12 and not (sp.part == "migdia"):
                p = sp.part
                if p in ("tarda",) and sp.hour < 12:
                    sp.hour += 12
                elif p == "nit":
                    sp.hour = sp.hour + 12 if 6 <= sp.hour <= 11 else (0 if sp.hour == 12 else sp.hour)
                elif p == "matinada":
                    sp.hour = 0 if sp.hour == 12 else sp.hour
            elif sp.part == "migdia" and 1 <= sp.hour <= 12:
                sp.hour = 12 if sp.hour in (11, 12) else sp.hour + 12 if sp.hour <= 4 else sp.hour
            elif 1 <= sp.hour <= 11:
                sp.hour_ambiguous = True            # «a les cinc»: ¿del matí o de la tarda?

    # --- motivo ---
    rest = _cut(orig, spans)
    rest = re.sub(r"\s+", " ", rest).strip(" ,.;:!?-")
    for _ in range(3):
        rest = re.sub(r"^(?:que|de|per a|per|sobre)\s+", "", rest, flags=re.I).strip(" ,.;:!?-")
    rest = re.sub(r"\s+(?:que|de|per a|per|a|el|la|les)$", "", rest, flags=re.I).strip(" ,.;:!?-")
    if len(rest) < 3 or re.fullmatch(r"(?:un\s+)?recordatori", norm(rest)):
        rest = None
    sp.reason = rest
    return sp


def _next_weekday(today, wd, force_next):
    d = today + dt.timedelta(days=(wd - today.weekday()) % 7)
    if force_next and d == today:
        d += dt.timedelta(days=7)
    return d


def resolve_date(sp, now):
    """-> (date | None, past: bool)"""
    today = now.date()
    if sp.rel_days is not None:
        return today + dt.timedelta(days=sp.rel_days), False
    if sp.weekday is not None:
        return _next_weekday(today, sp.weekday, sp.weekday_next), False
    if sp.explicit_dm:
        d, mth, y = sp.explicit_dm
        try:
            if y:
                cand = dt.date(y, mth, d)
                return cand, cand < today
            cand = dt.date(today.year, mth, d)
            if cand < today:
                if (today - cand).days <= 60:        # «el 20 de novembre» dicho el 25: es un error, no el año que ve
                    return cand, True
                cand = dt.date(today.year + 1, mth, d)
            return cand, False
        except ValueError:
            return None, True
    if sp.day_only:
        y, mth = today.year, today.month
        for _ in range(2):
            try:
                cand = dt.date(y, mth, sp.day_only)
                if cand >= today:
                    return cand, False
            except ValueError:
                pass
            mth += 1
            if mth > 12:
                y, mth = y + 1, 1
        return None, True
    return None, False


def resolve(sp, now, tz=None):
    """Convierte la especificación en una fecha y hora concretas, o dice qué falta.
    now debe llevar zona horaria (aware)."""
    res = Result(spec=sp)
    if sp.delta is not None:
        res.when = (now + sp.delta).replace(second=0, microsecond=0)
        if sp.delta < dt.timedelta(minutes=1):
            res.past = True
        return res
    d, past = resolve_date(sp, now)
    if past:
        res.past = True
        return res
    if d is None and sp.hour is None:
        res.need = "data"
        return res
    if d is None:
        res.need = "data"
        return res
    if sp.hour is None:
        res.need = "hora"
        return res
    cands = []
    hrs = [sp.hour] if not sp.hour_ambiguous else [sp.hour, sp.hour + 12]
    for h in hrs:
        if not (0 <= h <= 23 and 0 <= sp.minute <= 59):
            res.past = True
            return res
        cands.append(now.replace(year=d.year, month=d.month, day=d.day, hour=h, minute=sp.minute, second=0, microsecond=0))
    future = [c for c in cands if c > now - dt.timedelta(seconds=GRACE_S)]
    if not future:
        res.past = True
    elif len(future) == 1:
        res.when = future[0]
    else:
        res.need = "franja"
        res.options = future
    return res


# ---------- cómo se dice ----------
def _hour_name(h12, feminine_article=True):
    return "una" if h12 == 1 else "dues" if h12 == 2 else num_words(h12)


def _article(h12):
    return "la" if h12 == 1 else "les"


def _part_of(h, m):
    if h == 12 and m == 0:
        return "del migdia"
    if 0 <= h <= 0:
        return "de la nit"
    if 1 <= h <= 5:
        return "de la matinada"
    if 6 <= h <= 11:
        return "del matí"
    if h == 12:
        return "del migdia"
    if 13 <= h <= 20:
        return "de la tarda"
    return "de la nit"


def time_text(h, m, simple_part=False):
    """«les deu del matí», «dos quarts de cinc de la tarda»"""
    part = _part_of(h, m)
    if simple_part and part == "de la matinada":
        part = "del matí"
    h12 = h % 12 or 12
    nxt = (h + 1) % 12 or 12
    if m in (15, 30, 45):
        q = {15: "un quart", 30: "dos quarts", 45: "tres quarts"}[m]
        de = "d'" if nxt in (1, 11) else "de "
        base = "%s %s%s" % (q, de, _hour_name(nxt))
    elif m == 0:
        base = "%s %s" % (_article(h12), _hour_name(h12))
        if h == 12:
            return "les dotze del migdia"
        if h == 0:
            return "les dotze de la nit"
    elif m >= 40:
        base = "%s %s menys %s" % (_article(nxt), _hour_name(nxt), num_words(60 - m))
    else:
        base = "%s %s i %s" % (_article(h12), _hour_name(h12), num_words(m))
    return "%s %s" % (base, part)


def day_text(d, today, prefer_weekday=False, force_date=False):
    diff = (d - today).days
    if prefer_weekday and 2 <= diff <= 7:
        return WEEKDAYS[d.weekday()]
    if force_date and diff >= 2:
        diff = 99                      # «el 30 de novembre» es diu amb la data, no amb «dilluns»
    if diff == 0:
        return "avui"
    if diff == 1:
        return "demà"
    if diff == 2:
        return "demà passat"
    if 3 <= diff <= 6:
        return WEEKDAYS[d.weekday()]
    mon = MONTHS_SPOKEN[d.month - 1]
    de = "d'" if mon[0] in "aeiou" else "de "
    dia = "u" if d.day == 1 else num_words(d.day)
    s = "el %s %s%s" % (dia, de, mon)
    if d.year != today.year:
        s += " de %d" % d.year
    return s


def delta_text(delta):
    mins = int(round(delta.total_seconds() / 60))
    if mins < 60:
        return "d'aquí a %s minut%s" % (num_words(mins), "" if mins == 1 else "s")
    if mins % 60 == 0 and mins < 24 * 60:
        h = mins // 60
        return "d'aquí a %s %s" % (num_words(h, True), "hora" if h == 1 else "hores")
    return None


def quan_text(dtm, now, delta=None, weekday=False, force_date=False):
    if delta is not None and delta < dt.timedelta(hours=3):
        t = delta_text(delta)
        if t:
            return t
    return "%s a %s" % (day_text(dtm.date(), now.date(), weekday, force_date), time_text(dtm.hour, dtm.minute, True))


# ---------- persona: primera -> segunda ----------
_SWAP = [
    (r"\bm'he de\b", "t'has de"), (r"\bhe de\b", "has de"), (r"\bhaig de\b", "has de"), (r"\bhe d'", "has d'"),
    (r"\btinc que\b", "tens que"), (r"\btinc\b", "tens"), (r"\bhaig\b", "has"), (r"\bvull\b", "vols"), (r"\bpuc\b", "pots"),
    (r"\bvaig\b", "vas"), (r"\bestic\b", "estàs"), (r"\bsóc\b", "ets"), (r"\bsoc\b", "ets"), (r"\bfaig\b", "fas"),
    (r"\bhe\b", "has"), (r"\bsé\b", "saps"), (r"\bveig\b", "veus"), (r"\bdono\b", "dónes"),
    (r"\bem\b", "et"), (r"\bm'", "t'"), (r"\bmeu\b", "teu"), (r"\bmeva\b", "teva"), (r"\bmeus\b", "teus"), (r"\bmeves\b", "teves"),
    (r"\bjo\b", "tu"), (r"\ba mi\b", "a tu"), (r"\bmi\b", "tu"),
]
_NOT_INF = {"mar", "bar", "pare", "mare", "llibre", "sempre", "aire", "carrer", "paper", "dimecres", "cor", "hora", "ara", "mirar",
            "germana", "sobre", "nadir", "cadira", "metre", "centre", "nombre", "tarda", "taula"}
_NOT_SUBJ = {"taxi", "cafi", "menjari", "dimecri", "pani", "ali", "alli", "aqui", "avui", "mati", "dilluni"}
_VERBS2 = ("tens", "has", "vols", "pots", "vas", "estàs", "ets", "fas", "saps", "veus", "dónes")


def reason_second_person(txt):
    s = (txt or "").strip()
    for rx, rep in _SWAP:
        def sub(m, rep=rep):
            w = m.group(0)
            return rep.capitalize() if w[:1].isupper() else rep
        s = re.sub(rx, sub, s, flags=re.I)
    return s


def reason_kind(txt2):
    """«verb» si ya empieza por un verbo en segunda persona, «inf» si empieza por «de + infinitivo», «nom» si no"""
    w = norm(txt2).split()
    if not w:
        return "nom"
    if w[0] in [norm(v) for v in _VERBS2]:
        return "verb"
    if w[0] == "de" and len(w) > 1 and re.search(r"(?:ar|er|re|ir|or)$", w[1]):
        return "inf"
    if re.search(r"(?:ar|er|re|ir)$", w[0]) and w[0] not in _NOT_INF:
        return "inf0"
    if len(w[0]) > 3 and w[0].endswith("i") and w[0] not in _NOT_SUBJ:
        return "subj"                      # «prengui», «truqui»: es parla a la persona (prenguis)
    if len(w[0]) > 4 and w[0].endswith("in"):
        return "subj3"                     # «vinguin els fills»
    return "nom"


def _subj2(txt):
    """«prengui la pastilla» -> «prenguis la pastilla»"""
    w = txt.split(" ", 1)
    return (w[0] + "s") + ((" " + w[1]) if len(w) > 1 else "")


def reminder_sentence(reason):
    """Frase para el aviso. Con motivo: «Recorda que avui tens visita al metge.»; sin motivo: la genérica."""
    if not reason:
        return "Tens un recordatori programat per ara."
    r2 = reason_second_person(reason)
    r2 = r2[0].lower() + r2[1:] if r2 else r2
    k = reason_kind(r2)
    if k == "verb" or k == "subj3":
        return "Recorda que avui %s." % r2
    if k == "subj":
        return "Recorda que avui %s." % _subj2(r2)
    if k == "inf":
        return "Recorda que avui has %s." % r2
    if k == "inf0":
        return "Recorda que avui has de %s." % r2
    return "Recorda %s." % r2


def confirm_sentence(quan, reason):
    if not reason:
        return "D'acord, tens un recordatori programat per %s." % quan
    r2 = reason_second_person(reason)
    r2 = r2[0].lower() + r2[1:] if r2 else r2
    k = reason_kind(r2)
    if k == "verb" or k == "subj3":
        return "D'acord, %s et recordaré que %s." % (quan, r2)
    if k == "subj":
        return "D'acord, %s et recordaré que %s." % (quan, _subj2(r2))
    if k == "inf":
        return "D'acord, %s et recordaré que has %s." % (quan, r2)
    if k == "inf0":
        return "D'acord, %s et recordaré que has de %s." % (quan, r2)
    return "D'acord, %s et recordaré %s." % (quan, r2)
