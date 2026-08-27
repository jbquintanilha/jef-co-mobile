# ==============================================================================
# NOME DO SCRIPT: core_esteira.py
# DESCRICAO: Orquestrador da Esteira de Vendas. Agrega pedidos do Olist (V3),
#            enriquece com o prazo real de despacho do Mercado Livre (shipment),
#            ordena por urgencia e expoe as acoes de fase (faturar, baixar
#            etiqueta unificada / fallback ML). Consumido por pages/12_Esteira_Vendas.py.
# AUTOR: Conselho J&F Co. - Terminador (001)
# VERSAO: 1.0
# DATA: 2026-06-17
# STATUS: Operacional (MVP — Shopee pendente de pedido real)
# REF: plano Esteira de Vendas; core_olist.py; core_ml_auth.py
# ==============================================================================
"""Camada de orquestracao da esteira.

Fases (espelham o fluxo Upseller -> Olist):
  1. ITENS_VENDIDOS  — pedido pago, sem NF (situacao aberta/aprovada)
  2. NF_GERADA       — NF emitida (situacao faturado)
  3. ENVIADO_PLAT    — NF enviada a plataforma; etiqueta liberada
  4. PRONTO_ENVIO    — etiqueta baixada/impressa
  5. DESPACHADO      — enviado

A unica fonte do PRAZO DE DESPACHO e o shipment do ML (campo
`shipping.lead_time` / `date_first_printed` / status_history). O Olist nao
expoe esse prazo, so a `dataPrevista` (entrega). Por isso enriquecemos cada
pedido ML chamando a API do ML quando ha numeroPedidoEcommerce.

Ordenacao da caixa = por prazo de despacho ASC (mais urgente primeiro).
Ver [[project-telegram-pedidos-alerta]].
"""

from __future__ import annotations

import datetime as _dt
import logging
import requests

from core_olist import OlistClient, OlistError, SITUACAO_PEDIDO
import core_ml_auth

log = logging.getLogger(__name__)

ML_API = "https://api.mercadolibre.com"

# Mapeamento situacao Olist -> fase da esteira.
_FASE_POR_SITUACAO = {
    "0": "RESERVADO",        # Aberto (Reservado / Não Pago)
    "3": "PAG_APROVADO",     # Aprovado (Pago, pronto p/ faturar)
    "2": "PAG_APROVADO",     # Em separação
    "1": "DANFE_GERADA",     # Faturado (Nota emitida)
    "4": "AGUARDANDO_PLAT",  # Preparando envio (Aguardando plataforma / Enviado)
    "7": "PRONTO_ENVIO",     # Pronto para envio
    "5": "DESPACHADO",       # Enviado
    "6": "DESPACHADO",       # Entregue
    "9": "CANCELADO",        # Cancelado
}

FASES_ORDEM = [
    "RESERVADO", "PAG_APROVADO", "DANFE_GERADA", "AGUARDANDO_PLAT", "PRONTO_ENVIO", "DESPACHADO",
]

FASE_LABEL = {
    "RESERVADO": "⏳ Reservado / Não Pago",
    "PAG_APROVADO": "💳 Pag. Aprovado",
    "DANFE_GERADA": "🧾 DANFE Gerada",
    "AGUARDANDO_PLAT": "📤 Aguardando Plataforma",
    "PRONTO_ENVIO": "🏷️ Pronto p/ Envio",
    "DESPACHADO": "🚚 Despachado",
    "CANCELADO": "❌ Cancelado",
}

_PRAZO_MAX = _dt.datetime(2099, 1, 1)  # pedidos sem prazo vao pro fim da fila


# ------------------------------------------------------------------ #
# ML — prazo de despacho + etiqueta
# ------------------------------------------------------------------ #
def _ml_headers() -> dict | None:
    tok = core_ml_auth.get_token()
    if not tok:
        return None
    return {"Authorization": f"Bearer {tok}"}


def _ml_get(path: str) -> dict | None:
    """GET na API ML com auto-refresh no 401. O token expira em ~6h; sem refresh
    a esteira nao acha o shipment e a etiqueta sai 'indisponivel'."""
    h = _ml_headers()
    if not h:
        return None
    try:
        r = requests.get(f"{ML_API}{path}", headers=h, timeout=15)
        if r.status_code == 401:
            # token expirado -> renova uma vez e retenta
            try:
                core_ml_auth.atualizar_token()
            except Exception:
                return None
            h = _ml_headers()
            if not h:
                return None
            r = requests.get(f"{ML_API}{path}", headers=h, timeout=15)
        if r.status_code == 200:
            return r.json()
    except requests.RequestException:
        pass
    return None


