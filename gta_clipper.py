"""GTA Shorts clipper: paste a YouTube link -> narrated, meme-edited vertical Shorts.

Two steps, with a reviewer in between (a coding agent such as Claude Code, Codex
or Antigravity, following AGENTS.md - or the Claude API / a heuristic in `auto`):

    python gta_clipper.py prepare "https://youtu.be/..."   # candidates -> output/<id>/plan.json
    (reviewer looks at output/<id>/review/*.jpg + transcripts, edits plan.json)
    python gta_clipper.py render <id>                       # kept candidates -> output/<id>/short_*.mp4

    python gta_clipper.py auto "https://youtu.be/..." -n 3  # both, reviewed by Claude API or heuristic
    python gta_clipper.py peek <id> c03 10 20               # closer look at part of a candidate

Prepare: download (yt-dlp; long streams audio-first + per-clip video sections) ->
loud moments -> faster-whisper word timings on GPU -> frame grid per candidate.
Render: facecam-over-gameplay (or blurred background) with word captions, zoom
punch, shake, flash and SFX on the peak, then the Short is assembled as
flash-forward of the peak -> freeze + glitch -> rewind -> narrator setup (edge-tts)
-> zoom transition -> clip with slow-mo peak -> narrator outro, over mood BGM
that ducks under voices and drops out on the peak.
"""
import argparse
import asyncio
import importlib.util
import json
import os
import random
import re
import subprocess
import time
from collections import namedtuple
from pathlib import Path

import numpy as np

from reviewer import HOOKS, HYPE, INTROS, Reviewer, contact_sheet, heuristic, transcript_text

ROOT = Path(__file__).resolve().parent
W, H = 1080, 1920
FPS = 30
SR = 8000          # analysis sample rate
HOP = 0.25         # loudness window, seconds
VOICE = "en-US-AndrewMultilingualNeural"

Word = namedtuple("Word", "word start end")


def load_env():
    """KEY=value lines from .env next to this script (ANTHROPIC_API_KEY, FREESOUND_API_KEY)."""
    env = ROOT / ".env"
    if env.exists():
        for line in env.read_text(encoding="utf-8").splitlines():
            key, sep, val = line.partition("=")
            if sep and not line.lstrip().startswith("#"):
                os.environ.setdefault(key.strip(), val.strip().strip('"'))


def hms(sec):
    return f"{int(sec // 3600)}:{int(sec % 3600 // 60):02d}:{int(sec % 60):02d}"


def run(cmd, **kw):
    subprocess.run(cmd, check=True, **kw)


def duration(path):
    return float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0",
                                 str(path)], capture_output=True, text=True, check=True).stdout.strip())


# ---------------------------------------------------------------- download

VIDEO_FMT = "bv*[height<=1080][ext=mp4]+ba[ext=m4a]/b[height<=1080]/b"
LOWRES_FMT = "160/278/394/wv"      # 144p video only, ~230 MB for 6 h: the whole stream, for scanning + review
AUDIO_EXTS = {".m4a", ".webm", ".opus", ".mp3"}


def ytdlp(*args):
    run(["yt-dlp", "--js-runtimes", "node", "--no-playlist", *args])


def download(url, dest, full_max):
    """Returns (audio, full HD video or None, low-res scan video, info).

    Videos up to full_max seconds are downloaded whole. Longer streams get audio + a
    144p copy; HD video of each kept clip is fetched at render time with fetch_section."""
    if Path(url).is_file():  # local recording, no download
        p = Path(url).resolve()
        return p, p, p, {"id": p.stem, "uploader": ""}
    info = json.loads(subprocess.run(["yt-dlp", "--js-runtimes", "node", "-J", "--no-playlist", url],
                                     capture_output=True, text=True, check=True).stdout)
    if info.get("live_status") in ("is_live", "is_upcoming"):
        raise SystemExit(f"{info['id']} is still live - run it again after the stream ends")
    dest = dest / info["id"]
    dest.mkdir(parents=True, exist_ok=True)
    if (info.get("duration") or 0) <= full_max:
        video = dest / "source.mp4"
        if not video.exists():
            ytdlp("-f", VIDEO_FMT, "--merge-output-format", "mp4", "-o", str(video), url)
        return video, video, video, info
    print(f"  long stream ({info['duration'] / 3600:.1f} h): audio + 144p scan copy, HD only for kept clips")
    audio = next((f for f in dest.glob("audio.*") if f.suffix in AUDIO_EXTS), None)
    if audio is None:
        ytdlp("-f", "ba[ext=m4a]/ba", "-N", "4", "-o", str(dest / "audio.%(ext)s"), url)
        audio = next(f for f in dest.glob("audio.*") if f.suffix in AUDIO_EXTS)
    lowres = next((f for f in dest.glob("lowres.*") if f.suffix in (".mp4", ".webm")), None)
    if lowres is None:
        ytdlp("-f", LOWRES_FMT, "-N", "4", "-o", str(dest / "lowres.%(ext)s"), url)
        lowres = next(f for f in dest.glob("lowres.*") if f.suffix in (".mp4", ".webm"))
    return audio, None, lowres, info


def fetch_section(url, start, end, out):
    ytdlp("-q", "--no-progress", "-f", VIDEO_FMT, "--download-sections", f"*{start:.2f}-{end:.2f}",
          "--force-keyframes-at-cuts", "--merge-output-format", "mp4", "-o", str(out), url)


def probe_size(video):
    out = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0",
                          "-show_entries", "stream=width,height", "-of", "csv=p=0", str(video)],
                         capture_output=True, text=True, check=True).stdout
    w, h = map(int, out.strip().split(",")[:2])
    return w, h


# ---------------------------------------------------------------- moments

