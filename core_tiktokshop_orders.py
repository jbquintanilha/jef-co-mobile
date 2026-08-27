# ==============================================================================
# NOME DO SCRIPT: core_tiktokshop_orders.py
# DESCRICAO: Cliente de PEDIDOS do TikTok Shop (producao). Lista pedidos com
#            rastreio, para alimentar o indice do Scanner de Conferencia.
# AUTOR: Terminador (Claude) / J&F Co.
# VERSAO: 1.0
# DATA: 2026-08-02
# STATUS: Operacional
# ==============================================================================
"""Cliente de pedidos do TikTok Shop.

Promove pra producao a logica que vivia solta em ``scratch/consultar_pedido_tiktok.py``.

ACHADO QUE DEFINE O DESENHO (validado 2026-08-02 contra a loja real):
o campo ``tracking_number`` ja vem na PROPRIA listagem de
``/order/202309/orders/search`` -- nao e' preciso chamar o detalhe pedido a
pedido. Uma varredura de N pedidos custa ceil(N/50) requests, nao N.

Assinatura HMAC-SHA256: o ``sign`` cobre APP_SECRET + path + query params
ordenados (menos ``sign``/``access_token``) + corpo JSON + APP_SECRET. O corpo
entra na assinatura como string exata enviada -- por isso o mesmo ``body_str``
e' usado no sign e no ``data=`` do request.

Uso:
    from core_tiktokshop_orders import listar_pedidos
    for p in listar_pedidos():
        print(p["id"], p["tracking_number"], p["seller_sku"])
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import time

import requests
from dotenv import load_dotenv

log = logging.getLogger("core_tiktokshop_orders")

_ROOT = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(_ROOT, ".env"), override=False)

BASE_URL = "https://api.tiktok-shops.com"
PATH_SEARCH = "/order/202309/orders/search"

# Status que ainda passam pela bancada de expedicao.
#
# O CORTE E' O DESPACHO NA PLATAFORMA, nao o controle interno (Jota, 2026-08-03):
#   AWAITING_SHIPMENT   -> falta separar/etiquetar        -> ENTRA
#   AWAITING_COLLECTION -> etiqueta pronta, caixa AINDA AQUI esperando os
#                          Correios coletarem              -> ENTRA
#   PARTIALLY_SHIPPING  -> parte do pedido ainda por sair  -> ENTRA
#   IN_TRANSIT/DELIVERED-> a transportadora ja levou       -> fica de fora
#   CANCELLED/UNPAID    -> nao sera separado               -> fica de fora
STATUS_ATIVOS = {
    "AWAITING_SHIPMENT",
    "AWAITING_COLLECTION",
    "PARTIALLY_SHIPPING",
}


def _creds() -> tuple[str, str, str, str]:
    """Le as credenciais do .env. Lanca RuntimeError se faltar alguma."""
    app_key = os.getenv("TIKTOK_APP_KEY")
    app_secret = os.getenv("TIKTOK_APP_SECRET")
    token = os.getenv("TIKTOK_ACCESS_TOKEN")
    cipher = os.getenv("TIKTOK_SHOP_CIPHER")
    faltando = [n for n, v in (
        ("TIKTOK_APP_KEY", app_key), ("TIKTOK_APP_SECRET", app_secret),
        ("TIKTOK_ACCESS_TOKEN", token), ("TIKTOK_SHOP_CIPHER", cipher),
    ) if not v]
    if faltando:
        raise RuntimeError(f"Credenciais TikTok ausentes no .env: {', '.join(faltando)}")
    return app_key, app_secret, token, cipher


def _assinar(app_secret: str, path: str, query: dict, body_str: str = "") -> str:
    """Gera o sign HMAC-SHA256 exigido pela API do TikTok Shop."""
    chaves = sorted(k for k in query if k not in ("sign", "access_token"))
    base = (
        f"{app_secret}{path}"
        f"{''.join(f'{k}{query[k]}' for k in chaves)}"
        f"{body_str}{app_secret}"
    )
    return hmac.new(app_secret.encode(), base.encode(), hashlib.sha256).hexdigest()


def _post(path: str, params: dict | None = None, body: dict | None = None,
          *, timeout: int = 30) -> dict:
    """POST assinado na API do TikTok Shop. Retorna o JSON de resposta."""
    app_key, app_secret, token, cipher = _creds()
    params = params or {}
    body = body or {}

    query = {
        "app_key": app_key,
        "access_token": token,
        "shop_cipher": cipher,
        "timestamp": str(int(time.time())),
    }
    query.update(params)
    body_str = json.dumps(body, separators=(",", ":"))
    query["sign"] = _assinar(app_secret, path, query, body_str)

    r = requests.post(
        BASE_URL + path,
        params=query,
        headers={"Content-Type": "application/json", "x-tts-access-token": token},
        data=body_str,
        timeout=timeout,
    )
    r.raise_for_status()
    return r.json()


def _achatar(pedido: dict) -> dict:
    """Extrai do pedido bruto so o que o indice do scanner precisa."""
    itens = pedido.get("line_items") or []
    primeiro = itens[0] if itens else {}
    endereco = pedido.get("recipient_address") or {}
    return {
        "id": pedido.get("id") or "",
        "tracking_number": (pedido.get("tracking_number") or "").strip(),
        "status": pedido.get("status") or "",
        "seller_sku": primeiro.get("seller_sku") or "",
        "product_name": primeiro.get("product_name") or "",
        # Miniatura da variacao vendida — mesma foto que o cliente viu no
        # anuncio. Usada na conferencia visual do scanner.
        "sku_image": primeiro.get("sku_image") or "",
        "quantidade_itens": len(itens),
        # TODOS os itens do pedido. Um pedido multi-item sai numa etiqueta so:
        # mostrar apenas o primeiro faz a bancada separar caixa incompleta.
        "itens": [
            {
                "sku": it.get("seller_sku") or "",
                "nome": it.get("product_name") or "",
                "variacao": it.get("sku_name") or "",
                "quantidade": 1,  # TikTok emite 1 line_item por unidade
                "imagem_url": it.get("sku_image") or "",
                # Valor pago pela peca: serve de segunda checagem na auditoria
                # (valor alto x poucos itens = provavel item faltando na lista).
                "valor": float(it.get("sale_price") or 0),
            }
            for it in itens
        ],
        # Total do pedido somando as pecas — comparado com o CMV esperado.
        "valor_total": sum(float(i.get("sale_price") or 0) for i in itens),
        "cliente": endereco.get("name") or pedido.get("cpf_name") or "",
        "cep": (endereco.get("postal_code") or "").strip(),
        "shipping_provider": pedido.get("shipping_provider") or "",
    }


def listar_pedidos(*, page_size: int = 50, max_paginas: int = 20,
                   somente_ativos: bool = True) -> list[dict]:
    """Lista pedidos do TikTok Shop, ja achatados pro uso do scanner.

    ``somente_ativos=True`` filtra CANCELLED/UNPAID (nao vao ser expedidos).
    Pagina ate esgotar ou atingir ``max_paginas`` (trava de seguranca contra
    loop infinito caso a API devolva sempre o mesmo page_token).
    """
    pedidos: list[dict] = []
    page_token = ""

    for _ in range(max_paginas):
        params: dict = {"page_size": page_size}
        if page_token:
            params["page_token"] = page_token
        try:
            resp = _post(PATH_SEARCH, params=params)
        except Exception as e:
            log.error("Falha ao listar pedidos TikTok: %s", e)
            break

        if resp.get("code") != 0:
            log.error("API TikTok recusou a busca: code=%s msg=%s",
                      resp.get("code"), resp.get("message"))
            break

        data = resp.get("data") or {}
        for bruto in data.get("orders") or []:
            achatado = _achatar(bruto)
            if somente_ativos and achatado["status"] not in STATUS_ATIVOS:
                continue
            pedidos.append(achatado)

        page_token = data.get("next_page_token") or ""
        if not page_token:
            break

    return pedidos


def buscar_por_tracking(tracking: str) -> dict | None:
    """Acha o pedido cujo tracking_number bate (busca ao vivo, sem indice).

    Fallback pra quando o indice local ainda nao tem o rastreio -- a API nao
    oferece busca por tracking, entao varre a listagem (barato: o tracking ja
    vem nela).
    """
    alvo = (tracking or "").strip().upper()
    if not alvo:
        return None
    for p in listar_pedidos(somente_ativos=False):
        if (p.get("tracking_number") or "").upper() == alvo:
            return p
    return None


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    lista = listar_pedidos()
    print(f"== {len(lista)} pedido(s) ativo(s) no TikTok Shop ==")
    for p in lista:
        print(f"  {p['id']} | {p['tracking_number']:16s} | {p['status']:20s} | {p['seller_sku']}")
