from cryptography import x509
from cryptography.x509.oid import NameOID
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.backends import default_backend
import datetime, ipaddress, os

HOST_IP = "192.168.225.196"   # 改成你当前的实际 IP
VALID_DAYS = 3650

# 生成 CA
ca_key = rsa.generate_private_key(public_exponent=65537, key_size=2048, backend=default_backend())
ca_subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, u"WordPoP Local CA")])
ca_cert = (
    x509.CertificateBuilder()
    .subject_name(ca_subject)
    .issuer_name(ca_subject)
    .public_key(ca_key.public_key())
    .serial_number(x509.random_serial_number())
    .not_valid_before(datetime.datetime.utcnow())
    .not_valid_after(datetime.datetime.utcnow() + datetime.timedelta(days=VALID_DAYS))
    .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
    .sign(ca_key, hashes.SHA256(), default_backend())
)

# 生成服务器证书
server_key = rsa.generate_private_key(public_exponent=65537, key_size=2048, backend=default_backend())
server_subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, HOST_IP)])
san = x509.SubjectAlternativeName([x509.IPAddress(ipaddress.IPv4Address(HOST_IP))])
server_cert = (
    x509.CertificateBuilder()
    .subject_name(server_subject)
    .issuer_name(ca_subject)
    .public_key(server_key.public_key())
    .serial_number(x509.random_serial_number())
    .not_valid_before(datetime.datetime.utcnow())
    .not_valid_after(datetime.datetime.utcnow() + datetime.timedelta(days=VALID_DAYS))
    .add_extension(san, critical=False)
    .sign(ca_key, hashes.SHA256(), default_backend())
)

# 写入文件
with open("ca.key", "wb") as f:
    f.write(ca_key.private_bytes(encoding=serialization.Encoding.PEM,
                                 format=serialization.PrivateFormat.TraditionalOpenSSL,
                                 encryption_algorithm=serialization.NoEncryption()))
with open("ca.crt", "wb") as f:
    f.write(ca_cert.public_bytes(serialization.Encoding.PEM))
with open("server.key", "wb") as f:
    f.write(server_key.private_bytes(encoding=serialization.Encoding.PEM,
                                     format=serialization.PrivateFormat.TraditionalOpenSSL,
                                     encryption_algorithm=serialization.NoEncryption()))
with open("server.crt", "wb") as f:
    f.write(server_cert.public_bytes(serialization.Encoding.PEM))

print("✅ 证书生成完成！")
print("  - ca.crt：安装到平板信任列表")
print("  - server.crt + server.key：用于 Flask")