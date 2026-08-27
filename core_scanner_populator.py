# ==============================================================================
# NOME DO SCRIPT: core_scanner_populator.py
# DESCRICAO: Populador do indice rastreio -> pedido do Scanner de Conferencia.
#            Varre pedidos abertos (Olist) e cruza com tracking real das APIs
#            Shopee (get_tracking_number), Mercado Livre (shipment) e TikTok
#            Shop (tracking_number vem na propria listagem de pedidos).
# AUTOR: Conselho J&F Co. - Roo Code (sub-gerente operacional)
#        TikTok + cache de pedidos: Terminador (Claude), 2026-08-02
# VERSAO: 1.1
# DATA: 2026-08-02
# STATUS: Operacional (Shopee + ML + TikTok)
# REF: plans/scanner_conferencia_pedidos_2026-08-02.md
# ==============================================================================
"""Populador do indice de rastreio -> pedido.

Funcoes:
    popular_shopee(force=False) -> int   # via ShopeeClient.get_tracking_number
    popular_ml(force=False) -> int       # via core_esteira.obter_prazo_despacho_ml
    popular_tiktok(force=False) -> int   # via core_tiktokshop_orders.listar_pedidos
    popular_todos(force=False) -> dict   # consolida os 3 canais

Throttle: por padrao so refaz o refresh apos INTERVALO_MINIMO_SEG (300s), a menos
que ``force=True`` (botao "Atualizar agora" da UI). Evita martelar as APIs a cada
rerun do Streamlit.
"""

from __future__ import annotations

import logging
import time

import core_scanner_db as db
from core_scanner_decoder import sanitizar_codigo
from core_scanner_resolver import _extrair_cor, _extrair_kit

log = logging.getLogger("core_scanner_populator")

# Intervalo minimo entre refreshes automaticos (segundos).
INTERVALO_MINIMO_SEG = 300

# ------------------------------------------------------------------ #
# Escopo do indice: SO o que ainda vai ser separado/despachado.
#
# Decisao do Jota (2026-08-03): pedido ja enviado nao precisa ser bipado, entao
# nao precisa de rastreio no indice. Sem esse corte a base so crescia (147
# registros, muitos ja despachados), gastando chamada de API por pedido que
# nunca mais seria escaneado.
#
# O CORTE E' O DESPACHO NA PLATAFORMA, nao o controle interno: "Pronto para
# envio" (7) e' status daqui -- a caixa continua na bancada e ainda pode ser
# bipada, entao ENTRA. So sai do indice quando a transportadora levou (5/6).
#
# Situacoes do Olist (SITUACAO_PEDIDO em core_olist.py):
#   0 Aberto · 1 Faturado · 2 Em separacao · 3 Aprovado · 4 Preparando envio
#   7 Pronto para envio            -> ainda aqui  -> ENTRAM
#   5 Enviado · 6 Entregue         -> ja saiu     -> ficam de fora
#   8 Nao entregue · 9 Cancelado   -> nao sera separado -> ficam de fora
SITUACOES_PENDENTES = {"0", "1", "2", "3", "4", "7"}

# Janela de retencao: registros mais velhos que isto saem do indice.
DIAS_RETENCAO = 3


def _pedido_pendente(p: dict) -> bool:
    """True se o pedido ainda vai ser separado/despachado (vale indexar)."""
    return str(p.get("situacao")) in SITUACOES_PENDENTES


def limpar_antigos(dias: int = DIAS_RETENCAO) -> int:
    """Remove do indice os rastreios com mais de ``dias``. Retorna quantos saiu.

    Mantem a base enxuta: o scanner so precisa do que esta na fila de expedicao.
    """
    import sqlite3

    try:
        with sqlite3.connect(db.DB_PATH, timeout=30) as conn:
            cur = conn.execute(
                "DELETE FROM rastreio_pedidos "
                "WHERE criado_em < datetime('now','localtime', ?)",
                (f"-{int(dias)} days",),
            )
            return cur.rowcount or 0
    except Exception as e:
        log.error("Falha ao limpar rastreios antigos: %s", e)
        return 0


