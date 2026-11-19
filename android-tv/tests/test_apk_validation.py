"""Failure fixtures for artifact inspection; no Android SDK or device required."""
import importlib.util
import json
from pathlib import Path
import struct
import tempfile
import unittest
import zipfile

SPEC = importlib.util.spec_from_file_location("verify_apk", Path(__file__).resolve().parents[1] / "scripts/verify_apk.py")
APK = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(APK)


def elf(abi="arm64-v8a", alignment=16384):
    klass, machine = APK.ARCHITECTURES[abi]
    data = bytearray(256)
    data[:7] = b"\x7fELF" + bytes((klass, 1, 1))
    struct.pack_into("<HH", data, 16, 3, machine)
    if klass == 2:
        struct.pack_into("<Q", data, 32, 64)
        struct.pack_into("<HH", data, 54, 56, 1)
        struct.pack_into("<IIQQQQQQ", data, 64, 1, 5, 0, 0, 0, 256, 256, alignment)
    else:
        struct.pack_into("<I", data, 28, 52)
        struct.pack_into("<HH", data, 42, 32, 1)
        struct.pack_into("<IIIIIIII", data, 52, 1, 0, 0, 0, 256, 256, 5, alignment)
    return bytes(data)


MANIFEST = '''<manifest xmlns:android="http://schemas.android.com/apk/res/android" package="app.pspdock.tv">
<uses-feature android:name="android.hardware.touchscreen" android:required="false"/>
<application android:enableOnBackInvokedCallback="false">
<activity android:name="app.pspdock.tv.MainActivity" android:exported="true"><intent-filter>
<action android:name="android.intent.action.MAIN"/>
<category android:name="android.intent.category.LEANBACK_LAUNCHER"/>
</intent-filter></activity>
<activity android:name="org.ppsspp.ppsspp.PpssppActivity" android:exported="false" android:process=":emulation"/>
</application></manifest>'''


