# ==============================================================================
# NOME DO SCRIPT: core_scanner_populator_rapido.py
# DESCRICAO: Versao PARALELA do populador do Scanner — mesma logica, ~6x mais rapido
# FUNCAO: O "ATUALIZAR BASE" levava ~4 minutos porque consultava as APIs pedido
#         a pedido, em fila. Aqui os 3 canais rodam juntos e cada canal usa
#         varias conexoes.
# STATUS: ATIVO
# VERSAO: 1.0
# DATA: 17/08/2026
# AUTOR: Terminador (001) / Claude
# ==============================================================================
"""
🔴 NAO altera `core_scanner_populator.py`. Este modulo IMPORTA as funcoes dele
e so' muda a forma de percorrer: onde havia um `for` sequencial, entra um
ThreadPool. A logica de negocio (o que e' pendente, como monta o registro,
sanitizacao do tracking) continua vindo do original — se ela mudar la', muda
aqui junto.

MEDICAO que motivou este arquivo (17/08/2026, base real do Jota):

    _pedidos_olist()          16s   (421 pedidos, 1 chamada)
    Shopee   38 pendentes  ~145s   (2 chamadas de ~1.9s por pedido)
    TikTok   30 pendentes   ~60s
    ML        4 pendentes    ~8s
    ----------------------------------------
    TOTAL                   ~230s = 3,8 minutos

O tempo e' quase todo ESPERA DE REDE, nao processamento. Thread resolve: o GIL
solta durante I/O. Com 8 conexoes por canal e os 3 canais juntos, o total cai
para o tempo do canal mais lento — estimado ~35s.

Uso (mesma assinatura do original):
    import core_scanner_populator_rapido as pr
    r = pr.popular_todos_rapido(force=True)
"""

from __future__ import annotations

import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any

import core_scanner_db as db
import core_scanner_populator as orig
from core_scanner_decoder import sanitizar_codigo

log = logging.getLogger(__name__)

# Conexoes simultaneas por canal. 8 acelera ~6x sem irritar o rate limit —
# acima disso a Shopee comeca a devolver erro de limite.
MAX_PARALELO = 8

# 🔴 SQLite nao aceita escrita concorrente: dois canais gravando ao mesmo
# tempo dao "database is locked". Toda escrita passa por aqui.
_TRAVA_BANCO = threading.Lock()


def _pendentes_do_canal(brutos: list[dict], canal: str) -> list[dict]:
    """Pedidos de um canal que ainda podem ser bipados."""
    saida = []
    for pedido in brutos:
        ecom = pedido.get("ecommerce") or {}
        nome = (ecom.get("nome") or "").lower()
        if canal not in nome:
            continue
        if not ecom.get("numeroPedidoEcommerce"):
            continue
        if not orig._pedido_pendente(pedido):
            continue        # ja despachado/cancelado: nao entra na base
        saida.append(pedido)
    return saida


# ---------------------------------------------------------------------- #
# SHOPEE
# ---------------------------------------------------------------------- #
def _um_shopee(pedido: dict, cliente_olist: Any) -> tuple[dict | None, str]:
    """Resolve tracking + imagem de UM pedido Shopee.

    ⚠️ Cliente proprio por thread: sessao HTTP compartilhada nao e'
    garantidamente thread-safe.
    """
    from core_shopee import ShopeeClient

    ecom = pedido.get("ecommerce") or {}
    num = ecom.get("numeroPedidoEcommerce") or ""

    try:
        sh = ShopeeClient()
        detalhe = None
        tracking = sh.get_tracking_number(num)

        if not tracking:
            # Alguns pedidos exigem package_number explicito
            detalhe = sh.get_order_detail(num)
            pacote = ""
            if detalhe and detalhe[0].get("package_list"):
                pacote = (detalhe[0]["package_list"][0] or {}).get("package_number", "")
            if pacote:
                tracking = sh.get_tracking_number(num, package_number=pacote)

        if not tracking:
            return None, ""

        imagem = ""
        try:
            if detalhe is None:
                detalhe = sh.get_order_detail(num)
            itens = (detalhe[0].get("item_list") or []) if detalhe else []
            if itens:
                imagem = ((itens[0].get("image_info") or {}).get("image_url") or "")
        except Exception as exc:
            log.debug("Imagem Shopee %s indisponivel: %s", num, exc)

        # A Shopee as vezes cola sufixo interno no rastreio — sanitizar na
        # fonte evita ter que limpar o banco depois (incidente 2026-08-09).
        tracking = sanitizar_codigo(tracking) or tracking

        registro = orig._registro_do_pedido_olist(
            cliente_olist, pedido, canal="shopee",
            tracking=tracking, imagem_url=imagem)
        return registro, db.normalizar_codigo(tracking)
    except Exception as exc:
        log.warning("Shopee %s: %s", num, exc)
        return None, ""


