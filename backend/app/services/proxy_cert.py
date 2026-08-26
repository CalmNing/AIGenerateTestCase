"""Mock 代理 HTTPS MITM 证书模块。

首次调用时自动生成自签名 CA（私钥 + 证书落盘 backend/data/certs/），
之后按主机名动态签发叶子证书（内存 + 磁盘双层缓存）。
用户需手动将 ca.pem 导入系统/浏览器信任区后，MITM 解密才不会触发证书告警。
"""
import datetime
import ipaddress
import logging
import os
import threading
from typing import Optional

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

logger = logging.getLogger(__name__)

# 证书存放目录：backend/data/certs/
CERTS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "data", "certs")
CA_KEY_FILE = os.path.join(CERTS_DIR, "ca.key")
CA_CERT_FILE = os.path.join(CERTS_DIR, "ca.pem")

CA_SUBJECT_CN = "AI Testcase Mock Proxy CA"
CA_VALID_DAYS = 3650
LEAF_VALID_DAYS = 365

_lock = threading.Lock()
_ca_key: Optional[rsa.RSAPrivateKey] = None
_ca_cert: Optional[x509.Certificate] = None
# 主机 -> PEM 字符串（含证书+私钥），进程内缓存
_leaf_cache: dict[str, str] = {}


def _generate_key() -> rsa.RSAPrivateKey:
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


def _build_name(cn: str, is_ca: bool) -> x509.Name:
    return x509.Name([
        x509.NameAttribute(NameOID.COMMON_NAME, cn),
        x509.NameAttribute(NameOID.ORGANIZATION_NAME, "AI Testcase Tool"),
    ])


def ensure_ca() -> tuple[rsa.RSAPrivateKey, x509.Certificate]:
    """加载或生成 CA 私钥与证书。线程安全，进程内单例。"""
    global _ca_key, _ca_cert
    if _ca_key is not None and _ca_cert is not None:
        return _ca_key, _ca_cert

    with _lock:
        if _ca_key is not None and _ca_cert is not None:
            return _ca_key, _ca_cert
        if os.path.exists(CA_KEY_FILE) and os.path.exists(CA_CERT_FILE):
            try:
                with open(CA_KEY_FILE, "rb") as f:
                    _ca_key = serialization.load_pem_private_key(f.read(), password=None)
                with open(CA_CERT_FILE, "rb") as f:
                    _ca_cert = x509.load_pem_x509_certificate(f.read())
                # 已有 CA 且未过期则直接复用
                if _ca_cert.not_valid_after.replace(tzinfo=None) > datetime.datetime.utcnow():
                    return _ca_key, _ca_cert
                logger.warning("Mock 代理 CA 证书已过期，重新生成")
            except Exception as exc:
                logger.warning("加载 Mock 代理 CA 失败，将重新生成: %s", exc)

        os.makedirs(CERTS_DIR, exist_ok=True)
        key = _generate_key()
        subject = issuer = _build_name(CA_SUBJECT_CN, is_ca=True)
        now = datetime.datetime.utcnow()
        cert = (
            x509.CertificateBuilder()
            .subject_name(subject)
            .issuer_name(issuer)
            .public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - datetime.timedelta(days=1))
            .not_valid_after(now + datetime.timedelta(days=CA_VALID_DAYS))
            .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
            .sign(key, hashes.SHA256())
        )
        with open(CA_KEY_FILE, "wb") as f:
            f.write(key.private_bytes(
                encoding=serialization.Encoding.PEM,
                format=serialization.PrivateFormat.TraditionalOpenSSL,
                encryption_algorithm=serialization.NoEncryption(),
            ))
        with open(CA_CERT_FILE, "wb") as f:
            f.write(cert.public_bytes(serialization.Encoding.PEM))
        _ca_key, _ca_cert = key, cert
        logger.info("已生成 Mock 代理 CA 证书: %s", CA_CERT_FILE)
        return _ca_key, _ca_cert


def load_ca_cert_pem() -> bytes:
    """读取 CA 证书 PEM 内容（供下载接口）。不存在时先触发生成。"""
    ensure_ca()
    with open(CA_CERT_FILE, "rb") as f:
        return f.read()


def get_cert_for_host(host: str) -> str:
    """获取指定主机的叶子证书 PEM（证书+私钥拼接），带内存与磁盘缓存。"""
    host = (host or "").strip().lower().rstrip(".")
    if not host:
        raise ValueError("host 不能为空")

    cached = _leaf_cache.get(host)
    if cached:
        return cached

    ca_key, ca_cert = ensure_ca()

    disk_path = os.path.join(CERTS_DIR, f"{host}.pem")
    if os.path.exists(disk_path):
        try:
            with open(disk_path, "r", encoding="utf-8") as f:
                pem = f.read()
            # 校验未过期
            cert = x509.load_pem_x509_certificate(pem.encode())
            if cert.not_valid_after.replace(tzinfo=None) > datetime.datetime.utcnow():
                _leaf_cache[host] = pem
                return pem
        except Exception as exc:
            logger.warning("读取主机 %s 缓存证书失败，将重新签发: %s", host, exc)

    key = _generate_key()
    now = datetime.datetime.utcnow()
    builder = (
        x509.CertificateBuilder()
        .subject_name(_build_name(host, is_ca=False))
        .issuer_name(ca_cert.subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(days=1))
        .not_valid_after(now + datetime.timedelta(days=LEAF_VALID_DAYS))
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(
            x509.SubjectAlternativeName([x509.DNSName(host)]),
            critical=False,
        )
    )
    # IP 形式的主机补充 IP SAN
    try:
        ip = ipaddress.ip_address(host)
        builder = builder.add_extension(
            x509.SubjectAlternativeName([x509.DNSName(host), x509.IPAddress(ip)]),
            critical=False,
        )
    except ValueError:
        pass

    cert = builder.sign(ca_key, hashes.SHA256())
    pem = (
        cert.public_bytes(serialization.Encoding.PEM).decode()
        + key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.TraditionalOpenSSL,
            encryption_algorithm=serialization.NoEncryption(),
        ).decode()
    )

    try:
        os.makedirs(CERTS_DIR, exist_ok=True)
        with open(disk_path, "w", encoding="utf-8") as f:
            f.write(pem)
    except Exception as exc:
        logger.warning("写入主机 %s 证书缓存失败（忽略）: %s", host, exc)

    _leaf_cache[host] = pem
    return pem
