# PSP Dock — development handoff, 1 November 2026

This branch builds on Claude's PSP XMB PR #1 (ae2ae7f). The PSP
and PPSSPP were previously validated physically for gamepad control,
launch, HOME/pause and return to the Windows XMB. The new features
described below have been tested on the PC only. The actual PSP
firmware, USB driver and savedata were NOT modified.

## Original XMB art, sound and cache

psp_xmb_media.py reads ICON0.PNG, PIC0.PNG, PIC1.PNG and SND0.AT3
from ISO, CSO or EBOOT.PBP with bounded reads and validation.
FFmpeg converts ATRAC3 sound locally to browser-compatible MP3.
The cache is keyed by Sony volume serial and game version identity.
The XMB shows the original background and optional foreground,
and previews game audio at low volume when a game is selected.
There is a user setting to turn off music. Chrome autoplay may
require a browser user gesture before audio playback works.

PC-only Dariusburst test: PIC1 205242 bytes, SND0 472608 bytes,
converted MP3 413667 bytes; no PIC0 supplied by that ISO.
No music is redistributed or uploaded to other services.

## Choose where PC content is stored

Settings now exposes a single main PC storage directory with a
native Windows folder picker. Games, PPSSPP memstick/SAVEDATA,
HD textures, artwork, preview music and backups can be relocated.
All existing files are copied and SHA-256 verified into a NEW
directory on a FIXED PC drive, retaining originals as rollback.
Configuration/session metadata remain in AppData. No files in the
PSP USB volume are relocated or overwritten, and active sessions,
pending transfers, symlinks and overlapping destinations are
rejected. After relocation PPSSPP needs a fresh preparation.
POST /api/storage-root applies the explicitly chosen location.

## User-initiated HD textures

Graphics can show curated HD packs matching the actual DISC_ID
read from the game in the PC. Installer checks HTTPS source,
maximum size, SHA-256 digest, ZIP path traversal, expansion ratios,
valid textures.ini and refuses executables or overwrites.
Verified packs go to PC memstick/PSP/TEXTURES/DISC_ID/textures.zip.
The PC-only PPSSPP INI enables texture replacement when a pack exists.

Official format documentation:
https://www.ppsspp.org/docs/reference/use-texture-replacement/

Current approved archive list is EMPTY: no downloadable pack has
yet passed all source integrity and size checks. There is a
genuine Patapon 3 EU pack supporting UCES01421 at
https://github.com/Lin-zl522/Patapon-3-HD-Texture-Pack,
but the repository is approximately 900 MB and is not yet
verified as a directly installable single ZIP. No pack is
yet verified for Dariusburst ULJM05558. Do not claim otherwise.

## A single-game copy from PSP TRIANGLE

TRIANGLE ALWAYS opens the XMB right-side contextual menu.
It never copies a game merely because the menu opened.
Ready games show "Jugar"; missing ones offer
"Sincronizar con Dock". The user must choose that item,
then confirm "Sí" in a separate dialog.

The backend saves one pending copy intent with original Sony
volume serial, relative game path and game ID. It waits for
that same USBSTOR volume to return and for HID to be absent.
Only then it copies a missing ISO/PBP to the PC with the existing
verified transfer/journal system. An interrupted attempt is
recorded, not automatically retried.

The transition HID to storage, and storage back to HID, are
still MANUAL. UI says so and blocks normal controller navigation
during the copy. Automatic transitions remain the next
firmware-engineering milestone.

## Exit and app shutdown

Exit requests are durable. Only after original Sony USBSTOR
returns, HID is absent, PPSSPP is closed and save sync is
verified to have no remaining differences does Windows close
the unique PSP Dock browser app window and shut down its server.
Unknown volume, save conflicts, pending copy and failures
never trigger automatic shutdown. If no standalone Chrome window
exists, the service still exits but a tab may remain open.

## The actual USB bridge is not ready

The PSP HID firmware already implements a PDK1 session-bound
REQUEST_STORAGE feature report. Windows can encode and validate
this request. But the installed PSP Dock kernel core does NOT yet
consume the request and activate Sony USBSTOR.

USBSTOR to HID is even harder: the original Sony mass-storage
interface does not provide a proven host-to-firmware control
channel independent of FAT. The proposed solution requires an
audited composite USB or vendor-control interface, host volume
flush/lock evidence, live matching PSP ACK, careful device
reenumeration and screen brightness recovery.

Do not attempt Windows parent PnP eject, SCSI experiments or
firmware installation on the user's real Memory Stick. Test a
dedicated disposable card with verified backups and recovery
before attempting actual role changes.

## Claude frontend coordination

Keep the five-category original XMB and gamepad navigation.
TRIANGLE is a right-side menu only. The backend endpoints for
art/audio, HD packs, one-game copy queues, storage relocation
and verified shutdown now exist on this feature branch. Treat
all hardware transition states as pending until independently
proved in a physical PSP session. Do not merge over Claude's
PR #1 without coordinating the branch dependency.

Focused unit tests should set PSP_DOCK_TEST_NO_REAL_USB=1.
The LIVE PSP and savedata were not touched by this branch.
