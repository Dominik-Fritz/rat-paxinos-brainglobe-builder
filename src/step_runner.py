#!/usr/bin/env python3
"""Run one builder command and record phase, command, exit code, and output."""
from __future__ import annotations
import argparse
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--build-dir", required=True)
    parser.add_argument("--phase", required=True)
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)
    command = args.command[1:] if args.command[:1] == ["--"] else args.command
    if not command:
        parser.error("a command is required after --")
    folder = Path(args.build_dir)
    steps = folder / "steps"
    steps.mkdir(parents=True, exist_ok=True)
    started = datetime.now(timezone.utc)
    token = started.strftime("%H%M%S%f")
    log_path = steps / f"{token}.log"
    with log_path.open("w", encoding="utf-8", errors="replace") as log:
        log.write(f"Phase: {args.phase}\nCommand: {subprocess.list2cmdline(command)}\n\n")
        process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                   text=True, encoding="utf-8", errors="replace")
        assert process.stdout is not None
        for line in process.stdout:
            print(line, end="")
            log.write(line)
        exit_code = process.wait()
        process.stdout.close()
    finished = datetime.now(timezone.utc)
    record = {"phase": args.phase, "command": command, "exit_code": exit_code,
              "started_utc": started.isoformat(), "finished_utc": finished.isoformat(),
              "duration_seconds": (finished - started).total_seconds(), "log": str(log_path)}
    with (folder / "steps.jsonl").open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(record, ensure_ascii=False) + "\n")
    with (folder / "BUILD_LOG.txt").open("a", encoding="utf-8") as stream:
        stream.write(f"[{finished.isoformat()}] exitcode={exit_code} phase={args.phase} log={log_path.name} command={subprocess.list2cmdline(command)}\n")
    return exit_code

if __name__ == "__main__":
    raise SystemExit(main())
