# Übergabe an Claude: nativer ABBA-0.11-Nissl-Builder

Stand des Repository-Branches: 2026-09-07. Dieses Dokument trennt strikt
zwischen gesicherten Fakten, dem zuletzt tatsächlich auf Windows beobachteten
Atlas und noch nicht auf Windows validierten Änderungen im aktuellen Branch.

## 1. Ziel und unveränderliche wissenschaftliche Regeln

Der Builder soll mit einem Doppelklick auf `run_builder.bat` den BrainGlobe-
Atlas `paxinos_watson_rat_40um` erzeugen und in vorhandenen ABBA-Python-
Installationen sichtbar machen. Der Paxinos-Annotationsatlas ist autoritativ;
der registrierte Waxholm-Nissl-Kanal ist ausschließlich eine visuelle Hilfe.

Unveränderlich sind:

- Annotation, Ontologie, Label-IDs, Orientierung und Landmarken;
- die autoritative Registrierung
  `resources/optional_ch03/nissl_registration_0_3_0/final_for_V_0_3.abba`;
- deren SHA-256
  `e038741ac9825c35e62c1e88658c3533a5e4da3460ebc9644275c4b6e48e7f06`;
- 588 Quellen, `source_id` 0–587, Waxholm AP 189–776, anterior nach posterior;
- Zielzuordnung `nonempty_ap[1:589]`, mit ausschließlicher Duplikation der
  ersten registrierten Ebene nach `nonempty_ap[0]`;
- kein wissenschaftlicher Python-TPS-Renderer, kein Legacy-Stack-Fallback und
  kein synthetisches Auffüllen fehlender Ebenen;
- `visual_parity_status: pending` und `release_eligible: false`, bis der Nutzer
  die visuelle Parität ausdrücklich bestätigt.

## 2. Letzter tatsächlich beobachteter Windows-Atlas (`0.3.1_test_28`)

Der letzte vollständige Windows-Lauf war technisch erfolgreich und installierte
einen nativen Kanal. `BUILD_SUMMARY.txt` meldete unter anderem:

```text
Status: SUCCESS WITH WARNINGS
Registered Nissl channel: present
Nissl renderer backend: native_abba_0.11
Native backend verified: True
Visual parity status: pending
Release eligible: False
Transform roundtrip criterion: deformation_landmarks
Mapped Nissl planes: 588
Target sequence offset: 1
Duplicated anterior target AP: 9
Native zero-valued registered planes: 151
Native coverage status: review_required
```

Die ABBA-Sichtprüfung zeigte zwei klare Fehler:

1. Der Nissl-Inhalt lag systematisch zu weit rechts gegenüber den blauen
   Paxinos-Konturen.
2. Zahlreiche Schnitte waren vollständig unsichtbar. Der Report bestätigte 151
   vollständig nullwertige registrierte Ziel-AP-Ebenen; dies ist keine bloße
   Display-Helligkeitsschwankung.

Wichtig: Dieser Atlas wurde **vor** der letzten, jetzt im Branch enthaltenen
konsistenten Fixed-Source-Origin-Korrektur erzeugt. Es gibt noch keinen
Windows-Atlas, der diese neueste Korrektur visuell bestätigt oder widerlegt.

## 3. Gesicherte Fakten aus dem ABBA-State

Das `.abba`-ZIP enthält exakt `sources.json`, `state.json` und
`_bdvdataset_0.xml`. Der State hat Version 0.11.0, 588 Slice-States und pro
Slice genau eine `RegisterSliceAction` vom Typ `SacBigWarp2DRegistration`.

Die gespeicherte Transformkette lautet:

```text
BoundedRealTransform
  -> InvertibleWrapped2DTransformAs3D
  -> WrappedIterativeInvertibleRealTransform
  -> ThinplateSplineTransform
```

Die BigWarp-Parameter sind:

```text
sx = 18.8 mm
sy = 13.12 mm
px = -9.4 mm
py = -6.56 mm
```

Die gespeicherten XY-Bounds liegen bei ungefähr
`[-9.407, +9.407] × [-6.578, +6.578] mm`. Das ist ein um LR/SI null
zentrierter BigWarp-Canvas. Die 588 Z-Bounds folgen den Slice-Positionen im
Abstand von 0.04 mm.

