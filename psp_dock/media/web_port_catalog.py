"""Offline Ported2Browser discovery with separately curated launch information.

The source lists projects, not verified playable runtimes. Imported rows never
contain url and always have launch_state="catalog_only". Only the curated
catalogue can supply launch URLs. Parsing and merging are pure; network access
occurs only through an explicitly requested refresh.
"""
from __future__ import annotations

from copy import deepcopy
from html.parser import HTMLParser
import ipaddress
import json
import math
import os
from pathlib import Path
import re
import tempfile
import time
from urllib.error import URLError
from urllib.parse import unquote, urljoin, urlsplit, urlunsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

SOURCE_URL = "https://ported2browser.sbs/games/"
MAX_HTML_BYTES = 8 * 1024 * 1024
MAX_DETAIL_BYTES = 2 * 1024 * 1024
MAX_CARDS = 10_000
MIN_CATALOG_ENTRIES = 500
MAX_URL_LENGTH = 2048
DEFAULT_TIMEOUT = 15.0
_CATALOG_HOSTS = frozenset({"ported2browser.sbs", "www.ported2browser.sbs"})
_DIRECTORY_DOMAINS = ("ported2browser.sbs", "ported2browser.org", "ported2browser.com")
_ID = re.compile(r"[a-z0-9][a-z0-9_-]{0,159}\Z")
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")
_HIDDEN = frozenset({"script", "style", "template", "noscript"})
_FILE_VALUES = {"required": True, "true": True, "included": False,
                "not-requested": False, "false": False}


class CatalogError(ValueError):
    """Invalid, incomplete or oversized catalogue data; keep the last good cache."""


def _text(value, limit=256):
    if not isinstance(value, str):
        return ""
    return " ".join(_CONTROL.sub(" ", value[:limit * 4]).split())[:limit]


def _identifier(value):
    return value if isinstance(value, str) and _ID.fullmatch(value) else None


def _is_directory(host):
    return any(host == base or host.endswith("." + base)
               for base in _DIRECTORY_DOMAINS)


def _https_url(value):
    """Syntactic validation only: this does not resolve or authorize a DNS host."""
    if (not isinstance(value, str) or not value or len(value) > MAX_URL_LENGTH
            or value != value.strip() or "\\" in value
            or _CONTROL.search(value) or _CONTROL.search(unquote(value))):
        return None
    try:
        parsed = urlsplit(value)
        if (parsed.scheme.lower() != "https" or not parsed.hostname
                or parsed.username is not None or parsed.password is not None
                or parsed.port not in (None, 443)):
            return None
        host = parsed.hostname.rstrip(".").encode("idna").decode("ascii").lower()
    except (ValueError, UnicodeError):
        return None
    if not host or "%" in host or len(host) > 253:
        return None
    try:
        address = ipaddress.ip_address(host)
        if not address.is_global or (address.version == 6 and address.ipv4_mapped
                                     and not address.ipv4_mapped.is_global):
            return None
    except ValueError:
        labels = host.split(".")
        if (len(labels) < 2
                or labels[-1] in {"local", "localhost", "internal", "lan", "home",
                                 "invalid", "test", "example", "onion"}
                or re.fullmatch(r"(?:[0-9]+|0x[0-9a-f]+)", labels[-1])
                or any(not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?",
                                        label) for label in labels)):
            return None
    netloc = "[" + host + "]" if ":" in host else host
    return urlunsplit(("https", netloc, parsed.path or "/", parsed.query, parsed.fragment))


def validated_external_url(value):
    """Return a public-looking HTTPS candidate, excluding the directory itself.

    DNS is deliberately not consulted by pure parsing. A caller fetching an
    arbitrary candidate must perform its own DNS/redirect checks.
    """
    result = _https_url(value)
    if result and not _is_directory(urlsplit(result).hostname):
        return result
    return None


def _catalog_source(value):
    result = _https_url(value)
    if result:
        parsed = urlsplit(result)
        if (parsed.hostname in _CATALOG_HOSTS and parsed.path == "/games/"
                and not parsed.query and not parsed.fragment):
            return result
    raise CatalogError("Catalogue source must be https://ported2browser.sbs/games/")


def _source_slug(value):
    result = _https_url(value)
    if not result:
        return None
    parsed = urlsplit(result)
    if not _is_directory(parsed.hostname):
        return None
    parts = parsed.path.strip("/").split("/")
    return _identifier(parts[1]) if len(parts) == 2 and parts[0] == "games" else None


