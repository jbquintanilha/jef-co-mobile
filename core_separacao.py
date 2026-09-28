# ==============================================================================
# NOME DO SCRIPT: core_separacao.py
# DESCRICAO: Motor de Batch Picking e Lista de Separacao da Expedicao J&F Co.
# FUNCAO: Consolida pedidos pendentes do Olist por SKU para eliminar caminhadas
# STATUS: ATIVO
# VERSAO: 1.0
# DATA: 16/08/2026
# AUTOR: Violino (000) / Gemini CLI
# REF: plans/expedicao_master_2026-08-09.md (Modulo M1)
# ==============================================================================

from __future__ import annotations
import core_env_loader

import logging
import os
import time
from collections import defaultdict
from typing import Any, Dict, List, Optional

from core_olist import OlistClient, OlistError

log = logging.getLogger("core_separacao")


def normalizar_sku(sku: str) -> str:
    """Sanitiza espacos extras e normaliza o SKU para agrupamento confiavel."""
    if not sku:
        return "SEM_SKU"
    return " ".join(str(sku).split()).strip().upper()


# ---------------------------------------------------------------------- #
# Recomposicao de kits desmembrados pelo Olist
# ---------------------------------------------------------------------- #
# ⚠️ Achado real (28/09/2026): o Comandante mudou uma config global do
# Olist ("Desmembrar kits ao importar pedidos") e pedidos passaram a vir
# com o ATOMO multiplicado (ex: MEINVMAY1014046PRE x3) em vez do SKU do
# Kit (MEINVMAY1014046-PRE3_KIT3 x1). A esteira (lista de montagem) e o
# Scanner leem `sku`/`quantidade` cegamente e mostravam "3x Meia Preta"
# em vez de "1x Kit 3 Preto" -- a bancada nao sabia se eram 3 kits ou 3
# pares avulsos. A config ja foi revertida, mas o codigo precisa aguentar
# as DUAS formas (kit direto OU desmembrado), sempre mostrando o kit —
# porque o Olist pode voltar a desmembrar (ou outro canal fizer o mesmo).
#
# Fonte de verdade: tabela `itens_kit` do Supabase (sku_kit, sku_componente,
# quantidade). Um item desmembrado so' e' recomposto quando a quantidade
# BATE EXATO com uma linha dessa tabela -- 3x do atomo do Kit3 vira 1x
# Kit3; 3x de um atomo que NAO tem kit de tamanho 3 correspondente fica
# como estava (nunca inventa kit que nao existe).
_CACHE_ITENS_KIT: dict[str, list[tuple[str, int]]] | None = None
_CACHE_ITENS_KIT_TS: float = 0.0
_CACHE_TTL_ITENS_KIT = 600  # 10 min -- tabela de kits muda raramente


def _carregar_itens_kit() -> dict[str, list[tuple[str, int]]]:
    """{sku_componente: [(sku_kit, quantidade), ...]} — cache de 10 min."""
    global _CACHE_ITENS_KIT, _CACHE_ITENS_KIT_TS
    agora = time.monotonic()
    if _CACHE_ITENS_KIT is not None and (agora - _CACHE_ITENS_KIT_TS) < _CACHE_TTL_ITENS_KIT:
        return _CACHE_ITENS_KIT

    mapa: dict[str, list[tuple[str, int]]] = defaultdict(list)
    try:
        import requests

        url = os.getenv("SUPABASE_URL")
        key = os.getenv("SUPABASE_SERVICE_ROLE_KEY") or os.getenv("SUPABASE_SERVICE_KEY")
        if url and key:
            r = requests.get(
                f"{url}/rest/v1/itens_kit",
                params={"select": "sku_kit,sku_componente,quantidade"},
                headers={"apikey": key, "Authorization": f"Bearer {key}"},
                timeout=15,
            )
            r.raise_for_status()
            for row in r.json():
                comp = normalizar_sku(row.get("sku_componente") or "")
                sku_kit = row.get("sku_kit") or ""
                qtd = int(row.get("quantidade") or 0)
                if comp and sku_kit and qtd > 0:
                    mapa[comp].append((sku_kit, qtd))
    except Exception as exc:
        log.warning("Recomposicao de kits: itens_kit indisponivel (%s) — "
                    "seguindo sem recompor", exc)

    _CACHE_ITENS_KIT = dict(mapa)
    _CACHE_ITENS_KIT_TS = agora
    return _CACHE_ITENS_KIT