Die BDV-ViewRegistration der ursprünglichen Moving Sources enthält für die
relevanten Setups die Affine:

```text
0.039  0      0      -9.984
0      0.039  0      -9.984
0      0      0.001  -0.0005
```

Das portable Rebinding ersetzt nur die historischen QuPath-Opener durch
explizit benannte Bio-Formats-TIFFs. `sources.json`, `state.json`, die BDV-
ViewRegistrations und die gespeicherten ABBA-Actions bleiben erhalten.

## 4. Architektur des aktuellen nativen Pfades

`run_builder.bat` installiert bzw. verwendet builderlokal:

- Python-Venv;
- Temurin JDK 17.0.14+7;
- Maven 3.9.9;
- separate Maven-, JGO-, ImageJ-, Download- und Temp-Caches;
- ABBA-Python 0.11.0 aus `vendor/abba_python_0_11_0`.

Der Renderer:

1. liest den gepinnten Waxholm-Referenzstack;
2. materialisiert AP 189–776 als 588 temporäre, eindeutig benannte TIFFs;
3. erstellt `reports/native_abba/rebound_state.abba` ohne historischen
   QuPath-Pfad;
4. öffnet den gebauten Paxinos-Atlas als native Fixed Source;
5. lädt den State mit der vendorten `state_load`-Methode bzw.
   `ABBAStateLoadCommand`;
6. wartet mit `waitForTasks()` auf alle Move-/Register-Actions;
7. prüft die TPS-Landmarken über einen nativen Load/Save-Roundtrip;
8. exportiert über `ExportResampledSlicesToBDVSourceCommand`;
9. überträgt das bereits nativ transformierte Raster auf das
   608×286×409-Zielraster;
10. installiert TIFF, NIfTI und Metadaten transaktional.

## 5. Bereits behobene Fehler und Sackgassen

### 5.1 Falscher ABBA-Ladebefehl

`ImportStdZipStateCommand` erwartet ein anderes Austauschformat mit
`meta.json`. Der autoritative Drei-Dateien-State wird jetzt korrekt über
`state_load`/`ABBAStateLoadCommand` geladen.

### 5.2 Race nach dem State-Laden

588 vorhandene Slices beweisen nur, dass die Create-Actions liefen. Deshalb
wartet der Renderer nach `state_load` und nach der Thickness-Aktion ausdrücklich
auf ABBAs Task-Queue.

### 5.3 Falsche Roundtrip-Schranke

Der frühere Code verlangte Hashgleichheit des vollständigen serialisierten
`BoundedRealTransform`. Das war falsch, weil `interval_min/max` source- und
sitzungsabhängig sind. Jetzt werden Registrierungstyp, Typkette und die
numerischen `srcPts`/`tgtPts` mit einer Toleranz von `1e-9 mm` geprüft.

Persistierende Beweise:

- `reports/native_abba/native_state_roundtrip.abba`;
- `reports/native_abba/transform_roundtrip_diff.json`.

`src/v34_debug_transform_roundtrip.py <package>` führt nur diesen kurzen
nativen Load/Save/Diff-Pfad aus.

### 5.4 Falsche Java-Library-Version

Der Python-Helper des Vendors nennt `imglib2-realtransform:4.0.3`; die reale
ABBA-Python-0.11.0-Fiji-Distribution verwendet 4.0.4. Der Runtime-Override auf
4.0.4 ist explizit, auf dieses eine Artefakt begrenzt und wird berichtet.

### 5.5 AP-Intensitätsmischung

Benachbarte histologische Schnitte dürfen nicht linear entlang AP gemischt
werden. Aktuell wird AP per nächster nativer Ebene ausgewählt; nur innerhalb
der SI/LR-Ebene wird linear interpoliert. Es erfolgt keine Slice-Normalisierung.

### 5.6 Optionaler Ch03-Fehler