def _joined_https(base, value):
    # urljoin strips some control characters, so validate the raw href first.
    if (not isinstance(value, str) or not value or len(value) > MAX_URL_LENGTH
            or value != value.strip() or "\\" in value
            or _CONTROL.search(value) or _CONTROL.search(unquote(value))):
        return None
    try:
        return _https_url(urljoin(base, value))
    except ValueError:
        return None


def _source_page(value, source_url, identifier):
    result = _joined_https(source_url, value)
    if (result and urlsplit(result).hostname in _CATALOG_HOSTS
            and _source_slug(result) == identifier):
        parsed = urlsplit(result)
        return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", ""))
    return None


def _html_text(raw, max_bytes):
    if isinstance(raw, str):
        if len(raw) > max_bytes or len(raw.encode("utf-8")) > max_bytes:
            raise CatalogError("HTML exceeds the byte limit")
        return raw.lstrip("\ufeff")
    if not isinstance(raw, bytes) or len(raw) > max_bytes:
        raise CatalogError("HTML must be bounded UTF-8 bytes or text")
    try:
        return raw.decode("utf-8-sig")
    except UnicodeDecodeError as error:
        raise CatalogError("HTML is not valid UTF-8") from error


def _controls(value):
    tokens = re.split(r"[\s,/+_-]+", value.lower())
    return [control for control in ("gamepad", "keyboard", "mouse", "touch")
            if control in tokens or (control == "mouse" and "pointer" in tokens)]


class _CardParser(HTMLParser):
    def __init__(self, source_url, max_cards):
        super().__init__(convert_charrefs=True)
        self.source_url = source_url
        self.max_cards = max_cards
        self.seen_cards = 0
        self.games = {}
        self.card = None
        self.article_depth = 0
        self.hidden = None
        self.hidden_depth = 0
        self.in_title = False
        self.title = ""
        self.fallback_title = ""

    def handle_starttag(self, tag, attrs):
        if self.hidden:
            if tag == self.hidden:
                self.hidden_depth += 1
            return
        if tag in _HIDDEN:
            self.hidden = tag
            self.hidden_depth = 1
            return
        fields = dict(attrs)
        if tag == "article" and "game-card" in (fields.get("class") or "").split():
            self.seen_cards += 1
            if self.seen_cards > self.max_cards:
                raise CatalogError("Catalogue exceeds the card limit")
            # A new card discards an unclosed predecessor instead of inheriting it.
            self.card = fields
            self.article_depth = 1
            self.hidden = None
            self.in_title = False
            self.title = ""
            self.fallback_title = _text(fields.get("data-title"))
            return
        if self.card is None:
            return
        if tag == "article":
            self.article_depth += 1
        elif tag == "h3":
            self.in_title = True
        elif tag == "button" and not self.fallback_title:
            self.fallback_title = _text(fields.get("data-save-name"))
        elif tag == "a":
            page = _source_page(fields.get("href"), self.source_url,
                                self.card.get("data-game"))
            if page and "_page" not in self.card:
                self.card["_page"] = page
        if fields.get("data-platform") and not self.card.get("data-platform"):
            self.card["data-platform"] = fields["data-platform"]

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        self.handle_endtag(tag)

    def handle_data(self, data):
        if self.card is not None and self.in_title and not self.hidden:
            self.title += data[:max(0, 1024 - len(self.title))]

    def handle_endtag(self, tag):
        if self.hidden:
            if tag == self.hidden:
                self.hidden_depth -= 1
                if not self.hidden_depth:
                    self.hidden = None
            return
        if self.card is None:
            return
        if tag == "h3":
            self.in_title = False
        if tag != "article":
            return
        self.article_depth -= 1
        if self.article_depth:
            return
        card = self.card
        self.card = None
        identifier = _identifier(card.get("data-game"))
        title = _text(self.title) or self.fallback_title
        if not identifier or not title or identifier in self.games:
            return
        input_kind = _text(card.get("data-input"), 96) or "undocumented"
        files = _text(card.get("data-files"), 48).lower() or "unknown"
        available = _text(card.get("data-available"), 16).lower()
        row = {
            "id": identifier, "title": title,
            "genre": _text(card.get("data-genre"), 96),
            "input": input_kind, "controls": _controls(input_kind),
            "requires_files": _FILE_VALUES.get(files),
            "files_status": files,
            "available": {"true": True, "false": False}.get(available),
            "launch_state": "catalog_only",
        }
        if card.get("_page"):
            row["source_page"] = card["_page"]
        platform = _text(card.get("data-platform"), 64)
        if platform:
            row["platform"] = platform
        self.games[identifier] = row