def limpar_ja_despachados(trackings_pendentes: set[str]) -> int:
    """Remove do indice tudo que nao apareceu como pendente na varredura atual.

    A poda por idade nao basta: um pedido pode ser despachado no mesmo dia em
    que entrou. Como cada ``popular_*`` ja percorre exatamente os pedidos que
    seguem pendentes na plataforma, o que ficou de fora dessa lista ou ja foi
    coletado, ou foi cancelado -- nos dois casos nao sera bipado.

    Chamado so quando a varredura completou sem erro; senao uma falha de API
    apagaria a base inteira por engano.
    """
    import sqlite3

    if not trackings_pendentes:
        return 0
    try:
        with sqlite3.connect(db.DB_PATH, timeout=30) as conn:
            marcadores = ",".join("?" * len(trackings_pendentes))
            cur = conn.execute(
                f"DELETE FROM rastreio_pedidos WHERE tracking NOT IN ({marcadores})",
                tuple(trackings_pendentes),
            )
            return cur.rowcount or 0
    except Exception as e:
        log.error("Falha ao podar despachados: %s", e)
        return 0

# Cache do cliente Olist (evita refresh de token a cada chamada).
_OLIST_CLIENT = None

# Timestamp do ultimo refresh (throttle em memoria — suficiente p/ Streamlit).
_ULTIMO_REFRESH: float = 0.0

# Cache curto da listagem do Olist. Sem isto, popular_todos() varria a lista
# inteira 3x (uma por canal) na mesma execucao -- 3 paginacoes completas de
# /pedidos pra obter exatamente o mesmo resultado.
_CACHE_PEDIDOS: list[dict] | None = None
_CACHE_PEDIDOS_TS: float = 0.0
_CACHE_TTL_SEG = 120


def _pedidos_olist(*, forcar: bool = False) -> list[dict]:
    """Listagem de pedidos do Olist com cache curto (TTL 120s)."""
    global _CACHE_PEDIDOS, _CACHE_PEDIDOS_TS
    agora = time.monotonic()
    if (not forcar and _CACHE_PEDIDOS is not None
            and (agora - _CACHE_PEDIDOS_TS) < _CACHE_TTL_SEG):
        return _CACHE_PEDIDOS
    _CACHE_PEDIDOS = _client_olist().listar_pedidos_todos()
    _CACHE_PEDIDOS_TS = agora
    return _CACHE_PEDIDOS


def _client_olist():
    """Retorna o OlistClient unico do processo (lazy)."""
    global _OLIST_CLIENT
    if _OLIST_CLIENT is None:
        from core_olist import OlistClient

        _OLIST_CLIENT = OlistClient()
    return _OLIST_CLIENT


def _pode_refresh() -> bool:
    """True se o intervalo minimo entre refreshes ja passou."""
    return (time.monotonic() - _ULTIMO_REFRESH) >= INTERVALO_MINIMO_SEG


