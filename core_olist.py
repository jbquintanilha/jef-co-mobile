import core_env_loader
# ==============================================================================
# NOME DO SCRIPT: core_olist.py
# DESCRICAO: Cliente universal da API V3 (OAuth2) do Olist/Tiny ERP. Centraliza
#            auth com refresh+persistencia, throttle/backoff Cloudflare, e as
#            operacoes de pedidos/notas/expedicao usadas pela Esteira de Vendas.
# AUTOR: Conselho J&F Co. - Terminador (001)
# VERSAO: 1.0
# DATA: 2026-06-17
# STATUS: Operacional
# REF: plano Esteira de Vendas; estudos _INBOX/400.8xx_olist_*; faturar_pedido_olist.py
# ==============================================================================
"""Cliente V3 do Olist/Tiny.

Diferente de `core_olist_auth.py` (API V2, token simples por query string), este
modulo fala a API V3 RESTful via OAuth2 Bearer. O refresh_token tem escopo
`offline_access` (nao expira); o access_token vale ~4h e e renovado sob demanda.

Pontos aprendidos (validados 2026-06-17):
- Rate limit V3 = 120 req/min. Backoff de 15s em HTTP 429 / codigo 6.
- Cloudflare exige User-Agent realista — sem isso, 403.
- Listagem de pedidos: GET /pedidos (resumido). Detalhe: GET /pedidos/{id}.
- Faturar: PUT /pedidos/{id}/situacao {"situacao":3} -> POST /pedidos/{id}/gerar-nota-fiscal.
- Nota: GET /notas/{id}, /notas/{id}/link (DANFE), /notas/{id}/xml.
- Expedicao: GET /expedicao (lista). Etiqueta unificada: descoberta dinamica
  (ver baixar_etiqueta_olist) — depende da expedicao existir.
"""

import os
import time
import requests
from dotenv import load_dotenv

ENV_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
load_dotenv(ENV_PATH)

API_BASE = "https://api.tiny.com.br/public-api/v3"
TOKEN_URL = "https://accounts.tiny.com.br/realms/tiny/protocol/openid-connect/token"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"
)

# Situacao do pedido (V3). Fonte: estudo 400.830 + verificacao em prod.
# OBS: os docs divergem; tratamos os codigos abaixo de forma defensiva.
SITUACAO_PEDIDO = {
    "0": "Aberto",
    "1": "Faturado",
    "2": "Em separacao",
    "3": "Aprovado",
    "4": "Preparando envio",
    "5": "Enviado",
    "6": "Entregue",
    "7": "Pronto para envio",
    "8": "Nao entregue",
    "9": "Cancelado",
}

# Situacao da nota (V3). 7 = autorizada/emitida (observado em prod p/ NF 000001).
SITUACAO_NOTA = {
    "0": "Pendente",
    "1": "Emitida",
    "2": "Cancelada",
    "3": "Aguardando autorizacao",
    "7": "Autorizada",
}


class OlistError(RuntimeError):
    """Erro de chamada a API V3 do Olist."""


# ------------------------------------------------------------------ #
# Auth
# ------------------------------------------------------------------ #
# IMPORTANTE (corrigido 2026-06-26): o refresh_token V3 do Olist EXPIRA em ~24h
# (campo exp no JWT), apesar do escopo offline_access. Se ficar parado >24h,
# morre e exige re-OAuth manual. Por isso: (a) refresh rotativo persistido em
# Supabase + .env (igual core_ml_auth), (b) keepalive agendado renova a cada 12h
# mantendo vivo mesmo sem uso. Ver scripts/olist_token_keepalive.py.

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_SERVICE_KEY") or os.getenv("SUPABASE_KEY")


def _sb_headers() -> dict:
    return {
        "apikey": SUPABASE_KEY or "",
        "Authorization": f"Bearer {SUPABASE_KEY or ''}",
        "Content-Type": "application/json",
        "Prefer": "resolution=merge-duplicates",
    }


