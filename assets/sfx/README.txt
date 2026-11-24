Sound effects, one folder per type: boom, explosion, fail, buzzer, win, whoosh, bonk, laugh.
The reviewer picks the type for each clip; a random file from that folder plays on the peak.

Fill them with:  python fetch_sfx.py
(Mixkit sounds, free for monetised YouTube with no attribution; plus CC0 Freesound
sounds if FREESOUND_API_KEY is set. Sources are listed in credits.json)

You can also drop your own .mp3/.wav files into any folder.
If a folder is empty, any other sound is used; if all are empty, a generated bass drop is used.
