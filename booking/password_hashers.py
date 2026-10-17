"""Per-password salt & PBKDF2 plus a separate server-side key (Django SECRET_KEY).

Passwords are never decrypted; DB-only theft does not expose the pepper.
Preserve SECRET_KEY / SECRET_KEY_FALLBACKS when rotating keys.
"""
import hashlib
import hmac
from django.conf import settings
from django.contrib.auth.hashers import PBKDF2PasswordHasher
from django.utils.crypto import constant_time_compare

class PepperedPBKDF2PasswordHasher(PBKDF2PasswordHasher):
    algorithm = "pbkdf2_sha256_peppered_v1"

    @staticmethod
    def _pepper(password, secret):
        key = str(secret).encode("utf-8")
        body = b"classroom-password-pepper-v1\0" + password.encode("utf-8")
        return hmac.new(key, body, hashlib.sha256).hexdigest()

    def encode(self, password, salt, iterations=None):
        return super().encode(self._pepper(password, settings.SECRET_KEY), salt, iterations)

    def verify(self, password, encoded):
        if super().verify(password, encoded):
            return True
        decoded = self.decode(encoded)
        # Permit planned SECRET_KEY rotation using Django's fallback keys.
        for key in getattr(settings, "SECRET_KEY_FALLBACKS", ())[:3]:
            candidate = super().encode(self._pepper(password, key), decoded["salt"], decoded["iterations"])
            if constant_time_compare(encoded, candidate):
                return True
        return False
