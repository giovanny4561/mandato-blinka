"""Anclaje de la cabeza del registro en Bitcoin con OpenTimestamps.

La firma Ed25519 prueba quién firmó; el anclaje prueba CUÁNDO existía esa cabeza, sin confiar en nosotros: los
calendarios públicos agregan el hash en una transacción de Bitcoin. Quien tenga la base y la llave puede reescribir
la historia y volver a firmar, pero no puede fabricar una prueba anclada con fecha anterior para la cabeza nueva.
Solo sale el sha256 de la cabeza (32 bytes); ni datos de facturas ni billetera ni dinero.
"""
import base64
import hashlib
import logging

import httpx

from app import firma, registro

CALENDARIOS = ("https://a.pool.opentimestamps.org", "https://b.pool.opentimestamps.org",
               "https://a.pool.eternitywall.com")
MAGIA = b"\x00OpenTimestamps\x00\x00Proof\x00\xbf\x89\xe2\xe8\x84\xe8\x92\x94"  # cabecera de un archivo .ots
PENDIENTE = bytes.fromhex("83dfe30d2ef90c8e")  # atestación "pendiente en el calendario <uri>"
BITCOIN = bytes.fromhex("0588960d73d71901")  # atestación "incluida en el bloque <altura> de Bitcoin"


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


def _varuint(b: bytes, i: int) -> tuple[int, int]:
    v = s = 0
    while True:
        x = b[i]
        i, v, s = i + 1, v | (x & 0x7F) << s, s + 7
        if not x & 0x80:
            return v, i


def _varbytes(b: bytes, i: int) -> tuple[bytes, int]:
    n, i = _varuint(b, i)
    return b[i:i + n], i + n


def _rama(b: bytes, i: int, msg: bytes):
    """Recorre una rama lineal (append, prepend, sha256 y una atestación) desde b[i]. None si bifurca o no la conoce.
    Devuelve (inicio de la atestación, mensaje en ese punto, etiqueta, carga, fin de la rama)."""
    while True:
        t = b[i]
        if t == 0x00:
            carga, fin = _varbytes(b, i + 9)
            return i, msg, b[i + 1:i + 9], carga, fin
        if t == 0x08:
            msg, i = hashlib.sha256(msg).digest(), i + 1
        elif t in (0xF0, 0xF1):
            arg, i = _varbytes(b, i + 1)
            msg = msg + arg if t == 0xF0 else arg + msg
        else:
            return None


def _bloque(nodo: bytes) -> int | None:
    k = nodo.find(b"\x00" + BITCOIN)
    return _varuint(_varbytes(nodo, k + 9)[0], 0)[0] if k >= 0 else None


def completar(ots: bytes) -> tuple[bytes, int | None]:
    """Pide a cada calendario la versión confirmada en Bitcoin de su rama pendiente y la guarda en el .ots, que así
    se verifica sin depender del calendario. Devuelve (.ots, altura del bloque o None si sigue pendiente)."""
    inicio = len(MAGIA) + 2
    digest, i = ots[inicio:inicio + 32], inicio + 32
    ramas, bloque = [], None
    while True:
        bifurca = ots[i] == 0xFF
        r = _rama(ots, i + bifurca, digest)
        if r is None:
            return ots, None  # estructura que no armó sellar(): no se toca
        att, msg, etiqueta, carga, fin = r
        rama = ots[i + bifurca:fin]
        if etiqueta == PENDIENTE:
            uri = _varbytes(carga, 0)[0].decode()
            try:
                resp = httpx.get(f"{uri}/timestamp/{msg.hex()}", timeout=15,
                                 headers={"Accept": "application/vnd.opentimestamps.v1", "User-Agent": "mandato-blinka"})
                if resp.status_code == 200 and (b := _bloque(resp.content)) is not None:
                    rama, bloque = ots[i + bifurca:att] + resp.content, min(bloque or b, b)
            except httpx.HTTPError as e:
                logging.warning("calendario %s no respondió al completar: %r", uri, e)
        elif etiqueta == BITCOIN:
            b = _varuint(carga, 0)[0]
            bloque = min(bloque or b, b)
        ramas.append(rama)
        i = fin
        if not bifurca:
            break
    return ots[:inicio + 32] + b"".join(b"\xff" + x for x in ramas[:-1]) + ramas[-1], bloque


def completar_pendientes(db=None) -> int:
    """Completa los anclajes que siguen sin bloque de Bitcoin. Devuelve cuántos quedaron confirmados."""
    hechos = 0
    for a in registro.anclajes(20, db):
        if a["bloque"] is None:
            ots, bloque = completar(base64.b64decode(a["ots_b64"]))
            if bloque is not None:
                registro.confirmar_anclaje(a["id"], ots, bloque, db)
                hechos += 1
    return hechos


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