def _count_limits(min_entries, max_cards=MAX_CARDS):
    if (type(min_entries) is not int or type(max_cards) is not int
            or not 1 <= min_entries <= max_cards <= MAX_CARDS):
        raise CatalogError("Invalid catalogue count limits")


def parse_ported2browser(html, *, source_url=SOURCE_URL,
                        min_entries=MIN_CATALOG_ENTRIES, max_cards=MAX_CARDS):
    """Parse the index with no I/O; reject incomplete/blocked responses by default."""
    _count_limits(min_entries, max_cards)
    source_url = _catalog_source(source_url)
    parser = _CardParser(source_url, max_cards)
    parser.feed(_html_text(html, MAX_HTML_BYTES))
    parser.close()
    games = [parser.games[key] for key in sorted(parser.games)]
    if len(games) < min_entries:
        raise CatalogError(f"Only {len(games)} valid unique games; expected at least {min_entries}")
    return {"version": 1, "source": source_url, "catalog_total": len(games), "games": games}


def _read_bounded(path, max_bytes=MAX_HTML_BYTES):
    with Path(path).open("rb") as handle:
        raw = handle.read(max_bytes + 1)
    if len(raw) > max_bytes:
        raise CatalogError("File exceeds the catalogue byte limit")
    return raw


def parse_catalog_file(path, **options):
    """Import a saved HTML index offline."""
    return parse_ported2browser(_read_bounded(path), **options)


def _rows(document):
    if document is None:
        return []
    rows = document.get("games") if isinstance(document, dict) else document
    if not isinstance(rows, list) or len(rows) > MAX_CARDS:
        raise CatalogError("Catalogue games must be a bounded list")
    return rows


def _discovery_row(row):
    if not isinstance(row, dict):
        return None
    identifier = _identifier(row.get("id"))
    title = _text(row.get("title"))
    if not identifier or not title:
        return None
    input_kind = _text(row.get("input"), 96) or "undocumented"
    files = row.get("requires_files")
    result = {
        "id": identifier, "title": title,
        "genre": _text(row.get("genre"), 96),
        "input": input_kind, "controls": _controls(input_kind),
        "requires_files": files if isinstance(files, bool) else None,
        "files_status": _text(row.get("files_status"), 48) or "unknown",
        "available": row.get("available") if isinstance(row.get("available"), bool) else None,
        "launch_state": "catalog_only",
    }
    page = _source_page(row.get("source_page"), SOURCE_URL, identifier)
    if page:
        result["source_page"] = page
    platform = _text(row.get("platform"), 64)
    if platform:
        result["platform"] = platform
    return result


def build_merged_catalog(curated, snapshot=None):
    """Pure merge: curated order/IDs/fields win; discovery cannot authorize launch.

    Match both IDs and observed directory page slugs. Curated aliases such as
    diabloweb remain stable while consuming the discovered diablo row.
    Inputs and their nested control mappings are never mutated or shared.
    """
    discovered = {}
    for row in _rows(snapshot):
        clean = _discovery_row(row)
        if clean:
            discovered.setdefault(clean["id"], clean)
    games = []
    seen = set()
    for row in _rows(curated):
        if not isinstance(row, dict) or not _identifier(row.get("id")):
            continue
        identifier = row["id"]
        if identifier in seen:
            continue
        source_id = _source_slug(row.get("source_page"))
        match = discovered.pop(identifier, None)
        source_match = discovered.pop(source_id, None) if source_id else None
        match = match or source_match
        merged = deepcopy(match or {})
        # An absent curated launch_state has existing launcher semantics.
        merged.pop("launch_state", None)
        if match and match["id"] != identifier:
            merged["source_id"] = match["id"]
        merged.update(deepcopy(row))
        if row.get("launch_state") == "requires_original_iso":
            merged["requires_files"] = True
        if not merged.get("url") and "launch_state" not in merged:
            merged["launch_state"] = "catalog_only"
        games.append(merged)
        seen.add(identifier)
    games.extend(discovered[key] for key in sorted(discovered) if key not in seen)
    source = snapshot.get("source", SOURCE_URL) if isinstance(snapshot, dict) else SOURCE_URL
    return {"version": 1, "source": source, "catalog_total": len(games), "games": games}


