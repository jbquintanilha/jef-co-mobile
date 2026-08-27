# ==============================================================================
# NOME DO SCRIPT: core_etiquetas_shopee_api.py
# DESCRICAO: Baixa etiquetas de envio da Shopee direto pela API oficial
# FUNCAO: Mesmo padrao do core_etiquetas_tiktok_api.py — busca os pedidos
#         prontos para envio (READY_TO_SHIP), baixa as etiquetas termicas e
#         unifica num PDF unico pronto para a LABEL 2.
# STATUS: ATIVO
# VERSAO: 1.0
# DATA: 16/08/2026
# AUTOR: Terminador (001) / Claude
# REF: core_shopee.py ja implementa o fluxo de 3 passos da Shopee:
#      create_shipping_document -> get_shipping_document_result -> download
# ==============================================================================
"""
Uso:
    from core_etiquetas_shopee_api import baixar_etiquetas
    res = baixar_etiquetas()
    print(res["pdf"], res["total"])

Linha de comando:
    python core_etiquetas_shopee_api.py --listar   # so lista os pedidos
    python core_etiquetas_shopee_api.py            # baixa e unifica

⚠️ A Shopee exige janela de no maximo 15 dias em get_order_list. Por isso
`listar_pedidos_a_enviar` varre em blocos de 15 dias para tras.
"""

from __future__ import annotations
import core_env_loader

import logging
import os
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from typing import Any

from core_shopee import ShopeeClient

log = logging.getLogger(__name__)

PASTA_SAIDA = Path(os.path.expanduser("~")) / "Downloads"

# Downloads simultaneos. 6 acelera ~5x sem irritar o rate limit da Shopee.
MAX_PARALELO = 6

# A Shopee limita a janela de busca a 15 dias por chamada
JANELA_DIAS = 15
SEGUNDOS_DIA = 86400


class ShopeeEtiquetaError(RuntimeError):
    """Falha ao falar com a API de logistica da Shopee."""


# --------------------------------------------------------------------------- #
# 1. Pedidos prontos para envio
# --------------------------------------------------------------------------- #
# 🔴 Na Shopee o pedido SAI de READY_TO_SHIP assim que a etiqueta e' gerada e
# vai para PROCESSED. Medido em producao 2026-08-16: READY_TO_SHIP=0,
# PROCESSED=7, SHIPPED=41. Buscar so READY_TO_SHIP devolve lista vazia sempre.
STATUS_A_DESPACHAR = ("READY_TO_SHIP", "PROCESSED")


def listar_pedidos_a_enviar(
    dias: int = 30,
    *,
    status: str | tuple[str, ...] = STATUS_A_DESPACHAR,
    client: ShopeeClient | None = None,
) -> list[str]:
    """order_sn dos pedidos aguardando despacho.

    Varre em blocos de 15 dias porque a API recusa janelas maiores.
    Por padrao cobre READY_TO_SHIP **e** PROCESSED (ver nota acima).
    """
    cli = client or ShopeeClient()
    agora = int(time.time())
    encontrados: list[str] = []
    status_lista = (status,) if isinstance(status, str) else tuple(status)

    restantes = dias
    fim = agora
    while restantes > 0:
        bloco = min(JANELA_DIAS, restantes)
        inicio = fim - bloco * SEGUNDOS_DIA
        for st in status_lista:
            try:
                lote = cli.get_order_list(time_from=inicio, time_to=fim, status=st)
                encontrados.extend(lote or [])
            except Exception as exc:
                log.warning("Falha ao listar %s em %s..%s: %s", st, inicio, fim, exc)
        fim = inicio
        restantes -= bloco

    # get_order_list devolve dicts {"order_sn": ..., "booking_sn": ...},
    # nao strings. Normaliza para order_sn puro.
    vistos: set[str] = set()
    unicos: list[str] = []
    for item in encontrados:
        sn = item.get("order_sn") if isinstance(item, dict) else item
        if not sn:
            continue
        sn = str(sn)
        if sn not in vistos:
            vistos.add(sn)
            unicos.append(sn)
    return unicos


