"""Independent integrity audit and Ed25519 detached signatures for P1-P4 work copies.

This module deliberately has no dependency on a PitGuard application instance,
project database, HTTP authentication, or mutable design state.  The signing key
must remain outside the web service.  A valid signature proves that the archive
has not changed since signing by the holder of an explicitly trusted private
key.  It does NOT certify the original site data, an engineering method, an
engineer's professional authorization, or permission to issue drawings.

Offline use (requires cryptography >= 44)::

    python dossier_signature.py audit P1_P4_review.zip
    python dossier_signature.py keygen --private-key private.pem --public-key public.pem
    python dossier_signature.py sign P1_P4_review.zip --private-key private.pem
    python dossier_signature.py verify P1_P4_review.zip --trusted-public-key public.pem

All security decisions fail closed and none can set formalConstructionIssue true.
"""
from __future__ import annotations

import argparse
import base64
import binascii
from datetime import datetime, timezone
from hashlib import sha256
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import sys
from typing import Any
from zipfile import BadZipFile, ZipFile

SCHEMA = "pitguard.detached-dossier-signature/1"
DOMAIN = b"PitGuard P1-P4 reviewed working dossier Ed25519 signature v1\n"
HEX64 = re.compile(r"[0-9a-f]{64}\Z")
MAX_ARCHIVE_BYTES = 500_000_000
MAX_FILES = 256
MAX_ENTRY_BYTES = 100_000_000
MAX_UNCOMPRESSED_BYTES = 600_000_000
MAX_JSON_BYTES = 5_000_000
MAX_SIGNATURE_BYTES = 20_000
SIGNED_KEYS = (
    "schemaVersion", "algorithm", "keyId", "archiveName", "archiveBytes",
    "archiveSha256", "snapshotHash", "resultHash", "reviewHash", "signedAtUtc",
)


class DossierValidationError(ValueError):
    """Untrusted input failed an integrity, identity or signature check."""


def _fail(message: str) -> None:
    raise DossierValidationError(message)


def _digest(data: bytes) -> str:
    return sha256(data).hexdigest()


def _sha_file(path: Path) -> tuple[str, int]:
    size = 0
    h = sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            size += len(chunk)
            if size > MAX_ARCHIVE_BYTES:
                _fail("Archive exceeds maximum byte limit")
            h.update(chunk)
    return h.hexdigest(), size


