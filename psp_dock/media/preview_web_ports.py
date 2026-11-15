"""Local, opt-in launch manifest. Reading metadata never certifies gameplay."""
from __future__ import annotations

import re
from pathlib import Path

from .official_port_inventory import load_inventory, metadata_game_url
from .official_ported2browser import SOURCE, _public_game_url

PREVIEW_ID = re.compile(r"[0-9A-HJKMNP-TV-Z]{26}\Z")


def build_preview_manifest(inventory):
    if inventory.get('source') != SOURCE or inventory.get('version') != 1:
        raise ValueError('Origen del inventario experimental no válido.')
    games, seen = [], set()
    for game in inventory['games']:
        game_id = game.get('id', '')
        if (not isinstance(game_id, str) or not PREVIEW_ID.fullmatch(game_id) or
                game_id in seen or game.get('detail_url') != SOURCE + 'ports/' + game_id or
                not isinstance(game.get('title'), str) or not 1 <= len(game['title']) <= 120):
            raise ValueError('Identidad del inventario experimental no válida.')
        seen.add(game_id)
        url = game.get('direct_url')
        candidates = game.get('direct_candidates', [])
        reason = ''
        if game.get('link_status') == 'broken' or game.get('verification_state') == 'unavailable':
            reason = 'Enlace marcado como roto o no disponible en el inventario.'
        elif game.get('category') != 'port_candidate':
            reason = 'Esta prueba solo abre candidatos a port; las demás categorías son fichas informativas.'
        elif not isinstance(candidates, list) or len(candidates) != 1 or candidates[0] != url:
            reason = 'El inventario no identifica un único destino directo.'
        elif not _public_game_url(url) or not metadata_game_url(url):
            reason = 'El destino no es una URL HTTPS pública admitida.'
        games.append({**game, 'preview_allowed': not reason,
                      'preview_block_reason': reason, 'url': url if not reason else None,
                      'runtime_verified': False, 'gamepad_tested': False})
    return {'version': 1, 'source': SOURCE, 'demo': True, 'preview_web_ports': True,
            'games': games}


def load_preview_manifest(root: Path):
    return build_preview_manifest(load_inventory(root))
