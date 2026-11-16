"""Local reusable cache. Successful responses only; credentials never form stored URLs."""
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import time
from urllib.parse import quote

import httpx


class Cache:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.execute('CREATE TABLE IF NOT EXISTS entries (key TEXT PRIMARY KEY, value BLOB, expires REAL)')

    def connect(self):
        return sqlite3.connect(self.path, timeout=30)

    @staticmethod
    def key(namespace, value):
        return hashlib.sha256((namespace + json.dumps(value, sort_keys=True, ensure_ascii=False)).encode()).hexdigest()

    def get(self, key):
        with self.connect() as db:
            row = db.execute('SELECT value, expires FROM entries WHERE key=?', (key,)).fetchone()
            if row and row[1] > time.time():
                return json.loads(row[0])
            if row:
                db.execute('DELETE FROM entries WHERE key=?', (key,))
        return None

    def put(self, key, value, ttl):
        with self.connect() as db:
            db.execute('INSERT OR REPLACE INTO entries VALUES (?,?,?)',
                       (key, json.dumps(value, ensure_ascii=False), time.time() + ttl))


def configured_cache():
    path = os.getenv('PAPERGRAPH_CACHE_PATH')
    return Cache(path) if path else None


class CachedClient(httpx.AsyncClient):
    async def send(self, request, **kwargs):
        cache = configured_cache()
        if not cache or request.method != 'GET':
            return await super().send(request, **kwargs)
        params = [(k, v) for k, v in request.url.params.multi_items()
                  if k.lower() not in {'api_key', 'key', 'token', 'access_token'}]
        key = cache.key('http-v1', [str(request.url.copy_with(query=None)), sorted(params)])
        saved = None if os.getenv('PAPERGRAPH_REFRESH') == '1' else cache.get(key)
        if saved:
            import base64
            return httpx.Response(200, content=base64.b64decode(saved['body']),
                                  headers={'content-type': saved['type']}, request=request,
                                  extensions={'papergraph_cache_hit': True})
        response = await super().send(request, **kwargs)
        if response.status_code != 200:
            return response
        # Streamed PDFs retain their existing size limit and aren't buffered here.
        if kwargs.get('stream'):
            return response
        body = await response.aread()
        content_type = response.headers.get('content-type', '')
        if len(body) > 10 * 1024 * 1024:
            return response
        if 'json' not in content_type:
            return response
        if 'json' in content_type:
            try:
                data = json.loads(body)
                if isinstance(data, dict) and data.get('error'):
                    return response
            except ValueError:
                return response
        # SerpApi may echo the credential in pagination URLs. Redact it even there.
        for name in ('SERPAPI_API_KEY', 'OPENALEX_API_KEY', 'GOOGLE_AI_API_KEY', 'GEMINI_API_KEY'):
            secret = os.getenv(name)
            if secret:
                for value in (secret, quote(secret, safe='')):
                    body = body.replace(value.encode(), b'[redacted]')
        import base64
        ttl = 86400 if request.url.host in {'serpapi.com', 'scholar.google.com'} else 7 * 86400
        cache.put(key, {'body': base64.b64encode(body).decode(), 'type': content_type}, ttl)
        return response


def http_client(**kwargs):
    return CachedClient(**kwargs) if os.getenv('PAPERGRAPH_CACHE_PATH') else httpx.AsyncClient(**kwargs)
