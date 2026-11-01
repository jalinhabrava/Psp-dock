"""Evidence-driven DZIP compatibility proof, independent of game titles.

A shared DZIP header does not certify that a single decompressor works.
This module separates read-only format probing from exact decoder proofs,
which require independent decompressed-reference bytes from TWO games.
No model calls, file writes, PSP device access, runtime code execution,
or installation of unverified textures.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
import argparse
import hashlib
import json
from pathlib import Path
import re
import struct
from typing import Callable

from psp_dariusburst_re import _iso_entries
from psp_game_metadata import read_metadata
from psp_hd_textures import safe_pc_path
from psp_texture_engine import MAX_INPUT, MAX_OUTPUT, identify

DISC = re.compile(r"[A-Z]{4}\d{5}\Z")
MAX_CASE_BYTES = 8 * 1024 * 1024
MAX_SCAN_FILES = 5000


class DzipProofError(ValueError):
    pass


@dataclass(frozen=True)
class DzipEnvelope:
    declared_output_size: int
    size_bytes: int
    unverified_prefix: str


def parse_envelope(raw: bytes) -> DzipEnvelope:
    if not isinstance(raw, bytes) or not 9 <= len(raw) <= MAX_INPUT:
        raise DzipProofError("No hay un contenedor DZIP completo válido.")
    detected = identify(raw)
    if detected.family != "dzip_unknown" or detected.status != "recognized_unsupported":
        raise DzipProofError("La firma no corresponde al DZIP analizado.")
    if type(detected.declared_size) is not int or not 1 <= detected.declared_size <= MAX_OUTPUT:
        raise DzipProofError("Tamaño de salida no verificado o peligroso.")
    return DzipEnvelope(detected.declared_size, len(raw), detected.variant_prefix)


def probe_game_iso(iso: Path) -> dict:
    """Scan a locally held ISO/CSO without copying or decoding its game assets."""
    source = safe_pc_path(iso)
    if not source.is_file() or source.suffix.lower() not in (".iso", ".cso"):
        raise DzipProofError("La fuente debe ser una ISO/CSO local.")
    metadata = read_metadata(source)
    disc = metadata.get("DISC_ID")
    if not isinstance(disc, str) or not DISC.fullmatch(disc):
        raise DzipProofError("Falta el DISC_ID verificable de la copia local.")
    report = {
        "schema": 1,
        "disc_id": disc,
        "disc_version": metadata.get("DISC_VERSION"),
        "mode": "header_probe_only",
        "container_count": 0,
        "files_visited": 0,
        "extensions": {},
        "prefix_fingerprints": {},
        "errors": 0,
        "decoder_verified": False,
        "cross_game_verified": False,
        "source_size": source.stat().st_size,
        "source_sha256": "",
        "cases": [],
    }
    digest = hashlib.sha256()
    with source.open("rb") as data:
        for block in iter(lambda: data.read(1024 * 1024), b""):
            digest.update(block)
    report["source_sha256"] = digest.hexdigest()
    ext_counts = Counter()
    prefix_counts = Counter()
    for reader, relative, offset, size in _iso_entries(source):
        report["files_visited"] += 1
        if report["files_visited"] > MAX_SCAN_FILES:
            raise DzipProofError("Demasiados ficheros para un escaneo acotado.")
        if not 9 <= size <= MAX_INPUT:
            continue
        reader.seek(offset)
        header = reader.read(min(size, 12))
        if header[:4] != b"DZIP":
            continue
        claimed = struct.unpack_from("<I", header, 4)[0]
        if not 1 <= claimed <= MAX_OUTPUT:
            report["errors"] += 1
            continue
        extension = Path(relative).suffix.lower()
        prefix = header[8:12].hex()
        ext_counts[extension] += 1
        prefix_counts[prefix] += 1
        report["cases"].append({
            "path": relative,
            "offset": offset,
            "compressed_bytes": size,
            "declared_output_size": claimed,
            "unverified_prefix": prefix,
            "decoded": False,
        })
        report["container_count"] += 1
    report["extensions"] = dict(sorted(ext_counts.items()))
    report["prefix_fingerprints"] = dict(sorted(prefix_counts.items()))
    return report


def compare_headers(left: dict, right: dict) -> dict:
    """Matching DZIP envelopes never imply cross-game algorithm compatibility."""
    if (not isinstance(left, dict) or not isinstance(right, dict) or
            not all(isinstance(r.get("disc_id"), str) and
                    DISC.fullmatch(r["disc_id"]) for r in (left, right)) or
            left["disc_id"] == right["disc_id"] or
            not all(r.get("container_count", 0) > 0 for r in (left, right))):
        raise DzipProofError("Se necesitan dos juegos distintos con recursos DZIP.")
    common = sorted(set(left.get("prefix_fingerprints", {})) &
                    set(right.get("prefix_fingerprints", {})))
    return {
        "games": sorted((left["disc_id"], right["disc_id"])),
        "same_magic": True,
        "matching_unverified_prefixes": common,
        "decoder_verified": False,
        "cross_game_verified": False,
        "reason": "La cabecera y los prefijos no prueban el bitstream ni los bytes decodificados.",
    }


@dataclass(frozen=True)
class ReferenceCase:
    """Provenance must be documented by the investigator, never guessed from a hash."""
    disc_id: str
    resource_name: str
    packed: bytes
    reference_decoded: bytes
    reference_kind: str


@dataclass(frozen=True)
class DecodedStream:
    decoded: bytes
    consumed_bytes: int


def verify_cross_game_decoder(
    cases: tuple[ReferenceCase, ...],
    decode_payload: Callable[[bytes, int], DecodedStream],
) -> dict:
    """Strict exact proof against 2+ independent assets per 2+ distinct games.

    Runtime reference bytes must be independently acquired (e.g. from the
    game's own decompressor via an isolated emulator); this API cannot prove
    their provenance independently. Reject partial consumption, extra data,
    wrong length, multiple copies of one fixture and non-bytes output.
    """
    if not isinstance(cases, tuple) or not 4 <= len(cases) <= 64:
        raise DzipProofError("Se requieren al menos cuatro muestras completas.")
    if not callable(decode_payload):
        raise DzipProofError("Falta un decodificador determinista.")
    by_game = Counter()
    independent_input = set()
    independent_output = set()
    evidence = []
    for case in cases:
        if (not isinstance(case, ReferenceCase) or
                not isinstance(case.disc_id, str) or
                not DISC.fullmatch(case.disc_id) or
                not isinstance(case.resource_name, str) or
                not 1 <= len(case.resource_name) <= 220 or
                not isinstance(case.reference_kind, str) or
                case.reference_kind not in ("emulator_runtime", "independent_decoder") or
                not isinstance(case.packed, bytes) or
                not isinstance(case.reference_decoded, bytes) or
                not 1 <= len(case.reference_decoded) <= MAX_CASE_BYTES or
                len(case.packed) > MAX_CASE_BYTES):
            raise DzipProofError("Referencia de compresión incompleta o inválida.")
        decoded_header = parse_envelope(case.packed)
        if decoded_header.declared_output_size != len(case.reference_decoded):
            raise DzipProofError("Longitud esperada incompatible con la cabecera.")
        compressed_hash = hashlib.sha256(case.packed).hexdigest()
        ref_hash = hashlib.sha256(case.reference_decoded).hexdigest()
        if (compressed_hash in independent_input or
                (case.disc_id, case.resource_name) in
                {(s["disc_id"], s["resource"]) for s in evidence}):
            raise DzipProofError("Muestra repetida: no constituye evidencia independiente.")
        result = decode_payload(case.packed[8:], len(case.reference_decoded))
        if (not isinstance(result, DecodedStream) or
                not isinstance(result.decoded, bytes) or
                type(result.consumed_bytes) is not int or
                result.consumed_bytes != len(case.packed) - 8 or
                result.decoded != case.reference_decoded):
            raise DzipProofError("El decodificador no reproduce exactamente la referencia.")
        independent_input.add(compressed_hash)
        independent_output.add(ref_hash)
        by_game[case.disc_id] += 1
        evidence.append({
            "disc_id": case.disc_id, "resource": case.resource_name,
            "packed_sha256": compressed_hash,
            "reference_sha256": ref_hash,
            "output_bytes": len(case.reference_decoded),
            "consumed_payload_bytes": result.consumed_bytes,
            "reference_kind": case.reference_kind,
            "exact": True,
        })
    if len(by_game) < 2 or min(by_game.values()) < 2 or len(independent_output) < 2:
        raise DzipProofError("No hay al menos dos juegos con dos muestras distintas cada uno.")
    return {
        "schema": 1, "mode": "exact_reference_compatibility",
        "cross_game_verified_for_fixtures": True,
        "games": sorted(by_game),
        "samples_per_game": dict(sorted(by_game.items())),
        "reference_provenance_independently_audited": False,
        "proof": evidence,
        # A limited fixture proof is NOT universal format compatibility.
        "universal_game_compatibility": False,
        "automatic_texture_install_authorized": False,
    }


def main(argv: list[str] | None = None) -> int:
    """Run a metadata-only read-only probe of any locally verified PSP game."""
    parser = argparse.ArgumentParser(
        description="Explorar cabeceras DZIP de ISO/CSO sin descifrar ni editar juegos."
    )
    parser.add_argument("iso", type=Path, help="Ruta local a ISO/CSO de PSP")
    args = parser.parse_args(argv)
    result = probe_game_iso(args.iso)
    summary = {k: result[k] for k in (
        "schema", "disc_id", "disc_version", "mode", "source_size",
        "source_sha256", "files_visited", "container_count",
        "prefix_fingerprints", "extensions", "errors",
        "decoder_verified", "cross_game_verified",
    )}
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
