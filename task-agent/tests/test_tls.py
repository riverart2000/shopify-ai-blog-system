import ssl

from task_agent.tls import describe_tls_error, tls_context


def test_context_always_checks_certificates() -> None:
    context = tls_context()
    assert context.verify_mode == ssl.CERT_REQUIRED
    assert context.check_hostname is True


def test_certificate_failure_is_explained() -> None:
    exc = ssl.SSLCertVerificationError(1, "certificate verify failed")
    exc.verify_message = "self-signed certificate in certificate chain"
    assert describe_tls_error(exc) == (
        "The email server's security certificate couldn't be checked: self-signed certificate in certificate chain"
    )
    assert describe_tls_error(OSError("timed out")) is None
