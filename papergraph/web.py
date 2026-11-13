"""Local browser interface: python -m papergraph.web."""
import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import threading
from urllib.parse import urlparse
import uuid

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from papergraph.config import load_project_env, PROJECT_ROOT

STATIC = Path(__file__).parent / 'static'


def stop_process(process):
    if process.poll() is not None:
        return
    try:
        if os.name == 'nt':
            subprocess.run(['taskkill', '/PID', str(process.pid), '/T', '/F'],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10)
        else:
            os.killpg(process.pid, signal.SIGKILL)
    except (ProcessLookupError, subprocess.SubprocessError):
        pass


class Search(BaseModel):
    model_config = ConfigDict(extra='forbid')
    paper: str = Field(min_length=1, max_length=1000)
    keywords: str = Field(default='', max_length=500)
    depth: int = Field(default=2, ge=1, le=3)
    max_papers: int = Field(default=100, ge=1, le=1000)
    max_neighbors: int = Field(default=20, ge=1, le=1000)
    gemini: bool = True


class Jobs:
    def __init__(self, root):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.pool = ThreadPoolExecutor(max_workers=1)
        self.items = {}
        self.processes = {}
        for path in self.root.glob('*/status.json'):
            try:
                job = json.loads(path.read_text())
                if job['status'] in {'queued', 'running'}:
                    job.update(status='failed', error='服务已重启，请重新查询。')
                self.items[job['id']] = job
            except (ValueError, KeyError, OSError):
                continue

    def save(self, job):
        path = self.root / job['id'] / 'status.json'
        path.parent.mkdir(exist_ok=True)
        temporary = path.with_suffix('.tmp')
        temporary.write_text(json.dumps(job, ensure_ascii=False))
        temporary.replace(path)

    def create(self, query):
        with self.lock:
            if sum(j['status'] in {'queued', 'running'} for j in self.items.values()) >= 3:
                raise ValueError('已有 3 个待完成任务，请稍后再试。')
            job = dict(id=uuid.uuid4().hex, status='queued', query=query.model_dump(),
                       created=datetime.now(timezone.utc).isoformat(), logs=[], error=None)
            self.items[job['id']] = job
            self.save(job)
        self.pool.submit(self.run, job['id'])
        return job

    def run(self, identifier):
        job = self.items[identifier]
        query = Search(**job['query'])
        destination = self.root / identifier
        command = [sys.executable, '-u', str(PROJECT_ROOT / 'scripts/run_paper.py'),
                   query.paper.strip(), '--output-dir', str(destination), '--source', 'scholar',
                   '--scholar-backend', 'serpapi', '--direction', 'citing',
                   '--depth', str(query.depth), '--keywords', query.keywords,
                   '--max-papers', str(query.max_papers), '--max-neighbors', str(query.max_neighbors),
                   '--gemini' if query.gemini else '--no-gemini', '--no-arxiv-auto-verify']
        with self.lock:
            job['status'] = 'running'
            self.save(job)
        try:
            environment = os.environ.copy()
            environment['PYTHONUTF8'] = '1'
            with subprocess.Popen(command, cwd=PROJECT_ROOT, stdout=subprocess.PIPE,
                                  stderr=subprocess.STDOUT, text=True, encoding='utf-8',
                                  errors='replace', env=environment,
                                  start_new_session=os.name != 'nt') as process:
                with self.lock:
                    self.processes[identifier] = process
                timer = threading.Timer(3600, stop_process, args=(process,))
                timer.start()
                try:
                    for line in process.stdout:
                        # Provider errors should be concise and must never expose keys.
                        for name in ('GOOGLE_AI_API_KEY', 'GEMINI_API_KEY', 'SERPAPI_API_KEY', 'OPENALEX_API_KEY'):
                            value = os.getenv(name)
                            if value:
                                line = line.replace(value, '[已隐藏]')
                        with self.lock:
                            job['logs'] = (job['logs'] + [line.strip()])[-150:]
                    code = process.wait()
                finally:
                    timer.cancel()
                    with self.lock:
                        self.processes.pop(identifier, None)
            result = next((p for p in destination.glob('*.json')
                           if p.name != 'status.json' and not p.name.endswith('.metadata.json')), None)
            with self.lock:
                if code or result is None:
                    job.update(status='failed', error='查询未完成，请查看运行记录并检查密钥、网络或额度。')
                else:
                    job.update(status='completed', result=result.name, excel=result.with_suffix('.xlsx').name)
                self.save(job)

        except Exception:
            with self.lock:
                job.update(status='failed', error='任务执行异常，请检查本地环境。')
                self.save(job)

    def close(self):
        self.pool.shutdown(wait=False, cancel_futures=True)
        with self.lock:
            for process in self.processes.values():
                if process.poll() is None:
                    stop_process(process)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def send(self, code, body, mime='application/json; charset=utf-8', filename=None):
        if not isinstance(body, bytes):
            body = json.dumps(body, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header('Content-Type', mime)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'self'; frame-ancestors 'none'; base-uri 'none'")
        if filename:
            self.send_header('Content-Disposition', f'attachment; filename="{filename}"')
        self.end_headers()
        self.wfile.write(body)

    def trusted(self):
        hostname = urlparse('http://' + self.headers.get('Host', '')).hostname
        if hostname not in {'localhost', '127.0.0.1', '::1'}:
            self.send(403, {'error': '仅支持本机访问。'})
            return False
        origin = self.headers.get('Origin')
        if origin and urlparse(origin).netloc != self.headers.get('Host'):
            self.send(403, {'error': '不允许跨站请求。'})
            return False
        return True

    def do_GET(self):
        if not self.trusted():
            return
        path = urlparse(self.path).path
        if path in {'/', '/app.js', '/style.css'}:
            name = {'/': 'index.html', '/app.js': 'app.js', '/style.css': 'style.css'}[path]
            mime = {'/': 'text/html', '/app.js': 'text/javascript', '/style.css': 'text/css'}[path]
            return self.send(200, (STATIC / name).read_bytes(), mime + '; charset=utf-8')
        if path == '/api/config':
            return self.send(200, {'serpapi': bool(os.getenv('SERPAPI_API_KEY')),
                                   'gemini': bool(os.getenv('GOOGLE_AI_API_KEY') or os.getenv('GEMINI_API_KEY'))})
        if path == '/api/jobs':
            with self.server.jobs.lock:
                return self.send(200, list(self.server.jobs.items.values())[-50:][::-1])
        match = re.fullmatch(r'/api/jobs/([a-f0-9]{32})(?:/(result|excel|json))?', path)
        if match:
            identifier, kind = match.groups()
            with self.server.jobs.lock:
                job = self.server.jobs.items.get(identifier)
                if not job:
                    return self.send(404, {'error': '任务不存在。'})
                if not kind:
                    return self.send(200, job)
                if job['status'] != 'completed':
                    return self.send(409, {'error': '结果尚未生成。'})
                result = self.server.jobs.root / identifier / job['result']
                if kind == 'result':
                    return self.send(200, json.loads(result.read_text()))
                artifact = result if kind == 'json' else result.with_suffix('.xlsx')
                mime = 'application/json' if kind == 'json' else 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
                return self.send(200, artifact.read_bytes(), mime, 'papergraph.' + ('json' if kind == 'json' else 'xlsx'))
        self.send(404, {'error': '页面不存在。'})

    def do_POST(self):
        if not self.trusted():
            return
        if self.path != '/api/jobs':
            return self.send(404, {'error': '页面不存在。'})
        try:
            size = int(self.headers.get('Content-Length', '0'))
            if not 0 < size <= 8192:
                raise ValueError('请求大小不正确。')
            query = Search.model_validate_json(self.rfile.read(size))
            if not query.paper.strip():
                raise ValueError('请输入论文标题或链接。')
            if not os.getenv('SERPAPI_API_KEY'):
                raise ValueError('请先在项目 .env 中设置 SERPAPI_API_KEY，再重启服务。')
            if query.gemini and not (os.getenv('GOOGLE_AI_API_KEY') or os.getenv('GEMINI_API_KEY')):
                raise ValueError('请配置 Gemini 密钥，或关闭 AI 分析。')
            return self.send(202, self.server.jobs.create(query))
        except ValidationError:
            self.send(400, {'error': '参数不正确：深度 1–3，论文上限及邻居上限 1–1000。'})
        except ValueError as error:
            self.send(400, {'error': str(error)})


def make_server(port=8000, root=None):
    server = ThreadingHTTPServer(('127.0.0.1', port), Handler)
    server.jobs = Jobs(root or PROJECT_ROOT / 'outputs/web')
    return server


def main():
    parser = argparse.ArgumentParser(description='PaperGraph 本地网站')
    parser.add_argument('--port', type=int, default=8000)
    args = parser.parse_args()
    load_project_env()
    server = make_server(args.port)
    print(f'PaperGraph 已启动：http://127.0.0.1:{server.server_port}（Ctrl+C 停止）', flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        server.jobs.close()


if __name__ == '__main__':
    main()
