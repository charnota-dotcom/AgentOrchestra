"""Update manifest signature verification."""

from __future__ import annotations

import base64

import pytest

from apps.service.updates import manifest


def _try_crypto():
    try:
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric.ed25519 import (
            Ed25519PrivateKey,
        )

        return Ed25519PrivateKey, serialization
    except ImportError:
        return None, None


def test_canonical_bytes_excludes_signature() -> None:
    payload = {"version": "0.1", "signature": "abc", "channels": {}}
    assert b"signature" not in manifest.canonical_bytes(payload)


@pytest.mark.skipif(_try_crypto()[0] is None, reason="cryptography not installed")
def test_round_trip_signature() -> None:
    Ed25519PrivateKey, serialization = _try_crypto()
    sk = Ed25519PrivateKey.generate()
    pk = sk.public_key()
    pem = pk.public_bytes(
        serialization.Encoding.PEM,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode()

    payload = {
        "version": "0.2.0",
        "released_at": "2026-06-01T12:00:00+00:00",
        "channels": {
            "stable": {
                "linux-x86_64": {"url": "https://x", "sha256": "abc"},
            },
        },
        "notes": "test",
    }
    sig = sk.sign(manifest.canonical_bytes(payload))
    payload["signature"] = base64.b64encode(sig).decode()

    parsed = manifest.verify(payload, public_key_pem=pem)
    assert parsed.version == "0.2.0"
    assert parsed.channels["stable"]["linux-x86_64"].url == "https://x"


@pytest.mark.skipif(_try_crypto()[0] is None, reason="cryptography not installed")
def test_tampered_payload_fails() -> None:
    Ed25519PrivateKey, serialization = _try_crypto()
    sk = Ed25519PrivateKey.generate()
    pk = sk.public_key()
    pem = pk.public_bytes(
        serialization.Encoding.PEM,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode()

    payload = {"version": "0.2.0", "channels": {}}
    sig = sk.sign(manifest.canonical_bytes(payload))
    payload["signature"] = base64.b64encode(sig).decode()
    payload["version"] = "0.9.9"  # tamper after signing

    with pytest.raises(manifest.InvalidSignatureError):
        manifest.verify(payload, public_key_pem=pem)


def test_missing_signature_rejected() -> None:
    Ed25519PrivateKey, _ = _try_crypto()
    if Ed25519PrivateKey is None:
        pytest.skip("cryptography not installed")
    payload = {"version": "0.2.0", "channels": {}}
    with pytest.raises(manifest.InvalidSignatureError):
        manifest.verify(
            payload, public_key_pem="-----BEGIN PUBLIC KEY-----\n-----END PUBLIC KEY-----\n"
        )
