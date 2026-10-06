# On a Vast worker, turns a RunPod handler into a local job server: when the
# handler calls runpod.serverless.start, it serves /run and /status on
# 127.0.0.1:18000 instead, for worker.py (the PyWorker) to proxy. Loaded by
# PYTHONPATH on the handler's process only.
import importlib.abc
import importlib.util
import json
import queue
import sys
import threading
import time
import traceback
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PORT = 18000
JOBS, TODO = {}, queue.Queue()


def _work(handler):
    while True:
        job_id = TODO.get()
        job = JOBS[job_id]
        job['status'] = 'IN_PROGRESS'
        try:
            out = handler({'id': job_id, 'input': job.pop('input')})
            if isinstance(out, dict) and out.get('error') and len(out) == 1:
                job.update(status='FAILED', error=str(out['error']))
            else:
                job.update(status='COMPLETED', output=out)
        except Exception as e:
            traceback.print_exc()
            job.update(status='FAILED', error=f'{type(e).__name__}: {e}')
        job['ended'] = time.time()
        for k in [k for k, j in JOBS.items() if time.time() - j.get('ended', time.time()) > 6 * 3600]:
            JOBS.pop(k, None)


class _Http(BaseHTTPRequestHandler):
    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers.get('Content-Length') or 0)) or b'{}')
        if self.path == '/run':
            job_id = uuid.uuid4().hex
            JOBS[job_id] = {'status': 'IN_QUEUE', 'input': body.get('input') or {}}
            TODO.put(job_id)
            out = {'id': job_id, 'status': 'IN_QUEUE'}
        elif self.path == '/status':
            job = JOBS.get(str(body.get('id')))
            out = ({'id': body.get('id'), **{k: v for k, v in job.items() if k != 'input'}}
                   if job else {'id': body.get('id'), 'status': 'FAILED', 'error': 'unknown job'})
        else:
            self.send_error(404)
            return
        data = json.dumps(out).encode()
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *a):
        pass


def _start(config):
    threading.Thread(target=_work, args=(config['handler'],), daemon=True).start()
    print('vast-jobs ready', flush=True)
    ThreadingHTTPServer(('127.0.0.1', PORT), _Http).serve_forever()


def _patch(mod):
    mod.start = _start
    mod.progress_update = lambda job, msg: print('progress', msg, flush=True)


class _Finder(importlib.abc.MetaPathFinder):
    def find_spec(self, name, path, target=None):
        if name != 'runpod.serverless':
            return None
        sys.meta_path.remove(self)
        spec = importlib.util.find_spec(name)
        sys.meta_path.insert(0, self)
        if spec and spec.loader:
            run = spec.loader.exec_module
            spec.loader.exec_module = lambda m: (run(m), _patch(m))
        return spec


sys.meta_path.insert(0, _Finder())
