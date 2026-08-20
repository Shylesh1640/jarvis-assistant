# Voice Interface

Speech-to-text (input) and text-to-speech (output), both behind provider
abstractions. **Off by default** — no provider ships enabled.

## How it works

* `VoiceInputProvider` (`transcribe(audio, content_type) -> text`) in
  `src/jarvis/voice/base.py`, registered in `VOICE_INPUT_PROVIDERS`.
* `VoiceOutputProvider` (`synthesize(text) -> (audio, media_type)`)
  registered in `VOICE_OUTPUT_PROVIDERS`.

Enable each capability independently:

```
VOICE_INPUT_ENABLED=true
VOICE_INPUT_PROVIDER=whisper_local
VOICE_OUTPUT_ENABLED=true
VOICE_OUTPUT_PROVIDER=edge_tts
VOICE_CREDENTIALS_PATH=./credentials/voice.json
```

Until configured, `/voice` routes return a structured
`503 voice_not_configured` response and never contact a speech API.

## API

| Method | Path | Body |
|---|---|---|
| POST | `/voice/transcribe` | multipart `audio` file (≤25 MB) |
| POST | `/voice/synthesize` | `{"text": "hello"}` → audio bytes |

## Safety

* Off by default; explicit provider config required.
* Providers read credentials from `VOICE_CREDENTIALS_PATH` — never stored
  in the DB or logged.
* Audio data is never logged. Empty/oversized audio is rejected.