def recompor_kit_se_desmembrado(sku: str, quantidade: int) -> tuple[str, int] | None:
    """Se `quantidade` de `sku` bate exato com um kit conhecido, devolve
    (sku_do_kit, 1). Senao devolve None (item fica como veio).

    Quando MAIS DE UM kit bate (ex: atomo entra no Kit3 e no Kit6, e o
    pedido tem exatamente 6 unidades — ambiguo com "2x Kit3" tambem
    valido), prefere o kit MAIOR: e' o caso mais comum (cliente comprando
    o kit inteiro), e reduz o risco de sugerir "2x Kit3" quando o cliente
    comprou "1x Kit6" de fato.
    """
    sku_norm = normalizar_sku(sku)
    candidatos = _carregar_itens_kit().get(sku_norm) or []
    bateram = [(sk, q) for sk, q in candidatos if q == quantidade]
    if not bateram:
        return None
    sku_kit, _ = max(bateram, key=lambda par: par[1])
    return sku_kit, 1


def extrair_familia(sku: str) -> str:
    """Identifica a familia/categoria aproximada pelo prefixo do SKU."""
    s = sku.upper()
    if s.startswith("MEINV") or s.startswith("MEIA") or s.startswith("MEMED") or s.startswith("MESOQ") or s.startswith("MEBAI"):
        return "MEIAS"
    if s.startswith("CAL") or "CALCINHA" in s:
        return "CALCINHAS"
    if s.startswith("TOP") or s.startswith("SUT") or "SUTIA" in s:
        return "TOPS & SUTIAS"
    if s.startswith("CUE") or "CUECA" in s:
        return "CUECAS"
    if s.startswith("CAM") or "CAMISOLA" in s or "BABY" in s:
        return "HOMEWEAR"
    return "OUTROS"


_MSG_TOKEN = (
    "Token do Olist expirou — a lista de separacao nao pode ser gerada.\n\n"
    "  Para resolver agora:\n"
    "    python scripts/olist_reauth.py\n"
    "    (abre o navegador para reautorizar; leva ~30s)\n\n"
    "  Para nao repetir, agende o keepalive a cada 12h:\n"
    "    python scripts/olist_token_keepalive.py\n\n"
    "  Motivo: o refresh_token V3 do Olist expira em ~24h mesmo sem uso."
)


def _do_cache(situacoes: List[int]) -> List[Dict[str, Any]]:
    """Pedidos guardados em disco, sem tocar na API. [] se nao houver."""
    try:
        from core_cache_expedicao import ler
    except ImportError:
        return []
    saida: List[Dict[str, Any]] = []
    for sit in situacoes:
        try:
            envelope = ler(f"pedidos_sit{sit}")
        except Exception:
            envelope = None
        # ⚠️ `ler()` devolve o ENVELOPE do cache
        # ({chave, gravado_em, dados, ...}) — os pedidos estao em `dados`.
        # Usar o envelope direto fazia cada CHAVE virar um "pedido" (str).
        itens = (envelope or {}).get("dados") if isinstance(envelope, dict) else None
        if isinstance(itens, list):
            saida.extend(x for x in itens if isinstance(x, dict))
    return saida


def _e_falha_de_token(err: Exception) -> bool:
    """Distingue token morto de erro comum de API (rede, 500, rate limit)."""
    t = str(err).lower()
    return any(m in t for m in (
        "invalid_grant", "token is not active", "falha ao autenticar",
        "credenciais v3 ausentes", "401", "unauthorized",
    ))


