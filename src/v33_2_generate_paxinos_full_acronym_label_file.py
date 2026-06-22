import re, csv, json, zipfile, datetime
from pathlib import Path

RAW = Path('/mnt/data/Paxinos_Watson_Labels(2).txt')
CORTEX = Path('/mnt/data/Paxinos_Watson_Labels_Cortex(2).txt')
OUTROOT = Path('/mnt/data/v33_2_paxinos_full_acronym_label_file_package')
RES = OUTROOT/'resources'/'label_curation'
REPORT = OUTROOT/'reports'/'v33_2_paxinos_full_acronym_label_file'
SRC = OUTROOT/'src'
for d in [RES, REPORT, SRC]: d.mkdir(parents=True, exist_ok=True)

raw_pat = re.compile(r'^(\d+)\s+(\d+)\s+(\d+)\s+(\d+)\s+(\d+)\s+(\d+)\s+(\d+)\s+"(.*)"\s*$')
rows=[]
for line in RAW.read_text(encoding='utf-8').splitlines():
    m = raw_pat.match(line)
    if not m:
        raise ValueError(f'Could not parse raw label line: {line!r}')
    label_id = int(m.group(1)); rgb = tuple(int(m.group(i)) for i in [2,3,4]); name=m.group(8)
    rows.append({'label_id':label_id,'r':rgb[0],'g':rgb[1],'b':rgb[2],'paxinos_name':name})

ctx_pat = re.compile(r'^(\d+)\s+(\S+)\s+"(.*)"\s*$')
cortex={}
for line in CORTEX.read_text(encoding='utf-8').splitlines():
    m=ctx_pat.match(line)
    if not m:
        raise ValueError(f'Could not parse cortex label line: {line!r}')
    cortex[int(m.group(1))] = {'acronym':m.group(2), 'name':m.group(3)}