def _salvar_refresh_supabase(refresh_token: str) -> None:
    """Persiste o refresh_token rotacionado no Supabase (tabela olist_tokens)."""
    if not (SUPABASE_URL and SUPABASE_KEY):
        return
    import datetime as _d
    payload = {
        "id": "olist",
        "refresh_token": refresh_token,
        "updated_at": _d.datetime.now(_d.timezone.utc).isoformat(),
    }
    try:
        requests.post(f"{SUPABASE_URL}/rest/v1/olist_tokens",
                      headers=_sb_headers(), json=payload, timeout=10)
    except requests.RequestException:
        pass


def _carregar_refresh_supabase() -> str | None:
    """Carrega o refresh_token mais recente do Supabase (fonte da verdade)."""
    if not (SUPABASE_URL and SUPABASE_KEY):
        return None
    try:
        r = requests.get(
            f"{SUPABASE_URL}/rest/v1/olist_tokens?id=eq.olist&select=refresh_token",
            headers=_sb_headers(), timeout=10)
        if r.status_code == 200 and r.json():
            return r.json()[0].get("refresh_token")
    except requests.RequestException:
        pass
    return None


def _atualizar_env(chave: str, valor: str) -> None:
    """Persiste/atualiza uma chave no .env de forma robusta (preserva o resto)."""
    if not os.path.exists(ENV_PATH):
        return
    with open(ENV_PATH, "r", encoding="utf-8") as f:
        linhas = f.readlines()
    nova = f'{chave}="{valor}"\n'
    for i, linha in enumerate(linhas):
        if linha.strip().startswith(f"{chave}="):
            linhas[i] = nova
            break
    else:
        linhas.append(nova)
    with open(ENV_PATH, "w", encoding="utf-8") as f:
        f.writelines(linhas)
    os.environ[chave] = valor


def obter_access_token() -> str:
    """Renova o access_token via refresh_token. Persiste o novo refresh no .env.

    Levanta OlistError se as credenciais estiverem ausentes ou o refresh falhar.
    """
    client_id = os.getenv("OLIST_CLIENT_ID")
    client_secret = os.getenv("OLIST_CLIENT_SECRET")
    # Fonte da verdade do refresh = Supabase (rotativo, compartilhado entre
    # dashboard/keepalive/scripts). Fallback no .env local.
    refresh_token = _carregar_refresh_supabase() or os.getenv("OLIST_REFRESH_TOKEN")
    if not (client_id and client_secret and refresh_token):
        raise OlistError(
            "Credenciais V3 ausentes "
            "(OLIST_CLIENT_ID, OLIST_CLIENT_SECRET, OLIST_REFRESH_TOKEN)."
        )

    # IMPORTANTE (validado 2026-06-21): o token endpoint V3 exige client_id +
    # client_secret via HTTP Basic auth (header Authorization), NAO no body.
    # Enviar no body retorna 400 invalid_grant "Token is not active".
    payload = {
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
    }
    headers = {
        "Content-Type": "application/x-www-form-urlencoded",
        "User-Agent": USER_AGENT,
    }
    ultimo_erro = ""
    for tentativa in range(3):
        try:
            r = requests.post(
                TOKEN_URL, data=payload, headers=headers,
                auth=(client_id, client_secret), timeout=20,
            )
            if r.status_code == 200:
                data = r.json()
                novo_refresh = data.get("refresh_token")
                if novo_refresh:
                    _atualizar_env("OLIST_REFRESH_TOKEN", novo_refresh)
                    _salvar_refresh_supabase(novo_refresh)  # rotativo compartilhado
                return data["access_token"]
            ultimo_erro = f"{r.status_code} {r.text[:200]}"
        except requests.RequestException as e:
            ultimo_erro = str(e)
        time.sleep(2)
    raise OlistError(f"Falha ao autenticar na API V3: {ultimo_erro}")