def _parse_json(data: bytes, name: str) -> Any:
    if len(data) > MAX_JSON_BYTES:
        _fail(f"JSON evidence too large: {name}")

    def unique_pairs(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                _fail(f"Duplicate JSON key in {name}: {key}")
            result[key] = value
        return result

    try:
        return json.loads(data.decode("utf-8"), object_pairs_hook=unique_pairs,
                          parse_constant=lambda x: _fail(f"Non-finite JSON in {name}: {x}"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        _fail(f"Malformed UTF-8 JSON: {name}: {exc}")


def _canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, ensure_ascii=False,
                      allow_nan=False, separators=(",", ":")).encode("utf-8")


def _require_hash(value: Any, label: str) -> str:
    if not isinstance(value, str) or not HEX64.fullmatch(value):
        _fail(f"Invalid 64-character SHA256: {label}")
    return value


def _require_safe_name(name: str) -> None:
    if (not name or "\\" in name or ":" in name or "\x00" in name or
            name.startswith("/") or name.endswith("/") or
            any(ord(c) < 32 for c in name) or
            any(part in ("", ".", "..") for part in name.split("/")) or
            PurePosixPath(name).is_absolute()):
        _fail(f"Unsafe or ambiguous ZIP entry name: {name!r}")


def _check_manifest_items(manifest: dict, *, name: str) -> dict[str, tuple[int, str]]:
    rows = manifest.get("files")
    if not isinstance(rows, list) or not rows:
        _fail(f"{name} requires a nonempty files array")
    output: dict[str, tuple[int, str]] = {}
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get("name"), str):
            _fail(f"Malformed file record in {name}")
        fname = row["name"]
        _require_safe_name(fname)
        size = row.get("bytes")
        if fname == "manifest.json" or fname in output:
            _fail(f"Duplicate or self-referential manifest entry: {fname}")
        if type(size) is not int or size < 0 or size > MAX_ENTRY_BYTES:
            _fail(f"Invalid file byte count for {fname}")
        output[fname] = (size, _require_hash(row.get("sha256"), fname))
    return output


def _identity(manifest: dict, label: str) -> dict[str, str]:
    return {key: _require_hash(manifest.get(key), f"{label}.{key}") for key in
            ("snapshotHash", "resultHash", "reviewHash")}


def audit_dossier(path: str | Path) -> dict:
    """Audit all archive bytes and both manifests without trusting client claims.

    This audit deliberately returns ``engineeringComplianceVerified=False`` and
    does not inspect any private signing key.  No cryptographic provenance is
    implied until :func:`verify_detached_signature` succeeds.
    """
    archive = Path(path)
    if not archive.is_file() or archive.is_symlink():
        _fail("Archive must be an existing regular non-symlink file")
    archive_hash, archive_size = _sha_file(archive)
    meta: dict[str, tuple[int, str]] = {}
    retained: dict[str, bytes] = {}
    read_total = 0
    json_names = {"manifest.json", "original_export_manifest.json",
                  "P1_P4_gate_index.json", "P1_P4_phase_reviews.json",
                  "frozen_input.json", "sealed_result.json", "review_record.json"}
    try:
        with ZipFile(archive, "r") as z:
            infos = z.infolist()
            if not infos or len(infos) > MAX_FILES:
                _fail("Invalid ZIP file count")
            seen: set[str] = set()
            for entry in infos:
                name = entry.filename
                _require_safe_name(name)
                if name in seen:
                    _fail(f"Duplicate ZIP entry: {name}")
                seen.add(name)
                if entry.is_dir() or entry.flag_bits & 1:
                    _fail(f"Directory or encrypted entry is disallowed: {name}")
                unix_mode = (entry.external_attr >> 16) & 0xFFFF
                if stat.S_IFMT(unix_mode) == stat.S_IFLNK:
                    _fail(f"ZIP symlink is disallowed: {name}")
                if entry.file_size > MAX_ENTRY_BYTES:
                    _fail(f"ZIP entry exceeds size limit: {name}")
                if entry.compress_size and entry.file_size > entry.compress_size * 1000:
                    _fail(f"Suspicious ZIP compression ratio: {name}")
                h = sha256()
                actual = 0
                chunks = []
                with z.open(entry) as f:
                    for chunk in iter(lambda: f.read(1024 * 1024), b""):
                        actual += len(chunk)
                        read_total += len(chunk)
                        if actual > MAX_ENTRY_BYTES or read_total > MAX_UNCOMPRESSED_BYTES:
                            _fail("ZIP extraction budget exceeded")
                        h.update(chunk)
                        if name in json_names:
                            if actual > MAX_JSON_BYTES:
                                _fail(f"Oversized JSON evidence: {name}")
                            chunks.append(chunk)
                if actual != entry.file_size:
                    _fail(f"ZIP claimed size differs from extracted bytes: {name}")
                meta[name] = (actual, h.hexdigest())
                if name in json_names:
                    retained[name] = b"".join(chunks)
    except (BadZipFile, EOFError, OSError, RuntimeError, ValueError) as exc:
        if isinstance(exc, DossierValidationError):
            raise
        _fail(f"Unreadable ZIP archive: {exc}")

    if "manifest.json" not in retained or "original_export_manifest.json" not in retained:
        _fail("Both outer and original export manifests are required")
    for required_name in ("P1_P4_gate_index.json", "P1_P4_phase_reviews.json",
                          "frozen_input.json", "sealed_result.json", "review_record.json"):
        if required_name not in retained:
            _fail(f"Missing required evidence document: {required_name}")
    outer = _parse_json(retained["manifest.json"], "manifest.json")
    nested = _parse_json(retained["original_export_manifest.json"],
                         "original_export_manifest.json")
    gate = _parse_json(retained["P1_P4_gate_index.json"], "P1_P4_gate_index.json")
    if not all(isinstance(x, dict) for x in (outer, nested, gate)):
        _fail("Manifest and gate JSON roots must be objects")
    if outer.get("formalConstructionIssue") is not False or gate.get("formalConstructionIssue") is not False:
        _fail("A reviewed working dossier cannot claim formal construction issue")
    if nested.get("formalIssueReady", False) is not False or nested.get("formalConstructionIssue", False) is not False:
        _fail("Original export cannot claim formal construction issue")
    if outer.get("schemaVersion") != "pitguard.P1-P4-review-bundle/1":
        _fail("Unknown outer dossier schema")
    if nested.get("schemaVersion") != "pitguard.one-strut-delivery/1":
        _fail("Unknown original export manifest schema")
    identities = _identity(outer, "outer")
    for label, document in (("original", nested), ("gate", gate)):
        if _identity(document, label) != identities:
            _fail(f"{label} identity does not match outer manifest")
    # Internal JSON content identities are checked independently of both
    # manifests, which can otherwise self-certify altered bytes.
    frozen = _parse_json(retained["frozen_input.json"], "frozen_input.json")
    sealed = _parse_json(retained["sealed_result.json"], "sealed_result.json")
    review = _parse_json(retained["review_record.json"], "review_record.json")
    if not all(isinstance(x,dict) for x in (frozen,sealed,review)):
        _fail("Frozen input, sealed result and review must be JSON objects")
    if _digest(_canonical(frozen)) != identities["snapshotHash"]:
        _fail("Frozen input canonical snapshot hash mismatch")
    if (sealed.get("snapshotHash") != identities["snapshotHash"] or
            sealed.get("snapshot") != frozen or
            sealed.get("resultHash") != identities["resultHash"] or
            _digest(_canonical({k:v for k,v in sealed.items() if k!="resultHash"})) != identities["resultHash"]):
        _fail("Sealed result canonical identity does not match the frozen input")
    if (review.get("reviewHash") != identities["reviewHash"] or
            review.get("resultHash") != identities["resultHash"] or
            review.get("scope") != "preliminary_calculation_review_only" or
            review.get("formalConstructionIssue") is not False or
            _digest(_canonical({k:v for k,v in review.items() if k!="reviewHash"})) != identities["reviewHash"]):
        _fail("Preliminary review canonical identity or scope mismatch")
    if sealed.get("analysisStatus") != "complete":
        _fail("Work copy requires a completed sealed calculation")
    expected_outer = _check_manifest_items(outer, name="outer")
    actual_outer = {k: v for k, v in meta.items() if k != "manifest.json"}
    if expected_outer != actual_outer:
        omitted = sorted(set(actual_outer) - set(expected_outer))
        missing = sorted(set(expected_outer) - set(actual_outer))
        wrong = sorted(k for k in set(actual_outer) & set(expected_outer)
                       if actual_outer[k] != expected_outer[k])
        _fail(f"Outer manifest mismatch: unlisted={omitted}, missing={missing}, altered={wrong}")
    expected_orig_sha = _require_hash(outer.get("previousManifestSha256"), "previousManifestSha256")
    if expected_orig_sha != meta["original_export_manifest.json"][1]:
        _fail("Original export manifest digest mismatch")
    expected_nested = _check_manifest_items(nested, name="original")
    for fname, (length, digest) in expected_nested.items():
        if fname not in meta or meta[fname] != (length, digest):
            _fail(f"Original export file has changed or is missing: {fname}")
    if not isinstance(gate.get("phases"), list):
        _fail("Missing P1-P4 gate stages")
    phases = gate["phases"]
    phase_codes = [p.get("phase") for p in phases if isinstance(p, dict)]
    if (len(phases) != 4 or len(phase_codes) != 4 or
            any(not isinstance(x, str) for x in phase_codes) or
            sorted(phase_codes) != ["P1", "P2", "P3", "P4"]):
        _fail("Gate stages must contain P1-P4 exactly once")
    for phase in phases:
        if not isinstance(phase.get("outstanding"), list) or not all(
                isinstance(x, str) for x in phase["outstanding"]):
            _fail(f"Invalid outstanding register for {phase['phase']}")
        if not isinstance(phase.get("assessmentStatus"), str):
            _fail(f"Invalid assessment status for {phase['phase']}")
        if phase.get("releaseApproved") is not False:
            _fail(f"Gate phase must not claim issue approval: {phase['phase']}")
    reviews = _parse_json(retained["P1_P4_phase_reviews.json"], "P1_P4_phase_reviews.json")
    if not isinstance(reviews, list) or len(reviews) > 4:
        _fail("Invalid phase-review record array")
    declared_reviews = gate.get("phaseReviewHashes")
    if not isinstance(declared_reviews, dict):
        _fail("Gate phase-review index must be an object")
    actual_reviews = {}
    for entry in reviews:
        if not isinstance(entry, dict) or entry.get("phase") not in ("P1", "P2", "P3", "P4"):
            _fail("Unexpected phase-review record")
        phase = entry["phase"]
        if phase in actual_reviews:
            _fail("Duplicate phase-review record")
        if entry.get("resultHash") != identities["resultHash"] or entry.get("snapshotHash") != identities["snapshotHash"]:
            _fail("Phase review refers to another calculation")
        if entry.get("formalEngineeringApproval") is not False:
            _fail("Phase review cannot claim formal engineering approval")
        review_digest = entry.get("phaseReviewHash")
        if (not isinstance(review_digest,str) or not HEX64.fullmatch(review_digest) or
                _digest(_canonical({k:v for k,v in entry.items() if k!="phaseReviewHash"})) != review_digest):
            _fail("Phase review canonical hash mismatch")
        actual_reviews[phase] = review_digest
    if declared_reviews != actual_reviews:
        _fail("Gate phase-review hashes must match records exactly")
    # A file modified during verification must not inherit the hash of a
    # different byte sequence (race between hashing and ZIP parsing).
    if _sha_file(archive) != (archive_hash, archive_size):
        _fail("Archive changed during integrity verification")
    return {
        "schemaVersion": "pitguard.detached-dossier-audit/1",
        "status": "archive_byte_integrity_verified_source_authenticity_unverified",
        "archiveName": archive.name,
        "archiveBytes": archive_size,
        "archiveSha256": archive_hash,
        "fileCount": len(meta),
        "snapshotHash": identities["snapshotHash"],
        "resultHash": identities["resultHash"],
        "reviewHash": identities["reviewHash"],
        "phases": [
            {"phase": p["phase"], "assessmentStatus": p.get("assessmentStatus"),
             "outstandingCount": len(p.get("outstanding") or []),
             "outstanding": p.get("outstanding") or []}
            for p in phases
        ],
        "sourceFilesIndependentlyAuthenticated": False,
        "engineeringComplianceVerified": False,
        "formalConstructionIssue": False,
    }


def _crypto():
    try:
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
        from cryptography.exceptions import InvalidSignature
    except ImportError as exc:
        _fail("Ed25519 signing requires cryptography>=44: pip install 'cryptography>=44,<47'")
    return serialization, Ed25519PrivateKey, Ed25519PublicKey, InvalidSignature


def _public_key_id(public_key) -> str:
    serialization, _, _, _ = _crypto()
    raw = public_key.public_bytes(encoding=serialization.Encoding.Raw,
                                  format=serialization.PublicFormat.Raw)
    return _digest(raw)


def _write_new_file(path: Path, data: bytes) -> None:
    # x-mode means an existing key, signature or trusted certificate is never overwritten.
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
    except BaseException:
        try:
            path.unlink()
        except OSError:
            pass
        raise


def _password_from_env(var_name: str) -> bytes:
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,80}", var_name):
        _fail("Password environment variable name is invalid")
    password = os.getenv(var_name)
    if password is None or len(password) < 12:
        _fail(f"Set {var_name} to a passphrase with at least 12 characters")
    return password.encode("utf-8")


