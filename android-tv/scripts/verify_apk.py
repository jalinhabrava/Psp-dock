#!/usr/bin/env python3
"""Verify the built Dock APK and write evidence; requires the official Android SDK.

Checks the merged manifest, actual native ELF files, ZIP alignment and (when
requested) optimized CMake commands. It does not install an APK or access USB.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import struct
import subprocess
import sys
import xml.etree.ElementTree as ET
import zipfile

ARCHITECTURES = {"arm64-v8a": (2, 183), "armeabi-v7a": (1, 40), "x86_64": (2, 62)}
ANDROID = "{http://schemas.android.com/apk/res/android}"
PACKAGE = "app.pspdock.tv"
ENGINE = "org.ppsspp.ppsspp.PpssppActivity"
# PPSSPP 1.20.4 builds its emulation lifecycle/run loop from Core/System.cpp.
# These paths are also checked against the pinned source tree and CMake list.
NATIVE_OPTIMIZATION_SOURCES = ("UI/EmuScreen.cpp", "Core/System.cpp")


def verify_elf(data: bytes, abi: str) -> dict:
    expected_class, expected_machine = ARCHITECTURES[abi]
    if len(data) < 52 or data[:4] != b"\x7fELF" or data[4] != expected_class or data[5:7] != b"\x01\x01":
        raise ValueError("Invalid little-endian ELF for " + abi)
    elf_type, machine = struct.unpack_from("<HH", data, 16)
    if elf_type != 3 or machine != expected_machine:
        raise ValueError("Native library has the wrong ELF type or machine for " + abi)
    if expected_class == 2:
        if len(data) < 64:
            raise ValueError("Truncated ELF64 header")
        phoff = struct.unpack_from("<Q", data, 32)[0]
        phsize, count = struct.unpack_from("<HH", data, 54)
        layout = struct.Struct("<IIQQQQQQ")
    else:
        phoff = struct.unpack_from("<I", data, 28)[0]
        phsize, count = struct.unpack_from("<HH", data, 42)
        layout = struct.Struct("<IIIIIIII")
    if count == 0 or phsize < layout.size or phoff + count * phsize > len(data):
        raise ValueError("Missing or truncated ELF program headers")
    alignments = []
    for index in range(count):
        header = layout.unpack_from(data, phoff + index * phsize)
        if header[0] != 1:  # PT_LOAD
            continue
        if expected_class == 2:
            _, _, offset, vaddr, _, filesz, memsz, alignment = header
        else:
            _, offset, vaddr, _, filesz, memsz, _, alignment = header
        minimum = 16384 if expected_class == 2 else 4096
        if alignment < minimum or alignment & (alignment - 1):
            raise ValueError("ELF load alignment is below " + str(minimum) + " for " + abi)
        if offset % alignment != vaddr % alignment or filesz > memsz or offset + filesz > len(data):
            raise ValueError("Invalid ELF load segment")
        alignments.append(alignment)
    if not alignments:
        raise ValueError("ELF has no loadable segments")
    return {"bytes": len(data), "sha256": hashlib.sha256(data).hexdigest(),
            "elf_class": expected_class * 32, "machine": machine,
            "minimum_load_alignment": min(alignments)}


def verify_manifest(xml: str) -> dict:
    root = ET.fromstring(xml)
    if root.get("package") != PACKAGE:
        raise ValueError("APK is not the Dock application")
    app = root.find("application")
    if app is None:
        raise ValueError("Merged manifest is missing application")
    activities = app.findall("activity")
    launchers = []
    for activity in activities + app.findall("activity-alias"):
        for intent in activity.findall("intent-filter"):
            actions = {a.get(ANDROID + "name") for a in intent.findall("action")}
            categories = {c.get(ANDROID + "name") for c in intent.findall("category")}
            if "android.intent.action.MAIN" in actions and categories.intersection({
                    "android.intent.category.LAUNCHER", "android.intent.category.LEANBACK_LAUNCHER"}):
                launchers.append((activity, categories))
    if len(launchers) != 1 or "android.intent.category.LEANBACK_LAUNCHER" not in launchers[0][1]:
        raise ValueError("APK must have exactly one TV launcher")
    launcher = launchers[0][0]
    if launcher.get(ANDROID + "exported") != "true":
        raise ValueError("TV launcher is not exported")
    engine = next((a for a in activities if a.get(ANDROID + "name") == ENGINE), None)
    if engine is None or engine.get(ANDROID + "exported") != "false" or engine.get(ANDROID + "process") != ":emulation":
        raise ValueError("Embedded PPSSPP must be a non-exported private-process Activity")
    if engine.findall("intent-filter") or engine.get(ANDROID + "taskAffinity") not in (None, PACKAGE):
        raise ValueError("Embedded engine must remain within the Dock task")
    features = {e.get(ANDROID + "name"): e.get(ANDROID + "required", "true") for e in root.findall("uses-feature")}
    if features.get("android.hardware.touchscreen") != "false":
        raise ValueError("Dock must not require a touchscreen")
    if app.get(ANDROID + "enableOnBackInvokedCallback") != "false":
        raise ValueError("Pinned PPSSPP legacy Back handling requires the reviewed API36 opt-out")
    for item in activities:
        if item.get(ANDROID + "name", "").startswith("org.ppsspp.") and item.get(ANDROID + "exported") != "false":
            raise ValueError("An engine Activity is exposed outside the Dock")
    return {"package": PACKAGE, "version_name": root.get(ANDROID + "versionName"),
            "version_code": root.get(ANDROID + "versionCode"),
            "tv_launcher": launcher.get(ANDROID + "name"), "engine": ENGINE,
            "engine_process": ":emulation"}


def verify_native(apk: Path, abis: set[str]) -> list[dict]:
    libraries = []
    with zipfile.ZipFile(apk) as archive:
        names = archive.namelist()
        if len(names) != len(set(names)):
            raise ValueError("APK contains duplicate ZIP entries")
        observed = set()
        for name in names:
            if not (name.startswith("lib/") and name.endswith(".so")):
                continue
            parts = name.split("/")
            if len(parts) != 3 or parts[1] not in abis:
                raise ValueError("Unexpected native library architecture: " + name)
            observed.add(parts[1])
            libraries.append({"path": name, "abi": parts[1], **verify_elf(archive.read(name), parts[1])})
        if observed != abis:
            raise ValueError("Missing requested native ABIs: " + str(abis - observed))
        for abi in abis:
            for library in ("libppsspp_jni.so", "libc++_shared.so"):
                if "lib/" + abi + "/" + library not in names:
                    raise ValueError("Missing embedded engine/runtime: " + abi + "/" + library)
    return libraries


def verify_optimization(directory: Path, abis: set[str]) -> list[dict]:
    evidence = {}
    required_sources = tuple("/" + source for source in NATIVE_OPTIMIZATION_SOURCES)
    for cache in sorted(directory.rglob("CMakeCache.txt")):
        values = {}
        for line in cache.read_text().splitlines():
            match = re.match(r"([^:#][^:]*):[^=]+=(.*)$", line)
            if match:
                values[match[1]] = match[2]
        abi = values.get("ANDROID_ABI") or values.get("CMAKE_ANDROID_ARCH_ABI")
        if abi not in abis or values.get("CMAKE_BUILD_TYPE") != "RelWithDebInfo":
            continue
        commands = json.loads(cache.with_name("compile_commands.json").read_text())
        checked = []
        for entry in commands:
            source = entry["file"].replace("\\", "/")
            if not source.endswith(required_sources):
                continue
            arguments = entry.get("arguments") or shlex.split(entry["command"])
            optimizations = [arg for arg in arguments if re.fullmatch(r"-O(?:[0-3sgz]|fast)", arg)]
            if not optimizations or optimizations[-1] in ("-O0", "-Og") or "-DNDEBUG" not in arguments:
                raise ValueError("Engine compilation is not optimized: " + source)
            checked.append({"source": next(s.lstrip("/") for s in required_sources if source.endswith(s)),
                            "optimization": optimizations[-1], "ndebug": True})
        missing = set(NATIVE_OPTIMIZATION_SOURCES) - {item["source"] for item in checked}
        if missing:
            raise ValueError("Missing core/UI native compile commands for " + abi + ": " + ", ".join(sorted(missing)))
        evidence[abi] = {"abi": abi, "configuration": "RelWithDebInfo", "sources": checked}
    if set(evidence) != abis:
        raise ValueError("Missing optimized native build evidence: " + str(abis - set(evidence)))
    return [evidence[abi] for abi in sorted(evidence)]


def sdk_tool(sdk: Path, name: str) -> Path:
    if name == "apkanalyzer":
        candidates = [sdk / "cmdline-tools/latest/bin/apkanalyzer"]
        candidates += sorted(sdk.glob("cmdline-tools/*/bin/apkanalyzer"), reverse=True)
    else:
        candidates = sorted(sdk.glob("build-tools/*/zipalign"), reverse=True)
    for candidate in candidates:
        if candidate.is_file():
            return candidate
        if candidate.with_suffix(".bat" if name == "apkanalyzer" else ".exe").is_file():
            return candidate.with_suffix(".bat" if name == "apkanalyzer" else ".exe")
    raise ValueError("Official Android SDK tool is missing: " + name)


def run(*args: str | Path) -> str:
    result = subprocess.run([str(arg) for arg in args], capture_output=True, text=True, timeout=60)
    if result.returncode:
        raise ValueError("Android SDK check failed: " + str(args[0]) + "\n" + (result.stdout + result.stderr)[-8000:])
    return result.stdout


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apk", type=Path, required=True)
    parser.add_argument("--sdk", type=Path, default=os.environ.get("ANDROID_HOME") or os.environ.get("ANDROID_SDK_ROOT"))
    parser.add_argument("--abis", default="arm64-v8a,armeabi-v7a")
    parser.add_argument("--native-build-dir", type=Path)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    try:
        abis = set(args.abis.split(","))
        if not abis or not abis <= ARCHITECTURES.keys() or args.sdk is None:
            raise ValueError("Select supported --abis and provide --sdk or ANDROID_HOME")
        apk = args.apk.resolve(strict=True)
        manifest = verify_manifest(run(sdk_tool(args.sdk, "apkanalyzer"), "manifest", "print", apk))
        libraries = verify_native(apk, abis)
        run(sdk_tool(args.sdk, "zipalign"), "-c", "-P", "16", "-v", "4", apk)
        report = {"apk": apk.name, "bytes": apk.stat().st_size,
                  "sha256": hashlib.sha256(apk.read_bytes()).hexdigest(),
                  "manifest": manifest, "abis": sorted(abis), "libraries": libraries,
                  "zip_alignment": "zipalign -c -P 16 -v 4 passed",
                  "native_build": verify_optimization(args.native_build_dir, abis) if args.native_build_dir else None}
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, indent=2) + "\n")
        print("Verified single-app Dock APK for " + ", ".join(sorted(abis)) + "; report: " + str(args.report))
    except (OSError, ValueError, ET.ParseError, zipfile.BadZipFile, subprocess.TimeoutExpired) as failure:
        print("APK validation failed: " + str(failure), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
