# Android TV protocol and controller preview

The baseline is Windows `main` at `ff3033351bd9901041eb6b42a639d1810e85bb81`, with PSP firmware **0308** and PDK2/v3. Android's read-only decoder accepts the same identity/build/capability matrix as `psp_dock/devices/native_dock.py` (0303 through 0308); the controller preview requires **0308**. Firmware 0300, 0301 and 0302 remain rejected. Sony's `bcdDevice=0100` is only a USB filter.

`windows_vectors.py` generates 43 wire vectors from the current Python decoder and encoder. The Kotlin tests compare accepted status fields, rejected states, exact commands, Output fragments and their CRC against those fixtures. Run `python3 protocol/windows_vectors.py --check` from `android-tv/` in CI; regenerate and review parity whenever the Windows protocol changes.

## USB ownership

The diagnostic button still uses one GET_REPORT without claiming or detaching an interface. The separate **Use PSP as controller · preview** action asks for visible confirmation and Android's USB permission. A foreground `connectedDevice` service then owns **only the HID interface** with `claimInterface(hid, true)`. The application replaces Android's kernel handling of this PSP's buttons with its own input reader. It never claims the Sony mass-storage interface, changes configurations, resets USB, writes a PSP file, or runs at boot.

A single Sony 054C:01CD device, expected composite functions, interrupt IN endpoint, USB descriptor and live NAT3 identity are required. The service sends HOST_BEGIN only when unclaimed and SET_PHASE to CONTROLLER, with the exact verified nonce and firmware build. No copy, SAFE or HOST_SHUTDOWN method is exposed by the controller client. A failed fragment, uncertain commit, wrong ACK, nonce change or timeout stops the session; commands are not automatically replayed.

| Transfer | Android wire format |
| --- | --- |
| Status | GET_REPORT, bmRequestType `A1`, request `01`, wValue `0300`, actual HID interface number; exactly 64 bytes |
| Command | 32 Output reports of four bytes: `C0..DF`, one random batch token, two command bytes |
| Commit | One four-byte Output report: `E0`, token, CRC16-CCITT low byte, high byte |
| Output setup | SET_REPORT, bmRequestType `21`, request `09`, wValue `0200`, actual HID interface number |
| Input | Unnumbered eight-byte interrupt packet: axes 0–3, low nibble of byte 4 is hat, high nibble plus byte 5 contain 12 buttons, bytes 6–7 are zero padding |

The idle rate is set to 8 (32 ms) and confirmed with GET_IDLE because firmware otherwise emits input only when it changes. This provides real USB heartbeat samples while holding a button. The previous idle rate is restored before releasing the interface where possible. Afterward, unplug/reconnect may be required for another app to reclaim the PSP's kernel gamepad input.

## Internal input bridge

`UsbControllerService` is not exported. Both the Dock Activity and the embedded PPSSPP Activity bind over Android Messenger. Binding does not itself start USB. The engine does not use BIND_AUTO_CREATE.

- Message 1 registers a `replyTo` Messenger; message 2 unregisters it.
- Input message 10: `protocol=1`, `active`, monotonic `sequence`, USB sample `elapsedRealtime`, `buttons` (0–4095), raw `hat` (0 neutral, 1–8 clockwise), `x` and `y` (0–255, centre 127).
- Status message 11: `protocol=1`, `active`, `text`, event `elapsedRealtime`, `exitRequested`.

New consumers must observe neutral input before any held control can reach a menu or game. Both processes reject stale/replayed samples and release buttons after 250 ms without fresh input. Pausing, disconnecting, losing Binder, switching consumers and stopping the service neutralize input. A fresh firmware exit-request event enters PPSSPP's existing clean finish path; it is not cached and replayed to a later engine session.

The foreground session stops on explicit stop, task removal, device/transport failure or firmware exit request. It is START_NOT_STICKY. **Closing the game is separate from closing Dock.** Game saves stay in Android-private storage. The app does not send HOST_SHUTDOWN because it cannot prove that Android released the PSP volume; it tells the user to use Android's system ejection before unplugging.

## Device acceptance still required

Passing unit tests and compiling the APK cannot establish that a particular TV allows HID ownership, accepts all Output control transfers or preserves its Sony storage mount. On a physical TV, verify USB grant/refusal, neutral first input, each button/axis, held input, app/game switching, Start+Select, ordinary game exit, process loss, service stop, unplug, no unexpected duplicate input, and original PSP files remaining unchanged. Keep the TV remote available. No physical PSP probe or firmware installation is part of the automated tests.
