"""Download free meme/impact SFX into assets/sfx/<category>/.

Default source is a hand-picked list from Mixkit (free for commercial use incl.
monetised YouTube, no attribution needed, no API key). Optionally adds CC0 sounds
from Freesound when FREESOUND_API_KEY is set (in the environment or .env).

Every file is trimmed to 4 s, loudness-normalised to WAV, and recorded in
assets/sfx/credits.json.

    python fetch_sfx.py
"""
import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SFX = ROOT / "assets" / "sfx"
UA = "Mozilla/5.0 (GTA-Bloke clip editor)"
MIXKIT_URL = "https://assets.mixkit.co/active_storage/sfx/{id}/{id}-preview.mp3"

# category -> Mixkit sound ids (picked by title from mixkit.co/free-sound-effects/<tag>/)
# the reviewer chooses a category per clip, so keep these names in sync with reviewer.SfxChoice
MIXKIT = {
    "boom": {2299: "Short bass hit", 2303: "Futuristic bass hit", 772: "Quick zoom impact",
             2901: "Epic movie impact", 788: "Big cinematic impact"},
    "explosion": {1562: "Car explosion debris", 1616: "Truck crash with explosion", 1705: "Fuel explosion",
                  2800: "Bomb explosion in battle", 1694: "Short explosion"},
    "fail": {744: "Trombone disappoint", 471: "Sad game over trombone", 1940: "Game over trombone",
             527: "Cartoon sad party horn"},
    "buzzer": {948: "Wrong answer bass buzzer", 2876: "Funny fail low tone", 946: "Wrong answer fail notification"},
    "win": {722: "Successful horns fanfare", 2060: "Winning an extra bonus", 2059: "Game level completed"},
    "whoosh": {1492: "Cinematic whoosh fast transition", 1490: "Fast whoosh transition", 1489: "Air woosh",
               787: "Cinematic whoosh stutter"},
    "bonk": {2894: "Boing hit sound", 2895: "Metallic boing hit", 2149: "Cartoon punch",
             746: "Cartoon dazzle hit and birds", 2155: "Impact of a strong punch"},
    "laugh": {424: "Crowd laugh", 2882: "Cartoon laugh voice", 343: "Cartoon voice laugh"},
}
# optional Freesound (CC0 only) searches per category
FREESOUND = {
    "boom": ["vine boom"], "buzzer": ["bruh"], "win": ["air horn"], "whoosh": ["whoosh"],
    "fail": ["sad trombone"], "bonk": ["bonk"], "explosion": ["explosion"], "laugh": ["crowd laugh"],
}


def load_env():
    """KEY=value lines from .env next to this script (ANTHROPIC_API_KEY, FREESOUND_API_KEY)."""
    env = ROOT / ".env"
    if env.exists():
        for line in env.read_text(encoding="utf-8").splitlines():
            key, sep, val = line.partition("=")
            if sep and not line.lstrip().startswith("#"):
                os.environ.setdefault(key.strip(), val.strip().strip('"'))


def fetch(url, timeout=60):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def save_normalised(url, dest):
    with tempfile.NamedTemporaryFile(delete=False) as tmp:
        tmp.write(fetch(url))
    try:
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", tmp.name, "-t", "4", "-ac", "2", "-ar", "48000",
                        "-af", "silenceremove=start_periods=1:start_threshold=-50dB,"
                               "afade=t=out:st=3.4:d=0.6,loudnorm=I=-16:TP=-1",
                        str(dest)], check=True)
    finally:
        os.unlink(tmp.name)


def freesound(query, key, limit):
    params = {
        "query": query, "token": key, "page_size": limit, "sort": "downloads_desc",
        "filter": 'license:"Creative Commons 0" duration:[0.2 TO 4]',
        "fields": "id,name,username,url,previews",
    }
    data = json.loads(fetch("https://freesound.org/apiv2/search/text/?" + urllib.parse.urlencode(params)))
    for s in data.get("results", []):
        yield {"id": f"fs{s['id']}", "name": s["name"], "source": "freesound", "author": s["username"],
               "license": "CC0", "page": s["url"], "audio": s["previews"]["preview-hq-mp3"]}


def main():
    load_env()
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--freesound-key", default=os.environ.get("FREESOUND_API_KEY"))
    ap.add_argument("--freesound-per", type=int, default=2, help="extra Freesound sounds per category")
    args = ap.parse_args()

    credits_path = SFX / "credits.json"
    credits = json.loads(credits_path.read_text(encoding="utf-8")) if credits_path.exists() else {}
    for cat, sounds in MIXKIT.items():
        hits = [{"id": f"mk{i}", "name": name, "source": "mixkit", "license": "Mixkit Sound Effects Free License",
                 "page": "https://mixkit.co/free-sound-effects/", "audio": MIXKIT_URL.format(id=i)}
                for i, name in sounds.items()]
        if args.freesound_key:
            for q in FREESOUND.get(cat, []):
                try:
                    hits += list(freesound(q, args.freesound_key, args.freesound_per))
                except Exception as e:
                    print(f"  freesound {q}: {e}")
        folder = SFX / cat
        folder.mkdir(parents=True, exist_ok=True)
        for h in hits:
            dest = folder / f"{h['id']}.wav"
            if dest.exists():
                continue
            try:
                save_normalised(h["audio"], dest)
            except Exception as e:
                print(f"  skip {h['name']}: {e}")
                continue
            credits[f"{cat}/{dest.name}"] = {k: v for k, v in h.items() if k != "audio"}
            time.sleep(0.3)
        print(f"{cat}: {len(list(folder.glob('*.wav')))} sounds")
    credits_path.write_text(json.dumps(credits, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
