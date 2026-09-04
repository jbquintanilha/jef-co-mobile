# ==============================================================================
# NOME DO SCRIPT: core_tiktok_token_store.py
# DESCRICAO: Guarda o access_token do TikTok Shop no Supabase (nuvem)
# FUNCAO: Dar ao app online um lugar PERSISTENTE para o token renovado
# STATUS: ATIVO
# VERSAO: 1.0
# DATA: 04/09/2026
# AUTOR: Terminador (Claude) / J&F Co.
# ==============================================================================
"""Persistencia do token do TikTok Shop na nuvem.

PROBLEMA (Jota, 03-04/09/2026): a Esteira online quebrava com 105002 e
voltava a quebrar mesmo depois do auto-refresh. Motivo:

  - o token da Shop vive ~7 dias;
  - `cmd_refresh()` renova e grava no `.env` -- que NAO EXISTE na nuvem;
  - `core_env_loader.carregar_tudo()` roda a cada `get_secret` e repoe o
    token ANTIGO (do blob embutido) por cima do que estava em memoria.

Ou seja: o refresh acontecia, mas o valor novo era descartado na leitura
seguinte. Sem um lugar que sobreviva ao restart, o app volta ao token
morto.

Aqui a tabela `ml_tokens` do Supabase e' reaproveitada com
`id="tiktokshop"` -- mesmo esquema ja' usado pelo Mercado Livre
(`core_ml_auth`), sem DDL nova. O registro do ML nao e' tocado.

Ordem de precedencia na leitura (`token_valido`):
  1. Supabase  -- unico que sobrevive a um restart na nuvem
  2. get_secret -- Streamlit Secrets / blob, se o banco estiver fora

Nunca levanta: token vencido ou banco indisponivel devolvem "" e o
chamador cai no caminho de sempre.
"""

from __future__ import annotations

import datetime
import logging
import os
import time
from typing import Any

import requests

log = logging.getLogger(__name__)

TABELA = "ml_tokens"          # tabela generica de tokens; id separa o canal
REGISTRO = "tiktokshop"

# Margem antes do vencimento real. Renovar faltando 10 min evita o caso de
# o token morrer no meio de um lote de etiquetas ja' comecado.
FOLGA_S = 600


def _cred() -> tuple[str, str]:
    """(url, key) do Supabase. Vazio se nao configurado."""
    try:
        from core_env_loader import get_secret
        url = get_secret("SUPABASE_URL", "")
        key = (get_secret("SUPABASE_SERVICE_KEY", "")
               or get_secret("SUPABASE_KEY", ""))
    except Exception:
        url = os.getenv("SUPABASE_URL", "")
        key = (os.getenv("SUPABASE_SERVICE_KEY", "")
               or os.getenv("SUPABASE_KEY", ""))
    return url, key


def _headers(key: str, escrita: bool = False) -> dict:
    h = {"apikey": key, "Authorization": f"Bearer {key}"}
    if escrita:
        h["Content-Type"] = "application/json"
        # upsert: o registro e' unico por canal, sempre sobrescreve
        h["Prefer"] = "resolution=merge-duplicates"
    return h


def carregar() -> dict[str, Any]:
    """Registro do Supabase, ou {} se indisponivel. Nunca levanta."""
    url, key = _cred()
    if not url or not key:
        return {}
    try:
        r = requests.get(
            f"{url}/rest/v1/{TABELA}?id=eq.{REGISTRO}&select=*",
            headers=_headers(key), timeout=10,
        )
        if r.status_code == 200:
            linhas = r.json() or []
            return linhas[0] if linhas else {}
        log.warning("Supabase %s ao ler token TikTok", r.status_code)
    except Exception as exc:
        log.warning("Supabase indisponivel ao ler token TikTok: %s", exc)
    return {}


def salvar(access_token: str, refresh_token: str = "",
           expira_em: int = 0) -> bool:
    """Grava o token. `expira_em` e' epoch absoluto (nao duracao)."""
    if not access_token:
        return False
    url, key = _cred()
    if not url or not key:
        return False

    payload = {
        "id": REGISTRO,
        "access_token": access_token,
        "updated_at": datetime.datetime.now(
            datetime.timezone.utc).isoformat(),
    }
    if refresh_token:
        payload["refresh_token"] = refresh_token
    if expira_em:
        payload["expires_in"] = int(expira_em)

    try:
        r = requests.post(f"{url}/rest/v1/{TABELA}",
                          headers=_headers(key, escrita=True),
                          json=payload, timeout=15)
        if r.status_code in (200, 201, 204):
            log.info("Token TikTok salvo no Supabase.")
            return True
        log.warning("Supabase %s ao salvar token: %s",
                    r.status_code, r.text[:120])
    except Exception as exc:
        log.warning("Supabase indisponivel ao salvar token: %s", exc)
    return False


def token_valido() -> str:
    """Token do Supabase se ainda estiver dentro do prazo; senao "".

    ⚠️ Devolve "" tambem quando o registro nao traz `expires_in`: sem saber
    o vencimento nao da' para afirmar que serve, e um token morto daqui
    silenciaria o valor bom do Secrets.
    """
    reg = carregar()
    tok = (reg.get("access_token") or "").strip()
    if not tok:
        return ""
    exp = reg.get("expires_in")
    try:
        exp = int(exp or 0)
    except (TypeError, ValueError):
        return ""
    if not exp or time.time() > (exp - FOLGA_S):
        return ""
    return tok
