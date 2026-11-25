import sys, datetime as dt, zoneinfo, importlib.util
spec = importlib.util.spec_from_file_location("parser", ""+__import__("os").path.join(__import__("os").path.dirname(__file__),"ovos_skill_iaia_recordatoris","parser.py")+"")
P = importlib.util.module_from_spec(spec); sys.modules["parser"] = P; spec.loader.exec_module(P)
tz = zoneinfo.ZoneInfo("Europe/Madrid")
now = dt.datetime(2026, 11, 25, 13, 30, tzinfo=tz)   # dimecres
fails = 0


def run(t, **k):
    sp = P.extract(t, **k); return sp, P.resolve(sp, now)


def chk(t, when=None, need=None, past=False, reason="__", **k):
    global fails
    sp, r = run(t, **k)
    ok = True
    if when is not None: ok &= (r.when is not None and r.when.strftime("%Y-%m-%d %H:%M") == when)
    if need is not None: ok &= r.need == need
    ok &= r.past == past
    if reason != "__": ok &= (sp.reason == reason)
    print("OK   " if ok else "FALLA", t, "->", r.when and r.when.strftime("%d/%m %H:%M"), r.need, r.past, repr(sp.reason))
    if not ok: fails += 1


chk("recorda'm demà a les deu del matí que tinc visita al metge", "2026-11-26 10:00", reason="tinc visita al metge")
chk("posa'm un recordatori divendres a les cinc de la tarda", "2026-11-27 17:00", reason=None)
chk("recorda'm el 30 de novembre a les nou del matí que he de trucar a la Maria", "2026-11-30 09:00", reason="he de trucar a la Maria")
chk("recorda'm d'aquí a cinc minuts que apagui el foc", "2026-11-25 13:35", reason="apagui el foc")
chk("posa'm un recordatori d'aquí a una hora", "2026-11-25 14:30", reason=None)
chk("recorda'm d'aquí a mitja hora que prengui la pastilla", "2026-11-25 14:00")
chk("recorda'm avui a les cinc de la tarda que truqui al fill", "2026-11-25 17:00")
chk("recorda'm avui a les cinc que truqui al fill", "2026-11-25 17:00")
chk("recorda'm avui a les set que he de sopar", "2026-11-25 19:00")
chk("recorda'm demà a les cinc que vagi al metge", need="franja")
chk("recorda'm demà que tinc metge", need="hora")
chk("recorda'm a les deu que tinc metge", need="data")
chk("recorda'm que he de trucar a la Maria", need="data")
chk("recorda'm demà a dos quarts de cinc de la tarda que tinc dentista", "2026-11-26 16:30")
chk("recorda'm demà a un quart de sis de la tarda", "2026-11-26 17:15")
chk("recorda'm demà a tres quarts de nou del matí", "2026-11-26 08:45")
chk("recorda'm demà a les vuit i mitja del vespre", "2026-11-26 20:30")
chk("recorda'm demà a les nou menys quart del matí", "2026-11-26 08:45")
chk("recorda'm demà a les 10:30 del matí", "2026-11-26 10:30")
chk("recorda'm demà a les 15:00", "2026-11-26 15:00")
chk("recorda'm demà a les deu i quart de la nit", "2026-11-26 22:15")
chk("recorda'm dilluns a les nou del matí", "2026-11-30 09:00")
chk("recorda'm el dilluns que ve a les nou del matí", "2026-11-30 09:00")
chk("recorda'm demà passat a les dotze", "2026-11-27 12:00")
chk("recorda'm demà a migdia que dino amb la Maria", "2026-11-26 12:00")
chk("recorda'm el 20 de novembre a les deu del matí", past=True)
chk("recorda'm el 3 de gener a les deu del matí", "2027-01-03 10:00")
chk("recorda'm el 31 de febrer a les deu del matí", past=True)
chk("recorda'm avui a les set del matí", past=True)
chk("recorda'm el dia 30 a les nou del matí", "2026-11-30 09:00")
chk("recorda'm aquesta tarda a les sis", "2026-11-25 18:00")
chk("recorda'm d'aquí a vint-i-cinc minuts", "2026-11-25 13:55")
chk("posa un recordatori demà a les deu del matí", "2026-11-26 10:00", reason=None)
chk("recorda'm el trenta de novembre a les nou del matí que he de trucar", "2026-11-30 09:00", reason="he de trucar")
chk("recorda'm demà a la una de la tarda", "2026-11-26 13:00")
chk("recorda'm demà a les tretze i trenta", "2026-11-26 13:30")
chk("recordem d'aquí dos minuts que t'inca veurà aigua", "2026-11-25 13:32", reason="t'inca veurà aigua")
chk("recordem que aquí 2 minuts que tinc aigua", "2026-11-25 13:32", reason="tinc aigua")
chk("recorda que avui a l'1.55 tinc anar-me al menjar", "2026-11-25 13:55")
chk("recordam que demà a les nou del matí tinc metge", "2026-11-26 09:00", reason="tinc metge")
chk("recorda demà a les nou del matí", "2026-11-26 09:00", reason=None)
chk("posa'm un recordatori divendres a les cinc de la tarda", "2026-11-27 17:00", reason=None)
chk("posam un recordatori divendres a les 5 de la tarda", "2026-11-27 17:00", reason=None)
chk("posa'm un recordatori divendres a les 17.00", "2026-11-27 17:00", reason=None)
chk("demà", need="hora")
chk("divendres", need="hora")
chk("a les deu del matí", need="data")
chk("deu", need="data", bare_time=True)
print("--- textos")
for t in ["recorda'm demà a les deu del matí que tinc visita al metge", "posa'm un recordatori divendres a les cinc de la tarda",
          "recorda'm el 30 de novembre a les nou del matí que he de trucar a la Maria",
          "recorda'm de prendre la pastilla demà a les vuit del matí", "recorda'm la medicació demà a les vuit del matí",
          "recorda'm demà a les vuit del matí que vinguin els meus fills"]:
    sp, r = run(t); print(P.confirm_sentence(P.quan_text(r.when, now, None, sp.weekday is not None, sp.explicit_dm is not None or sp.day_only is not None), sp.reason), " | ", P.reminder_sentence(sp.reason))
sp, r = run("recorda'm d'aquí a cinc minuts que prengui la pastilla"); print(P.confirm_sentence(P.quan_text(r.when, now, sp.delta), sp.reason))
for h, m in [(0, 0), (0, 30), (1, 5), (12, 0), (12, 15), (13, 0), (16, 30), (22, 45), (7, 50)]: print(h, m, P.time_text(h, m))
print("RESULTAT:", "OK" if not fails else f"{fails} FALLADES")
