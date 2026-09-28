#!/usr/bin/env python3
"""Run a build step and collapse its per-item log spam into a progress line.

The native ABBA stages print one line per slice and one line per source from
inside the Java jars -- "Action registered in observer: ..." from
ImageToAtlasRegister and "Reading first plane of (...)" from
bigdataviewer-biop-tools. With 588 slices that is thousands of lines scrolling
past, which hides the few lines that matter. The jars cannot be changed, so the
output is filtered here instead.

Two properties keep this from becoming a way to lose errors:

* every line is written verbatim to reports/console/<label>.log, so nothing is
  discarded -- the noise becomes a diagnostic artefact instead of scrollback;
* only lines matching a known noise pattern are collapsed. Anything else is
  passed straight through. The pattern table is an allowlist of noise, not a
  denylist of signal, so if ABBA rewords a message the line simply becomes
  visible again rather than silently vanishing.

The counted noise lines are themselves the progress signal: one
"Reading first plane of" per source means counting them measures the export.
No percentage is shown unless the denominator is actually known, because a made
up percentage is worse than none.

Exit status is the child's, so the caller's ERRORLEVEL handling is unaffected.
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

# (regex, shown label, whether the plane count is the denominator)
#
# Deliberately not anchored to the start of the line: the jars print through
# several paths and a line may carry a logger or class prefix. Anchoring here
# would silently stop matching and put the spam straight back on the console.
NOISE_PATTERNS: tuple[tuple[str, str, bool], ...] = (
    (r"Reading first plane of\b", "Reading source planes", True),
    (r"Action (registered|removed) in observer\b", "Restoring slice actions", False),
    (r"Action \[", "Restoring slice actions", False),
)

# A noise pattern can be too generous -- "Action [" also matches
# "Action [RegisterSliceAction] failed". Any line carrying one of these words is
# printed whatever else it matches, because the whole point of collapsing the
# per-slice chatter is to make lines like these findable.
SIGNAL_WORDS = re.compile(
    r"(?i)\b(fail(ed|ure)?|error|exception|warn(ing)?|cannot|can't|unable|abort|"
    r"invalid|missing|refus|denied|timeout|traceback)\b"
)


def plane_total() -> int | None:
    """The pinned plane count, which is the denominator for per-source lines.

    Taken from the manifest that defines the planes, so the bar cannot drift from
    what is actually exported. plane_count is the manifest's own declaration;
    len(planes) is only a fallback, and planes is a mapping keyed by source id.
    """
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

    # -- output ------------------------------------------------------------
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
            # Pad to clear a previously longer line; \r alone leaves remnants.
            self.stream.write("\r" + text.ljust(self.BAR_WIDTH + 48)[:118])
        else:
            # Piped or logged output: no carriage-return animation, one line per
            # redraw would be its own kind of spam, so only the final state is
            # written, by finish().
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

    # -- input -------------------------------------------------------------
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


def run(label: str, command: list[str], log_path: Path) -> int:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    environment = dict(os.environ, PYTHONUNBUFFERED="1")
    process = subprocess.Popen(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        env=environment,
    )
    collapser = Collapser(label, sys.stdout, sys.stdout.isatty(), plane_total())
    carry = ""
    assert process.stdout is not None
    with log_path.open("w", encoding="utf-8", errors="replace", newline="\n") as log:
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
    return process.wait()


def passthrough(command: list[str]) -> int:
    """Used when filtering itself fails: the build must not depend on cosmetics."""
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

    slug = re.sub(r"[^a-z0-9]+", "_", args.label.lower()).strip("_") or "step"
    log_path = Path(args.log) if args.log else ROOT / "reports" / "console" / f"{slug}.log"
    try:
        return run(args.label, command, log_path)
    except OSError as exc:
        print(f"WARNING [CONSOLE_FILTER]: {exc}; running unfiltered.", file=sys.stderr)
        return passthrough(command)


if __name__ == "__main__":
    raise SystemExit(main())
