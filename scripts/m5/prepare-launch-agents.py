"""Generate launchd definitions without installing or starting them."""
import argparse
from pathlib import Path
import plistlib

parser = argparse.ArgumentParser()
parser.add_argument('--runtime', required=True, type=Path)
parser.add_argument('--node', required=True, type=Path)
args = parser.parse_args()
runtime = args.runtime.resolve()
runner = Path(__file__).with_name('run-component.py').resolve()
output = runtime / 'launch-agents'
output.mkdir(parents=True, exist_ok=True)
(runtime / 'logs').mkdir(exist_ok=True)
for component in ['backend', 'frontend', 'scheduler']:
    label = 'com.joebains.shopify-blog.' + component
    config = {
        'Label': label,
        'ProgramArguments': [str(runtime / 'venv/bin/python'), str(runner), component,
                             '--runtime', str(runtime), '--node', str(args.node.resolve())],
        'WorkingDirectory': str(runtime),
        'RunAtLoad': True,
        'KeepAlive': True,
        'ThrottleInterval': 15,
        'ExitTimeOut': 60,
        'Umask': 0o077,
        'StandardOutPath': str(runtime / 'logs' / (component + '.log')),
        'StandardErrorPath': str(runtime / 'logs' / (component + '.log')),
        'EnvironmentVariables': {'PATH': str(args.node.parent) + ':/opt/homebrew/bin:/usr/bin:/bin'},
    }
    with (output / (label + '.plist')).open('wb') as handle:
        plistlib.dump(config, handle)
for component, command in {
    'proxy': ['/opt/homebrew/bin/caddy', 'run', '--config', str(runtime / 'Caddyfile'), '--adapter', 'caddyfile'],
    'tunnel': ['/opt/homebrew/bin/cloudflared', 'tunnel', '--no-autoupdate',
               '--metrics', '127.0.0.1:20241', 'run', '--token-file', str(runtime / 'tunnel.token')],
}.items():
    config = {
        'Label': 'com.joebains.shopify-blog.' + component,
        'ProgramArguments': command,
        'WorkingDirectory': str(runtime),
        'RunAtLoad': True, 'KeepAlive': True, 'ThrottleInterval': 15,
        'ExitTimeOut': 60, 'Umask': 0o077,
        'StandardOutPath': str(runtime / 'logs' / (component + '.log')),
        'StandardErrorPath': str(runtime / 'logs' / (component + '.log')),
    }
    with (output / (config['Label'] + '.plist')).open('wb') as handle:
        plistlib.dump(config, handle)
print('Generated app, proxy and tunnel launch agents in', output)
print('Do not install scheduler before final cutover, or tunnel before its token file exists.')