def loudness_db(src):
    """Per-HOP loudness, streamed in chunks so 24 h streams don't need GBs of RAM."""
    hop_bytes = int(SR * HOP) * 2
    proc = subprocess.Popen(["ffmpeg", "-v", "error", "-i", str(src), "-vn", "-ac", "1",
                             "-ar", str(SR), "-f", "s16le", "-"], stdout=subprocess.PIPE)
    rms, buf = [], b""
    while chunk := proc.stdout.read(hop_bytes * 4096):
        buf += chunk
        n = len(buf) // hop_bytes
        if n:
            a = np.frombuffer(buf[:n * hop_bytes], np.int16).astype(np.float32) / 32768
            rms.append(np.sqrt((a.reshape(n, -1) ** 2).mean(1) + 1e-10))
            buf = buf[n * hop_bytes:]
    if proc.wait():
        raise subprocess.CalledProcessError(proc.returncode, "ffmpeg loudness")
    return 20 * np.log10(np.concatenate(rms))


FW, FH = 96, 54   # analysis frame size


def live_gameplay(video, n_hops):
    """Per-HOP True where the screen shows moving gameplay - not a pause/save/map menu, phone,
    loading or black screen. Looks at the centre of the frame (facecam, chat and HUD sit at the
    edges). Calibrated on GTA 6 streams: menus/phone move < 3, gameplay 10-70, loading is black."""
    fps = 2
    frame = FW * FH
    proc = subprocess.Popen(["ffmpeg", "-v", "error", "-i", str(video), "-an", "-vf",
                             f"fps={fps},scale={FW}:{FH},format=gray", "-f", "rawvideo", "-"],
                            stdout=subprocess.PIPE)
    bright, motion, prev, buf = [], [], None, b""
    while chunk := proc.stdout.read(frame * 2048):
        buf += chunk
        n = len(buf) // frame
        if not n:
            continue
        f = np.frombuffer(buf[:n * frame], np.uint8).reshape(n, FH, FW)[:, 5:49, 24:72].astype(np.float32)
        buf = buf[n * frame:]
        bright.append(f.mean((1, 2)))
        seq = f if prev is None else np.concatenate([prev[None], f])
        d = np.abs(np.diff(seq, axis=0)).mean((1, 2))
        motion.append(d if prev is not None else np.concatenate([[0.0], d]))
        prev = f[-1]
    if proc.wait():
        raise subprocess.CalledProcessError(proc.returncode, "ffmpeg scan")
    bright, motion = np.concatenate(bright), np.concatenate(motion)
    k = 2 * fps                                         # 2 s centred average of motion
    motion = np.convolve(motion, np.ones(k) / k, mode="same")
    live = (bright > 12) & (motion > 4)
    per_hop = np.repeat(live, int(1 / (HOP * fps)))     # fps samples -> HOP grid
    out = np.zeros(n_hops, bool)
    out[:min(n_hops, len(per_hop))] = per_hop[:n_hops]
    return out


def moving_avg(x, k):
    c = np.concatenate([[0], np.cumsum(x)])
    out = np.empty_like(x)
    for i in range(len(x)):  # trailing window
        lo = max(0, i - k + 1)
        out[i] = (c[i + 1] - c[lo]) / (i + 1 - lo)
    return out


def find_moments(db, live, clip_len, max_n, min_z, min_live, skip=15.0):
    """Every moment where a loud second jumps above the 8 s before it (screams, crashes,
    explosions) while real gameplay is on screen, best first, until scores drop below min_z.
    Returns (moments sorted by time, number of loud peaks dropped as menus/loading)."""
    k, kb = int(1 / HOP), int(8 / HOP)
    burst = moving_avg(db, k)
    before = np.roll(moving_avg(db, kb), k)
    before[:k] = burst[:k]
    z = lambda x: (x - np.median(x)) / (x.std() + 1e-6)
    score = 0.6 * z(burst) + 0.4 * z(burst - before)

    total = len(db) * HOP
    near_live = np.convolve(live, np.ones(int(4 / HOP)), mode="same") > 0   # reactions land just after action
    score[~near_live] = -np.inf
    lo, hi = int(skip / HOP), int(max(skip, total - skip) / HOP)
    score[:lo] = score[hi:] = -np.inf
    moments, dropped = [], 0
    while len(moments) < max_n:
        i = int(np.argmax(score))
        if not np.isfinite(score[i]) or score[i] < min_z:
            break
        peak = i * HOP - 0.5  # centre of the loud second
        start = min(max(0.0, peak - clip_len * 0.65), max(0.0, total - clip_len))
        a, b = int(start / HOP), int((start + clip_len) / HOP)
        frac = float(live[a:b].mean()) if b > a else 0.0
        if frac < min_live:                           # mostly menus/loading: skip just this peak
            score[max(0, i - int(3 / HOP)):i + int(3 / HOP)] = -np.inf
            dropped += 1
            continue
        moments.append({"start": round(start, 2), "end": round(min(total, start + clip_len), 2),
                        "peak": round(peak - start, 2), "score": round(float(score[i]), 2), "live": round(frac, 2)})
        a, b = int((start - clip_len * 0.5) / HOP), int((start + clip_len * 1.5) / HOP)
        score[max(0, a):max(0, b)] = -np.inf
    return sorted(moments, key=lambda m: m["start"]), dropped


# ---------------------------------------------------------------- speech

def add_cuda_dlls():
    """faster-whisper on Windows needs the cuBLAS/cuDNN DLLs from the nvidia-* pip wheels on the search path."""
    spec = importlib.util.find_spec("nvidia")
    if not spec or not spec.submodule_search_locations or not hasattr(os, "add_dll_directory"):
        return
    for d in Path(spec.submodule_search_locations[0]).glob("*/bin"):
        os.add_dll_directory(str(d))
        os.environ["PATH"] = str(d) + os.pathsep + os.environ["PATH"]


class Transcriber:
    def __init__(self, size):
        self.size = size
        self.model = None
        self.cpu = False

    def _load(self):
        add_cuda_dlls()
        from faster_whisper import WhisperModel
        if self.cpu:
            self.model = WhisperModel(self.size, device="cpu", compute_type="int8")
        else:
            self.model = WhisperModel(self.size, device="cuda", compute_type="float16")

    def words(self, wav):
        for _ in range(2):
            try:
                if self.model is None:
                    self._load()
                segs, _ = self.model.transcribe(str(wav), word_timestamps=True, vad_filter=True,
                                                beam_size=5, condition_on_previous_text=False)
                return [Word(w.word, w.start, w.end) for s in segs for w in (s.words or [])]
            except Exception as e:  # missing CUDA/cuDNN DLLs show up here, not at load
                if self.cpu:
                    raise
                print(f"  GPU whisper unavailable ({e.__class__.__name__}), using CPU")
                self.cpu, self.model = True, None
        return []


