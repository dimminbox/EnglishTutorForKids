#!/usr/bin/env python3
"""
Прокси-сервер для alex-tutor.html
Использует Claude Code CLI для авторизации — отдельный API-ключ не нужен.
Запуск: python3 server.py
Открой:  http://localhost:8080/alex-tutor.html
"""
import http.server
import subprocess
import urllib.request
import urllib.error
import os
import sys
import json

# Proxy settings
os.environ['HTTP_PROXY'] = 'http://127.0.0.1:1080'
os.environ['HTTPS_PROXY'] = 'http://127.0.0.1:1080'

PORT = 8080
DIRECTORY = os.path.dirname(os.path.abspath(__file__))

# API backend — выбери один: 'deepseek' или 'claude'
AI_BACKEND = 'deepseek'

# DeepSeek API key (https://platform.deepseek.com)
DEEPSEEK_KEY = "sk-0743bbd7ff9141109bbb8282d5f5cc22"

# ElevenLabs ключ — вставь свой (или оставь пустым для Browser TTS)
ELEVENLABS_KEY = "ВСТАВЬ_СЮДА_ELEVENLABS_KEY"

# STT-движок по умолчанию: 'whisper' (локально) или 'deepgram' (облако).
# Из UI можно переключать на каждый запрос (?engine=...), это лишь дефолт.
STT_ENGINE = 'whisper'

# Deepgram API key (https://console.deepgram.com) — для облачного STT.
# Модель nova-3 + language=multi: авто RU+EN, в т.ч. смена языка внутри фразы.
DEEPGRAM_KEY = "d97bb6e358f8e63f784bab087863677b72382e3b"

# Whisper (faster-whisper) — локальное распознавание речи с авто-определением языка.
# 'base' — быстрее, 'small' — баланс, 'medium'/'large-v3' — точнее (желательно GPU).
WHISPER_MODEL = 'small'

_whisper = None
def get_whisper():
    """Лениво грузим модель один раз при первом запросе на распознавание."""
    global _whisper
    if _whisper is None:
        from faster_whisper import WhisperModel
        print(f'  ⏳ Загружаю Whisper "{WHISPER_MODEL}" (первый раз — может скачаться модель)...', file=sys.stderr)
        _whisper = WhisperModel(WHISPER_MODEL, device='cpu', compute_type='int8')
        print('  ✓ Whisper готов', file=sys.stderr)
    return _whisper

