"""Run over SSH to quiesce this app only, leaving Caddy and other sites running."""
from pathlib import Path
import os
import signal
import socket
import sqlite3
import time

root = Path('/home/ubuntu/shopify-ai-blog-system')
backend = root / 'ai-blog-generator-python-server'
db = sqlite3.connect((backend / 'data/ai_blog_server.db').as_uri() + '?mode=ro', uri=True)
for table in ['landing_page_jobs', 'seo_growth_runs', 'seo_repair_jobs', 'intelligence_runs']:
    columns = [r[1] for r in db.execute('PRAGMA table_info(' + table + ')')]
    if 'status' in columns:
        active = db.execute('SELECT COUNT(*) FROM ' + table +
                            " WHERE status IN ('queued','running','pending')").fetchone()[0]
        if active:
            raise SystemExit('Refusing cutover: active work in ' + table)
db.close()

def process_info(pid):
    try:
        status = (Path('/proc') / str(pid) / 'stat').read_text().rsplit(')', 1)[1].split()
        return status[0], int(status[1])
    except (FileNotFoundError, ProcessLookupError):
        return None

roots = []
for name in ['scheduler', 'frontend', 'main']:
    pid = int((backend / 'run' / (name + '.pid')).read_text())
    cwd = (Path('/proc') / str(pid) / 'cwd').resolve()
    if not cwd.is_relative_to(root):
        raise SystemExit('Refusing to stop unexpected process for ' + name)
    roots.append(pid)

processes = {int(p.name): process_info(int(p.name)) for p in Path('/proc').iterdir()
             if p.name.isdigit()}
selected = set(roots)
while True:
    children = {pid for pid, info in processes.items() if info and info[1] in selected}
    if children <= selected:
        break
    selected |= children

# Stop the scheduler first. Ask the frontend process and backend reloader to
# drain normally; signal their descendants too so npm does not orphan Node.
for pid in roots + sorted(selected - set(roots)):
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        pass

deadline = time.monotonic() + 60
while time.monotonic() < deadline:
    live = [pid for pid in selected if (info := process_info(pid)) and info[0] != 'Z']
    if not live:
        break
    time.sleep(0.5)
else:
    raise SystemExit('Processes did not drain within 60 seconds; do not copy data.')

for port in [3001, 4000]:
    with socket.socket() as probe:
        probe.settimeout(1)
        if probe.connect_ex(('127.0.0.1', port)) == 0:
            raise SystemExit('Port still has a listener: ' + str(port))
print('AWS scheduler, frontend and backend stopped; both app ports are closed.')
