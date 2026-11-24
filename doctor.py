"""Checks that everything GTA-Bloke needs is installed and working.

    python doctor.py
"""
import importlib.util
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
ok = True


def check(name, passed, fix=""):
    global ok
    print(f"  [{'OK' if passed else 'XX'}] {name}" + ("" if passed else f"\n       fix: {fix}"))
    ok &= bool(passed)


def out(cmd):
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=120).stdout + ""
    except (OSError, subprocess.TimeoutExpired):
        return ""


def main():
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    print("Tools")
    check(f"Python {sys.version.split()[0]} (3.11+)", sys.version_info >= (3, 11), "install Python 3.11 or newer")
    ff = shutil.which("ffmpeg")
    filters = out(["ffmpeg", "-hide_banner", "-filters"]) if ff else ""
    check("ffmpeg", ff, "winget install Gyan.FFmpeg   (then open a new terminal)")
    need = ["ass", "xfade", "sidechaincompress", "rgbashift", "loudnorm", "drawtext"]
    missing = [f for f in need if f" {f} " not in filters]
    check("ffmpeg filters " + ", ".join(need), ff and not missing,
          f"your ffmpeg lacks {missing}; install the full build: winget install Gyan.FFmpeg")
    check("ffprobe", shutil.which("ffprobe"), "comes with ffmpeg")
    check("Node.js (yt-dlp YouTube solver)", shutil.which("node"), "winget install OpenJS.NodeJS.LTS")

    print("Python packages")
    for mod, pkg in [("yt_dlp", "yt-dlp"), ("numpy", "numpy"), ("faster_whisper", "faster-whisper"),
                     ("edge_tts", "edge-tts"), ("pydantic", "pydantic"), ("anthropic", "anthropic")]:
        check(pkg, importlib.util.find_spec(mod), "pip install -r requirements.txt")

    print("Assets")
    sfx = list((ROOT / "assets" / "sfx").rglob("*.wav"))
    bgm = list((ROOT / "assets" / "bgm").rglob("*.m4a"))
    check(f"sound effects ({len(sfx)})", len(sfx) >= 20, "python fetch_sfx.py")
    check(f"background music ({len(bgm)})", len(bgm) >= 20, "python fetch_bgm.py")

    print("Speech")
    try:
        sys.path.insert(0, str(ROOT))
        import gta_clipper as g
        tmp = ROOT / "work" / "_doctor"
        tmp.mkdir(parents=True, exist_ok=True)
        wav, secs, words = g.narrate("Testing the narrator voice.", tmp / "voice.wav", g.VOICE)
        check(f"edge-tts narrator ({secs:.1f}s, {len(words)} words)", secs > 0.5 and words,
              "needs internet; pip install -U edge-tts")
        tr = g.Transcriber("base")
        got = tr.words(wav)
        check(f"whisper transcription on {'CPU (slow, no NVIDIA GPU found)' if tr.cpu else 'GPU'}", got,
              "pip install -r requirements.txt")
    except Exception as e:
        check(f"speech ({e.__class__.__name__}: {e})", False, "see the error above")

    print()
    print("All good - open this folder in Antigravity / Codex / Claude Code, paste a stream link and say "
          "'get me clips'." if ok else "Fix the XX items above, then run python doctor.py again.")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