def obter_prazo_despacho_ml(numero_ecommerce: str) -> dict:
    """Busca o shipment do pedido ML e extrai prazo de despacho + status.

    Retorna dict: {shipment_id, status, substatus, prazo_despacho(datetime|None),
                   tracking}. Campos None quando indisponivel.
    """
    out = {"shipment_id": None, "status": None, "substatus": None,
           "prazo_despacho": None, "tracking": None, "pack_id": None}
    pedido = _ml_get(f"/orders/{numero_ecommerce}")
    if not pedido:
        return out
    ship = pedido.get("shipping") or {}
    sid = ship.get("id")
    out["shipment_id"] = sid
    # pack_id: varias compras do mesmo cliente numa etiqueta so'. Vem no
    # pedido, nao no shipment. Necessario pro bipador avisar a bancada que a
    # caixa leva mais de um pedido.
    out["pack_id"] = pedido.get("pack_id")
    if not sid:
        return out
    det = _ml_get(f"/shipments/{sid}") or {}
    out["status"] = det.get("status")
    out["substatus"] = det.get("substatus")
    out["tracking"] = det.get("tracking_number")
    # Prazo de despacho: validado em prod (shipment 47324309422). O ML guarda o
    # limite em shipping_option.estimated_delivery_time.pay_before. Fallbacks:
    # buffering.date e estimated_schedule_limit.date.
    opt = det.get("shipping_option") or {}
    prazo = (
        _extrair_data((opt.get("estimated_delivery_time") or {}).get("pay_before"))
        or _extrair_data((opt.get("estimated_schedule_limit") or {}).get("date"))
        or _extrair_data((opt.get("buffering") or {}).get("date"))
    )
    out["prazo_despacho"] = prazo
    return out


def _extrair_data(valor) -> _dt.datetime | None:
    if not valor or not isinstance(valor, str):
        return None
    try:
        # ML usa ISO 8601 com timezone
        return _dt.datetime.fromisoformat(valor.replace("Z", "+00:00")).replace(tzinfo=None)
    except (ValueError, TypeError):
        return None


def baixar_etiqueta_ml(shipment_id: int | str, *, formato: str = "pdf") -> bytes | None:
    """Baixa a etiqueta logistica do ML via shipment_labels. PDF por padrao.

    formato: 'pdf' (PDF unico) ou 'zpl2' (ZIP com TXT Zebra). Validado em prod.
    """
    h = _ml_headers()
    if not h:
        return None
    params = {"shipment_ids": str(shipment_id), "response_type": formato}
    try:
        r = requests.get(f"{ML_API}/shipment_labels", headers=h, params=params, timeout=20)
        if r.status_code == 401:
            try:
                core_ml_auth.atualizar_token()
            except Exception:
                return None
            h = _ml_headers()
            if not h:
                return None
            r = requests.get(f"{ML_API}/shipment_labels", headers=h, params=params, timeout=20)
        if r.status_code == 200 and r.content:
            return r.content
        # O ML explica a recusa no corpo (ex: NOT_PRINTABLE_STATUS quando o
        # envio ainda esta' `pending`). Sem logar isso, quem chama so' ve'
        # "etiqueta indisponivel" e nao sabe se e' erro nosso ou regra deles.
        if r.status_code != 200:
            log.warning("shipment_labels %s -> HTTP %s: %s",
                        shipment_id, r.status_code, (r.text or "")[:300])
    except requests.RequestException:
        pass
    return None


# ------------------------------------------------------------------ #
# Modelo da esteira
# ------------------------------------------------------------------ #

import os
import json
import pandas as pd

def aplicar_regra_horario_ml(prazo: _dt.datetime) -> _dt.datetime:
    """Aplica a regra de horario limite do ML se a data cair no intervalo configurado."""
    if not prazo:
        return prazo
    dir_atual = os.path.dirname(os.path.abspath(__file__))
    config_path = os.path.join(dir_atual, "local_db", "config_prazo_ml.json")
    if os.path.exists(config_path):
        try:
            with open(config_path, "r", encoding="utf-8") as f:
                cfg = json.load(f)
            d_ini = _dt.date.fromisoformat(cfg.get("data_inicio"))
            d_fim = _dt.date.fromisoformat(cfg.get("data_fim"))
            h_str = cfg.get("horario", "")
            if h_str and ":" in h_str:
                h, m = map(int, h_str.split(":"))
                p_date = prazo.date()
                if d_ini <= p_date <= d_fim:
                    return _dt.datetime.combine(p_date, _dt.time(h, m))
        except Exception:
            pass
    return prazo


