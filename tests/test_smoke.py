"""P0 smoke test: the package imports and the scaffolding is in place."""

import os
import unittest

import phases_oss

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))


class SmokeTest(unittest.TestCase):
    def test_package_version(self):
        self.assertTrue(phases_oss.__version__)
        self.assertRegex(phases_oss.__version__, r"^\d+\.\d+\.\d+")

    def test_scaffolding_files_exist(self):
        for name in ("LICENSE", ".gitignore", "README.md", "pyproject.toml"):
            with self.subTest(name=name):
                self.assertTrue(
                    os.path.exists(os.path.join(REPO_ROOT, name)),
                    "missing scaffolding file: %s" % name,
                )

    def test_license_is_apache(self):
        with open(os.path.join(REPO_ROOT, "LICENSE"), encoding="utf-8") as fh:
            text = fh.read()
        self.assertIn("Apache License", text)
        self.assertIn("Version 2.0", text)


if __name__ == "__main__":
    unittest.main()