# ---------------------------------------------------------------------- #
# Motor generico
# ---------------------------------------------------------------------- #
def _rodar_canal(pedidos: list[dict], funcao, cliente_olist: Any,
                 vistos: set[str]) -> int:
    """Executa a funcao de um canal em paralelo e grava o resultado.

    A gravacao no SQLite acontece na thread principal: o banco nao aceita
    escrita concorrente e nao vale arriscar `database is locked`.
    """
    if not pedidos:
        return 0

    resultados: list[tuple[dict | None, str]] = []
    with ThreadPoolExecutor(max_workers=min(MAX_PARALELO, len(pedidos))) as executor:
        resultados = list(executor.map(
            lambda p: funcao(p, cliente_olist), pedidos))

    inseridos = 0
    for registro, tracking in resultados:
        if tracking:
            vistos.add(tracking)
        if not registro:
            continue
        with _TRAVA_BANCO:          # os outros canais tambem escrevem
            if db.upsert_rastreio(registro):
                inseridos += 1
    return inseridos


def popular_todos_rapido(*, force: bool = True) -> dict[str, Any]:
    """Mesma coisa que `popular_todos`, com os 3 canais em paralelo.

    Retorna: {shopee, ml, tiktok, removidos, total, segundos, skip}
    """
    inicio = time.time()
    vistos: set[str] = set()

    cliente = orig._client_olist()
    brutos = orig._pedidos_olist(forcar=force)

    grupos = {
        "shopee": _pendentes_do_canal(brutos, "shopee"),
        "tiktok": _pendentes_do_canal(brutos, "tiktok"),
        "ml": _pendentes_do_canal(brutos, "mercado"),
    }
    log.info("pendentes: %s", {k: len(v) for k, v in grupos.items()})

    contagem: dict[str, Any] = {}

    # ⚡ Os 3 canais AO MESMO TEMPO. São APIs independentes: o rate limit de
    # uma não afeta a outra, e o total passa a ser o tempo do canal mais lento
    # em vez da soma dos três.
    #
    # 🔴 Cada canal usa seu próprio `set` de vistos e só depois se juntam: um
    # `set` compartilhado entre threads pode perder item em escrita simultânea,
    # e `vistos` é o que autoriza a PODA da base — perder item ali apagaria
    # rastreio de pedido que ainda vai ser bipado.
    vistos_shopee: set[str] = set()
    vistos_tiktok: set[str] = set()
    vistos_ml: set[str] = set()

    def _shopee() -> int:
        return _rodar_canal(grupos["shopee"], _um_shopee, cliente, vistos_shopee)

    def _tiktok() -> int:
        try:
            return orig.popular_tiktok(force=True, vistos=vistos_tiktok)
        except Exception as exc:
            log.error("TikTok falhou: %s", exc)
            return 0

    def _ml() -> int:
        try:
            return orig.popular_ml(force=True, vistos=vistos_ml)
        except Exception as exc:
            log.error("ML falhou: %s", exc)
            return 0

    with ThreadPoolExecutor(max_workers=3) as executor:
        futuros = {
            "shopee": executor.submit(_shopee),
            "tiktok": executor.submit(_tiktok),
            "ml": executor.submit(_ml),
        }
        for canal, futuro in futuros.items():
            try:
                contagem[canal] = futuro.result()
            except Exception as exc:
                log.error("Canal %s falhou: %s", canal, exc)
                contagem[canal] = 0

    vistos |= vistos_shopee | vistos_tiktok | vistos_ml

    contagem["total"] = sum(v for v in contagem.values() if isinstance(v, int))

    # Poda so' quando a varredura viu algo: se as APIs cairem, `vistos` fica
    # vazio e apagar tudo seria destruir a base por causa de queda de rede.
    removidos = orig.limpar_ja_despachados(vistos) if vistos else 0
    removidos += orig.limpar_antigos()
    contagem["removidos"] = removidos
    contagem["skip"] = False
    contagem["segundos"] = round(time.time() - inicio, 1)

    log.info("Base atualizada em %ss: %s", contagem["segundos"], contagem)
    return contagem


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    resultado = popular_todos_rapido(force=True)
    print(f"Shopee {resultado['shopee']} · ML {resultado['ml']} · "
          f"TikTok {resultado['tiktok']} · removidos {resultado['removidos']} "
          f"em {resultado['segundos']}s")
