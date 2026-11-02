# PSP Dock for Android TV

First Android TV milestone: a TV-native Dock, a verified local game library, an embedded PPSSPP engine in the **same APK**, and read-only PSP USB diagnostics. This is a separate platform implementation; it does not change the Windows host or the PSP firmware.

## What this milestone does

- Launches from the Android TV launcher and uses focusable controls for the TV remote, a D-pad, and Android gamepads. No touchscreen is required.
- Imports one ISO, CSO, CHD, or PBP through Android's Storage Access Framework. The document may be on a mounted USB volume or another readable document provider.
- Copies into application-private storage, checks the expected length when supplied, validates the format signature, computes SHA-256, then rereads the complete copy to verify the hash. Only a verified file is published to the library.
- Starts PPSSPP inside this APK with a private local file path. A separate PPSSPP installation, package lookup, external launcher, and URI handoff are not part of this product.
- Shows the connected USB functions and Android input devices. With USB permission, a single matching device can receive one read-only PDK2/v3 status request; the returned NAT3 identity must identify firmware 0302 and build 03020001.
- Checks whether a selected document tree can be read. The UI explicitly distinguishes that grant from proof that the tree belongs to the connected PSP.

Game saves remain on the Android device in this milestone. **It does not yet synchronize saves back to the PSP, claim the PSP session, send PDK2 phase changes, or certify safe USB removal.** Firmware 0302 requires a successful host handshake before it enables PSP controller input; this read-only diagnostic does not send that handshake. Use the TV remote or a separate Android gamepad for this milestone. Closing a game and leaving the Dock are separate actions. Returning to the Dock Activity alone is never accepted as evidence of emulator shutdown or permission to write to the PSP.

## Build

The project uses JDK 17, Gradle 8.13, Android Gradle Plugin 8.11.1, Kotlin 2.2.21, and Android SDK Platform 36. Minimum Android API level is 26. The engine pins NDK 29.0.14206865 and CMake 3.22.1; the pinned Android plugin selects its compatible default Build Tools version. The default native ABIs are arm64-v8a and armeabi-v7a.

Use an Android SDK installed through the official Android tools, with its license terms reviewed and accepted by the build operator. Point `ANDROID_HOME` at that SDK or create an untracked `local.properties` with `sdk.dir`.

From this directory:

```sh
python3 scripts/prepare_engine.py
./gradlew --no-daemon :session-contract:test testDebugUnitTest assembleDebug lintDebug
```

The debug APK is generated under `app/build/outputs/apk/debug/`. The repository's Android TV workflow runs the same checks and retains the APK as a CI artifact. The wrapper distribution is pinned by SHA-256. A successful build verifies software integration; it does not establish compatibility with a particular television's USB host, storage, or input drivers.

PPSSPP is an integrated third-party engine. Its source pin, integration changes, and license notices must accompany redistribution of the app. No game, firmware dump, or copyrighted game asset is bundled.

## Embedded engine and session evidence

The Dock and PPSSPP are Activities in the same application and APK. The non-exported engine Activity runs in the private `:emulation` process. It can read the Dock's verified library directly because both processes share the app's UID. The engine's memory stick lives in private storage under `files/ppsspp/memstick`; no separate app or inter-app document grant is involved.

The shared `:session-contract` module records a random token for each launch. The Dock commits that token before opening the engine. The engine must acquire an exclusive OS file lock and match the pending token before initializing native code. An existing launch cannot be overwritten by a second game.

The patched native `finish` path writes a matching closed record only after `NativeApp.shutdown()` and callback cleanup have completed, then exits the private emulator process. The lock remains held until that process exits. Returning to the Dock, `onResume`, a graphics recreation, an Activity result, or an arbitrary `onDestroy` does not produce a closed record.

| Observed evidence | Dock behavior |
| --- | --- |
| Matching active token and held lock | Offer return to the running engine; block another game |
| Closed marker but lock still held | Wait for the emulator process to finish |
| Matching closed marker and free lock | Allow a new game; retain saves locally |
| Active marker but free lock | Report interruption; require explicit recovery |
| Pending start without completion | Block a replacement until the start resolves or is explicitly recovered |
| Corrupt or mismatched record | Treat the session as unknown; never infer clean shutdown |

Recovery only resets session bookkeeping after proving that no engine owns the lock. It keeps local games and saves, is labeled as recovery from an interruption, and never claims that the old game closed cleanly. The UI polls only while the Dock is visible and a session is unresolved; there is no permanent background daemon. All session states keep PSP synchronization disabled in this milestone.

## Local library and import failures

Games use content-addressed filenames under the app's private `files/library` directory. Metadata is published through `AtomicFile` under `files/manifests`. A game cannot be launched by supplying an arbitrary filesystem path to the Dock UI. The file ID must be a SHA-256 digest and an allowed extension, and its metadata and size must match.

Imports have an 8 GiB size ceiling and retain a 64 MiB free-space reserve. A provider may omit the size; the byte limit, free-space checks, signature check, and complete reread still apply. Imports run off the UI thread, show copy/verification progress, and can be cancelled. Temporary files are removed after failure or when the next Dock session discovers an interrupted import. A corrupt existing destination is reported instead of silently replaced. The source document is opened only for reading, and the source game is never deleted.

