---
description: Turn a stream link into every production-ready GTA Short in it
---
1. If `python doctor.py` has never passed on this machine, run it and fix anything marked XX (see SETUP.md).
2. Run `python gta_clipper.py prepare "<the link the user gave>"` and wait for it to finish (several minutes).
3. Review every candidate in output/<video_id>/plan.json exactly as AGENTS.md describes: open each sheet image, read the transcript, set the fields, set the top-level facecam once.
4. Run `python gta_clipper.py render <video_id>`.
5. Open every preview strip in output/<video_id>/preview/, fix plan.json and re-render with --only where needed.
6. Report the Shorts (path, one line, score), the rejects and the total time; point to upload.json.