def obter_prazo_despacho_shopee(order_sn: str) -> _dt.datetime | None:
    """Busca o prazo ship_by_date na API Shopee."""
    if not order_sn:
        return None
    try:
        from core_shopee import ShopeeClient
        c = ShopeeClient()
        det = c.get_order_detail(order_sn)
        if det and isinstance(det, list) and len(det) > 0:
            ts = det[0].get("ship_by_date")
            if ts:
                return _dt.datetime.fromtimestamp(int(ts))
    except Exception:
        pass
    return None


def baixar_e_salvar_capa_anuncio(sku: str, canal: str, num_ecom: str) -> str | None:
    """Baixa a imagem de capa do anuncio do marketplace (ML ou Shopee) e salva localmente."""
    if not sku or not canal or not num_ecom:
        return None

    # Resolve primeiro o REF/SPU do item
    info = obter_info_produto_por_sku_raw(sku)
    ref = info.get("ref")
    spu = info.get("spu")
    if not ref:
        ref = sku.strip().upper()

    canal_lower = canal.lower()
    url_imagem = None

    # 1. Se for Mercado Livre
    if "mercado" in canal_lower:
        try:
            pedido = _ml_get(f"/orders/{num_ecom}")
            if pedido and pedido.get("order_items"):
                item_ml = pedido["order_items"][0].get("item", {})
                item_id = item_ml.get("id")
                if item_id:
                    det_item = _ml_get(f"/items/{item_id}")
                    if det_item and det_item.get("pictures"):
                        url_imagem = det_item["pictures"][0].get("secure_url") or det_item["pictures"][0].get("url")
        except Exception:
            pass

    # 2. Se for Shopee
    elif "shopee" in canal_lower:
        try:
            from core_shopee import ShopeeClient
            sh_client = ShopeeClient()
            det_sh = sh_client.get_order_detail(num_ecom)
            if det_sh and isinstance(det_sh, list) and len(det_sh) > 0:
                itens_sh = det_sh[0].get("item_list", [])
                if itens_sh:
                    item_id = itens_sh[0].get("item_id")
                    if item_id:
                        det_base = sh_client.get_item_base_info(item_id)
                        imgs = det_base.get("image", {}).get("image_url_list", [])
                        if imgs:
                            url_imagem = imgs[0]
        except Exception:
            pass

    # 3. Se achou a URL da imagem, baixa e salva
    if url_imagem:
        try:
            from PIL import Image, ImageOps
            import io
            r = requests.get(url_imagem, timeout=15)
            if r.status_code == 200:
                img = Image.open(io.BytesIO(r.content))
                if img.mode in ("RGBA", "P"):
                    img = img.convert("RGB")
                thumb = ImageOps.fit(img, (800, 800), Image.Resampling.LANCZOS)
                dir_atual = os.path.dirname(os.path.abspath(__file__))
                thumbs_dir = os.path.join(dir_atual, "thumbs")
                os.makedirs(thumbs_dir, exist_ok=True)
                # Tenta salvar como REF.jpg ou SPU.jpg
                save_ref = ref if ref else (spu if spu else sku.strip().upper())
                caminho_final = os.path.join(thumbs_dir, f"{save_ref.strip().upper()}.jpg")
                thumb.save(caminho_final, format="JPEG", quality=85, optimize=True)
                return caminho_final
        except Exception:
            pass

    return None


