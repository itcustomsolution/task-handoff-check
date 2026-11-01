"""Regression cases for defects that package metadata validation alone misses."""

import tempfile
import subprocess
import sys
import zipfile

import check_package_page
import unittest
from pathlib import Path

from check_package_page import check_description


class PackagePageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / "LICENSE").write_text("MIT\n")

    def check(self, text):
        return check_description(text, "example-tool", "1.2.3", self.root)

    def test_absolute_docs_and_matching_heading_work(self):
        rendered, links = self.check("[Usage](#usage)\n\n## Usage\n\n[MIT](https://github.com/itcustomsolution/example-tool/blob/main/LICENSE)")
        self.assertIn("<h2", rendered)
        self.assertEqual(len(links), 1)

    def test_relative_readme_link_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "absolute HTTPS"):
            self.check("[Changelog](CHANGELOG.md)")

    def test_missing_absolute_repository_file_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "missing repository"):
            self.check("[Missing](https://github.com/itcustomsolution/example-tool/blob/main/MISSING.md)")

    def test_stale_pinned_install_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "stale installation pin"):
            self.check('```sh\npython -m pip install "example-tool[pdf]==1.2.2"\n```')

    def test_current_pin_and_unpinned_install_work(self):
        self.check('```sh\npip install example-tool==1.2.3\npip install --upgrade example-tool\n```')

    def test_utf8_wheel_description_matches_readme(self):
        description = "# Example\n\nMIT © Example — Unicode description.\n"
        (self.root / "README.md").write_text(description)
        (self.root / "pyproject.toml").write_text('[project]\nname = "example-tool"\nversion = "1.2.3"\nreadme = "README.md"\n')
        wheel = self.root / "example.whl"
        metadata = "Metadata-Version: 2.4\nName: example-tool\nVersion: 1.2.3\nDescription-Content-Type: text/markdown\n\n" + description
        with zipfile.ZipFile(wheel, "w") as archive:
            archive.writestr("example_tool-1.2.3.dist-info/METADATA", metadata.encode("utf-8"))
        result = subprocess.run([sys.executable, check_package_page.__file__, str(wheel), "--root", str(self.root)], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_missing_heading_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "broken description anchor"):
            self.check("[Missing](#missing)")


if __name__ == "__main__":
    unittest.main()
