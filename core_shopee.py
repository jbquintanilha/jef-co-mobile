import core_env_loader
# ==============================================================================
# NOME DO SCRIPT: core_shopee.py
# DESCRICAO: Cliente da API V2 da Shopee (loja J&F Co.). Auth/refresh, upload de
#            imagem, criacao de anuncio (add_item -> init_tier_variation ->
#            update_model peso por variacao), estoque. Motor do /shopee-anuncio-api.
# AUTOR: Conselho J&F Co. - Terminador (001)
# VERSAO: 1.0
# DATA: 2026-06-18
# STATUS: Operacional (validado em prod: CALTAY748 item_id 58212746516)
# REF: _INBOX/shopee_api_cadastro_codigos_caltay748_20260618.md
# ==============================================================================
"""Cliente fino da API V2 da Shopee Open Platform.

Aprendizados validados em prod (2026-06-18, primeiro anuncio 100% via API):

- O anuncio com variacao se cria em 3 passos, NAO em um:
    1) add_item        -> cria item base (nasce has_model:false; tier no add_item
                          NAO pega)
    2) init_tier_variation -> adiciona as variacoes (tier_variation 2 eixos)
    3) update_model    -> ajusta peso POR variacao (Shopee aceita peso distinto
                          por model; dimensao fica fixa no item)
- Estoque usa `seller_stock:[{stock:N}]` — `normal_stock` da erro error_param.
- brand_id e OBRIGATORIO no add_item (NoBrand = 0).
- Endpoints `get_attributes` e `get_dts_limit` estao api_suspended p/ nosso app.
- Assinatura: HMAC-SHA256(partner_key, base). base do refresh =
  f"{partner_id}{path}{ts}"; base das chamadas autenticadas =
  f"{partner_id}{path}{ts}{access_token}{shop_id}".
- Token expira em 4h (expire_in 14400) — refresh sob demanda, persiste no JSON.

Codigos uteis (das tabelas J&F, ver _INBOX):
  Categoria lingerie = 100385 | Size chart calcinha = 2154590052
  CSOSN Simples = 102 | Origem nacional = 0 | CFOP venda 5102/6102
  Logistica habilitada: 90024 (Retirada) + 91003 (Shopee Xpress)
"""

import os
import json
import time
import hmac
import hashlib
import requests
from dotenv import load_dotenv

ENV_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
load_dotenv(ENV_PATH)

HOST = "https://partner.shopeemobile.com"
TOKENS_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           "local_db", "shopee_tokens.json")

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_SERVICE_KEY") or os.getenv("SUPABASE_KEY")


def _sb_headers() -> dict:
    return {
        "apikey": SUPABASE_KEY or "",
        "Authorization": f"Bearer {SUPABASE_KEY or ''}",
        "Content-Type": "application/json",
        "Prefer": "resolution=merge-duplicates",
    }


def _carregar_tokens_supabase() -> dict | None:
    """Fonte da verdade dos tokens (tabela shopee_tokens, id=shopee_production).
    Permite rodar sem local_db/shopee_tokens.json (ex: GitHub Actions)."""
    if not (SUPABASE_URL and SUPABASE_KEY):
        return None
    try:
        r = requests.get(
            f"{SUPABASE_URL}/rest/v1/shopee_tokens?id=eq.shopee_production"
            "&select=access_token,refresh_token,expires_in,shop_id",
            headers=_sb_headers(), timeout=10)
        if r.status_code == 200 and r.json():
            return r.json()[0]
    except requests.RequestException:
        pass
    return None


def _salvar_tokens_supabase(tokens: dict) -> None:
    if not (SUPABASE_URL and SUPABASE_KEY):
        return
    import datetime as _d
    payload = {
        "id": "shopee_production",
        "access_token": tokens.get("access_token"),
        "refresh_token": tokens.get("refresh_token"),
        "expires_in": tokens.get("expires_in"),
        "shop_id": tokens.get("shop_id"),
        "salvo_em": tokens.get("salvo_em"),
        "updated_at": _d.datetime.now(_d.timezone.utc).isoformat(),
    }
    try:
        requests.post(f"{SUPABASE_URL}/rest/v1/shopee_tokens",
                      headers=_sb_headers(), json=payload, timeout=10)
    except requests.RequestException:
        pass

# Codigos canonicos J&F (referencia rapida)
CATEGORIA_LINGERIE = 100385
SIZE_CHART_CALCINHA = 2154590052
BRAND_NOBRAND = 0
LOGISTICA_PADRAO = [
    {"logistic_id": 90024, "enabled": True},   # Retirada pelo Comprador
    {"logistic_id": 91003, "enabled": True},   # Shopee Xpress
]


class ShopeeError(RuntimeError):
    """Erro de chamada a API V2 da Shopee."""


