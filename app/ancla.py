"""Anclaje de la cabeza del registro en Bitcoin con OpenTimestamps.

La firma Ed25519 prueba quién firmó; el anclaje prueba CUÁNDO existía esa cabeza, sin confiar en nosotros: los
calendarios públicos agregan el hash en una transacción de Bitcoin. Quien tenga la base y la llave puede reescribir
la historia y volver a firmar, pero no puede fabricar una prueba anclada con fecha anterior para la cabeza nueva.
Solo sale el sha256 de la cabeza (32 bytes); ni datos de facturas ni billetera ni dinero.
"""
import hashlib
import logging

import httpx

from app import firma, registro

CALENDARIOS = ("https://a.pool.opentimestamps.org", "https://b.pool.opentimestamps.org",
               "https://a.pool.eternitywall.com")
MAGIA = b"\x00OpenTimestamps\x00\x00Proof\x00\xbf\x89\xe2\xe8\x84\xe8\x92\x94"  # cabecera de un archivo .ots


def sellar(mensaje: bytes) -> tuple[bytes, list[str]]:
    """Envía sha256(mensaje) a los calendarios y arma el archivo .ots (versión 1, sha256) con una rama por calendario."""
    digest = hashlib.sha256(mensaje).digest()
    ramas: list[tuple[str, bytes]] = []
    for url in CALENDARIOS:
        try:
            r = httpx.post(f"{url}/digest", content=digest, timeout=15,
                           headers={"Accept": "application/vnd.opentimestamps.v1", "User-Agent": "mandato-blinka"})
            r.raise_for_status()
            ramas.append((url, r.content))
        except httpx.HTTPError as e:
            logging.warning("calendario %s no respondió: %r", url, e)
    # cada respuesta es un árbol para el digest; 0xff abre una bifurcación y la rama que empieza con 0xff va al final
    ramas.sort(key=lambda x: x[1][:1] == b"\xff")
    ramas = [x for i, x in enumerate(ramas) if x[1][:1] != b"\xff" or i == len(ramas) - 1]
    if not ramas:
        raise RuntimeError("ningún calendario de OpenTimestamps respondió")
    cuerpo = b"".join(b"\xff" + c for _, c in ramas[:-1]) + ramas[-1][1]
    return MAGIA + b"\x01\x08" + digest + cuerpo, [u for u, _ in ramas]


def anclar_cabeza(db=None) -> dict | None:
    """Ancla la cabeza actual si la cadena está íntegra y cambió desde el último anclaje. Devuelve el anclaje vigente."""
    cabeza = registro.verificar(db)["cabeza"]
    if cabeza is None:
        return None
    ultimo = registro.anclajes(1, db)
    if ultimo and ultimo[0]["hash"] == cabeza["hash"]:
        return ultimo[0]
    mensaje = firma.mensaje_de(cabeza)
    ots, calendarios = sellar(mensaje.encode())
    return registro.insertar_anclaje(cabeza, mensaje, ots, calendarios, db)
