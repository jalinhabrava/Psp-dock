"""PC-only durable and privacy-bounded event journal for PSP Dock.

Physical USB roles, FAT locks and firmware ACKs must NEVER be inferred
from these records. The journal logs what Windows Dock actually observed
or attempted, not a synthetic state-machine event represented as success.

No tokens, PDK1 sessions, save content or arbitrarily supplied file paths.
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import threading


SAFE_FIELDS = frozenset({
    "phase", "reason", "game_id", "count", "conflicts", "actions",
    "to_pc", "to_psp", "volume_serial", "result", "attempted", "mode",
    "manual", "quality", "aspect", "render_scale", "backend",
})


class EventJournal:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.lock = threading.Lock()
        self.write_errors = 0

    def record(self, event: str, *, evidence: str = "pc_observed", **fields) -> bool:
        """Append and fsync a bounded event. Never fail after a committed copy."""
        if (not isinstance(event, str) or len(event) > 64 or
                any(not (char.islower() or char.isdigit() or char == '_')
                    for char in event)):
            raise ValueError("Tipo de evento PSP Dock inválido.")
        if evidence not in ("pc_observed", "pc_action", "synthetic"):
            raise ValueError("Fuente de evidencia desconocida.")
        filtered = {}
        for name, value in fields.items():
            if name not in SAFE_FIELDS:
                continue
            if isinstance(value, bool):
                filtered[name] = value
            elif isinstance(value, int) and 0 <= value <= 2**63 - 1:
                filtered[name] = value
            elif isinstance(value, str):
                filtered[name] = value[:140]
        entry = {
            "at_utc": datetime.now(timezone.utc).isoformat(),
            "event": event, "evidence": evidence,
            **filtered,
            "physical_role_bridge_verified": False,
            "firmware_ack_verified": False,
        }
        # One compact JSON line per event; no multi-megabyte payloads.
        data = (json.dumps(entry, ensure_ascii=False, separators=(',',':'))+'\n').encode('utf-8')
        try:
            with self.lock:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                with self.path.open('ab') as output:
                    output.write(data)
                    output.flush()
                    os.fsync(output.fileno())
            return True
        except OSError:
            # The copy may already have COMMITTED; reporting failure here
            # would invite blind retries. Journal copy_slot itself stays
            # authoritative for interrupted writes.
            self.write_errors += 1
            return False
