# Rat Paxinos BrainGlobe Builder - V32 Stable Restart

This package intentionally returns to the last stable builder line.

## Included

- Root / structure compatibility fixes
- ABBA Java structure enrichment
- hemispheres.tiff generation
- additional_references fixed to []
- Clean native BrainGlobe install
- Optional ABBA visibility patch

## Not included

Everything from V33 onward is intentionally removed from this stable restart:

- NeuroRat MRI reference replacement
- V34/V35 orientation tools
- V36/V37/V38 experimental runners
- extra one-off patch BATs

Reason: V33 produced an all-zero reference after affine-space resampling. That means the post-V32 reference-replacement approach was not safe enough to live in the main builder.

## Use

Copy all contents into:

G:\rat-paxinos-brainglobe-builder

Overwrite existing files.

Then run only:

run_builder.bat

After completion, restart ABBA/Fiji completely and open:

paxinos_watson_rat_40um

## Important limitation

The V32 atlas still uses the provisional label-edge reference. It is a technical loading/compatibility atlas, not yet a validated anatomical reference atlas.

Future reference improvements should be developed separately and only merged after they produce a non-zero, visually plausible reference.
