import json
from pathlib import Path
import threading
import time

import httpx

from papergraph.web import Jobs, Search, make_server


def test_local_http_validation_and_downloads(tmp_path, monkeypatch):
    monkeypatch.setenv('SERPAPI_API_KEY', 'test-secret')
    def run(self, identifier):
        with self.lock:
            job = self.items[identifier]
            path = self.root / identifier
            (path / 'graph.json').write_text(json.dumps({'nodes': [], 'edges': []}))
            (path / 'graph.xlsx').write_bytes(b'excel-artifact')
            job.update(status='completed', result='graph.json', excel='graph.xlsx')
            self.save(job)
    monkeypatch.setattr(Jobs, 'run', run)
    server = make_server(0, tmp_path)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with httpx.Client(base_url=f'http://127.0.0.1:{server.server_port}', trust_env=False) as client:
            assert client.get('/').status_code == 200
            assert client.get('/app.js').status_code == 200
            assert 'test-secret' not in client.get('/api/config').text
            assert client.get('/.env').status_code == 404
            assert client.post('/api/jobs', json={'paper': 'A', 'depth': 5}).status_code == 400
            assert client.post('/api/jobs', json={'paper': 'A', 'gemini': False}, headers={'Origin': 'https://other.example'}).status_code == 403
            assert client.get('/api/jobs', headers={'Host': 'other.example'}).status_code == 403
            response = client.post('/api/jobs', json={'paper': 'A', 'gemini': False})
            assert response.status_code == 202
            identifier = response.json()['id']
            for _ in range(100):
                if client.get('/api/jobs/' + identifier).json()['status'] == 'completed':
                    break
                time.sleep(.01)
            assert client.get(f'/api/jobs/{identifier}/result').json()['nodes'] == []
            assert client.get(f'/api/jobs/{identifier}/excel').content == b'excel-artifact'
            assert client.get(f'/api/jobs/{"a"*32}/excel').status_code == 404
            assert len(client.get('/api/jobs').json()) == 1
    finally:
        server.shutdown()
        server.server_close()
        server.jobs.pool.shutdown(wait=True)


def test_restart_marks_interrupted_task_failed(tmp_path):
    identifier = 'a' * 32
    path = tmp_path / identifier
    path.mkdir()
    (path / 'status.json').write_text(json.dumps({'id': identifier, 'status': 'running'}))
    jobs = Jobs(tmp_path)
    assert jobs.items[identifier]['status'] == 'failed'
    jobs.pool.shutdown()
