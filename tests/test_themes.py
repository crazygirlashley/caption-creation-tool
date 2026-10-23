"""Theme loading and upgrades (themes.py).

Run from the repo root with:  python -m unittest discover tests
"""

import json
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import themes  # noqa: E402

REPO_THEMES = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "themes")


class ThemeUpgradeTests(unittest.TestCase):
    """An installed copy (BASE_DIR) being updated from the bundled themes
    (RESOURCE_DIR) — the frozen-app situation."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.bundle = os.path.join(self.tmp.name, "bundle")
        self.user = os.path.join(self.tmp.name, "user", "themes")
        shutil.copytree(REPO_THEMES, os.path.join(self.bundle, "themes"))
        os.makedirs(self.user)
        self.saved = (themes.RESOURCE_DIR, themes._THEMES_DIR, themes._SETTINGS_PATH,
                      themes._palettes_cache)
        themes.RESOURCE_DIR = self.bundle
        themes._THEMES_DIR = self.user
        themes._SETTINGS_PATH = os.path.join(self.tmp.name, "user", "ui_settings.json")
        themes._palettes_cache = None

    def tearDown(self):
        (themes.RESOURCE_DIR, themes._THEMES_DIR, themes._SETTINGS_PATH,
         themes._palettes_cache) = self.saved
        self.tmp.cleanup()

    def _write(self, name, data):
        with open(os.path.join(self.user, name), "w", encoding="utf-8") as f:
            json.dump(data, f)

    def test_old_install_gets_new_themes_and_loses_stock_bubblegum(self):
        self._write("Dark.json", {"name": "Dark", "bg": "#2e2e2e", "panel": "#383838",
                                  "input_bg": "#3f3f3f", "hover": "#4a4a4a", "border": "#4f4f4f",
                                  "fg": "#e0e0e0", "muted_fg": "#a3a3a3", "select_bg": "#565656"})
        self._write("Bubblegum.json", themes._LEGACY_BUBBLEGUM)
        palettes = themes.get_palettes(force_reload=True)
        self.assertEqual(sorted(palettes), ["Bimbo", "Dark"])
        self.assertEqual(palettes["Dark"]["bg"], "#1f1c24")  # upgraded to version 2
        self.assertIn("accent", palettes["Bimbo"])

    def test_customized_bubblegum_is_kept(self):
        custom = dict(themes._LEGACY_BUBBLEGUM, bg="#123456")
        self._write("Bubblegum.json", custom)
        self.assertIn("Bubblegum", themes.get_palettes(force_reload=True))

    def test_saved_bubblegum_choice_means_bimbo(self):
        with open(themes._SETTINGS_PATH, "w", encoding="utf-8") as f:
            json.dump({"theme": "Bubblegum"}, f)
        themes._palettes_cache = None
        self.assertEqual(themes.load_theme(), "Bimbo")

    def test_newer_user_copy_is_not_downgraded(self):
        self._write("Dark.json", dict(json.load(open(os.path.join(REPO_THEMES, "Dark.json"))),
                                      version=99, bg="#000000"))
        self.assertEqual(themes.get_palettes(force_reload=True)["Dark"]["bg"], "#000000")


if __name__ == "__main__":
    unittest.main()
