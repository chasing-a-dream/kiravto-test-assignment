"""Loopback-only HTML viewer. Reads the authenticated API inside Docker; no keys leave it."""
import os
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FETCH = """import httpx
from kiravto.config import Settings
s=Settings.load()
r=httpx.get('http://127.0.0.1:8000/report',headers={'Authorization':'Bearer '+s.api_token},timeout=40)
r.raise_for_status()
print(r.text)
"""


class Report:
    def __init__(self):
        self.lock = threading.Lock()
        self.body = None
        self.failed = False

    def refresh(self):
        with self.lock:
            try:
                result = subprocess.run(
                    ['docker', 'exec', 'kiravto-api-1', 'python', '-c', FETCH],
                    cwd=ROOT, capture_output=True, encoding='utf-8', timeout=50,
                    creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0),
                )
                if result.returncode or not result.stdout.startswith('<!doctype html>'):
                    raise RuntimeError('API unavailable')
                body = result.stdout.encode('utf-8')
                for name in ('final-stock-report.html', 'stock-report.html'):
                    path = ROOT / 'output' / name
                    path.parent.mkdir(parents=True, exist_ok=True)
                    temporary = path.with_suffix('.html.tmp')
                    temporary.write_bytes(body)
                    os.replace(temporary, path)
                self.body, self.failed = body, False
                return True
            except (OSError, subprocess.SubprocessError, RuntimeError):
                self.failed = True
                return False

    def response(self):
        with self.lock:
            if self.failed or self.body is None:
                return 503, ('<!doctype html><html lang="ru"><meta charset="utf-8">'
                             '<meta http-equiv="refresh" content="300"><h1>Не удалось обновить отчёт</h1>'
                             '<p>Проверьте Docker Desktop и контейнер API. Сохранённый HTML остаётся '
                             'прежним снимком. Следующая попытка — через пять минут.</p></html>').encode('utf-8')
            return 200, self.body


def main():
    report = Report()

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path not in ('/', '/report'):
                self.send_error(404)
                return
            status, body = report.response()
            self.send_response(status)
            self.send_header('Content-Type', 'text/html; charset=utf-8')
            self.send_header('Cache-Control', 'no-store')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_):
            pass

    server = ThreadingHTTPServer(('127.0.0.1', 8765), Handler)
    report.refresh()
    stop = threading.Event()

    def poll():
        while not stop.wait(300):
            report.refresh()

    threading.Thread(target=poll, daemon=True).start()
    print('HTML: http://127.0.0.1:8765/ — обновление раз в пять минут', flush=True)
    try:
        server.serve_forever()
    finally:
        stop.set()
        server.server_close()


if __name__ == '__main__':
    main()
