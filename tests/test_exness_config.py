import base64
import hashlib
import json
import unittest

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives import serialization

from xau_intel.exness_config import _signed_get_headers


class ExnessReadOnlySigningTests(unittest.TestCase):
    def test_get_headers_sign_payload_and_empty_idempotency(self):
        key = Ed25519PrivateKey.generate()
        headers = _signed_get_headers("/v1/configuration/accounts/123/account", "test-key", key)
        payload_bytes = base64.urlsafe_b64decode(headers["EXN-DATA"] + "==")
        payload = json.loads(payload_bytes)
        self.assertEqual(payload["method"], "GET")
        self.assertEqual(payload["idempotency_key"], "")
        self.assertEqual(headers["EXN-IDEMPOTENCY-KEY"], "")
        self.assertEqual(payload["body_hash"], base64.urlsafe_b64encode(hashlib.sha256(b"").digest()).decode().rstrip("="))
        signature = base64.urlsafe_b64decode(headers["EXN-SIGN"] + "==")
        key.public_key().verify(signature, payload_bytes)


if __name__ == "__main__":
    unittest.main()
