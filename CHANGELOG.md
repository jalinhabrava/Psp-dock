# Changelog

**English** · [Español](CHANGELOG.es.md)

## Unreleased — Clean final-version source tree

- `main` now contains only the supported Windows host, the final PSP 0308 homebrew and the tools needed to build, install, upgrade and back up that version.
- Removed retired USB/HID/K1/VSH experiments, old firmware variants, historical lab scripts and their obsolete tests. The unfinished Android TV prototype remains accessible on its own development branch, not in the released source tree.
- Moved the small ELF/USB safety-gate dependencies and third-party notices into the final PSP 0308 directory so it builds independently. The audited EBOOT/PBP output is byte-identical to the published v0.1.2 PSP binary.
- No change to the Windows-to-PSP protocol, game library, ROM copying or save-sync behavior. Old source remains recoverable from Git history without cluttering a fresh checkout.

## v0.1.3 — Retro games, without the setup detour · November 4, 2026

- RetroArch and approved compatible cores now install on **Windows in the background** when PSP emulators are discovered; existing manual-policy users can start the installation just by selecting the emulator. Installing PC software never requires switching the PSP back to USB storage.
- The XMB shows per-emulator installation progress with an animated status ring (or a static indicator when reduced motion is preferred), while browsing remains available.
- Select a PSP emulator → see its ROMs in the right-hand menu → select a ready ROM to launch it directly in RetroArch with the existing verified PSP controller and save safeguards.
- A missing PC ROM still requires an explicit USB transfer. External RetroArch installations and user-disabled downloads are never modified; restricted cores, ROMs and BIOS are not downloaded.
- PC-only live download smoke: the official pinned RetroArch 1.22.2 archive and mGBA core were fetched, unpacked and verified in an isolated temporary directory, then removed without executing any downloaded code.
- No firmware, PSP EBOOT, USB protocol, game-save or memory-stick transfer logic was changed.

## v0.1.2 — One Dock, two languages · November 4, 2026

- **English and Spanish** are included in the *same* Windows executable and PSP EBOOT. No separate downloads or game/save migrations are needed to change language.
- The Windows interface chooses the browser/UI language on first launch and allows a manual preference in the XMB Settings menu.
- The PSP interface uses the system language selected in the console settings, with an English fallback.
- README, install guide and branding are now available in English, with Spanish translations one click away.
- The USB protocol, save-transfer behavior, native revision 0308 and safety checks remain unchanged.

The language implementation is validated with software tests. Testing on the exact hardware combinations and consoles of other users remains important.

## v0.1.1 — Windows and PSP downloads · November 4, 2026

Our first downloadable release: a portable Windows executable that doesn't need a Python installation, and a PSP 0308 homebrew package with EBOOT.PBP, docknative.prx and the required licenses. It also introduced a [getting-started guide](INSTALL.md), a friendlier README and a hero banner using the approved PSP Dock logo.

**Important:** The Windows executable is unsigned and doesn't bundle PPSSPP. PSP Dock requires compatible homebrew and should be tested on each console. This release did not change the USB protocol or the save-sync logic of v0.1.0.

## v0.1.0 — First public source release · November 3, 2026

**Windows MVP:** XMB with smooth transitions checked in a real browser, a Memory Stick-aware persistent catalog, the native 0308 Sony USB Storage/PSP HID session flow, explicit transfer choices, compatible PSP/retro saves with conflict review and verified returns, PSP-sourced media where available, and PPSSPP/RetroArch integration.

**Included but experimental:** Premium PSP homebrew UI, a hybrid HD texture pipeline, deterministic DZIP research (not a claimed universal DZIP decoder), and an Android TV app prototype.

**Not merged:** The Linux work remains in its own `feat/linux-dock` branch because it still assumes native protocol 0301 while the main Windows MVP uses 0308.

**Known validation:** A full Windows PSP Dock connection/exit cycle was completed with a real PSP. Automated host, UI and Chromium animation tests don't replace physical testing of every feature or certify an installer on every Windows PC.

**Licenses:** PolyForm Noncommercial 1.0.0 for original code, requiring credit to `jalinhabrava`; FuSa GAMEPAD-derived code under GPL-3.0 and other third-party material under its existing terms. See [NOTICE](NOTICE.md) and [third-party licenses](THIRD_PARTY_NOTICES.md).
