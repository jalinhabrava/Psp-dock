# Embedded PPSSPP engine

PSP Dock TV installs one APK (`app.pspdock.tv`). Its private
`org.ppsspp.ppsspp.PpssppActivity` runs in the same application and task, in the
`:emulation` process. PPSSPP is compiled into this APK; an external PPSSPP install
is neither queried nor launched. The upstream Java package is retained because
its native JNI entry points depend on those names.

## Reproducible source preparation

`engine.lock.json` fixes PPSSPP 1.20.4 to commit
`fa50bb1976065c4f8b1b47af227d367fe9771555`. Every recursive dependency is fixed by
the corresponding upstream Git gitlink. The preparation script refuses a
different commit or edited tracked source. It never resets an existing checkout.

```sh
python3 scripts/prepare_engine.py
./gradlew --no-daemon :session-contract:test :app:testDebugUnitTest :app:assembleDebug
```

Use JDK 17, Android SDK platform 36, NDK `29.0.14206865`, and CMake `3.22.1`.
The wrapper fixes Gradle 8.13 with its distribution SHA-256; AGP is 8.11.1 and
Kotlin is 2.2.21. Git and Python 3.10 or newer are required. On systems where
Python is named `python`, add `-PpythonCommand=python`.

An existing verified source checkout can be selected with
`-PppssppSourceDir=/absolute/path/to/ppsspp`. This property changes only source
location; the exact commit, tracked source, dependency revisions, and patched
Java source hashes are still checked. Gradle verification never fetches code.

The normal APK contains `arm64-v8a` and `armeabi-v7a`, including Android TVs with
a 32-bit userspace. `-PdockAbis=x86_64` selects an emulator build. A faster single
ARM build can use `-PdockAbis=arm64-v8a`. CMake compiles `ppsspp_jni` and its source
dependencies; FFmpeg uses the platform-specific static archives supplied in the
fixed official PPSSPP FFmpeg submodule, as the upstream Android build does.
The debug APK retains native symbols but compiles the engine as
`RelWithDebInfo`; the release engine uses `Release` optimization.

## Lifecycle contract

The Dock prepares a token in `files/emulation`, then launches the explicit
internal Activity with `com.pspdock.SESSION_TOKEN`. Game launch arguments use
`org.ppsspp.ppsspp.Args` and `--pause-menu-exit` plus the private imported game
path. Intent data is omitted: in this PPSSPP version a data URI takes precedence
over the Args extra.

Before native initialization the engine acquires the `:session-contract` lease.
The lease remains held across Activity and graphics recreation. Returning to the
Dock, pressing Home, receiving an Activity result, or merely destroying an
Activity does not acknowledge a completed game session. Only the explicit native
`finish` command enables a clean-close marker, and that marker is written after
`NativeApp.shutdown()`, callback removal, and clearing native initialization.
The engine then exits its private process while retaining the lock through exit.
The Dock process survives, and the next game gets a new native runtime. A crash,
write failure, or forced process death leaves an interrupted/unknown session.

`graphics_restart` also calls native shutdown upstream, but is expressly excluded
from clean-close acknowledgment; it recreates the Activity under the same token.
Resuming a live session does not replace its launch intent or send a new game to
the existing runtime. A second simultaneous Activity is rejected.

## Private storage boundary

Before initialization, an atomic `files/ppsspp/memstick_dir.txt` points at
`files/ppsspp/memstick`. Both of the native Android storage-directory fallbacks
are also bound to this private directory. PPSSPP creates its ordinary `PSP`
subdirectories there, including local saved games and configuration.

Upstream settings cannot relocate the Memory Stick: folder browsing is canceled,
and the legacy Android 8/9 manual path dialog is rejected before it returns a
path. File selectors retain read permission only. The Java JNI `ContentUri`
boundary rejects write modes and all document creation, deletion, copying,
moving, and renaming, even if a document provider unexpectedly grants write
access. No broad Android storage permissions are declared by the embedded
module. PSP synchronization remains disabled at the Dock session layer.

## Adapter and validation

`scripts/patch_engine_java.py` copies the original Java sources into
`ppsspp-engine/build/generated/ppsspp-java`. It excludes the additional upstream
shortcut launcher and changes three hash-checked classes: lifecycle/private
paths in `PpssppActivity`, read-only persistence in `DocumentResultProxyActivity`,
and the read-only `ContentUri` boundary. The original source checkout stays
unchanged. `DockEngineHost.java` contains the small app-owned integration layer.

```sh
python3 -m unittest discover -s tests -p 'test_engine_embedding.py' -v
```

For an existing checkout set `PSP_DOCK_PPSSPP_SOURCE` to its absolute path when
running these adapter checks. JVM session tests and Android build/lint are
separate checks. Actual game launch, rendering, audio, controller mapping,
graphics recreation, Home/resume, normal exit, crash recovery, and opening a
second game still require a TV/emulator device run; source checks do not prove
those runtime behaviors.

## License and source availability

PPSSPP is distributed under GNU GPL version 2 or later, with additional notices
for bundled components in its `LICENSE.TXT` and dependency trees. The upstream
license is included at `licenses/PPSSPP-LICENSE.txt` and in the APK assets. The
Dock embedding source and adapter changes are provided in this repository;
redistributions of the combined APK must include the corresponding source and
build instructions for the exact revision, including the pinned dependencies
and these modifications. Preserve all dependency copyright/license notices.
The Android integration does not change the licenses of separate Windows/Linux
components in the surrounding project.