def obter_pedidos_pendentes(
    client: Optional[OlistClient] = None,
    situacoes: Optional[List[int]] = None,
    max_pedidos: int = 100,
) -> List[Dict[str, Any]]:
    """Busca os pedidos que ainda precisam ser separados e embalados.

    ⚠️ NAO e' so' a situacao 2 (incidente 24/08/2026).

    O pedido MUDA de situacao quando a etiqueta e' emitida: sai de
    "Em separacao" (2) e vira "Pronto para envio" (7). Mas etiqueta impressa
    NAO quer dizer caixa embalada -- a peca continua na prateleira.

    Trazendo so' a 2, a lista escondia todo pedido que ja' tinha passado pela
    impressao: 47 apareciam e 11 sumiam. Na bancada isso vira lista "furada"
    e etiqueta fora da ordem de embalagem, porque a lista de coleta ignorava
    justamente as pecas cuja etiqueta ja' estava na pilha.

    ⚠️ Aqui so' LEMOS a situacao 7. Nunca escrever/voltar pedido pra 7 --
    ali ele ja' passou pela etiqueta e reimprimir gera divergencia de coleta
    (regra ja' anotada em `pages/17_Lista_Separacao.py`).
    """
    sits = situacoes or [2, 7]  # 2 = Em separacao · 7 = Pronto para envio

    # ⚠️ OlistClient() ja' autentica no construtor — o token morto estoura
    # AQUI, antes do loop. Por isso o guard precisa envolver a construcao.
    #
    # 🔴 Token morto NAO precisa parar a expedicao (incidente 23/08/2026):
    # `core_cache_expedicao` guarda os pedidos por 15 dias. A composicao dos
    # kits sai do proprio SKU V5 (`decompor_e_custar`), sem depender de API.
    # O que so' o Olist tem e' QUAIS pedidos existem — e disso o cache tem
    # copia. Entao caimos nele em vez de travar a bancada.
    try:
        c = client or OlistClient()
    except OlistError as err:
        if not _e_falha_de_token(err):
            raise
        do_cache = _do_cache(sits)
        if do_cache:
            log.warning("Token do Olist expirou — servindo %d pedido(s) do "
                        "cache. Rode: python scripts/olist_reauth.py",
                        len(do_cache))
            return do_cache
        raise OlistError(_MSG_TOKEN) from err
    pedidos_detalhados: List[Dict[str, Any]] = []

    for sit in sits:
        try:
            resumidos = c.listar_pedidos(situacao=sit, limit=max_pedidos)
            for res in resumidos:
                id_ped = res.get("id")
                if not id_ped:
                    continue
                try:
                    det = c.obter_pedido(id_ped)
                    # Guarda a situacao de ORIGEM: o detalhe as vezes volta com
                    # a situacao em branco, e sem isso nao da' pra saber se a
                    # etiqueta ja' saiu (7) ou nao (2).
                    det["_situacao_origem"] = sit
                    pedidos_detalhados.append(det)
                    time.sleep(0.3)  # Respeita o rate limit da V3 do Olist
                except OlistError as err_det:
                    log.warning("Falha ao detalhar pedido %s: %s", id_ped, err_det)
                    res["_situacao_origem"] = sit
                    pedidos_detalhados.append(res)
        except OlistError as err_list:
            # 🔴 Token morto NAO pode virar lista vazia silenciosa (incidente
            # 23/08/2026): a separacao "rodava" e nao mostrava pedido nenhum,
            # como se nao houvesse o que separar. O refresh_token V3 expira em
            # ~24h; quando morre, a unica saida e' re-OAuth manual.
            if _e_falha_de_token(err_list):
                raise OlistError(_MSG_TOKEN) from err_list
            log.error("Erro ao listar pedidos com situacao %s: %s", sit, err_list)

    return pedidos_detalhados


