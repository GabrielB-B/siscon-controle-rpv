import argparse
import ipaddress
import socket
from datetime import datetime, timedelta, timezone
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID


ROOT_CERT_NAME = "controle_rpv_local_ca.crt"
ROOT_KEY_NAME = "controle_rpv_local_ca.key"
SERVER_CERT_NAME = "controle_rpv_local.crt"
SERVER_KEY_NAME = "controle_rpv_local.key"


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _normalize_hosts(values: list[str]) -> tuple[list[str], list[x509.GeneralName]]:
    normalized: list[str] = []
    san_entries: list[x509.GeneralName] = []
    seen: set[str] = set()

    for raw in values:
        value = str(raw or "").strip()
        if not value:
            continue
        key = value.lower()
        if key in seen:
            continue
        seen.add(key)
        normalized.append(value)
        try:
            san_entries.append(x509.IPAddress(ipaddress.ip_address(value)))
        except ValueError:
            san_entries.append(x509.DNSName(value))

    if "localhost" not in seen:
        normalized.append("localhost")
        san_entries.append(x509.DNSName("localhost"))

    if "127.0.0.1" not in seen:
        normalized.append("127.0.0.1")
        san_entries.append(x509.IPAddress(ipaddress.ip_address("127.0.0.1")))

    hostname = socket.gethostname().strip()
    if hostname and hostname.lower() not in seen:
        normalized.append(hostname)
        san_entries.append(x509.DNSName(hostname))

    return normalized, san_entries


def _write_private_key(path: Path, key: rsa.RSAPrivateKey) -> None:
    path.write_bytes(
        key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.TraditionalOpenSSL,
            encryption_algorithm=serialization.NoEncryption(),
        )
    )


def _write_cert(path: Path, cert: x509.Certificate) -> None:
    path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))


def _load_private_key(path: Path) -> rsa.RSAPrivateKey:
    return serialization.load_pem_private_key(path.read_bytes(), password=None)


def _load_cert(path: Path) -> x509.Certificate:
    return x509.load_pem_x509_certificate(path.read_bytes())


def _ensure_root_ca(cert_dir: Path) -> tuple[x509.Certificate, rsa.RSAPrivateKey]:
    root_cert_path = cert_dir / ROOT_CERT_NAME
    root_key_path = cert_dir / ROOT_KEY_NAME

    if root_cert_path.exists() and root_key_path.exists():
        return _load_cert(root_cert_path), _load_private_key(root_key_path)

    now = _utc_now()
    root_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = x509.Name(
        [
            x509.NameAttribute(NameOID.ORGANIZATION_NAME, "Controle RPV"),
            x509.NameAttribute(NameOID.COMMON_NAME, "Controle RPV Local Root CA"),
        ]
    )

    root_cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(subject)
        .public_key(root_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=5))
        .not_valid_after(now + timedelta(days=3650))
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
        .add_extension(
            x509.KeyUsage(
                digital_signature=False,
                content_commitment=False,
                key_encipherment=False,
                data_encipherment=False,
                key_agreement=False,
                key_cert_sign=True,
                crl_sign=True,
                encipher_only=False,
                decipher_only=False,
            ),
            critical=True,
        )
        .add_extension(
            x509.SubjectKeyIdentifier.from_public_key(root_key.public_key()),
            critical=False,
        )
        .sign(root_key, hashes.SHA256())
    )

    _write_private_key(root_key_path, root_key)
    _write_cert(root_cert_path, root_cert)
    return root_cert, root_key


def _build_server_cert(
    hosts: list[str],
    san_entries: list[x509.GeneralName],
    issuer_cert: x509.Certificate,
    issuer_key: rsa.RSAPrivateKey,
) -> tuple[x509.Certificate, rsa.RSAPrivateKey]:
    now = _utc_now()
    server_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    common_name = hosts[0]
    subject = x509.Name(
        [
            x509.NameAttribute(NameOID.ORGANIZATION_NAME, "Controle RPV"),
            x509.NameAttribute(NameOID.COMMON_NAME, common_name),
        ]
    )

    server_cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer_cert.subject)
        .public_key(server_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=5))
        .not_valid_after(now + timedelta(days=825))
        .add_extension(x509.SubjectAlternativeName(san_entries), critical=False)
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(
            x509.KeyUsage(
                digital_signature=True,
                content_commitment=False,
                key_encipherment=True,
                data_encipherment=False,
                key_agreement=False,
                key_cert_sign=False,
                crl_sign=False,
                encipher_only=False,
                decipher_only=False,
            ),
            critical=True,
        )
        .add_extension(
            x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]),
            critical=False,
        )
        .add_extension(
            x509.SubjectKeyIdentifier.from_public_key(server_key.public_key()),
            critical=False,
        )
        .add_extension(
            x509.AuthorityKeyIdentifier.from_issuer_public_key(issuer_key.public_key()),
            critical=False,
        )
        .sign(issuer_key, hashes.SHA256())
    )

    return server_cert, server_key


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Gera CA local e certificado HTTPS para uso interno do Controle RPV."
    )
    parser.add_argument(
        "--cert-dir",
        default="instance/certs",
        help="Pasta onde os certificados serao salvos.",
    )
    parser.add_argument(
        "--host",
        action="append",
        default=[],
        help="Hostname ou IP que sera incluido no certificado. Pode repetir.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Regenera o certificado do servidor mesmo que ele ja exista.",
    )
    args = parser.parse_args()

    cert_dir = Path(args.cert_dir).resolve()
    cert_dir.mkdir(parents=True, exist_ok=True)

    hosts, san_entries = _normalize_hosts(args.host)
    root_cert, root_key = _ensure_root_ca(cert_dir)

    server_cert_path = cert_dir / SERVER_CERT_NAME
    server_key_path = cert_dir / SERVER_KEY_NAME

    if args.force or not server_cert_path.exists() or not server_key_path.exists():
        server_cert, server_key = _build_server_cert(hosts, san_entries, root_cert, root_key)
        _write_private_key(server_key_path, server_key)
        _write_cert(server_cert_path, server_cert)

    print(f"CA_ROOT_CERT={cert_dir / ROOT_CERT_NAME}")
    print(f"CA_ROOT_KEY={cert_dir / ROOT_KEY_NAME}")
    print(f"SERVER_CERT={server_cert_path}")
    print(f"SERVER_KEY={server_key_path}")
    print(f"HOSTS={', '.join(hosts)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
