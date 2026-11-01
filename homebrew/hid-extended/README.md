# PSP Dock — HID Extended TEST (15 minutes)

**COMPILE-ONLY EXPERIMENT. NOT INSTALLED ON PSP.**

This variant copies physically verified 90-second HID-only LAB sources
into a separate WSL build and changes only:

* kernel HID timeout from 90 to 900 seconds (15 minutes);
* user EBOOT auto-exit from 105 to 920 seconds;
* title and homebrew folder to PSPDockHidLong.

The 90-second LAB source and compiled binaries remain intact. This mode
still starts HID only with USBSTOR stopped and the cable unplugged.
START+SELECT held for one second, cable loss after connection, and
the 15-minute maximum terminate HID. No flash or SAVEDATA writes.

This does NOT automatically switch storage/HID, darken the PSP display,
or prove FAT ownership. It is not the final Nintendo Switch-like Dock.

To build in WSL Ubuntu-22.04, run build-wsl.sh; the resulting separate
dist folder holds EBOOT.PBP, hidonlycore.prx and bridgehid.prx.
There is deliberately NO installation script.

Before any future hardware test: use a disposable Memory Stick, ensure
a backup and PSP recovery plan, review the build, install separately as
PSP/GAME/PSPDockHidLong without overwriting the proven short LAB.
If black screen or shutdown occurs, stop testing and recover ARK-5
with START; never retry a failed transition blindly.

The HID driver derives from GPL-3.0 FuSa GamePad. Preserve upstream
credits and the LICENSE for any future distribution.
