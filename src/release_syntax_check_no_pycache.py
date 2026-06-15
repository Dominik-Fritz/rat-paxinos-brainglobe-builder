from __future__ import annotations

import ast
import csv
import datetime as dt
import json
from pathlib import Path


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    src = root / "src"
    report_dir = root / "reports" / "release_syntax_check_no_pycache"
    report_dir.mkdir(parents=True, exist_ok=True)

    rows = []
    failures = []
    for py_file in sorted(src.glob("*.py")):
        rel = py_file.relative_to(root).as_posix()
        try:
            text = py_file.read_text(encoding="utf-8")
            ast.parse(text, filename=str(py_file))
            rows.append({"file": rel, "ok": True, "error": ""})
        except Exception as exc:
            msg = f"{type(exc).__name__}: {exc}"
            rows.append({"file": rel, "ok": False, "error": msg})
            failures.append({"file": rel, "error": msg})

    report = {
        "version": "Release syntax check without pycache",
        "generated_at": dt.datetime.now().isoformat(timespec="seconds"),
        "root": str(root),
        "files_checked": len(rows),
        "failures": failures,
        "passed": not failures,
        "writes_pycache": False,
    }
    (report_dir / "release_syntax_check_no_pycache_report.json").write_text(
        json.dumps(report, indent=2), encoding="utf-8", newline="\n"
    )
    with (report_dir / "release_syntax_check_no_pycache_files.csv").open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["file", "ok", "error"])
        writer.writeheader()
        writer.writerows(rows)

    lines = [
        "Release syntax check without pycache",
        "=" * 72,
        f"Generated: {report['generated_at']}",
        f"Root: {root}",
        f"Files checked: {len(rows)}",
        f"Failures: {len(failures)}",
        "Writes pycache: False",
        f"PASSED: {report['passed']}",
        "",
    ]
    if failures:
        lines.append("Failures:")
        for item in failures:
            lines.append(f"- {item['file']}: {item['error']}")
    else:
        lines.append("No syntax errors found.")
    (report_dir / "release_syntax_check_no_pycache_summary.txt").write_text(
        "\n".join(lines) + "\n", encoding="utf-8", newline="\n"
    )

    print("Release syntax check without pycache")
    print("=" * 72)
    print(f"Files checked: {len(rows)}")
    print(f"Failures: {len(failures)}")
    print(f"PASSED: {report['passed']}")
    print(f"Report dir: {report_dir}")
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
