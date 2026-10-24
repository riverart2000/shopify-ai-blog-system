"""Restore final AWS state with local services stopped; preserve the staged copy."""
import argparse
import os
from pathlib import Path
import socket
import sqlite3
import tarfile
import tempfile
import time

parser = argparse.ArgumentParser()
parser.add_argument('--runtime', required=True, type=Path)
parser.add_argument('--archive', required=True, type=Path)
args = parser.parse_args()
os.umask(0o077)
runtime = args.runtime.resolve()
release = runtime / 'release'
for port in [3001, 4000]:
    with socket.socket() as probe:
        probe.settimeout(1)
        if probe.connect_ex(('127.0.0.1', port)) == 0:
            raise SystemExit('Stop local services before restore: port ' + str(port))
paths = ['.env', 'ai-blog-generator-app/.env', 'ai-blog-generator-app/prisma/prod.sqlite',
         'ai-blog-generator-python-server/.env', 'ai-blog-generator-python-server/config.json',
         'ai-blog-generator-python-server/data', 'ai-blog-generator-python-server/static',
         'ai-blog-generator-python-server/social',
         'ai-blog-generator-python-server/services/landing_pages/output']
backup = runtime / 'snapshots' / ('local-before-restore-' + str(time.time_ns()))
with tempfile.TemporaryDirectory(prefix='incoming-', dir=runtime) as tmp:
    incoming = Path(tmp)
    with tarfile.open(args.archive) as archive:
        archive.extractall(incoming, filter='data')
    for name in ['ai-blog-generator-app/prisma/prod.sqlite',
                 'ai-blog-generator-python-server/data/ai_blog_server.db']:
        with sqlite3.connect(incoming / name) as db:
            if db.execute('PRAGMA integrity_check').fetchone() != ('ok',):
                raise SystemExit('Snapshot database failed integrity check: ' + name)
    with sqlite3.connect(incoming / 'ai-blog-generator-python-server/data/ai_blog_server.db') as db:
        db.execute("UPDATE landing_page_jobs SET result_path = replace(result_path, ?, ?) "
                   "WHERE result_path LIKE ?", ('/home/ubuntu/shopify-ai-blog-system', str(release),
                                               '/home/ubuntu/shopify-ai-blog-system/%'))
    for name in paths:
        source, destination, previous = incoming / name, release / name, backup / name
        if not source.exists():
            continue
        previous.parent.mkdir(parents=True, exist_ok=True)
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.exists():
            os.replace(destination, previous)
        if name.endswith('.sqlite'):
            for suffix in ['-wal', '-shm', '-journal']:
                sidecar = Path(str(destination) + suffix)
                if sidecar.exists():
                    os.replace(sidecar, Path(str(previous) + suffix))
        os.replace(source, destination)
print('Final databases and files restored; previous local state retained at', backup)