def narrate(text, out, voice):
    """Narrator line with edge-tts (free Microsoft neural voices). Returns (wav, seconds, word timings)."""
    import edge_tts

    async def synth():
        words = []
        with open(out.with_suffix(".mp3"), "wb") as f:
            async for chunk in edge_tts.Communicate(text, voice, rate="+8%", boundary="WordBoundary").stream():
                if chunk["type"] == "audio":
                    f.write(chunk["data"])
                elif chunk["type"] == "WordBoundary":
                    s = chunk["offset"] / 1e7
                    words.append(Word(chunk["text"], s, s + chunk["duration"] / 1e7))
        return words

    words = asyncio.run(synth())
    # trailer-narrator polish: cut rumble, add presence, compress, then level it
    run(["ffmpeg", "-v", "error", "-y", "-i", str(out.with_suffix(".mp3")), "-ac", "2", "-ar", "48000",
         "-af", "highpass=f=90,equalizer=f=3200:t=q:w=1:g=3,bass=g=3:f=120,"
                "acompressor=threshold=0.08:ratio=3:attack=5:release=120,loudnorm=I=-13:TP=-1.5",
         str(out)])
    return out, duration(out), words


# ---------------------------------------------------------------- captions

def ass_time(t):
    t = max(0.0, t)
    return f"{int(t // 3600)}:{int(t % 3600 // 60):02d}:{t % 60:05.2f}"


def clean(word):
    return re.sub(r"[^A-Z0-9'!?]", "", word.upper())


def group_words(words, max_words=3, gap=0.45):
    groups, cur = [], []
    for w in words:
        text = clean(w.word)
        if not text:
            continue
        if cur and (len(cur) >= max_words or w.start - cur[-1][2] > gap or cur[-1][0][-1] in "!?"):
            groups.append(cur)
            cur = []
        cur.append((text, w.start, w.end))
    if cur:
        groups.append(cur)
    return groups


class Ass:
    """One .ass subtitle file; times in seconds on the timeline it is burned into."""
    STYLES = [
        "Style: Cap,Arial Black,96,&H00FFFFFF,&H00FFFFFF,&H00000000,&H80000000,-1,0,0,0,100,100,0,0,1,9,4,5,60,60,0,1",
        "Style: Narr,Arial Black,80,&H00FFFFFF,&H00FFFFFF,&H00000000,&HA0000000,-1,0,0,0,100,100,0,0,1,10,6,5,60,60,0,1",
        "Style: Hook,Impact,104,&H00FFFFFF,&H00FFFFFF,&H00000000,&H00000000,0,0,0,0,100,100,1,0,1,9,0,5,60,60,0,1",
        "Style: Tag,Arial Black,56,&H00FFFFFF,&H00FFFFFF,&H00000000,&H00000000,-1,0,0,0,100,100,2,0,1,4,0,7,0,0,0,1",
        "Style: Credit,Arial Black,40,&H60FFFFFF,&H60FFFFFF,&H60000000,&H00000000,-1,0,0,0,100,100,0,0,1,3,0,5,0,0,0,1",
    ]

    def __init__(self):
        self.events = []

    def add(self, s, e, style, text):
        self.events.append(f"Dialogue: 0,{ass_time(s)},{ass_time(e)},{style},,0,0,0,,{text}")

    def hook(self, text, s, e, y):
        self.add(s, e, "Hook", rf"{{\pos(540,{y})\fscx40\fscy40\t(0,160,\fscx108\fscy108)\t(160,240,\fscx100\fscy100)"
                               rf"\c&H00E5FF&}}{text}")

    def words(self, words, y, end, style="Cap", active="&H00E5FF&", offset=0.0, max_words=3):
        """Word-by-word captions: short groups pop in, the spoken word is highlighted."""
        groups = group_words(words, max_words)
        for gi, g in enumerate(groups):
            g_end = min(g[-1][2] + 0.35, groups[gi + 1][0][1] if gi + 1 < len(groups) else end)
            for wi, (text, s, _) in enumerate(g):
                e = g[wi + 1][1] if wi + 1 < len(g) else g_end
                parts = []
                for wj, (t2, _, _) in enumerate(g):
                    if wj == wi:
                        col = "&H3030FF&" if style == "Cap" and t2.strip("!?") in HYPE else active
                        parts.append(rf"{{\c{col}\fscx125\fscy125\t(0,90,\fscx105\fscy105)}}{t2}{{\r}}")
                    else:
                        parts.append(t2)
                pop = r"\fscx70\fscy70\t(0,70,\fscx100\fscy100)" if wi == 0 else ""
                self.add(s + offset, e + offset, style, rf"{{\pos(540,{y}){pop}}}" + " ".join(parts))

    def write(self, path):
        head = ["[Script Info]", "ScriptType: v4.00+", f"PlayResX: {W}", f"PlayResY: {H}", "WrapStyle: 2", "",
                "[V4+ Styles]",
                "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, "
                "Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, "
                "Shadow, Alignment, MarginL, MarginR, MarginV, Encoding",
                *self.STYLES, "", "[Events]",
                "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text"]
        path.write_text("\n".join(head + self.events) + "\n", encoding="utf-8")


# ---------------------------------------------------------------- assets

AUDIO_FILES = {".mp3", ".wav", ".ogg", ".m4a"}


def boom_sfx(path):
    """Fallback bass drop when assets/sfx is empty."""
    run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
         "aevalsrc='0.9*sin(2*PI*(55+90*exp(-9*t))*t)*exp(-3.2*t)':s=48000:d=1.4",
         "-af", "acompressor=threshold=0.3:ratio=4,volume=2", str(path)])