# ------------------------------------------------------------------ #
# Cliente
# ------------------------------------------------------------------ #
class OlistClient:
    """Cliente fino da API V3. Mantem o access_token em memoria e renova sob 401.

    Uso:
        c = OlistClient()
        pedidos = c.listar_pedidos(situacao=0)
    """

    def __init__(self, access_token: str | None = None):
        self._token = access_token or obter_access_token()

    # -- baixo nivel ------------------------------------------------ #
    def _headers(self, accept: str = "application/json") -> dict:
        return {
            "Authorization": f"Bearer {self._token}",
            "Accept": accept,
            "User-Agent": USER_AGENT,
        }

    def request(
        self,
        method: str,
        endpoint: str,
        *,
        params: dict | None = None,
        json_payload: dict | None = None,
        accept: str = "application/json",
        raw: bool = False,
    ):
        """Chamada generica com throttle/backoff e auto-refresh de token.

        - 429 / codigo 6 -> dorme 15s e retenta.
        - 401 -> renova token uma vez e retenta.
        - raw=True -> retorna o objeto Response (para baixar PDF/binario).
        """
        url = f"{API_BASE}{endpoint}"
        renovou = False
        for tentativa in range(6):
            headers = self._headers(accept)
            if json_payload is not None:
                headers["Content-Type"] = "application/json"
            resp = requests.request(
                method, url, headers=headers, params=params,
                json=json_payload, timeout=30,
            )

            if resp.status_code == 401 and not renovou:
                self._token = obter_access_token()
                renovou = True
                continue
            if resp.status_code == 429:
                time.sleep(15)
                continue
            if resp.status_code >= 500:
                time.sleep(5)
                continue
            if resp.status_code in (200, 201, 204):
                if raw:
                    return resp
                if not resp.content:
                    return {}
                data = resp.json()
                # erro logico estilo Tiny (raro na V3, mas defensivo)
                if isinstance(data, dict) and data.get("erros"):
                    msg = str(data["erros"])
                    if "excedido" in msg.lower() or "bloquead" in msg.lower():
                        time.sleep(15)
                        continue
                return data
            # erro definitivo
            raise OlistError(f"{method} {endpoint} -> {resp.status_code}: {resp.text[:300]}")
        raise OlistError(f"Falha persistente em {method} {endpoint}")

    # -- pedidos ---------------------------------------------------- #
    def listar_pedidos(
        self,
        *,
        situacao: int | None = None,
        limit: int = 100,
        offset: int = 0,
        extra_params: dict | None = None,
    ) -> list[dict]:
        """Lista pedidos (resumido). Filtra por situacao quando informado.

        Retorna a lista `itens`. Pagina manualmente via offset se precisar de mais.
        """
        params = {"limit": limit, "offset": offset}
        if situacao is not None:
            params["situacao"] = situacao
        if extra_params:
            params.update(extra_params)
        data = self.request("GET", "/pedidos", params=params)
        return data.get("itens", [])

    def listar_pedidos_todos(self, *, situacao: int | None = None, max_paginas: int = 20) -> list[dict]:
        """Pagina /pedidos ate esgotar (ou max_paginas). Throttle implicito no request."""
        todos: list[dict] = []
        offset = 0
        for _ in range(max_paginas):
            lote = self.listar_pedidos(situacao=situacao, limit=100, offset=offset)
            if not lote:
                break
            todos.extend(lote)
            if len(lote) < 100:
                break
            offset += 100
            time.sleep(0.6)
        return todos

    def obter_pedido(self, id_pedido: int | str) -> dict:
        """Detalhe completo de um pedido."""
        return self.request("GET", f"/pedidos/{id_pedido}")

    def buscar_pedido_por_ecommerce(self, numero_ecommerce: str) -> dict | None:
        """Localiza pedido pelo numero do e-commerce (ML/Shopee). None se nao achar."""
        data = self.request(
            "GET", "/pedidos",
            params={"numeroPedidoEcommerce": numero_ecommerce, "limit": 5},
        )
        itens = data.get("itens", [])
        return itens[0] if itens else None

    def atualizar_situacao(self, id_pedido: int | str, situacao: int) -> dict:
        """PUT /pedidos/{id}/situacao. ESCRITA — exige confirmacao a montante."""
        return self.request(
            "PUT", f"/pedidos/{id_pedido}/situacao",
            json_payload={"situacao": situacao},
        )

    def gerar_nota_fiscal(
        self, id_pedido: int | str, *, enviar_email: bool = True,
        identificar_consumidor: bool = True,
    ) -> dict:
        """POST /pedidos/{id}/gerar-nota-fiscal. ESCRITA FISCAL — confirmar antes.

        Se o pedido nao estiver em situacao que libera faturamento (3/4/7),
        o chamador deve aprovar antes via atualizar_situacao(id, 3).
        """
        return self.request(
            "POST", f"/pedidos/{id_pedido}/gerar-nota-fiscal",
            json_payload={
                "enviarEml": enviar_email,
                "identificarConsumidor": identificar_consumidor,
            },
        )

    # -- notas ------------------------------------------------------ #
    def listar_notas(self, *, limit: int = 100, offset: int = 0) -> list[dict]:
        data = self.request("GET", "/notas", params={"limit": limit, "offset": offset})
        return data.get("itens", [])

    def obter_nota(self, id_nota: int | str) -> dict:
        return self.request("GET", f"/notas/{id_nota}")

    def link_danfe(self, id_nota: int | str) -> str | None:
        """GET /notas/{id}/link — URL publica da DANFE (PDF). None se indisponivel."""
        data = self.request("GET", f"/notas/{id_nota}/link")
        return data.get("link")

    def xml_nota(self, id_nota: int | str) -> dict:
        """GET /notas/{id}/xml — {xmlNfe, xmlCancelamento}."""
        return self.request("GET", f"/notas/{id_nota}/xml")

    # -- expedicao / etiqueta -------------------------------------- #
    def listar_expedicoes(self, *, limit: int = 50, offset: int = 0) -> list[dict]:
        data = self.request("GET", "/expedicao", params={"limit": limit, "offset": offset})
        return data.get("itens", [])

    def obter_expedicao(self, id_agrupamento: int | str) -> dict:
        return self.request("GET", f"/expedicao/{id_agrupamento}")

    def criar_expedicao(self, ids_notas: list[int]) -> dict:
        """POST /expedicao — cria agrupamento de expedicao para as notas dadas.

        Campo confirmado em prod: idsNotasFiscais. Retorna {"id": <agrupamento>}.
        Erro se a nota ja foi expedida.
        """
        return self.request("POST", "/expedicao", json_payload={"idsNotasFiscais": list(ids_notas)})

    def concluir_expedicao(self, id_agrupamento: int | str) -> dict:
        """POST /expedicao/{id}/concluir — fecha o agrupamento (libera etiquetas)."""
        return self.request("POST", f"/expedicao/{id_agrupamento}/concluir")

    def baixar_etiqueta_olist(self, id_agrupamento: int | str) -> bytes | None:
        """Baixa a etiqueta unificada (etiqueta+DANFE) de um agrupamento de expedicao.

        IMPORTANTE (validado em prod 2026-06-17): so funciona para formas de
        envio com gateway logistico do Olist. Para Mercado Envios / Shopee Envios,
        a API responde 400 "Forma de envio nao possui recurso de etiquetas" — a
        etiqueta vem da plataforma (usar fallback ML em core_esteira).

        Pre-condicao: o agrupamento precisa estar CONCLUIDO (concluir_expedicao).
        Retorna bytes do PDF ou None.
        """
        try:
            resp = self.request(
                "GET", f"/expedicao/{id_agrupamento}/etiquetas",
                accept="application/pdf", raw=True,
            )
        except OlistError:
            return None
        ct = resp.headers.get("content-type", "")
        if "pdf" in ct.lower():
            return resp.content
        return None


# ------------------------------------------------------------------ #
# Diagnostico
# ------------------------------------------------------------------ #
if __name__ == "__main__":
    c = OlistClient()
    print("[OK] autenticado na V3")
    abertos = c.listar_pedidos(situacao=0, limit=5)
    print(f"Pedidos abertos (situacao=0): {len(abertos)}")
    for p in abertos:
        ecom = p.get("ecommerce", {})
        print(
            f"  #{p.get('numeroPedido')} | {p.get('cliente', {}).get('nome', '?')} "
            f"| {ecom.get('nome', '-')} {ecom.get('numeroPedidoEcommerce', '')} "
            f"| R$ {p.get('valor')} | prev {p.get('dataPrevista')}"
        )
