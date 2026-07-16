#!/usr/bin/env python3
"""Domain intelligence checker.

Given a domain, this script attempts to gather as much publicly available
information as possible, including:
- DNS records (A, AAAA, CNAME, MX, NS, SOA, TXT, CAA, DS, DNSKEY)
- RDAP registration data
- WHOIS output (if local whois CLI is installed)
- HTTP reachability and response headers
- TLS certificate metadata
- Reverse DNS for resolved IP addresses

Usage:
    python dnschecker.py example.com
"""

from __future__ import annotations

import argparse
import datetime as dt
import ipaddress
import json
import socket
import ssl
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any


DEFAULT_RECORD_TYPES = ["A", "AAAA", "CNAME", "MX", "NS", "SOA", "TXT", "CAA", "DS", "DNSKEY"]


def utc_now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def normalize_domain(raw_value: str) -> str:
    value = (raw_value or "").strip()
    if not value:
        raise ValueError("Domain cannot be empty.")

    if "://" in value:
        parsed = urllib.parse.urlparse(value)
        value = parsed.netloc or parsed.path

    value = value.split("/")[0].split(":")[0].strip().rstrip(".")
    if not value:
        raise ValueError("Could not parse a domain from input.")

    try:
        ascii_domain = value.encode("idna").decode("ascii")
    except UnicodeError as exc:
        raise ValueError(f"Invalid internationalized domain: {exc}") from exc

    labels = ascii_domain.split(".")
    if len(labels) < 2 or any(not label for label in labels):
        raise ValueError("Input does not look like a valid domain.")

    return ascii_domain.lower()