# --------------------------------------------------------------------------- #
# 2. Baixar e unificar
# --------------------------------------------------------------------------- #
def baixar_etiquetas(
    order_sns: list[str] | None = None,
    *,
    dias: int = 30,
    saida: str | Path | None = None,
    unificar: bool = True,
    normalizar: bool = True,
) -> dict[str, Any]:
    """Baixa as etiquetas termicas e devolve um PDF unico em paginas 10x15.

    order_sns=None -> busca sozinho os pedidos READY_TO_SHIP.

    ⚠️ `normalizar=True` (default) recorta a folha A4 que a Shopee devolve.
    A etiqueta ja' vem desenhada em 105x148mm no canto superior esquerdo de
    uma A4 — sem o recorte sobra 2/3 de papel em branco por etiqueta e ela
    nao casa com o cartao de agradecimento 10x15.

    Retorna:
        {"pdf": caminho, "total": n, "arquivos": [...], "falhas": [(sn, motivo)]}
    """
    cli = ShopeeClient()

    if order_sns is None:
        order_sns = listar_pedidos_a_enviar(dias=dias, client=cli)

    if not order_sns:
        return {
            "pdf": None, "total": 0, "arquivos": [], "falhas": [],
            "aviso": "Nenhum pedido aguardando envio na Shopee.",
        }

    PASTA_SAIDA.mkdir(parents=True, exist_ok=True)
    tmp_dir = PASTA_SAIDA / f"_shopee_etiquetas_{datetime.now():%Y%m%d_%H%M%S}"
    tmp_dir.mkdir(exist_ok=True)

    arquivos: list[Path] = []
    falhas: list[tuple[str, str]] = []

    # ⚡ Em paralelo: cada etiqueta gasta ~6s quase todo em espera de rede
    # (status + download). Sequencial, 8 etiquetas levavam ~48s; com 6 threads
    # cai para o tempo da mais lenta. Nao e' CPU, entao o GIL nao atrapalha.
    def _uma(sn: str) -> tuple[str, bytes | None, str | None]:
        try:
            # Cliente proprio por thread — sessao HTTP compartilhada nao e'
            # garantidamente thread-safe.
            return sn, _baixar_pdf(ShopeeClient(), sn), None
        except Exception as exc:
            return sn, None, f"{type(exc).__name__}: {exc}"[:120]

    with ThreadPoolExecutor(max_workers=min(MAX_PARALELO, len(order_sns))) as executor:
        for sn, conteudo, erro in executor.map(_uma, order_sns):
            if erro:
                falhas.append((sn, erro))
            elif not conteudo:
                falhas.append((sn, "etiqueta indisponivel"))
            else:
                destino = tmp_dir / f"{sn}.pdf"
                destino.write_bytes(conteudo)
                arquivos.append(destino)

    # A ordem do `map` segue a entrada, mas o disco nao garante — reordena
    # para a etiqueta bater com a sequencia dos pedidos.
    ordem = {sn: i for i, sn in enumerate(order_sns)}
    arquivos.sort(key=lambda p: ordem.get(p.stem, 999))

    resultado: dict[str, Any] = {
        "pdf": None,
        "total": len(arquivos),
        "arquivos": [str(a) for a in arquivos],
        "falhas": falhas,
    }

    if unificar and arquivos:
        resultado["pdf"] = _unificar_pdfs(
            arquivos,
            saida or PASTA_SAIDA / f"etiquetas_shopee_{datetime.now():%Y%m%d_%H%M}.pdf",
        )

        if normalizar and resultado["pdf"]:
            # Recorta a A4 para 10x15 no proprio arquivo, para quem chama nao
            # precisar saber que a Shopee entrega folha grande.
            try:
                from core_etiqueta_normalizar import normalizar_10x15
                r = normalizar_10x15(resultado["pdf"], saida=resultado["pdf"])
                resultado["normalizado"] = True
                resultado["paginas_recortadas"] = r["recortadas"]
            except Exception as exc:
                # Falhar aqui nao pode custar a etiqueta — segue com a A4.
                log.warning("Normalizacao 10x15 falhou (%s) — PDF segue em A4.", exc)
                resultado["normalizado"] = False

    return resultado