The signature check identifies the selected container format; it does not promise that every game image or standalone PBP is complete or supported by the emulator. Homebrew requiring companion files is outside the single-file importer in this milestone.

If the system has no compatible document picker, the app reports that limitation. It does not request broad filesystem access, root, or permission to install another application. USB storage visibility depends on the Android device and its document provider. A USB permission dialog grants access to the USB device; it does not automatically grant access to its mounted filesystem.

## PSP USB contract

The diagnostic path is intentionally narrow:

| Property | Required value or behavior |
| --- | --- |
| Device | Exactly one PSP candidate; native VID `054C`, PID `01CD` |
| USB descriptor | `bcdDevice` 0100 or 0302; this does not identify the firmware |
| Firmware identity | NAT3 extension: firmware `0302`, capabilities `0007`, build `03020001` |
| Functions | Mass storage and one matching HID interface on the same USB device |
| Input endpoint | Interrupt IN, 8-byte maximum packet |
| Request | `bmRequestType=0xA1`, `GET_REPORT=1`, `wValue=0x0300`, actual HID interface number |
| Payload | Exactly 64 bytes; no Windows HIDAPI report-ID prefix |
| Validation | PDK2 magic, version 3, STATUS operation, NAT3 identity, zero reserved field, known phase/flags, nonzero session, coherent sequence/result |
| Host/progress | Report host-confirmed status honestly; validate phase/activity and unsigned 64-bit counters; no progress before a host claim |
| Device changes | Recheck the attached USB device after the request; discard a disconnected result |

The request value `0x0300` means **Feature report type 3, report ID 0**. It is not the firmware revision. Sony may publish the composite USB device as `bcdDevice=0100`; the host must inspect the returned NAT3 identity. Firmware revisions 0300 and 0301, the old 32-byte frame, a Windows 65-byte prefixed report, and unknown identity/capability/build combinations are rejected. NAT3 is an ABI/build discriminator, not cryptographic attestation or a physical volume identity proof.

Android's kernel may own the HID interface. A class control request can fail while that driver is attached. This implementation never calls `claimInterface`, never forces a driver detach, never changes the USB configuration, and never submits `SET_REPORT`, `HOST_BEGIN`, or `HOST_PROGRESS`. A negative Java `controlTransfer` result is reported as an access/transport failure, with the possible HID conflict described; Java does not supply enough information to label it definitively as a particular errno. The kernel's gamepad ownership is preserved, but firmware 0302 keeps PSP input gated until a host claim and appropriate phase/activity have been confirmed.

The PDK2 storage flag describes the PSP's Sony storage driver. It does not establish the Android mount, its filesystem identity, flush completion, or safe removal. A SAF grant, a folder named PSP, and a visible gamepad likewise do not prove that all three refer to the same physical PSP. Those are explicit gates for a future synchronization implementation.

## Validation

Pure JVM tests exercise the PDK2 frame and revision gates, unsafe/unknown responses, input signatures and path validation, cancellation, insufficient storage, unknown source length, duplicate imports, corrupt destinations, token replay/mismatch, interrupted launches, explicit recovery, and closure evidence. A test starts a second JVM to check the actual OS lock across processes and verifies that abrupt process death remains an interruption. Hardware tests are a separate acceptance gate.

On a real Android TV device, record the model, Android version, ABI, USB topology, and PSP firmware revision before evaluating:

1. TV launcher visibility, legible focus, remote D-pad/confirm/Back, and generic gamepad navigation.
2. Import from a readable USB document provider; verify the original remains intact and the local file's hash matches. Exercise cancellation, unplug during reading, low space, app recreation, and an interrupted import.
3. Start a legally owned game in the embedded engine, use the controller, save, exit the game, and confirm return to the Dock in the same application. Repeat with another game. Exercise Home/resume and process death separately; neither may be mistaken for a clean shutdown or trigger PSP writes.
4. Confirm a connected PSP is still available as an Android input device before and after the read-only USB probe. Record denied permission, unsupported revision, multiple candidates, short response, and kernel ownership failures without forcing the interface.
5. Select a readable PSP folder and inspect the reported capabilities. The UI must continue to say that physical association and automatic safe removal are unverified.

These checks do not install or update PSP firmware, modify the PSP Memory Stick, or grant approval to enable write-back. A later parity milestone needs validated composite-device support, physical PSP-to-volume association, a safe phase/control transport, save synchronization with conflict recovery, and platform-appropriate flush/eject evidence.

## Primary references

- [Android USB host APIs](https://developer.android.com/develop/connectivity/usb/host)
- [Storage Access Framework](https://developer.android.com/guide/topics/providers/document-provider)
- [Android TV navigation](https://developer.android.com/training/tv/start/navigation)
- [Android Gradle Plugin 8.11 compatibility](https://developer.android.com/build/releases/agp-8-11-0-release-notes)
- [Android Kotlin compiler support](https://developer.android.com/build/kotlin-support)
- [PPSSPP source, v1.20.4](https://github.com/hrydgard/ppsspp/tree/v1.20.4)

The PDK2/v3 layout and supported build are taken from this repository's `homebrew/native-dock/native_protocol.h`, `native_report_descriptor.h`, and `native_dock.py` (0302 contract introduced in bb397d1), rather than inferred from generic USB gamepad behavior.
