"""Rieles de pago de Blinka (blinka.co, octubre de 2026) y validación de los datos del beneficiario por riel.

Cada riel dice en qué países y monedas paga y qué dato identifica al beneficiario. Los validadores siguen los estándares
públicos: dígitos de control de ABA, CLABE, CBU/CVU, IBAN, CPF y CNPJ; formato de BIC, llaves Bre-B, chaves Pix y
direcciones de USDC por red. PSE (Colombia) solo sirve para recibir y la tarjeta Visa es para gastos: ninguno paga
facturas. Nada aquí habla con Blinka: su API está en acceso anticipado y no es pública, así que la orden es simulada.
"""
from __future__ import annotations

import re
from datetime import date, datetime, timezone
from xml.etree import ElementTree as ET

SEPA = {"AT", "BE", "BG", "CH", "CY", "CZ", "DE", "DK", "EE", "ES", "FI", "FR", "GB", "GR", "HR", "HU", "IE", "IS",
        "IT", "LI", "LT", "LU", "LV", "MC", "MT", "NL", "NO", "PL", "PT", "RO", "SE", "SI", "SK", "SM", "VA", "AD"}
REDES_USDC = {"base", "ethereum", "polygon", "celo", "solana"}


def _digitos(s: str | None) -> str:
    return re.sub(r"[\s./\-]", "", s or "")


def aba(r: str | None) -> bool:
    """Número de ruta ABA (EE. UU.): 9 dígitos, 3·(d1+d4+d7) + 7·(d2+d5+d8) + (d3+d6+d9) múltiplo de 10."""
    d = _digitos(r)
    if not re.fullmatch(r"\d{9}", d):
        return False
    n = [int(x) for x in d]
    return (3 * (n[0] + n[3] + n[6]) + 7 * (n[1] + n[4] + n[7]) + n[2] + n[5] + n[8]) % 10 == 0


def clabe(c: str | None) -> bool:
    """CLABE (México, SPEI): 18 dígitos; el último es control con pesos 3, 7, 1."""
    d = _digitos(c)
    if not re.fullmatch(r"\d{18}", d):
        return False
    suma = sum(int(x) * (3, 7, 1)[i % 3] % 10 for i, x in enumerate(d[:17]))
    return (10 - suma % 10) % 10 == int(d[17])


def cbu(c: str | None) -> bool:
    """CBU o CVU (Argentina): 22 dígitos en dos bloques, cada uno con su dígito de control."""
    d = _digitos(c)
    if not re.fullmatch(r"\d{22}", d):
        return False

    def control(bloque: str, pesos: tuple[int, ...]) -> int:
        return (10 - sum(int(x) * p for x, p in zip(bloque, pesos)) % 10) % 10
    return (control(d[:7], (7, 1, 3, 9, 7, 1, 3)) == int(d[7])
            and control(d[8:21], (3, 9, 7, 1, 3, 9, 7, 1, 3, 9, 7, 1, 3)) == int(d[21]))


def iban(s: str | None, pais: str | None = None) -> bool:
    """IBAN: dos letras de país, dos de control y el resto; el número reordenado módulo 97 da 1."""
    t = re.sub(r"\s", "", s or "").upper()
    if not re.fullmatch(r"[A-Z]{2}\d{2}[A-Z0-9]{10,30}", t) or (pais and t[:2] != pais):
        return False
    return int("".join(str(int(ch, 36)) for ch in t[4:] + t[:4])) % 97 == 1


def bic(b: str | None) -> bool:
    return bool(re.fullmatch(r"[A-Z]{4}[A-Z]{2}[A-Z0-9]{2}([A-Z0-9]{3})?", (b or "").upper()))


def _cpf(d: str) -> bool:
    if not re.fullmatch(r"\d{11}", d) or len(set(d)) == 1:
        return False
    for k in (9, 10):
        if (sum(int(d[i]) * (k + 1 - i) for i in range(k)) * 10 % 11) % 10 != int(d[k]):
            return False
    return True


