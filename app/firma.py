"""Firma Ed25519 de la cabeza del registro sellado. Quien guarde una cabeza firmada puede comprobar después
que ninguna fila anterior cambió, aunque alguien con acceso a la base recalcule toda la cadena."""
import base64
import json

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey

from app import config


def llave() -> Ed25519PrivateKey:
    if valor := config.sello_llave():
        return Ed25519PrivateKey.from_private_bytes(base64.b64decode(valor))
    # shortcut: sin SELLO_LLAVE la llave vive en DATA_DIR y cambia si el disco se borra; en producción, definir SELLO_LLAVE
    archivo = config.data_dir() / "sello.key"
    if not archivo.exists():
        archivo.write_bytes(Ed25519PrivateKey.generate().private_bytes_raw())
        archivo.chmod(0o600)
    return Ed25519PrivateKey.from_private_bytes(archivo.read_bytes())


def b64(datos: bytes) -> str:
    return base64.b64encode(datos).decode()


def mensaje_de(cabeza: dict) -> str:
    """JSON canónico de la cabeza ({"id", "hash"}): lo que se firma y lo que se ancla en Bitcoin."""
    return json.dumps(cabeza, separators=(",", ":"), sort_keys=True)


def firmar_cabeza(cabeza: dict) -> dict:
    """Firma el JSON canónico de la cabeza y devuelve el mensaje exacto, la firma y la llave pública."""
    mensaje = mensaje_de(cabeza)
    k = llave()
    return {"algoritmo": "Ed25519", "mensaje": mensaje, "firma": b64(k.sign(mensaje.encode())),
            "llave_publica": b64(k.public_key().public_bytes_raw())}


def verificar_firma(mensaje: str, firma: str, llave_publica: str) -> bool:
    try:
        Ed25519PublicKey.from_public_bytes(base64.b64decode(llave_publica)).verify(base64.b64decode(firma), mensaje.encode())
        return True
    except Exception:
        return False