class ApkValidationTest(unittest.TestCase):
    def test_elf_architectures_and_page_sizes(self):
        self.assertEqual(APK.verify_elf(elf(), "arm64-v8a")["minimum_load_alignment"], 16384)
        self.assertEqual(APK.verify_elf(elf("armeabi-v7a", 4096), "armeabi-v7a")["elf_class"], 32)
        self.assertEqual(APK.verify_elf(elf("x86_64"), "x86_64")["machine"], 62)
        with self.assertRaises(ValueError):
            APK.verify_elf(elf(alignment=4096), "arm64-v8a")
        with self.assertRaises(ValueError):
            APK.verify_elf(elf("x86_64"), "arm64-v8a")

    def test_truncated_headers_and_invalid_load_segments_fail(self):
        with self.assertRaises(ValueError):
            APK.verify_elf(elf()[:80], "arm64-v8a")
        data = bytearray(elf())
        struct.pack_into("<Q", data, 64 + 40, 128)  # memsz < filesz
        with self.assertRaises(ValueError):
            APK.verify_elf(bytes(data), "arm64-v8a")

    def test_single_tv_app_and_private_engine_manifest(self):
        self.assertEqual(APK.verify_manifest(MANIFEST)["engine_process"], ":emulation")
        for old, new in [('android:exported="false"', 'android:exported="true"'),
                         ('android:process=":emulation"', 'android:process="another.app"'),
                         ('android:required="false"', 'android:required="true"'),
                         ('enableOnBackInvokedCallback="false"', 'enableOnBackInvokedCallback="true"')]:
            with self.subTest(new=new), self.assertRaises(ValueError):
                APK.verify_manifest(MANIFEST.replace(old, new))
        launcher = MANIFEST.split('<activity android:name="app.pspdock.tv.MainActivity"')[1].split('</activity>')[0]
        duplicate = '<activity android:name="extra.Launcher"' + launcher + '</activity>'
        with self.assertRaises(ValueError):
            APK.verify_manifest(MANIFEST.replace('</application>', duplicate + '</application>'))

    def test_every_packaged_library_is_checked(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "dock.apk"
            with zipfile.ZipFile(target, "w") as archive:
                archive.writestr("lib/arm64-v8a/libppsspp_jni.so", elf())
                archive.writestr("lib/arm64-v8a/libc++_shared.so", elf())
            self.assertEqual(len(APK.verify_native(target, {"arm64-v8a"})), 2)
            with zipfile.ZipFile(target, "a") as archive:
                archive.writestr("lib/arm64-v8a/libdependency.so", elf(alignment=4096))
            with self.assertRaises(ValueError):
                APK.verify_native(target, {"arm64-v8a"})

    def test_missing_engine_or_abi_is_not_a_valid_apk(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "dock.apk"
            with zipfile.ZipFile(target, "w") as archive:
                archive.writestr("lib/arm64-v8a/libc++_shared.so", elf())
            with self.assertRaises(ValueError):
                APK.verify_native(target, {"arm64-v8a"})
            with self.assertRaises(ValueError):
                APK.verify_native(target, {"arm64-v8a", "armeabi-v7a"})

    def test_native_optimization_uses_final_compiler_flag(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory)
            (target / "CMakeCache.txt").write_text("ANDROID_ABI:STRING=arm64-v8a\nCMAKE_BUILD_TYPE:STRING=RelWithDebInfo\n")
            commands = [{"file": "/source" + file, "arguments": ["clang++", "-O2", "-DNDEBUG"]}
                        for file in ("/UI/EmuScreen.cpp", "/Core/System.cpp")]
            (target / "compile_commands.json").write_text(json.dumps(commands))
            self.assertEqual(APK.verify_optimization(target, {"arm64-v8a"})[0]["configuration"], "RelWithDebInfo")
            for flags in (["-O2", "-DNDEBUG", "-O0"], ["-O2", "-DNDEBUG", "-Og"], ["-O2"]):
                with self.subTest(flags=flags):
                    commands[0]["arguments"] = ["clang++", *flags]
                    (target / "compile_commands.json").write_text(json.dumps(commands))
                    with self.assertRaisesRegex(ValueError, "Engine compilation is not optimized"):
                        APK.verify_optimization(target, {"arm64-v8a"})

    def test_cmake_commands_require_real_core_and_ui_for_both_abis(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory)
            abis = {"arm64-v8a", "armeabi-v7a"}
            for abi in abis:
                build = target / "Debug" / "cmake-hash" / abi
                build.mkdir(parents=True)
                (build / "CMakeCache.txt").write_text(
                    "ANDROID_ABI:STRING=" + abi + "\nCMAKE_BUILD_TYPE:STRING=RelWithDebInfo\n")
                # CMake's compile_commands.json uses a shell command string.
                commands = [{"directory": str(build), "file": "/source/" + source,
                             "command": "/ndk/bin/clang++ -O2 -g -DNDEBUG -fPIC -c /source/" + source}
                            for source in ("UI/EmuScreen.cpp", "Core/System.cpp")]
                (build / "compile_commands.json").write_text(json.dumps(commands))
            evidence = APK.verify_optimization(target, abis)
            self.assertEqual({item["abi"] for item in evidence}, abis)
            for item in evidence:
                self.assertEqual({s["source"] for s in item["sources"]}, {"UI/EmuScreen.cpp", "Core/System.cpp"})
            database = target / "Debug" / "cmake-hash" / "arm64-v8a" / "compile_commands.json"
            commands = json.loads(database.read_text())
            commands[1]["file"] = "/source/Core/PSP.cpp"
            database.write_text(json.dumps(commands))
            with self.assertRaisesRegex(ValueError, "arm64-v8a: Core/System.cpp"):
                APK.verify_optimization(target, abis)


if __name__ == "__main__":
    unittest.main()
