import unittest
from pathlib import Path


ROOT = Path(__file__).parents[1]


class NativeMavenBootstrapTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.script = (ROOT / "src/bootstrap_native_maven.ps1").read_text(encoding="utf-8")

    def test_maven_version_and_archive_are_pinned(self):
        self.assertIn('$Version = "3.9.9"', self.script)
        self.assertIn("apache-maven-$Version-bin.zip", self.script)
        self.assertIn("archive.apache.org", self.script)

    def test_publisher_sha512_is_verified(self):
        self.assertIn('Invoke-WebRequest -Uri "$BaseUrl.sha512"', self.script)
        self.assertIn("Get-FileHash -Algorithm SHA512", self.script)
        self.assertIn("MAVEN_HASH", self.script)

    def test_install_and_manifest_are_builder_local(self):
        self.assertIn('data\\native_abba_runtime', self.script)
        self.assertIn('runtime-manifest.json', self.script)
        self.assertNotIn("Program Files", self.script)

    def test_does_not_overwrite_powershell_home_automatic_variable(self):
        # PowerShell variable names are case-insensitive and $HOME is read-only.
        self.assertNotIn("$Home =", self.script)
        self.assertIn("$MavenHome =", self.script)

    def test_maven_is_forced_to_use_builder_local_java(self):
        self.assertIn('$env:JAVA_HOME = $JavaHome', self.script)
        self.assertIn('MAVEN_JAVA_MISSING', self.script)


if __name__ == "__main__":
    unittest.main()