# Exact-name mappings for high-value/common neuroanatomical structures.
# Conservative: only very common abbreviations or direct Paxinos/cortex conventions.
manual = {
    # Amygdala and extended amygdala
    'anterior amygdaloid area': ('AA','manual_common_neuroanatomy','standard abbreviation for anterior amygdaloid area'),
    'anterior cortical amygdaloid nucleus': ('ACo','manual_common_neuroanatomy','standard abbreviation for anterior cortical amygdaloid nucleus'),
    'amygdalohippocampal area': ('AHi','manual_common_neuroanatomy','standard abbreviation for amygdalohippocampal area'),
    'amygdalohippocampal area, anterolateral part': ('AHiAL','manual_common_neuroanatomy','standard derived abbreviation: AHi + anterolateral'),
    'amygdalohippocampal area, posterolateral': ('AHiPL','manual_common_neuroanatomy','standard derived abbreviation: AHi + posterolateral'),
    'amygdalopiriform transition area': ('APir','manual_common_neuroanatomy','standard abbreviation for amygdalopiriform transition area'),
    'amygdalostriatal transition area': ('ASt','manual_common_neuroanatomy','standard abbreviation for amygdalostriatal transition area'),
    'basolateral amygdaloid nucleus': ('BLA','manual_common_neuroanatomy','common abbreviation for basolateral amygdala/amygdaloid nucleus'),
    'basolateral amygdaloid nucleus, anterior part': ('BLAa','manual_common_neuroanatomy','BLA + anterior part'),
    'basolateral amygdaloid nucleus, posterior part': ('BLAp','manual_common_neuroanatomy','BLA + posterior part'),
    'basolateral amygdaloid nucleus, ventral part': ('BLAv','manual_common_neuroanatomy','BLA + ventral part'),
    'basomedial amygdaloid nucleus': ('BMA','manual_common_neuroanatomy','common abbreviation for basomedial amygdala/amygdaloid nucleus'),
    'basomedial amygdaloid nucleus, anterior part': ('BMAa','manual_common_neuroanatomy','BMA + anterior part'),
    'basomedial amygdaloid nucleus, posterior part': ('BMAp','manual_common_neuroanatomy','BMA + posterior part'),
    'central amygdaloid nucleus': ('CeA','manual_common_neuroanatomy','common abbreviation for central amygdala/amygdaloid nucleus'),
    'central amygdaloid nucleus, capsular part': ('CeC','manual_common_neuroanatomy','common abbreviation for central amygdala capsular part'),
    'central amygdaloid nucleus, lateral division': ('CeL','manual_common_neuroanatomy','common abbreviation for central amygdala lateral division'),
    'central amygdaloid nucleus, medial division': ('CeM','manual_common_neuroanatomy','common abbreviation for central amygdala medial division'),
    'intercalated nuclei of the amygdala': ('I','manual_common_neuroanatomy','common Paxinos-style abbreviation for intercalated amygdala nuclei'),
    'intercalated amygdaloid nucleus, main part': ('I','manual_common_neuroanatomy','common Paxinos-style abbreviation for intercalated amygdala nucleus'),
    'lat amygdaloid nucleus': ('LA','manual_common_neuroanatomy','common abbreviation for lateral amygdala/amygdaloid nucleus'),
    'lateral amygdaloid nucleus, dorsolateral part': ('LAdl','manual_common_neuroanatomy','LA + dorsolateral'),
    'lateral amygdaloid nucleus, ventrolateral part': ('LAvl','manual_common_neuroanatomy','LA + ventrolateral'),
    'lateral amygdaloid nucleus, ventromedial part': ('LAvm','manual_common_neuroanatomy','LA + ventromedial'),
    'medial amygdaloid nucleus': ('MeA','manual_common_neuroanatomy','common abbreviation for medial amygdala/amygdaloid nucleus'),
    'medial amygdaloid nucleus, anterior part': ('MeA','manual_common_neuroanatomy','MeA anterior subdivision, review if separate suffix desired'),
    'medial amygdaloid nucleus, ant dorsal': ('MeAD','manual_common_neuroanatomy','MeA + anterodorsal'),
    'medial amygdaloid nucleus, anteroventral part': ('MeAV','manual_common_neuroanatomy','MeA + anteroventral'),
    'medial amygdaloid nucleus, posterodorsal part': ('MePD','manual_common_neuroanatomy','common abbreviation for medial amygdala posterodorsal'),
    'medial amygdaloid nucleus, posteroventral part': ('MePV','manual_common_neuroanatomy','common abbreviation for medial amygdala posteroventral'),
    'posterolateral cortical amygdaloid nucleus': ('PLCo','manual_common_neuroanatomy','standard abbreviation for posterolateral cortical amygdala'),
    'posteromedial cortical amygdaloid nucleus': ('PMCo','manual_common_neuroanatomy','standard abbreviation for posteromedial cortical amygdala'),
    'cortex-amygdala transition zone': ('CxA','manual_common_neuroanatomy','standard abbreviation for cortex-amygdala transition zone'),
    'cortex-amygdala transition zone, layer 1': ('CxA1','manual_common_neuroanatomy','CxA layer 1'),
    'rostral amygdalopiriform area': ('RAIP','manual_common_neuroanatomy','standard-derived abbreviation for rostral amygdalopiriform area'),
    'sublenticular extended amygdala': ('SLEA','manual_common_neuroanatomy','descriptive standard abbreviation'),
    'sublenticular extended amygdala, central part': ('SLEAc','manual_common_neuroanatomy','SLEA + central'),
    'sublenticular extended amygdala, medial part': ('SLEAm','manual_common_neuroanatomy','SLEA + medial'),

    # Septum / striatum / basal forebrain
    'lateral septal nucleus': ('LS','manual_common_neuroanatomy','common abbreviation for lateral septal nucleus'),
    'lateral septal nucleus, dorsal part': ('LSD','manual_common_neuroanatomy','common abbreviation for lateral septum dorsal'),
    'lateral septal nucleus, intermediate part': ('LSI','manual_common_neuroanatomy','common abbreviation for lateral septum intermediate'),
    'lateral septal nucleus, ventral part': ('LSV','manual_common_neuroanatomy','common abbreviation for lateral septum ventral'),
    'medial septal nucleus': ('MS','manual_common_neuroanatomy','common abbreviation for medial septal nucleus'),
    'nucleus of the horizontal limb of the diagonal band': ('HDB','manual_common_neuroanatomy','common abbreviation for horizontal diagonal band'),
    'bed nucleus of stria terminalis, fusiform part': ('BSTFU','manual_common_neuroanatomy','common derived abbreviation: BST fusiform part'),
    'bed nucleus of the anterior commissure': ('BAC','manual_common_neuroanatomy','descriptive common abbreviation'),
    'bed nucleus of the accessory olfactory tract': ('BAOT','manual_common_neuroanatomy','descriptive common abbreviation'),
    'nucleus of the commissural stria terminalis': ('CST','manual_common_neuroanatomy','common abbreviation for commissural stria terminalis nucleus'),
    'accumbens nucleus': ('Acb','manual_common_neuroanatomy','common Paxinos-style abbreviation for nucleus accumbens'),
    'accumbens nucleus, core': ('AcbC','manual_common_neuroanatomy','Acb core'),
    'accumbens nucleus, rostral pole': ('AcbR','manual_common_neuroanatomy','Acb rostral pole'),
    'accumbens nucleus, shell': ('AcbSh','manual_common_neuroanatomy','Acb shell'),
    'caudate putamen (striatum)': ('CPu','manual_common_neuroanatomy','common Paxinos-style abbreviation for caudate putamen'),
    'lateral stripe of the striatum': ('LSS','manual_common_neuroanatomy','descriptive abbreviation'),
    'globus pallidus': ('GP','manual_common_neuroanatomy','standard abbreviation'),
    'entopeduncular nucleus': ('EP','manual_common_neuroanatomy','standard abbreviation'),
    'basal nucleus (Meynert)': ('B','manual_common_neuroanatomy','common Paxinos-style abbreviation'),
    'cell bridges of the ventral striatum': ('CB','manual_common_neuroanatomy','descriptive abbreviation'),
    'claustrum': ('Cl','manual_common_neuroanatomy','common abbreviation'),
    'dorsal part of claustrum': ('DCl','manual_common_neuroanatomy','dorsal claustrum'),
    'dorsal endopiriform nucleus': ('DEn','manual_common_neuroanatomy','common abbreviation'),
    'intermediate endopiriform nucleus': ('IEn','manual_common_neuroanatomy','common abbreviation'),

    # Hippocampus
    'hippocampus': ('HP','manual_common_neuroanatomy','common broad abbreviation'),
    'hippocampal formation': ('HPF','manual_common_neuroanatomy','common abbreviation'),
    'hippocampus proper': ('CA','manual_common_neuroanatomy','common abbreviation for Ammon horn/CA fields'),
    'field CA1 of the hippocampus': ('CA1','manual_common_neuroanatomy','standard CA field'),
    'field CA2 of the hippocampus': ('CA2','manual_common_neuroanatomy','standard CA field'),
    'field CA3 of the hippocampus': ('CA3','manual_common_neuroanatomy','standard CA field'),
    'dentate gyrus': ('DG','manual_common_neuroanatomy','standard abbreviation'),
    'dorsal subiculum': ('DS','manual_common_neuroanatomy','dorsal subiculum'),
    'parasubiculum': ('PaS','manual_common_neuroanatomy','common abbreviation'),
    'postsubiculum': ('PoS','manual_common_neuroanatomy','common abbreviation'),
    'presubiculum': ('PrS','manual_common_neuroanatomy','common abbreviation'),
    'indusium griseum': ('IG','manual_common_neuroanatomy','standard abbreviation'),
    'fasciola cinereum': ('FC','manual_common_neuroanatomy','standard abbreviation'),
    'lacunosum moleculare layer of the hippocampus': ('SLM','manual_common_neuroanatomy','stratum lacunosum-moleculare'),
    'oriens layer of the hippocampus': ('SO','manual_common_neuroanatomy','stratum oriens'),
    'pyramidal cell layer of the hippocampus': ('SP','manual_common_neuroanatomy','stratum pyramidale'),
    'molecular layer of the dentate gyrus': ('ML','manual_common_neuroanatomy','molecular layer of dentate gyrus'),
    'granular layer of the dentate gyrus': ('GCL','manual_common_neuroanatomy','granule cell layer'),
    'polymorph layer of the dentate gyrus': ('PoDG','manual_common_neuroanatomy','polymorph layer of dentate gyrus'),

    # Hypothalamus / preoptic
    'hypothalamus': ('HY','manual_common_neuroanatomy','Allen-style broad hypothalamus abbreviation'),
    'anterior hypothalamic area': ('AHA','manual_common_neuroanatomy','standard abbreviation'),
    'anterior hypothalamic area, anterior part': ('AHAa','manual_common_neuroanatomy','AHA anterior'),
    'anterior hypothalamic area, central part': ('AHAc','manual_common_neuroanatomy','AHA central'),
    'anterior hypothalamic area, posterior part': ('AHAp','manual_common_neuroanatomy','AHA posterior'),
    'arcuate hypothalamic nucleus': ('Arc','manual_common_neuroanatomy','common Paxinos-style abbreviation'),
    'arcuate hypothalamic nucleus, dorsal part': ('ArcD','manual_common_neuroanatomy','Arc dorsal'),
    'arcuate hypothalamic nucleus, lateral part': ('ArcL','manual_common_neuroanatomy','Arc lateral'),
    'arcuate hypothalamic nucleus, lateroposterior part': ('ArcLP','manual_common_neuroanatomy','Arc lateroposterior'),
    'arcuate hypothalamic nucleus, medial part': ('ArcM','manual_common_neuroanatomy','Arc medial'),
    'arcuate hypothalamic nucleus, medial posterior part': ('ArcMP','manual_common_neuroanatomy','Arc medial posterior'),
    'dorsal hypothalamic area': ('DHA','manual_common_neuroanatomy','standard abbreviation'),
    'dorsomedial hypothalamic nucleus': ('DMH','manual_common_neuroanatomy','common abbreviation'),
    'dorsomedial hypothalamic nucleus, compact part': ('DMHc','manual_common_neuroanatomy','DMH compact'),
    'dorsomedial hypothalamic nucleus, dorsal part': ('DMHd','manual_common_neuroanatomy','DMH dorsal'),
    'dorsomedial hypothalamic nucleus, ventral part': ('DMHv','manual_common_neuroanatomy','DMH ventral'),
    'lateral hypothalamic area': ('LHA','manual_common_neuroanatomy','common abbreviation'),
    'lateroanterior hypothalamic nucleus': ('LA','manual_common_neuroanatomy','lateroanterior hypothalamic nucleus, review acronym conflict with lateral amygdala'),
    'magnocellular nucleus of the lateral hypothalamus': ('MCLH','manual_common_neuroanatomy','descriptive abbreviation'),
    'juxtaparaventricular part of lateral hypothalamus': ('JPLH','manual_common_neuroanatomy','descriptive abbreviation'),
    'peduncular part of lateral hypothalamus': ('PeF','manual_common_neuroanatomy','commonly related peduncular/perifornical lateral hypothalamus abbreviation, review'),
    'perifornical part of lateral hypothalamus': ('PeF','manual_common_neuroanatomy','common abbreviation for perifornical region, review'),
    'perifornical nucleus': ('PeF','manual_common_neuroanatomy','common abbreviation for perifornical nucleus'),
    'posterior hypothalamic nucleus': ('PH','manual_common_neuroanatomy','standard abbreviation'),
    'posterior hypothalamic area': ('PH','manual_common_neuroanatomy','standard abbreviation, review overlap with posterior hypothalamic nucleus'),
    'posterior hypothalamic area, dorsal part': ('PHD','manual_common_neuroanatomy','PH dorsal'),
    'paraventricular hypoth nucleus': ('PVH','manual_common_neuroanatomy','Allen-style abbreviation for paraventricular hypothalamic nucleus'),
    'paraventricular hypothalamic nucleus, anterior parvicellular part': ('PVHap','manual_common_neuroanatomy','PVH anterior parvicellular'),
    'paraventricular hypothalamic nucleus, dorsal cap': ('PVHdc','manual_common_neuroanatomy','PVH dorsal cap'),
    'paraventricular hypothalamic nucleus, lateral magnocellular part': ('PVHlm','manual_common_neuroanatomy','PVH lateral magnocellular'),
    'paraventricular hypothalamic nucleus, medial magnocellular part': ('PVHmm','manual_common_neuroanatomy','PVH medial magnocellular'),
    'paraventricular hypothalamic nucleus, medial parvicellular part': ('PVHmp','manual_common_neuroanatomy','PVH medial parvicellular'),
    'paraventricular hypothalamic nucleus, posterior part': ('PVHp','manual_common_neuroanatomy','PVH posterior'),
    'paraventricular hypothalamic nucleus, ventral part': ('PVHv','manual_common_neuroanatomy','PVH ventral'),
    'periventricular hypothalamic nucleus': ('Pe','manual_common_neuroanatomy','common Paxinos-style abbreviation for periventricular hypothalamic nucleus'),
    'preoptic recess of the 3rd ventricle': ('PR','manual_common_neuroanatomy','preoptic recess'),
    'anteroventral periventricular nucleus': ('AVPV','manual_common_neuroanatomy','standard abbreviation'),
    'medial preoptic area': ('MPA','manual_common_neuroanatomy','standard abbreviation'),
    'medial preoptic nucleus': ('MPN','manual_common_neuroanatomy','standard abbreviation'),
    'medial preoptic nucleus, central part': ('MPNc','manual_common_neuroanatomy','MPN central'),
    'medial preoptic nucleus, lateral part': ('MPNl','manual_common_neuroanatomy','MPN lateral'),
    'medial preoptic nucleus, medial part': ('MPNm','manual_common_neuroanatomy','MPN medial'),
    'median preoptic nucleus': ('MnPO','manual_common_neuroanatomy','standard abbreviation'),
    'lateral preoptic area': ('LPO','manual_common_neuroanatomy','standard abbreviation'),
    'magnocellular preoptic nucleus': ('MCPO','manual_common_neuroanatomy','standard abbreviation'),
    'posterodorsal preoptic nucleus': ('PDPO','manual_common_neuroanatomy','standard abbreviation'),
    'parastrial nucleus': ('PS','manual_common_neuroanatomy','standard abbreviation'),
    'suprachiasmatic nucleus': ('SCN','manual_common_neuroanatomy','standard abbreviation'),
    'subparaventricular zone': ('SPVZ','manual_common_neuroanatomy','standard abbreviation'),
    'retrochiiasmatic area': ('RCh','manual_common_neuroanatomy','standard abbreviation if present'),
    'retrochiasmatic area': ('RCh','manual_common_neuroanatomy','standard abbreviation'),
    'retrochiasmatic area, lateral part': ('RChL','manual_common_neuroanatomy','RCh lateral'),
    'median eminence': ('ME','manual_common_neuroanatomy','standard abbreviation'),
    'medial eminence, external layer': ('MEex','manual_common_neuroanatomy','median/medial eminence external layer, review source wording'),
    'medial eminence, internal layer': ('MEin','manual_common_neuroanatomy','median/medial eminence internal layer, review source wording'),
    'mammillary recess of the 3rd ventricle': ('MRe','manual_common_neuroanatomy','mammillary recess'),
    'medial mammillary nucleus, lateral part': ('MMl','manual_common_neuroanatomy','medial mammillary lateral'),
    'medial mammillary nucleus, medial part': ('MMm','manual_common_neuroanatomy','medial mammillary medial'),
    'medial mammillary nucleus, median part': ('MMn','manual_common_neuroanatomy','medial mammillary median'),
    'lateral mammillary nucleus': ('LM','manual_common_neuroanatomy','standard abbreviation'),
    'supramammillary nucleus': ('SuM','manual_common_neuroanatomy','standard abbreviation'),
    'submammillothalamic nucleus': ('SMT','manual_common_neuroanatomy','standard abbreviation'),
    'tuber cinereum area': ('TC','manual_common_neuroanatomy','descriptive abbreviation'),
    'tuberomammillary nucleus, dorsal part': ('TMd','manual_common_neuroanatomy','tuberomammillary dorsal'),
    'dorsal tuberomammillary nucleus': ('TMd','manual_common_neuroanatomy','tuberomammillary dorsal'),
    'ventral tuberomammillary nucleus': ('TMv','manual_common_neuroanatomy','tuberomammillary ventral'),
    'medial tuberal nucleus': ('MTu','manual_common_neuroanatomy','standard-derived abbreviation'),
    'gemini hypothalamic nucleus': ('Gem','manual_common_neuroanatomy','common abbreviation'),

    # Thalamus and epithalamus
    'thalamus': ('TH','manual_common_neuroanatomy','Allen-style broad abbreviation'),
    'anterodorsal thalamic nucleus': ('AD','manual_common_neuroanatomy','standard thalamic abbreviation'),
    'anteromedial thalamic nucleus': ('AM','manual_common_neuroanatomy','standard thalamic abbreviation'),
    'anteromedial thalamic nucleus, ventral part': ('AMv','manual_common_neuroanatomy','AM ventral'),
    'anteroventral thalamic nucleus': ('AV','manual_common_neuroanatomy','standard thalamic abbreviation'),
    'anterovent thalamic nucleus, dorsomedial part': ('AVDM','manual_common_neuroanatomy','AV dorsomedial; source name abbreviated'),
    'anteroventral thalamic nucleus, ventrolateral part': ('AVVL','manual_common_neuroanatomy','AV ventrolateral'),
    'angular thalamic nucleus': ('Ang','manual_common_neuroanatomy','common abbreviation'),
    'centrolateral thalamic nucleus': ('CL','manual_common_neuroanatomy','standard thalamic abbreviation'),
    'central medial thalamic nucleus': ('CM','manual_common_neuroanatomy','standard thalamic abbreviation'),
    'interanterodorsal thalamic nucleus': ('IAD','manual_common_neuroanatomy','standard abbreviation'),
    'interanteromedial thalamic nucleus': ('IAM','manual_common_neuroanatomy','standard abbreviation'),
    'intergeniculate leaf': ('IGL','manual_common_neuroanatomy','standard abbreviation'),
    'intramedullary thalamic area': ('IMD','manual_common_neuroanatomy','standard-derived abbreviation'),
    'intermediodorsal thalamic nucleus': ('IMD','manual_common_neuroanatomy','standard thalamic abbreviation, review duplicate'),
    'intralaminar thalamic nuclei': ('ILM','manual_common_neuroanatomy','descriptive abbreviation'),
    'laterodorsal thalamic nucleus': ('LD','manual_common_neuroanatomy','standard abbreviation'),
    'laterodorsal thalamic nucleus, dorsomedial part': ('LDDM','manual_common_neuroanatomy','LD dorsomedial'),
    'laterodorsal thalamic nucleus, ventrolateral part': ('LDVL','manual_common_neuroanatomy','LD ventrolateral'),
    'lateral posterior thalamic nucleus': ('LP','manual_common_neuroanatomy','standard abbreviation'),
    'lateral posterior thalamic nucleus, laterocaudal part': ('LPLC','manual_common_neuroanatomy','LP laterocaudal'),
    'lateral posterior thalamic nucleus, laterorostral part': ('LPLR','manual_common_neuroanatomy','LP laterorostral'),
    'lateral posterior thalamic nucleus, mediocaudal part': ('LPMC','manual_common_neuroanatomy','LP mediocaudal'),
    'lateral posterior thalamic nucleus, mediorostral part': ('LPMR','manual_common_neuroanatomy','LP mediorostral'),
    'dorsal lateral geniculate nucleus': ('DLG','manual_common_neuroanatomy','common Paxinos-style abbreviation'),
    'medial geniculate nucleus': ('MG','manual_common_neuroanatomy','standard abbreviation'),
    'medial geniculate nucleus, dorsal part': ('MGD','manual_common_neuroanatomy','MG dorsal'),
    'medial geniculate nucleus, medial part': ('MGM','manual_common_neuroanatomy','MG medial'),
    'medial geniculate nucleus, ventral part': ('MGV','manual_common_neuroanatomy','MG ventral'),
    'mediodorsal thalamic nucleus': ('MD','manual_common_neuroanatomy','standard abbreviation'),
    'mediodorsal thalamic nucleus, central part': ('MDC','manual_common_neuroanatomy','MD central'),
    'mediodorsal thalamic nucleus, lateral part': ('MDL','manual_common_neuroanatomy','MD lateral'),
    'mediodorsal thalamic nucleus, medial part': ('MDM','manual_common_neuroanatomy','MD medial'),
    'paracentral thalamic nucleus': ('PC','manual_common_neuroanatomy','standard abbreviation'),
    'parafascicular thalamic nucleus': ('PF','manual_common_neuroanatomy','standard abbreviation'),
    'paratenial thalamic nucleus': ('PT','manual_common_neuroanatomy','standard abbreviation'),
    'paraventricular thalamic nucleus': ('PVT','manual_common_neuroanatomy','standard abbreviation'),
    'paraventricular thalamic nucleus, anterior part': ('PVTa','manual_common_neuroanatomy','PVT anterior'),
    'paraventricular thalamic nucleus, posterior part': ('PVTp','manual_common_neuroanatomy','PVT posterior'),
    'posterior intralaminar thalamic nucleus': ('PIL','manual_common_neuroanatomy','standard abbreviation'),
    'posterior limitans thalamic nucleus': ('PoT','manual_common_neuroanatomy','common descriptive abbreviation, review'),
    'posterior thalamic nuclear group': ('Po','manual_common_neuroanatomy','common abbreviation'),
    'posterior thalamic nuclear group, triangular part': ('PoT','manual_common_neuroanatomy','posterior thalamic triangular'),
    'posteromedian thalamic nucleus': ('PoM','manual_common_neuroanatomy','standard abbreviation'),
    'reuniens thalamic nucleus': ('Re','manual_common_neuroanatomy','standard abbreviation'),
    'rhomboid thalamic nucleus': ('Rh','manual_common_neuroanatomy','standard abbreviation'),
    'submedius thalamic nucleus': ('Sub','manual_common_neuroanatomy','common abbreviation, review conflict'),
    'ventral anterior thalamic nucleus': ('VA','manual_common_neuroanatomy','standard abbreviation'),
    'ventral lateral thalamic nucleus': ('VL','manual_common_neuroanatomy','standard abbreviation'),
    'ventral medial thalamic nucleus': ('VM','manual_common_neuroanatomy','standard abbreviation'),
    'ventral posterolateral thalamic nucleus': ('VPL','manual_common_neuroanatomy','standard abbreviation'),
    'ventral posteromedial thalamic nucleus': ('VPM','manual_common_neuroanatomy','standard abbreviation'),
    'ventral posterior thalamic nucleus': ('VP','manual_common_neuroanatomy','standard abbreviation'),
    'paraxiphoid nucleus of thalamus': ('PaXi','manual_common_neuroanatomy','descriptive abbreviation'),
    'pineal gland': ('Pineal','manual_common_neuroanatomy','descriptive abbreviation'),
    'pineal stalk': ('PinealSt','manual_common_neuroanatomy','descriptive abbreviation'),
    'epith': ('EPI','manual_common_neuroanatomy','epithalamus/source abbreviated'),
    'habenula': ('Hb','manual_common_neuroanatomy','standard abbreviation'),
    'lateral habenular nucleus': ('LHb','manual_common_neuroanatomy','standard abbreviation'),
    'medial habenular nucleus': ('MHb','manual_common_neuroanatomy','standard abbreviation'),

    # Midbrain/hindbrain/PAG/raphe/VTA
    'periaqueductal gray': ('PAG','manual_common_neuroanatomy','standard abbreviation'),
    'dorsolateral periaqueductal gray': ('DLPAG','manual_common_neuroanatomy','standard subdivision abbreviation'),
    'dorsomedial periaqueductal gray': ('DMPAG','manual_common_neuroanatomy','standard subdivision abbreviation'),
    'lateral periaqueductal gray': ('LPAG','manual_common_neuroanatomy','standard subdivision abbreviation'),
    'ventrolateral periaqueductal gray': ('VLPAG','manual_common_neuroanatomy','standard subdivision abbreviation'),
    'pleoglial periaqueductal gray': ('PIPAG','manual_common_neuroanatomy','descriptive abbreviation, review'),
    'substantia nigra, compact part': ('SNc','manual_common_neuroanatomy','standard abbreviation'),
    'substantia nigra, reticular part': ('SNr','manual_common_neuroanatomy','standard abbreviation'),
    'substantia nigra, lateral part': ('SNL','manual_common_neuroanatomy','standard abbreviation'),
    'ventral tegmental area': ('VTA','manual_common_neuroanatomy','standard abbreviation'),
    'parabrachial pigmented nucleus of the VTA': ('PBP','manual_common_neuroanatomy','standard abbreviation'),
    'parainterfascicular nucleus of the VTA': ('PIF','manual_common_neuroanatomy','standard abbreviation'),
    'paranigral nucleus of the VTA': ('PN','manual_common_neuroanatomy','standard abbreviation'),
    'red nucleus': ('RN','manual_common_neuroanatomy','standard abbreviation'),
    'cuneiform nucleus': ('CnF','manual_common_neuroanatomy','standard abbreviation'),
    'cuneiform nucleus, dorsal part': ('CnFD','manual_common_neuroanatomy','CnF dorsal'),
    'cuneiform nucleus, intermediate part': ('CnFI','manual_common_neuroanatomy','CnF intermediate'),
    'cuneiform nucleus, ventral part': ('CnFV','manual_common_neuroanatomy','CnF ventral'),
    'dorsal raphe nucleus': ('DR','manual_common_neuroanatomy','standard abbreviation'),
    'dorsal raphe nucleus, caudal part': ('DRC','manual_common_neuroanatomy','DR caudal'),
    'dorsal raphe nucleus, dorsal part': ('DRD','manual_common_neuroanatomy','DR dorsal'),
    'dorsal raphe, interfascicular part': ('DRI','manual_common_neuroanatomy','DR interfascicular'),
    'dorsal raphe nucleus, lateral part': ('DRL','manual_common_neuroanatomy','DR lateral'),
    'dorsal raphe nucleus, ventral part': ('DRV','manual_common_neuroanatomy','DR ventral'),
    'median raphe nucleus': ('MnR','manual_common_neuroanatomy','standard abbreviation'),
    'paramedian raphe nucleus': ('PMnR','manual_common_neuroanatomy','standard abbreviation'),
    'pontine raphe nucleus': ('PnR','manual_common_neuroanatomy','standard abbreviation'),
    'raphe interpositus nucleus': ('RIP','manual_common_neuroanatomy','standard-derived abbreviation'),
    'caudal linear nucleus of the raphe': ('CLi','manual_common_neuroanatomy','standard abbreviation'),
    'rostral linear nucleus of raphe': ('RLi','manual_common_neuroanatomy','standard abbreviation'),
    'locus coeruleus': ('LC','manual_common_neuroanatomy','standard abbreviation'),
    'Barrington\'s nucleus': ('Bar','manual_common_neuroanatomy','standard abbreviation'),
    'Kolliker-Fuse nucleus': ('KF','manual_common_neuroanatomy','standard abbreviation'),
    'parabrachial nucleus': ('PB','manual_common_neuroanatomy','standard abbreviation'),
    'lateral parabrachial nucleus': ('LPB','manual_common_neuroanatomy','standard abbreviation'),
    'lateral parabrachial nucleus, central part': ('LPBC','manual_common_neuroanatomy','LPB central'),
    'lateral parabrachial nucleus, crescent part': ('LPBCr','manual_common_neuroanatomy','LPB crescent'),
    'lateral parabrachial nucleus, dorsal part': ('LPBD','manual_common_neuroanatomy','LPB dorsal'),
    'lateral parabrachial nucleus, external part': ('LPBE','manual_common_neuroanatomy','LPB external'),
    'lateral parabrachial nucleus, internal part': ('LPBI','manual_common_neuroanatomy','LPB internal'),
    'lateral parabrachial nucleus, superior part': ('LPBS','manual_common_neuroanatomy','LPB superior'),
    'lateral parabrachial nucleus, ventral part': ('LPBV','manual_common_neuroanatomy','LPB ventral'),
    'medial parabrachial nucleus': ('MPB','manual_common_neuroanatomy','standard abbreviation'),
    'medial parabrachial nucleus external part': ('MPBE','manual_common_neuroanatomy','MPB external'),
    'pedunculopontine tegmental nucleus': ('PPTg','manual_common_neuroanatomy','standard abbreviation'),
    'laterodorsal tegmental nucleus': ('LDTg','manual_common_neuroanatomy','standard abbreviation'),
    'laterodorsal tegmental nucleus, ventral part': ('LDTgV','manual_common_neuroanatomy','LDTg ventral'),

    # Olfactory
    'anterior olfactory nucleus': ('AON','manual_common_neuroanatomy','standard abbreviation'),
    'anterior olfactory nucleus, dorsal part': ('AOD','manual_common_neuroanatomy','AON dorsal'),
    'anterior olfactory nucleus, external part': ('AOE','manual_common_neuroanatomy','AON external'),
    'anterior olfactory nucleus, lateral part': ('AOL','manual_common_neuroanatomy','AON lateral'),
    'anterior olfactory nucleus, medial part': ('AOM','manual_common_neuroanatomy','AON medial'),
    'anterior olfactory nucleus, posterior part': ('AOP','manual_common_neuroanatomy','AON posterior'),
    'anterior olfactory nucleus, ventral part': ('AOV','manual_common_neuroanatomy','AON ventral'),
    'anterior olfactory nucleus, ventroposterior part': ('AOVP','manual_common_neuroanatomy','AON ventroposterior'),
    'accessory olfactory bulb': ('AOB','manual_common_neuroanatomy','standard abbreviation'),
    'main olfactory bulb': ('MOB','manual_common_neuroanatomy','standard abbreviation'),
    'olfactory cortex': ('OLF','manual_common_neuroanatomy','Allen-style broad abbreviation'),
    'piriform cortex': ('Pir','manual_common_neuroanatomy','standard abbreviation'),
    'nucleus of the lateral olfactory tract': ('LOT','manual_common_neuroanatomy','standard abbreviation'),
    'nucleus of the lateral olfactory tract, layer 1': ('LOT1','manual_common_neuroanatomy','LOT layer 1'),
    'olfactory nerve layer': ('ONL','manual_common_neuroanatomy','standard abbreviation'),
    'external plexiform layer of the olfactory bulb': ('EPl','manual_common_neuroanatomy','external plexiform layer'),
    'external plexiform layer of the accessory olfactory bulb': ('AOBEPl','manual_common_neuroanatomy','AOB external plexiform layer'),
    'glomerular layer of the olfactory bulb': ('Gl','manual_common_neuroanatomy','glomerular layer'),
    'glomerular layer of the accessory olfactory bulb': ('AOBGl','manual_common_neuroanatomy','AOB glomerular layer'),
    'granular cell layer of the olfactory bulb': ('GrO','manual_common_neuroanatomy','granular cell layer olfactory bulb'),
    'granule cell layer of the accessory olfactory bulb': ('AOBGr','manual_common_neuroanatomy','AOB granule cell layer'),
    'mitral cell layer of the olfactory bulb': ('Mi','manual_common_neuroanatomy','mitral layer'),
    'mitral cell layer of the accessory olfactory bulb': ('AOBMi','manual_common_neuroanatomy','AOB mitral layer'),
    'vomeronasal nerve': ('VN','manual_common_neuroanatomy','standard abbreviation'),

    # Brain divisions / ventricles / tracts common
    'whole brain': ('root','paxinos_raw_broad_structure','root/whole-brain label'),
    'forebrain': ('FB','manual_common_neuroanatomy','broad division abbreviation'),
    'midbrain': ('MB','manual_common_neuroanatomy','broad division abbreviation'),
    'hindbrain': ('HB','manual_common_neuroanatomy','broad division abbreviation'),
    'medulla-oblongolata': ('MY','manual_common_neuroanatomy','Allen-style abbreviation for medulla/myelencephalon, source spelling retained'),
    'Diencephalon': ('DIEN','manual_common_neuroanatomy','broad diencephalon abbreviation'),
    'cerebral cortex': ('CTX','manual_common_neuroanatomy','Allen-style broad abbreviation'),
    'isocortex': ('ISO','manual_common_neuroanatomy','Allen-style abbreviation'),
    'cerebellum': ('CB','manual_common_neuroanatomy','standard broad abbreviation'),
    '3rd ventricle': ('3V','manual_common_neuroanatomy','standard abbreviation'),
    '4th ventricle': ('4V','manual_common_neuroanatomy','standard abbreviation'),
    'lateral ventricle': ('LV','manual_common_neuroanatomy','standard abbreviation'),
    'dorsal 3rd ventricle': ('D3V','manual_common_neuroanatomy','standard derived abbreviation'),
    'central canal': ('cc','manual_common_neuroanatomy','standard abbreviation, review case'),
    'aqueduct': ('Aq','manual_common_neuroanatomy','standard abbreviation'),
    'interventricular foramen': ('IVF','manual_common_neuroanatomy','standard derived abbreviation'),
    'infundibulum': ('INF','manual_common_neuroanatomy','standard abbreviation'),
    'obex': ('obex','manual_common_neuroanatomy','descriptive label retained'),

    # Selected cranial nerve nuclei/tracts
    'dorsal motor nucleus of vagus': ('DMX','manual_common_neuroanatomy','standard abbreviation'),
    'vagus nerve': ('X','manual_common_neuroanatomy','cranial nerve X'),
    'accessory nerve nucleus': ('Acs','manual_common_neuroanatomy','standard-derived abbreviation'),
    'hypoglossal nucleus': ('XII','manual_common_neuroanatomy','cranial nerve XII nucleus'),
    'hypoglossal nucleus, geniohyoid part': ('XIIg','manual_common_neuroanatomy','hypoglossal geniohyoid part'),
    'root of hypoglossal nerve': ('RtXII','manual_common_neuroanatomy','root of cranial nerve XII'),
    'oculomotor nucleus': ('III','manual_common_neuroanatomy','cranial nerve III nucleus'),
    'oculomotor nucleus, parvicellular part': ('IIIpc','manual_common_neuroanatomy','oculomotor parvicellular part'),
    'oculomotor nerve': ('3n','manual_common_neuroanatomy','cranial nerve III'),
    'trochlear nucleus': ('IV','manual_common_neuroanatomy','cranial nerve IV nucleus'),
    'trochlear nerve': ('4n','manual_common_neuroanatomy','cranial nerve IV'),
    'abducens nucleus': ('VI','manual_common_neuroanatomy','cranial nerve VI nucleus'),
    'root of abducens nerve': ('RtVI','manual_common_neuroanatomy','root of cranial nerve VI'),
    'facial nucleus': ('VII','manual_common_neuroanatomy','cranial nerve VII nucleus'),
    'facial nerve': ('7n','manual_common_neuroanatomy','cranial nerve VII'),
    'vestibulocochlear nerve': ('8n','manual_common_neuroanatomy','cranial nerve VIII'),
    'glossopharyngeal nerve': ('9n','manual_common_neuroanatomy','cranial nerve IX'),
    'optic nerve': ('opt','manual_common_neuroanatomy','optic nerve'),
    'trigeminal ganglion (Gasseri)': ('Vg','manual_common_neuroanatomy','trigeminal ganglion'),
    'motor trigeminal nucleus': ('Mo5','manual_common_neuroanatomy','common abbreviation'),
    'principal sensory trigeminal nucleus': ('Pr5','manual_common_neuroanatomy','common abbreviation'),
    'spinal trigeminal nucleus': ('Sp5','manual_common_neuroanatomy','common abbreviation'),
    'spinal trigeminal nucleus, oral part': ('Sp5O','manual_common_neuroanatomy','common abbreviation'),
    'spinal trigeminal nucleus, interpolar part': ('Sp5I','manual_common_neuroanatomy','common abbreviation'),
    'spinal trigeminal nucleus, caudal part': ('Sp5C','manual_common_neuroanatomy','common abbreviation'),
    'area postrema': ('AP','manual_common_neuroanatomy','standard abbreviation'),
    'nucleus of the solitary tract': ('NTS','manual_common_neuroanatomy','standard abbreviation'),
    'solitary tract': ('sol','manual_common_neuroanatomy','standard abbreviation'),
    'ambiguus nucleus': ('Amb','manual_common_neuroanatomy','standard abbreviation'),
    'cuneate nucleus': ('Cu','manual_common_neuroanatomy','standard abbreviation'),
    'gracile nucleus': ('Gr','manual_common_neuroanatomy','standard abbreviation'),
    'inferior olive, principal nucleus': ('IOPr','manual_common_neuroanatomy','inferior olive principal nucleus'),
    'inferior olivary nucleus': ('IO','manual_common_neuroanatomy','standard abbreviation'),
    'inferior colliculus': ('IC','manual_common_neuroanatomy','standard abbreviation'),
    'superior colliculus': ('SC','manual_common_neuroanatomy','standard abbreviation'),
    'central nucleus of the inferior colliculus': ('CIC','manual_common_neuroanatomy','standard abbreviation'),
    'dorsal cortex of the inferior colliculus': ('DCIC','manual_common_neuroanatomy','standard abbreviation'),
    'external cortex of the inferior colliculus': ('ECIC','manual_common_neuroanatomy','standard abbreviation'),
}

