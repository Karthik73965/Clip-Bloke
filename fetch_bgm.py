"""Download free background music into assets/bgm/<mood>/.

Tracks are hand-picked from Mixkit (Mixkit Stock Music Free License: free for
monetised YouTube videos, no attribution; just don't register them with Content ID).
Each file is loudness-normalised, and the point where the track gets going
(after any quiet intro) is stored in assets/bgm/tracks.json so renders start there.

    python fetch_bgm.py
"""
import json
import subprocess
import sys
import tempfile
import os
import urllib.request
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent
BGM = ROOT / "assets" / "bgm"
URL = "https://assets.mixkit.co/music/{id}/{id}.mp3"

# mood -> Mixkit track ids; keep the mood names in sync with reviewer.Mood
MUSIC = {
    "hype": {369: "Need for speed", 357: "Head Bang", 364: "Waka Floka Type", 267: "Trap Hamza",
             370: "Sparta", 404: "Greed", 262: "Praise the Lord", 126: "Trap Electro Vibes"},
    "suspense": {871: "Fright Night", 676: "Epical Drums 01", 680: "Epical Drums 05", 677: "Epical Drums 02",
                 464: "Sci-Fi Score", 853: "Night Patrol"},
    "funny": {2: "Comical", 9: "Kidding Around", 11: "Just Kidding", 6: "Fun and Games", 644: "Upbeat Jazz"},
    "chill": {767: "Lo-Fi 05", 281: "Complicated", 282: "Sweet September", 738: "Hip Hop 02"},
    "epic": {706: "Games Music", 846: "I Won't surrender", 76: "Epic Games"},
}


def energy_start(path):
    """Seconds into the track where it reaches its normal energy (skips quiet intros)."""
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), "-ac", "1", "-ar", "8000", "-f", "s16le", "-"],
                         capture_output=True, check=True).stdout
    a = np.frombuffer(raw, np.int16).astype(np.float32) / 32768
    rms = np.sqrt((a[:len(a) // 8000 * 8000].reshape(-1, 8000) ** 2).mean(1))
    loud = np.nonzero(rms >= 0.6 * np.percentile(rms, 80))[0]
    return float(loud[0]) if len(loud) else 0.0


def main():
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    index_path = BGM / "tracks.json"
    index = json.loads(index_path.read_text(encoding="utf-8")) if index_path.exists() else {}
    for mood, tracks in MUSIC.items():
        folder = BGM / mood
        folder.mkdir(parents=True, exist_ok=True)
        for tid, name in tracks.items():
            dest = folder / f"mk{tid}.m4a"
            key = f"{mood}/{dest.name}"
            if dest.exists() and key in index:
                continue
            with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as tmp:
                req = urllib.request.Request(URL.format(id=tid), headers={"User-Agent": "Mozilla/5.0"})
                tmp.write(urllib.request.urlopen(req, timeout=60).read())
            try:
                subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", tmp.name, "-ac", "2", "-ar", "48000",
                                "-af", "loudnorm=I=-16:TP=-1.5", "-c:a", "aac", "-b:a", "160k", str(dest)], check=True)
                index[key] = {"name": name, "start": energy_start(dest), "source": "mixkit",
                              "license": "Mixkit Stock Music Free License",
                              "page": "https://mixkit.co/free-stock-music/"}
            except Exception as e:
                print(f"  skip {name}: {e}")
            finally:
                os.unlink(tmp.name)
        print(f"{mood}: {len(list(folder.glob('*.m4a')))} tracks")
    index_path.write_text(json.dumps(index, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