def obter_info_produto_por_sku_raw(sku: str) -> dict:
    """Resolve info basica do SKU sem checar ou baixar imagens (evita loops)."""
    res = {"spu": None, "ref": None, "titulo": None}
    if not sku:
        return res
    dir_atual = os.path.dirname(os.path.abspath(__file__))
    db_pai_path = os.path.join(dir_atual, 'db_pai.csv')
    db_sku_path = os.path.join(dir_atual, 'db_sku.csv')
    db_itens_kit_path = os.path.join(dir_atual, 'db_itens_kit.csv')

    try:
        sku_clean = str(sku).strip().upper()
        sku_comp = sku_clean
        if os.path.exists(db_itens_kit_path):
            df_itens = pd.read_csv(db_itens_kit_path, dtype=str)
            df_itens.columns = [c.upper() for c in df_itens.columns]
            if 'SKU_KIT' in df_itens.columns and 'SKU_COMPONENTE' in df_itens.columns:
                df_k = df_itens[df_itens['SKU_KIT'].str.upper() == sku_clean]
                if not df_k.empty:
                    sku_comp = df_k.iloc[0]['SKU_COMPONENTE'].strip().upper()

        spu = None
        if os.path.exists(db_sku_path):
            df_sku = pd.read_csv(db_sku_path, dtype=str)
            df_sku.columns = [c.upper() for c in df_sku.columns]
            if 'SKU' in df_sku.columns and 'SPU' in df_sku.columns:
                df_s = df_sku[df_sku['SKU'].str.upper() == sku_comp]
                if not df_s.empty:
                    spu = df_s.iloc[0]['SPU'].strip().upper()

        if not spu:
            spu = sku_comp

        ref = None
        titulo = None
        if os.path.exists(db_pai_path):
            df_pai = pd.read_csv(db_pai_path, dtype=str)
            df_pai.columns = [c.upper() for c in df_pai.columns]
            if 'SPU' in df_pai.columns:
                df_p = df_pai[df_pai['SPU'].str.upper() == spu]
                if not df_p.empty:
                    ref = df_p.iloc[0].get('REF', spu)
                    titulo = df_p.iloc[0].get('TITULO_SEO') or df_p.iloc[0].get('DESC_MARKETING')

        if not ref:
            ref = spu

        res["spu"] = spu
        res["ref"] = ref
        res["titulo"] = titulo
    except Exception:
        pass
    return res


def obter_info_produto_por_sku(sku: str, canal: str | None = None, num_ecom: str | None = None) -> dict:
    """Retorna dict com SPU, REF, titulo e caminho_imagem local para o SKU.
    Se a imagem nao existir localmente, tenta baixar da API do canal (ML ou Shopee).
    """
    res = {"spu": None, "ref": None, "titulo": None, "imagem_path": None}
    if not sku:
        return res

    dir_atual = os.path.dirname(os.path.abspath(__file__))
    thumbs_dir = os.path.join(dir_atual, 'thumbs')

    try:
        raw_info = obter_info_produto_por_sku_raw(sku)
        spu = raw_info["spu"]
        ref = raw_info["ref"]
        res["spu"] = spu
        res["ref"] = ref
        res["titulo"] = raw_info["titulo"]

        # Verifica imagem
        caminho_final = None
        if ref:
            ref_limpo = ref.strip().upper()
            caminho_img = os.path.join(thumbs_dir, f"{ref_limpo}.jpg")
            if os.path.exists(caminho_img):
                caminho_final = caminho_img
            else:
                caminho_img_spu = os.path.join(thumbs_dir, f"{spu}.jpg")
                if os.path.exists(caminho_img_spu):
                    caminho_final = caminho_img_spu

        # Se nao achou localmente e temos canal + num_ecom, tenta baixar dinamicamente
        if not caminho_final and canal and num_ecom:
            caminho_final = baixar_e_salvar_capa_anuncio(sku, canal, num_ecom)

        res["imagem_path"] = caminho_final

    except Exception:
        pass

    return res


def _normalizar_pedido(p: dict, *, enriquecer_ml: bool = True, shopee_cache: dict | None = None) -> dict:
    """Achata um pedido resumido do Olist no modelo da esteira.

    Inclui a flag ``cancelado`` (True quando o pedido foi cancelado na
    plataforma) — fonte primaria: situacao do Olist; reforco por status do
    shipment ML / order_status Shopee quando disponivel.
    """
    sit = str(p.get("situacao"))
    ecom = p.get("ecommerce") or {}
    cliente = p.get("cliente") or {}
    canal = ecom.get("nome", "-")
    num_ecom = ecom.get("numeroPedidoEcommerce", "")

    item = {
        "id_olist": p.get("id"),
        "numero": p.get("numeroPedido"),
        "canal": canal,
        "numero_ecommerce": num_ecom,
        "cliente": cliente.get("nome", "?"),
        "uf": (cliente.get("endereco") or {}).get("uf", ""),
        "valor": p.get("valor"),
        "situacao_cod": sit,
        "situacao_nome": SITUACAO_PEDIDO.get(sit, sit),
        "fase": _FASE_POR_SITUACAO.get(sit, "RESERVADO"),
        "cancelado": sit == "9",   # Olist: situacao 9 = Cancelado
        "data_prevista": p.get("dataPrevista"),
        "shipment_id": None,
        "prazo_despacho": None,
        "ship_status": None,
        "itens_detalhe": [],
    }

    if "mercado" in canal.lower() and num_ecom:
        if enriquecer_ml:
            info = obter_prazo_despacho_ml(num_ecom)
            item["shipment_id"] = info["shipment_id"]
            p_ml = info["prazo_despacho"]
            if p_ml:
                p_ml = aplicar_regra_horario_ml(p_ml)
            item["prazo_despacho"] = p_ml
            item["ship_status"] = info["status"]
            item["fase"] = _reconciliar_fase(item["fase"], info["status"])
            if info["status"] in ("cancelled", "canceled"):
                item["cancelado"] = True
    elif "shopee" in canal.lower() and num_ecom:
        det_sh = (shopee_cache or {}).get(num_ecom) if shopee_cache else None
        if det_sh:
            ts = det_sh.get("ship_by_date")
            if ts:
                item["prazo_despacho"] = _dt.datetime.fromtimestamp(int(ts))
            sh_status = det_sh.get("order_status")
            if sh_status in ("CANCELLED", "IN_CANCEL"):
                item["cancelado"] = True
            if sh_status == "UNPAID":
                item["fase"] = "RESERVADO"
        else:
            p_shopee = obter_prazo_despacho_shopee(num_ecom)
            if p_shopee:
                item["prazo_despacho"] = p_shopee
            try:
                from core_shopee import ShopeeClient
                c = ShopeeClient()
                det = c.get_order_detail(num_ecom)
                if det and isinstance(det, list) and len(det) > 0:
                    sh_status = det[0].get("order_status")
                    if sh_status in ("CANCELLED", "IN_CANCEL"):
                        item["cancelado"] = True
                    if sh_status == "UNPAID":
                        item["fase"] = "RESERVADO"
            except Exception:
                pass

    return item


