"""Check the integration boundary against the actual pinned upstream sources.

Run after scripts/prepare_engine.py. PSP_DOCK_PPSSPP_SOURCE may select an existing
verified clone. These checks need Python and Git, not a phone/TV or Android SDK.
"""
import hashlib
import importlib.util
import os
from pathlib import Path
import tempfile
import unittest
import xml.etree.ElementTree as ET

PROJECT = Path(__file__).resolve().parents[1]
SOURCE = Path(os.environ.get("PSP_DOCK_PPSSPP_SOURCE", PROJECT / "third_party/ppsspp"))


def load_script(name):
    spec = importlib.util.spec_from_file_location(name, PROJECT / "scripts" / (name + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


PATCH = load_script("patch_engine_java")
PREPARE = load_script("prepare_engine")


class EngineEmbeddingTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        PREPARE.verify(SOURCE)
        cls.upstream = SOURCE / "android/src/org/ppsspp/ppsspp"
        cls.original = (cls.upstream / "PpssppActivity.java").read_bytes()
        cls.adapted = PATCH.patch_activity(cls.original)

    def test_unreviewed_source_is_rejected(self):
        with self.assertRaises(ValueError):
            PATCH.patch_activity(self.original + b"\n// changed lifecycle\n")

    def test_generation_preserves_upstream_and_omits_extra_launcher(self):
        before = hashlib.sha256(self.original).hexdigest()
        with tempfile.TemporaryDirectory() as tmp:
            PATCH.generate(SOURCE, Path(tmp))
            generated = Path(tmp) / "org/ppsspp/ppsspp"
            self.assertFalse((generated / "ShortcutActivity.java").exists())
            self.assertEqual((generated / "NativeApp.java").read_bytes(),
                             (self.upstream / "NativeApp.java").read_bytes())
            self.assertEqual((generated / "PpssppActivity.java").read_text(), self.adapted)
        self.assertEqual(hashlib.sha256((self.upstream / "PpssppActivity.java").read_bytes()).hexdigest(), before)

    def test_refuses_to_patch_upstream_in_place(self):
        with self.assertRaises(ValueError):
            PATCH.generate(SOURCE, SOURCE / "generated-test")

    def test_closed_marker_requires_completed_native_shutdown_and_explicit_finish(self):
        start = self.adapted.index("protected void onDestroy()")
        end = self.adapted.index("protected void onStart()")
        destroy = self.adapted[start:end]
        self.assertLess(destroy.index("NativeApp.shutdown();"), destroy.index("unregisterCallbacks();"))
        self.assertLess(destroy.index("initialized = false;"), destroy.index("DockEngineHost.completedShutdown();"))
        self.assertIn("dockFinishRequested && isFinishing() && !isChangingConfigurations()", destroy)
        self.assertIn("&& !dockRecreating", destroy)
        self.assertEqual(self.adapted.count("dockFinishRequested = true;"), 1)
        graphics = self.adapted.split('command.equals("graphics_restart")', 1)[1].split('command.equals("ask_permission")', 1)[0]
        self.assertNotIn("completedShutdown", graphics)
        self.assertNotIn("dockFinishRequested = true", graphics)
        self.assertIn("dockRecreating = true;", graphics)

    def test_resume_does_not_replace_active_game_intent(self):
        resume = self.adapted.split("public void onNewIntent(Intent intent)", 1)[1]
        self.assertLess(resume.index('if (!intent.hasExtra("request_id")) return;'),
                        resume.index("setIntent(intent);"))

    def test_storage_picker_and_native_fallbacks_stay_private(self):
        folder = self.adapted.split('command.equals("browse_folder")', 1)[1].split('command.equals("share_text")', 1)[0]
        self.assertIn("NativeApp.RESULT_CANCELED", folder)
        self.assertNotIn("startActivity", folder)
        self.assertNotIn("ACTION_OPEN_DOCUMENT_TREE", folder)
        self.assertNotIn("FLAG_GRANT_WRITE_URI_PERMISSION", self.adapted)
        self.assertIn("externalFilesDir = extStorageDir;", self.adapted)
        self.assertIn('additionalStorageDirs = "";', self.adapted)
        self.assertIn("param[2].equals(DockEngineHost.memstickDirectory().getAbsolutePath())", self.adapted)

    def test_native_content_uri_boundary_rejects_writes_and_mutations(self):
        content = PATCH.patch_content_uri((self.upstream / "ContentUri.java").read_bytes())
        self.assertIn('if (!"r".equals(mode)) return -1;', content)
        self.assertEqual(content.count("if (DockEngineHost.readOnlyExternalStorage()) return STORAGE_ERROR_UNKNOWN;"), 6)
        proxy = PATCH.patch_document_proxy((self.upstream / "DocumentResultProxyActivity.java").read_bytes())
        self.assertNotIn("FLAG_GRANT_WRITE_URI_PERMISSION", proxy)

    def test_manifest_has_one_launcher_and_private_engine_in_same_apk(self):
        attr = "{http://schemas.android.com/apk/res/android}"
        app = ET.parse(PROJECT / "app/src/main/AndroidManifest.xml").getroot()
        engine = ET.parse(PROJECT / "ppsspp-engine/src/main/AndroidManifest.xml").getroot()
        self.assertIsNone(app.find("queries"))
        self.assertEqual(len(app.findall("application/activity/intent-filter/action")), 1)
        self.assertFalse(app.findall("application/provider"))
        activities = engine.findall("application/activity")
        self.assertEqual(len(activities), 3)
        for activity in activities:
            self.assertEqual(activity.get(attr + "exported"), "false")
            self.assertEqual(activity.get(attr + "process"), ":emulation")
            self.assertFalse(activity.findall("intent-filter"))


if __name__ == "__main__":
    unittest.main()
