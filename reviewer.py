"""Clip reviewer: Claude watches a frame grid + reads the transcript of each candidate
and decides whether it's a real Short-worthy moment, how to trim it, and which SFX fits.
Falls back to a transcript/loudness heuristic when no Anthropic credentials are set."""
import base64
import random
import subprocess
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

MODEL = "claude-opus-5-5"
GRID_COLS, GRID_ROWS = 4, 3
# bold font for the grid labels (ffmpeg drawtext needs the Windows drive colon escaped)
FONT = next((f.replace(":", "\\:", 1) for f in ["C:/Windows/Fonts/arialbd.ttf",
                                                 "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
                                                 "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"]
             if Path(f).exists()), "")
HYPE = {"WHAT", "NO", "BRO", "BRUH", "CHAT", "GOD", "YO", "WAIT", "RUN", "HELP", "STOP", "WHY",
        "HOW", "OH", "NAH", "DUDE", "AYO", "LETS", "GO"}
HOOKS = ["WAIT FOR IT...", "HE WAS NOT READY", "WATCH TILL THE END", "THIS WENT WRONG FAST"]
INTROS = ["Speed had no idea what was about to happen.", "This was supposed to be the easy part.",
          "Chat was not ready for what happened next.", "Nobody expected this to go so wrong."]

SYSTEM = """You are the editor for a GTA clip channel that cuts streams into IShowSpeed-style YouTube Shorts.
For each candidate you get a grid of frames sampled evenly across the clip (left-to-right, top-to-bottom,
each labelled with its time in seconds), the transcript with timestamps, and where the loudness spike is.

Keep a clip only if a viewer scrolling past would stop and watch it to the end:
- a real reaction: screaming, laughing, raging, shock, a funny line - the streamer's emotion is the product
- gameplay that pays it off: crash, explosion, getting wasted, police chaos, a ridiculous fail or clutch escape
- understandable on its own within the clip, with no outside context needed

Reject: loading/menu/map screens, dead air, ambient gunfire or music with no reaction, sponsor reads,
reading chat calmly, long setup with no payoff, anything where the loud part is just game audio.
Be strict - a low keep rate is fine; a boring Short hurts the channel more than a missing one.

Trim to the tightest cut that still has the setup: start 2-6 s before the action builds, end 1.5-4 s
after the reaction finishes. The cut must be at least 12 s long.

Each Short opens with a 1 s flash-forward of the peak, then a narrator (text-to-speech) sets up the clip
over a rewind before it plays. Write the narrator intro like a viral Shorts narrator: one or two short
sentences, at most 14 words, present or past tense, builds tension and teases the payoff without saying
it. The outro is optional (empty string for none), at most 8 words, said over the last seconds."""

# keep in sync with the folders fetch_sfx.py fills
SfxChoice = Literal["boom", "explosion", "fail", "buzzer", "win", "whoosh", "bonk", "laugh", "none"]
# keep in sync with the folders fetch_bgm.py fills
Mood = Literal["hype", "suspense", "funny", "chill", "epic"]


class FaceBox(BaseModel):
    x: float = Field(description="left edge, fraction of frame width 0-1")
    y: float = Field(description="top edge, fraction of frame height 0-1")
    w: float = Field(description="width, fraction of frame width 0-1")
    h: float = Field(description="height, fraction of frame height 0-1")


class Review(BaseModel):
    keep: bool = Field(description="true only if this is worth posting as a Short")
    score: int = Field(description="1-10; 8+ means a likely viral moment, 5 is forgettable")
    reason: str = Field(description="one sentence on what happens and why it does or does not work")
    hook: str = Field(description="on-screen hook for the first 3 s, ALL CAPS, max 6 words, no emoji, "
                                  "teases the payoff without spoiling it")
    trim_start: float = Field(description="seconds from clip start where the Short should begin")
    trim_end: float = Field(description="seconds from clip start where the Short should end")
    peak: float = Field(description="seconds from clip start of the single biggest reaction moment; "
                                    "the zoom punch, flash and SFX land here")
    sfx: SfxChoice = Field(description="sound effect for the peak: boom=shock/jump scare, explosion=crash or "
                                       "things blowing up, fail=sad L/death, buzzer=dumb mistake ('bruh' moment), "
                                       "win=W/clutch escape, whoosh=fast action/chase, bonk=getting hit or ragdoll, "
                                       "laugh=he says something funny, none if the moment is already loud enough")
    title: str = Field(description="YouTube Shorts title, under 70 characters")
    intro: str = Field(description="narrator setup line, max 14 words, teases without spoiling")
    outro: str = Field(description="optional narrator line over the ending, max 8 words, or empty")
    mood: Mood = Field(description="background music: hype=chaos/action, suspense=build-up/tense, "
                                   "funny=fails/goofy, chill=calm banter, epic=big win or mission complete")
    slowmo: bool = Field(description="slow the peak down to half speed (good for crashes, hits, jump scares)")
    captions: bool = Field(description="burn word captions; false when most speech is game dialogue, not the streamer")
    facecam: FaceBox | None = Field(description="the streamer's webcam overlay within ONE frame (each grid cell is a "
                                                "full frame), as fractions of that frame; only the camera image, not "
                                                "the counters or borders around it; null if there is no facecam")