# Abbreviation components for algorithmic generation.
repls = [
    (r'\baccessory\b','A'),(r'\banterior\b','A'),(r'\bposterior\b','P'),(r'\bdorsal\b','D'),(r'\bventral\b','V'),
    (r'\bmedial\b','M'),(r'\blateral\b','L'),(r'\brostral\b','R'),(r'\bcaudal\b','C'),(r'\bcentral\b','C'),
    (r'\bintermediate\b','I'),(r'\bexternal\b','E'),(r'\binternal\b','I'),(r'\bsuperior\b','S'),(r'\binferior\b','I'),
    (r'\bparvicellular\b','pc'),(r'\bmagnocellular\b','mc'),(r'\bcompact\b','c'),(r'\bloose\b','l'),
    (r'\bsubcompact\b','sc'),(r'\bdeep\b','D'),(r'\bgranular\b','Gr'),(r'\bmolecular\b','Mol'),(r'\bprimary\b','1'),(r'\bsecondary\b','2'),
    (r'\bnucleus\b','N'),(r'\bnuclei\b','N'),(r'\barea\b','A'),(r'\bcortex\b','Cx'),(r'\bcomplex\b','Cx'),(r'\btract\b','tr'),
    (r'\bnerve\b','n'),(r'\bventricle\b','V'),(r'\blayer\b','L'),(r'\bgroup\b','G'),(r'\bformation\b','F'),
    (r'\bamygdaloid\b','A'),(r'\bamygdala\b','A'),(r'\bhypothalamic\b','H'),(r'\bthalamic\b','T'),(r'\bseptal\b','S'),
    (r'\bhippocampal\b','H'),(r'\bpreoptic\b','PO'),(r'\btegmental\b','Tg'),(r'\breticular\b','R'),(r'\bolfactory\b','O'),
    (r'\bcerebellar\b','Cb'),(r'\blobule\b','Lob'),(r'\bcommissure\b','com'),(r'\bpeduncle\b','ped'),
]
STOP = set('of the and in to from with part division region zone subnucleus nucleus nuclei area layer cortex complex group tract nerve cell cells'.split())
DIR_WORDS = {'anterior':'A','posterior':'P','dorsal':'D','ventral':'V','medial':'M','lateral':'L','rostral':'R','caudal':'C','central':'C','intermediate':'I','external':'E','internal':'I','superior':'S','inferior':'I','compact':'c','parvicellular':'pc','magnocellular':'mc'}