def _registro_do_pedido_olist(client, p: dict, *, canal: str, tracking: str,
                              imagem_url: str = "") -> dict | None:
    """Monta o registro do indice a partir do pedido resumido do Olist.

    Busca o detalhe (itens) apenas quando ha tracking — evita N+1 desnecessario.

    ``imagem_url``: miniatura da variacao vendida. O Olist nao guarda imagem
    (campo `anexos` vem vazio), entao ela vem do proprio marketplace, aproveitando
    chamadas que o canal ja faz.

    ⚠️ A classificacao de risco (`alerta_volume`) vem de
    `core_separacao.processar_batch_picking()` — a MESMA funcao que a Esteira
    de Expedicao usa pra separar "simples" / "mesmo kit Nx" / "multi-itens"
    (Jota, 25/08: "faça ele importar a mesma lista de separação"). Antes o
    Scanner tinha logica PROPRIA pra isso na UI (`14_Scanner_Conferencia.py`,
    calculo de `_volumes`), reimplementando o mesmo criterio em codigo
    separado — foi assim que o bug do "mesmo kit comprado 2x" (achado
    25/08, [[armadilha_scanner_mesmo_kit_comprado_varias_vezes]]) apareceu:
    cada lado fazia sua propria conta e podiam divergir de novo no futuro.
    Reusando a funcao-fonte, os dois sistemas nunca mais podem discordar
    sobre o que e' "risco" — só existe uma verdade sobre isso no código.
    """
    ecom = p.get("ecommerce") or {}
    cliente = p.get("cliente") or {}
    end = cliente.get("endereco") or {}

    primeiro: dict = {}
    peso_kg = None
    itens: list[dict] = []
    detalhe: dict = {}
    if tracking:
        try:
            detalhe = client.obter_pedido(p.get("id"))
            itens = detalhe.get("itens") or []
            if itens:
                primeiro = itens[0]
                peso_kg = (
                    detalhe.get("peso")
                    or detalhe.get("pesoBruto")
                    or (primeiro.get("produto") or {}).get("peso")
                    or None
                )
        except Exception as e:
            log.warning("Falha ao obter detalhe do pedido Olist %s: %s", p.get("id"), e)

    prod = primeiro.get("produto") or primeiro
    sku = str(prod.get("sku") or prod.get("codigo") or "")

    # Lista COMPLETA de itens. Um pedido multi-item sai numa etiqueta so —
    # guardar apenas o primeiro fazia a bancada separar caixa incompleta.
    itens_lista = []
    for it in itens:
        pr = it.get("produto") or it
        s = str(pr.get("sku") or pr.get("codigo") or "")
        if not s:
            continue
        itens_lista.append({
            "sku": s,
            "nome": str(pr.get("descricao") or pr.get("nome") or ""),
            "cor": _extrair_cor(it),
            "kit": _extrair_kit(s),
            "quantidade": it.get("quantidade") or 1,
        })

    # Classifica pela MESMA funcao da Esteira. `processar_batch_picking`
    # espera `ped["itens"]` ja preenchido — usa `detalhe` (que ja' tem os
    # itens buscados acima) em vez do resumo `p`, senao ele nunca acharia
    # itens nenhum e classificaria tudo como "sem itens".
    alerta_volume = ""
    if detalhe:
        try:
            import core_separacao as cs
            ped_p_batch = dict(detalhe)
            ped_p_batch["ecommerce"] = ecom  # o resumo `p` tem o ecommerce; o detalhe pode nao ter
            _classificado = cs.processar_batch_picking([ped_p_batch])
            if _classificado["pedidos_multi_itens"]:
                alerta_volume = "multi_itens"
            elif _classificado["pedidos_simples_multi_un"]:
                alerta_volume = "mesmo_kit_multiplo"
        except Exception as e:
            log.warning("Classificacao de risco falhou p/ pedido %s: %s",
                       p.get("id"), e)

    return {
        "tracking": tracking,
        "canal": canal,
        "pedido_ecommerce": ecom.get("numeroPedidoEcommerce") or "",
        "sku_principal": sku,
        "produto_nome": str(prod.get("descricao") or prod.get("nome") or ""),
        "cor": _extrair_cor(primeiro) if primeiro else "",
        "kit": _extrair_kit(sku),
        "cliente_nome": cliente.get("nome") or "",
        "cep": end.get("cep") or "",
        "peso_kg": peso_kg,
        "imagem_url": imagem_url or None,
        "itens": itens_lista,
        "alerta_volume": alerta_volume,
    }


