"""Backup orchestration must preserve stopped workers, including on failure."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[3]


class BackupStateTests(unittest.TestCase):
    def run_backup(self, fail=False):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            binary = folder / "bin"
            binary.mkdir()
            docker = binary / "docker"
            docker.write_text("""#!/usr/bin/env python3
import json, os, sys
args = sys.argv[1:]
command = args[args.index('-f') + 2:]
with open(os.environ['MOCK_DOCKER_LOG'], 'a') as stream:
    stream.write(json.dumps(command) + '\\n')
if command[0] == 'ps':
    print('db\\napi\\nproxy')
if command[0] == 'exec':
    if os.environ.get('MOCK_DUMP_FAIL') == '1':
        raise SystemExit(1)
    print('fixture-dump')
""")
            docker.chmod(0o700)
            data = folder / "data"
            (data / "artifacts").mkdir(parents=True)
            (data / "artifacts/example.txt").write_text("fixture")
            log = folder / "docker.jsonl"
            environment = dict(os.environ, PATH=str(binary) + os.pathsep + os.environ["PATH"],
                               MOCK_DOCKER_LOG=str(log), MOCK_DUMP_FAIL="1" if fail else "0")
            result = subprocess.run(["bash", str(ROOT / "infra/deploy/backup.sh"), str(folder / "env"), str(data), str(folder / "backups")],
                                    env=environment, capture_output=True, text=True, timeout=30)
            calls = [json.loads(line) for line in log.read_text().splitlines()]
            self.assertEqual([row for row in calls if row[0] == "stop"], [["stop", "api", "proxy"]])
            self.assertEqual([row for row in calls if row[0] == "start"], [["start", "api", "proxy"]])
            self.assertFalse(any(row[0] == "up" for row in calls))
            self.assertEqual(result.returncode != 0, fail, result.stderr)

    def test_restores_only_initially_running_services(self):
        self.run_backup()

    def test_dump_failure_restores_only_initially_running_services(self):
        self.run_backup(fail=True)