def clean_acro(a):
    a = a.strip()
    a = re.sub(r'[^A-Za-z0-9_\-]', '', a)
    if not a: return ''
    return a[:32]

def make_from_words(name):
    # Try to preserve numeric/cortical style labels.
    s = name.lower().replace('assocn','association').replace('associatin','association').replace('hypoth','hypothalamic').replace('ant dorsal','anterodorsal')
    s = re.sub(r'\([^)]*\)', '', s)
    # Specific layer pattern: layer 6a of cortex -> L6aCtx
    m = re.match(r'layer\s+([0-9]+[a-z]?)\s+of\s+cortex', s)
    if m: return f'L{m.group(1)}Ctx'
    # Ordinal cerebellar lobules.
    m = re.match(r'(\d+)(?:st|nd|rd|th)?(?:\s+and\s+(\d+)(?:st|nd|rd|th)?)?\s+cerebellar\s+lobule', s)
    if m:
        return 'CbL' + m.group(1) + (('_'+m.group(2)) if m.group(2) else '')
    # A1/A2 transmitter groups keep beginning code.
    m = re.match(r'([acb]\d+)\s+', s, flags=re.I)
    if m: return m.group(1).upper()
    # Ventricles and cranial nerves with digits.
    if '3rd ventricle' in s: return '3V'
    if '4th ventricle' in s: return '4V'
    # Acronym from directional + key noun chunks.
    words = re.findall(r'[A-Za-z0-9]+', s)
    # Remove words that add little; keep descriptors and anatomic bases.
    parts=[]
    for w in words:
        if w in STOP: continue
        if w.isdigit(): parts.append(w); continue
        if w in DIR_WORDS: parts.append(DIR_WORDS[w]); continue
        # Some common roots
        roots = {
            'amygdaloid':'A','amygdala':'A','hypothalamic':'H','hypothalamus':'HY','thalamic':'T','thalamus':'TH','septal':'S','septum':'S',
            'hippocampus':'HP','hippocampal':'H','cerebellar':'Cb','cerebellum':'Cb','olfactory':'O','optic':'Opt','auditory':'Aud','visual':'Vis',
            'motor':'Mo','sensory':'S','trigeminal':'5','facial':'7','vagus':'X','abducens':'6','oculomotor':'3','trochlear':'4','hypoglossal':'12',
            'preoptic':'PO','periaqueductal':'PAG','geniculate':'G','mammillary':'M','raphe':'R','tegmental':'Tg','reticular':'R','vestibular':'Ve',
            'cochlear':'Coch','parabrachial':'PB','interpeduncular':'IP','colliculus':'Col','subiculum':'Sub','piriform':'Pir','entorhinal':'Ent',
            'cingulate':'Cg','orbital':'Orb','insular':'Ins','striatum':'Str','pallidus':'Pal','putamen':'Pu','accumbens':'Acb',
        }
        parts.append(roots.get(w, w[:3].title()))
    if not parts:
        words = re.findall(r'[A-Za-z0-9]+', s)
        parts = [w[:3].title() for w in words[:3]] or ['Lbl']
    ac = ''.join(parts[:5])
    # Avoid crazy length
    return clean_acro(ac)