def pick_sfx(category):
    """Random file from assets/sfx/<category>/, else anything in assets/sfx/, else a generated boom."""
    sfx_dir = ROOT / "assets" / "sfx"
    sfx = [p for p in (sfx_dir / category).glob("*") if p.suffix.lower() in AUDIO_FILES]
    sfx = sfx or [p for p in sfx_dir.rglob("*") if p.suffix.lower() in AUDIO_FILES]
    if sfx:
        return random.choice(sfx)
    boom = ROOT / "work" / "_boom.wav"
    if not boom.exists():
        boom.parent.mkdir(exist_ok=True)
        boom_sfx(boom)
    return boom


def pick_bgm(mood):
    """(track, start offset) from assets/bgm/<mood>/ (filled by fetch_bgm.py), or None."""
    bgm_dir = ROOT / "assets" / "bgm"
    tracks = [p for p in (bgm_dir / mood).glob("*") if p.suffix.lower() in AUDIO_FILES]
    tracks = tracks or [p for p in bgm_dir.rglob("*") if p.suffix.lower() in AUDIO_FILES]
    if not tracks:
        return None
    index = bgm_dir / "tracks.json"
    starts = json.loads(index.read_text(encoding="utf-8")) if index.exists() else {}
    track = random.choice(tracks)
    return track, starts.get(f"{track.parent.name}/{track.name}", {}).get("start", 0.0)


# ---------------------------------------------------------------- clip render

CAM_H = 880  # split layout: facecam band height; gameplay fills the rest


def even(x):
    return max(2, int(round(x / 2)) * 2)


def split_layout(face, src_w, src_h, zoom, env):
    """Facecam stacked over gameplay, the whole frame punched/shaken together. Returns (filter, caption y)."""
    fx, fy, fw, fh = face[0] * src_w, face[1] * src_h, face[2] * src_w, face[3] * src_h
    ch = min(src_h, fh * 0.96)                   # trim borders, then match the band aspect
    cw = min(src_w, ch * W / CAM_H)
    if cw > fw * 0.96:                           # cam is narrower than the band: crop height instead
        cw = fw * 0.96
        ch = cw * CAM_H / W
    cx = min(max(0, fx + (fw - cw) / 2), src_w - cw)
    cy = min(max(0, fy + (fh - ch) / 2), src_h - ch)
    game_h = H - CAM_H
    gh = src_h
    gw = min(src_w, gh * W / game_h)
    gx = (src_w - gw) / 2
    if fx + fw / 2 < src_w / 2:                  # cam on the left: keep its overlay out of the gameplay crop
        gx = min(max(gx, fx + fw), src_w - gw)
    else:
        gx = max(min(gx, fx - gw), 0)
    vf = (
        f"[0:v]fps={FPS},split[a][b];"
        f"[a]crop={even(cw)}:{even(ch)}:{int(cx)}:{int(cy)},scale={W}:{CAM_H},eq=saturation=1.15[cam];"
        f"[b]crop={even(gw)}:{even(gh)}:{int(gx)}:0,scale={W}:{game_h},eq=saturation=1.28:contrast=1.07[game];"
        f"[cam][game]vstack,"
        f"scale=w='2*trunc({W}*{zoom}/2)':h=-2:eval=frame,"
        f"crop={W}:{H}:x='clip((iw-ow)/2+26*{env}*sin(t*71),0,iw-ow)':"
        f"y='clip((ih-oh)/2+18*{env}*cos(t*59),0,ih-oh)',"
    )
    return vf, CAM_H


def blur_layout(src_w, src_h, zoom, env, tall):
    """Gameplay band over a blurred copy of itself. Returns (filter, caption y)."""
    aspect = src_w / src_h
    box_h = min(H - 400, 2 * round(W / aspect * tall / 2))     # gameplay band height
    base_w = box_h * aspect                                    # source width when band is filled
    fg_y = (H - box_h) // 2 - 80
    vf = (
        f"[0:v]fps={FPS},split[a][b];"
        f"[a]scale=270:480:force_original_aspect_ratio=increase,crop=270:480,boxblur=12:3,"
        f"scale={W}:{H},eq=brightness=-0.18:saturation=1.3[bg];"
        f"[b]eq=saturation=1.28:contrast=1.07,"
        f"scale=w='2*trunc({base_w:.1f}*{zoom}/2)':h=-2:eval=frame,"
        f"crop={W}:{box_h}:x='clip((iw-ow)/2+26*{env}*sin(t*71),0,iw-ow)':"
        f"y='clip((ih-oh)/2+18*{env}*cos(t*59),0,ih-oh)'[fg];"
        f"[bg][fg]overlay=0:{fg_y},"
    )
    return vf, fg_y + box_h + 170


def render_clip(video, c, out, work, tall):
    """The clip itself, 9:16 with captions, zoom punch, shake, flash and SFX on the peak. Returns caption y."""
    L, pk = c["end"] - c["start"], c["peak"]
    src_w, src_h = probe_size(video)
    env = f"if(between(t,{pk},{pk}+0.7),1-(t-{pk})/0.7,0)"
    if c["facecam"]:
        zoom = f"(1.03+0.03*t/{L}+if(gte(t,{pk}),0.22*exp(-(t-{pk})*1.3),0))"
        vf, cap_y = split_layout(c["facecam"], src_w, src_h, zoom, env)
    else:
        zoom = f"(1.04+0.04*t/{L}+if(gte(t,{pk}),0.32*exp(-(t-{pk})*1.3),0))"
        vf, cap_y = blur_layout(src_w, src_h, zoom, env, tall)
    subs = Ass()
    subs.words(c["words"], cap_y, L)
    if c["credit"]:
        subs.add(0, L, "Credit", rf"{{\pos(540,{H - 140})}}@{c['credit']}")
    subs.write(work / "clip.ass")
    vf += (
        f"drawbox=c=white@0.75:t=fill:enable='between(t,{pk},{pk + 0.07})',"
        f"ass=clip.ass,format=yuv420p[v];"
        f"[0:a]aresample=48000,bass=g=9:enable='between(t,{pk},{pk + 1.4})'[ga];"
        f"[1:a]aresample=48000,adelay={int(pk * 1000)}:all=1,volume={0 if c['sfx'] == 'none' else 0.9}[sx];"
        f"[ga][sx]amix=inputs=2:duration=first:normalize=0[au]"
    )
    run(["ffmpeg", "-v", "error", "-y", "-ss", str(c["start"]), "-t", str(L), "-i", str(video),
         "-i", str(pick_sfx(c["sfx"])), "-filter_complex", vf, "-map", "[v]", "-map", "[au]",
         "-c:v", "libx264", "-preset", "veryfast", "-crf", "15", "-c:a", "pcm_s16le", "-ar", "48000", "-ac", "2",
         str(out)], cwd=work)
    return cap_y