def _reconciliar_fase(fase_olist: str, ship_status: str | None) -> str:
    """Ajusta a fase usando o status real do shipment ML quando disponivel."""
    if not ship_status:
        return fase_olist
    if ship_status in ("shipped", "delivered"):
        return "DESPACHADO"
    if ship_status == "ready_to_ship":
        return "AGUARDANDO_PLAT" if fase_olist in ("DESPACHADO", "AGUARDANDO_PLAT", "DANFE_GERADA") else fase_olist
    return fase_olist


def carregar_esteira(*, enriquecer_ml: bool = True, incluir_despachados: bool = False) -> list[dict]:
    """Carrega todos os pedidos do Olist, normaliza e ordena por prazo de despacho ASC."""
    client = OlistClient()
    brutos = client.listar_pedidos_todos()

    # Busca detalhes da Shopee em lote para evitar N+1 requests
    shopee_sns = []
    for p in brutos:
        ecom = p.get("ecommerce") or {}
        canal = ecom.get("nome", "-")
        num_ecom = ecom.get("numeroPedidoEcommerce", "")
        if "shopee" in canal.lower() and num_ecom:
            shopee_sns.append(num_ecom)

    shopee_cache = {}
    if shopee_sns:
        try:
            from core_shopee import ShopeeClient
            sh_client = ShopeeClient()
            for i in range(0, len(shopee_sns), 50):
                chunk = shopee_sns[i:i+50]
                detalhes = sh_client.get_order_detail(chunk)
                for det in detalhes:
                    sn = det.get("order_sn")
                    if sn:
                        shopee_cache[sn] = det
        except Exception:
            pass

    itens = []
    for p in brutos:
        sit = str(p.get("situacao"))
        fase = _FASE_POR_SITUACAO.get(sit, "RESERVADO")
        if fase == "CANCELADO":
            continue
        if not incluir_despachados and fase == "DESPACHADO":
            continue
        item = _normalizar_pedido(p, enriquecer_ml=enriquecer_ml, shopee_cache=shopee_cache)

        # Enriquecer com os itens vendidos (busca o detalhe completo do pedido)
        pular_pedido = False
        try:
            detalhe = client.obter_pedido(item["id_olist"])
            item["itens_detalhe"] = detalhe.get("itens", [])

            # 🛡️ PROTEÇÃO CONTRA DUPLICIDADE: Se a Nota Fiscal já foi emitida (existe idNotaFiscal)
            # no ERP Tiny/Olist, garante que o pedido avance para pelo menos 'DANFE_GERADA'.
            # Isso evita que o usuário gere a nota novamente por engano.
            id_nota = detalhe.get("idNotaFiscal")
            if id_nota:
                item["id_nota_fiscal"] = id_nota
                if item["fase"] in ("RESERVADO", "PAG_APROVADO"):
                    item["fase"] = "DANFE_GERADA"

            # Se for despachado, checa se foi nos últimos 3 dias
            if item["fase"] == "DESPACHADO":
                data_limite = _dt.datetime.now() - _dt.timedelta(days=3)
                envio_str = detalhe.get("dataEnvio")
                dt_envio = None
                if envio_str:
                    try:
                        dt_envio = _dt.datetime.fromisoformat(envio_str)
                    except ValueError:
                        try:
                            dt_envio = _dt.datetime.strptime(envio_str, "%Y-%m-%d %H:%M:%S")
                        except ValueError:
                            pass
                if not dt_envio:
                    data_ped = detalhe.get("data")
                    if data_ped:
                        try:
                            dt_envio = _dt.datetime.strptime(data_ped, "%Y-%m-%d")
                        except ValueError:
                            pass
                if dt_envio and dt_envio < data_limite:
                    pular_pedido = True
        except Exception:
            item["itens_detalhe"] = []

        if pular_pedido:
            continue

        itens.append(item)

    itens.sort(key=lambda i: i["prazo_despacho"] or _PRAZO_MAX)

    # Scanner de Conferência: mantém o índice rastreio→pedido atualizado.
    # Roda em thread de fundo (daemon) p/ não travar a esteira — o populator
    # tem throttle interno (5min) e nunca quebra o fluxo se a API falhar.
    try:
        import threading

        def _popular_indice_scanner() -> None:
            try:
                from core_scanner_populator import popular_todos

                popular_todos()
            except Exception:
                pass

        threading.Thread(target=_popular_indice_scanner, daemon=True).start()
    except Exception:
        pass

    return itens