# Known exact line typos to fix in proposed_name but keep original in raw column
name_fixes = {
    'temporal associatin cortex':'temporal association cortex',
    'frontal assocn cortex':'frontal association cortex',
    'paraventricular hypoth nucleus':'paraventricular hypothalamic nucleus',
    'anterovent thalamic nucleus, dorsomedial part':'anteroventral thalamic nucleus, dorsomedial part',
    'medulla-oblongolata':'medulla oblongata',
}

# Generate raw candidate rows
out=[]
for r in rows:
    lid = r['label_id']; name = r['paxinos_name']; norm=name.strip()
    proposed_name = name_fixes.get(norm, norm)
    if norm == '-------' or set(norm) <= {'-'}:
        ac = f'UNL{lid}'
        basis='paxinos_placeholder'
        detail='Raw Paxinos label name is placeholder "-------"; no anatomical acronym assigned. Stable unique placeholder used.'
        conf='low'
        status='do_not_apply_until_review'
    elif lid in cortex:
        ac = cortex[lid]['acronym']
        # Keep --- as CTX container style? Replace with ID specific safe container if ---.
        if ac == '---':
            ac = f'CTX{lid}'
            basis='paxinos_cortex_file_container'
            detail='Cortex file gives acronym "---" for broad container; unique CTX+ID placeholder assigned.'
            conf='medium'
            status='pending_review'
        else:
            basis='paxinos_cortex_file_exact_id'
            detail=f'Exact ID match in Paxinos_Watson_Labels_Cortex.txt: {cortex[lid]["acronym"]}'
            conf='official_local'
            status='approved_candidate'
    elif norm in manual:
        ac,basis,detail = manual[norm]
        conf='high'
        status='pending_review'
    else:
        ac = make_from_words(norm)
        basis='rule_generated_from_paxinos_name'
        detail='Algorithmic acronym generated from Paxinos raw name; requires manual review before use as curated label.'
        conf='low'
        status='pending_review'
    ac = clean_acro(ac)
    out.append({**r, 'proposed_acronym_raw':ac, 'proposed_name':proposed_name, 'acronym_basis':basis, 'basis_detail':detail, 'confidence':conf, 'review_status':status})

