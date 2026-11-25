# GTA-Bloke

Paste a GTA stream link, get every postable moment back as a narrated, captioned vertical Short.

```
stream link
  └─ prepare  scan the whole stream: audio loudness + 144p video (menus, loading and black screens
  │           are skipped) -> up to 80 candidates, GPU transcripts, frame grids
  └─ review   a coding agent (Antigravity / Codex / Claude Code) looks at every candidate, keeps
  │           the real moments, sets the cut, peak and facecam, writes the hook, narration and title
  └─ render   HD only for kept cuts -> 1080x1920 Shorts + upload.json
```

**No repeats.** Every delivered Short and every rejected moment is recorded in `history/<video_id>.json`,
which the tool commits and pushes, so every laptop and agent shares it. Paste the same stream again
and you get only new moments (it digs into quieter ones each pass). `render` refuses a cut that repeats
a delivered Short. `python gta_clipper.py history [<id>] [--all]` shows what's been given.

Each Short is built like this:

| Section | What happens |
|---|---|
| Flash-forward | 1.2 s of the peak with the hook text |
| Freeze | glitch effect |
| Rewind | VHS-style rewind |
| Narrator setup | edge-tts voice with word captions over a blurred slow-mo background |
| Transition | zoom transition with a whoosh |
| Clip | streamer captions word by word, zoom punch, shake and flash, the peak in slow-mo with an SFX hit |
| Outro | optional narrator outro |

Under all of it: background music chosen by mood, ducked under the voices and dropped out on the peak.
The layout is the facecam stacked over the gameplay.

**Setup:** see [SETUP.md](SETUP.md). **Agent instructions:** [AGENTS.md](AGENTS.md).

| File | What it does |
|---|---|
| `gta_clipper.py` | the pipeline: `prepare`, `render`, `peek`, `auto`, `history` |
| `history/` | what was already delivered or rejected, per stream (committed; don't edit) |
| `reviewer.py` | the review schema, frame grids, and the optional Claude API reviewer |
| `fetch_sfx.py`, `fetch_bgm.py` | download the free Mixkit SFX and music |
| `doctor.py` | checks the install |
| `setup.ps1` | one-shot Windows setup |

Only clip streams you have permission to use, and credit the streamer. The description in
`upload.json` does this for you.