def _baixar_pdf(cli: ShopeeClient, order_sn: str) -> bytes | None:
    """Baixa a etiqueta como PDF de verdade.

    🔴 NAO usar `etiqueta_termica()` aqui: ela pede THERMAL_AIR_WAYBILL, e a
    Shopee devolve um ZIP com ZPL dentro (`thermal_zpl_shipping_label.txt`,
    Content-Type `application/force-download`, assinatura `PK\\x03\\x04`) — nao
    da para juntar num PDF. Medido em producao 2026-08-16:

        NORMAL_AIR_WAYBILL   -> application/pdf            %PDF  57 KB  ✅
        THERMAL_AIR_WAYBILL  -> application/force-download  ZIP   10 KB  ❌

    O documento normalmente ja esta READY (a Shopee gera quando o pedido sai de
    READY_TO_SHIP). Se nao estiver, dispara o create e espera.
    """
    tipo = "NORMAL_AIR_WAYBILL"

    try:
        status = cli.get_shipping_document_result(order_sn, doc_type=tipo)
    except Exception:
        status = None

    if status != "READY":
        tracking = ""
        try:
            tracking = cli.get_tracking_number(order_sn)
        except Exception:
            pass
        try:
            cli.create_shipping_document(
                order_sn, tracking_number=tracking, doc_type=tipo
            )
        except Exception as exc:
            log.warning("create falhou para %s: %s", order_sn, exc)

        for _ in range(8):
            try:
                if cli.get_shipping_document_result(order_sn, doc_type=tipo) == "READY":
                    break
            except Exception:
                pass
            time.sleep(2)

    return cli.download_shipping_document(order_sn, doc_type=tipo)


def _unificar_pdfs(arquivos: list[Path], saida: str | Path) -> str:
    try:
        from pypdf import PdfWriter
    except ImportError:
        from PyPDF2 import PdfWriter  # type: ignore

    writer = PdfWriter()
    for arq in arquivos:
        writer.append(str(arq))

    saida = Path(saida)
    saida.parent.mkdir(parents=True, exist_ok=True)
    with open(saida, "wb") as fh:
        writer.write(fh)
    return str(saida)


# --------------------------------------------------------------------------- #
# 3. Cruzamento com o Olist
# --------------------------------------------------------------------------- #
def cruzar_com_olist(
    order_sns: list[str] | None = None,
    *,
    dias: int = 30,
) -> dict[str, Any]:
    """Confere quais pedidos da Shopee ja existem no Olist.

    Mostra o que esta so num lado — util para achar pedido que nao desceu
    para o ERP antes de despachar.

    Retorna:
        {
          "shopee": [...], "no_olist": [...], "sem_olist": [...],
          "resumo": "texto curto"
        }
    """
    if order_sns is None:
        order_sns = listar_pedidos_a_enviar(dias=dias)

    no_olist: list[dict[str, Any]] = []
    sem_olist: list[str] = []

    try:
        from core_olist import OlistClient
        olist = OlistClient()
    except Exception as exc:
        return {
            "shopee": order_sns, "no_olist": [], "sem_olist": [],
            "erro": f"Olist indisponivel: {exc}",
        }

    for sn in order_sns:
        try:
            ped = olist.buscar_pedido_por_ecommerce(sn)
            if ped:
                no_olist.append({"order_sn": sn, "olist": ped})
            else:
                sem_olist.append(sn)
        except Exception as exc:
            log.warning("Falha ao buscar %s no Olist: %s", sn, exc)
            sem_olist.append(sn)

    return {
        "shopee": order_sns,
        "no_olist": no_olist,
        "sem_olist": sem_olist,
        "resumo": (
            f"{len(order_sns)} pedidos na Shopee · "
            f"{len(no_olist)} encontrados no Olist · "
            f"{len(sem_olist)} SEM correspondencia"
        ),
    }


# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    import sys

    logging.basicConfig(level=logging.INFO, format="%(message)s")

    if "--listar" in sys.argv:
        sns = listar_pedidos_a_enviar()
        print(f"Pedidos aguardando envio: {len(sns)}")
        for sn in sns[:20]:
            print("  ", sn)
    elif "--cruzar" in sys.argv:
        res = cruzar_com_olist()
        print(res.get("resumo") or res.get("erro"))
        for sn in res.get("sem_olist", [])[:20]:
            print("  SEM OLIST:", sn)
    else:
        res = baixar_etiquetas()
        if res.get("aviso"):
            print(res["aviso"])
        else:
            print(f"Etiquetas baixadas: {res['total']}")
            if res["pdf"]:
                print(f"PDF unificado: {res['pdf']}")
            for sn, motivo in res["falhas"]:
                print(f"  FALHA {sn}: {motivo}")
