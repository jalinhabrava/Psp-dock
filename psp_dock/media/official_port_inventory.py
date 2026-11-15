"""Public directory evidence, kept separate from the approved game launcher.

An HTTP response, a directory gamepad badge and a rendered runtime are three
different observations. None of them alone proves controller gameplay.
"""
from __future__ import annotations

from collections import Counter
import json
from pathlib import Path
import re
from urllib.parse import urlsplit, urlunsplit

from .official_ported2browser import PORT_ID, SOURCE, _public_game_url

NON_PC = {"Android / iOS", "Java", "Mobile", "Tabletop"}
WEB = {"Browser", "Web browser"}
BADGES = (("emulator-tag", "emulator"), ("flash-tag", "flash"),
          ("repository-tag", "repository"), ("fangame-tag", "fangame"),
          ("compendium-tag", "compendium"), ("wip-tag", "wip"),
          ("romhack-tag", "romhack"), ("ai-tag", "ai"),
          ("mashup-tag", "mashup"), ("software-tag", "software"),
          ("boardgame-tag", "tabletop"))


def metadata_game_url(value):
    """HTTP links may be inventoried, without authorizing the HTTPS launcher."""
    if not isinstance(value, str):
        return None
    try:
        url = urlsplit(value)
        if url.scheme not in ('http', 'https') or url.port not in (None, 80 if url.scheme == 'http' else 443):
            return None
        if re.search(r'(?:^|&)(?:[^=&]*(?:token|secret|session|auth|password|api.?key)[^=&]*)=', url.query, re.I):
            return None
        secure = urlunsplit(('https', url.netloc, url.path, url.query, url.fragment))
        return value if _public_game_url(secure) else None
    except ValueError:
        return None


def category_for(row):
    flags = set(row.get("flags", []))
    for flag, category in BADGES:
        if flag in flags:
            return category
    platforms = set(row.get("original_platforms", []))
    if platforms & WEB:
        return "web_native"
    if platforms and platforms <= NON_PC:
        return "mobile_or_other"
    if platforms - NON_PC - WEB:
        return "port_candidate"
    return "unclassified"