# Ensure unique acronyms while preserving originals in raw field.
seen={}
for item in out:
    ac = item['proposed_acronym_raw'] or f'LBL{item["label_id"]}'
    if ac in seen:
        # duplicates official/cortex are dangerous; suffix label id and mark.
        new_ac = f'{ac}_{item["label_id"]}'
        item['duplicate_resolution'] = f'Acronym {ac} duplicated with label_id {seen[ac]}; suffixed with label_id for uniqueness.'
        item['proposed_acronym'] = clean_acro(new_ac)
        if item['review_status'] == 'approved_candidate':
            item['review_status'] = 'pending_review'
            item['confidence'] = 'medium'
            item['basis_detail'] += ' Duplicate resolution applied; review before automatic use.'
    else:
        item['duplicate_resolution'] = ''
        item['proposed_acronym'] = ac
        seen[ac] = item['label_id']

# Re-check duplicates after suffix
assert len({x['proposed_acronym'] for x in out}) == len(out)

# Write pattern txt: ID<TAB>Acronym<TAB>"Name"
pattern = RES/'Paxinos_Watson_Labels_Acronyms.txt'
with pattern.open('w', encoding='utf-8', newline='') as f:
    for item in out:
        nm = item['proposed_name'].replace('"','\"')
        f.write(f'{item["label_id"]}\t{item["proposed_acronym"]}\t"{nm}"\n')