def processar_batch_picking(pedidos: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Processa a lista de pedidos detalhados e gera o agrupamento em lote (Batch Picking).

    Classifica os pedidos em:
      - simples_1un: 1 item, 1 unidade (Batch puro sem risco)
      - simples_multi_un: 1 item, N unidades (Batch por contagem)
      - multi_itens: Múltiplos itens distintos (Discrete / Atenção na bancada)
    """
    agrupamento_skus: Dict[str, Dict[str, Any]] = defaultdict(lambda: {
        "sku": "",
        "descricao": "",
        "familia": "",
        "total_unidades": 0,
        "total_pedidos": 0,
        "pedidos": [],
    })

    pedidos_simples_1un: List[Dict[str, Any]] = []
    pedidos_simples_multi_un: List[Dict[str, Any]] = []
    pedidos_multi_itens: List[Dict[str, Any]] = []

    total_pecas = 0

    for ped in pedidos:
        # ⚠️ `numeroPedidoEcommerce` vive DENTRO de `ecommerce`, nao na raiz.
        # Procurando so' na raiz nunca achava e caia no `id` interno da Olist:
        # a tela mostrava 351139582 enquanto a etiqueta na bancada dizia
        # 585618840622892713. Nao casava na conferencia. (2026-08-19)
        #
        # Ordem de preferencia:
        #   1. numeroPedidoEcommerce -- o numero do marketplace, o que esta'
        #      impresso na etiqueta e' o unico que o operador consegue casar
        #   2. numeroOrdemCompra     -- mesma coisa, campo alternativo
        #   3. numeroPedido          -- o sequencial da Olist (443)
        #   4. id                    -- ultimo recurso, so' pra nao ficar vazio
        ecom = ped.get("ecommerce") or {}
        ecom = ecom if isinstance(ecom, dict) else {}
        num_ped = str(
            ecom.get("numeroPedidoEcommerce")
            or ped.get("numeroPedidoEcommerce")
            or ped.get("numeroOrdemCompra")
            or ped.get("numeroPedido")
            or ped.get("numero")
            or ped.get("id")
            or ""
        )
        # 🆕 Numero SEQUENCIAL da Olist (443, 496...) -- pedido do Jota
        # (23/08/2026): curto, facil de bater o olho na bancada, e facil de
        # ver duplicata de envio. NAO substitui o `num_ped` do marketplace,
        # que e' o unico que casa com a etiqueta impressa; os dois convivem.
        num_olist = str(ped.get("numeroPedido") or ped.get("numero") or "")
        cliente = (ped.get("cliente") or {}).get("nome") or ped.get("cliente_nome") or "Cliente"
        # O canal vinha como o dict inteiro do ecommerce e a tela imprimia
        # `{'id': 44311, 'nome': 'TikTok Shop', ...}` no lugar de "TikTok Shop".
        canal = (ped.get("canal") or ecom.get("nome")
                 or (ped.get("ecommerce") if isinstance(ped.get("ecommerce"), str) else None)
                 or "Olist")
        itens = ped.get("itens") or []

        # Tratamento de pedidos sem itens explicitos
        if not itens and ped.get("produto"):
            itens = [{"produto": ped.get("produto"), "quantidade": ped.get("quantidade", 1)}]

        num_itens_distintos = len(itens)
        qtd_total_pedido = sum(int(it.get("quantidade") or 1) for it in itens)
        total_pecas += qtd_total_pedido

        # Situacao 7 = etiqueta JA' emitida (mas caixa ainda nao embalada).
        # A bancada precisa distinguir: sem isso o operador nao sabe se a
        # etiqueta daquele pedido ja' esta' na pilha ou ainda vai sair.
        sit_origem = ped.get("_situacao_origem") or ped.get("situacao")
        etiqueta_emitida = str(sit_origem) == "7"

        info_pedido = {
            "id": ped.get("id"),
            "numero_ecommerce": num_ped,
            "numero_olist": num_olist,   # sequencial curto (443, 496...)
            "cliente": cliente,
            "canal": canal,
            "itens": [],
            "qtd_total": qtd_total_pedido,
            "etiqueta_emitida": etiqueta_emitida,
        }

        for it in itens:
            prod = it.get("produto") or {}
            sku_bruto = prod.get("sku") or it.get("sku") or "SEM_SKU"
            sku = normalizar_sku(sku_bruto)
            descricao = prod.get("descricao") or prod.get("nome") or it.get("descricao") or sku
            qtd = int(it.get("quantidade") or 1)

            # Recompoe para EXIBICAO se o Olist desmembrou o kit em atomos
            # (ver bloco de comentario acima de recompor_kit_se_desmembrado).
            # `sku`/`qtd` (peca real, usados no agrupamento/estoque abaixo)
            # NAO mudam -- so' o que a bancada LE muda, para nao afetar
            # contagem de peca fisica nem classificacao simples/multi-item.
            recomposto = recompor_kit_se_desmembrado(sku, qtd)
            sku_exibicao, qtd_exibicao = recomposto if recomposto else (sku, qtd)

            info_pedido["itens"].append({
                "sku": sku_exibicao,
                "descricao": descricao,
                "quantidade": qtd_exibicao,
                # Peca real e SKU real do atomo -- usados por quem precisa
                # do fisico exato (estoque, lista de coleta), nao da rotulagem.
                "sku_fisico": sku,
                "quantidade_fisica": qtd,
            })

            # Agrupa para a lista de coleta
            fam = extrair_familia(sku)
            item_grp = agrupamento_skus[sku]
            item_grp["sku"] = sku
            item_grp["descricao"] = descricao
            item_grp["familia"] = fam
            item_grp["total_unidades"] += qtd
            item_grp["total_pedidos"] += 1
            # 🆕 Lista de coleta passa a mostrar o nº SEQUENCIAL da Olist
            # (#443) no lugar do numero longo do marketplace
            # (585618840622892713). Pedido do Jota (23/08): curto, legivel de
            # relance na bancada, e denuncia duplicata de envio na hora.
            # O numero do marketplace continua em `numero_ecommerce`, que e'
            # o que casa com a etiqueta impressa.
            rotulo = f"#{num_olist}" if num_olist else num_ped
            if rotulo not in item_grp["pedidos"]:
                item_grp["pedidos"].append(rotulo)

        if num_itens_distintos == 1:
            if qtd_total_pedido == 1:
                pedidos_simples_1un.append(info_pedido)
            else:
                pedidos_simples_multi_un.append(info_pedido)
        else:
            pedidos_multi_itens.append(info_pedido)

    # Ordena os SKUs agrupados por familia e quantidade decrescente
    lista_coleta = sorted(
        agrupamento_skus.values(),
        key=lambda x: (x["familia"], -x["total_unidades"], x["sku"]),
    )

    return {
        "total_pedidos": len(pedidos),
        "total_pecas": total_pecas,
        "total_skus_distintos": len(lista_coleta),
        "lista_coleta": lista_coleta,
        "pedidos_simples_1un": pedidos_simples_1un,
        "pedidos_simples_multi_un": pedidos_simples_multi_un,
        "pedidos_multi_itens": pedidos_multi_itens,
    }


def gerar_resumo_texto(dados: Dict[str, Any]) -> str:
    """Gera resumo formatado da lista de separacao para impressao / clipboard."""
    linhas = []
    linhas.append("=" * 60)
    linhas.append("📦 J&F CO. — LISTA DE SEPARAÇÃO (BATCH PICKING)")
    linhas.append("=" * 60)
    linhas.append(f"📊 Total de Pedidos: {dados['total_pedidos']} | Total de Peças: {dados['total_pecas']} | SKUs Únicos: {dados['total_skus_distintos']}")
    linhas.append("-" * 60)
    linhas.append("🛒 LISTA DE COLETA (O QUE BUSCAR NO ESTOQUE):")
    linhas.append("-" * 60)

    familia_atual = ""
    for item in dados["lista_coleta"]:
        if item["familia"] != familia_atual:
            familia_atual = item["familia"]
            linhas.append(f"\n📂 [{familia_atual}]")

        peds_str = ", ".join(item["pedidos"][:5])
        if len(item["pedidos"]) > 5:
            peds_str += f" (+{len(item['pedidos'])-5})"

        linhas.append(f" [ ] {item['total_unidades']:2d}x  {item['sku']} — {item['descricao'][:38]} (Peds: {peds_str})")

    linhas.append("\n" + "=" * 60)
    linhas.append("🔍 PERFIL DOS PEDIDOS:")
    linhas.append(f"  - Simples (1 item / 1un): {len(dados['pedidos_simples_1un'])} pedidos")
    linhas.append(f"  - Simples Multi-unidade: {len(dados['pedidos_simples_multi_un'])} pedidos")
    linhas.append(f"  - ⚠️ Multi-itens (Atenção): {len(dados['pedidos_multi_itens'])} pedidos")
    linhas.append("=" * 60)

    return "\n".join(linhas)
