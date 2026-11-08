Cached ElevenLabs clips for `callout` messages (T28), served as `/audio/<key>.mp3`.

`backend/api/voice.py` names each clip by a hash of voice, model and text. Fill the cache once on a laptop that has
`ELEVENLABS_API_KEY` set: `python -m backend.api.voice prewarm` (plays the recorded storm headless and synthesizes
every callout). Commit the clips only if they are small. Without clips or a key the demo shows captions only.