# ---------------------------------------------------------------- short assembly

PV, FZ, RW = 1.2, 0.35, 0.7   # flash-forward, freeze, rewind lengths (s)
X1, X2 = 0.12, 0.35           # white-flash and zoom transition lengths (s)


def assemble(clip, c, cap_y, work, out, encoder, voice):
    """Flash-forward -> freeze/glitch -> rewind -> narrator setup -> zoom -> clip (slow-mo peak) -> outro."""
    L, pk = c["end"] - c["start"], c["peak"]
    intro = narrate(c["intro"], work / "intro.wav", voice) if c["intro"].strip() else None
    outro = narrate(c["outro"], work / "outro.wav", voice) if c["outro"].strip() else None

    pv0 = min(max(0.0, pk - 0.8), max(0.0, L - PV))          # flash-forward window
    rw_len = min(2.8, pv0 + PV)                              # source seconds played backwards
    rw0 = pv0 + PV - rw_len
    sd = (intro[1] + 0.45) if intro else 1.2                 # narrator setup segment
    s1, s2 = (max(0.0, pk - 0.25), min(L, pk + 0.9)) if c["slowmo"] else (L, L)
    Dm = L + (s2 - s1)                                       # clip length after slow-mo
    LA = PV + FZ
    offC = LA + RW - X1
    offD = offC + sd - X2
    total = offD + Dm
    pk_g = offD + (s1 + 2 * (pk - s1) if c["slowmo"] else pk)

    inputs = [["-ss", f"{pv0:.3f}", "-t", f"{PV}", "-i", clip],
              ["-ss", f"{rw0:.3f}", "-t", f"{rw_len:.3f}", "-i", clip],
              ["-t", f"{sd / 2.5:.3f}", "-i", clip]]
    if c["slowmo"]:
        inputs += [["-t", f"{s1:.3f}", "-i", clip], ["-ss", f"{s1:.3f}", "-t", f"{s2 - s1:.3f}", "-i", clip],
                   ["-ss", f"{s2:.3f}", "-i", clip]]
    else:
        inputs += [["-i", clip]]
    n_clip = len(inputs)

    def add_input(*args):
        inputs.append([*map(str, args)])
        return len(inputs) - 1

    norm = f"fps={FPS},format=yuv420p,setsar=1,settb=AVTB"
    fc = [
        # flash-forward of the peak, then freeze with an RGB-split glitch
        f"[0:v]tpad=stop_mode=clone:stop_duration={FZ},"
        f"rgbashift=rh=-22:bh=22:enable='gte(t,{PV})',noise=alls=40:allf=t:enable='gte(t,{PV})',{norm}[A]",
        # VHS rewind back to before the peak
        f"[1:v]reverse,setpts=N/({FPS}*{rw_len / RW:.4f})/TB,{norm},hue=s=0.3,noise=alls=30:allf=t,"
        f"rgbashift=rh=10:bh=-10,eq=contrast=1.15,trim=duration={RW},setpts=PTS-STARTPTS[B]",
        # narrator setup: start of the clip slowed down, blurred and darkened, slow push-in
        f"[2:v]setpts=2.5*(PTS-STARTPTS),{norm},tpad=stop_mode=clone:stop_duration={sd},trim=duration={sd},"
        f"boxblur=10:2,eq=brightness=-0.16:saturation=1.25,"
        f"scale=w='2*trunc({W}*(1.04+0.06*t/{sd:.3f})/2)':h=-2:eval=frame,crop={W}:{H}[C]",
    ]
    if c["slowmo"]:
        fc.append(f"[4:v]setpts=2*(PTS-STARTPTS)[sv];[4:a]atempo=0.5[sa];"
                  f"[3:v][3:a][sv][sa][5:v][5:a]concat=n=3:v=1:a=1[dv][da]")
        fc.append(f"[dv]{norm}[D]")
    else:
        fc.append(f"[3:v]{norm}[D];[3:a]anull[da]")
    fc += [
        f"[A][B]concat=n=2:v=1:a=0,{norm}[AB]",
        f"[AB][C]xfade=transition=fadewhite:duration={X1}:offset={LA + RW - X1:.3f}[ABC]",
        f"[ABC][D]xfade=transition=zoomin:duration={X2}:offset={offD:.3f},ass=final.ass[v]",
    ]

    # audio: everything placed on the final timeline, then BGM ducked under it
    ms = lambda t: int(round(t * 1000))
    voice_bus = [f"[0:a]afade=t=out:st={PV - 0.06}:d=0.06[a0]",
                 f"[1:a]areverse,atempo={rw_len / RW:.4f},volume=0.4,adelay={ms(LA)}:all=1[a1]"]
    duck = (f",volume='if(gte(t,{Dm - outro[1] - 0.45:.3f}),0.35,1)':eval=frame" if outro else "")
    voice_bus.append(f"[da]aresample=48000{duck},adelay={ms(offD)}:all=1[a2]")
    parts = ["[a0]", "[a1]", "[a2]"]
    for name, path, at, vol, extra in [
        ("glitch", pick_sfx("boom"), PV, 0.8, ""),
        ("rew", pick_sfx("whoosh"), LA, 0.6, "areverse,"),
        ("swoosh", pick_sfx("whoosh"), offD - 0.2, 0.8, ""),
        ("intro", intro and intro[0], offC + 0.08, 1.0, ""),
        ("outro", outro and outro[0], total - (outro[1] + 0.35 if outro else 0), 1.0, ""),
    ]:
        if path:
            i = add_input("-i", path)
            voice_bus.append(f"[{i}:a]aresample=48000,{extra}volume={vol},adelay={ms(at)}:all=1[{name}]")
            parts.append(f"[{name}]")
    voice_bus.append(f"{''.join(parts)}amix=inputs={len(parts)}:duration=longest:normalize=0,"
                     f"atrim=duration={total:.3f},asplit[prog][key]")
    bgm = pick_bgm(c["mood"])
    if bgm:
        i = add_input("-stream_loop", "-1", "-ss", f"{bgm[1]:.2f}", "-i", bgm[0])
        rel = pk_g - LA   # BGM starts with the rewind; it drops out on the peak
        voice_bus.append(
            f"[{i}:a]aresample=48000,atrim=duration={total - LA:.3f},volume=0.32,afade=t=in:d=0.2,"
            f"afade=t=out:st={total - LA - 0.6:.3f}:d=0.6,"
            f"volume='if(between(t,{rel - 0.1:.3f},{rel + 1.3:.3f}),0.08,1)':eval=frame,adelay={ms(LA)}:all=1[bgm];"
            f"[bgm][key]sidechaincompress=threshold=0.05:ratio=3:attack=15:release=350[bgd];"
            f"[prog][bgd]amix=inputs=2:duration=first:normalize=0[mix]")
    else:
        voice_bus.append("[prog]anull[mix];[key]anullsink")
    voice_bus.append("[mix]loudnorm=I=-14:TP=-1.5:LRA=11,aresample=48000[a]")

    subs = Ass()
    hook_y = 170 if cap_y <= CAM_H else 300
    subs.hook(c["hook"].upper(), 0, LA, hook_y)
    subs.add(LA, LA + RW, "Tag", r"{\pos(70,150)\c&H3838FF&}◄◄ {\c&HFFFFFF&}REWIND")
    if intro:
        subs.words(intro[2], 1320 if cap_y <= CAM_H else H // 2, sd - 0.1, style="Narr", active="&HFFE500&", offset=offC + 0.08,
                   max_words=2)
    if outro:
        subs.words(outro[2], 1450 if cap_y <= CAM_H else 420, outro[1] + 0.35, style="Narr", active="&HFFE500&",
                   offset=total - outro[1] - 0.35, max_words=2)
    subs.write(work / "final.ass")

    enc = (["-c:v", "h264_nvenc", "-preset", "p5", "-cq", "21", "-b:v", "0"] if encoder == "nvenc"
           else ["-c:v", "libx264", "-preset", "medium", "-crf", "20"])
    cmd = ["ffmpeg", "-v", "error", "-y"]
    for args in inputs:
        cmd += args
    run([*cmd, "-filter_complex", ";".join(fc + voice_bus), "-map", "[v]", "-map", "[a]", *enc,
         "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", str(out)], cwd=work)
    return total, n_clip


