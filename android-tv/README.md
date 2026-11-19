# PSP Dock for Android TV

Android TV development version **0.1.3-tv-preview**, updated to Windows `main`
`ff3033351bd9901041eb6b42a639d1810e85bb81`: the Windows v0.1.3 release line,
current package layout and native PSP **0308 / PDK2 v3** contract.

## One application from library to game

The Dock and PPSSPP are packaged in **one APK**, with one TV launcher entry.
The internal, non-exported PPSSPP Activity runs in the private `:emulation`
process of the same application. No separate PPSSPP installation or external
application launch is needed. The official `--pause-menu-exit` option makes
Exit in the game's pause menu close the embedded engine and return to the Dock.

The TV interface supports remote/D-pad focus, Android gamepads and English or
Spanish selected from the system language. Games are imported through the
Android document picker into private storage. ISO, CSO, CHD and PBP containers
are checked for signature, size and available space; the copy is hashed while
reading and then completely reread before publishing it to the library.
Imports can be cancelled, and an existing corrupt destination is not silently
replaced. Source documents are opened for reading only.

## Optional PSP controller preview

The visible **Use PSP as controller · preview** action requests an explicit
confirmation and Android USB permission. The Dock then owns only the PSP's HID
interface, reads the controller itself and forwards input to both the Dock and
the embedded engine. The Sony mass-storage interface remains under Android's
control. This application does not request root or broad filesystem access.

This is a complete software path for the current firmware, pending physical
Android TV acceptance:

- Checks the USB functions, input endpoint, firmware/build/capability identity
  and PDK2 session before accepting input.
- Uses the current Output-report fragmentation and CRC16 protocol, with a
  confirmed host handshake and controller phase. An uncertain command is never
  automatically replayed.
- Configures and verifies a 32 ms HID idle interval for actual input freshness;
  restores the previous interval on cleanup when the device is still reachable.
- Delivers versioned, sequenced and timestamped state through private Messenger
  IPC. A new consumer waits for neutral input; stale or invalid state releases
  all buttons and axes.
- Runs a visible, bounded `connectedDevice` foreground service while connected.
  Disconnect, errors, task removal and the notification's Stop action terminate
  capture. It does not restart itself or start at boot.
- Treats a fresh firmware exit request, including PSP Start+Select, as a request
  to close the engine through its normal shutdown path. An unplug or timeout
  neutralizes controls without pretending that a game exited cleanly.

A TV remote or another Android gamepad can also be used. Use one game controller
at a time: disable the PSP preview before using another Android gamepad. The
pinned engine shares its default Android gamepad mapping between those sources. The connection panel
retains a separate read-only diagnostic path. Firmware 0303–0308 status is
understood using the same identity matrix as Windows; direct controller
commands require the fragmented transport introduced in 0306. The acceptance
target for this version is the current 0308 firmware.

See [the protocol contract and Windows parity vectors](protocol/README.md) for
the precise wire format, ownership rules and failure handling.

## Local saves and session recovery

Games and saves remain in application-private Android storage. **This version
does not synchronize saves back to the PSP or certify safe USB removal.** A
readable SAF folder does not prove that it belongs to the USB device whose HID
interface was selected. Use Android's storage/eject interface when appropriate.
The controller client cannot send SAFE, HOST_SHUTDOWN or filesystem-copy commands.

The shared session module writes a random launch token before starting the
engine and holds an OS file lock in the emulator process. A matching closed
record is written only after native shutdown and callback cleanup. Home/resume,
graphics recreation, an Activity result or process death cannot certify a clean
exit. A second game is blocked while a session is unresolved. Explicit recovery
keeps local files and never authorizes a PSP write.

The engine's memory stick stays under private `files/ppsspp/memstick`. Its folder
picker and document-write paths are constrained so a PPSSPP settings screen
cannot bypass the Dock's storage boundary. Details are in the
[embedded-engine documentation](ppsspp-engine/README.md).

## Build and validation

Pinned tools: JDK 17, Gradle 8.13, AGP 8.11.1, Kotlin 2.2.21, Android platform 36,
NDK 29.0.14206865 and CMake 3.22.1. Minimum Android is API 26. The default APK
contains `arm64-v8a` and `armeabi-v7a`; `-PdockAbis=x86_64` selects an emulator
build. The debug native engine is optimized as RelWithDebInfo.

Use an Android SDK installed with the operator's accepted license terms and set
`ANDROID_HOME` or an untracked `local.properties`. From this directory:

```sh
python3 protocol/windows_vectors.py --check
python3 scripts/prepare_engine.py
python3 -m unittest discover -s tests -p 'test_*.py' -v
./gradlew --no-daemon --max-workers=2 :session-contract:test :app:testDebugUnitTest :app:lintDebug
./gradlew --no-daemon --max-workers=2 :app:assembleDebug
python3 scripts/verify_apk.py --apk app/build/outputs/apk/debug/app-debug.apk --sdk "$ANDROID_HOME" --native-build-dir ppsspp-engine/.cxx --report app/build/reports/dock-apk.json
```

The validation script inspects the actual merged manifest, both native ABIs,
ELF load alignment, APK ZIP alignment and native optimization flags. Its JSON
report includes the APK hash. CI runs the same gates and retains test/lint and
APK validation reports. Public binary upload is currently gated by the
[Android-specific licensing decision](LICENSING.md); a passing build is not a
public release or physical-device acceptance.

PPSSPP 1.20.4 and every recursive dependency are pinned and verified. The source
adapter changes generated copies, preserving the upstream checkout. No game,
Sony firmware dump or commercial game asset is included.

## Physical acceptance still required

Record TV model, Android version, ABI, USB topology and PSP firmware before
checking the complete cycle:

1. Remote navigation and visible focus in both languages; import, cancellation,
   low space and provider disconnection; original files remain intact.
2. Launch a legally owned game in the same application, use a TV/gamepad, save,
   exit through the pause menu and launch another game. Separately exercise
   Home/resume, graphics recreation and process death.
3. Explicitly enable the PSP controller preview. Confirm the Sony storage
   mount remains intact, input mapping and latency are correct, held buttons do
   not leak across screens, and Start+Select returns through engine shutdown.
4. Unplug, deny permission, connect multiple candidates, interrupt an ACK, stop
   from the UI and notification, and remove the task. Verify neutral input,
   released HID ownership and no unattended service restart.
5. Confirm that stopping controller capture never announces safe storage removal
   and that no game or savedata file has been written to the PSP.

The software checks do not replace those device tests. This branch does not
install or update PSP firmware.
