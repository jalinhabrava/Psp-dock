"""Audited, single-browser navigation of the actual Ported2Browser.com.

Uses a *separate* Chrome profile. Never reuses another browser's cookies,
solves challenges, changes the user agent, launches game downloads or uploads
ISOs. Source pages are visited only if the website permits ordinary access.

Requires: python -m pip install playwright==1.56.0
Run: python scripts/catalog/audit_ported2browser_browser.py --visible
"""
from __future__ import annotations

import argparse
import ipaddress
import json
import os
import re
import socket
import sys
import time
from collections import OrderedDict
from pathlib import Path
from urllib.parse import quote, urljoin, urlsplit

# Running as a script must work without relying on an external PYTHONPATH.
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from psp_dock.media.official_ported2browser import (
    PORT_ID, SOURCE, parse_official_detail,
)

MAX_PAGES = 15
MAX_DETAILS = 330
EXCLUDED_FROM_PORT_FIRST_PASS = frozenset(
    ("emulator-tag", "flash-tag", "compendium-tag", "repository-tag",
     "ai-tag", "fangame-tag")
)
ORIGINAL_GAME_PLATFORMS = frozenset((
    "NES", "Super Nintendo", "Nintendo 64", "GameCube", "Wii",
    "Game Boy", "Game Boy Advance", "Nintendo Switch",
    "Genesis", "Dreamcast", "Sega CD", "SegaMD",
    "DOS", "Windows", "macOS", "ZX Spectrum",
    "Xbox", "Xbox 360", "Xbox One", "Xbox Series X|S",
    "PlayStation", "PlayStation 2", "PSP", "PlayStation 3",
    "PlayStation 4", "Arcade", "Neo Geo", "Wii U",
))
LIST_CARDS = """
nodes => nodes.map(node => {
  const id = node.getAttribute('data-port-id') || '';
  const href = node.querySelector('a.atropos')?.getAttribute('href') || '';
  const title = node.querySelector('.card-box h2')?.textContent?.trim() || '';
  const author = node.querySelector('.card-box p')?.textContent?.trim() || '';
  const flags = Array.from(node.querySelectorAll('.cover-tags b, .cover > b'))
     .flatMap(el => (el.className || '').split(/\\s+/)).filter(Boolean);
  const alerts = Array.from(node.querySelectorAll('[aria-label]'))
     .map(el => el.getAttribute('aria-label')).filter(value =>
       value && /Requires|broken|Link /i.test(value));
  return {id, href, title, author, flags, alerts,
          broken:node.classList.contains('is-broken')};
})
"""


class AccessBlocked(RuntimeError):
    pass


def _source_url(url: str, *, detail: bool = False) -> str:
    if not isinstance(url, str) or len(url) > 1024:
        raise ValueError("Invalid directory URL.")
    parsed = urlsplit(url)
    if (parsed.scheme != "https" or parsed.hostname != "ported2browser.com"
            or parsed.username or parsed.password or parsed.fragment
            or parsed.port not in (None, 443)):
        raise ValueError("Only the original Ported2Browser.com is accepted.")
    if detail and not re.fullmatch(r"/ports/[0-9A-Z]{26}", parsed.path):
        raise ValueError("Not an original detail URL.")
    if detail and parsed.query:
        raise ValueError("Detail URLs must not contain query arguments.")
    if not detail and parsed.path not in ("", "/"):
        raise ValueError("Not an original listing URL.")
    return url


def _private_candidate(host: str) -> bool:
    """No host probes towards private or internal addresses."""
    try:
        results = socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
    except OSError:
        return True
    if not results:
        return True
    try:
        return any(not ipaddress.ip_address(result[4][0]).is_global
                   for result in results)
    except ValueError:
        return True


def _goto_directory(page, url: str, *, interactive: bool,
                    success_selector: str = "#catalog-results"):
    response = page.goto(url, wait_until="domcontentloaded", timeout=25000)
    if response is None:
        raise AccessBlocked("No HTML response from the official directory.")
    blocked = response.status == 403 or page.locator("#human-form").count() > 0
    if blocked and interactive:
        print("This site's ordinary human check is displayed in a visible "
              "separate Chrome window. Complete it manually if desired; "
              "the script will not solve or circumvent it.", flush=True)
        try:
            page.wait_for_function(
                "(selector) => !document.querySelector('#human-form') && "
                "Boolean(document.querySelector(selector))",
                arg=success_selector,
                timeout=90000,
            )
        except Exception as exc:
            raise AccessBlocked("The site's human check was not completed.") from exc
        if page.locator("#human-form").count():
            raise AccessBlocked("The site's human check is still active.")
    elif blocked:
        raise AccessBlocked("The official directory returned a human check / "
                            "HTTP 403. No automated access attempted.")
    elif response.status >= 400:
        raise AccessBlocked(f"The directory rejected the request: HTTP {response.status}.")
    if urlsplit(page.url).hostname != "ported2browser.com":
        raise AccessBlocked("Unexpected redirection away from the official directory.")