def load_catalog_snapshot(snapshot_path: Path, curated_path: Path):
    """Read the bundled/cache snapshot and curated overrides, never the network.

    A missing or corrupt optional snapshot leaves the curated catalogue usable.
    The returned catalog_error exposes that fallback to the caller.
    """
    curated = json.loads(_read_bounded(curated_path))
    try:
        snapshot = json.loads(_read_bounded(snapshot_path))
        if not isinstance(snapshot, dict) or snapshot.get("version") != 1:
            raise CatalogError("Unsupported catalogue snapshot version")
        snapshot["source"] = _catalog_source(snapshot.get("source"))
        result = build_merged_catalog(curated, snapshot)
        valid_count = len({clean["id"] for row in _rows(snapshot)
                           if (clean := _discovery_row(row))})
        if valid_count < MIN_CATALOG_ENTRIES:
            raise CatalogError("Catalogue snapshot is incomplete")
        return result
    except (OSError, ValueError, TypeError, RecursionError) as error:
        result = build_merged_catalog(curated)
        result["catalog_error"] = _text(str(error))
        return result


def serialize_catalog(snapshot):
    """Deterministic UTF-8 JSON with one compact game per line, no clock fields."""
    source = _catalog_source(snapshot.get("source"))
    games = sorted(_rows(snapshot), key=lambda row: row["id"])
    dump = lambda value: json.dumps(value, ensure_ascii=False, sort_keys=True,
                                    separators=(",", ":"), allow_nan=False)
    text = ('{\n  "version":1,\n  "source":' + dump(source)
            + ',\n  "catalog_total":' + str(len(games)) + ',\n  "games":[\n'
            + ",\n".join("    " + dump(game) for game in games) + "\n  ]\n}\n")
    return text.encode("utf-8")


def write_catalog_snapshot(path, snapshot):
    """Publish a fully parsed snapshot atomically beside the target."""
    raw = serialize_catalog(snapshot)
    if len(raw) > MAX_HTML_BYTES:
        raise CatalogError("Snapshot exceeds the catalogue byte limit")
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="wb", dir=path.parent,
                                         prefix=path.name + ".", suffix=".tmp",
                                         delete=False) as handle:
            temporary = Path(handle.name)
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return snapshot


class _CatalogueRedirect(HTTPRedirectHandler):
    max_redirections = 3
    max_repeats = 1

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # Check before urllib opens the redirect target.
        safe = _catalog_source(newurl)
        return super().redirect_request(req, fp, code, msg, headers, safe)


def _open_catalog(request, *, timeout):
    return build_opener(_CatalogueRedirect()).open(request, timeout=timeout)