# ------------------------------------------------------------------ #
# Canais
# ------------------------------------------------------------------ #
def popular_shopee(*, force: bool = False, vistos: set | None = None) -> int:
    """Varre pedidos Shopee abertos no Olist e persiste o tracking no indice.

    Fluxo: numeroPedidoEcommerce (= order_sn) -> get_tracking_number().
    Se o tracking vier vazio, tenta com package_number (via get_order_detail).
    Retorna quantos registros inseriu/atualizou.
    """
    if not force and not _pode_refresh():
        return 0
    inseridos = 0
    try:
        from core_shopee import ShopeeClient

        client = _client_olist()
        brutos = _pedidos_olist()
        sh = ShopeeClient()

        for p in brutos:
            ecom = p.get("ecommerce") or {}
            canal = ecom.get("nome") or ""
            num_ecom = ecom.get("numeroPedidoEcommerce") or ""
            if "shopee" not in canal.lower() or not num_ecom:
                continue
            if not _pedido_pendente(p):
                continue  # ja enviado/entregue/cancelado: nao vai ser bipado
            try:
                det = None
                tracking = sh.get_tracking_number(num_ecom)
                if not tracking:
                    # alguns pedidos exigem package_number explícito
                    det = sh.get_order_detail(num_ecom)
                    pkg = ""
                    if det and det[0].get("package_list"):
                        pkg = (det[0]["package_list"][0] or {}).get("package_number", "")
                    if pkg:
                        tracking = sh.get_tracking_number(num_ecom, package_number=pkg)
                if not tracking:
                    continue

                # Miniatura da variacao vendida: e' a foto que o cliente viu ao
                # comprar (item_list[].image_info.image_url). Reaproveita o
                # detalhe se ja foi buscado acima.
                imagem_url = ""
                try:
                    if det is None:
                        det = sh.get_order_detail(num_ecom)
                    itens_sh = (det[0].get("item_list") or []) if det else []
                    if itens_sh:
                        imagem_url = ((itens_sh[0].get("image_info") or {})
                                      .get("image_url") or "")
                except Exception as e:
                    log.debug("Imagem Shopee %s indisponivel: %s", num_ecom, e)
                # A API da Shopee as vezes devolve o rastreio com um sufixo
                # interno colado (BR265271600891DSPXLM16252909) -- ja vimos
                # isso quebrar bipagem por pistola (2026-08-09) e regravar a
                # contaminacao sozinho a cada atualizacao (2026-08-10, mesmo
                # tracking voltou sujo). Sanitizar aqui, na fonte, evita ter
                # que corrigir o banco de novo toda vez que o populator roda.
                tracking = sanitizar_codigo(tracking) or tracking
                reg = _registro_do_pedido_olist(client, p, canal="shopee",
                                                tracking=tracking,
                                                imagem_url=imagem_url)
                if reg and db.upsert_rastreio(reg):
                    inseridos += 1
                if vistos is not None:
                    vistos.add(db.normalizar_codigo(tracking))
            except Exception as e:
                log.warning("Shopee %s: %s", num_ecom, e)
        return inseridos
    except Exception as e:
        log.error("Falha ao popular Shopee: %s", e)
        return 0


