# Get your PSP Dock up and running

**English** · [Español](INSTALL.es.md)

PSP Dock comes in **two downloads**, one for your Windows PC and one for your PSP. The PSP needs two program files working together: `EBOOT.PBP`, which appears in the PSP Games menu, and `docknative.prx`, which handles the Dock connection.

## 1 · Set up Windows

Download **[PSP-Dock-Windows-v0.1.3.exe](https://github.com/jalinhabrava/Psp-dock/releases/latest/download/PSP-Dock-Windows-v0.1.3.exe)** from GitHub Releases. Save it wherever you like and run it. It's portable, **doesn't require Python**, and doesn't install an always-on background service.

You'll also need your own copy of [PPSSPP for Windows](https://www.ppsspp.org/download/). In PSP Dock's settings, choose your PPSSPP location and where you'd like to keep games and saves. No games or Sony software are included.

The executable is not digitally signed yet, so Windows may warn about an unknown publisher. Only run it if you've downloaded it from this project's official GitHub release, and check its SHA-256 against the published hash list.

## 2 · Set up your PSP

Download **[PSP-Dock-PSP-v0.1.2.zip](https://github.com/jalinhabrava/Psp-dock/releases/download/v0.1.2/PSP-Dock-PSP-v0.1.2.zip)**. Connect your PSP in its original USB Storage mode (or use a Memory Stick reader).

For a **fresh installation**, copy the complete folder structure from the ZIP to the root of your Memory Stick:

```text
Memory Stick/
└── PSP/
    └── GAME/
        └── PSPDockNative/
            ├── EBOOT.PBP
            ├── docknative.prx
            ├── LICENSE-FuSa-GPL-3.0.txt
            └── LICENSE-DejaVu.txt
```

The ZIP also contains a verification manifest and installation instructions. **The EBOOT by itself is not enough**: it must have the matching `docknative.prx` next to it.

**Upgrading an existing PSP Dock?** If the `PSPDockNative` folder already exists, do **not** overwrite it without a backup. Save a copy of the previous app and your saves, then follow the verified upgrade process described in [psp_native_upgrade.py](psp_native_upgrade.py). It checks your PSP's identity and the backup before replacing the application.

Nothing in this package writes to the PSP flash or installs ARK plugins. The primary test device is a PSP-2003 running 6.61 and ARK-5; other devices and firmwares may need additional work.

## 3 · Play

Launch PSP Dock on Windows, then on your PSP. Connect USB and follow the on-screen instructions to scan your library and choose what, if anything, to copy.

Once controller mode is ready, navigate the PC's XMB-style menu using the real PSP. Choose a game and play through PPSSPP. When you're finished, **close the game normally**. Check any pending saves before leaving PSP Dock, and wait for the verified sync message.

Don't unplug the cable while files are being copied or verified.

### Retro games

If PSP Dock recognizes emulator homebrew on your PSP, select that emulator
in the Windows XMB to see its ROMs in a right-side menu. RetroArch and
license-approved cores can download and install **in the background on your
PC**; the emulator icon shows progress. Installing the Windows emulator
does **not** require switching your PSP back to USB Storage. A ROM missing
from your PC is different: copying it from the Memory Stick requires a
verified USB transfer and your approval. Restricted cores and BIOS files
are never downloaded automatically.

### Language selection

There's **one Windows executable and one PSP EBOOT for both English and Spanish**. Windows defaults to the browser/UI language; you can change it under **Settings → Language**. The PSP automatically uses its own **System Settings → System Language**. No extra download is needed when you change languages.

## Need a hand?

If your library doesn't appear, make sure Windows can detect the PSP and the handheld is running compatible PSP Dock homebrew. If a save is waiting to sync, **don't delete it**: reopen the Dock and follow the recovery message. You can report bugs in [Issues](https://github.com/jalinhabrava/Psp-dock/issues), including your Windows version, PSP firmware, and steps to reproduce — without sharing private saves or personal information.

**Credits and licenses:** [LICENSE.md](LICENSE.md) · [NOTICE.md](NOTICE.md) · [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).
