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
