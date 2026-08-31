"""Builder-local ABBA 0.11 runtime policy and immutable-state preflight.

This module deliberately contains no mathematical transform implementation.
Rendering code may proceed only through the Java artifacts declared by the
vendored ABBA 0.11.0 source.
"""
from __future__ import annotations
import hashlib, json, os, zipfile
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VENDOR = ROOT / "vendor" / "abba_python_0_11_0"
STATE_SHA256 = "e038741ac9825c35e62c1e88658c3533a5e4da3460ebc9644275c4b6e48e7f06"
ABBA_VERSION = "0.11.0"
JAVA_DEPENDENCIES = (
 "net.imagej:imagej:2.16.0", "net.imagej:imagej-legacy:2.0.0",
 "ch.epfl.biop:ijl-utilities-wrappers:0.11.5", "ch.epfl.biop:ImageToAtlasRegister:0.11.0",
 "ch.epfl.biop:bigdataviewer-biop-tools:0.13.4", "sc.fiji:bigdataviewer-playground:0.12.0",
 "sc.fiji.bigdataviewer:bigdataviewer-playground-display:0.5.0", "sc.fiji:bigwarp_fiji:9.3.1",
 "net.imglib2:imglib2-realtransform:4.0.3",
)

class NativeRuntimeError(RuntimeError):
 def __init__(self, code: str, detail: str): super().__init__(f"{code}: {detail}"); self.code=code

@dataclass(frozen=True)
class RuntimePaths:
 root: Path
 @property
 def java(self): return self.root/"java"
 @property
 def jgo(self): return self.root/"jgo"
 @property
 def maven(self): return self.root/"maven"
 @property
 def imagej(self): return self.root/"imagej"
 @property
 def downloads(self): return self.root/"downloads"
 @property
 def temporary(self): return self.root/"tmp"
 @property
 def reports(self): return ROOT/"reports"/"native_abba"
 def create(self):
  for p in (self.java,self.jgo,self.maven,self.imagej,self.downloads,self.temporary,self.reports): p.mkdir(parents=True,exist_ok=True)
 def environment(self):
  self.create()
  return {"JAVA_HOME":str(self.java), "JGO_CACHE_DIR":str(self.jgo), "MAVEN_USER_HOME":str(self.maven),
          "SCYJAVA_CONFIG_DIR":str(self.imagej), "TMP":str(self.temporary), "TEMP":str(self.temporary),
          "BRAINGLOBE_DIR":str(ROOT/"data"/"brainglobe")}

def sha256(path: Path):
 h=hashlib.sha256()
 with path.open('rb') as f:
  for b in iter(lambda:f.read(8<<20),b''): h.update(b)
 return h.hexdigest()

def validate_vendor():
 required=("__init__.py","abba.py","abba_atlas.py","abba_map.py","abba_ontology.py")
 missing=[n for n in required if not (VENDOR/n).is_file()]
 if missing: raise NativeRuntimeError("VENDOR_LAYOUT",f"direct ABBA package layout incomplete: {missing}")
 text=(VENDOR/"abba.py").read_text(encoding="utf-8")
 missing_deps=[d for d in JAVA_DEPENDENCIES if repr(d) not in text]
 if missing_deps: raise NativeRuntimeError("RUNTIME_VERSION",f"vendored dependency pins changed: {missing_deps}")

def inspect_state(path: Path):
 if not path.is_file() or sha256(path)!=STATE_SHA256: raise NativeRuntimeError("ABBA_STATE_HASH","authoritative state missing or corrupt")
 with zipfile.ZipFile(path) as z:
  if set(z.namelist())!={"sources.json","state.json","_bdvdataset_0.xml"}: raise NativeRuntimeError("ABBA_STATE_LAYOUT","unexpected archive members")
  sources=json.loads(z.read("sources.json")); state=json.loads(z.read("state.json"))
 if state.get("version")!=ABBA_VERSION: raise NativeRuntimeError("RUNTIME_VERSION",f"state requires {state.get('version')}")
 ids=[s.get("source_id") for s in sources]
 if ids!=list(range(588)): raise NativeRuntimeError("SOURCE_REBINDING",f"expected source_id 0..587, got invalid sequence")
 names=[s.get("source_name","") for s in sources]
 expected=[f"whs_nissl_40um_ap_{i}.tiff" for i in range(189,777)]
 bad=[i for i,(a,b) in enumerate(zip(names,expected)) if not a.startswith(b)]
 if bad: raise NativeRuntimeError("SOURCE_REBINDING",f"source_ids {bad[:8]} do not map uniquely to Waxholm AP 189..776")
 slices=state.get("slices_state_list",[])
 if len(slices)!=588: raise NativeRuntimeError("ABBA_STATE_SLICES",f"expected 588 slice states, got {len(slices)}")
 types=sorted({a.get("type") for s in slices for a in s.get("actions",[])})
 return {"abba_state_sha256":STATE_SHA256,"abba_version":ABBA_VERSION,"source_count":588,"slice_state_count":588,
         "source_id_range":[0,587],"waxholm_ap_range":[189,776],"ap_direction":"anterior-to-posterior",
         "action_types":types,"java_dependencies":list(JAVA_DEPENDENCIES)}

def main():
 validate_vendor(); paths=RuntimePaths(ROOT/"data"/"native_abba_runtime"); report=inspect_state(ROOT/"resources"/"optional_ch03"/"nissl_registration_0_3_0"/"final_for_V_0_3.abba")
 report["cache_paths"]={k:str(v) for k,v in paths.environment().items()}; report["renderer_backend"]="native_abba_0.11"; report["native_backend_verified"]=False
 out=paths.reports/"preflight.json"; out.write_text(json.dumps(report,indent=2),encoding="utf-8"); print(out)
if __name__=='__main__': main()