def _listing_cards(page):
    cards = page.locator("#catalog-results article.card").evaluate_all(LIST_CARDS)
    seen = {}
    for row in cards:
        game_id = row.get("id", "")
        if not PORT_ID.fullmatch(game_id):
            continue
        try:
            url = _source_url(urljoin(SOURCE, row["href"]), detail=True)
        except (KeyError, ValueError):
            continue
        if url != SOURCE + "ports/" + game_id:
            continue
        title = row.get("title", "")
        if not isinstance(title, str) or not 2 <= len(title) <= 120:
            continue
        seen[game_id] = {
            "id": game_id,
            "title": title,
            "author": str(row.get("author", ""))[:120],
            "detail_url": url,
            "source": SOURCE,
            "flags": row.get("flags", []),
            "alerts": row.get("alerts", []),
            "broken": bool(row.get("broken")),
            "status": "directory_only",
            "direct_url": None,
        }
    return list(seen.values())


def _atomic_json(path: Path, report: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".partial")
    try:
        temporary.write_text(
            json.dumps(report, indent=2, ensure_ascii=False, sort_keys=True)
            + "\n", encoding="utf-8")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _host_probe(context, url: str) -> dict:
    """A lightweight landing-page check, NOT a claim that gameplay works."""
    host = urlsplit(url).hostname
    if not host or _private_candidate(host):
        return {"status": "not_probed", "reason": "Host could resolve privately."}
    page = context.new_page()
    try:
        response = page.goto(url, timeout=15000, wait_until="domcontentloaded")
        if not response:
            return {"status": "no_response"}
        data = {
            "status": "landing_reachable" if 200 <= response.status < 400
                else "landing_error",
            "http_status": response.status,
            "final_origin": urlsplit(page.url).scheme + "://" +
                (urlsplit(page.url).hostname or ""),
            "page_title": page.title()[:120],
            "has_canvas": page.locator("canvas").count() > 0,
            "has_start_control": page.locator(
                "button:has-text('Play'), a:has-text('Play'), "
                "button:has-text('Start'), a:has-text('Start')").count() > 0,
        }
        if urlsplit(page.url).hostname != host:
            data["status"] = "redirected_to_other_host"
        return data
    except Exception as exc:
        return {"status": "probe_failed", "reason": str(exc)[:200]}
    finally:
        page.close()