def pick_encoder():
    try:
        run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "color=s=256x256:d=0.1",
             "-c:v", "h264_nvenc", "-f", "null", "-"], capture_output=True)
        return "nvenc"
    except subprocess.CalledProcessError:
        return "x264"


def preview_strip(video, out, frames=8):
    """One row of frames across the finished Short, for the reviewer to check the render."""
    d = duration(video)
    run(["ffmpeg", "-v", "error", "-y", "-i", str(video), "-vf",
         f"fps={frames}/{d:.3f},scale=240:426,tile={frames}x1", "-frames:v", "1", "-q:v", "4", str(out)])


# ---------------------------------------------------------------- plan

MIN_CLIP = 12.0
MAX_EXTEND = 20.0   # a reviewer may extend a cut this far past either side of its window


def plan_path(vid):
    return ROOT / "output" / vid / "plan.json"


def load_plan(ref):
    p = Path(ref)
    p = p if p.suffix == ".json" else plan_path(ref)
    return p, json.loads(p.read_text(encoding="utf-8"))


def save_plan(p, plan):
    p.write_text(json.dumps(plan, indent=2, ensure_ascii=False), encoding="utf-8")


def load_words(c):
    data = json.loads((ROOT / c["work"] / "words.json").read_text(encoding="utf-8"))
    return [Word(*w) for w in data]


def apply_review(c, r):
    c.update(r.model_dump(exclude={"facecam"}))
    c["facecam"] = [r.facecam.x, r.facecam.y, r.facecam.w, r.facecam.h] if r.facecam else None
    c["reviewed_by"] = "heuristic" if r.reason.startswith("heuristic") else "claude-api"


