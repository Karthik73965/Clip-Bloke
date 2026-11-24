# GTA-Bloke: you are the clip editor

The user gives you a stream link. Your job is to find **every** GTA moment in it that is worth posting
and turn each one into a production-ready vertical Short (narrated, captioned, with music). The tool
does the mechanical work. **You** do the editorial work: watch each candidate, decide if it is worth
posting, and write the hook and narration. You can see images, so look at the frames yourself.

## When the user pastes a link

If the user pastes a YouTube link (with or without "get me clips"), run the whole workflow below end
to end **without stopping to ask**. Commands like `prepare` and `render` take minutes, so let them
finish. The only reason to stop early is a failed `python doctor.py` check (see SETUP.md). At the end,
report the total time.

## Workflow

1. `python gta_clipper.py prepare "<stream url>"`
   - Long streams download audio plus a 144p copy (about 230 MB for 6 h) and scan the **whole** stream:
     loudness bursts, keeping only those that land on live gameplay. Pause, save and map menus, phone
     screens, loading and black screens are skipped automatically.
   - Up to 80 candidates are transcribed on the GPU.
   - It writes `output/<video_id>/plan.json` and a 4x3 frame grid per candidate in
     `output/<video_id>/review/<key>.jpg`. Grids come from the 144p copy, so they are blurry but enough
     to judge. Each cell is labelled with its second inside the 30 s window.
   - On a 6 h stream it takes about 5–8 min. No HD video is downloaded yet.
2. Review **every** candidate in `plan.json`. Don't stop after finding a few good ones: the goal is all
   of them.
   - Open the `sheet` image (the path is relative to `output/<video_id>/`) and read the `transcript`.
   - For a closer look, use `python gta_clipper.py peek <video_id> c03 10 20` (a denser grid of seconds
     10–20; the range may go outside the window, e.g. `-15 0` to see what came before) or
     `python gta_clipper.py peek <video_id> c03 --frame 12` (one frame with a 10% grid).
   - Edit the fields below and set `"reviewed_by"` to your tool name. Reviewing 80 candidates is a lot,
     so batch the reading, but look at every sheet.
   - Set the facecam **once** at the top level (`"facecam": [x, y, w, h]`), measured with `--frame` on a
     clearly visible frame. All Shorts from a stream share it.
3. `python gta_clipper.py render <video_id>`
   - Renders every candidate with `keep: true` and `score >= 6`, best first.
   - HD video is downloaded only for those cuts, 3 at a time in the background while others render.
   - About 20–40 s per Short. `--only c03 c07` re-renders specific ones.
4. **Check every render.** Open `output/<video_id>/preview/short_<id>.jpg`, a strip of 8 frames across
   the finished Short. Fix `plan.json` and re-render with `--only` if:
   - the facecam crop is wrong,
   - the cut starts or ends in the wrong place,
   - a menu or loading screen slipped in,
   - a caption collides with something.
5. `output/<video_id>/upload.json` has a title, description and tags per Short, ready for YouTube.
   Report back to the user:
   - each Short with its path, a one-line description and its score,
   - how many candidates you rejected, and the main reasons why.

## Fields you set (per candidate)

| field | meaning |
|---|---|
| `keep` | `true` only if a viewer scrolling past would stop and watch to the end |
| `score` | 1–10. 8+ = likely viral, 6–7 = solid, 5 = forgettable. Be strict. |
| `reason` | one sentence: what happens and why it does or doesn't work |
| `trim_start`, `trim_end` | seconds relative to the window start. They **may go outside the window**, up to 20 s either way (e.g. `-8` to include the setup before it), because the HD cut is downloaded exactly. Aim for 12–30 s: start 2–6 s before the action builds, end 1.5–4 s after the reaction. |
| `peak` | second of the single biggest reaction, on the same clock as the trims. The flash-forward, zoom punch, flash, SFX and slow-mo all land here, so be precise. Use `peek`. |
| `hook` | text over the opening flash-forward. ALL CAPS, max 6 words. Teases the payoff and doesn't spoil it. |
| `intro` | narrator setup line, spoken by TTS after the rewind: max 14 words, 1–2 short sentences. |
| `outro` | optional narrator line over the last seconds, max 8 words, or `""` |
| `mood` | background music: `hype` (chaos/action), `suspense` (build-up), `funny` (fails, goofy), `chill` (banter), `epic` (big win, mission complete) |
| `sfx` | sound on the peak: `boom` (shock), `explosion`, `fail` (sad L/death), `buzzer` (dumb mistake), `win`, `whoosh`, `bonk` (hit/ragdoll), `laugh`, `none` |
| `slowmo` | `true` to play the peak at half speed. Good for crashes, hits and jump scares; bad for punchlines. |
| `captions` | `false` when most of the speech is game/cutscene dialogue rather than the streamer |
| `title` | YouTube title, under 70 characters, curiosity-driven, may end with one emoji |
| `facecam` | per-candidate override of the top-level facecam; normally leave `null` |

Leave `key`, `start`, `length`, `work`, `sheet`, `transcript`, `live` and `loud_*` alone.

When two candidates are parts of the same moment, keep one and extend its trims to cover the moment.

## What makes a good Short

Keep:
- A real reaction (screaming, laughing, raging, shock, a funny line). The streamer's emotion is the product.
- Gameplay that pays the reaction off: a crash, an explosion, getting wasted, police chaos, a ridiculous fail
  or a clutch escape.
- Clips that make sense on their own with no outside context.

Reject:
- Leftover menus or loading screens.
- Cutscenes where only game characters talk.
- Dead air, reading chat calmly or sponsor reads.
- Long setup with no payoff.
- Clips where the loud part is just game audio.
- Non-GTA segments (IRL, just chatting).

A low keep rate is fine. A boring Short hurts the channel more than a missing one.

## Narration style

The narrator is the "storyteller" voice of viral gaming Shorts: short, punchy and building tension.
- Good: "Speed thought the final mission would be easy. Rocko had other plans."
- Good: "One wrong turn. That's all it took."
- Bad: "In this clip, IShowSpeed plays GTA 6 and something funny happens." (flat, and it spoils nothing worth watching)

Don't repeat the hook word for word, and don't swear. Vary the lines across Shorts from the same
stream. The outro can ask a question ("Would you have made that jump?") or be left empty.

## Assets

- SFX: `assets/sfx/<category>/`, filled by `python fetch_sfx.py`.
- Music: `assets/bgm/<mood>/`, filled by `python fetch_bgm.py`. These are Mixkit tracks: free for
  monetised YouTube with no attribution, but don't register them with Content ID.
- Narrator voice: the top-level `voice` in `plan.json` (edge-tts, e.g. `en-US-AndrewMultilingualNeural`,
  `en-US-GuyNeural` or `en-US-ChristopherNeural`).