def contact_sheet(video, start, length, out):
    n = GRID_COLS * GRID_ROWS
    font = f"fontfile='{FONT}':" if FONT else ""
    vf = (f"fps={n}/{length:.3f},scale=384:216:force_original_aspect_ratio=decrease,pad=384:216:-1:-1,"
          f"drawtext={font}text='%{{eif\\:t\\:d}}s':x=8:y=8:fontsize=26:fontcolor=yellow:"
          f"box=1:boxcolor=black@0.6:boxborderw=4,tile={GRID_COLS}x{GRID_ROWS}")
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", str(start), "-t", f"{length:.3f}", "-i", str(video),
                    "-vf", vf, "-frames:v", "1", "-q:v", "4", str(out)], check=True)


def transcript_text(words):
    lines, cur, t0 = [], [], None
    for w in words:
        if cur and (w.start - prev_end > 0.8 or len(cur) >= 14):
            lines.append(f"[{t0:5.1f}s] {' '.join(cur)}")
            cur = []
        if not cur:
            t0 = w.start
        cur.append(w.word.strip())
        prev_end = w.end
    if cur:
        lines.append(f"[{t0:5.1f}s] {' '.join(cur)}")
    return "\n".join(lines) or "(no speech detected)"


def heuristic(words, loud_score, length, peak):
    text = [w.word.strip().upper().strip(".,!?") for w in words]
    hype = sum(t in HYPE for t in text)
    excited = sum(w.word.strip()[-1:] in "!?" for w in words if w.word.strip())
    wps = len(words) / max(length, 1)
    score = round(min(10, max(1, 2 + loud_score + 0.7 * hype + 0.3 * excited + min(wps, 3))))
    return Review(keep=score >= 5, score=score, reason=f"heuristic: {hype} hype words, {wps:.1f} words/s",
                  hook=random.choice(HOOKS), trim_start=0, trim_end=length, peak=peak,
                  sfx="boom", title="", facecam=None, intro=random.choice(INTROS), outro="", mood="hype",
                  slowmo=True, captions=True)


class Reviewer:
    def __init__(self, use_ai=True):
        self.client = None
        if use_ai:
            import anthropic
            self.anthropic = anthropic
            self.client = anthropic.Anthropic()

    def review(self, sheet, words, loud_score, length, peak):
        if self.client is None:
            return heuristic(words, loud_score, length, peak)
        prompt = (f"Clip length: {length:.1f} s. Loudness spike at {peak:.1f} s.\n\n"
                  f"Transcript:\n{transcript_text(words)}")
        image = base64.standard_b64encode(sheet.read_bytes()).decode()
        a = self.anthropic
        try:
            resp = self.client.beta.messages.parse(
                model=MODEL,
                max_tokens=16000,
                betas=["server-side-fallback-2026-07-01"],
                fallbacks="default",
                output_config={"effort": "medium"},
                system=SYSTEM,
                messages=[{"role": "user", "content": [
                    {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": image}},
                    {"type": "text", "text": prompt},
                ]}],
                output_format=Review,
            )
        except (a.AuthenticationError, a.PermissionDeniedError, TypeError) as e:
            # TypeError: SDK raises it when no API key / auth token / profile is configured
            print(f"  Claude review unavailable ({e.__class__.__name__}); using heuristic scoring from now on")
            self.client = None
            return heuristic(words, loud_score, length, peak)
        except (a.RateLimitError, a.APIStatusError, a.APIConnectionError) as e:
            print(f"  Claude review failed for this clip ({e.__class__.__name__}); heuristic used")
            return heuristic(words, loud_score, length, peak)
        if resp.stop_reason == "refusal" or resp.parsed_output is None:
            return heuristic(words, loud_score, length, peak)
        return resp.parsed_output
