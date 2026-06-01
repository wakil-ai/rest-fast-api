# How to Configure Speech-to-Text

WakilAI supports two speech-to-text backends: **Azure Cognitive Services** (default, supports WebSocket streaming) and **Google Cloud Speech-to-Text** (REST only). Both accept Uzbek, Russian, and English audio.

## Prerequisites

- Azure Speech account **or** Google Cloud account with Speech-to-Text API enabled
- Access to `.env`

---

## Steps — Azure (Recommended)

### 1. Get Azure credentials

In the Azure portal:
1. Create a **Cognitive Services** resource (Speech).
2. Note the **API key** and **region** (e.g. `eastus`).

### 2. Add credentials to `.env`

```env
SPEECH_TO_TEXT_PROVIDER=azure
AZURE_SPEECH_KEY=your-azure-speech-key
AZURE_SPEECH_REGION=eastus
```

### 3. Restart and test — REST transcription

Upload an audio file:

```bash
curl -X POST http://localhost:8080/api/v2/speech-to-text/transcribe \
  -H "admin: your-api-key" \
  -F "file=@/path/to/audio.wav" \
  -F "language=uz-UZ"
```

Supported `language` values: `uz-UZ`, `ru-RU`, `en-US`.

Expected response:

```json
{
  "transcript": "Mehnat kodeksining 128-moddasi...",
  "language": "uz-UZ",
  "duration_ms": 3200
}
```

### 4. Test WebSocket streaming

WebSocket streaming sends audio chunks in real time and receives partial transcripts:

```
ws://localhost:8080/api/v2/speech-to-text/ws/transcribe?language=uz-UZ
```

Send binary audio frames (PCM 16-bit, 16kHz, mono) and receive JSON messages:

```json
{"type": "partial", "text": "Mehnat ko..."}
{"type": "final", "text": "Mehnat kodeksining 128-moddasi nima deydi?"}
```

---

## Steps — Google Cloud Speech

### 1. Get Google credentials

1. Create a GCP project and enable the **Cloud Speech-to-Text API**.
2. Create a service account with `Speech Client` role.
3. Download the JSON key file.

### 2. Add credentials to `.env`

```env
SPEECH_TO_TEXT_PROVIDER=google
# Google STT uses Application Default Credentials or GOOGLE_APPLICATION_CREDENTIALS
GOOGLE_APPLICATION_CREDENTIALS=/path/to/service-account.json
```

### 3. Test

Same REST endpoint as Azure:

```bash
curl -X POST http://localhost:8080/api/v2/speech-to-text/transcribe \
  -H "admin: your-api-key" \
  -F "file=@/path/to/audio.mp3" \
  -F "language=uz-UZ"
```

> WebSocket streaming is only available with the Azure backend.

---

## Supported audio formats

| Format | Notes |
|--------|-------|
| WAV (PCM) | Best quality, no conversion needed |
| MP3 | Supported by both providers |
| OGG/OPUS | Supported by Google; Azure support varies by region |
| WebM | Supported for browser-recorded audio |

---

## Verification

- [ ] `POST /api/v2/speech-to-text/transcribe` returns a non-empty transcript
- [ ] Language code is recognized (try `uz-UZ`, `ru-RU`)
- [ ] (Azure only) WebSocket connection at `/ws/transcribe` does not immediately close

---

## Troubleshooting

| Problem | Fix |
|---------|-----|
| `AZURE_SPEECH_KEY` not working | Key may be expired or wrong region; re-check in Azure portal |
| Empty transcript | Audio format unsupported; convert to WAV 16kHz mono first |
| WebSocket disconnects immediately | Only Azure backend supports WebSocket; check `SPEECH_TO_TEXT_PROVIDER=azure` |
| `AuthenticationError` from Google | Check `GOOGLE_APPLICATION_CREDENTIALS` path and that the service account has `Speech Client` role |

---

## Related

- [Reference: Environment Variables](reference-environment-variables.md) — `SPEECH_TO_TEXT_PROVIDER`, `AZURE_*`
- [API Reference: Speech-to-Text endpoints](api-reference.md)