def popular_ml(*, force: bool = False, vistos: set | None = None) -> int:
    """Varre pedidos ML abertos no Olist e persiste o tracking do shipment.

    Reusa ``core_esteira.obter_prazo_despacho_ml`` (que ja busca o shipment);
    aqui o tracking deixa de ser descartado e passa a ser gravado no indice.
    Retorna quantos registros inseriu/atualizou.
    """
    if not force and not _pode_refresh():
        return 0
    inseridos = 0
    try:
        from core_esteira import obter_prazo_despacho_ml

        client = _client_olist()
        brutos = _pedidos_olist()

        for p in brutos:
            ecom = p.get("ecommerce") or {}
            canal = ecom.get("nome") or ""
            num_ecom = ecom.get("numeroPedidoEcommerce") or ""
            if "mercado" not in canal.lower() or not num_ecom:
                continue
            if not _pedido_pendente(p):
                continue  # ja enviado/entregue/cancelado: nao vai ser bipado
            try:
                info = obter_prazo_despacho_ml(num_ecom)
                shipment_id = str(info.get("shipment_id") or "")
                pack_id = str(info.get("pack_id") or "")
                tracking = info.get("tracking")
                # A etiqueta do ML imprime o SHIPMENT no code128 grande, nao o
                # tracking da transportadora. Sem shipment nao ha' o que bipar
                # -- mas com shipment e sem tracking ainda da' pra indexar, e'
                # justamente o caso que quebrava o bipador.
                if not tracking and not shipment_id:
                    continue
                tracking = sanitizar_codigo(tracking or "") or tracking or ""
                # Chave do indice: UNIQUE(tracking, canal). Sem tracking, usa o
                # shipment como chave pra nao colidir todos os pedidos em "".
                chave = tracking or shipment_id
                reg = _registro_do_pedido_olist(client, p, canal="ml", tracking=chave)
                if reg:
                    reg["shipment_id"] = shipment_id
                    reg["pack_id"] = pack_id
                    if db.upsert_rastreio(reg):
                        inseridos += 1
                if vistos is not None:
                    for cod in (chave, shipment_id, pack_id):
                        if cod:
                            vistos.add(db.normalizar_codigo(cod))
            except Exception as e:
                log.warning("ML %s: %s", num_ecom, e)
        return inseridos
    except Exception as e:
        log.error("Falha ao popular ML: %s", e)
        return 0


def popular_tiktok(*, force: bool = False, vistos: set | None = None) -> int:
    """Varre pedidos do TikTok Shop e persiste o tracking no indice.

    Diferente de Shopee/ML, aqui a fonte primaria e' a propria API do TikTok
    (nao o Olist): o ``tracking_number`` ja vem na listagem de pedidos, entao
    uma varredura resolve tudo sem N+1.

    O pedido do TikTok NAO expoe o numero do pedido na etiqueta impressa (so o
    rastreio dos Correios) -- por isso este indice e' o UNICO caminho pra bipar
    uma etiqueta TikTok e achar o pedido. Validado 2026-08-02 com a etiqueta
    real AP296430628BR -> pedido 585303162106315783.

    Enriquece com dados do Olist quando o pedido tambem existe la (cor/kit/CEP
    formatado); se nao achar no Olist, grava mesmo assim com o que a API do
    TikTok fornece -- melhor um registro parcial que nenhum.
    """
    if not force and not _pode_refresh():
        return 0
    inseridos = 0
    try:
        from core_tiktokshop_orders import listar_pedidos

        pedidos = listar_pedidos()
    except Exception as e:
        log.error("Falha ao listar pedidos TikTok: %s", e)
        return 0

    # Indice dos pedidos do Olist por numeroPedidoEcommerce (o TikTok usa o
    # proprio order id como numero de e-commerce no Olist).
    olist_por_ecom: dict[str, dict] = {}
    try:
        client = _client_olist()
        for p in _pedidos_olist():
            num = (p.get("ecommerce") or {}).get("numeroPedidoEcommerce") or ""
            if num:
                olist_por_ecom[str(num)] = p
    except Exception as e:
        log.warning("Nao foi possivel cruzar TikTok com o Olist: %s", e)

    for ped in pedidos:
        tracking = ped.get("tracking_number") or ""
        if not tracking:
            continue  # pedido ainda sem etiqueta gerada
        tracking = sanitizar_codigo(tracking) or tracking
        pedido_id = str(ped.get("id") or "")
        try:
            imagem_url = ped.get("sku_image") or ""
            itens_tk = ped.get("itens") or []
            p_olist = olist_por_ecom.get(pedido_id)
            if p_olist:
                reg = _registro_do_pedido_olist(
                    client, p_olist, canal="tiktok", tracking=tracking,
                    imagem_url=imagem_url,
                )
                # A lista da API do TikTok e' a fonte de verdade do que o
                # cliente comprou; o espelho no Olist as vezes chega
                # incompleto. Se o TikTok trouxe mais itens, prevalece.
                if reg and len(itens_tk) > len(reg.get("itens") or []):
                    reg["itens"] = itens_tk
                elif reg and itens_tk:
                    # Mesma contagem: mantem os dados do Olist mas herda do
                    # marketplace o que so ele tem — imagem e NOME DA VARIACAO.
                    # O nome real ("Kit 3 Un Sortida") e' mais confiavel que a
                    # cor deduzida do SKU: em kit misto (-BRA2-PRE1_) a deducao
                    # devolve uma cor so e a bancada separa a peca errada.
                    _tk_por_sku = {
                        (i.get("sku") or "").upper(): i for i in itens_tk
                    }
                    for _it in reg.get("itens") or []:
                        _ref = _tk_por_sku.get((_it.get("sku") or "").upper())
                        if not _ref:
                            continue
                        if not _it.get("imagem_url"):
                            _it["imagem_url"] = _ref.get("imagem_url") or ""
                        if _ref.get("variacao"):
                            _it["variacao"] = _ref["variacao"]
            else:
                # Sem correspondencia no Olist: usa os dados da propria API.
                sku = ped.get("seller_sku") or ""
                reg = {
                    "tracking": tracking,
                    "canal": "tiktok",
                    "pedido_ecommerce": pedido_id,
                    "sku_principal": sku,
                    "produto_nome": ped.get("product_name") or "",
                    "cor": _extrair_cor({"produto": {"sku": sku}}),
                    "kit": _extrair_kit(sku),
                    "cliente_nome": ped.get("cliente") or "",
                    "cep": ped.get("cep") or "",
                    "peso_kg": None,
                    "imagem_url": imagem_url or None,
                    "itens": itens_tk,
                }
            if reg and db.upsert_rastreio(reg):
                inseridos += 1
            if vistos is not None:
                vistos.add(db.normalizar_codigo(tracking))
        except Exception as e:
            log.warning("TikTok %s: %s", pedido_id, e)
    return inseridos


