"""Save the explicitly approved clipboard credential without printing secrets.

Run only after the user approves reading the Mac clipboard with pbpaste.
Accept only the Cloudflare tunnel created for this migration, and never overwrite
an existing credential file.
"""
import argparse
import base64
import json
import os
from pathlib import Path
import re
import subprocess

parser = argparse.ArgumentParser()
parser.add_argument('--runtime', required=True, type=Path)
args = parser.parse_args()
os.umask(0o077)
copied = subprocess.run(['/usr/bin/pbpaste'], check=True, capture_output=True,
                        text=True).stdout
match = re.fullmatch(r'\s*cloudflared tunnel run --token ([A-Za-z0-9_=-]+)\s*', copied)
if not match:
    raise SystemExit('Clipboard does not contain the expected tunnel command; no file was written.')
token = match.group(1)
try:
    credential = json.loads(base64.urlsafe_b64decode(token + '=' * (-len(token) % 4)))
except (ValueError, UnicodeDecodeError):
    raise SystemExit('Clipboard credential has an invalid format; no file was written.')
if credential.get('a') != 'b2967eee2560b13b7e1761c466e7a067' or credential.get('t') != 'a46d228b-465e-4038-adc4-115528e699a6':
    raise SystemExit('Credential is for a different account or tunnel; no file was written.')
path = args.runtime.resolve() / 'tunnel.token'
fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
with os.fdopen(fd, 'w') as handle:
    handle.write(token)
print('Expected tunnel credential saved with owner-only permissions; token not displayed.')
