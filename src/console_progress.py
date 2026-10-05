#!/usr/bin/env python3
"""Run a build step and collapse its per-slice log output into one progress line.

Every line still goes verbatim to reports/console/<label>.log. Only lines that
match a known noise pattern are collapsed; everything else is printed, and the
child's exit code is returned unchanged.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "resources" / "optional_ch03" / "whs_nissl_slices_manifest.json"

# (regex, shown label, whether the plane count is the denominator).
# Not anchored: the jars may prefix a line with a logger or class name.
NOISE_PATTERNS: tuple[tuple[str, str, bool], ...] = (
    (r"Reading first plane of\b", "Reading source planes", True),
    (r"Action (registered|removed) in observer\b", "Restoring slice actions", False),
    (r"Action \[", "Restoring slice actions", False),
)

# Lines with any of these words are always printed, even if a noise pattern matches.
SIGNAL_WORDS = re.compile(
    r"(?i)\b(fail(ed|ure)?|error|exception|warn(ing)?|cannot|can't|unable|abort|"
    r"invalid|missing|refus|denied|timeout|traceback)\b"
)


def plane_total() -> int | None:
    """Plane count from the pinned manifest, the denominator for per-source lines."""
    try:
        manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    declared = manifest.get("plane_count")
    if isinstance(declared, int) and declared > 0:
        return declared
    planes = manifest.get("planes")
    return len(planes) if isinstance(planes, (list, dict)) and planes else None


class Collapser:
    """Redraws one line in place for known noise, prints everything else."""

    BAR_WIDTH = 32
    MIN_REDRAW_SECONDS = 0.1

    def __init__(self, label: str, stream, interactive: bool, total: int | None):
        self.label = label
        self.stream = stream
        self.interactive = interactive
        self.total = total
        self.patterns = [(re.compile(p), text, scaled) for p, text, scaled in NOISE_PATTERNS]
        self.counts: dict[str, int] = {}
        self.current: str | None = None
        self.started = time.monotonic()
        self.last_drawn = 0.0
        self.dirty = False

    def _elapsed(self) -> str:
        seconds = int(time.monotonic() - self.started)
        return f"{seconds // 60}m{seconds % 60:02d}s"

    def _progress_text(self) -> str:
        assert self.current is not None
        count = self.counts[self.current]
        scaled = next((s for _, text, s in NOISE_PATTERNS if text == self.current), False)
        if scaled and self.total:
            done = min(count, self.total)
            filled = round(self.BAR_WIDTH * done / self.total)
            bar = "#" * filled + "-" * (self.BAR_WIDTH - filled)
            return f"   {self.current}  [{bar}]  {done}/{self.total}  {self._elapsed()}"
        return f"   {self.current}  {count} steps  {self._elapsed()}"

    def _redraw(self, force: bool = False) -> None:
        if self.current is None:
            return
        now = time.monotonic()
        if not force and now - self.last_drawn < self.MIN_REDRAW_SECONDS:
            return
        self.last_drawn = now
        text = self._progress_text()
        if self.interactive:
            # Pad so a shorter line fully covers the previous one.
            self.stream.write("\r" + text.ljust(self.BAR_WIDTH + 48)[:118])
        else:
            # Piped output gets no \r animation; finish() writes the final state once.
            return
        self.stream.flush()
        self.dirty = True

    def _end_progress(self) -> None:
        """Close an in-place progress line before printing something else."""
        if self.current is None:
            return
        text = self._progress_text()
        if self.interactive and self.dirty:
            self.stream.write("\r" + text.ljust(self.BAR_WIDTH + 48)[:118] + "\n")
        else:
            self.stream.write(text + "\n")
        self.stream.flush()
        self.current = None
        self.dirty = False

    def line(self, text: str) -> None:
        stripped = text.strip()
        if not SIGNAL_WORDS.search(stripped):
            for pattern, shown, _ in self.patterns:
                if pattern.search(stripped):
                    if self.current != shown:
                        self._end_progress()
                        self.current = shown
                        self.counts.setdefault(shown, 0)
                        self.started = time.monotonic()
                    self.counts[shown] += 1
                    self._redraw()
                    return
        if not stripped:
            return
        self._end_progress()
        self.stream.write(stripped + "\n")
        self.stream.flush()

    def finish(self) -> None:
        self._end_progress()


def split_lines(chunk: str, carry: str) -> tuple[list[str], str]:
    """Split on either terminator: Java progress output also uses bare \\r."""
    buffer = carry + chunk
    parts = re.split(r"\r\n|\n|\r", buffer)
    return parts[:-1], parts[-1]


def filter_stream(process: subprocess.Popen, label: str, log) -> None:
    collapser = Collapser(label, sys.stdout, sys.stdout.isatty(), plane_total())
    carry = ""
    assert process.stdout is not None
    while True:
        raw = process.stdout.read(4096)
        if not raw:
            break
        chunk = raw.decode("utf-8", errors="replace")
        lines, carry = split_lines(chunk, carry)
        for line in lines:
            log.write(line + "\n")
            collapser.line(line)
        log.flush()
    if carry:
        log.write(carry + "\n")
        collapser.line(carry)
    collapser.finish()


def drain(process: subprocess.Popen, log) -> None:
    """Pass the remaining output through unfiltered."""
    if process.stdout is None:
        return
    while True:
        raw = process.stdout.read(4096)
        if not raw:
            break
        text = raw.decode("utf-8", errors="replace")
        log.write(text)
        sys.stdout.write(text)
    sys.stdout.flush()


def run(label: str, command: list[str], log_path: Path) -> int:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    environment = dict(os.environ, PYTHONUNBUFFERED="1")
    process = subprocess.Popen(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        env=environment,
    )
    with log_path.open("w", encoding="utf-8", errors="replace", newline="\n") as log:
        try:
            filter_stream(process, label, log)
        except Exception as exc:
            # This exit code decides whether the build fails, so a defect in a
            # cosmetic layer must never cause that.
            print(f"\nWARNING [CONSOLE_FILTER]: {exc!r}; rest of the output is unfiltered.",
                  file=sys.stderr, flush=True)
            drain(process, log)
    return process.wait()


def passthrough(command: list[str]) -> int:
    """Run the command without filtering."""
    return subprocess.call(command)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--label", required=True, help="step name shown on the progress line")
    parser.add_argument("--log", default=None, help="log file; defaults to reports/console/<label>.log")
    parser.add_argument("command", nargs=argparse.REMAINDER,
                        help="-- followed by the command to run")
    args = parser.parse_args(argv)

    command = args.command[1:] if args.command and args.command[0] == "--" else args.command
    if not command:
        parser.error("no command given after --")

    # A non-ASCII character in a Java path must not raise UnicodeEncodeError on a
    # console whose code page cannot show it.
    try:
        sys.stdout.reconfigure(errors="replace")
    except (AttributeError, ValueError):
        pass

    slug = re.sub(r"[^a-z0-9]+", "_", args.label.lower()).strip("_") or "step"
    log_path = Path(args.log) if args.log else ROOT / "reports" / "console" / f"{slug}.log"
    try:
        return run(args.label, command, log_path)
    except OSError as exc:
        # The log could not be opened or the child did not start; passthrough
        # reports the child's own exit code.
        print(f"WARNING [CONSOLE_FILTER]: {exc}; running unfiltered.", file=sys.stderr)
        return passthrough(command)


if __name__ == "__main__":
    raise SystemExit(main())
