"""Import a user-exported HTML page of the *original* Ported2Browser.com.

No crawling, no Cloudflare bypass, no .org/.sbs source substitution and no
automatic authorization to launch a port. The directory card points to its
detail page, NOT to the playable author's runtime.
"""
from __future__ import annotations

import argparse
import ipaddress
import json
import re
from collections import OrderedDict
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlsplit

SOURCE = "https://ported2browser.com/"
MAX_HTML_BYTES = 4 * 1024 * 1024
PORT_ID = re.compile(r"^[0-9A-Z]{26}$")
TAG_CLASSES = {
    "emulator-tag": "emulator",
    "wip-tag": "wip",
    "fangame-tag": "fangame",
    "flash-tag": "flash",
    "compendium-tag": "compendium",
    "ai-tag": "ai",
    "repository-tag": "repository",
    "is-disc": "requires_disc_image",
}


def _classes(attrs):
    return set(dict(attrs).get("class", "").split())


class _Cards(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.cards = OrderedDict()
        self.stack = []
        self.tab = "page"
        self.tab_total = []
        self.current = None
        self.reading = None
        self.reading_depth = 0
        self.total = None

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        classes = set(attrs.get("class", "").split())
        if tag == "template" and attrs.get("data-catalog-preload"):
            self.tab = attrs["data-catalog-preload"][:24]
            if attrs.get("data-total", "").isdigit():
                self.tab_total.append(int(attrs["data-total"]))
        if tag == "article":
            game_id = attrs.get("data-port-id", "")
            if PORT_ID.fullmatch(game_id):
                self.current = {
                    "id": game_id,
                    "title": "",
                    "author": "",
                    "detail_url": f"{SOURCE}ports/{game_id}",
                    "flags": ["broken_link"] if "is-broken" in classes else [],
                    "alerts": [],
                    "tabs": [self.tab],
                    "confirmed_launch_url": None,
                    "status": "directory_only",
                    "original_platform": None,
                }
        if self.current is not None:
            if tag == "a" and attrs.get("href") == self.current["detail_url"]:
                self.current["has_valid_detail_link"] = True
            for key, flag in TAG_CLASSES.items():
                if key in classes and flag not in self.current["flags"]:
                    self.current["flags"].append(flag)
            if "alert-badge" in classes:
                note = (attrs.get("aria-label") or attrs.get("title") or "").strip()
                if 0 < len(note) <= 400 and note not in self.current["alerts"]:
                    self.current["alerts"].append(note)
            if tag in ("h2", "p") and self.reading is None:
                self.reading = "title" if tag == "h2" else "author"
                self.reading_depth = 1
            elif self.reading is not None:
                self.reading_depth += 1
        self.stack.append(tag)

    def handle_data(self, text):
        if self.current and self.reading:
            current = self.current[self.reading]
            self.current[self.reading] = (current + text)[:220]

    def handle_endtag(self, tag):
        if self.current and self.reading:
            self.reading_depth -= 1
            if self.reading_depth <= 0:
                self.current[self.reading] = self.current[self.reading].strip()
                self.reading = None
        if tag == "article" and self.current is not None:
            row = self.current
            self.current = None
            self.reading = None
            if (row.get("has_valid_detail_link")
                    and 2 <= len(row["title"]) <= 120
                    and len(row["author"]) <= 120):
                row.pop("has_valid_detail_link", None)
                previous = self.cards.get(row["id"])
                if previous:
                    for tab in row["tabs"]:
                        if tab not in previous["tabs"]:
                            previous["tabs"].append(tab)
                    for flag in row["flags"]:
                        if flag not in previous["flags"]:
                            previous["flags"].append(flag)
                    for alert in row["alerts"]:
                        if alert not in previous["alerts"]:
                            previous["alerts"].append(alert)
                else:
                    self.cards[row["id"]] = row
        if tag == "template":
            self.tab = "page"
        if self.stack:
            self.stack.pop()


def parse_official_homepage(raw: bytes | str) -> dict:
    """Read the homepage as supplied by the user; never fetch remote sites."""
    if isinstance(raw, bytes):
        if len(raw) > MAX_HTML_BYTES:
            raise ValueError("Original directory HTML exceeds the size cap.")
        raw = raw.decode("utf-8-sig")
    if not isinstance(raw, str) or len(raw) > MAX_HTML_BYTES:
        raise ValueError("Invalid or oversized directory HTML.")
    if ("ported2browser.com" not in raw or
            "data-catalog-preload" not in raw or
            "data-port-id" not in raw):
        raise ValueError("Not the original Ported2Browser.com directory export.")
    parser = _Cards()
    parser.feed(raw)
    parser.close()
    if not parser.cards:
        raise ValueError("No cards with valid original directory links found.")
    reported = max(parser.tab_total, default=0)
    if not reported or reported < len(parser.cards):
        raise ValueError("The export does not contain a coherent total count.")
    return {
        "schema": 1, "source": SOURCE,
        "scope": "user_supplied_homepage_only",
        "reported_total": reported,
        "extracted": len(parser.cards),
        "complete": False,
        "games": list(parser.cards.values()),
    }


def _public_game_url(value: str) -> str | None:
    """Accept a public author-host HTTPS URL; never a local or directory link."""
    if not isinstance(value, str) or not (8 < len(value) <= 2048):
        return None
    if any(ord(char) < 32 or char == "\\" for char in value):
        return None
    try:
        url = urlsplit(value)
        host = (url.hostname or "").lower().rstrip(".")
        if (url.scheme != "https" or not host or url.username or url.password
                or (url.port is not None and url.port != 443)
                or re.fullmatch(
                    r"[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?\.[a-z]{2,}", host
                ) is None or ".." in host):
            return None
        if (host in ("localhost", "ported2browser.com",
                     "ported2browser.org", "ported2browser.sbs")
                or host.endswith((".localhost", ".local", ".internal",
                                  ".test", ".invalid", ".example",
                                  ".ported2browser.com"))
                or any(char.isspace() for char in host)):
            return None
        try:
            ipaddress.ip_address(host)
        except ValueError:
            pass
        else:
            return None
        return value
    except ValueError:
        return None


class _Detail(HTMLParser):
    """Read only the original site's game page and its explicit Play anchors."""

    def __init__(self, game_id: str):
        super().__init__(convert_charrefs=True)
        self.game_id = game_id
        self.found_page_identity = False
        self.title = ""
        self.play_links = []
        self.controls = {}
        self.alerts = []
        self.tags = []
        self._title = False
        self._control = None
        self._control_label = False
        self._alert_div_depth = 0
        self._alert_text = ""
        self._alert_disc = False

    def handle_starttag(self, tag, attrs):
        fields = dict(attrs)
        classes = set(fields.get("class", "").split())
        if (tag == "input" and fields.get("name") == "return" and
                fields.get("value") == "/ports/" + self.game_id):
            self.found_page_identity = True
        if (tag == "a" and "play" in classes and "button" in classes and
                fields.get("data-id") == self.game_id and
                fields.get("data-page") == "/ports/" + self.game_id):
            link = _public_game_url(fields.get("href", ""))
            if link and link not in self.play_links:
                self.play_links.append(link)
        if tag == "h1":
            self._title = True
        if tag == "li" and "ctl" in classes:
            self._control = {
                "supported": "is-supported" in classes and "is-unsupported" not in classes,
                "label": ""
            }
        if tag == "span" and "ctl-label" in classes and self._control is not None:
            self._control_label = True
        if tag == "div" and "port-alert" in classes:
            self._alert_div_depth = 1
            self._alert_text = ""
            self._alert_disc = "is-disc" in classes
        elif tag == "div" and self._alert_div_depth:
            self._alert_div_depth += 1
        if tag == "a" and fields.get("href", "").startswith(
                "https://ported2browser.com?tag="):
            self.tags.append(fields["href"].split("?tag=", 1)[1][:48])

    def handle_data(self, data):
        if self._title:
            self.title = (self.title + data)[:120]
        if self._control is not None and self._control_label:
            self._control["label"] = (self._control["label"] + data)[:40]
        if self._alert_div_depth:
            self._alert_text = (self._alert_text + data)[:400]

    def handle_endtag(self, tag):
        if tag == "h1":
            self._title = False
        if tag == "span":
            self._control_label = False
        if tag == "li" and self._control is not None:
            label = self._control["label"].strip().lower()
            if label in ("gamepad", "keyboard", "touch"):
                self.controls[label] = self._control["supported"]
            self._control = None
        if tag == "div" and self._alert_div_depth:
            self._alert_div_depth -= 1
            if not self._alert_div_depth:
                text = " ".join(self._alert_text.split())
                if text:
                    self.alerts.append({
                        "type": "requires_disc_image" if self._alert_disc else "notice",
                        "text": text,
                    })


def parse_official_detail(raw: bytes | str, game_id: str) -> dict:
    """Extract the author's explicit destination from an original .com detail.

    The URL is a *candidate*, not permission to launch it from PSP Dock or a
    claim of gamepad compatibility. A separate, nonprivileged browser probe
    and per-title review are still necessary.
    """
    if not isinstance(game_id, str) or not PORT_ID.fullmatch(game_id):
        raise ValueError("Invalid original port identifier.")
    if isinstance(raw, bytes):
        if len(raw) > MAX_HTML_BYTES:
            raise ValueError("Oversized port detail response.")
        raw = raw.decode("utf-8-sig")
    if not isinstance(raw, str) or len(raw) > MAX_HTML_BYTES:
        raise ValueError("Invalid port detail response.")
    if ("data-page" not in raw or
            "class=" not in raw):
        raise ValueError("Not a recognized original port detail page.")
    parser = _Detail(game_id)
    parser.feed(raw)
    parser.close()
    if not parser.found_page_identity or not parser.title.strip():
        raise ValueError("Port detail did not match its ID or title.")
    disc = any(row["type"] == "requires_disc_image" for row in parser.alerts)
    status = ("direct_candidate" if len(parser.play_links) == 1 else
              "multiple_candidates" if parser.play_links else "no_direct_link")
    return {
        "id": game_id,
        "title": parser.title.strip(),
        "detail_url": f"{SOURCE}ports/{game_id}",
        "direct_url": parser.play_links[0] if len(parser.play_links) == 1 else None,
        "direct_candidates": parser.play_links,
        "controls": parser.controls,
        "requires_disc_image": disc,
        "alerts": parser.alerts,
        "tags": list(dict.fromkeys(parser.tags)),
        "status": status,
        "runtime_verified": False,
        "gamepad_tested": False,
        "original_platform": None,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Import user-exported HTML from original Ported2Browser.com")
    parser.add_argument("--html", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    result = parse_official_homepage(args.html.read_bytes())
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"Official directory: {result['extracted']} of "
          f"{result['reported_total']} listings. All runtime URLs pending.")


if __name__ == "__main__":
    main()
