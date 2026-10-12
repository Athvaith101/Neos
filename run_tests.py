"""Run the full suite and record the result in test_status.json: the ONE source for the test count.
README, the Evidence page and /api/meta read this file; no document should hard-code a count."""
import json, subprocess, sys, re, datetime, hashlib, pathlib
r = subprocess.run([sys.executable, '-m', 'pytest', '-q', 'tests', '-p', 'no:cacheprovider'], capture_output=True, text=True,
                   env={**__import__('os').environ, 'PYTHONPATH': '.'})
tail = r.stdout.strip().splitlines()[-1] if r.stdout.strip() else ''
passed = int((re.search(r'(\d+) passed', tail) or [0, 0])[1]); failed = int((re.search(r'(\d+) failed', tail) or [0, 0])[1])
errors = int((re.search(r'(\d+) error', tail) or [0, 0])[1])
src = hashlib.sha256(b''.join(p.read_bytes() for p in sorted(pathlib.Path('neos').glob('*.py')) + sorted(pathlib.Path('tests').glob('*.py')))).hexdigest()[:16]
out = dict(passed=passed, failed=failed, errors=errors, total=passed + failed + errors, summary=tail,
           run_at=datetime.datetime.now(datetime.timezone.utc).isoformat(timespec='seconds'), source_sha256_16=src)
pathlib.Path('test_status.json').write_text(json.dumps(out, indent=1))
print(json.dumps(out)); sys.exit(0 if r.returncode == 0 else 1)