def agrupar_por_fase(itens: list[dict]) -> dict[str, list[dict]]:
    """Agrupa itens da esteira por fase, preservando a ordem por prazo."""
    grupos: dict[str, list[dict]] = {f: [] for f in FASES_ORDEM}
    for i in itens:
        grupos.setdefault(i["fase"], []).append(i)
    return grupos


# ------------------------------------------------------------------ #
# Acoes de fase (ESCRITA — confirmar a montante na UI)
# ------------------------------------------------------------------ #
def faturar(id_olist: int | str) -> dict:
    """Aprova (se preciso) e gera NF-e do pedido. ESCRITA FISCAL.

    Retorna dict com {ok, situacao_antes, resultado|erro}.
    """
    client = OlistClient()
    detalhe = client.obter_pedido(id_olist)
    sit = str(detalhe.get("situacao"))
    if sit == "1":
        return {"ok": True, "situacao_antes": sit, "resultado": "ja_faturado"}
    if sit not in ("3", "4", "7"):
        client.atualizar_situacao(id_olist, 3)
    res = client.gerar_nota_fiscal(id_olist)
    return {"ok": True, "situacao_antes": sit, "resultado": res}


def avancar_fase(id_olist: int | str, fase_atual: str) -> dict:
    """Avanca o pedido para a proxima situacao no Olist.

    - De PAG_APROVADO -> avanca para DANFE_GERADA (situacao 1)
    - De DANFE_GERADA -> avanca para AGUARDANDO_PLAT (situacao 4)
    - De AGUARDANDO_PLAT -> avanca para PRONTO_ENVIO (situacao 7)
    """
    client = OlistClient()
    if fase_atual == "PAG_APROVADO":
        res = client.atualizar_situacao(id_olist, 1)
        return {"ok": True, "resultado": res}
    elif fase_atual == "DANFE_GERADA":
        res = client.atualizar_situacao(id_olist, 4)
        return {"ok": True, "resultado": res}
    elif fase_atual == "AGUARDANDO_PLAT":
        res = client.atualizar_situacao(id_olist, 7)
        return {"ok": True, "resultado": res}
    return {"ok": False, "erro": "Fase invalida para avanco manual"}