# Write with basis CSV
basis_csv = RES/'Paxinos_Watson_Labels_Acronyms_with_basis.csv'
fields = ['label_id','r','g','b','paxinos_name','proposed_acronym','proposed_name','acronym_basis','basis_detail','confidence','review_status','duplicate_resolution']
with basis_csv.open('w', encoding='utf-8', newline='') as f:
    w=csv.DictWriter(f, fieldnames=fields)
    w.writeheader(); w.writerows([{k:item.get(k,'') for k in fields} for item in out])

# High confidence subset
high_csv = RES/'Paxinos_Watson_Labels_Acronyms_high_confidence_review.csv'
with high_csv.open('w', encoding='utf-8', newline='') as f:
    w=csv.DictWriter(f, fieldnames=fields)
    w.writeheader(); w.writerows([{k:item.get(k,'') for k in fields} for item in out if item['confidence'] in ('official_local','high')])

# Low confidence subset
low_csv = RES/'Paxinos_Watson_Labels_Acronyms_needs_review.csv'
with low_csv.open('w', encoding='utf-8', newline='') as f:
    w=csv.DictWriter(f, fieldnames=fields)
    w.writeheader(); w.writerows([{k:item.get(k,'') for k in fields} for item in out if item['confidence'] not in ('official_local','high')])