def popular_todos(*, force: bool = False) -> dict:
    """Chama os populadores dos canais, limpa o antigo e consolida as contagens.

    Retorna dict: {shopee, ml, tiktok, removidos, total, skip}.
    """
    global _ULTIMO_REFRESH
    if not force and not _pode_refresh():
        return {"shopee": 0, "ml": 0, "tiktok": 0, "removidos": 0,
                "total": 0, "skip": True}

    # Conjunto dos trackings que seguem PENDENTES nas plataformas nesta rodada.
    # E' o que autoriza a poda: quem nao aparece aqui ja foi despachado.
    vistos: set[str] = set()
    contagem = {
        "shopee": popular_shopee(force=True, vistos=vistos),
        "ml": popular_ml(force=True, vistos=vistos),
        "tiktok": popular_tiktok(force=True, vistos=vistos),
    }
    contagem["total"] = sum(contagem.values())

    # Poda so quando a varredura viu algo -- se as 3 APIs falharem, `vistos`
    # fica vazio e apagar tudo seria destruir a base por causa de queda de rede.
    removidos = limpar_ja_despachados(vistos) if vistos else 0
    removidos += limpar_antigos()
    contagem["removidos"] = removidos
    contagem["skip"] = False
    _ULTIMO_REFRESH = time.monotonic()
    return contagem


# ------------------------------------------------------------------ #
# Diagnostico
# ------------------------------------------------------------------ #
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    print("== Popular indice de rastreio (todos os canais) ==")
    res = popular_todos(force=True)
    print(res)
    print("\n== Indice atual (10 mais recentes) ==")
    for r in db.listar_rastreios(10):
        print(f"  {r['tracking']:20s} | {r['canal']:8s} | {r['pedido_ecommerce']:20s} | {r.get('produto_nome') or ''}")
