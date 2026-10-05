# ==============================================================================
# NOME DO SCRIPT: core_esteira_snapshot.py
# DESCRICAO: Instantaneo da fila de separacao da Esteira no Supabase -- carregar
#            no PC e continuar no celular (uma base so').
# STATUS: ATIVO
# VERSAO: 1.0
# DATA: 05/10/2026
# AUTOR: Terminador (001) / Claude
# ==============================================================================
"""Instantaneo da fila da Esteira (tabela `esteira_fila_snapshot`).

O "Atualizar fila" baixa os pedidos do Olist e monta a lista de separacao em
memoria (`st.session_state`) -- some no F5 e precisava ser refeito no celular
(2o download). Aqui o resultado do download (SO' os pedidos brutos) e' salvo no
Supabase; qualquer aparelho abre o mais recente e recalcula a lista localmente
(`processar_batch_picking` e' deterministico, entao nao se guarda o derivado).

Guarda os 5 ultimos. Acesso pela chave de servico dos apps (tabela com RLS e sem
politica publica). Falha de rede NUNCA derruba a Esteira: devolve None/False.
"""

from __future__ import annotations

import datetime as _dt
import logging
from typing import Any

import requests

log = logging.getLogger(__name__)

TABELA = "esteira_fila_snapshot"
MANTER = 5
TIMEOUT_S = 8


class _Cfg:
    """URL + cabecalhos com a chave de SERVICO (a tabela e' fechada ao publico)."""

    def __init__(self, url: str, key: str):
        self.SUPABASE_URL = url
        self.SUPABASE_KEY = key

    def _headers(self) -> dict:
        return {"apikey": self.SUPABASE_KEY, "Authorization": f"Bearer {self.SUPABASE_KEY}",
                "Content-Type": "application/json"}


def _cfg():
    """O instantaneo guarda o pedido BRUTO do Olist (endereco, CPF): por isso a tabela
    `esteira_fila_snapshot` tem RLS e NENHUMA politica publica -- so' a chave de servico
    entra. No app da nuvem ela vem no mesmo bloco embutido (`SUPABASE_SERVICE_KEY`)."""
    import os
    try:
        import core_env_loader  # noqa: F401  (repoe as chaves embutidas no ambiente da nuvem)
    except ImportError:
        pass
    import core_scanner_supabase as nuvem
    url = nuvem.SUPABASE_URL
    key = (os.environ.get("SUPABASE_SERVICE_KEY") or os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
           or nuvem.SUPABASE_KEY)
    if not (url and key):
        return None
    return _Cfg(url, key)


def salvar(pedidos: list[dict], situacoes: Any, criado_por: str) -> bool:
    """Grava o instantaneo e poda os antigos. Devolve True se gravou."""
    try:
        n = _cfg()
        if n is None or not pedidos:
            return False
        h = dict(n._headers())
        h["Prefer"] = "return=representation"
        r = requests.post(f"{n.SUPABASE_URL}/rest/v1/{TABELA}", headers=h, timeout=TIMEOUT_S + 12,
                          json={"criado_por": criado_por, "situacoes": ",".join(str(s) for s in (situacoes or [])),
                                "total": len(pedidos), "dados": {"pedidos": pedidos}})
        if r.status_code not in (200, 201):
            log.warning("Snapshot da fila nao gravou: HTTP %s %s", r.status_code, r.text[:120])
            return False
        _podar(n)
        return True
    except Exception as exc:
        log.warning("Snapshot da fila nao gravou: %s", exc)
        return False


def _podar(n) -> None:
    """Mantem so' os ultimos MANTER instantaneos."""
    try:
        h = n._headers()
        r = requests.get(f"{n.SUPABASE_URL}/rest/v1/{TABELA}", headers=h, timeout=TIMEOUT_S,
                         params={"select": "id", "order": "criado_em.desc", "offset": str(MANTER), "limit": "100"})
        ids = [x["id"] for x in r.json()] if r.status_code == 200 else []
        if ids:
            requests.delete(f"{n.SUPABASE_URL}/rest/v1/{TABELA}", headers=h, timeout=TIMEOUT_S,
                            params={"id": f"in.({','.join(str(i) for i in ids)})"})
    except Exception as exc:
        log.warning("Poda dos snapshots falhou: %s", exc)


def carregar_ultimo(max_horas: float = 12.0) -> dict | None:
    """Instantaneo mais recente com ate' `max_horas`; None se nao houver/falhar.

    Devolve {pedidos, criado_em (datetime UTC), criado_por, situacoes, total}.
    """
    try:
        n = _cfg()
        if n is None:
            return None
        r = requests.get(f"{n.SUPABASE_URL}/rest/v1/{TABELA}", headers=n._headers(), timeout=TIMEOUT_S + 12,
                         params={"select": "*", "order": "criado_em.desc", "limit": "1"})
        if r.status_code != 200 or not r.json():
            return None
        linha = r.json()[0]
        quando = _dt.datetime.fromisoformat(str(linha["criado_em"]).replace("Z", "+00:00"))
        idade_h = (_dt.datetime.now(_dt.timezone.utc) - quando).total_seconds() / 3600
        if idade_h > max_horas:
            return None
        pedidos = (linha.get("dados") or {}).get("pedidos") or []
        if not pedidos:
            return None
        return {"pedidos": pedidos, "criado_em": quando, "criado_por": linha.get("criado_por") or "?",
                "situacoes": linha.get("situacoes") or "", "total": linha.get("total") or len(pedidos)}
    except Exception as exc:
        log.warning("Snapshot da fila nao carregou: %s", exc)
        return None


def hora_brasilia(quando: _dt.datetime) -> str:
    return quando.astimezone(_dt.timezone(_dt.timedelta(hours=-3))).strftime("%d/%m %H:%M")