Ein Fehler des nichtautoritativen Nissl-Kanals lässt den bereits installierten
Paxinos-Annotationsatlas bestehen und beendet den normalen Lauf mit Warnung.
Nur `--nissl-required` macht einen Nissl-Fehler absichtlich fatal.

## 6. Neueste Ursachenanalyse: falscher Fixed-Atlas-Weltrahmen

Der vendorte `AbbaMap` erzeugte bisher eine reine Scale-Affine. Damit belegte
der Paxinos-Fixed-Atlas in ABBA nur positive Weltkoordinaten:

```text
LR: 0 ... 16.32 mm
SI: 0 ... 11.40 mm
```

Die gespeicherte BigWarp-Registrierung und ihr `BoundedRealTransform` erwarten
dagegen einen um null zentrierten Canvas mit negativen und positiven LR-/SI-
Koordinaten. Das ist eine konkrete Frame-Inkonsistenz: Ein erheblicher Teil der
Fixed Source lag außerhalb der gespeicherten Bounds. Sie erklärt sowohl den
systematischen Rechtsversatz als auch mögliche vollständig abgeschnittene
Moving Sources wesentlich besser als eine Helligkeitshypothese.

Der aktuelle Branch berechnet deshalb den Ursprung der **Voxelzentren** des
Zielrasters:

```text
LR = -((409 - 1) * 0.04) / 2 = -8.16 mm
SI = -((286 - 1) * 0.04) / 2 = -5.70 mm
AP = 0.00 mm
```

Dieser identische Ursprung wird jetzt gleichzeitig:

1. der nativen ABBA Fixed Source über das Metadatum
   `abba_world_origin_xyz_mm` gegeben; und
2. beim Post-Export-Sampling verwendet.

Das gleichzeitige Ändern beider Seiten ist entscheidend. Frühere Versuche
änderten nur das Sampling-Gitter, während die Fixed Source bei Ursprung null
blieb; dadurch wurden zwei verschiedene Frames miteinander verrechnet.

Der Vendor-Adapter behält ohne das optionale Metadatum sein ursprüngliches
scale-only-Verhalten. Externe ABBA-Installationen und normale BrainGlobe-
Atlanten erhalten daher nicht automatisch einen neuen Ursprung.

## 7. Was noch nicht bewiesen ist

Die neue konsistente Origin-Korrektur ist durch Python-/Strukturtests geprüft,
aber noch nicht in einem vollständigen Windows-JVM-Lauf visuell validiert.
Insbesondere darf derzeit nicht behauptet werden, dass:

- der Rechtsversatz vollständig beseitigt ist;
- alle 588 registrierten Ebenen sichtbaren Nissl-Inhalt enthalten;
- die native 3-D-Export-Z-Abdeckung korrekt ist;
- die visuelle BigWarp-Parität bestanden ist.

## 8. Nächste Untersuchung für Claude

Claude soll den nächsten Windows-Lauf anhand der erzeugten Dateien untersuchen,
nicht anhand weiterer geschätzter Offsets.

### 8.1 Zuerst zu sichernde Reports

```text
reports/BUILD_SUMMARY.txt
reports/ch03_nissl/ch03_nissl_report.json
reports/native_abba/preflight.json
reports/native_abba/native_state_roundtrip.abba
reports/native_abba/transform_roundtrip_diff.json
reports/native_abba/native_diagnostics_summary.txt
reports/native_abba/native_diagnostics_summary.json
```

Erwartung für den Roundtrip:

```text
criterion: deformation_landmarks
deformation_mismatch_count: 0
```

Bounds dürfen abweichen, `srcPts`/`tgtPts` und die Transform-Typkette nicht.

### 8.2 Fixed-Frame-Konsistenz prüfen

Im Rekonstruktionsreport müssen beide Angaben identisch sein:

```text
fixed_source.native_fixed_source_origin_xyz_mm = [-8.16, -5.70, 0.0]
target_origin_xyz_mm                            = [-8.16, -5.70, 0.0]
```

Anschließend `spatial_diagnostics.median_centroid_delta_si_lr_um` prüfen. Ein
kleiner Rest von wenigen Voxeln kann visuell/biologisch sein; ein Median in der
Größenordnung mehrerer Millimeter beweist weiterhin einen systematischen
Frame- oder Achsenfehler. Nicht automatisch anhand dieses Medians verschieben.