def collect(*, profile_dir: Path, output: Path, visible: bool = False,
            interactive: bool = False, max_pages: int = MAX_PAGES,
            max_details: int = MAX_DETAILS, delay: float = 2.5,
            probe_hosts: int = 0, resume: bool = True,
            platform: str | None = None):
    """Serial Playwright browsing. Never shares authentication or device data."""
    if not 1 <= max_pages <= MAX_PAGES or not 1 <= max_details <= MAX_DETAILS:
        raise ValueError("Page/detail safety limit exceeded.")
    if delay < 1.5:
        raise ValueError("Minimum 1.5 seconds between directory navigations.")
    if not 0 <= probe_hosts <= 50:
        raise ValueError("Host probe safety limit exceeded.")
    if interactive and not visible:
        raise ValueError("Human check can only be completed in a visible browser.")
    if platform is not None and platform not in ORIGINAL_GAME_PLATFORMS:
        raise ValueError("Choose an original console/PC platform, not a web-minigame category.")
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise RuntimeError(
            "Playwright is not installed. pip install playwright==1.56.0") from exc

    records = OrderedDict()
    report = {
        "source": SOURCE,
        "kind": "original_directory_browser_audit",
        "platform_filter": platform,
        "reported_total": None,
        "listed": 0,
        "detailed": 0,
        "complete": False,
        "games": [],
    }
    if resume and output.is_file():
        previous = json.loads(output.read_text(encoding="utf-8"))
        if (previous.get("source") != SOURCE or
                previous.get("kind") != "original_directory_browser_audit" or
                previous.get("platform_filter") != platform or
                not isinstance(previous.get("games"), list)):
            raise ValueError("Existing audit report is not from this directory/platform.")
        for row in previous["games"][:MAX_DETAILS]:
            if (isinstance(row, dict) and PORT_ID.fullmatch(str(row.get("id", "")))
                    and row.get("detail_url") == SOURCE + "ports/" + row["id"]):
                records[row["id"]] = row
        report.update({k: v for k, v in previous.items() if k != "games"})
        report["games"] = list(records.values())
    with sync_playwright() as playwright:
        # A private audit profile is deliberately different from the user's
        # normal Chrome profile. No cookies/tokens are exported in the report.
        context = playwright.chromium.launch_persistent_context(
            user_data_dir=str(profile_dir),
            channel="chrome",
            headless=not visible,
            accept_downloads=False,
            viewport={"width": 1365, "height": 900},
            java_script_enabled=True,
        )
        try:
            page = context.pages[0] if context.pages else context.new_page()
            next_url = SOURCE + ("?platform=" + quote(platform, safe="")
                                 if platform else "")
            for page_no in range(max_pages):
                _goto_directory(page, _source_url(next_url), interactive=interactive)
                rows = _listing_cards(page)
                if not rows:
                    raise AccessBlocked(
                        "The directory did not expose any original port cards.")
                for row in rows:
                    if platform:
                        row["original_platform"] = platform
                    records.setdefault(row["id"], row)
                report["listed"] = len(records)
                result_count = page.locator("#result-count")
                if result_count.count() and report["reported_total"] is None:
                    try:
                        report["reported_total"] = int(
                            result_count.inner_text().replace(",", "").strip())
                    except ValueError:
                        pass
                report["games"] = list(records.values())
                _atomic_json(output, report)
                print(f"Listing page {page_no + 1}: {len(rows)} cards, "
                      f"{len(records)} distinct.", flush=True)
                next_anchor = page.locator("#catalog-results nav[data-pagination] "
                                           "a[rel=next]")
                if not next_anchor.count():
                    break
                next_url = _source_url(
                    urljoin(SOURCE, next_anchor.first.get_attribute("href")))
                time.sleep(delay)

            detail_page = context.new_page()
            host_budget = probe_hosts
            for index, row in enumerate(records.values()):
                if index >= max_details:
                    break
                if row.get("status") not in ("directory_only", "detail_not_available"):
                    continue
                if row.get("broken"):
                    row["status"] = "broken_as_reported"
                    continue
                if (EXCLUDED_FROM_PORT_FIRST_PASS & set(row.get("flags", []))):
                    row["status"] = "excluded_browser_emulation_or_non_port"
                    continue
                try:
                    _goto_directory(detail_page, _source_url(row["detail_url"],detail=True),
                                    interactive=interactive,
                                    success_selector=".port-page")
                    detail = parse_official_detail(
                        detail_page.content(), row["id"])
                    row["detail"] = detail
                    row["status"] = detail["status"]
                    report["detailed"] += 1
                    if host_budget and detail["direct_url"]:
                        row["probe"] = _host_probe(context,detail["direct_url"])
                        host_budget -= 1
                except (AccessBlocked, ValueError) as exc:
                    row["status"] = "detail_not_available"
                    row["error"] = str(exc)[:200]
                    if isinstance(exc, AccessBlocked):
                        print("Access blocked; ending without further requests.",
                              flush=True)
                        break
                finally:
                    report["games"] = list(records.values())
                    _atomic_json(output, report)
                print(f"Detail {index + 1}/{min(len(records),max_details)}: "
                      f"{row['title']} -> {row['status']}", flush=True)
                time.sleep(delay)
            report["complete"] = (
                len(records) >= (report["reported_total"] or float("inf")) and
                all(row.get("status") not in ("directory_only", "detail_not_available")
                    for row in records.values())
            )
            _atomic_json(output, report)
            return report
        finally:
            context.close()


def main():
    parser = argparse.ArgumentParser(
        description="Read real Ported2Browser.com with normal Chrome/Playwright")
    parser.add_argument("--visible", action="store_true")
    parser.add_argument("--interactive", action="store_true",
                        help="Allow manually completing ordinary human check")
    parser.add_argument("--max-pages", type=int, default=MAX_PAGES)
    parser.add_argument("--max-details", type=int, default=MAX_DETAILS)
    parser.add_argument("--delay", type=float, default=2.5)
    parser.add_argument("--probe-hosts", type=int, default=0)
    parser.add_argument("--platform", choices=sorted(ORIGINAL_GAME_PLATFORMS),
                        help="Only ports of this console/PC platform; excludes native web games")
    parser.add_argument("--fresh", action="store_true",
                        help="Ignore an earlier partial report instead of resuming")
    parser.add_argument("--profile-dir", type=Path, default=Path(
        os.environ.get("LOCALAPPDATA", ".")) /
        "PSP-Dock" / "directory-audit-chrome")
    parser.add_argument("--output", type=Path, default=Path(
        os.environ.get("LOCALAPPDATA", ".")) /
        "PSP-Dock" / "directory-audit.json")
    args = parser.parse_args()
    try:
        result = collect(
            profile_dir=args.profile_dir,
            output=args.output,
            visible=args.visible,
            interactive=args.interactive,
            max_pages=args.max_pages,
            max_details=args.max_details,
            delay=args.delay,
            probe_hosts=args.probe_hosts,
            resume=not args.fresh,
            platform=args.platform)
        print("Audit complete:", result["listed"], "listed;",
              result["detailed"], "details. See", args.output)
    except AccessBlocked as exc:
        print("Directory access unavailable:", exc, file=sys.stderr)
        sys.exit(3)


if __name__ == "__main__":
    main()
