from pathlib import Path
import unittest


class JavaBootstrapScriptTests(unittest.TestCase):
    def test_adoptium_queries_do_not_use_invalid_project_or_vendor_filters(self):
        script = (Path(__file__).parents[1] / "src/bootstrap_native_java.ps1").read_text(encoding="utf-8")
        self.assertIn('/assets/version/${ApiVersion}?architecture=x64', script)
        self.assertIn('/assets/feature_releases/17/ga?architecture=x64', script)
        self.assertNotIn('&vendor=eclipse', script)
        self.assertNotIn('&project=jdk', script)

    def test_feature_release_schema_and_fallback_are_supported(self):
        script = (Path(__file__).parents[1] / "src/bootstrap_native_java.ps1").read_text(encoding="utf-8")
        self.assertIn('$_.version_data.semver', script)
        self.assertIn('$_.release_name -eq $PinnedRelease', script)
        self.assertIn('foreach ($CandidateApi in @($VersionApi, $FeatureApi))', script)
        self.assertIn('all Adoptium metadata requests failed', script)

    def test_release_selection_does_not_require_optional_jvm_impl_response_field(self):
        script = (Path(__file__).parents[1] / "src/bootstrap_native_java.ps1").read_text(encoding="utf-8")
        selection = script.split("$Asset = $NormalizedAssets | Where-Object", 1)[1].split("if ($null -eq $Asset)", 1)[0]
        self.assertNotIn("binary.jvm_impl", selection)
        self.assertIn("release_name -eq $PinnedRelease", selection)
        self.assertIn('JAVA_PLATFORM', script)

    def test_both_adoptium_binary_response_shapes_are_normalized(self):
        script = (Path(__file__).parents[1] / "src/bootstrap_native_java.ps1").read_text(encoding="utf-8")
        self.assertIn("$Item.binary", script)
        self.assertIn("$Item.binaries", script)
        self.assertIn("$NormalizedAssets", script)
        normalization = script.split("$NormalizedAssets = @()", 1)[1].split("$Asset = $NormalizedAssets", 1)[0]
        self.assertIn("binary = $Binary", normalization)
        self.assertIn("no binary entries", normalization)
