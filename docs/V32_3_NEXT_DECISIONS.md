# V32.3 Next Decisions

## Confirmed

- V32.2 orientation is the correct display basis for ABBA.
- Shape is `[608,286,409]`.
- Metadata orientation is `PIL`.
- ABBA buttons now map correctly enough for normal use.

## Remaining display problem

The current `reference.tiff` is not anatomical. It is still a synthetic edge image derived from label boundaries. That is why it looks like labels even when the `borders` overlay is hidden.

## Channel interpretation

Expected ABBA channels for current atlas:

```text
reference (Ch.0) = current reference.tiff
borders   (Ch.1) = ABBA/annotation border overlay
```

Since current `reference.tiff` itself is label-edge-like, Ch.0 and Ch.1 both look like atlas lines.

## Correct next step

Use a separate SIGMA-reference test atlas:

```text
paxinos_watson_rat_40um_sigma_reference_test
```

This keeps Paxinos labels/ontology but replaces the background with a resampled SIGMA anatomical volume.

## Ontology/acronym plan

Run audit first. Then create a curated override table, rather than making the builder invent neuroanatomy by acronym roulette.