def refresh_catalog(cache_path, *, source_url=SOURCE_URL, timeout=DEFAULT_TIMEOUT,
                    max_bytes=MAX_HTML_BYTES, min_entries=MIN_CATALOG_ENTRIES,
                    opener=None):
    """Explicit metadata-only refresh; failures never overwrite the good cache.

    Only the known HTTPS index (including each redirect) may be requested.
    opener is a dependency injection point for offline tests, not a URL source.
    Both declared and actual response size are capped. Socket timeout and an
    elapsed-time check bound reads; compressed responses are rejected.
    """
    source_url = _catalog_source(source_url)
    _count_limits(min_entries)
    if (isinstance(timeout, bool) or not isinstance(timeout, (int, float))
            or not math.isfinite(timeout) or not 0 < timeout <= 60):
        raise CatalogError("Timeout must be greater than zero and at most 60 seconds")
    if type(max_bytes) is not int or not 1 <= max_bytes <= MAX_HTML_BYTES:
        raise CatalogError("Invalid HTML byte limit")
    request = Request(source_url, headers={
        "User-Agent": "PSP-Dock-Catalog/1",
        "Accept": "text/html,application/xhtml+xml",
        "Accept-Encoding": "identity",
    })
    started = time.monotonic()
    try:
        with (opener or _open_catalog)(request, timeout=timeout) as response:
            _catalog_source(response.geturl())
            if getattr(response, "status", 200) != 200:
                raise CatalogError("Catalogue response was not HTTP 200")
            content_type = response.headers.get("Content-Type", "").split(";")[0].strip().lower()
            if content_type and content_type not in {"text/html", "application/xhtml+xml"}:
                raise CatalogError("Catalogue response is not HTML")
            encoding = response.headers.get("Content-Encoding", "identity").lower()
            if encoding not in {"", "identity"}:
                raise CatalogError("Compressed catalogue responses are not accepted")
            length = response.headers.get("Content-Length")
            if length is not None:
                try:
                    declared = int(length)
                except ValueError as error:
                    raise CatalogError("Invalid catalogue Content-Length") from error
                if not 0 <= declared <= max_bytes:
                    raise CatalogError("Catalogue response exceeds the byte limit")
            chunks = []
            received = 0
            reader = getattr(response, "read1", response.read)
            while True:
                if time.monotonic() - started >= timeout:
                    raise CatalogError("Catalogue refresh timed out")
                chunk = reader(min(64 * 1024, max_bytes - received + 1))
                if not chunk:
                    break
                received += len(chunk)
                if received > max_bytes:
                    raise CatalogError("Catalogue response exceeds the byte limit")
                chunks.append(chunk)
        snapshot = parse_ported2browser(b"".join(chunks), source_url=source_url,
                                       min_entries=min_entries)
        return write_catalog_snapshot(cache_path, snapshot)
    except (OSError, URLError) as error:
        raise CatalogError(f"Catalogue refresh failed: {error}") from error


def refresh_catalogue(url, cache_path, **options):
    """British-spelling adapter; prefer refresh_catalog(cache_path, source_url=...)."""
    return refresh_catalog(cache_path, source_url=url, **options)


class _DetailParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.links = []
        self.anchor = None
        self.hidden = None
        self.hidden_depth = 0
        self.count = 0

    def handle_starttag(self, tag, attrs):
        if self.hidden:
            if tag == self.hidden:
                self.hidden_depth += 1
            return
        if tag in _HIDDEN:
            self.hidden = tag
            self.hidden_depth = 1
            return
        if tag == "a":
            self.count += 1
            if self.count > 1024:
                raise CatalogError("Detail page exceeds the link limit")
            fields = dict(attrs)
            self.anchor = {"href": fields.get("href"),
                           "class": _text(fields.get("class"), 256), "text": ""}

    def handle_data(self, data):
        if self.anchor is not None and not self.hidden:
            self.anchor["text"] += data[:max(0, 512 - len(self.anchor["text"]))]

    def handle_endtag(self, tag):
        if self.hidden:
            if tag == self.hidden:
                self.hidden_depth -= 1
                if not self.hidden_depth:
                    self.hidden = None
            return
        if tag == "a" and self.anchor is not None:
            self.links.append(self.anchor)
            self.anchor = None


def parse_game_detail(html_bytes, source_slug: str):
    """Extract a documented original-link candidate; never declare it playable.

    Explicit original-game links take precedence over an "Open game host" link
    or an external-game-preview anchor. Directory, local and unsafe URL syntax
    is discarded. No resources referenced in the page are fetched.
    """
    if not _identifier(source_slug):
        raise CatalogError("Invalid source game slug")
    parser = _DetailParser()
    parser.feed(_html_text(html_bytes, MAX_DETAIL_BYTES))
    parser.close()
    source_page = SOURCE_URL + source_slug + "/"
    candidates = []
    for link in parser.links:
        href = link["href"]
        if not isinstance(href, str) or len(href) > MAX_URL_LENGTH:
            continue
        url = validated_external_url(_joined_https(source_page, href))
        if not url:
            continue
        label = _text(link["text"], 256).lower()
        if re.search(r"\bopen\s+(?:the\s+)?(?:original\s+game|game\s+directly)\b", label):
            candidates.append((0, url, "explicit_original"))
        elif re.search(r"\bopen\s+(?:the\s+)?game\s+host\b", label):
            candidates.append((1, url, "explicit_host"))
        elif "external-game-preview" in link["class"].split():
            candidates.append((2, url, "external_preview"))
    if not candidates:
        return None
    _, url, confidence = min(candidates, key=lambda candidate: candidate[0])
    return {"url": url, "confidence": confidence,
            "launch_state": "unverified", "source_page": source_page}
