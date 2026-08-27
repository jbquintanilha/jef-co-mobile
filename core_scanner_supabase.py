# ==============================================================================
# NOME DO SCRIPT: core_scanner_supabase.py
# DESCRICAO: Camada REST Supabase do Scanner e Esteira de Expedicao J&F Co.
# FUNCAO: Permite persistencia e sincronizacao em nuvem (PC <-> Celular)
# STATUS: ATIVO
# MOTOR: Monge (003)
# VERSAO: 1.0
# DATA: 26/08/2026
# AUTOR: Violino (000)
# ==============================================================================

from __future__ import annotations

import logging
import os
import time
from datetime import date, datetime
from typing import Any

import requests
from dotenv import load_dotenv

load_dotenv(dotenv_path=r"c:\JF_Automacoes\.env")

log = logging.getLogger("core_scanner_supabase")

SUPABASE_URL = os.getenv("SUPABASE_URL", "").rstrip("/")
SUPABASE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY") or os.getenv("SUPABASE_ANON_KEY", "")

RETRY_DELAYS = [2, 4, 8, 16]


def _headers() -> dict[str, str]:
    return {
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Content-Type": "application/json",
        "Prefer": "return=representation",
    }


def _requisicao_supabase(metodo: str, endpoint: str, params: dict | None = None, payload: Any = None) -> Any:
    """Executa requisicao REST contra o Supabase com retry e backoff exponencial."""
    if not SUPABASE_URL or not SUPABASE_KEY:
        log.warning("Supabase URL ou Key nao configurados no ambiente.")
        return None

    url = f"{SUPABASE_URL}/rest/v1/{endpoint.lstrip('/')}"
    headers = _headers()

    for tentativa, delay in enumerate(RETRY_DELAYS):
        try:
            r = requests.request(metodo, url, headers=headers, params=params, json=payload, timeout=30)
            if r.status_code in (200, 201, 204):
                if r.status_code == 204 or not r.text:
                    return True
                return r.json()
            if r.status_code == 404:
                return None
            if r.status_code in (429, 500, 502, 503, 504):
                time.sleep(delay)
                continue
            log.warning(f"Erro Supabase REST ({r.status_code}): {r.text}")
            return None
        except (requests.exceptions.Timeout, requests.exceptions.ConnectionError) as e:
            log.warning(f"Timeout/Conexao Supabase tentativa {tentativa + 1}: {e}")
            time.sleep(delay)

    log.error("Falha ao comunicar com Supabase apos retries.")
    return None


def salvar_rastreio_nuvem(dados: dict[str, Any]) -> bool:
    """Salva/atualiza registro de rastreio na tabela `rastreio_pedidos_expedicao` do Supabase."""
    tracking = dados.get("tracking")
    if not tracking:
        return False

    payload = {
        "tracking": tracking.strip().upper(),
        "canal": dados.get("canal", "manual"),
        "pedido_ecommerce": str(dados.get("pedido_ecommerce", "")),
        "sku_principal": dados.get("sku_principal", ""),
        "produto_nome": dados.get("produto_nome", ""),
        "cor": dados.get("cor", ""),
        "kit": dados.get("kit", ""),
        "cliente_nome": dados.get("cliente_nome", ""),
        "cep": dados.get("cep", ""),
        "peso_kg": float(dados.get("peso_kg") or 0.0),
        "imagem_url": dados.get("imagem_url", ""),
        "itens_json": dados.get("itens_json", "[]"),
        "alerta_volume": dados.get("alerta_volume", ""),
        "shipment_id": str(dados.get("shipment_id", "") or ""),
        "pack_id": str(dados.get("pack_id", "") or ""),
        "atualizado_em": datetime.now().isoformat(),
    }

    # Upsert com header Prefer: resolution=merge-duplicates
    headers = _headers()
    headers["Prefer"] = "resolution=merge-duplicates,return=minimal"
    url = f"{SUPABASE_URL}/rest/v1/rastreio_pedidos_expedicao"

    for _, delay in enumerate(RETRY_DELAYS):
        try:
            r = requests.post(url, headers=headers, json=payload, timeout=30)
            if r.status_code in (200, 201, 204):
                return True
            time.sleep(delay)
        except Exception:
            time.sleep(delay)
    return False


def buscar_rastreio_nuvem(codigo: str) -> dict[str, Any] | None:
    """Busca pedido por tracking, shipment_id ou numero do pedido no Supabase."""
    cod = codigo.strip().upper()
    
    # 1. Match por tracking
    params = {"tracking": f"eq.{cod}", "select": "*", "limit": "1"}
    res = _requisicao_supabase("GET", "rastreio_pedidos_expedicao", params=params)
    if res and len(res) > 0:
        return res[0]

    # 2. Match por shipment_id
    params = {"shipment_id": f"eq.{cod}", "select": "*", "limit": "1"}
    res = _requisicao_supabase("GET", "rastreio_pedidos_expedicao", params=params)
    if res and len(res) > 0:
        return res[0]

    # 3. Match por pedido_ecommerce
    params = {"pedido_ecommerce": f"eq.{cod}", "select": "*", "limit": "1"}
    res = _requisicao_supabase("GET", "rastreio_pedidos_expedicao", params=params)
    if res and len(res) > 0:
        return res[0]

    return None


def registrar_conferencia_nuvem(tracking: str, conferido_por: str = "mobile") -> bool:
    """Registra uma conferencia de pedido em nuvem."""
    tracking_limpo = tracking.strip().upper()
    hoje = date.today().isoformat()
    agora = datetime.now().isoformat()

    payload = {
        "tracking": tracking_limpo,
        "data_conferencia": hoje,
        "conferido_em": agora,
        "conferido_por": conferido_por,
        "status": "CONFERIDO",
    }

    headers = _headers()
    headers["Prefer"] = "resolution=merge-duplicates,return=minimal"
    url = f"{SUPABASE_URL}/rest/v1/conferencias_expedicao"

    for _, delay in enumerate(RETRY_DELAYS):
        try:
            r = requests.post(url, headers=headers, json=payload, timeout=30)
            if r.status_code in (200, 201, 204):
                return True
            time.sleep(delay)
        except Exception:
            time.sleep(delay)
    return False


def obter_metricas_dia_nuvem(data_iso: str | None = None) -> dict[str, int]:
    """Retorna contadores de conferidos no dia informado (ou hoje)."""
    dia = data_iso or date.today().isoformat()
    params = {"data_conferencia": f"eq.{dia}", "select": "tracking"}
    res = _requisicao_supabase("GET", "conferencias_expedicao", params=params)
    
    total_conferidos = len(res) if res and isinstance(res, list) else 0
    return {
        "conferidos": total_conferidos,
    }
