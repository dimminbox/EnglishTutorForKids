# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A single-page browser app where kids practice English with "Lily", an animated AI tutor. Two files, no build step, no dependencies beyond the Python standard library:

- `alex-tutor.html` — the entire frontend (markup, CSS, JS, and an animated inline SVG avatar) in one file.
- `server.py` — a stdlib `http.server` proxy that serves the HTML and forwards chat/TTS requests to external APIs.

## Running

Speech-to-text uses `faster-whisper`, installed in a local venv:

```bash
.venv/bin/python server.py
# then open http://localhost:8080/alex-tutor.html
```

(Plain `python3 server.py` works too, but then the `/proxy/stt` route fails because `faster_whisper` isn't on the system interpreter — use the venv.)

There are no tests, linter, or build. The server binds port `8080` and routes all outbound traffic through an HTTP proxy hardcoded at `127.0.0.1:1080` (`HTTP_PROXY`/`HTTPS_PROXY` set at the top of `server.py`) — requests will hang or fail if that local proxy isn't running, so remove/adjust those two lines when working without it. Note this proxy also applies to the first-time Whisper model download from HuggingFace; the model is then cached in `~/.cache/huggingface` and needs no network afterward.

## Configuration (all in `server.py`, top of file)

- `AI_BACKEND` — `'deepseek'` (default) or `'claude'`. Selects which handler `_handle_anthropic` dispatches to.
- `DEEPSEEK_KEY` — DeepSeek API key.
- `ELEVENLABS_KEY` — optional; if left as the placeholder containing `ВСТАВЬ`, the ElevenLabs voice route returns 400 and the frontend falls back to browser TTS.
- On startup the server refuses to run if the selected backend's key is still a placeholder.

## Architecture

The frontend always speaks the **Anthropic Messages API shape** (`{system, messages:[{role,content}]}` in, `{content:[{type:'text',text}]}` out). The server's job is to translate that shape to/from whichever backend is active:

- `_handle_deepseek` — prepends `system` as a `{role:'system'}` message, calls DeepSeek's OpenAI-style `/chat/completions`, then repackages the reply back into Anthropic shape.
- `_handle_claude_cli` — flattens the message history into a prompt string and shells out to the local `claude` CLI (`claude -p ... --output-format json`). Uses Claude Code's own auth, so no API key needed for this path.

So a new backend = a new `_handle_*` method that consumes/produces the Anthropic shape; the frontend never changes.

### Server routes
- `GET /alex-tutor.html` (+ static files) — served from the script's directory.
- `POST /proxy/chat` — chat completion (dispatches by `AI_BACKEND`).
- `POST /proxy/stt` — raw audio body (webm/opus blob) in, `{text, language}` out. Runs faster-whisper locally with `language=None` (auto-detection), so a child can speak English or Russian without any toggle. Model is lazy-loaded once via `get_whisper()`; size set by `WHISPER_MODEL` (default `small`).
- `POST /proxy/elevenlabs/<voice_id>` — forwards a TTS request to ElevenLabs, returning `audio/mpeg`.
- All responses carry permissive CORS headers; `OPTIONS` is handled.

### Frontend behavior worth knowing
- The tutor's persona and correction rules live in the `SYSTEM` prompt constant in the inline `<script>`. The model is instructed to append a line starting `CORRECTION:` when it spots a grammar/spelling mistake; the JS splits on that marker and renders the correction in a separate amber box.
- Conversation `history` is kept client-side and resent in full on every turn — there is no server-side session.
- Voice input is **hold-to-talk**: pressing the 🎤 button records via `MediaRecorder`; releasing posts the audio blob to `/proxy/stt`, and the returned transcript is dropped into the input and auto-sent through the normal `send()` path. The button is hidden if the browser lacks `MediaRecorder`. `getUserMedia` requires localhost or HTTPS.
- Three voice modes (toggle in the UI): **Browser TTS** (Web Speech API), **ElevenLabs** (via the proxy route, multilingual voice "Rachel"), and **Mute**. Browser TTS uses `splitByLang`/`detectLang` to segment mixed Russian/English text and pick a per-segment voice.
- The SVG avatar animates via CSS keyframes (bob, blink, hair sway) plus JS-driven lip-sync (`lip()`) and a "thinking" dot indicator (`face()`), synced to chat status.
- The tutor is bilingual by design: English by default, switches to Russian when the user writes/asks in Russian.
