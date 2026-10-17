"""Orden de pago x402 SIMULADA (especificación v2, esquema "exact" en EVM con autorización EIP-3009).

Cuando el mandato dice PAGAR y el proveedor acepta USDC, Mandato arma lo que entregaría a una billetera con permiso
del mandato para firmar y a un facilitador x402 para liquidar. No firma ni envía nada: el reto excluye mover dinero,
y la red es Base Sepolia (pruebas). Las billeteras de data/mandato.json son inventadas.
"""
import hashlib
from datetime import datetime

from app import rieles
from app.modelos import Decision, Proveedor

RED = "eip155:84532"  # Base Sepolia, formato CAIP-2
USDC = "0x036CbD53842c5426634e7929541eC2318f3dCF7e"  # USDC de prueba de Circle en Base Sepolia
SEGUNDOS = 300


def orden(d: Decision, prov: Proveedor | None) -> dict | None:
    """PaymentPayload x402 sin firma para una decisión PAGAR en USD a un proveedor con billetera USDC; si no, None."""
    e = d.extraccion
    if d.decision != "PAGAR" or "x402" not in (rieles.etiqueta(prov) or "") or e.moneda != "USD" or e.monto_total is None:
        return None
    monto = str(int(e.monto_total * 10**6))  # USDC tiene 6 decimales
    desde = int(datetime.fromisoformat(d.ts).timestamp()) if d.ts else 0
    requisito = {"scheme": "exact", "network": RED, "amount": monto, "asset": USDC, "payTo": prov.billetera_usdc,
                 "maxTimeoutSeconds": SEGUNDOS, "extra": {"name": "USDC", "version": "2"}}
    return {
        "x402Version": 2,
        "resource": {"url": f"/decisiones/{d.id}", "mimeType": "application/json",
                     "description": f"Factura {e.numero_factura or 'sin número'} de {prov.nombre}"},
        "accepted": requisito,
        "payload": {
            "signature": None,  # la pone la billetera de la empresa; aquí no se firma
            "authorization": {"from": None, "to": prov.billetera_usdc, "value": monto, "validAfter": str(desde),
                              "validBefore": str(desde + SEGUNDOS),
                              # nonce atado a la decisión sellada: la misma orden no se puede liquidar dos veces
                              "nonce": "0x" + hashlib.sha256(f"mandato:{d.id}:{d.sello}".encode()).hexdigest()},
        },
        "extensions": {"mandato": {"simulada": True, "decision": d.id, "sello": d.sello, "mandato": d.mandato_hash,
                                   "decidido_por": d.decidido_por}},
    }