def _cnpj(d: str) -> bool:
    if not re.fullmatch(r"\d{14}", d) or len(set(d)) == 1:
        return False
    for k, pesos in ((12, (5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2)), (13, (6, 5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2))):
        r = sum(int(d[i]) * pesos[i] for i in range(k)) % 11
        if (0 if r < 2 else 11 - r) != int(d[k]):
            return False
    return True


_CORREO = re.compile(r"[^@\s]+@[^@\s]+\.[A-Za-z]{2,}")


def chave_pix(c: str | None) -> bool:
    """Chave Pix (Brasil): CPF, CNPJ, correo, teléfono +55 o chave aleatória (UUID)."""
    c = (c or "").strip()
    d = _digitos(c)
    return (_cpf(d) or _cnpj(d) or bool(_CORREO.fullmatch(c)) or bool(re.fullmatch(r"\+55\d{10,11}", c))
            or bool(re.fullmatch(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", c.lower())))


def llave_breb(c: str | None) -> bool:
    """Llave Bre-B (Colombia): celular, documento (cédula o NIT), correo o alfanumérica que empieza con @."""
    c = (c or "").strip()
    d = _digitos(c)
    return (bool(re.fullmatch(r"(\+?57)?3\d{9}", d)) or bool(re.fullmatch(r"\d{6,10}", d))
            or bool(_CORREO.fullmatch(c)) or bool(re.fullmatch(r"@[A-Za-z0-9]{3,30}", c)))


def billetera(direccion: str | None, red: str | None) -> bool:
    if red == "solana":
        return bool(re.fullmatch(r"[1-9A-HJ-NP-Za-km-z]{32,44}", direccion or ""))
    return bool(re.fullmatch(r"0x[0-9a-fA-F]{40}", direccion or ""))


def _cuenta(n_min: int, n_max: int):
    return lambda c: bool(re.fullmatch(rf"\d{{{n_min},{n_max}}}", _digitos(c)))


# riel → nombre, países (None = cualquiera), monedas, validador de la cuenta, qué es `codigo_banco` y su validador
RIELES: dict[str, dict] = {
    "ach": dict(nombre="ACH", paises={"US"}, monedas={"USD"}, cuenta=_cuenta(4, 17), banco=("número de ruta ABA", aba)),
    "wire": dict(nombre="Wire", paises={"US"}, monedas={"USD"}, cuenta=_cuenta(4, 17), banco=("número de ruta ABA", aba)),
    "swift": dict(nombre="SWIFT", paises=None, monedas={"USD"},
                  cuenta=lambda c: iban(c) or bool(re.fullmatch(r"[A-Za-z0-9]{5,34}", _digitos(c))), banco=("BIC", bic)),
    "sepa": dict(nombre="SEPA", paises=SEPA, monedas={"EUR"}, cuenta=iban, banco=None),
    "bre_b": dict(nombre="Bre-B", paises={"CO"}, monedas={"COP"}, cuenta=llave_breb, banco=None),
    "pix": dict(nombre="Pix", paises={"BR"}, monedas={"BRL"}, cuenta=chave_pix, banco=None),
    "ted": dict(nombre="TED", paises={"BR"}, monedas={"BRL"}, cuenta=_cuenta(5, 20),
                banco=("código del banco (3 dígitos)", lambda b: bool(re.fullmatch(r"\d{3}", b or "")))),
    "spei": dict(nombre="SPEI", paises={"MX"}, monedas={"MXN"}, cuenta=clabe, banco=None),
    "cbu": dict(nombre="CBU / CVU / Alias", paises={"AR"}, monedas={"ARS"},
                cuenta=lambda c: cbu(c) or bool(re.fullmatch(r"[A-Za-z0-9.\-]{6,20}", c or "")) and not _digitos(c).isdigit(),
                banco=None),
    "local_cl": dict(nombre="Transferencia bancaria (Chile)", paises={"CL"}, monedas={"CLP"}, cuenta=_cuenta(6, 20), banco=None),
    # shortcut: el CCI se valida por largo (20 dígitos), no por sus dígitos de control; agregarlos si se paga mucho a Perú
    "local_pe": dict(nombre="Transferencia bancaria (Perú, CCI)", paises={"PE"}, monedas={"PEN"}, cuenta=_cuenta(20, 20), banco=None),
    "local_bo": dict(nombre="Transferencia bancaria (Bolivia)", paises={"BO"}, monedas={"BOB"}, cuenta=_cuenta(6, 20), banco=None),
    "local_cr": dict(nombre="Transferencia bancaria (Costa Rica, IBAN)", paises={"CR"}, monedas={"CRC"},
                     cuenta=lambda c: iban(c, "CR"), banco=None),
    "local_za": dict(nombre="Transferencia bancaria (Sudáfrica)", paises={"ZA"}, monedas={"ZAR"}, cuenta=_cuenta(6, 16),
                     banco=("código de sucursal (6 dígitos)", lambda b: bool(re.fullmatch(r"\d{6}", b or "")))),
    "usd_local": dict(nombre="Ruta local en USD", paises={"PE", "PA", "CN", "HK"}, monedas={"USD"},
                      cuenta=lambda c: bool(re.fullmatch(r"[A-Za-z0-9]{5,34}", _digitos(c))), banco=None),
    "usdc": dict(nombre="USDC", paises=None, monedas={"USD"}, cuenta=None, banco=None),
}


def problemas(riel: str, pais: str, moneda: str, cuenta: str | None, codigo_banco: str | None,
              billetera_usdc: str | None, red_usdc: str | None) -> list[str]:
    """Lo que impide pagarle a este beneficiario por este riel de Blinka. Vacío = se le puede pagar."""
    r = RIELES.get(riel)
    if r is None:
        return [f"riel desconocido '{riel}'; los de Blinka son: {', '.join(RIELES)}"]
    out = []
    if r["paises"] is not None and pais not in r["paises"]:
        out.append(f"{r['nombre']} no paga a {pais} (paga a {', '.join(sorted(r['paises']))})")
    if moneda not in r["monedas"]:
        out.append(f"{r['nombre']} paga en {', '.join(sorted(r['monedas']))}, no en {moneda}")
    if riel == "usdc":
        if red_usdc not in REDES_USDC:
            out.append(f"USDC necesita la red: {', '.join(sorted(REDES_USDC))}")
        elif not billetera(billetera_usdc, red_usdc):
            out.append(f"la billetera USDC no es una dirección válida de {red_usdc}")
    elif not r["cuenta"](cuenta):
        out.append(f"la cuenta no es válida para {r['nombre']}")
    if r["banco"] and not r["banco"][1](codigo_banco):
        out.append(f"{r['nombre']} necesita un {r['banco'][0]} válido")
    return out


def etiqueta(prov) -> str | None:
    """Riel sugerido que queda en la decisión, por ejemplo "SPEI (MXN)" o "USDC en Base por x402 (simulado)"."""
    if prov is None:
        return None
    if not prov.riel:
        return "USDC por x402 (simulado)" if prov.acepta_usdc and prov.billetera_usdc else f"Transferencia a {prov.banco}"
    if prov.riel == "usdc":
        red = (prov.red_usdc or "").capitalize()
        return f"USDC en {red} por x402 (simulado)" if prov.red_usdc == "base" else f"USDC en {red}"
    return f"{RIELES[prov.riel]['nombre']} ({prov.moneda})"


def _mascara(v: str | None) -> str | None:
    return f"****{v[-4:]}" if v and len(v) > 4 else v


def orden(d, prov) -> dict | None:
    """Orden de pago SIMULADA para una decisión PAGAR, por el riel del proveedor. No se envía a ninguna parte."""
    e = d.extraccion
    if d.decision != "PAGAR" or prov is None or not prov.riel or e.monto_total is None:
        return None
    if prov.riel == "usdc":
        beneficiario = {"red": prov.red_usdc, "billetera": prov.billetera_usdc}
    else:
        beneficiario = {"nombre": prov.nombre, "pais": prov.pais, "banco": prov.banco,
                        "cuenta": _mascara(prov.cuenta), RIELES[prov.riel]["banco"][0] if RIELES[prov.riel]["banco"] else "codigo_banco": prov.codigo_banco}
        beneficiario = {k: v for k, v in beneficiario.items() if v}
    return {"estado": "simulada: sin enviar", "riel": RIELES[prov.riel]["nombre"], "moneda_pago": "USDC" if prov.riel == "usdc" else prov.moneda,
            "monto": str(e.monto_total), "moneda_factura": e.moneda, "beneficiario": beneficiario,
            "referencia": e.numero_factura, "decision": d.id, "sello": d.sello, "mandato": d.mandato_hash,
            "fecha_pago": d.fecha_pago_programada.isoformat() if d.fecha_pago_programada else None}


PAIN001 = {"ach", "wire", "swift", "sepa"}  # los rieles bancarios: los que un banco recibe en un pain.001


def _hijo(padre: ET.Element, ruta: str, texto: str | None = None, **atributos) -> ET.Element:
    """Crea la cadena de elementos de `ruta` ("A/B/C") bajo `padre` y pone texto y atributos en el último."""
    for nombre in ruta.split("/"):
        padre = ET.SubElement(padre, nombre)
    padre.text = texto
    padre.attrib.update(atributos)
    return padre


def pain001(d, prov, empresa) -> str | None:
    """pain.001.001.09 (ISO 20022, Customer Credit Transfer Initiation) de una orden por ACH, wire, SWIFT o SEPA: el
    archivo que se carga en el banco del pagador. SIMULADO: la cuenta del deudor es un marcador y nada se envía."""
    o = orden(d, prov)
    if o is None or prov.riel not in PAIN001:
        return None
    e = d.extraccion
    monto = f"{e.monto_total:.2f}"
    raiz = ET.Element("Document", xmlns="urn:iso:std:iso:20022:tech:xsd:pain.001.001.09")
    inicio = ET.SubElement(raiz, "CstmrCdtTrfInitn")
    grupo = ET.SubElement(inicio, "GrpHdr")
    _hijo(grupo, "MsgId", f"MANDATO-{d.id}")
    _hijo(grupo, "CreDtTm", datetime.now(timezone.utc).replace(microsecond=0).isoformat())
    _hijo(grupo, "NbOfTxs", "1")
    _hijo(grupo, "CtrlSum", monto)
    _hijo(grupo, "InitgPty/Nm", empresa.nombre[:140])
    pago = ET.SubElement(inicio, "PmtInf")
    _hijo(pago, "PmtInfId", f"MANDATO-{d.id}-1")
    _hijo(pago, "PmtMtd", "TRF")
    _hijo(pago, "NbOfTxs", "1")
    _hijo(pago, "CtrlSum", monto)
    _hijo(pago, "ReqdExctnDt/Dt", o["fecha_pago"] or date.today().isoformat())
    _hijo(pago, "Dbtr/Nm", empresa.nombre[:140])
    _hijo(pago, "DbtrAcct/Id/Othr/Id", "SIMULADA")
    _hijo(pago, "DbtrAgt/FinInstnId/Othr/Id", "NOTPROVIDED")  # obligatorio en el esquema; lo completa el banco del pagador
    tx = ET.SubElement(pago, "CdtTrfTxInf")
    _hijo(tx, "PmtId/EndToEndId", (d.sello or f"MANDATO-{d.id}")[:35])
    _hijo(tx, "Amt/InstdAmt", monto, Ccy=e.moneda)
    if prov.riel == "swift":
        _hijo(tx, "CdtrAgt/FinInstnId/BICFI", prov.codigo_banco.upper())
    elif prov.riel in ("ach", "wire"):
        miembro = _hijo(tx, "CdtrAgt/FinInstnId/ClrSysMmbId")
        _hijo(miembro, "ClrSysId/Cd", "USABA")
        _hijo(miembro, "MmbId", _digitos(prov.codigo_banco))
    _hijo(tx, "Cdtr/Nm", prov.nombre[:140])
    if iban(prov.cuenta):
        _hijo(tx, "CdtrAcct/Id/IBAN", re.sub(r"\s", "", prov.cuenta).upper())
    else:
        _hijo(tx, "CdtrAcct/Id/Othr/Id", prov.cuenta)
    _hijo(tx, "RmtInf/Ustrd", f"Factura {e.numero_factura}")
    ET.indent(raiz)
    return '<?xml version="1.0" encoding="UTF-8"?>\n' + ET.tostring(raiz, encoding="unicode") + "\n"
