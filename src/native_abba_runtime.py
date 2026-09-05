"""Builder-local ABBA 0.11 runtime validation and PyImageJ startup.

No transform is evaluated in Python.  This module only establishes the isolated
JVM and verifies the exact Java API used by the vendored ABBA package.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib
import importlib.metadata
import json
import os
import shutil
import subprocess
import sys
import traceback
import zipfile
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VENDOR = ROOT / "vendor" / "abba_python_0_11_0"
STATE = ROOT / "resources/optional_ch03/nissl_registration_0_3_0/final_for_V_0_3.abba"
STATE_SHA256 = "e038741ac9825c35e62c1e88658c3533a5e4da3460ebc9644275c4b6e48e7f06"
ABBA_VERSION = "0.11.0"
VENDORED_JAVA_DEPENDENCIES = (
    "net.imagej:imagej:2.16.0",
    "net.imagej:imagej-legacy:2.0.0",
    "ch.epfl.biop:ijl-utilities-wrappers:0.11.5",
    "ch.epfl.biop:ImageToAtlasRegister:0.11.0",
    "ch.epfl.biop:bigdataviewer-biop-tools:0.13.4",
    "sc.fiji:bigdataviewer-playground:0.12.0",
    "sc.fiji.bigdataviewer:bigdataviewer-playground-display:0.5.0",
    "sc.fiji:bigwarp_fiji:9.3.1",
    "net.imglib2:imglib2-realtransform:4.0.3",
    "com.formdev:flatlaf:3.5.1",
    "ch.epfl.biop:bigdataviewer-image-loaders:0.11.2",
    "ch.epfl.biop:atlas:0.3.2",
    "org.scijava:scijava-ui-swing:1.0.3",
    "net.imglib2:imglib2:7.1.4",
    "org.janelia.saalfeldlab:n5:3.5.1",
    "org.janelia.saalfeldlab:n5-blosc:1.1.1",
    "org.janelia.saalfeldlab:n5-ij:4.4.1",
    "org.janelia.saalfeldlab:n5-aws-s3:4.3.0",
    "org.janelia.saalfeldlab:n5-google-cloud:5.1.0",
    "org.janelia.saalfeldlab:n5-viewer_fiji:6.1.2",
    "org.janelia.saalfeldlab:n5-zarr:1.5.1",
    "org.janelia.saalfeldlab:n5-universe:2.3.0",
)
# ABBA-Python 0.11.0's Python helper declares realtransform 4.0.3, but the
# released ABBA 0.11.0 Fiji installation actually ships 4.0.4.  This library
# evaluates the saved ThinplateSplineTransform and its iterative inverse, so
# reproducing the release runtime takes precedence over the stale helper pin.
JAVA_DEPENDENCY_OVERRIDES = {
    "net.imglib2:imglib2-realtransform": {
        "vendored_python_coordinate": "net.imglib2:imglib2-realtransform:4.0.3",
        "abba_0_11_release_coordinate": "net.imglib2:imglib2-realtransform:4.0.4",
        "reason": "match the JAR shipped by the ABBA-Python 0.11.0 Fiji release",
    },
}
JAVA_DEPENDENCIES = tuple(
    JAVA_DEPENDENCY_OVERRIDES.get(":".join(coordinate.split(":")[:2]), {}).get(
        "abba_0_11_release_coordinate", coordinate
    )
    for coordinate in VENDORED_JAVA_DEPENDENCIES
)
REQUIRED_JAVA_CLASSES = (
    "ch.epfl.biop.atlas.aligner.command.ABBAStartCommand",
    "ch.epfl.biop.atlas.aligner.command.ABBAStateLoadCommand",
    "ch.epfl.biop.atlas.aligner.command.ImportSliceFromSourcesCommand",
    "ch.epfl.biop.atlas.aligner.command.ExportResampledSlicesToBDVSourceCommand",
    "ch.epfl.biop.atlas.struct.Atlas",
    "sc.fiji.bdvpg.sourceandconverter.SourceAndConverterHelper",
    "net.imglib2.realtransform.ThinplateSplineTransform",
)
JGO_REPOSITORIES = {
    "scijava.public": "https://maven.scijava.org/content/groups/public",
    "ome.releases": "https://artifacts.openmicroscopy.org/artifactory/maven/",
}


class NativeRuntimeError(RuntimeError):
    def __init__(self, code: str, detail: str):
        super().__init__(f"{code}: {detail}")
        self.code = code


@dataclass(frozen=True)
class RuntimePaths:
    root: Path = ROOT / "data/native_abba_runtime"

    @property
    def java(self) -> Path: return self.root / "java"
    @property
    def jgo(self) -> Path: return self.root / "jgo"
    @property
    def maven(self) -> Path: return self.root / "maven"
    @property
    def maven_home(self) -> Path: return self.maven / "apache-maven-3.9.9"
    @property
    def maven_user_home(self) -> Path: return self.root / "maven_user_home"
    @property
    def maven_repository(self) -> Path: return self.maven_user_home / ".m2" / "repository"
    @property
    def maven_settings(self) -> Path: return self.maven_user_home / ".m2" / "settings.xml"
    @property
    def imagej(self) -> Path: return self.root / "imagej"
    @property
    def downloads(self) -> Path: return self.root / "downloads"
    @property
    def temporary(self) -> Path: return self.root / "tmp"
    @property
    def brainglobe(self) -> Path: return ROOT / "data/brainglobe"
    @property
    def reports(self) -> Path: return ROOT / "reports/native_abba"

    def create(self) -> None:
        for path in (self.java, self.jgo, self.maven, self.maven_repository, self.maven_user_home,
                     self.imagej, self.downloads,
                     self.temporary, self.brainglobe, self.reports):
            path.mkdir(parents=True, exist_ok=True)

    def environment(self) -> dict[str, str]:
        self.create()
        return {
            "JAVA_HOME": str(self.java),
            "JGO_CACHE_DIR": str(self.jgo),
            "MAVEN_USER_HOME": str(self.maven_user_home),
            "MAVEN_OPTS": (
                f'-Duser.home="{self.maven_user_home}" '
                f'-Dmaven.repo.local="{self.maven_repository}"'
            ),
            # Maven 3.9 appends MAVEN_ARGS to every invocation, including the
            # subprocess launched internally by legacy jgo.
            "MAVEN_ARGS": f'-s "{self.maven_settings}"',
            "SCYJAVA_CONFIG_DIR": str(self.imagej),
            "CJDK_CACHE_DIR": str(self.java),
            "TMP": str(self.temporary),
            "TEMP": str(self.temporary),
            "BRAINGLOBE_DIR": str(self.brainglobe),
            # jgo 1.0.6 reads ~/.jgo.rc; keep that file and every related
            # resolver input inside the builder rather than the Windows user profile.
            "HOME": str(self.maven_user_home),
            "USERPROFILE": str(self.maven_user_home),
        }

    def activate(self) -> dict[str, str]:
        env = self.environment()
        os.environ.update(env)
        os.environ["PATH"] = os.pathsep.join(
            (str(self.java / "bin"), str(self.maven_home / "bin"), os.environ.get("PATH", ""))
        )
        return env


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(8 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def validate_vendor() -> None:
    required = ("__init__.py", "abba.py", "abba_atlas.py", "abba_map.py", "abba_ontology.py")
    missing = [name for name in required if not (VENDOR / name).is_file()]
    if missing:
        raise NativeRuntimeError("VENDOR_LAYOUT", f"direct ABBA package layout incomplete: {missing}")
    source = (VENDOR / "abba.py").read_text(encoding="utf-8")
    missing_dependencies = [
        coordinate for coordinate in VENDORED_JAVA_DEPENDENCIES
        if repr(coordinate) not in source
    ]
    if missing_dependencies:
        raise NativeRuntimeError("RUNTIME_VERSION", f"vendored dependency pins changed: {missing_dependencies}")
    for class_name in REQUIRED_JAVA_CLASSES[:4]:
        short_name = class_name.rsplit(".", 1)[-1]
        if short_name not in source:
            raise NativeRuntimeError("VENDOR_API", f"vendored ABBA does not reference {class_name}")


def inspect_state(path: Path = STATE) -> dict:
    if not path.is_file() or sha256(path) != STATE_SHA256:
        raise NativeRuntimeError("ABBA_STATE_HASH", "authoritative state missing or corrupt")
    with zipfile.ZipFile(path) as archive:
        if set(archive.namelist()) != {"sources.json", "state.json", "_bdvdataset_0.xml"}:
            raise NativeRuntimeError("ABBA_STATE_LAYOUT", "unexpected archive members")
        sources = json.loads(archive.read("sources.json"))
        state = json.loads(archive.read("state.json"))
        xml = archive.read("_bdvdataset_0.xml").decode("utf-8")
    if state.get("version") != ABBA_VERSION:
        raise NativeRuntimeError("RUNTIME_VERSION", f"state requires {state.get('version')}")
    ids = [source.get("source_id") for source in sources]
    if ids != list(range(588)):
        raise NativeRuntimeError("SOURCE_REBINDING", "expected source_id 0..587 in exact order")
    expected = [f"whs_nissl_40um_ap_{index}.tiff" for index in range(189, 777)]
    bad = [index for index, (source, name) in enumerate(zip(sources, expected))
           if not source.get("source_name", "").startswith(name)]
    if bad:
        raise NativeRuntimeError(
            "SOURCE_REBINDING",
            f"invalid source/AP identity for {len(bad)}/588 sources (first: {bad[:16]})",
        )
    slices = state.get("slices_state_list", [])
    if len(slices) != 588:
        raise NativeRuntimeError("ABBA_STATE_SLICES", f"expected 588 slice states, got {len(slices)}")
    return {
        "abba_state_sha256": STATE_SHA256,
        "abba_version": ABBA_VERSION,
        "source_count": 588,
        "slice_state_count": 588,
        "source_id_range": [0, 587],
        "waxholm_ap_range": [189, 776],
        "ap_direction": "anterior-to-posterior",
        "historical_qupath_reference_present": "project.qpproj" in xml,
        "action_types": sorted({action.get("type") for item in slices for action in item.get("actions", [])}),
        "java_dependencies": list(JAVA_DEPENDENCIES),
        "vendored_java_dependencies": list(VENDORED_JAVA_DEPENDENCIES),
        "java_dependency_overrides": JAVA_DEPENDENCY_OVERRIDES,
        "expected_python_bridge_versions": dict(PYTHON_BRIDGE_VERSIONS),
    }


def validate_java(paths: RuntimePaths) -> Path:
    executable = paths.java / "bin" / ("java.exe" if os.name == "nt" else "java")
    if not executable.is_file():
        raise NativeRuntimeError(
            "JAVA_COMPONENT_MISSING",
            f"builder-local Java is missing: {executable}; runtime bootstrap must populate it",
        )
    marker = paths.java / "runtime-manifest.json"
    if not marker.is_file():
        raise NativeRuntimeError("JAVA_CACHE_CORRUPT", f"runtime marker is missing: {marker}")
    try:
        manifest = json.loads(marker.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise NativeRuntimeError("JAVA_CACHE_CORRUPT", f"invalid runtime marker: {exc}") from exc
    if manifest.get("pinned_version") != "17.0.14+7":
        raise NativeRuntimeError(
            "JAVA_VERSION", f"expected 17.0.14+7, marker reports {manifest.get('pinned_version')!r}"
        )
    try:
        completed = subprocess.run(
            [str(executable), "-version"], capture_output=True, text=True, timeout=30, check=False
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise NativeRuntimeError("JAVA_CACHE_CORRUPT", f"builder-local java cannot execute: {exc}") from exc
    version_output = (completed.stdout + completed.stderr).strip()
    if completed.returncode != 0:
        raise NativeRuntimeError("JAVA_CACHE_CORRUPT", f"java -version failed: {version_output}")
    if '17.0.14' not in version_output:
        raise NativeRuntimeError("JAVA_VERSION", f"expected Java 17.0.14, got: {version_output}")
    return executable



def validate_maven(paths: RuntimePaths) -> Path:
    executable = paths.maven_home / "bin" / ("mvn.cmd" if os.name == "nt" else "mvn")
    marker = paths.maven / "runtime-manifest.json"
    if not executable.is_file():
        raise NativeRuntimeError("MAVEN_COMPONENT_MISSING", f"builder-local Maven is missing: {executable}")
    if not marker.is_file():
        raise NativeRuntimeError("MAVEN_CACHE_CORRUPT", f"runtime marker is missing: {marker}")
    try:
        manifest = json.loads(marker.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise NativeRuntimeError("MAVEN_CACHE_CORRUPT", f"invalid runtime marker: {exc}") from exc
    if manifest.get("pinned_version") != "3.9.9":
        raise NativeRuntimeError("MAVEN_VERSION", f"expected 3.9.9, marker reports {manifest.get('pinned_version')!r}")
    try:
        command = [str(executable), "--version"]
        if os.name == "nt":
            command = [os.environ.get("COMSPEC", "cmd.exe"), "/d", "/c", *command]
        completed = subprocess.run(
            command, capture_output=True, text=True, timeout=30, check=False,
            env=os.environ.copy(),
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise NativeRuntimeError("MAVEN_CACHE_CORRUPT", f"builder-local Maven cannot execute: {exc}") from exc
    output = (completed.stdout + completed.stderr).strip()
    if completed.returncode != 0:
        raise NativeRuntimeError("MAVEN_CACHE_CORRUPT", f"mvn --version failed: {output}")
    if "Apache Maven 3.9.9" not in output:
        raise NativeRuntimeError("MAVEN_VERSION", f"expected Apache Maven 3.9.9, got: {output}")
    return executable


def configure_jgo(paths: RuntimePaths) -> Path:
    """Write the isolated legacy-jgo repository configuration used by ABBA 0.11."""
    paths.create()
    config = paths.maven_user_home / ".jgo.rc"
    lines = ["[repositories]"]
    lines.extend(f"{name} = {url}" for name, url in JGO_REPOSITORIES.items())
    expected = "\n".join(lines) + "\n"
    if config.exists() and config.read_text(encoding="utf-8") != expected:
        raise NativeRuntimeError("JGO_CACHE_CORRUPT", f"unexpected builder-local repository config: {config}")
    config.write_text(expected, encoding="utf-8")
    return config


def configure_maven_settings(paths: RuntimePaths) -> Path:
    """Activate the external repositories required by ABBA's pinned transitives.

    jgo 1.0.6 does not reliably propagate its rc repositories to every Maven
    resolution on Windows.  A builder-local Maven settings profile is therefore
    the authoritative repository configuration.
    """
    paths.maven_settings.parent.mkdir(parents=True, exist_ok=True)
    repositories = "\n".join(
        """        <repository>
          <id>{name}</id>
          <url>{url}</url>
          <releases><enabled>true</enabled></releases>
          <snapshots><enabled>false</enabled></snapshots>
        </repository>""".format(name=name, url=url)
        for name, url in JGO_REPOSITORIES.items()
    )
    expected = """<?xml version="1.0" encoding="UTF-8"?>