class ProxyHandler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=DIRECTORY, **kwargs)

    def add_cors(self):
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')

    def do_OPTIONS(self):
        self.send_response(200)
        self.add_cors()
        self.end_headers()

    def do_POST(self):
        from urllib.parse import urlsplit
        path = urlsplit(self.path).path   # без query (?engine=...)
        if path == '/proxy/chat':
            self._handle_anthropic()
        elif path == '/proxy/stt':
            self._handle_stt()
        elif path.startswith('/proxy/elevenlabs/'):
            voice_id = path.replace('/proxy/elevenlabs/', '')
            length = int(self.headers.get('Content-Length', 0))
            body = self.rfile.read(length)
            self._forward_elevenlabs(voice_id, body)
        else:
            self.send_error(404)

    def _handle_anthropic(self):
        length = int(self.headers.get('Content-Length', 0))
        body = self.rfile.read(length)
        payload = json.loads(body)

        # backend можно переключать из UI (поле "backend"); иначе берём значение по умолчанию
        backend = payload.get('backend') or AI_BACKEND
        if backend == 'deepseek':
            self._handle_deepseek(payload)
        else:
            self._handle_claude_cli(payload)

    def _handle_deepseek(self, payload):
        messages = payload.get('messages', [])
        system = payload.get('system', '')
        max_tokens = payload.get('max_tokens', 512)

        if system:
            messages = [{"role": "system", "content": system}] + messages

        ds_payload = {
            "model": "deepseek-chat",
            "messages": messages,
            "max_tokens": max_tokens,
        }

        print(f'  → DeepSeek API', file=sys.stderr)
        try:
            req_body = json.dumps(ds_payload).encode()
            req = urllib.request.Request(
                'https://api.deepseek.com/chat/completions',
                data=req_body,
                headers={
                    'Content-Type': 'application/json',
                    'Authorization': f'Bearer {DEEPSEEK_KEY}',
                },
                method='POST'
            )
            with urllib.request.urlopen(req) as resp:
                ds_data = json.loads(resp.read())

            reply_text = ds_data['choices'][0]['message']['content']

            # Конвертируем в формат Anthropic API (которого ожидает frontend)
            response = {
                "content": [{"type": "text", "text": reply_text}],
                "model": "deepseek-chat",
                "role": "assistant"
            }
            resp_bytes = json.dumps(response).encode()
            self.send_response(200)
            self.add_cors()
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(resp_bytes)))
            self.end_headers()
            self.wfile.write(resp_bytes)

        except urllib.error.HTTPError as e:
            err_body = e.read()
            print(f'  ✗ DeepSeek {e.code}: {err_body[:200]}', file=sys.stderr)
            self.send_response(e.code)
            self.add_cors()
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(err_body)
        except Exception as e:
            print(f'  ✗ {e}', file=sys.stderr)
            err = json.dumps({"error": {"type": "error", "message": str(e)}}).encode()
            self.send_response(500)
            self.add_cors()
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(err)

    def _handle_claude_cli(self, payload):
        messages = payload.get('messages', [])
        system = payload.get('system', '')

        prompt_parts = []
        for msg in messages:
            role = msg['role']
            content = msg['content']
            if role == 'user':
                prompt_parts.append(f"User: {content}")
            elif role == 'assistant':
                prompt_parts.append(f"Assistant: {content}")
        prompt = '\n'.join(prompt_parts)

        print(f'  → claude CLI', file=sys.stderr)
        try:
            cmd = ['claude', '-p', prompt, '--output-format', 'json']
            if system:
                cmd += ['--system-prompt', system]
            # Запускаем в нейтральной директории, чтобы CLI не подтягивал CLAUDE.md проекта
            # — иначе тутор «знал» бы про кодовую базу и сравнение моделей было бы нечестным.
            import tempfile
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=60,
                                    cwd=tempfile.gettempdir())
            if result.returncode != 0:
                raise RuntimeError(result.stderr or 'claude CLI error')
            cli_out = json.loads(result.stdout)
            reply_text = cli_out.get('result', cli_out.get('content', str(cli_out)))
            response = {
                "content": [{"type": "text", "text": reply_text}],
                "model": "claude", "role": "assistant"
            }
            resp_bytes = json.dumps(response).encode()
            self.send_response(200)
            self.add_cors()
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(resp_bytes)))
            self.end_headers()
            self.wfile.write(resp_bytes)
        except Exception as e:
            print(f'  ✗ {e}', file=sys.stderr)
            err = json.dumps({"error": {"type": "error", "message": str(e)}}).encode()
            self.send_response(500)
            self.add_cors()
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(err)

    def _send_json(self, obj, status=200):
        resp_bytes = json.dumps(obj).encode()
        self.send_response(status)
        self.add_cors()
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(resp_bytes)))
        self.end_headers()
        self.wfile.write(resp_bytes)

    def _handle_stt(self):
        from urllib.parse import urlsplit, parse_qs
        engine = parse_qs(urlsplit(self.path).query).get('engine', [None])[0] or STT_ENGINE

        length = int(self.headers.get('Content-Length', 0))
        audio = self.rfile.read(length)
        content_type = self.headers.get('Content-Type') or 'audio/webm'

        print(f'  → STT [{engine}] ({len(audio)} bytes)', file=sys.stderr)
        try:
            if engine == 'deepgram':
                text, language = self._stt_deepgram(audio, content_type)
            else:
                text, language = self._stt_whisper(audio)
            print(f'  ✓ [{language}] {text!r}', file=sys.stderr)
            self._send_json({"text": text, "language": language})
        except urllib.error.HTTPError as e:
            err_body = e.read()
            print(f'  ✗ STT {e.code}: {err_body[:200]}', file=sys.stderr)
            self._send_json({"error": {"type": "error",
                             "message": err_body.decode('utf-8', 'replace')}}, status=e.code)
        except Exception as e:
            print(f'  ✗ {e}', file=sys.stderr)
            self._send_json({"error": {"type": "error", "message": str(e)}}, status=500)

    def _stt_whisper(self, audio):
        import tempfile
        # Whisper читает файл, поэтому пишем blob во временный .webm
        with tempfile.NamedTemporaryFile(suffix='.webm', delete=False) as f:
            f.write(audio)
            path = f.name
        try:
            model = get_whisper()
            # language=None → авто-определение языка (ru / en и т.д.)
            segments, info = model.transcribe(path, language=None, beam_size=1)
            text = ''.join(seg.text for seg in segments).strip()
            return text, info.language
        finally:
            os.unlink(path)

    def _stt_deepgram(self, audio, content_type):
        if not DEEPGRAM_KEY or 'ВСТАВЬ' in DEEPGRAM_KEY:
            raise RuntimeError('Deepgram ключ не задан в server.py (DEEPGRAM_KEY)')
        from urllib.parse import urlencode
        params = urlencode({
            'model': 'nova-3',
            'language': 'multi',   # авто RU+EN, в т.ч. смена языка внутри фразы
            'smart_format': 'true',
            'punctuate': 'true',
        })
        req = urllib.request.Request(
            f'https://api.deepgram.com/v1/listen?{params}',
            data=audio,
            headers={'Authorization': f'Token {DEEPGRAM_KEY}', 'Content-Type': content_type},
            method='POST',
        )
        with urllib.request.urlopen(req) as resp:
            data = json.loads(resp.read())
        channel = data['results']['channels'][0]
        alt = channel['alternatives'][0]
        text = alt.get('transcript', '').strip()
        langs = alt.get('languages') or ([channel['detected_language']]
                                         if channel.get('detected_language') else [])
        return text, (langs[0] if langs else 'multi')

    def _forward_elevenlabs(self, voice_id, body):
        if not ELEVENLABS_KEY or 'ВСТАВЬ' in ELEVENLABS_KEY:
            err = json.dumps({"error": "No ElevenLabs key"}).encode()
            self.send_response(400)
            self.add_cors()
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(err)
            return

        url = f'https://api.elevenlabs.io/v1/text-to-speech/{voice_id}'
        headers = {
            'Content-Type': 'application/json',
            'xi-api-key': ELEVENLABS_KEY,
        }
        try:
            req = urllib.request.Request(url, data=body, headers=headers, method='POST')
            with urllib.request.urlopen(req) as resp:
                data = resp.read()
                self.send_response(200)
                self.add_cors()
                self.send_header('Content-Type', 'audio/mpeg')
                self.send_header('Content-Length', str(len(data)))
                self.end_headers()
                self.wfile.write(data)
        except urllib.error.HTTPError as e:
            err_body = e.read()
            print(f'  ✗ ElevenLabs {e.code}: {err_body[:200]}', file=sys.stderr)
            self.send_response(e.code)
            self.add_cors()
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(err_body)

    def log_message(self, fmt, *args):
        # request-лог: args[0] = строка вида "POST /path HTTP/1.1"; для send_error
        # сюда прилетают int-аргументы — такие записи пропускаем.
        if not args or not isinstance(args[0], str) or ' ' not in args[0]:
            return
        parts = args[0].split()
        path, code = parts[1], (args[1] if len(args) > 1 else '')
        if not path.endswith(('.js', '.css', '.ico', '.png')):
            print(f'  {parts[0]} {path} [{code}]')

