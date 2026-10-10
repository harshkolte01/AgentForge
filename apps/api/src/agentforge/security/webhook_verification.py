"""Verify the untouched request bytes before JSON parsing or delivery admission."""
import hashlib
import hmac
import re

def verify_github_signature(body: bytes, signature: str | None, secret: str):
    if not signature or not re.fullmatch(r"sha256=[0-9a-f]{64}", signature):
        raise ValueError("github_webhook_signature_invalid")
    expected = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, signature):
        raise ValueError("github_webhook_signature_invalid")