def generate_keypair(private_path: str | Path, public_path: str | Path, *,
                     password_env: str = "PITGUARD_SIGNING_PASSWORD") -> dict:
    serialization, Ed25519PrivateKey, _, _ = _crypto()
    priv_path, pub_path = Path(private_path), Path(public_path)
    if priv_path.absolute() == pub_path.absolute():
        _fail("Private and public key paths must differ")
    if priv_path.exists() or pub_path.exists():
        _fail("Refusing to overwrite existing key material")
    key = Ed25519PrivateKey.generate()
    private_pem = key.private_bytes(serialization.Encoding.PEM,
                                    serialization.PrivateFormat.PKCS8,
                                    serialization.BestAvailableEncryption(_password_from_env(password_env)))
    public = key.public_key()
    public_pem = public.public_bytes(serialization.Encoding.PEM,
                                      serialization.PublicFormat.SubjectPublicKeyInfo)
    _write_new_file(priv_path, private_pem)
    try:
        _write_new_file(pub_path, public_pem)
    except BaseException:
        priv_path.unlink(missing_ok=True)
        raise
    return {"status": "keypair_generated", "keyId": _public_key_id(public),
            "publicKeyPath": str(pub_path), "privateKeyPath": str(priv_path),
            "note": "Distribute public key out of band; keep encrypted private key offline"}