if __name__ == '__main__':
    if AI_BACKEND == 'deepseek':
        if 'ВСТАВЬ' in DEEPSEEK_KEY:
            print('\n  ⚠️  Вставь DEEPSEEK_KEY в server.py')
            sys.exit(1)
        print(f'\n  AI: DeepSeek  key: {DEEPSEEK_KEY[:8]}...')
    else:
        try:
            r = subprocess.run(['claude', '--version'], capture_output=True, text=True)
            print(f'\n  AI: Claude Code {r.stdout.strip()}')
        except FileNotFoundError:
            print('\n  ✗ claude CLI не найден.')
            sys.exit(1)

    dg = 'настроен' if (DEEPGRAM_KEY and 'ВСТАВЬ' not in DEEPGRAM_KEY) else 'не настроен'
    print(f'  STT по умолчанию: {STT_ENGINE}  (Whisper: {WHISPER_MODEL} · Deepgram: {dg})')
    print(f'  Открой: http://localhost:{PORT}/alex-tutor.html')
    if ELEVENLABS_KEY and 'ВСТАВЬ' not in ELEVENLABS_KEY:
        print(f'  ElevenLabs: {ELEVENLABS_KEY[:8]}...')
    else:
        print(f'  ElevenLabs: не настроен (будет Browser TTS)')
    print('  Ctrl+C чтобы остановить\n')

    with http.server.ThreadingHTTPServer(('', PORT), ProxyHandler) as httpd:
        httpd.serve_forever()