def fetch_json(url: str, timeout: float) -> dict[str, Any] | None:
    request = urllib.request.Request(url, headers={"User-Agent": "dnschecker/1.0"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read().decode("utf-8", errors="replace")
            return json.loads(body)
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, json.JSONDecodeError):
        return None


def fetch_rdap(domain: str, timeout: float) -> dict[str, Any]:
    rdap_url = f"https://rdap.org/domain/{domain}"
    payload = fetch_json(rdap_url, timeout)
    if payload is None:
        return {"available": False, "source": rdap_url, "error": "RDAP lookup failed."}

    events = []
    for event in payload.get("events", []):
        events.append(
            {
                "eventAction": event.get("eventAction"),
                "eventDate": event.get("eventDate"),
            }
        )

    nameservers = []
    for nameserver in payload.get("nameservers", []):
        nameservers.append(
            {
                "ldhName": nameserver.get("ldhName"),
                "unicodeName": nameserver.get("unicodeName"),
            }
        )

    return {
        "available": True,
        "source": rdap_url,
        "handle": payload.get("handle"),
        "ldhName": payload.get("ldhName"),
        "unicodeName": payload.get("unicodeName"),
        "status": payload.get("status", []),
        "events": events,
        "nameservers": nameservers,
        "links": payload.get("links", []),
        "raw": payload,
    }


def run_whois(domain: str, timeout: float) -> dict[str, Any]:
    try:
        proc = subprocess.run(
            ["whois", domain],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except FileNotFoundError:
        return {"available": False, "error": "whois command not found on system."}
    except subprocess.TimeoutExpired:
        return {"available": False, "error": "whois lookup timed out."}

    return {
        "available": proc.returncode == 0,
        "exitCode": proc.returncode,
        "stdout": proc.stdout,
        "stderr": proc.stderr,
    }


def socket_ip_resolution(domain: str) -> dict[str, Any]:
    results: dict[str, list[str]] = {"A": [], "AAAA": []}
    try:
        info = socket.getaddrinfo(domain, None)
    except socket.gaierror as exc:
        return {"available": False, "error": str(exc), "records": results}

    for row in info:
        family, _, _, _, sockaddr = row
        if family == socket.AF_INET:
            ip = sockaddr[0]
            if ip not in results["A"]:
                results["A"].append(ip)
        elif family == socket.AF_INET6:
            ip = sockaddr[0]
            if ip not in results["AAAA"]:
                results["AAAA"].append(ip)

    return {"available": True, "records": results}


def resolve_dns_records(domain: str, timeout: float) -> tuple[dict[str, Any], list[str]]:
    errors: list[str] = []
    data: dict[str, Any] = {"resolver": "socket-fallback", "records": {}, "errors": []}

    try:
        import dns.resolver  # type: ignore
    except Exception:
        fallback = socket_ip_resolution(domain)
        data["records"] = fallback.get("records", {})
        if not fallback.get("available"):
            errors.append(fallback.get("error", "Socket fallback failed."))
        data["errors"] = errors
        data["note"] = "Install dnspython for full DNS record coverage: pip install dnspython"
        return data, errors

    resolver = dns.resolver.Resolver()  # type: ignore[name-defined]
    resolver.lifetime = timeout
    resolver.timeout = timeout
    data["resolver"] = "dnspython"

    records: dict[str, list[str]] = {}
    for record_type in DEFAULT_RECORD_TYPES:
        try:
            answers = resolver.resolve(domain, record_type)
            records[record_type] = [r.to_text() for r in answers]
        except Exception as exc:
            errors.append(f"{record_type}: {exc}")

    # DMARC check
    try:
        dmarc_answers = resolver.resolve(f"_dmarc.{domain}", "TXT")
        records["DMARC"] = [r.to_text() for r in dmarc_answers]
    except Exception:
        records["DMARC"] = []

    # SPF extraction from TXT
    txt_values = records.get("TXT", [])
    records["SPF"] = [txt for txt in txt_values if "v=spf1" in txt.lower()]

    data["records"] = records
    data["errors"] = errors
    return data, errors


def reverse_dns(ip: str) -> dict[str, Any]:
    try:
        hostname, aliases, _ = socket.gethostbyaddr(ip)
        return {"ip": ip, "hostname": hostname, "aliases": aliases}
    except Exception as exc:
        return {"ip": ip, "error": str(exc)}


def ip_details_from_dns(dns_records: dict[str, Any]) -> list[dict[str, Any]]:
    collected: list[str] = []
    for key in ("A", "AAAA"):
        for ip in dns_records.get("records", {}).get(key, []):
            if ip not in collected:
                collected.append(ip)

    details = []
    for ip in collected:
        item = reverse_dns(ip)
        try:
            parsed = ipaddress.ip_address(ip)
            item["version"] = parsed.version
            item["isPrivate"] = parsed.is_private
            item["isGlobal"] = parsed.is_global
        except ValueError:
            pass
        details.append(item)
    return details


def tls_certificate_info(domain: str, timeout: float) -> dict[str, Any]:
    context = ssl.create_default_context()
    try:
        with socket.create_connection((domain, 443), timeout=timeout) as sock:
            with context.wrap_socket(sock, server_hostname=domain) as wrapped:
                cert = wrapped.getpeercert()
                cipher = wrapped.cipher()
                version = wrapped.version()
    except Exception as exc:
        return {"available": False, "error": str(exc)}

    return {
        "available": True,
        "subject": cert.get("subject"),
        "issuer": cert.get("issuer"),
        "serialNumber": cert.get("serialNumber"),
        "notBefore": cert.get("notBefore"),
        "notAfter": cert.get("notAfter"),
        "subjectAltName": cert.get("subjectAltName", []),
        "ocsp": cert.get("OCSP", []),
        "caIssuers": cert.get("caIssuers", []),
        "crlDistributionPoints": cert.get("crlDistributionPoints", []),
        "tlsVersion": version,
        "cipher": cipher,
    }


def http_probe(domain: str, timeout: float) -> dict[str, Any]:
    results: dict[str, Any] = {}

    for scheme in ("https", "http"):
        url = f"{scheme}://{domain}"
        request = urllib.request.Request(
            url,
            method="GET",
            headers={
                "User-Agent": "dnschecker/1.0",
                "Accept": "*/*",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                headers = dict(response.getheaders())
                results[scheme] = {
                    "reachable": True,
                    "status": response.status,
                    "finalUrl": response.geturl(),
                    "headers": headers,
                }
        except urllib.error.HTTPError as exc:
            results[scheme] = {
                "reachable": True,
                "status": exc.code,
                "finalUrl": exc.geturl(),
                "headers": dict(exc.headers.items()) if exc.headers else {},
                "error": str(exc),
            }
        except Exception as exc:
            results[scheme] = {
                "reachable": False,
                "error": str(exc),
            }

    return results


def build_report(domain: str, timeout: float, include_whois: bool) -> dict[str, Any]:
    started = time.time()

    dns_data, dns_errors = resolve_dns_records(domain, timeout)
    report: dict[str, Any] = {
        "meta": {
            "queriedAt": utc_now_iso(),
            "domain": domain,
            "timeoutSeconds": timeout,
            "python": sys.version,
        },
        "dns": dns_data,
        "ips": ip_details_from_dns(dns_data),
        "rdap": fetch_rdap(domain, timeout),
        "tls": tls_certificate_info(domain, timeout),
        "http": http_probe(domain, timeout),
    }

    if include_whois:
        report["whois"] = run_whois(domain, timeout)
    else:
        report["whois"] = {"available": False, "note": "Disabled by --no-whois"}

    elapsed = time.time() - started
    report["meta"]["durationSeconds"] = round(elapsed, 3)
    report["meta"]["errors"] = dns_errors
    return report


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Gather broad, publicly available intelligence for a domain."
    )
    parser.add_argument("domain", help="Domain to inspect (e.g. example.com)")
    parser.add_argument(
        "--timeout",
        type=float,
        default=8.0,
        help="Network timeout in seconds for each lookup (default: 8)",
    )
    parser.add_argument(
        "--no-whois",
        action="store_true",
        help="Skip WHOIS CLI lookup",
    )
    parser.add_argument(
        "--output",
        help="Optional output JSON file path. If omitted, prints to stdout.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        domain = normalize_domain(args.domain)
    except ValueError as exc:
        print(f"Input error: {exc}", file=sys.stderr)
        return 2

    report = build_report(domain, args.timeout, include_whois=not args.no_whois)
    rendered = json.dumps(report, indent=2, ensure_ascii=False)

    if args.output:
        with open(args.output, "w", encoding="utf-8") as handle:
            handle.write(rendered)
            handle.write("\n")
        print(f"Wrote report to {args.output}")
    else:
        print(rendered)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())