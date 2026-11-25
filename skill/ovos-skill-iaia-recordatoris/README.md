# ovos-skill-iaia-recordatoris

Recordatoris per data i hora, dits per veu en català, a la Raspberry (sense MQTT, sense app, sense internet).

    «Yaya, recorda'm demà a les deu del matí que tinc visita al metge»
    «Yaya, posa'm un recordatori divendres a les cinc de la tarda»
    «Yaya, recorda'm d'aquí a mitja hora que prengui la pastilla»

## Com funciona
- `parser.py`: analitza dia (avui, demà, demà passat, dilluns…, «el 30 de novembre», «el dia 30», 30/11), hora («a les deu», «dos quarts de cinc», «i quart», «menys deu», 10:30, migdia), franja (matí/tarda/nit) i relatiu («d'aquí a cinc minuts / mitja hora / una hora / dos dies»). Tot el que sobra és el motiu (opcional; mai es pregunta).
- `__init__.py`: skill. Desa a `~/.local/share/iaia-recordatoris/recordatoris.json` (escriptura atòmica, 600). Un fil mira cada 2 s: marca com a `done` i desa ABANS de parlar (un sol avís), toca `res/avis.wav` amb `pw-play` (bloquejant, fins que acaba) i després `speak()`.
- Només pregunta el que falta: dia, hora, o «matí o tarda» si l'hora és ambigua (p. ex. «a les cinc» per a demà). Data passada: avisa i demana una altra; si no, no desa.
- Avís perdut (Pi apagada): si el retard és ≤ 2 h s'avisa en arrencar; si és més, queda `missed` i no es diu. Els `done/missed` s'esborren als 7 dies.
- Sense avisos anticipats (decisió del projecte).

## Per què no `ovos-skill-alerts`
No desa el motiu en català, no entén «recorda'm demà a les deu que…», confirma amb frases incorrectes i no permet so + frase pròpia. Als `alerts` només s'ha desactivat `locale/ca-ES/intent/create_reminder.intent` (còpia a `~/backups-recordatoris-20261125/`); temporitzadors i alarmes no s'han tocat.

## Proves
`python3 test_parser.py` (40 casos, sense OVOS) i proves a la Pi injectant frases pel bus. Instal·lació: `pip install --no-deps -e .` (no toca OVOS).