legend = RES/'ACRONYM_BASIS_LEGEND.md'
legend.write_text('''# Acronym basis legend\n\nThis directory contains a complete Paxinos/Watson label acronym proposal file.\n\n## Files\n\n- `Paxinos_Watson_Labels_Acronyms.txt`\n  - Pattern-compatible file: `label_id<TAB>acronym<TAB>"name"`.\n  - Intended as the human-readable/importable full-label mapping.\n\n- `Paxinos_Watson_Labels_Acronyms_with_basis.csv`\n  - Same labels plus provenance/basis for every acronym.\n  - Use this file for review and future automation.\n\n- `Paxinos_Watson_Labels_Acronyms_high_confidence_review.csv`\n  - Rows with either direct local Paxinos cortex acronym evidence or high-confidence common neuroanatomy.\n\n- `Paxinos_Watson_Labels_Acronyms_needs_review.csv`\n  - Low/medium-confidence generated, placeholder, or container labels.\n\n## Basis categories\n\n- `paxinos_cortex_file_exact_id`\n  - Acronym comes directly from `Paxinos_Watson_Labels_Cortex.txt` by exact label ID.\n\n- `paxinos_cortex_file_container`\n  - Cortex file uses `---` for a broad container entry. A unique CTX+ID placeholder was assigned.\n\n- `manual_common_neuroanatomy`\n  - Acronym assigned from widely used neuroanatomical conventions and Allen/Paxinos-style naming.\n  - Review recommended before automatic application.\n\n- `rule_generated_from_paxinos_name`\n  - Acronym generated algorithmically from the Paxinos raw name.\n  - Not a curated anatomical acronym yet.\n\n- `paxinos_placeholder`\n  - Raw Paxinos name is `-------`; a stable `UNL<label_id>` placeholder was assigned.\n  - Do not treat as anatomy.\n\n## Automation policy\n\nFor future builder integration, apply only rows with explicit reviewed approval. Suggested safe policy:\n\n```text\napply if review_status in {approved, approved_candidate} AND confidence in {official_local, high}\nnever apply placeholder rows as anatomical labels\nnever change annotation IDs or voxel values\n```\n\nIDs and annotation volumes must remain unchanged. This file is a metadata layer only.\n''', encoding='utf-8')

readme = OUTROOT/'README_V33_2_PAXINOS_FULL_ACRONYM_LABEL_FILE.md'
readme.write_text(f'''# V33.2 Paxinos full acronym label file\n\nGenerated: {datetime.datetime.now().isoformat(timespec='seconds')}\n\nThis package creates a complete Paxinos/Watson label acronym proposal file from the uploaded raw label files.\n\nInput files:\n\n- `Paxinos_Watson_Labels.txt`\n- `Paxinos_Watson_Labels_Cortex.txt`\n\nCore output:\n\n- `resources/label_curation/Paxinos_Watson_Labels_Acronyms.txt`\n- `resources/label_curation/Paxinos_Watson_Labels_Acronyms_with_basis.csv`\n\nThe text file follows the same simple pattern as the cortex label file:\n\n```text\nlabel_id<TAB>acronym<TAB>"name"\n```\n\nThis package does not modify annotation volumes or BrainGlobe atlas files.\n\nRecommended GitHub integration path:\n\n```text\nresources/label_curation/Paxinos_Watson_Labels_Acronyms.txt\nresources/label_curation/Paxinos_Watson_Labels_Acronyms_with_basis.csv\nresources/label_curation/ACRONYM_BASIS_LEGEND.md\n```\n\nImportant policy:\n\n- Use raw Paxinos names as primary ID/name source.\n- Use `Paxinos_Watson_Labels_Cortex.txt` as direct source for cortex acronyms.\n- Use common/Allen-style neuroanatomy only as documented proposed acronym basis.\n- Keep all low-confidence generated acronyms review-only.\n- Never change annotation IDs or voxel values.\n''', encoding='utf-8')

# Summary/report
from collections import Counter
cnt_basis=Counter(x['acronym_basis'] for x in out)
cnt_conf=Counter(x['confidence'] for x in out)
cnt_status=Counter(x['review_status'] for x in out)
report = {
    'version':'V33.2 Paxinos Full Acronym Label File',
    'generated_at': datetime.datetime.now().isoformat(timespec='seconds'),
    'does_modify_atlas': False,
    'input_files': {'raw_labels': str(RAW), 'cortex_labels': str(CORTEX)},
    'row_count': len(out),
    'unique_acronyms': len({x['proposed_acronym'] for x in out}),
    'cortex_exact_rows': cnt_basis.get('paxinos_cortex_file_exact_id',0),
    'placeholder_rows': cnt_basis.get('paxinos_placeholder',0),
    'basis_counts': dict(cnt_basis),
    'confidence_counts': dict(cnt_conf),
    'review_status_counts': dict(cnt_status),
    'outputs': {
        'pattern_txt': str(pattern.relative_to(OUTROOT)),
        'basis_csv': str(basis_csv.relative_to(OUTROOT)),
        'high_confidence_csv': str(high_csv.relative_to(OUTROOT)),
        'needs_review_csv': str(low_csv.relative_to(OUTROOT)),
        'legend': str(legend.relative_to(OUTROOT)),
    },
    'automation_recommendation': 'Integrate as a metadata proposal layer. Apply only reviewed/approved rows; never modify annotation IDs or voxel values.'
}
(REPORT/'v33_2_paxinos_full_acronym_label_file_report.json').write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding='utf-8')
summary = REPORT/'v33_2_paxinos_full_acronym_label_file_summary.txt'
summary.write_text(f'''V33.2 Paxinos Full Acronym Label File\n========================================================================\nGenerated: {report['generated_at']}\nDoes modify atlas: False\n\nInputs:\n- {RAW.name}\n- {CORTEX.name}\n\nOutputs:\n- resources/label_curation/Paxinos_Watson_Labels_Acronyms.txt\n- resources/label_curation/Paxinos_Watson_Labels_Acronyms_with_basis.csv\n- resources/label_curation/Paxinos_Watson_Labels_Acronyms_high_confidence_review.csv\n- resources/label_curation/Paxinos_Watson_Labels_Acronyms_needs_review.csv\n- resources/label_curation/ACRONYM_BASIS_LEGEND.md\n\nCounts:\n- total labels: {len(out)}\n- unique acronyms: {len({x['proposed_acronym'] for x in out})}\n- cortex exact acronyms: {cnt_basis.get('paxinos_cortex_file_exact_id',0)}\n- common/manual high-confidence acronyms: {cnt_basis.get('manual_common_neuroanatomy',0)}\n- rule-generated acronyms: {cnt_basis.get('rule_generated_from_paxinos_name',0)}\n- raw placeholder rows: {cnt_basis.get('paxinos_placeholder',0)}\n\nConfidence counts:\n{json.dumps(dict(cnt_conf), indent=2, ensure_ascii=False)}\n\nReview status counts:\n{json.dumps(dict(cnt_status), indent=2, ensure_ascii=False)}\n\nRecommended next step:\n- Review `Paxinos_Watson_Labels_Acronyms_with_basis.csv`.\n- Use this as the project-integrated metadata source for later automatic label curation.\n- Do not apply low-confidence generated labels automatically.\n- Keep annotation IDs and voxel values unchanged.\n''', encoding='utf-8')

# Copy generator script into package
Path(__file__).rename(SRC/'v33_2_generate_paxinos_full_acronym_label_file.py')

# Zip
zip_path = Path('/mnt/data/v33_2_paxinos_full_acronym_label_file_package.zip')
if zip_path.exists(): zip_path.unlink()
with zipfile.ZipFile(zip_path, 'w', compression=zipfile.ZIP_DEFLATED) as z:
    for p in OUTROOT.rglob('*'):
        z.write(p, p.relative_to(OUTROOT.parent))
print(zip_path)
print(json.dumps(report, indent=2, ensure_ascii=False))
