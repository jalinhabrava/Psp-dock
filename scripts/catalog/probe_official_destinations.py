"""Bounded public HTML observations. Never cookies, game assets or gameplay claims."""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
import html
import ipaddress
import json
from pathlib import Path
import re
import socket
import sys
import urllib.error
import urllib.request
from urllib.parse import urlsplit, urlunsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from psp_dock.media.official_port_inventory import metadata_game_url


def public_destination(url):
    from urllib.parse import urlsplit
    if not metadata_game_url(url):
        raise ValueError("Not a public HTTP(S) destination")
    host = urlsplit(url).hostname
    addresses = socket.getaddrinfo(host, 80 if urlsplit(url).scheme == 'http' else 443,
                                   type=socket.SOCK_STREAM)
    if not addresses or any(not ipaddress.ip_address(a[4][0]).is_global
                            for a in addresses):
        raise ValueError("Destination does not resolve publicly")
    return url


class PublicRedirects(urllib.request.HTTPRedirectHandler):
    max_redirections = 5

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        public_destination(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def probe(url):
    result = {"url": url, "checked_at": datetime.now(timezone.utc).isoformat(),
              "link_status": "pending", "runtime_verified": False,
              "gamepad_tested": False, "method": "public_html_get_no_cookies"}
    try:
        public_destination(url)
        opener = urllib.request.build_opener(PublicRedirects())
        request = urllib.request.Request(url, headers={
            "User-Agent": "PSP-Dock catalog link audit", "Accept": "text/html"})
        with opener.open(request, timeout=12) as response:
            result["http_status"] = response.status
            final = urlsplit(response.url)
            # Redirect query parameters may be generated session identifiers.
            result["final_url"] = urlunsplit((final.scheme, final.netloc, final.path, '', ''))
            result["x_frame_options"] = response.headers.get("X-Frame-Options")
            policy = response.headers.get("Content-Security-Policy", "")
            ancestors = re.search(r"(?:^|;)\s*frame-ancestors\s+([^;]+)", policy, re.I)
            result["frame_ancestors"] = ancestors[1].strip() if ancestors else None
            result["cross_origin_opener_policy"] = response.headers.get("Cross-Origin-Opener-Policy")
            result["cross_origin_embedder_policy"] = response.headers.get("Cross-Origin-Embedder-Policy")
            content_type = response.headers.get("Content-Type", "")
            result["content_type"] = content_type
            if "html" not in content_type.lower():
                result["observation"] = "non_html_not_downloaded"
                return result
            raw = response.read(512 * 1024 + 1)
            result["html_truncated"] = len(raw) > 512 * 1024
            page = raw[:512 * 1024].decode("utf-8", errors="replace")
            title = re.search(r"<title[^>]*>(.*?)</title>", page, re.I | re.S)
            result["title"] = html.unescape(re.sub("<[^>]+>", "", title[1])).strip()[:160] if title else ""
            challenged = bool(re.search(r"Just a moment|Verify you are human|"
                                         r"Attention Required!.*Cloudflare", result["title"], re.I))
            result["link_status"] = "pending" if challenged else "active"
            result["observation"] = "access_challenge" if challenged else "html_received_only"
            result["signals"] = {
                "canvas_markup": bool(re.search(r"<canvas\b", page, re.I)),
                "runtime_reference": bool(re.search(r"\.wasm\b|WebAssembly|createUnityInstance|Emscripten", page, re.I)),
                "file_input": bool(re.search(r"<input[^>]+type\s*=\s*['\"]file", page, re.I)),
                "password_input": bool(re.search(r"<input[^>]+type\s*=\s*['\"]password", page, re.I)),
                "start_text": bool(re.search(r">\s*(?:Play|Start|Launch)(?: game)?\s*<", page, re.I)),
            }
    except urllib.error.HTTPError as exc:
        result["http_status"] = exc.code
        result["observation"] = "http_error"
        # Access restrictions/rate limits are not proof that the game is dead.
        result["link_status"] = "broken" if exc.code in (404, 410) else "pending"
        exc.close()
    except Exception as exc:
        result["observation"] = "request_failed"
        result["error"] = type(exc).__name__ + ": " + str(exc)[:160]
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--capture", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output = args.output.resolve()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    capture = json.loads(args.capture.read_text(encoding="utf-8"))
    if capture.get("source") != "https://ported2browser.com/":
        raise ValueError("Wrong source")
    urls = list(dict.fromkeys(url for game in capture["games"]
                             for url in (game.get("direct_candidates", []) +
                                         [link.get('href') for link in game.get('raw_play_links', [])
                                          if link.get('id') == game['id'] and
                                          link.get('page') == '/ports/' + game['id']])
                             if metadata_game_url(url)))
    results = json.loads(args.output.read_text(encoding="utf-8")) if args.output.exists() else {}
    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = {pool.submit(probe, url): url for url in urls if url not in results}
        for future in as_completed(futures):
            url = futures[future]
            results[url] = future.result()
            temporary = args.output.with_suffix(".partial")
            temporary.write_text(json.dumps(results, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            temporary.replace(args.output)
            print(f"Destinations observed: {len(results)}/{len(urls)}", flush=True)


if __name__ == "__main__":
    main()
