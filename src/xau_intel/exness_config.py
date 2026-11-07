"""Read-only Exness Trader API configuration reader.

This module only reads account and instrument configuration. It does not place,
modify, or close orders. EXN_API_BASE_URL must be the account Access Point
provided by Exness, not a guessed/static host.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _load_private_key() -> Ed25519PrivateKey:
    key_file = os.getenv("EXN_PRIVATE_KEY_FILE")
    if key_file:
        raw = Path(key_file).read_bytes()
        return serialization.load_pem_private_key(raw, password=None)

    value = os.getenv("EXN_PRIVATE_KEY", "").strip()
    if not value:
        raise RuntimeError("Set EXN_PRIVATE_KEY_FILE or EXN_PRIVATE_KEY securely.")
    if value.startswith("-----BEGIN"):
        return serialization.load_pem_private_key(value.encode(), password=None)

    # Exness-generated key format: 128 hex chars = 32-byte seed + 32-byte public key.
    try:
        decoded = bytes.fromhex(value)
    except ValueError as exc:
        raise RuntimeError("EXN_PRIVATE_KEY must be an Ed25519 hex key or unencrypted PKCS#8 PEM.") from exc
    if len(decoded) == 64:
        decoded = decoded[:32]
    if len(decoded) != 32:
        raise RuntimeError("Hex Ed25519 private key must contain 64 or 128 hex characters.")
    return Ed25519PrivateKey.from_private_bytes(decoded)


def _signed_get_headers(path: str, api_key: str, private_key: Ed25519PrivateKey) -> dict[str, str]:
    timestamp = int(time.time() * 1000)
    body_hash = _b64url(hashlib.sha256(b"").digest())
    payload = {
        "api_key": api_key,
        "idempotency_key": "",
        "timestamp": timestamp,
        "sign_version": 1,
        "method": "GET",
        "path": path,
        "body_hash": body_hash,
    }
    payload_bytes = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return {
        "EXN-API-KEY": api_key,
        "EXN-IDEMPOTENCY-KEY": "",
        "EXN-TIMESTAMP": str(timestamp),
        "EXN-SIGN-VERSION": "1",
        "EXN-DATA": _b64url(payload_bytes),
        "EXN-SIGN": _b64url(private_key.sign(payload_bytes)),
        "Accept": "application/json",
    }


def signed_get(path: str, *, timeout: float = 15.0) -> dict:
    """Send a signed GET to the configured account Access Point."""
    api_key = os.getenv("EXN_API_KEY", "").strip()
    base_url = os.getenv("EXN_API_BASE_URL", "").strip().rstrip("/")
    if not api_key:
        raise RuntimeError("EXN_API_KEY is not configured.")
    if not base_url.startswith("https://"):
        raise RuntimeError("EXN_API_BASE_URL must be the HTTPS account Access Point from Exness.")
    if not path.startswith("/") or "?" in path and any(part == "" for part in path.split("?", 1)[1].split("&")):
        raise ValueError("Use an exact absolute API path and a well-formed query string.")
    private_key = _load_private_key()
    request = urllib.request.Request(
        base_url + path,
        headers=_signed_get_headers(path, api_key, private_key),
        method="GET",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Exness API HTTP {exc.code}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Exness API connection failed: {exc.reason}") from exc


def read_xauusd_account_configuration() -> dict:
    """Read account settings and XAUUSD trading conditions; never sends orders."""
    account_id = os.getenv("EXN_ACCOUNT_ID", "").strip()
    if not account_id or not account_id.isdigit():
        raise RuntimeError("EXN_ACCOUNT_ID must be set to the numeric Exness trading account ID.")
    root = f"/v1/configuration/accounts/{account_id}"
    account = signed_get(root + "/account")
    instrument = signed_get(root + "/instruments/XAUUSD/conditions")
    settings = account.get("settings") or {}
    return {
        "mode": "read_only",
        "account_id": account.get("account_id", account_id),
        "account_name": account.get("account_name"),
        "account_currency": settings.get("currency"),
        "account_leverage": settings.get("leverage"),
        "account_status": settings.get("account_status"),
        "account_trade_mode": settings.get("trade_mode"),
        "margin_call_level": settings.get("margin_call_level"),
        "stopout_level": settings.get("stopout_level"),
        "instrument": instrument.get("instrument"),
        "contract_size": instrument.get("contract_size"),
        "volume_min": instrument.get("volume_min"),
        "volume_max": instrument.get("volume_max"),
        "volume_step": instrument.get("volume_step"),
        "swap_long": instrument.get("swap_long"),
        "swap_short": instrument.get("swap_short"),
        "commission_settings": instrument.get("commission_settings"),
        "margin_percent": instrument.get("margin_percent"),
        "trade_mode_instrument": instrument.get("trade_mode"),
        "point_digits": instrument.get("point_digits"),
        "raw_account": account,
        "raw_instrument": instrument,
        "warning": "Commission charging convention and dynamic HMR/margin rules must be interpreted before account simulation or trading.",
    }
