# Original Ported2Browser.com: source-controlled import

## Source, not the imitators

PSP Dock accepts only a user-exported HTML document from
https://ported2browser.com/. Other domains (.sbs, .org, .co, etc.)
must never be substituted, regardless of their similar names.

The 14 November 2026 homepage reports 330 ports, but its HTML embeds
only the first 30 cards from Recent, Popular and A-Z, with overlap.
The user-provided document contains 82 distinct IDs, not 330.
Every card links to an official detail page of the form
https://ported2browser.com/ports/01M7B4G8SQ3YWXN97YN95Z7SYP.
This is not necessarily the creator's playable game URL.

## Manual, offline import

    python -m psp_dock.media.official_ported2browser --html "ported2browser.html" --output "official-export.json"

This tool makes no network requests. It parses and deduplicates only
the HTML supplied by the user. Every entry has the directory_only
status, a null confirmed_launch_url, and an unknown original_platform.
It preserves creator credit and observed badges such as requires disc,
broken link, emulator and work-in-progress. IDs are uppercase 26-char
original port identifiers, not invented slugs.

Importing these metadata entries does not add unverified games to the
playable XMB. An actual direct author-site URL is required for Play.

## Next source needed

The Response HTML of an individual
https://ported2browser.com/ports/<ID> page establishes how the original
directory presents its author link, platform, controller support and
required files. Do not guess an author link from the title or profile.
Obtain subsequent pages and individual details through ordinary,
authorized browser navigation, without bypassing bot protection or
performing automated bulk requests.

## Safety

No authentication tokens, browser cookies, private game data, save
files or PSP device reads are needed. This importer cannot authorize
the WebView launcher to visit a third-party URL.

## Direct play links: example verified against supplied SOCOM II HTML

The original port detail page contains an anchor with these attributes:

    class="button primary play"
    href="https://socomlegacy.com/"
    data-id="01M6FJS3FP9ER9BWP5FTP5XP7H"
    data-page="/ports/01M6FJS3FP9ER9BWP5FTP5XP7H"

Its controls explicitly say **gamepad supported**, keyboard and touch
unsupported. Its notes say **Requires disc image**; the original port
description explains that the disc is read into local browser storage.
This identifies a *direct URL candidate*, not an automatic gamepad test
or permission to read/upload a selected ISO.

Use parse_official_detail(html, game_id) to obtain the unique HTTPS
creator URL, controls, tags, disc-image alerts and provenance. Multiple
URLs or missing play anchors are not converted into an automatic launch.

## Normal browser-assisted audit (optional)

Playwright 1.56.0 can drive a new isolated Chrome profile on the user's
PC. It does not attach to the normal Chrome profile or copy cookies:

    python -m pip install playwright==1.56.0
    python scripts/catalog/audit_ported2browser_browser.py --max-pages 2 --max-details 10 --probe-hosts 2

This pilot is deliberately bounded; the full run can use --max-pages 15
and --max-details 330. A cache report is saved under the local private
PSP-Dock data directory, supports resuming, and contains only public
title/creator/page/author-site URL, controls, and audited status. It
does not download a game, press login or Play, read any ISO, or launch
PPSSPP. An optional host probe checks only the landing page. The status
"landing_reachable" never means "gameplay_verified".

To restrict extraction to actual console/PC originals and avoid the
site's Web browser / Browser / Mobile / Tabletop / Java categories:

    python scripts/catalog/audit_ported2browser_browser.py --visible --interactive --platform "PlayStation 2" --max-pages 2 --max-details 10

Use separate output files when selecting other platforms (PSP, Windows,
DOS, Nintendo 64, etc.). Directory entries marked Emulator, Flash,
Fangame or Repository are excluded from the first round of true
ports, but can be reviewed separately. The audit output is not
installed into PSP Dock until explicit game-specific approval.

If the official directory responds with HTTP 403 / human check, it
**stops rather than circumventing the protection**. A visible browser
can be started with --visible --interactive so a human may complete a
normal one-time check. The script does not solve the challenge and
never forces access. An operator should also comply with the site's
access terms and appropriate request limits.

Actual probe on the user's Windows PC: both isolated headless Chrome
and visible Chrome returned HTTP 403 / human check before any catalogue
page or detail could be collected. These probes did NOT establish
unattended access, and no source-page crawl was attempted.

### Browser methods

Obscura is CDP-compatible with Playwright, but a new isolated browser
does not automatically inherit access granted to the user's normal
Chrome. Replacing Chrome with Obscura cannot by itself resolve a
website access restriction. There is no reason to use stealth
mechanisms or bulk parallelism for this small directory.
