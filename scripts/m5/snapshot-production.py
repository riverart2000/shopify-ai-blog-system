"""Run on AWS via SSH; stream a private, consistent SQLite migration snapshot.

No services are stopped. Use a second snapshot after stopping writers at cutover.
The archive contains secrets and must be stored with mode 0600, outside Git.
"""
import os
from pathlib import Path
import sqlite3
import sys
import tarfile
import tempfile

os.umask(0o077)
root = Path('/home/ubuntu/shopify-ai-blog-system')
databases = [
    'ai-blog-generator-python-server/data/ai_blog_server.db',
    'ai-blog-generator-app/prisma/prod.sqlite',
]
paths = [
    '.env',
    'ai-blog-generator-app/.env',
    'ai-blog-generator-python-server/.env',
    'ai-blog-generator-python-server/config.json',
    'ai-blog-generator-python-server/data',
    'ai-blog-generator-python-server/static',
    'ai-blog-generator-python-server/social',
    'ai-blog-generator-python-server/services/landing_pages/output',
]


def include_file(info):
    if Path(info.name).name.startswith('._'):
        return None
    if any(info.name == db or info.name.startswith(db + '-') for db in databases):
        return None
    return info


with tempfile.TemporaryDirectory(prefix='shopify-m5-snapshot-') as tmp:
    for name in databases:
        source = sqlite3.connect((root / name).as_uri() + '?mode=ro', uri=True)
        target = sqlite3.connect(Path(tmp) / Path(name).name)
        with source, target:
            source.backup(target)
            assert target.execute('PRAGMA integrity_check').fetchone() == ('ok',)
        source.close()
        target.close()
    with tarfile.open(fileobj=sys.stdout.buffer, mode='w|gz') as archive:
        for name in paths:
            if (root / name).exists():
                archive.add(root / name, arcname=name, filter=include_file)
        for name in databases:
            archive.add(Path(tmp) / Path(name).name, arcname=name)
