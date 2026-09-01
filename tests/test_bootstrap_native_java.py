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
