# Setup on a new laptop

Tested on Windows 11 with an NVIDIA RTX 4050. It works without an NVIDIA GPU too; transcription is just
slower on the CPU.

## 1. Get the code

```powershell
git clone <your repo url> GTA-Bloke
cd GTA-Bloke
```

## 2. One-shot setup (about 10 minutes)

```powershell
powershell -ExecutionPolicy Bypass -File setup.ps1
```

This does four things:

1. **System tools.** Installs whichever of these are missing, using `winget`:
   - Python 3.11
   - ffmpeg (Gyan full build, which has the subtitle, transition and audio filters the editor needs)
   - Node.js (yt-dlp uses it to get past YouTube's player checks)
2. **Python packages.** Installs everything in `requirements.txt`: yt-dlp, faster-whisper plus the CUDA 12
   / cuDNN 9 libraries for the GPU, edge-tts, the Anthropic SDK and numpy.
3. **Sound effects and music.** Downloads 32 SFX and 26 tracks from Mixkit. They're free for monetised
   YouTube, but the licence doesn't allow putting the raw files in a repo, so they're fetched on each
   machine.
4. **Checks.** Runs `python doctor.py` to confirm everything works, including a real narrator line and a
   GPU transcription.

If `winget` installed something, **close the terminal, open a new one** and run `setup.ps1` again so the
new tools are on PATH. The script is safe to rerun.

When doctor ends with `All good`, you're ready.

### Manual setup (macOS / Linux, or if the script fails)

```bash
# macOS:  brew install python@3.11 ffmpeg node
# Ubuntu: sudo apt install python3.11 ffmpeg nodejs
python -m pip install -r requirements.txt
python fetch_sfx.py
python fetch_bgm.py
python doctor.py
```

The ffmpeg build needs libass (subtitles) and the `xfade`, `sidechaincompress` and `rgbashift` filters.
Doctor tells you if one is missing.

## 3. Make clips

Open the `GTA-Bloke` folder in your coding agent, paste a stream link and ask for clips:

| Agent | How it picks up the instructions | What to type |
|---|---|---|
| **Antigravity** | `.agent/rules/gta-bloke.md` (always on) and `GEMINI.md`, which point to `AGENTS.md` | paste the link + "get me clips", or `/get-clips <link>` |
| **Codex** | `AGENTS.md` | paste the link + "get me clips" |
| **Claude Code** | `CLAUDE.md`, which imports `AGENTS.md` | paste the link + "get me clips" |

Let the agent run terminal commands (allow `python`) so it can work without asking each time. A 6-hour
stream takes about:
- 5–8 min to scan,
- however long the agent needs to review about 80 candidates,
- 20–40 s per rendered Short.

The results go to `output/<video_id>/`:

| File | What it is |
|---|---|
| `short_cXX.mp4` | the finished Shorts (1080x1920, 30 fps, -14 LUFS) |
| `upload.json` | title, description and tags for each Short |
| `preview/` | a frame strip of each Short |
| `review/` | the frame grids the agent judged |
| `plan.json` | every editing decision; edit it and run `render --only cXX` to change one Short |

## Without an agent

```powershell
python gta_clipper.py prepare "<link>"     # then edit output/<id>/plan.json yourself
python gta_clipper.py render <video_id>
```

Or let the Claude API do the review (it needs `ANTHROPIC_API_KEY=...` in a `.env` file in this folder):

```powershell
python gta_clipper.py auto "<link>"
```

## Troubleshooting

- **`yt-dlp` errors such as "Sign in to confirm" or "JS challenge":** run `python -m pip install -U yt-dlp`.
  YouTube changes often, and yt-dlp updates to match.
- **The stream is still live:** run it after the stream ends and the VOD is processed.
- **Whisper says "GPU whisper unavailable":** update your NVIDIA driver. The run still works on the CPU.
- **NVENC isn't used:** the driver is older than version 610. Encoding falls back to x264 automatically, which
  is just slower.
- **Disk space:** about 0.5 GB per 6 h stream (audio + 144p scan) plus about 30 MB per kept clip. Delete
  `downloads/`, `work/` and `output/` folders you no longer need.
