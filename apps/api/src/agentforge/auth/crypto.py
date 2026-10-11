"""Versioned authenticated encryption for credentials; no tokens in JSON/logs."""
import base64
from hashlib import sha256
from cryptography.fernet import Fernet, InvalidToken

class CredentialCipher:
    def __init__(self, secret, *, domain: str = "github"):
        self.domain = domain
        if secret is None:
            raise ValueError(f"{domain}_encryption_not_configured")
        raw = secret.get_secret_value().encode()
        if len(raw) < 32:
            raise ValueError(f"{domain}_encryption_key_too_short")
        # Accept existing deployment key material as well as Fernet keys. Domain
        # separation keeps this key distinct from an operator's other uses, and keeps
        # each kind of credential (GitHub tokens, plugin secrets) unreadable by the other.
        self.fernet = Fernet(base64.urlsafe_b64encode(sha256(f"agentforge:{domain}:v1:".encode() + raw).digest()))

    def encrypt(self, value: str) -> str:
        return "v1:" + self.fernet.encrypt(value.encode()).decode("ascii")

    def decrypt(self, value: str) -> str:
        try:
            if not value.startswith("v1:"):
                raise ValueError()
            return self.fernet.decrypt(value[3:].encode()).decode()
        except (InvalidToken, UnicodeError, ValueError):
            raise ValueError(f"{self.domain}_credentials_unavailable") from None