def prepare(args):
    t0 = time.time()
    print("Downloading...")
    audio, video, lowres, info = download(args.url, ROOT / "downloads", args.full_max)
    vid = info["id"]
    out_dir = ROOT / "output" / vid
    (out_dir / "review").mkdir(parents=True, exist_ok=True)
    p = plan_path(vid)
    plan = json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}
    if plan.get("version") != 2:   # older plan layout: keep it for reference, start fresh
        if p.exists():
            p.replace(p.with_name("plan_v1.json"))
        plan = {"version": 2, "video_id": vid, "url": args.url, "title": info.get("title", ""),
                "duration": info.get("duration"), "credit": re.sub(r"\s+", "", info.get("uploader") or ""),
                "voice": VOICE, "facecam": None, "layout_tall": 1.25, "candidates": []}
    plan.update(audio=str(Path(audio).relative_to(ROOT)) if Path(audio).is_relative_to(ROOT) else str(audio),
                video=str(Path(video).relative_to(ROOT)) if video and Path(video).is_relative_to(ROOT) else video and str(video),
                lowres=str(Path(lowres).relative_to(ROOT)) if Path(lowres).is_relative_to(ROOT) else str(lowres))
    known = {c["key"]: c for c in plan["candidates"]}
    print(f"  downloaded ({time.time() - t0:.0f}s)")

    print("Scanning the whole stream (loudness + gameplay vs menus/loading)...")
    db = loudness_db(audio)
    live = live_gameplay(lowres, len(db))
    print(f"  {live.mean() * 100:.0f}% of the stream is live gameplay ({time.time() - t0:.0f}s)")
    moments, dropped = find_moments(db, live, args.len, args.max, args.min_z, args.min_live)
    print(f"  {len(moments)} candidate moments; {dropped} loud moments skipped as menus/loading/black")
    tr = Transcriber(args.model)
    for i, m in enumerate(moments, 1):
        key = f"t{int(m['start'])}"
        if key in known:
            continue
        work = ROOT / "work" / vid / key
        work.mkdir(parents=True, exist_ok=True)
        L = m["end"] - m["start"]
        wav = work / "audio.wav"
        run(["ffmpeg", "-v", "error", "-y", "-ss", str(m["start"]), "-t", str(L),
             "-i", str(audio), "-vn", "-ac", "1", "-ar", "16000", str(wav)])
        words = tr.words(wav)
        (work / "words.json").write_text(json.dumps([list(w) for w in words]), encoding="utf-8")
        sheet = out_dir / "review" / f"{key}.jpg"
        contact_sheet(lowres, m["start"], L, sheet)
        c = {"id": "", "key": key, "source_time": hms(m["start"]), "start": m["start"], "length": round(L, 2),
             "loud_peak": m["peak"], "loud_score": m["score"], "live": m["live"],
             "work": str(work.relative_to(ROOT)), "sheet": f"review/{sheet.name}",
             "transcript": transcript_text(words)}
        apply_review(c, heuristic(words, m["score"], L, m["peak"]))
        c.update(keep=False, reviewed_by="none")
        plan["candidates"].append(c)
        print(f"  [{i}/{len(moments)}] {hms(m['start'])}  live {m['live']:.0%}  loud {m['score']:.1f}")

    plan["candidates"].sort(key=lambda c: c["start"])
    for i, c in enumerate(plan["candidates"], 1):
        c["id"] = f"c{i:02d}"
    save_plan(p, plan)
    print(f"Prepared {len(plan['candidates'])} candidates in {(time.time() - t0) / 60:.1f} min -> {p}")
    return p, plan


def cut_range(c):
    """(a, b, peak) in seconds relative to the candidate window; a may be < 0 and b > length."""
    L = c["length"]
    a = min(max(-MAX_EXTEND, -c["start"], c["trim_start"]), L - MIN_CLIP)
    b = min(L + MAX_EXTEND, max(c["trim_end"], a + MIN_CLIP))
    peak = c["peak"] if a <= c["peak"] <= b else (c["loud_peak"] if a <= c["loud_peak"] <= b else a + (b - a) * 0.65)
    return a, b, peak


def hd_source(plan, c, fetch=True):
    """(HD video, offset of the cut start inside it). Full-length videos are used directly;
    for long streams the cut (+0.5 s each side) is downloaded on demand and cached."""
    a, b, _ = cut_range(c)
    s0, s1 = c["start"] + a, c["start"] + b
    if plan.get("video"):
        return ROOT / plan["video"], s0
    work = ROOT / c["work"]
    section = work / "section.mp4"          # whole window, from older runs
    if section.exists() and a >= 0 and b <= c["length"]:
        return section, a
    out = work / f"cut_{int(s0 * 10)}_{int(s1 * 10)}.mp4"
    if not out.exists():
        if not fetch:
            return None, 0.0
        fetch_section(plan["url"], max(0.0, s0 - 0.5), s1 + 0.5, out)
    return out, min(0.5, s0)


def write_upload(out_dir, plan):
    """Titles, descriptions and tags for every rendered Short, ready to paste into YouTube."""
    credit = plan.get("credit") or "the streamer"
    items = []
    for c in plan["candidates"]:
        if not c.get("output") or not (out_dir / c["output"]).exists():
            continue
        title = c.get("title") or c["hook"].title()
        items.append({"file": c["output"], "title": title[:100],
                      "description": f"{title}\n\nClip from @{credit}'s stream (used with permission), "
                                     f"{c['source_time']} into the VOD: {plan['url']}&t={int(c['start'])}s\n\n"
                                     f"#gta6 #{credit.lower()} #shorts #gaming #gta",
                      "tags": ["gta 6", "gta vi", credit, "shorts", "gaming", "funny moments"],
                      "score": c["score"]})
    items.sort(key=lambda x: -x["score"])
    (out_dir / "upload.json").write_text(json.dumps(items, indent=2, ensure_ascii=False), encoding="utf-8")


def render(args, p=None, plan=None):
    if plan is None:
        p, plan = load_plan(args.plan)
    t0 = time.time()
    out_dir = p.parent
    (out_dir / "preview").mkdir(exist_ok=True)
    cands = [c for c in plan["candidates"] if c["keep"] and c["score"] >= args.min_score]
    if args.only:
        cands = [c for c in plan["candidates"] if c["id"] in args.only or c["key"] in args.only]
    cands = sorted(cands, key=lambda c: -c["score"])[:args.n or None]
    if not cands:
        print("Nothing to render: no candidate has keep=true and score >= --min-score")
        return

    # HD sections download in the background while earlier clips render
    from concurrent.futures import ThreadPoolExecutor
    pool = ThreadPoolExecutor(3)
    sources = {c["id"]: pool.submit(hd_source, plan, c) for c in cands}
    encoder = pick_encoder()
    tr = Transcriber(plan.get("whisper_model", "small"))
    audio = ROOT / plan["audio"] if not Path(plan["audio"]).is_absolute() else Path(plan["audio"])
    done = 0
    for n, c in enumerate(cands, 1):
        t = time.time()
        a, b, peak = cut_range(c)
        try:
            video, off = sources[c["id"]].result()
        except subprocess.CalledProcessError:
            print(f"[{n}/{len(cands)}] {c['id']}: HD download failed, skipped")
            continue
        work = ROOT / c["work"]
        wav = work / "cut.wav"   # transcribe exactly the cut (it may reach outside the prepared window)
        run(["ffmpeg", "-v", "error", "-y", "-ss", str(c["start"] + a), "-t", str(b - a),
             "-i", str(audio), "-vn", "-ac", "1", "-ar", "16000", str(wav)])
        words = tr.words(wav) if c.get("captions", True) else []
        facecam = None if args.layout == "blur" else (args.facecam or c.get("facecam") or plan.get("facecam"))
        cut = {"start": off, "end": off + (b - a), "peak": round(peak - a, 2), "words": words,
               "credit": plan.get("credit", ""), "sfx": c["sfx"], "facecam": facecam,
               "hook": c["hook"] or random.choice(HOOKS), "intro": c["intro"] or "", "outro": c["outro"] or "",
               "slowmo": c.get("slowmo", True), "mood": c.get("mood", "hype")}
        out = out_dir / f"short_{c['id']}.mp4"
        cap_y = render_clip(video, cut, work / "clip.mkv", work, plan.get("layout_tall", 1.25))
        total, _ = assemble("clip.mkv", cut, cap_y, work, out, encoder, plan.get("voice", VOICE))
        preview_strip(out, out_dir / "preview" / f"short_{c['id']}.jpg")
        c["output"] = out.name
        done += 1
        print(f"[{n}/{len(cands)}] {out.name}  {c['score']}/10, {total:.0f}s, mood {cut['mood']}  "
              f"({time.time() - t:.0f}s)")
    pool.shutdown()
    save_plan(p, plan)
    write_upload(out_dir, plan)
    print(f"Rendered {done} Shorts in {(time.time() - t0) / 60:.1f} min -> {out_dir} (titles in upload.json)")


