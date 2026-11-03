#!/usr/bin/env python3
"""Copy pinned PPSSPP Java sources and apply the reviewed Dock lifecycle adapter.

Only generated build output is changed. Every replacement requires an exact,
unique upstream anchor, and the whole original Activity is SHA-256 verified.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import shutil

PROJECT = Path(__file__).resolve().parents[1]
LOCK = json.loads((PROJECT / "engine.lock.json").read_text(encoding="utf-8"))


def replace_once(source: str, old: str, new: str) -> str:
    if source.count(old) != 1:
        raise ValueError("Upstream embedding anchor changed: " + old[:100])
    return source.replace(old, new, 1)


def patch_activity(original: bytes) -> str:
    if hashlib.sha256(original).hexdigest() != LOCK["activity_sha256"]:
        raise ValueError("PpssppActivity.java is not the reviewed, pinned upstream source")
    source = original.decode("utf-8")
    source = replace_once(source, "\tprivate boolean shuttingDown;",
        "\tprivate boolean shuttingDown;\n"
        "\tprivate boolean dockAccepted;\n"
        "\tprivate boolean dockFinishRequested;\n"
        "\tprivate boolean dockRecreating;")
    source = replace_once(source, "\t\tsuper.onCreate(savedInstanceState);",
        "\t\tsuper.onCreate(savedInstanceState);\n"
        "\t\tdockAccepted = DockEngineHost.begin(this);\n"
        "\t\tif (!dockAccepted) {\n"
        "\t\t\tfinish();\n"
        "\t\t\treturn;\n"
        "\t\t}")
    source = replace_once(source, "\t\tFile filesDir = this.getFilesDir();",
        "\t\tFile filesDir = DockEngineHost.dataDirectory();")
    source = replace_once(source, "\t\tNativeApp.init(model, deviceType, languageRegion, apkFilePath, dataDir, extStorageDir, externalFilesDir, nativeLibDir, additionalStorageDirs, cacheDir, shortcut, installerName, Build.VERSION.SDK_INT, Build.BOARD);",
        "\t\t// Native storage-root/private-storage choices both resolve inside Dock.\n"
        "\t\textStorageDir = DockEngineHost.memstickDirectory().getAbsolutePath();\n"
        "\t\texternalFilesDir = extStorageDir;\n"
        "\t\tadditionalStorageDirs = \"\";\n"
        "\t\tNativeApp.init(model, deviceType, languageRegion, apkFilePath, dataDir, extStorageDir, externalFilesDir, nativeLibDir, additionalStorageDirs, cacheDir, shortcut, installerName, Build.VERSION.SDK_INT, Build.BOARD);")
    folder_start = '\t\t} else if (command.equals("browse_folder")) {'
    folder_end = '\t\t} else if (command.equals("share_text")) {'
    if source.count(folder_start) != 1 or source.count(folder_end) != 1:
        raise ValueError("Upstream folder picker boundary changed")
    begin, end = source.index(folder_start), source.index(folder_end)
    source = source[:begin] + folder_start + "\n" + (
        "\t\t\t// PSP Dock owns the Memory Stick; no external folder/write grant.\n"
        "\t\t\tint requestId = Integer.parseInt(params);\n"
        "\t\t\tNativeApp.sendRequestResult(requestId, false, \"\", NativeApp.RESULT_CANCELED);\n"
        "\t\t\tToast.makeText(this, \"La Memory Stick usa el almacenamiento privado de PSP Dock.\", Toast.LENGTH_LONG).show();\n"
        "\t\t\treturn true;\n"
    ) + source[end:]
    source = replace_once(source,
        "\t\t\t\tintent.addFlags(Intent.FLAG_GRANT_PERSISTABLE_URI_PERMISSION);",
        "\t\t\t\tintent.addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION | Intent.FLAG_GRANT_PERSISTABLE_URI_PERMISSION);")
    # Android 8/9 expose a manual Memory Stick path instead of the folder picker.
    # Its initial value is the current (fixed private) Memory Stick. Refuse this
    # specific dialog before any string can become a native writable path.
    source = replace_once(source, "\t\t\tint requestID = Integer.parseInt(param[0]);",
        "\t\t\tint requestID = Integer.parseInt(param[0]);\n"
        "\t\t\tif (param.length > 2 && param[2].equals(DockEngineHost.memstickDirectory().getAbsolutePath())) {\n"
        "\t\t\t\tNativeApp.sendRequestResult(requestID, false, \"\", NativeApp.RESULT_CANCELED);\n"
        "\t\t\t\tToast.makeText(this, \"La Memory Stick usa el almacenamiento privado de PSP Dock.\", Toast.LENGTH_LONG).show();\n"
        "\t\t\t\treturn true;\n"
        "\t\t\t}")
    # A rejected overlapping Activity must never touch another Activity's native VM.
    for call in (
        "onStart()", "onStop()", "onPause()", "onResume()", "onDestroy()",
        "onWindowFocusChanged(hasFocus)", "onAttachedToWindow()",
        "onConfigurationChanged(newConfig)",
        "onMultiWindowModeChanged(isInMultiWindowMode, newConfig)",
        "onActivityResult(packedRequest, resultCode, data)",
    ):
        anchor = "\t\tsuper." + call + ";"
        source = replace_once(source, anchor, anchor + "\n\t\tif (!dockAccepted) return;")
    source = replace_once(source,
        '\t\t\tLog.i(TAG, "Setting shuttingDown = true and calling Finish");\n'
        "\t\t\tshuttingDown = true;",
        '\t\t\tLog.i(TAG, "Setting shuttingDown = true and calling Finish");\n'
        "\t\t\tdockFinishRequested = true;\n"
        "\t\t\tshuttingDown = true;")
    source = replace_once(source,
        '\t\t} else if (command.equals("graphics_restart")) {',
        '\t\t} else if (command.equals("graphics_restart")) {\n'
        "\t\t\tdockRecreating = true;")
    source = replace_once(source,
        '\t\t} else if (command.equals("recreate")) {',
        '\t\t} else if (command.equals("recreate")) {\n'
        "\t\t\tdockRecreating = true;")
    source = replace_once(source,
        "\t\t\tNativeApp.shutdown();\n\t\t\tunregisterCallbacks();\n\t\t\tinitialized = false;",
        "\t\t\tNativeApp.shutdown();\n\t\t\tunregisterCallbacks();\n\t\t\tinitialized = false;\n"
        "\t\t\t// graphics_restart also shuts down native code, but retains the session.\n"
        "\t\t\tif (dockFinishRequested && isFinishing() && !isChangingConfigurations() && !dockRecreating) {\n"
        "\t\t\t\tDockEngineHost.completedShutdown();\n"
        "\t\t\t}")
    source = replace_once(source, "\t\tlifeCycle.onDestroy();",
        "\t\tlifeCycle.onDestroy();\n\t\tDockEngineHost.activityDestroyed(this);")
    source = replace_once(source, "\t\tsuper.onNewIntent(intent);",
        "\t\tsuper.onNewIntent(intent);\n"
        "\t\tif (!dockAccepted) return;\n"
        "\t\t// Resume the existing game without replacing its original launch intent.\n"
        "\t\t// Internal result proxies are the only runtime intents passed upstream.\n"
        "\t\tif (!intent.hasExtra(\"request_id\")) return;")
    return source


def patch_document_proxy(original: bytes) -> str:
    if hashlib.sha256(original).hexdigest() != LOCK["document_proxy_sha256"]:
        raise ValueError("DocumentResultProxyActivity.java differs from the pinned version")
    return replace_once(original.decode("utf-8"),
        "Intent.FLAG_GRANT_READ_URI_PERMISSION | Intent.FLAG_GRANT_WRITE_URI_PERMISSION",
        "Intent.FLAG_GRANT_READ_URI_PERMISSION")


def patch_content_uri(original: bytes) -> str:
    if hashlib.sha256(original).hexdigest() != LOCK["content_uri_sha256"]:
        raise ValueError("ContentUri.java differs from the pinned version")
    source = original.decode("utf-8")
    source = replace_once(source,
        "\tpublic static int openContentUri(Activity activity, String uriString, String mode) {",
        "\tpublic static int openContentUri(Activity activity, String uriString, String mode) {\n"
        "\t\tif (!\"r\".equals(mode)) return -1;  // External documents are read-only in Dock.")
    # Defense in depth: do not trust providers to honor requested read-only grants.
    # All engine JNI mutations of content:// documents fail before touching a provider.
    for name in ("contentUriCreateDirectory", "contentUriCreateFile", "contentUriRemoveFile",
                 "contentUriCopyFile", "contentUriMoveFile", "contentUriRenameFileTo"):
        matches = re.findall(r"\tpublic static int " + name + r"\([^\n]+\) \{", source)
        if len(matches) != 1:
            raise ValueError("Upstream ContentUri mutation signature changed: " + name)
        anchor = matches[0]
        source = replace_once(source, anchor,
            anchor + "\n\t\tif (DockEngineHost.readOnlyExternalStorage()) return STORAGE_ERROR_UNKNOWN;")
    return source


def generate(source: Path, output: Path) -> None:
    upstream = source.resolve() / "android/src/org/ppsspp/ppsspp"
    output = output.resolve()
    if output == source.resolve() or source.resolve() in output.parents:
        raise ValueError("Generated sources must be outside the immutable upstream checkout")
    patched = patch_activity((upstream / "PpssppActivity.java").read_bytes())
    destination = output / "org/ppsspp/ppsspp"
    destination.mkdir(parents=True, exist_ok=True)
    # ShortcutActivity provides an extra launcher/exported flow and is not part of Dock.
    wanted = {path.name for path in upstream.glob("*.java") if path.name != "ShortcutActivity.java"}
    for stale in destination.glob("*.java"):
        if stale.name not in wanted:
            stale.unlink()
    for path in upstream.glob("*.java"):
        if path.name not in wanted:
            continue
        target = destination / path.name
        if path.name == "PpssppActivity.java":
            target.write_text(patched, encoding="utf-8", newline="\n")
        elif path.name == "DocumentResultProxyActivity.java":
            target.write_text(patch_document_proxy(path.read_bytes()), encoding="utf-8", newline="\n")
        elif path.name == "ContentUri.java":
            target.write_text(patch_content_uri(path.read_bytes()), encoding="utf-8", newline="\n")
        else:
            shutil.copyfile(path, target)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    generate(args.source, args.output)
    print("Prepared pinned PPSSPP Java sources with the Dock session hooks")


if __name__ == "__main__":
    main()
