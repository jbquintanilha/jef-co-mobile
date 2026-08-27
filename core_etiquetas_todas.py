# ==============================================================================
# NOME DO SCRIPT: core_etiquetas_todas.py
# DESCRICAO: Baixa TikTok + Shopee ao mesmo tempo e entrega um PDF unico
# FUNCAO: Sao APIs independentes, em bases distintas — nao ha motivo para
#         esperar uma terminar para comecar a outra.
# STATUS: ATIVO
# VERSAO: 1.0
# DATA: 16/08/2026
# AUTOR: Terminador (001) / Claude
# ==============================================================================
"""
⚡ Ganho medido (2026-08-16, 23 etiquetas):

    sequencial, 1 a 1     ~1min40s
    paralelo por canal      22s (Shopee 8)  +  20s (TikTok 15)
    os dois canais juntos   ~22s            <- o tempo do canal mais lento

Cada canal ja' baixa suas etiquetas em 6 threads; aqui os dois canais rodam
lado a lado. Como sao APIs diferentes, o rate limit de uma nao afeta a outra.

⚠️ Um canal que falha NAO derruba o outro: o resultado traz o que deu certo e
lista o erro do que falhou. Expedicao parada por causa de uma API fora do ar
seria pior que despachar so' metade.

Uso:
    from core_etiquetas_todas import baixar_tudo
    r = baixar_tudo()                    # os dois canais
    r = baixar_tudo(com_cartao=True)     # ja' intercala o cartao certo
    print(r["resumo"])
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

log = logging.getLogger(__name__)

PASTA_SAIDA = Path(os.path.expanduser("~")) / "Downloads"
PASTA_SAIDA.mkdir(parents=True, exist_ok=True)


def _pedidos_filtrados_shopee(somente: set[str]) -> list[str]:
    """order_sn ja' e' numero_ecommerce direto -- filtro por intersecao simples."""
    import core_etiquetas_shopee_api as api
    todos = api.listar_pedidos_a_enviar()
    return [sn for sn in todos if sn in somente]


def _pacotes_filtrados_tiktok(somente: set[str]) -> list[str]:
    """package_id != numero_ecommerce -- precisa olhar dentro de cada pacote.

    Um pacote pode conter mais de 1 pedido (`orders[]`); entra se QUALQUER
    pedido dele estiver no range escolhido (senao a etiqueta do pacote
    inteiro fica de fora, mesmo tendo pedido selecionado dentro).
    """
    import core_etiquetas_tiktok_api as api
    pacotes = api.listar_pacotes_a_enviar()
    alvo = []
    for p in pacotes:
        pid = str(p.get("id") or p.get("package_id") or "")
        oids = {str(o.get("id") or "") for o in (p.get("orders") or [])}
        if pid and (oids & somente):
            alvo.append(pid)
    return alvo


def _envios_filtrados_ml(somente: set[str]) -> list[str]:
    """shipment_id != numero_ecommerce -- traduz pelo `pedido` de cada envio."""
    import core_etiquetas_ml_api as api
    envios = api.listar_envios_a_despachar()
    return [e["shipment_id"] for e in envios if str(e.get("pedido") or "") in somente]


def _baixar_canal(canal: str, somente: set[str] | None = None) -> dict[str, Any]:
    """Baixa um canal isoladamente. Nunca levanta — devolve o erro no dict.

    ``somente``: numeros de pedido (numero_ecommerce) a INCLUIR — filtra na
    ORIGEM, antes de chamar a API de download, em vez de baixar tudo e
    descartar depois. `None` = comportamento de sempre (tudo pendente).
    Usado pela selecao de ciclo/onda ja' na Fase 1 (Jota, 25/08: "ver os
    numeros antes de baixar").
    """
    inicio = time.time()
    try:
        if canal == "shopee":
            import core_etiquetas_shopee_api as api
            ids = _pedidos_filtrados_shopee(somente) if somente is not None else None
            r = api.baixar_etiquetas(order_sns=ids)
        elif canal == "ml":
            import core_etiquetas_ml_api as api
            ids = _envios_filtrados_ml(somente) if somente is not None else None
            r = api.baixar_etiquetas(shipment_ids=ids)
        else:
            import core_etiquetas_tiktok_api as api
            ids = _pacotes_filtrados_tiktok(somente) if somente is not None else None
            r = api.baixar_etiquetas(package_ids=ids)
        r["canal"] = canal
        r["segundos"] = round(time.time() - inicio, 1)
        return r
    except Exception as exc:
        log.warning("Canal %s falhou: %s", canal, exc)
        return {
            "canal": canal, "pdf": None, "total": 0, "arquivos": [],
            "falhas": [], "erro": f"{type(exc).__name__}: {exc}"[:200],
            "segundos": round(time.time() - inicio, 1),
        }


def baixar_tudo(
    canais: list[str] | None = None,
    *,
    com_cartao: bool = False,
    saida: str | Path | None = None,
    somente: set[str] | None = None,
) -> dict[str, Any]:
    """Baixa os canais em paralelo e junta tudo num PDF unico 10x15.

    Args:
        canais: default ["tiktok", "shopee"].
        com_cartao: intercala o cartao de agradecimento de cada canal.
                    ⚠️ Feito ANTES de juntar, para cada etiqueta receber o
                    cartao do seu proprio canal.
        somente: numero_ecommerce dos pedidos a INCLUIR (filtra na ORIGEM,
                 antes do download). `None` = tudo que estiver pendente nas
                 3 APIs, como sempre foi. Usado pela selecao de ciclo/onda
                 na Fase 1 da esteira (Jota, 25/08).

    Retorna:
        {"pdf", "total", "por_canal", "erros", "segundos", "resumo"}
    """
    canais = canais or ["tiktok", "shopee"]
    inicio = time.time()

    # Os dois canais ao mesmo tempo — APIs independentes
    with ThreadPoolExecutor(max_workers=len(canais)) as executor:
        resultados = list(executor.map(
            lambda c: _baixar_canal(c, somente), canais))

    por_canal: dict[str, Any] = {}
    erros: list[str] = []
    pdfs: list[str] = []
    total = 0

    for r in resultados:
        canal = r["canal"]
        por_canal[canal] = {
            "total": r.get("total", 0),
            "segundos": r.get("segundos"),
            "falhas": len(r.get("falhas") or []),
            "erro": r.get("erro"),
            # Os PDFs individuais, um por pedido. Necessarios para reordenar
            # a pilha na sequencia de embalagem: o `stem` de cada arquivo e'
            # o identificador (Shopee=order_sn, TikTok=package_id). Sem isto
            # so' resta o PDF ja' unificado, onde a pagina perdeu a origem.
            "arquivos": r.get("arquivos") or [],
            # O PDF unificado do canal — quem remonta a pilha precisa saber
            # qual arquivo apagar depois, para nao deixar rastro no Downloads.
            "pdf": r.get("pdf"),
            # ML: envios `pending` que NENHUMA API imprime (nem ML nem Olist),
            # so' o modal do Olist. Precisam chegar a' tela, senao o pedido
            # some da pilha e parece que nao existe.
            "represados": r.get("represados") or [],
        }

        if r.get("erro"):
            erros.append(f"{canal}: {r['erro']}")
            continue

        for sn, motivo in (r.get("falhas") or []):
            erros.append(f"{canal}/{sn}: {motivo}")

        if not r.get("pdf"):
            continue

        caminho = r["pdf"]

        # Normaliza para 10x15 exato ANTES do cartao. Sem isto a etiqueta do
        # TikTok (298x420pt, 5.1mm mais larga que o alvo) ia pro PDF final
        # sem corte nem centralizacao e saia cortada na impressora (achado
        # real, 25/08 — ver core_etiqueta_normalizar.py). A Shopee ja' vem
        # em folha A4 e SEMPRE precisou disto; agora os dois canais passam.
        try:
            import core_etiqueta_normalizar as norm
            res_norm = norm.normalizar_10x15(caminho)
            if res_norm.get("saida") and Path(res_norm["saida"]).exists():
                caminho = res_norm["saida"]
        except Exception as exc:
            erros.append(f"{canal}: normalização 10x15 falhou — {exc}")

        # Cartao por canal, antes de misturar os canais no PDF final
        if com_cartao:
            try:
                import core_etiqueta_com_cartao as ccc
                alvo = caminho.replace(".pdf", "_com_cartao.pdf")
                rc = ccc.intercalar_canal_unico(caminho, alvo, canal)
                if rc.get("ok"):
                    caminho = alvo
                    por_canal[canal]["com_cartao"] = True
                else:
                    erros.append(f"{canal}: cartão não aplicado — {rc.get('erro')}")
            except Exception as exc:
                erros.append(f"{canal}: cartão falhou — {exc}")

        pdfs.append(caminho)
        total += r.get("total", 0)

    # ---- junta os canais num PDF unico ------------------------------------ #
    pdf_final = None
    if pdfs:
        import fitz

        destino = Path(saida) if saida else (
            PASTA_SAIDA / f"etiquetas_todas_{datetime.now():%Y%m%d_%H%M}.pdf")

        doc = fitz.open()
        for caminho in pdfs:
            parcial = fitz.open(caminho)
            doc.insert_pdf(parcial)
            parcial.close()
        doc.save(destino)
        doc.close()
        pdf_final = str(destino)

    segundos = round(time.time() - inicio, 1)

    partes = [f"{total} etiquetas"]
    partes += [f"{c} {d['total']}" for c, d in por_canal.items()]
    if erros:
        partes.append(f"⚠️ {len(erros)} problema(s)")

    # ---- sincroniza a base do Scanner em background -------------------- #
    # Jota (26/08): "não foi sincronizado ainda o scanner... ideal seria ele
    # usar a base... ao baixar as etiquetas gerar uma base unica". Achado
    # real no dia: 4 de 21 etiquetas recem-baixadas nao resolviam no Scanner
    # porque o indice dele so' atualiza com clique manual em "Atualizar
    # Base" — furo de sincronizacao entre baixar a etiqueta e poder bipar.
    #
    # Solucao imediata (nao a unificacao completa, que fica pendente — ver
    # `_INBOX` do Bibliotecario / memoria do Claude): dispara o populator do
    # Scanner em THREAD separada assim que a etiqueta sai, sem bloquear o
    # retorno do PDF. `popular_todos()` sem `force` respeita o throttle de
    # 300s (nao martela a API se acabou de rodar) e sempre varre TUDO que
    # esta' pendente — nao precisa filtrar so' os pedidos deste lote.
    if total > 0:
        import threading

        def _sincronizar_scanner_bg():
            try:
                import core_scanner_populator as pop
                r_pop = pop.popular_todos()
                if not r_pop.get("skip"):
                    log.info("Scanner sincronizado apos baixar etiquetas: %s",
                             r_pop)
            except Exception as exc:
                log.warning("Sincronizacao do Scanner (background) falhou: %s", exc)

        threading.Thread(target=_sincronizar_scanner_bg, daemon=True).start()

    return {
        "pdf": pdf_final,
        "total": total,
        "por_canal": por_canal,
        "erros": erros,
        "segundos": segundos,
        "com_cartao": com_cartao,
        "resumo": " · ".join(partes) + f" em {segundos}s",
    }


if __name__ == "__main__":
    import sys

    logging.basicConfig(level=logging.INFO, format="%(message)s")

    r = baixar_tudo(com_cartao="--cartao" in sys.argv)
    print(r["resumo"])
    print(f"  {r['pdf']}")
    for canal, d in r["por_canal"].items():
        print(f"    {canal:8s} {d['total']:>3} etiquetas em {d['segundos']}s"
              + (f"  ERRO: {d['erro']}" if d.get("erro") else ""))
    for e in r["erros"]:
        print(f"    ⚠️ {e}")