def auto(args):
    t0 = time.time()
    p, plan = prepare(args)
    rv = Reviewer(use_ai=not args.no_ai)
    for c in plan["candidates"]:
        if c["reviewed_by"] not in ("none", "heuristic"):
            continue
        r = rv.review(ROOT / "output" / plan["video_id"] / c["sheet"], load_words(c), c["loud_score"],
                      c["length"], c["loud_peak"])
        apply_review(c, r)
        print(f"{c['id']} {c['source_time']}  {r.score}/10 {'KEEP' if r.keep else 'drop'} - {r.reason}")
    save_plan(p, plan)
    render(args, p, plan)
    print(f"Done in {(time.time() - t0) / 60:.1f} min")


def peek(args):
    """Denser frame grid over part of a candidate (may reach outside its window), or one full
    frame with a 10% grid for measuring the facecam. Uses HD video when already downloaded."""
    p, plan = load_plan(args.plan)
    c = next(c for c in plan["candidates"] if args.cand in (c["id"], c["key"]))
    a = c["start"] + (args.start if args.start is not None else 0.0)
    b = c["start"] + (args.end if args.end is not None else c["length"])
    video, vstart = ROOT / plan["lowres"], 0.0
    if plan.get("video"):
        video = ROOT / plan["video"]
    elif (ROOT / c["work"] / "section.mp4").exists():
        video, vstart = ROOT / c["work"] / "section.mp4", c["start"]
    if args.frame is not None:
        t = c["start"] + args.frame - vstart
        if t < 0 or (vstart and args.frame > c["length"]):
            video, t = ROOT / plan["lowres"], c["start"] + args.frame
        out = p.parent / "review" / f"{c['id']}_frame_{args.frame:g}.jpg"
        run(["ffmpeg", "-v", "error", "-y", "-ss", str(t), "-i", str(video), "-vf",
             "scale=1280:-2,drawgrid=w=iw/10:h=ih/10:t=2:c=yellow@0.7", "-frames:v", "1", "-q:v", "3", str(out)])
    else:
        if vstart and (a < vstart or b > vstart + c["length"]):
            video, vstart = ROOT / plan["lowres"], 0.0
        out = p.parent / "review" / f"{c['id']}_{args.start or 0:g}-{args.end or c['length']:g}.jpg"
        contact_sheet(video, a - vstart, b - a, out)
    print(out)


def parse_box(text):
    return [float(v) for v in text.split(",")]


def main():
    load_env()
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    def prep_args(sp):
        sp.add_argument("url")
        sp.add_argument("--max", type=int, default=80, help="most candidate moments to prepare (default 80)")
        sp.add_argument("--min-z", type=float, default=0.8,
                        help="loudness score a moment needs (default 0.8; lower finds more, weaker ones)")
        sp.add_argument("--min-live", type=float, default=0.6,
                        help="share of a window that must be live gameplay (default 0.6)")
        sp.add_argument("--len", type=float, default=30, help="seconds per candidate window (default 30)")
        sp.add_argument("--model", default="small", help="whisper model: base/small/medium/large-v3")
        sp.add_argument("--full-max", type=float, default=1800,
                        help="videos longer than this (s) are handled audio-first with per-clip video sections")

    def render_args(sp):
        sp.add_argument("-n", type=int, default=0, help="max Shorts, best scores first (default: all kept)")
        sp.add_argument("--min-score", type=int, default=6, help="score needed to render (default 6)")
        sp.add_argument("--only", nargs="+", help="render just these candidate ids (c03 ...), ignoring keep/score")
        sp.add_argument("--layout", choices=["auto", "blur"], default="auto",
                        help="auto: facecam-over-gameplay when a facecam box is known, else blurred background")
        sp.add_argument("--facecam", type=parse_box, help="force facecam box as x,y,w,h fractions")

    sp = sub.add_parser("prepare", help="scan the whole stream, write every candidate to plan.json for review")
    prep_args(sp)
    sp = sub.add_parser("render", help="render the kept candidates of a plan")
    sp.add_argument("plan", help="video id or path to plan.json")
    render_args(sp)
    sp = sub.add_parser("auto", help="prepare + Claude API (or heuristic) review + render")
    prep_args(sp)
    render_args(sp)
    sp.add_argument("--no-ai", action="store_true", help="skip Claude review, use heuristic scoring")
    sp = sub.add_parser("peek", help="closer frame grid of a candidate, or one gridded frame (--frame)")
    sp.add_argument("plan", help="video id or path to plan.json")
    sp.add_argument("cand", help="candidate id, e.g. c03")
    sp.add_argument("start", type=float, nargs="?", help="seconds into the candidate window (may be negative)")
    sp.add_argument("end", type=float, nargs="?")
    sp.add_argument("--frame", type=float, help="seconds into the candidate; full frame with a 10%% grid")
    args = ap.parse_args()
    {"prepare": prepare, "render": render, "auto": auto, "peek": peek}[args.cmd](args)


if __name__ == "__main__":
    main()