def sincronizar_status_pedido(item: dict) -> dict:
    """Consulta Olist e a plataforma, reconcilia e atualiza a situacao do pedido no Olist se houver discrepancia."""
    client = OlistClient()
    id_olist = item["id_olist"]
    detalhe = client.obter_pedido(id_olist)
    situacao_atual = str(detalhe.get("situacao"))

    id_nota = detalhe.get("idNotaFiscal")
    canal = (item.get("canal") or "").lower()
    num_ecom = item.get("numero_ecommerce")

    atualizou_olist = False
    log_acoes = []

    # 1. Se tem nota fiscal, a situacao minima no Olist deve ser faturado (1)
    if id_nota and situacao_atual in ("0", "2", "3"):
        client.atualizar_situacao(id_olist, 1)
        situacao_atual = "1"
        atualizou_olist = True
        log_acoes.append("Situação no Tiny atualizada para Faturado (NF já existia)")

    # 2. Se e ML e tem shipment
    if "mercado" in canal and num_ecom:
        info = obter_prazo_despacho_ml(num_ecom)
        ship_status = info.get("status")
        if ship_status == "ready_to_ship" and situacao_atual == "1":
            client.atualizar_situacao(id_olist, 7) # Pronto para envio
            atualizou_olist = True
            log_acoes.append("Situação no Tiny atualizada para Pronto para Envio (Shipment pronto no ML)")
        elif ship_status in ("shipped", "delivered") and situacao_atual not in ("5", "6"):
            client.atualizar_situacao(id_olist, 5) # Enviado
            atualizou_olist = True
            log_acoes.append("Situação no Tiny atualizada para Enviado (Pacote despachado no ML)")

    # 3. Se e Shopee
    elif "shopee" in canal and num_ecom:
        try:
            from core_shopee import ShopeeClient
            sh_client = ShopeeClient()
            det_sh = sh_client.get_order_detail(num_ecom)
            if det_sh and isinstance(det_sh, list) and len(det_sh) > 0:
                sh_status = det_sh[0].get("order_status")
                if sh_status in ("READY_TO_SHIP", "PROCESSED") and situacao_atual in ("0", "2", "3", "1"):
                    client.atualizar_situacao(id_olist, 7) # Pronto para envio
                    atualizou_olist = True
                    log_acoes.append("Situação no Tiny atualizada para Pronto para Envio (Shopee Ready to Ship)")
                elif sh_status in ("SHIPPED", "COMPLETED") and situacao_atual not in ("5", "6"):
                    client.atualizar_situacao(id_olist, 5) # Enviado
                    atualizou_olist = True
                    log_acoes.append("Situação no Tiny atualizada para Enviado (Shopee Shipped)")
        except Exception as e:
            log_acoes.append(f"Erro ao checar Shopee: {e}")

    return {
        "ok": True,
        "atualizou": atualizou_olist,
        "acoes": log_acoes
    }




def obter_etiqueta(
    item: dict, *, formato: str = "pdf", casar_fallback: bool = True,
) -> tuple[bytes | None, str]:
    """Obtem a etiqueta do item. Olist unificada -> fallback ML (casado).

    Retorna (bytes_pdf, origem) onde origem in:
      'olist'      — etiqueta unificada nativa do Olist (etiqueta+DANFE)
      'ml+danfe'   — etiqueta ML casada com DANFE simplificada gerada (padrao Upseller)
      'ml'         — so etiqueta logistica ML (sem DANFE; casar_fallback=False ou sem nota)
      'indisponivel'
    """
    client = OlistClient()
    canal = (item.get("canal") or "").lower()

    # 1) Marketplaces com logistica propria (Mercado Envios / Shopee Envios): a
    #    etiqueta unificada do Olist NAO se aplica ("Forma de envio nao possui
    #    recurso de etiquetas" — validado em prod). Vamos direto ao fallback.
    eh_marketplace = "mercado" in canal or "shopee" in canal

    # Para frete proprio (transportadora/correios contratado), tentar a etiqueta
    # unificada nativa do Olist (etiqueta+DANFE casada, padrao Upseller).
    if not eh_marketplace:
        for exp in client.listar_expedicoes():
            if str(exp.get("idPedido", "")) == str(item.get("id_olist")) or \
               str(exp.get("numeroPedido", "")) == str(item.get("numero")):
                pdf = client.baixar_etiqueta_olist(exp.get("id"))
                if pdf:
                    return pdf, "olist"

    # 2) SHOPEE: baixa a etiqueta termica via API Shopee e funde a DANFE.
    if "shopee" in canal:
        etiqueta_sp = _baixar_etiqueta_shopee(item)
        if not etiqueta_sp:
            # Fallback: tenta buscar etiqueta no Olist
            for exp in client.listar_expedicoes():
                if str(exp.get("idPedido", "")) == str(item.get("id_olist")) or \
                   str(exp.get("numeroPedido", "")) == str(item.get("numero")):
                    pdf = client.baixar_etiqueta_olist(exp.get("id"))
                    if pdf:
                        return pdf, "olist"
            if item.get("shopee_status_erro") == "UNPAID":
                return None, "shopee_unpaid"
            elif item.get("shopee_status_erro"):
                return None, f"shopee_api_error: {item.get('shopee_status_erro')}"
            return None, "indisponivel"
        if casar_fallback and formato == "pdf":
            nota = _obter_nota_do_pedido(client, item)
            if nota:
                try:
                    import core_etiqueta_merge as merge
                    return merge.compor_unificada(etiqueta_sp, nota), "shopee+danfe"
                except Exception:
                    pass
        return etiqueta_sp, "shopee"

    # 3) ML: etiqueta logistica do ML + DANFE casada.
    if not item.get("shipment_id"):
        # Fallback: tenta buscar etiqueta no Olist
        for exp in client.listar_expedicoes():
            if str(exp.get("idPedido", "")) == str(item.get("id_olist")) or \
               str(exp.get("numeroPedido", "")) == str(item.get("numero")):
                pdf = client.baixar_etiqueta_olist(exp.get("id"))
                if pdf:
                    return pdf, "olist"
        return None, "indisponivel"
    etiqueta_ml = baixar_etiqueta_ml(item["shipment_id"], formato=formato)
    if not etiqueta_ml:
        # Fallback: tenta buscar etiqueta no Olist
        for exp in client.listar_expedicoes():
            if str(exp.get("idPedido", "")) == str(item.get("id_olist")) or \
               str(exp.get("numeroPedido", "")) == str(item.get("numero")):
                pdf = client.baixar_etiqueta_olist(exp.get("id"))
                if pdf:
                    return pdf, "olist"
        return None, "indisponivel"

    # 3a) Unificar no padrao Upseller pag.1: faixa fiscal fina (NF+chave+barras)
    #     no topo + etiqueta ML logo abaixo. Precisa da nota emitida.
    if casar_fallback and formato == "pdf":
        nota = _obter_nota_do_pedido(client, item)
        if nota:
            try:
                import core_etiqueta_merge as merge
                unificada = merge.compor_unificada(etiqueta_ml, nota)
                return unificada, "ml+danfe"
            except Exception:
                pass  # cai para etiqueta crua

    return etiqueta_ml, "ml"


