Cached ElevenLabs clips for `callout` messages (T28), served as `/audio/<key>.mp3`.

`backend/api/voice.py` names each clip by a hash of voice, model and text. Fill the cache once on a laptop that has
`ELEVENLABS_API_KEY` set: `python -m backend.api.voice prewarm` (plays the recorded storm headless and synthesizes
every callout). Commit the clips only if they are small. Without clips or a key the demo shows captions only.

Shipped clips (2026-11-08, human B): the six `fault-storm` callouts (SAT-12, SAT-07, SAT-19: alert + agent
narration each), voice `JBFqnCBsd6RMkjVDRZzb` (premade), model `eleven_flash_v2_5`, 1.28 MB in total, about
1,000 characters billed once. Audio is generated speech of simulated-scenario text; the replay server needs no key.
