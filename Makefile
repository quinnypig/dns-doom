.PHONY: check check-recorded evidence
check:
	uv run --with-requirements requirements.txt python -c 'import os,subprocess,sys; subprocess.run(["bash","build-native.sh"],env=dict(os.environ,PYTHON_BIN=sys.executable),check=True); subprocess.run([sys.executable,"scripts/check-cpu.py"],check=True)'
	uv run --with-requirements requirements.txt python scripts/verify-evidence.py

check-recorded:
	uv run --with-requirements requirements.txt python scripts/verify-evidence.py

evidence:
	uv run --with-requirements requirements.txt python scripts/verify-evidence.py --write-release .fleet-evidence