class ShopeeClient:
    """Cliente fino da API V2 da Shopee para a loja J&F Co.

    Uso:
        c = ShopeeClient()
        img_id = c.upload_image(open('capa.jpeg','rb').read())
        r = c.add_item({...})
    """

    def __init__(self):
        self.partner_id = int(os.getenv("SHOPEE_PROD_PARTNER_ID"))
        self.partner_key = os.getenv("SHOPEE_PROD_PARTNER_KEY")
        self.shop_id = int(os.getenv("SHOPEE_PROD_SHOP_ID"))
        self._tokens = self._carregar_tokens()
        self._access = self._tokens.get("access_token")

    # -- tokens ----------------------------------------------------- #
    def _carregar_tokens(self) -> dict:
        """Supabase e' a fonte da verdade (compartilhada entre PC e CI);
        cai pro arquivo local se o Supabase estiver fora do ar ou vazio."""
        remoto = _carregar_tokens_supabase()
        if remoto and remoto.get("access_token"):
            return remoto
        with open(TOKENS_PATH, encoding="utf-8") as f:
            return json.load(f)

    def _salvar_tokens(self) -> None:
        self._tokens["salvo_em"] = time.time()
        try:
            with open(TOKENS_PATH, "w", encoding="utf-8") as f:
                json.dump(self._tokens, f)
        except OSError:
            pass  # ambiente efemero (CI) pode nao ter local_db/ gravavel
        _salvar_tokens_supabase(self._tokens)

    def refresh(self) -> str:
        """Renova o access_token via refresh_token. Persiste no JSON."""
        path = "/api/v2/auth/access_token/get"
        ts = int(time.time())
        base = f"{self.partner_id}{path}{ts}"
        sign = hmac.new(self.partner_key.encode(), base.encode(),
                        hashlib.sha256).hexdigest()
        r = requests.post(
            f"{HOST}{path}",
            params={"partner_id": self.partner_id, "timestamp": ts, "sign": sign},
            json={"refresh_token": self._tokens["refresh_token"],
                  "partner_id": self.partner_id, "shop_id": self.shop_id},
            timeout=20,
        )
        data = r.json()
        if data.get("error"):
            raise ShopeeError(f"refresh falhou: {data}")
        self._tokens["access_token"] = data["access_token"]
        self._tokens["refresh_token"] = data["refresh_token"]
        self._access = data["access_token"]
        self._salvar_tokens()
        return self._access

    # -- assinatura ------------------------------------------------- #
    def _sign(self, path: str, ts: int) -> str:
        base = f"{self.partner_id}{path}{ts}{self._access}{self.shop_id}"
        return hmac.new(self.partner_key.encode(), base.encode(),
                        hashlib.sha256).hexdigest()

    def _params(self, path: str) -> dict:
        ts = int(time.time())
        return {"partner_id": self.partner_id, "timestamp": ts,
                "sign": self._sign(path, ts), "access_token": self._access,
                "shop_id": self.shop_id}

    def _check(self, data: dict):
        if data.get("error") == "error_auth" or "token" in str(data.get("message", "")).lower():
            self.refresh()
            return None  # sinaliza retry
        if data.get("error"):
            raise ShopeeError(f"{data.get('error')}: {data.get('message')} {data.get('debug_message','')}")
        return data

    def get(self, path: str, extra: dict | None = None) -> dict:
        for _ in range(2):
            params = self._params(path)
            if extra:
                params.update(extra)
            data = requests.get(f"{HOST}{path}", params=params, timeout=20).json()
            res = self._check(data)
            if res is not None:
                return res
        raise ShopeeError(f"GET {path} falhou apos refresh")

    def post(self, path: str, body: dict) -> dict:
        for _ in range(2):
            data = requests.post(f"{HOST}{path}", params=self._params(path),
                                 json=body, timeout=30).json()
            res = self._check(data)
            if res is not None:
                return res
        raise ShopeeError(f"POST {path} falhou apos refresh")

    # -- imagem ----------------------------------------------------- #
    def upload_image(self, img_bytes: bytes, *, nome: str = "img.jpeg",
                     mime: str = "image/jpeg") -> str:
        """Sobe imagem e retorna o image_id (usado no add_item)."""
        path = "/api/v2/media_space/upload_image"
        r = requests.post(f"{HOST}{path}", params=self._params(path),
                          files={"image": (nome, img_bytes, mime)}, timeout=30)
        data = self._check(r.json())
        return data["response"]["image_info"]["image_id"]

    # -- produto ---------------------------------------------------- #
    def add_item(self, payload: dict) -> int:
        """Cria o item BASE. Retorna item_id. (tier_variation entra depois.)"""
        data = self.post("/api/v2/product/add_item", payload)
        return data["response"]["item_id"]

    def init_tier_variation(self, item_id: int, tier_variation: list, model: list) -> dict:
        """Adiciona as variacoes ao item ja criado."""
        return self.post("/api/v2/product/init_tier_variation",
                         {"item_id": item_id, "tier_variation": tier_variation, "model": model})

    # -- publicar / despublicar anuncio ----------------------------- #
    def unlist_item(self, itens: list[dict]) -> dict:
        """Despublica (ou republica) anuncios.

        `itens` = [{"item_id": 123, "unlist": True}, ...]
            unlist=True  -> tira do ar (vira UNLIST)
            unlist=False -> volta ao ar (vira NORMAL)

        ⚠️ NAO apaga nada. O anuncio, as variacoes, as fotos, o historico de
        avaliacoes e o estoque continuam existindo -- so' deixa de aparecer
        para o comprador. E' reversivel a qualquer momento.

        Usar para concentrar clique quando ha' anuncios duplicados do mesmo
        produto competindo entre si.
        """
        return self.post("/api/v2/product/unlist_item", {"item_list": itens})

    # -- desconto (campanha de promocao) ---------------------------- #
    def get_discount(self, discount_id: int) -> dict:
        """Detalhe de uma campanha de Desconto, com os itens e precos promo."""
        data = self.get("/api/v2/discount/get_discount",
                        {"discount_id": int(discount_id)})
        return (data or {}).get("response") or {}

    def get_discount_list(self, *, status: str = "ongoing",
                          page_size: int = 50) -> list:
        """Campanhas de Desconto da loja. status: upcoming|ongoing|expired|all."""
        data = self.get("/api/v2/discount/get_discount_list",
                        {"discount_status": status, "page_size": page_size})
        return ((data or {}).get("response") or {}).get("discount_list") or []

    def update_discount_item(self, discount_id: int, item_list: list) -> dict:
        """Altera o preco promocional de variacoes JA' dentro da campanha.

        `item_list` = [{"item_id": X, "model_list":
                        [{"model_id": Y, "model_promotion_price": 23.90}, ...]}]

        ⚠️ Mexe SO' nos models informados. Uma mesma campanha pode cobrir
        varios anuncios (a "Promo Meia Invisivel Jul-Ago" cobre 4 itens):
        mandar a campanha inteira alteraria produto que ninguem pediu.

        ⚠️ A Shopee valida contra o menor preco praticado recente. Preco
        promo que suba muito pode ser aceito, mas vira o novo piso do
        historico -- ver [[lei_consultar_campanha_anterior_antes_de_precificar]].
        """
        return self.post("/api/v2/discount/update_discount_item",
                         {"discount_id": int(discount_id), "item_list": item_list})

    def get_model_list(self, item_id: int) -> list:
        data = self.get("/api/v2/product/get_model_list", {"item_id": item_id})
        return data.get("response", {}).get("model", [])

    def update_model_weight(self, item_id: int, model_id: int, weight: float) -> dict:
        """Ajusta o peso (kg) de uma variacao especifica.

        IMPORTANTE (validado prod 2026-06-19): `model` e ARRAY de UpdateModelInfo.
        Passar model_id+weight SOLTOS faz a API aceitar e IGNORAR (peso nao persiste).
        Formato correto: model:[{model_id, weight: <float>}]. weight DEVE ser float (nao str).
        """
        return self.post("/api/v2/product/update_model",
                         {"item_id": item_id,
                          "model": [{"model_id": model_id, "weight": float(weight)}]})

    def update_stock(self, item_id: int, model_id: int, stock: int) -> dict:
        """Atualiza estoque de uma variacao (seller_stock)."""
        return self.post("/api/v2/product/update_stock", {
            "item_id": item_id,
            "stock_list": [{"model_id": model_id, "seller_stock": [{"stock": stock}]}],
        })

    # -- sondagem --------------------------------------------------- #
    def get_brand_list(self, category_id: int, *, page_size: int = 100) -> list:
        data = self.get("/api/v2/product/get_brand_list",
                        {"category_id": category_id, "page_size": page_size,
                         "offset": 0, "status": 1})
        return data.get("response", {}).get("brand_list", [])

    def get_channel_list(self) -> list:
        data = self.get("/api/v2/logistics/get_channel_list")
        return data.get("response", {}).get("logistics_channel_list", [])

    def get_item_base_info(self, item_id: int) -> dict:
        data = self.get("/api/v2/product/get_item_base_info",
                        {"item_id_list": str(item_id)})
        itens = data.get("response", {}).get("item_list", [])
        return itens[0] if itens else {}

    # -- pedidos ---------------------------------------------------- #
    def get_order_list(self, *, time_from: int, time_to: int,
                       page_size: int = 50, status: str = "READY_TO_SHIP") -> list:
        """Lista order_sn por janela de criacao (epoch s). Janela max 15 dias."""
        data = self.get("/api/v2/order/get_order_list", {
            "time_range_field": "create_time", "time_from": time_from,
            "time_to": time_to, "page_size": page_size, "order_status": status,
        })
        return data.get("response", {}).get("order_list", [])

    def get_order_detail(self, order_sns: list[str] | str) -> list:
        """Detalhe de pedidos (ate 50 order_sn). Inclui package_number p/ etiqueta."""
        if isinstance(order_sns, str):
            order_sns = [order_sns]
        data = self.get("/api/v2/order/get_order_detail", {
            "order_sn_list": ",".join(order_sns),
            "response_optional_fields": "package_list,recipient_address,item_list",
        })
        return data.get("response", {}).get("order_list", [])

    # -- logistica / etiqueta --------------------------------------- #
    def get_tracking_number(self, order_sn: str, *, package_number: str = "") -> str:
        body = {"order_sn": order_sn}
        if package_number:
            body["package_number"] = package_number
        data = self.get("/api/v2/logistics/get_tracking_number", body)
        return data.get("response", {}).get("tracking_number", "")

    def create_shipping_document(self, order_sn: str, *, package_number: str = "",
                                 tracking_number: str = "",
                                 doc_type: str = "NORMAL_AIR_WAYBILL") -> dict:
        """Pede ao Shopee a geracao do documento de envio (etiqueta).
        doc_type THERMAL_AIR_WAYBILL = etiqueta termica (10x15).

        IMPORTANTE (validado 2026-06-22): exige tracking_number explicito no item,
        senao falha 'logistics.tracking_number_invalid' mesmo com tracking ja atribuido.
        """
        item = {"order_sn": order_sn, "shipping_document_type": doc_type}
        if package_number:
            item["package_number"] = package_number
        if tracking_number:
            item["tracking_number"] = tracking_number
        return self.post("/api/v2/logistics/create_shipping_document",
                         {"order_list": [item]})

    def get_shipping_document_result(self, order_sn: str, *, package_number: str = "",
                                     doc_type: str = "NORMAL_AIR_WAYBILL") -> str:
        """Status do documento: READY|PROCESSING|FAILED."""
        item = {"order_sn": order_sn, "shipping_document_type": doc_type}
        if package_number:
            item["package_number"] = package_number
        data = self.post("/api/v2/logistics/get_shipping_document_result",
                         {"order_list": [item]})
        lst = data.get("response", {}).get("result_list", [])
        return (lst[0].get("status") if lst else "") or ""

    def download_shipping_document(self, order_sn: str, *, package_number: str = "",
                                   doc_type: str = "NORMAL_AIR_WAYBILL") -> bytes | None:
        """Baixa o PDF da etiqueta. Retorna bytes (PDF) ou None.

        Fluxo Shopee: create -> (poll result READY) -> download. Aqui assumimos
        que create + result ja foram chamados; este faz o download binario.
        """
        path = "/api/v2/logistics/download_shipping_document"
        item = {"order_sn": order_sn, "shipping_document_type": doc_type}
        if package_number:
            item["package_number"] = package_number
        body = {"shipping_document_type": doc_type, "order_list": [item]}
        for _ in range(2):
            r = requests.post(f"{HOST}{path}", params=self._params(path),
                              json=body, timeout=40)
            ctype = r.headers.get("Content-Type", "")
            # NORMAL_AIR_WAYBILL devolve application/pdf; aceitar tb pela assinatura.
            if r.status_code == 200 and ("application/pdf" in ctype
                                         or r.content[:4] == b"%PDF"):
                return r.content
            # erro vem como JSON (token expirado etc.)
            try:
                if self._check(r.json()) is None:
                    continue  # refresh + retry
            except Exception:
                pass
            return None
        return None

    def etiqueta_termica(self, order_sn: str, *, package_number: str = "") -> bytes | None:
        """Fluxo completo: tracking -> create -> poll READY -> download. PDF 10x15.

        O create exige tracking_number explicito (bug Shopee). Buscamos antes.
        """
        tracking = ""
        try:
            tracking = self.get_tracking_number(order_sn, package_number=package_number)
        except ShopeeError:
            pass
        self.create_shipping_document(order_sn, package_number=package_number,
                                      tracking_number=tracking, doc_type="THERMAL_AIR_WAYBILL")
        for _ in range(8):
            status = self.get_shipping_document_result(order_sn, package_number=package_number,
                                                       doc_type="THERMAL_AIR_WAYBILL")
            if status == "READY":
                break
            if status == "FAILED":
                return None
            time.sleep(2)
        return self.download_shipping_document(order_sn, package_number=package_number,
                                               doc_type="THERMAL_AIR_WAYBILL")


if __name__ == "__main__":
    c = ShopeeClient()
    info = c.get("/api/v2/shop/get_shop_info")
    print("Loja:", info.get("shop_name"), "| region:", info.get("region"),
          "| status:", info.get("status"))