### 8.3 Unsichtbare Ebenen klassifizieren

Für jede nullwertige Ziel-AP-Ebene sind mindestens drei Stufen zu unterscheiden:

1. War die Waxholm-Quelle bereits leer?
   `source_plane_intensity_diagnostics[source_id]`.
2. War die native BDV-Ebene leer, bevor Python sie aufs Zielraster legte?
   Diese Stufe ist im aktuellen Report noch nicht ausreichend source-id-genau
   instrumentiert und sollte bei verbleibenden Lücken ergänzt werden.
3. Wurde eine nichtleere native Ebene erst beim AP-/SI-/LR-Sampling vollständig
   abgeschnitten?
   `output_plane_intensity_diagnostics` zusammen mit
   `native_grid_diagnostics` und den Bounding-Boxen prüfen.

Wenn nach der Origin-Korrektur weiterhin viele Lücken bestehen, ist der nächste
Hauptverdacht nicht „zu dunkel“, sondern der kombinierte volumetrische Export:
588 diskrete ABBA-Slices werden über Slice-Thickness und ein 40-µm-Z-Raster in
eine 3-D-BDV-Source überführt. Dann muss anhand der nativen Source-Z-Positionen
geprüft werden, ob die Exportebenen wirklich eins-zu-eins den 588 Slice-IDs
entsprechen. Keine Ebenen synthetisch duplizieren oder auffüllen.

### 8.4 Helligkeit getrennt von Coverage bewerten

Die per-Slice-Felder `nonzero_pixels`, `nonzero_mean`, `maximum` und das
Output/Source-Verhältnis verwenden. Eine vollständig nullwertige Ebene ist ein
Coverage-/Clippingproblem. Eine nichtleere Ebene mit niedrigerem Mittelwert ist
eine Intensitäts- oder Interpolationsfrage. Diese Fälle dürfen nicht gemeinsam
als „Helligkeit“ behandelt werden.

### 8.5 AP und Z prüfen

Die gespeicherten Slice-Z-Positionen reichen ungefähr von 2.194 bis 25.675 mm,
während das 608er Zielraster bei 40 µm 24.32 mm umfasst. Der Builder verwendet
die validierte Sequenzzuordnung statt einer anatomischen Streckung. Falls die
Lücken posterior gehäuft sind, muss geprüft werden, ob der native BDV-Export die
State-Z-Positionen abschneidet oder neu phasiert. Die bestätigte
`nonempty_ap[1:589]`-Zuordnung darf dabei nicht verändert werden.

## 9. Verbotene Schnelllösungen

Claude soll ausdrücklich nicht:

- einen weiteren visuellen Offset raten;
- `srcPts` und `tgtPts` vertauschen;
- den Python-TPS-Renderer installieren;
- den alten `registered_slices_ImageJ_stack.tif` als Quelle/Fallback verwenden;
- Null-Ebenen auffüllen;
- Intensitäten pro Slice normalisieren, bevor geklärt ist, ob echte Daten
  abgeschnitten werden;
- Annotation, Ontologie, Label-IDs oder AP-Mapping verändern;
- `visual_parity_status` automatisch auf `passed` setzen;
- aus `native_backend_verified: true` visuelle oder wissenschaftliche Parität
  ableiten.

## 10. Konkretes Erfolgskriterium des nächsten Laufs

Ein technisch aussagekräftiger nächster Lauf muss mindestens zeigen:

```text
Nissl renderer backend: native_abba_0.11
Native backend verified: True
Transform roundtrip criterion: deformation_landmarks
Visual parity status: pending
Release eligible: False
```

Zusätzlich müssen der Landmark-Diff null echte Deformationsabweichungen zeigen,
die Fixed-/Target-Ursprünge identisch sein und die Zahl nullwertiger Ebenen
gegenüber den 151 Ebenen des letzten beobachteten Atlasses nachvollziehbar
aufgeschlüsselt werden. Erst danach ist eine erneute visuelle Bewertung der
Deckung sinnvoll.

