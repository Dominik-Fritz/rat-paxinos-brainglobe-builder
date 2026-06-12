from __future__ import annotations

import py_compile
from pathlib import Path
from rich.console import Console
from rich.table import Table

console = Console()

# Clean V32.2 critical path. Legacy V33-V38 scripts are intentionally not part
# of this stable package, because the NeuroRat reference line was invalidated by
# the affine-overlap diagnostic.
CRITICAL = [
    "src/utils_paths.py",
    "src/parse_labels.py",
    "src/record_environment.py",
    "src/inspect_inputs.py",
    "src/analyze_paxinos_labels.py",
    "src/analyze_placeholder_context.py",
    "src/build_structures_json.py",
    "src/v15_cleanup_structures_labels.py",
    "src/v16_build_hierarchical_structures.py",
    "src/build_provisional_brainglobe_atlas.py",
    "src/v22_fix_metadata_compliance.py",
    "src/v29_fix_abba_structure_root.py",
    "src/v30_enrich_abba_java_structures.py",
    "src/v14_export_brainglobe_tiffs.py",
    "src/v31_create_hemispheres_tiff.py",
    "src/validate_provisional_atlas_package.py",
    "src/build_official_candidate.py",
    "src/v25_clean_native_brainglobe_install.py",
    "src/v32_fix_no_additional_references.py",
    "src/v32_2_apply_validated_abba_orientation.py",
    "src/v17_patch_abba_visibility.py",
    "src/experiments/reference_orientation_diagnostic.py",
    "src/experiments/sigma_reference_experiment.py",
]

def main() -> int:
    table = Table(title="V32.2 syntax smoke test")
    table.add_column("File")
    table.add_column("OK")
    errors = []
    for rel in CRITICAL:
        path = Path(rel)
        try:
            py_compile.compile(str(path), doraise=True)
            table.add_row(rel, "True")
        except Exception as exc:
            table.add_row(rel, "False")
            errors.append(f"{rel}: {exc}")
    console.print(table)
    if errors:
        for e in errors:
            console.print(f"[red]{e}[/red]")
        return 1
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