def _baixar_etiqueta_shopee(item: dict) -> bytes | None:
    """Baixa a etiqueta termica (10x15) do pedido Shopee via API.

    O order_sn da Shopee = numeroPedidoEcommerce do Olist.
    """
    order_sn = item.get("numero_ecommerce")
    if not order_sn:
        return None
    try:
        from core_shopee import ShopeeClient
        c = ShopeeClient()
        # resolve package_number (alguns pedidos exigem)
        pkg = ""
        try:
            det = c.get_order_detail(order_sn)
            if det:
                status = det[0].get("order_status")
                if status == "UNPAID":
                    item["shopee_status_erro"] = "UNPAID"
                pl = det[0].get("package_list") or []
                if pl:
                    pkg = pl[0].get("package_number", "")
        except Exception as e:
            item["shopee_status_erro"] = f"api_error: {e}"
        return c.etiqueta_termica(order_sn, package_number=pkg)
    except Exception as e:
        if "shopee_status_erro" not in item:
            item["shopee_status_erro"] = str(e)
        return None


def _obter_nota_do_pedido(client: OlistClient, item: dict) -> dict | None:
    """Localiza a nota emitida do pedido (por chave de acesso via listagem)."""
    detalhe = client.obter_pedido(item["id_olist"])
    id_nota = detalhe.get("idNotaFiscal")
    if id_nota:
        try:
            return client.obter_nota(id_nota)
        except OlistError:
            pass
    return None


def unir_pdfs(lista_pdfs: list[bytes]) -> bytes:
    """Mescla multiplos PDFs em um unico PDF em formato bytes."""
    from pypdf import PdfReader, PdfWriter
    import io
    writer = PdfWriter()
    for pdf_bytes in lista_pdfs:
        if pdf_bytes:
            try:
                reader = PdfReader(io.BytesIO(pdf_bytes))
                for page in reader.pages:
                    writer.add_page(page)
            except Exception:
                pass
    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()


# ------------------------------------------------------------------ #
# Diagnostico
# ------------------------------------------------------------------ #
if __name__ == "__main__":
    print("== Carregando esteira (com prazo ML) ==")
    itens = carregar_esteira(enriquecer_ml=True, incluir_despachados=True)
    print(f"Total: {len(itens)}\n")
    for i in itens:
        prazo = i["prazo_despacho"].strftime("%d/%m %H:%M") if i["prazo_despacho"] else "—"
        print(
            f"  [{FASE_LABEL.get(i['fase'], i['fase'])}] #{i['numero']} "
            f"{i['cliente'][:22]:22} | {i['canal']} | prazo {prazo} "
            f"| ship {i['ship_status'] or '-'} ({i['shipment_id'] or '-'})"
        )