def build_inventory(capture, probes=None, file_requirements=None, runtime_observations=None):
    if (capture.get("source") != SOURCE or
            capture.get("kind") != "existing_chrome_public_dom" or
            not isinstance(capture.get("games"), list)):
        raise ValueError("Expected an original-directory Chrome capture.")
    probes = probes or {}
    file_requirements = file_requirements or {}
    runtime_observations = runtime_observations or {}
    games, seen = [], set()
    for row in capture["games"]:
        game_id = row.get("id", "")
        if (not PORT_ID.fullmatch(game_id) or game_id in seen or
                row.get("detail_url") != SOURCE + "ports/" + game_id or
                not isinstance(row.get("title"), str) or
                not 1 <= len(row["title"]) <= 120):
            raise ValueError("Invalid or duplicate directory identity.")
        seen.add(game_id)
        links = list(row.get("direct_candidates", []))
        links.extend(link.get('href') for link in row.get('raw_play_links', [])
                     if link.get('id') == game_id and link.get('page') == '/ports/' + game_id)
        candidates = list(dict.fromkeys(url for url in links if metadata_game_url(url)))
        direct = candidates[0] if len(candidates) == 1 else None
        observations = [probes[url] for url in candidates if url in probes]
        # A source's broken badge remains evidence even if its parked domain
        # happens to return HTTP 200. Blocked/timeouts are pending, not broken.
        broken = row.get("broken") is True or bool(observations) and all(
            item.get("link_status") == "broken" for item in observations
        ) and len(observations) == len(candidates)
        link_status = ("broken" if broken else "active" if any(
            item.get("link_status") == "active" for item in observations
        ) else "pending")
        disc = row.get("requires_disc_image") is True
        description = row.get("description", "")
        # The whole <main> also includes unrelated Reddit headlines. Only the
        # directory author's About section may establish game requirements.
        if 'About this port' in description:
            description = description.split('About this port', 1)[1].strip()
        # Only explicit source wording is recorded; unknown stays unknown.
        own_files = disc or bool(re.search(
            r"requires? (?:your (?:own |original )?|an? )?(?:ISO|disc image|"
            r"original (?:game )?files)|(?:your own|original) (?:game )?(?:ISO|"
            r"disc image)|(?:upload|select|provide) (?:your |the )?(?:own )?"
            r"(?:DIABDAT\.MPQ|game files)|(?:requires?|need) your own .{0,90}"
            r"(?:ROM|game assets|game data|disc dump)|runs from your own copy "
            r"of the game's data files|does not provide .{0,30}game data",
            description, re.I))
        optional_files = own_files and 'shareware game by default' in description
        if optional_files:
            own_files = False
        file_review = file_requirements.get(game_id)
        if file_review:
            if (file_review.get('detail_url') != row['detail_url'] or
                    file_review.get('direct_url') not in candidates):
                raise ValueError('File requirement evidence does not match the port.')
            own_files = file_review.get('required') is True
            optional_files = file_review.get('optional') is True
            disc = file_review.get('disc_image') is True
        mouse = True if (re.search(r"requires? (?:a )?mouse|mouse (?:is )?required|"
                                   r"mouse control is central|played with (?:a |the )?"
                                   r"(?:keyboard and )?mouse|(?:aim|look|command units) "
                                   r"with the mouse|WASD and mouse", description, re.I)
                         and row.get('controls', {}).get('gamepad') is not True) else None
        l2_r2 = True if re.search(r"(?:requires?|needs?) .{0,25}\bL2\b.{0,25}\bR2\b",
                                  description, re.I) else None
        second_stick = True if re.search(r"requires? (?:a )?(?:second|right) "
                                         r"(?:analog )?(?:stick|joystick)",
                                         description, re.I) else None
        state = ("unavailable" if broken else "requires_original_files" if own_files
                 else "needs_pointer" if mouse else "direct_unverified" if candidates
                 else "pending")
        browser_review = runtime_observations.get(game_id)
        if browser_review and browser_review.get('url') not in candidates:
            raise ValueError('Browser observation does not match the port destination.')
        games.append({
            "id": game_id, "title": row["title"], "author": row.get("author", ""),
            "detail_url": row["detail_url"],
            "original_platforms": row.get("original_platforms", []),
            "category": category_for(row), "flags": row.get("flags", []),
            "direct_url": direct, "direct_candidates": candidates,
            "controls_declared": row.get("controls", {}),
            "requirements": row.get("requirements", []),
            "requires_disc_image": disc, "requires_original_files": own_files,
            "optional_original_files": optional_files,
            "file_requirement_review": file_review,
            "documented_controls": [label for label, pattern in (
                ('L2/R2', r'\bL2\b.{0,35}\bR2\b'),
                ('second_stick', r'\bright stick\b|\bsecond (?:analog )?stick\b'),
                ('mouse', r'\bmouse\b')) if re.search(pattern, description, re.I)],
            "additional_controls": {"l2_r2": l2_r2, "second_stick": second_stick,
                                    "mouse": mouse},
            "link_status": link_status, "verification_state": state,
            "runtime_verified": False, "gamepad_tested": False,
            "runtime_observation": browser_review,
            "runtime_scene_observed": bool(browser_review and browser_review.get('rendered_game_scene')),
            "requirements_conflict": bool(browser_review and browser_review.get('requirements_conflict')),
            "description": description,
            "observed_at": row.get("observed_at"),
            "destination_observations": observations,
        })
    complete = (capture.get("listing_complete") is True and
                len(games) == capture.get("reported_total"))
    summary = {
        "listed": len(games), "reported_total": capture.get("reported_total"),
        "listing_complete": complete,
        "details": sum(bool(g["observed_at"]) for g in games),
        "platforms_complete": capture.get("platforms_complete") is True,
        "direct_url": sum(bool(g["direct_candidates"]) for g in games),
        "single_direct_url": sum(bool(g["direct_url"]) for g in games),
        "requires_disc_image": sum(g["requires_disc_image"] for g in games),
        "requires_original_files": sum(g["requires_original_files"] for g in games),
        "optional_original_files": sum(g["optional_original_files"] for g in games),
        "requires_mouse": sum(g['additional_controls']['mouse'] is True for g in games),
        "documented_l2_r2": sum('L2/R2' in g['documented_controls'] for g in games),
        "documented_second_stick": sum('second_stick' in g['documented_controls'] for g in games),
        "runtime_verified": 0, "gamepad_tested": 0,
        "browser_observed": sum(bool(g['runtime_observation']) for g in games),
        "runtime_scenes_observed": sum(g['runtime_scene_observed'] for g in games),
        "file_requirement_conflicts": sum(g['requirements_conflict'] for g in games),
        "categories": dict(Counter(g["category"] for g in games)),
        "states": dict(Counter(g["verification_state"] for g in games)),
        "links": dict(Counter(g["link_status"] for g in games)),
        "declared_gamepad": sum(g["controls_declared"].get("gamepad") is True
                                for g in games),
    }
    return {"version": 1, "source": SOURCE,
            "captured_at": capture.get("finished_at"), "summary": summary,
            "games": games}


def load_inventory(root: Path):
    """Read only the shipped public inventory; this does not grant launch access."""
    result = json.loads((root / "web/official-ports.json").read_text(encoding="utf-8"))
    if result.get("version") != 1 or result.get("source") != SOURCE:
        raise ValueError("Unrecognized official inventory.")
    return result
