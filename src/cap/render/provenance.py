"""C2PA provenance on the final release (D29; principle 3, "disclosed AI, always").

After the final render and before the release is sealed, every video and the
thumbnail get a signed C2PA manifest embedded: what the file is (the episode title),
that it was made by this platform, and that it is AI-generated media
(IPTC digital source type "trainedAlgorithmicMedia"). The approval token then binds
the signed files, so the publisher uploads exactly what was signed.

The certificate is self-signed for the MVP (D29): a local root issues one ES256
signing certificate, made on first use under <data_dir>/c2pa/, the private key
readable by its owner only. The root's own key is not kept. A reader therefore says
the manifest is valid and the signer untrusted, which is what D29 accepts until a
trusted CA certificate replaces it after go-live (SSL.com; drop the files in the same
folder). Only the review render is unsigned: it never leaves the machine.

c2pa-python is pinned to 0.38.0: 0.38.1 ships no Windows build.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import threading
import uuid
from pathlib import Path
from typing import Any

from cap.config import get_settings
from cap.secretfile import restrict_to_owner
from cap.version import build_id

AI_GENERATED = "http://cv.iptc.org/newscodes/digitalsourcetype/trainedAlgorithmicMedia"
MIME = {".mp4": "video/mp4", ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg"}
CHAIN, KEY = "signer-chain.pem", "signer.key"  # the pair from before CURRENT existed
CURRENT = "current"  # names the pair in use: "<id>", for <id>-signer-chain.pem and <id>-signer.key
LEAF_DAYS = 730


class ProvenanceError(RuntimeError):
    """The files could not be signed; the release fails with a retry on the Outbox."""


def credentials_dir() -> Path:
    s = get_settings()
    return Path(s.c2pa_dir) if s.c2pa_dir else Path(s.data_dir) / "c2pa"


def _new_credentials(folder: Path) -> None:
    """A local root and one signing certificate under it (C2PA needs a chain, not a
    lone self-signed leaf). Written owner-only; the root's key is discarded.

    The pair is written under a fresh id, then CURRENT is switched to it in one atomic
    rename, so a signer can never read one job's key with another's certificate
    (review 24 Oct: two first-time jobs at once did). Old pairs stay; they are tiny."""
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

    def name(cn: str) -> x509.Name:
        return x509.Name(
            [
                x509.NameAttribute(NameOID.COMMON_NAME, cn),
                x509.NameAttribute(NameOID.ORGANIZATION_NAME, "Content Agent Portfolio"),
            ]
        )

    def usage(*, sign: bool) -> x509.KeyUsage:
        return x509.KeyUsage(
            digital_signature=not sign, content_commitment=False, key_encipherment=False,
            data_encipherment=False, key_agreement=False, key_cert_sign=sign,
            crl_sign=sign, encipher_only=False, decipher_only=False,
        )  # fmt: skip

    now = dt.datetime.now(dt.UTC)
    root_key, key = ec.generate_private_key(ec.SECP256R1()), ec.generate_private_key(ec.SECP256R1())
    root = (
        x509.CertificateBuilder()
        .subject_name(name("CAP local root (self-signed, D29)"))
        .issuer_name(name("CAP local root (self-signed, D29)"))
        .public_key(root_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - dt.timedelta(days=1))
        .not_valid_after(now + dt.timedelta(days=LEAF_DAYS + 1))
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
        .add_extension(usage(sign=True), critical=True)
        .add_extension(x509.SubjectKeyIdentifier.from_public_key(root_key.public_key()), False)
        .sign(root_key, hashes.SHA256())
    )
    leaf = (
        x509.CertificateBuilder()
        .subject_name(name("CAP release signer"))
        .issuer_name(root.subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - dt.timedelta(days=1))
        .not_valid_after(now + dt.timedelta(days=LEAF_DAYS))
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(usage(sign=False), critical=True)
        .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.EMAIL_PROTECTION]), False)
        .add_extension(
            x509.AuthorityKeyIdentifier.from_issuer_public_key(root_key.public_key()), False
        )
        .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()), False)
        .sign(root_key, hashes.SHA256())
    )
    folder.mkdir(parents=True, exist_ok=True)
    restrict_to_owner(folder)  # the folder first: nothing inside is ever readable by others
    pem = serialization.Encoding.PEM
    pair = uuid.uuid4().hex[:12]
    key_file = folder / f"{pair}-{KEY}"
    key_file.write_bytes(
        key.private_bytes(pem, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
    )
    restrict_to_owner(key_file)
    (folder / f"{pair}-{CHAIN}").write_bytes(leaf.public_bytes(pem) + root.public_bytes(pem))
    pointer = folder / f"{CURRENT}.{pair}.tmp"
    pointer.write_text(pair, encoding="utf-8")
    os.replace(pointer, folder / CURRENT)  # atomic: readers see the old pair or the new


def _expired(chain: Path) -> bool:
    from cryptography import x509

    leaf = x509.load_pem_x509_certificates(chain.read_bytes())[0]
    return leaf.not_valid_after_utc <= dt.datetime.now(dt.UTC) + dt.timedelta(days=7)


_MAKING = threading.Lock()  # one new pair at a time in this process


def credential_files(folder: Path) -> tuple[Path, Path] | None:
    """The (chain, key) pair CURRENT names, or the pre-CURRENT pair; None when none."""
    current = folder / CURRENT
    if current.is_file():
        pair = current.read_text(encoding="utf-8").strip()
        return folder / f"{pair}-{CHAIN}", folder / f"{pair}-{KEY}"
    if (folder / CHAIN).is_file() and (folder / KEY).is_file():
        return folder / CHAIN, folder / KEY
    return None


def _credentials() -> tuple[bytes, bytes]:
    folder = credentials_dir()
    with _MAKING:
        files = credential_files(folder)
        if files is None or _expired(files[0]):
            _new_credentials(folder)
            files = credential_files(folder)
    assert files is not None
    chain, key = files
    return chain.read_bytes(), key.read_bytes()


def manifest_for(title: str, post_id: str, channel: str) -> dict[str, Any]:
    return {
        "claim_generator_info": [
            {"name": "Content Agent Portfolio", "version": build_id().split()[0]}
        ],
        "title": title,
        "assertions": [
            {
                "label": "c2pa.actions.v2",
                "data": {
                    "actions": [
                        {
                            "action": "c2pa.created",
                            "digitalSourceType": AI_GENERATED,
                            "softwareAgent": {"name": "Content Agent Portfolio"},
                        }
                    ]
                },
            },
            {"label": "org.cap.post", "data": {"post_id": post_id, "channel": channel}},
        ],
    }


def sign_in_place(path: Path, manifest: dict[str, Any]) -> None:
    """Embed a signed manifest in `path`, replacing the file atomically."""
    import c2pa

    mime = MIME.get(path.suffix.lower())
    if mime is None:
        raise ProvenanceError(f"no C2PA format for {path.name}")
    chain, key = _credentials()
    tmp = path.with_name(path.name + ".signing")
    try:
        signer = c2pa.Signer.from_info(
            c2pa.C2paSignerInfo(c2pa.C2paSigningAlg.ES256, chain, key, None)
        )
        with path.open("rb") as src, tmp.open("w+b") as dst:
            c2pa.Builder(manifest).sign(signer, mime, src, dst)
        os.replace(tmp, path)
    except Exception as e:  # the native library raises its own types
        tmp.unlink(missing_ok=True)
        if isinstance(e, ProvenanceError):
            raise
        raise ProvenanceError(f"could not sign {path.name}: {type(e).__name__}: {e}") from e


def sign_release(title: str, post_id: str, channel: str, files: list[Path]) -> list[str]:
    """Sign every media file of a release; returns the names signed. Text files (the
    test renderer) are skipped."""
    manifest = manifest_for(title, post_id, channel)
    signed = []
    for path in files:
        if path.suffix.lower() in MIME:
            sign_in_place(path, manifest)
            signed.append(path.name)
    return signed


def read(path: Path) -> dict[str, Any] | None:
    """The active manifest and the validation result, or None when the file has none:
    {"title", "validation_state", "ai_generated", "post_id"}."""
    import c2pa

    try:
        report = json.loads(c2pa.Reader(str(path)).json())
    except Exception:  # noqa: BLE001 - no manifest, or not a C2PA format
        return None
    active = report.get("manifests", {}).get(report.get("active_manifest", ""), {})
    by_label = {a.get("label"): a.get("data", {}) for a in active.get("assertions", [])}
    actions = by_label.get("c2pa.actions.v2", {}).get("actions", [])
    return {
        "title": active.get("title", ""),
        "validation_state": report.get("validation_state", ""),
        "ai_generated": any(a.get("digitalSourceType") == AI_GENERATED for a in actions),
        "post_id": by_label.get("org.cap.post", {}).get("post_id", ""),
    }
