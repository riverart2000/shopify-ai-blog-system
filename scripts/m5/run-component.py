"""Run one production component from the private M5 release, under launchd."""
import argparse
import json
import os
from pathlib import Path
import sys

from dotenv import load_dotenv

parser = argparse.ArgumentParser()
parser.add_argument('component', choices=['backend', 'frontend', 'scheduler'])
parser.add_argument('--runtime', type=Path, required=True)
parser.add_argument('--node', type=Path)
args = parser.parse_args()
runtime = args.runtime.resolve()
release = runtime / 'release'
backend = release / 'ai-blog-generator-python-server'
frontend = release / 'ai-blog-generator-app'

# Match the existing production service manager's environment precedence.
for env in [release / '.env', backend / '.env', frontend / '.env']:
    load_dotenv(env, override=True)

os.environ['DB_PATH'] = str(backend / 'data/ai_blog_server.db')
os.environ['DATABASE_URL'] = 'file:' + str(frontend / 'prisma/prod.sqlite')
os.environ['AI_BLOG_BACKEND_URL'] = 'http://127.0.0.1:4000'
os.environ['NODE_ENV'] = 'production'
os.environ['HOST'] = '127.0.0.1'
os.environ['PORT'] = '3001'
os.environ['PYTHONUNBUFFERED'] = '1'

if args.component == 'frontend':
    if not args.node or not args.node.is_file():
        parser.error('--node must point to the installed Node executable')
    os.chdir(frontend)
    os.execv(str(args.node), [str(args.node), 'node_modules/@react-router/serve/bin.js',
                             'build/server/index.js'])

os.chdir(backend)
config = json.loads((backend / 'config.json').read_text())
config['server'].update(mode='production', host='127.0.0.1', port=4000)
config_path = runtime / ('config-' + args.component + '.json')
config_path.write_text(json.dumps(config))
config_path.chmod(0o600)
os.environ['APP_CONFIG_PATH'] = str(config_path)

if args.component == 'scheduler':
    if not (runtime / 'SCHEDULER_ENABLED').is_file():
        raise SystemExit('Scheduler disabled: enable only after the AWS scheduler is stopped and final data is copied.')
    os.execv(sys.executable, [sys.executable, 'scheduler.py'])
else:
    os.execv(sys.executable, [sys.executable, '-m', 'uvicorn', 'main:app',
                             '--host', '127.0.0.1', '--port', '4000',
                             '--proxy-headers', '--forwarded-allow-ips', '127.0.0.1'])