<settings xmlns="http://maven.apache.org/SETTINGS/1.2.0">
  <profiles>
    <profile>
      <id>native-abba-repositories</id>
      <repositories>
{repositories}
      </repositories>
    </profile>
  </profiles>
  <activeProfiles>
    <activeProfile>native-abba-repositories</activeProfile>
  </activeProfiles>
</settings>
""".format(repositories=repositories)
    if paths.maven_settings.exists() and paths.maven_settings.read_text(encoding="utf-8") != expected:
        raise NativeRuntimeError(
            "MAVEN_CACHE_CORRUPT", f"unexpected builder-local Maven settings: {paths.maven_settings}"
        )
    paths.maven_settings.write_text(expected, encoding="utf-8")
    try:
        ET.parse(paths.maven_settings)
    except ET.ParseError as exc:
        raise NativeRuntimeError("MAVEN_CACHE_CORRUPT", f"invalid generated Maven settings: {exc}") from exc
    return paths.maven_settings

def install_vendor_package() -> Path:
    """Expose the direct vendor directory under its original package name."""
    target = ROOT / "data/native_abba_runtime/python/abba_python"
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists() or target.is_symlink():
        if target.is_symlink() or target.is_file(): target.unlink()
        else: shutil.rmtree(target)
    shutil.copytree(VENDOR, target, ignore=shutil.ignore_patterns("*.backup_*", "a", "__pycache__"))
    sys.path.insert(0, str(target.parent))
    return target



PYTHON_BRIDGE_VERSIONS = {
    "pyimagej": "1.5.0",
    "scyjava": "1.10.2",
    "JPype1": "1.5.1",
    "jgo": "1.0.6",
}


def validate_python_bridge() -> dict[str, str]:
    """Reject transitive bridge upgrades that break the pinned PyImageJ API."""
    observed: dict[str, str] = {}
    for distribution, expected in PYTHON_BRIDGE_VERSIONS.items():
        try:
            actual = importlib.metadata.version(distribution)
        except importlib.metadata.PackageNotFoundError as exc:
            raise NativeRuntimeError(
                "PYIMAGEJ_COMPONENT_MISSING", f"required distribution is missing: {distribution}=={expected}"
            ) from exc
        observed[distribution] = actual
        if actual != expected:
            raise NativeRuntimeError(
                "PYIMAGEJ_VERSION",
                f"expected {distribution}=={expected}, found {actual}; rerun pip install -r requirements.txt",
            )
    try:
        jgo_package = importlib.import_module("jgo")
    except ImportError as exc:
        raise NativeRuntimeError("PYIMAGEJ_COMPONENT_MISSING", f"cannot import pinned jgo: {exc}") from exc
    if not hasattr(jgo_package, "jgo"):
        raise NativeRuntimeError(
            "PYIMAGEJ_API",
            "pinned PyImageJ/ScyJava requires `from jgo import jgo`, but the installed package does not expose it",
        )
    return observed

def initialize_native_api(paths: RuntimePaths | None = None):
    """Start isolated PyImageJ and resolve only Java classes proven by vendor source."""
    paths = paths or RuntimePaths()
    paths.activate()
    validate_java(paths)
    configure_jgo(paths)
    configure_maven_settings(paths)
    validate_maven(paths)
    install_vendor_package()
    validate_python_bridge()
    try:
        imagej = importlib.import_module("imagej")
        scyjava = importlib.import_module("scyjava")
    except ImportError as exc:
        raise NativeRuntimeError("PYIMAGEJ_COMPONENT_MISSING", str(exc)) from exc
    try:
        ij = imagej.init(list(JAVA_DEPENDENCIES), mode="headless")
        resolved = {class_name: scyjava.jimport(class_name) for class_name in REQUIRED_JAVA_CLASSES}
    except Exception as exc:
        raise NativeRuntimeError("NATIVE_API_INITIALIZATION", str(exc)) from exc
    return ij, resolved


def write_preflight(paths: RuntimePaths, verify_api: bool) -> Path:
    validate_vendor()
    report = inspect_state()
    report["cache_paths"] = paths.activate()
    report["renderer_backend"] = "native_abba_0.11"
    report["native_backend_verified"] = False
    if verify_api:
        _, classes = initialize_native_api(paths)
        report["native_api_classes"] = sorted(classes)
        report["python_bridge_versions"] = validate_python_bridge()
        report["native_api_initialized"] = True
    output = paths.reports / "preflight.json"
    output.write_text(json.dumps(report, indent=2), encoding="utf-8")
    return output



def write_failure_report(paths: RuntimePaths, exc: NativeRuntimeError) -> Path:
    """Persist preflight failures even when JVM initialization never completes."""
    paths.create()
    report = {
        "renderer_backend": "native_abba_0.11",
        "native_backend_verified": False,
        "visual_parity_status": "not_applicable",
        "release_eligible": False,
        "error_code": exc.code,
        "error_message": str(exc),
        "exception_type": type(exc).__name__,
        "traceback": traceback.format_exc(),
        "cache_paths": paths.environment(),
        "java_dependencies": list(JAVA_DEPENDENCIES),
        "vendored_java_dependencies": list(VENDORED_JAVA_DEPENDENCIES),
        "java_dependency_overrides": JAVA_DEPENDENCY_OVERRIDES,
        "expected_python_bridge_versions": dict(PYTHON_BRIDGE_VERSIONS),
    }
    output = paths.reports / "preflight_failure.json"
    output.write_text(json.dumps(report, indent=2), encoding="utf-8")
    return output

def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--verify-api", action="store_true")
    args = parser.parse_args(argv)
    try:
        print(write_preflight(RuntimePaths(), args.verify_api))
        return 0
    except NativeRuntimeError as exc:
        failure_report = write_failure_report(RuntimePaths(), exc)
        print(f"ERROR [{exc.code}]: {exc}", file=sys.stderr)
        print(f"Failure report: {failure_report}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
