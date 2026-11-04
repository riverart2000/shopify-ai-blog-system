"""Certificate checks for the agent's own connections (IMAP, SMTP).

Python's built-in default reads /etc/ssl/cert.pem, which on macOS is missing newer roots such as
Let's Encrypt's ISRG Root X2 (used by mail.themedicalpark.com), so a valid certificate fails with
"self-signed certificate in certificate chain". This checks against the Mac's own trust store, as
Chrome and Mail do, or certifi's up-to-date list. Certificates are always checked.
"""

from __future__ import annotations

import ssl


def tls_context() -> ssl.SSLContext:
    try:
        import truststore
    except ImportError:
        import certifi

        return ssl.create_default_context(cafile=certifi.where())
    return truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)


def describe_tls_error(exc: Exception) -> str | None:
    """A plain message for a certificate failure, or None if it's not one."""
    if isinstance(exc, ssl.SSLCertVerificationError):
        return f"The email server's security certificate couldn't be checked: {exc.verify_message or exc}"
    return None