def sign_dossier(archive: str | Path, private_key: str | Path, *,
                 signature_out: str | Path | None = None,
                 password_env: str = "PITGUARD_SIGNING_PASSWORD") -> dict:
    serialization, Ed25519PrivateKey, _, _ = _crypto()
    audit = audit_dossier(archive)
    key_path = Path(private_key)
    if not key_path.is_file() or key_path.is_symlink():
        _fail("Encrypted private signing key must be a local regular file")
    try:
        key = serialization.load_pem_private_key(key_path.read_bytes(),
                                                  password=_password_from_env(password_env))
    except (TypeError, ValueError) as exc:
        _fail(f"Cannot open the encrypted private key: {exc}")
    if not isinstance(key, Ed25519PrivateKey):
        _fail("Signing key must use Ed25519")
    claims = {
        "schemaVersion": SCHEMA, "algorithm": "Ed25519",
        "keyId": _public_key_id(key.public_key()),
        "archiveName": audit["archiveName"], "archiveBytes": audit["archiveBytes"],
        "archiveSha256": audit["archiveSha256"],
        "snapshotHash": audit["snapshotHash"], "resultHash": audit["resultHash"],
        "reviewHash": audit["reviewHash"],
        "signedAtUtc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    signature = base64.b64encode(key.sign(DOMAIN + _canonical(claims))).decode("ascii")
    record = {**claims, "signatureBase64": signature}
    output = Path(signature_out) if signature_out else Path(str(archive) + ".sig.json")
    _write_new_file(output, json.dumps(record, ensure_ascii=False, sort_keys=True,
                                       indent=2).encode("utf-8") + b"\n")
    return {"status": "detached_signature_created", "signaturePath": str(output),
            "keyId": claims["keyId"], "archiveSha256": claims["archiveSha256"],
            "formalConstructionIssue": False}


def verify_detached_signature(archive: str | Path, signature_file: str | Path,
                              trusted_public_key: str | Path) -> dict:
    serialization, _, Ed25519PublicKey, InvalidSignature = _crypto()
    audit = audit_dossier(archive)
    sig_path = Path(signature_file)
    pub_path = Path(trusted_public_key)
    if (not sig_path.is_file() or sig_path.is_symlink() or
            not pub_path.is_file() or pub_path.is_symlink()):
        _fail("Signature and trusted public key must be local regular files")
    if sig_path.stat().st_size > MAX_SIGNATURE_BYTES:
        _fail("Signature record exceeds size budget")
    record = _parse_json(sig_path.read_bytes(), "detached signature")
    if not isinstance(record, dict) or set(record) != set(SIGNED_KEYS) | {"signatureBase64"}:
        _fail("Detached signature must contain exactly the defined signed fields")
    claims = {key: record[key] for key in SIGNED_KEYS}
    if claims["schemaVersion"] != SCHEMA or claims["algorithm"] != "Ed25519":
        _fail("Unsupported detached signature scheme")
    if not isinstance(claims["signedAtUtc"], str) or not claims["signedAtUtc"].endswith("+00:00"):
        _fail("Signature time must have an explicit UTC timezone")
    try:
        datetime.fromisoformat(claims["signedAtUtc"])
    except ValueError:
        _fail("Invalid signature timestamp")
    for key in ("archiveName", "archiveBytes", "archiveSha256", "snapshotHash", "resultHash", "reviewHash"):
        if claims[key] != audit[key]:
            _fail(f"Signed archive identity differs from the audited dossier: {key}")
    try:
        public = serialization.load_pem_public_key(pub_path.read_bytes())
    except ValueError as exc:
        _fail(f"Unreadable trusted public key: {exc}")
    if not isinstance(public, Ed25519PublicKey):
        _fail("Trusted public key must use Ed25519")
    if claims["keyId"] != _public_key_id(public):
        _fail("Trusted public key identity differs from the signature")
    try:
        raw_sig = base64.b64decode(record["signatureBase64"], validate=True)
        if len(raw_sig) != 64:
            _fail("Ed25519 signature must be 64 bytes")
        public.verify(raw_sig, DOMAIN + _canonical(claims))
    except (binascii.Error, TypeError, InvalidSignature):
        _fail("Detached Ed25519 signature is invalid")
    return {**audit, "schemaVersion": "pitguard.detached-dossier-verification/1",
            "status": "archive_and_trusted_signer_verified_working_copy_only",
            "signatureVerified": True,
            "trustedKeyId": claims["keyId"],
            "signatureTimeClaimUtc": claims["signedAtUtc"],
            "signatureTimeIndependentlyAttested": False,
            "sourceFilesIndependentlyAuthenticated": False,
            "engineeringComplianceVerified": False,
            "formalConstructionIssue": False}


def _cli() -> int:
    parser = argparse.ArgumentParser(description="PitGuard P1-P4 offline dossier integrity and Ed25519 verification")
    commands = parser.add_subparsers(dest="action", required=True)
    audit = commands.add_parser("audit", help="Check nested and outer archive byte integrity")
    audit.add_argument("archive")
    keygen = commands.add_parser("keygen", help="Generate encrypted Ed25519 key and distributable public key")
    keygen.add_argument("--private-key", required=True)
    keygen.add_argument("--public-key", required=True)
    keygen.add_argument("--password-env", default="PITGUARD_SIGNING_PASSWORD")
    sign = commands.add_parser("sign", help="Sign a verified working dossier with an offline encrypted private key")
    sign.add_argument("archive")
    sign.add_argument("--private-key", required=True)
    sign.add_argument("--signature-out")
    sign.add_argument("--password-env", default="PITGUARD_SIGNING_PASSWORD")
    verify = commands.add_parser("verify", help="Verify archive integrity with a separately trusted public key")
    verify.add_argument("archive")
    verify.add_argument("--signature")
    verify.add_argument("--trusted-public-key", required=True)
    args = parser.parse_args()
    try:
        if args.action == "audit":
            result = audit_dossier(args.archive)
        elif args.action == "keygen":
            result = generate_keypair(args.private_key, args.public_key,
                                      password_env=args.password_env)
        elif args.action == "sign":
            result = sign_dossier(args.archive, args.private_key,
                                  signature_out=args.signature_out,
                                  password_env=args.password_env)
        else:
            result = verify_detached_signature(args.archive,
                          args.signature or args.archive + ".sig.json", args.trusted_public_key)
    except (DossierValidationError, OSError) as exc:
        print(json.dumps({"status": "verification_failed", "error": str(exc),
                          "formalConstructionIssue": False}, ensure_ascii=False), file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(_cli())
