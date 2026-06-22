# Acronym basis legend

This directory contains a complete Paxinos/Watson label acronym proposal file.

## Files

- `Paxinos_Watson_Labels_Acronyms.txt`
  - Pattern-compatible file: `label_id<TAB>acronym<TAB>"name"`.
  - Intended as the human-readable/importable full-label mapping.

- `Paxinos_Watson_Labels_Acronyms_with_basis.csv`
  - Same labels plus provenance/basis for every acronym.
  - Use this file for review and future automation.

- `Paxinos_Watson_Labels_Acronyms_high_confidence_review.csv`
  - Rows with either direct local Paxinos cortex acronym evidence or high-confidence common neuroanatomy.

- `Paxinos_Watson_Labels_Acronyms_needs_review.csv`
  - Low/medium-confidence generated, placeholder, or container labels.

## Basis categories

- `paxinos_cortex_file_exact_id`
  - Acronym comes directly from `Paxinos_Watson_Labels_Cortex.txt` by exact label ID.

- `paxinos_cortex_file_container`
  - Cortex file uses `---` for a broad container entry. A unique CTX+ID placeholder was assigned.

- `manual_common_neuroanatomy`
  - Acronym assigned from widely used neuroanatomical conventions and Allen/Paxinos-style naming.
  - Review recommended before automatic application.

- `rule_generated_from_paxinos_name`
  - Acronym generated algorithmically from the Paxinos raw name.
  - Not a curated anatomical acronym yet.

- `paxinos_placeholder`
  - Raw Paxinos name is `-------`; a stable `UNL<label_id>` placeholder was assigned.
  - Do not treat as anatomy.

## Automation policy

For future builder integration, apply only rows with explicit reviewed approval. Suggested safe policy:

```text
apply if review_status in {approved, approved_candidate} AND confidence in {official_local, high}
never apply placeholder rows as anatomical labels
never change annotation IDs or voxel values
```

IDs and annotation volumes must remain unchanged. This file is a metadata layer only.
