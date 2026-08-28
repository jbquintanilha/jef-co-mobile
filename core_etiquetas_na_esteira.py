# ==============================================================================
# NOME DO SCRIPT: core_etiquetas_na_esteira.py
# DESCRICAO: PDF unico dos 2 canais, na ordem da esteira e numerado #1..#N
# FUNCAO: Junta Shopee + TikTok, REORDENA conforme a sequencia de embalagem
#         e carimba o numero de ordem em cada etiqueta.
# STATUS: ATIVO
# VERSAO: 1.0
# DATA: 19/08/2026
# AUTOR: Terminador (001) / Claude
# ==============================================================================
"""
Regras do Jota (2026-08-19):

    "lembrando que as etiquetas sao geradas em ordem de facilitar embalar...
     conforme separacao"
    "sim... precisa ser junto q e' melhor... embalo junto"

## O problema que isto resolve

`baixar_tudo` ja' junta os dois canais, mas na ordem que as APIs devolvem.
Medido em 19/08: a etiqueta #9 do PDF era o 2o pedido da sequencia de
embalagem. Numerar assim faria o operador garimpar etiqueta a cada caixa —
pior do que nao ter numero nenhum.

Aqui o PDF e' REMONTADO na ordem da esteira e so' entao numerado, de modo que
a pilha de etiquetas e a pilha de caixas andem juntas.

## Como cada pagina e' identificada

O nome do arquivo individual carrega o identificador do pedido:

    Shopee -> `{order_sn}.pdf`     casa direto com `numero_ecommerce`
    TikTok -> `{package_id}.pdf`   precisa traduzir pacote -> pedido

⚠️ Sem essa traducao o pedido do TikTok nunca casa e cai no fim da pilha
como "sem posicao". Por isso `_mapa_tiktok()` existe.

## O que sobra fora da esteira

Etiqueta cujo pedido nao esta' na sequencia (canal fora da fila, pedido que
mudou de situacao no meio) NAO e' descartada: vai para o FIM da pilha,
numerada normalmente. Sumir com etiqueta e' o unico erro pior que
desordena-la — a caixa ficaria sem envio.

Uso:
    from core_etiquetas_na_esteira import gerar
    r = gerar()   # baixa, ordena, numera
"""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)


# Faixa no rodape pros numeros #N #m e SKU do produto vendido.
#
# 27/08: fontes subiram de 8.5/6.0 para 12/10 -- o Comandante nao conseguia ler
# na bancada ("dificil de ler hj"). O padrao discreto da DANFE serve pra quem
# le' o documento na mao; aqui a leitura e' de pe', a um metro, com a caixa na
# frente. Legibilidade vale mais que economia de papel.
#
# A faixa cresceu junto (7mm -> 10.5mm): 12pt + 10pt + respiro nao cabem em
# 19.8pt, e forcar so' as fontes faria as duas linhas se sobreporem.
FAIXA_NUMERO_PT = 10.5 / 25.4 * 72   # 10.5mm ≈ 29.8pt
FONTE_NUMERO = 12.0
FONTE_SKU = 10.0
COR = (0.0, 0.0, 0.0)
COR_SKU = (0.30, 0.30, 0.30)


def _formatar_itens_sku(itens: list[dict]) -> str:
    """Formata '1x SKU' ou '1x SKU_A + 2x SKU_B' para o rodape."""
    partes = []
    for it in itens:
        qtd = it.get("quantidade") or 1
        sku = (it.get("sku") or "").strip()
        if sku:
            partes.append(f"{qtd}x {sku}")
    return " + ".join(partes)


def _recompor_com_faixa_numero(doc, idx: int, texto_num: str, texto_sku: str = "") -> None:
    """Reescreve a pagina `idx` do `doc`: encolhe sutilmente e abre faixa compacta embaixo."""
    import fitz

    from core_etiqueta_numerar import COR

    original = doc[idx]
    largura, altura = original.rect.width, original.rect.height

    temp = fitz.open()
    temp.insert_pdf(doc, from_page=idx, to_page=idx)

    doc.delete_page(idx)
    nova = doc.new_page(pno=idx, width=largura, height=altura)

    # Area util = preserva a etiqueta original com reducao minima
    area_util = fitz.Rect(0, 0, largura, altura - FAIXA_NUMERO_PT)
    nova.show_pdf_page(area_util, temp, 0)
    temp.close()

    # Linha 1: #N  #m (Ordem na esteira + Numero no Olist)
    largura_num = fitz.get_text_length(texto_num, fontname="hebo", fontsize=FONTE_NUMERO)
    x_num = max(10, (largura - largura_num) / 2)
    # Baseline da 1a linha = topo da faixa + a altura da propria fonte. Antes
    # era um 9.5 fixo, calibrado pra fonte 8.5; com 12pt o texto encostava na
    # borda de cima. Derivar da fonte mantem o respiro em qualquer tamanho.
    y_num = altura - FAIXA_NUMERO_PT + FONTE_NUMERO + 1.0
    nova.insert_text(fitz.Point(x_num, y_num), texto_num, fontsize=FONTE_NUMERO,
                     fontname="hebo", color=COR)

    # Linha 2: SKU do produto vendido
    if texto_sku:
        largura_sku = fitz.get_text_length(texto_sku, fontname="helv", fontsize=FONTE_SKU)
        x_sku = max(8, (largura - largura_sku) / 2)
        y_sku = altura - 4.0
        nova.insert_text(fitz.Point(x_sku, y_sku), texto_sku, fontsize=FONTE_SKU,
                         fontname="helv", color=COR_SKU)



def _mapa_tiktok() -> dict[str, str]:
    """package_id -> numero do pedido no marketplace.

    A etiqueta do TikTok e' nomeada pelo PACOTE, mas a esteira raciocina em
    PEDIDO. Sem esta ponte as etiquetas do TikTok nunca casariam.
    """
    try:
        import core_etiquetas_tiktok_api as tt
    except Exception as exc:
        log.warning("Mapa TikTok indisponivel: %s", exc)
        return {}

    mapa: dict[str, str] = {}
    try:
        for pacote in (tt.listar_pacotes_a_enviar() or []):
            pid = str(pacote.get("id") or "")
            for pedido in (pacote.get("orders") or []):
                oid = str(pedido.get("id") or "")
                if pid and oid:
                    mapa[pid] = oid
    except Exception as exc:
        log.warning("Falha ao mapear pacotes TikTok: %s", exc)

    return mapa


def _sequencia_e_mapa_olist() -> tuple[list[str], dict[str, str], dict[str, str]]:
    """(ordem_da_esteira, {numero_ecommerce: numero_olist}, {numero_ecommerce: sku_formatado})."""
    import core_separacao as cs
    import core_sequencia_embalagem as seq
    import core_sync_expedicao as sync

    dados = sync.sincronizar([4, 7], max_pedidos=100)
    processado = cs.processar_batch_picking(dados["pedidos"])
    resultado = seq.sequenciar(processado)

    ordem = [str(p.get("numero_ecommerce") or "") for p in resultado["sequencia"]]
    mapa_olist = {
        str(p.get("numero_ecommerce") or ""): str(p.get("numero_olist") or "")
        for p in resultado["sequencia"]
    }
    mapa_skus = {
        str(p.get("numero_ecommerce") or ""): _formatar_itens_sku(p.get("itens") or [])
        for p in resultado["sequencia"]
    }
    return ordem, mapa_olist, mapa_skus



def _ordem_da_esteira() -> list[str]:
    """Numeros de pedido na ordem de embalagem definida pela esteira.

    Mantida por compatibilidade com quem so' precisa da ordem (nao do mapa
    Olist) — chama `_sequencia_e_mapa_olist()` e descarta o mapa.
    """
    ordem, _ = _sequencia_e_mapa_olist()
    return ordem


def gerar(*, com_cartao: bool = False,
          nome_real: bool = True,
          somente: set[str] | None = None,
          saida: str | Path | None = None,
          numerar: bool = True) -> dict[str, Any]:
    """PDF unico, dois canais, ordem da esteira, numerado #1..#N.

    Args:
        nome_real: acrescenta o nome civil entre parenteses quando a etiqueta
            veio com apelido do comprador ("Thata" -> "Thata (Aurora
            Machado)"). Os Correios devolvem ao remetente quando o nome nao
            corresponde a ninguem no endereco.
        somente: numeros de pedido a INCLUIR. `None` = todos.
            Usado pelas ONDAS de expedicao: a segunda leva do dia imprime so'
            o que ainda nao foi processado, em vez de repetir a pilha inteira
            ([[core_ondas_expedicao]]).
        numerar: False pula o carimbo #N #m na faixa. Uso interno de teste
            (`scratch/testar_faixa_com_sku.py`) — o script recompoe a faixa
            do zero (com SKU) e duplicaria o numero se este `gerar()` ja'
            tivesse carimbado antes. Producao sempre usa o default True.
    """
    import fitz

    import core_etiqueta_normalizar as norm
    import core_etiquetas_todas as todas

    inicio = time.time()

    # 1. Baixa os três canais (TikTok + Shopee + ML) em paralelo
    baixado = todas.baixar_tudo(canais=["tiktok", "shopee", "ml"], com_cartao=com_cartao, somente=somente)
    if not baixado.get("pdf"):
        # Mesmas chaves do caminho feliz: a tela le `resumo` e `erros` sem
        # checar, e um dict curto aqui quebraria a pagina com KeyError.
        return {"pdf": None, "total": 0, "fora_da_esteira": 0,
                "por_canal": baixado.get("por_canal") or {},
                "erros": baixado.get("erros") or ["Nenhuma etiqueta baixada nos canais"],
                "segundos": round(time.time() - inicio, 1),
                "resumo": "Nenhuma etiqueta disponível"}

    # 2. De qual pedido e' cada arquivo individual
    import core_etiqueta_com_cartao as ccc

    mapa_tt = _mapa_tiktok()
    arquivos: list[tuple[str, str]] = []   # (caminho, numero_do_pedido)

    for canal in ("tiktok", "shopee", "ml"):
        info = (baixado.get("por_canal") or {}).get(canal) or {}
        arqs_canal = info.get("arquivos") or []
        # Se nao houver arquivos individuais mas houver PDF consolidado do canal
        if not arqs_canal and info.get("pdf") and Path(info["pdf"]).exists():
            arqs_canal = [info["pdf"]]

        for caminho in arqs_canal:
            if not Path(caminho).exists():
                continue
            chave = Path(caminho).stem

            # Recorta a folha A4 da Shopee para 10x15. O TikTok e ML ja' vem no
            # tamanho e a funcao devolve sem mexer.
            try:
                res_norm = norm.normalizar_10x15(caminho)
                if res_norm.get("saida") and Path(res_norm["saida"]).exists():
                    caminho = res_norm["saida"]
            except Exception as exc:
                log.warning("Normalizacao de %s falhou: %s", chave, exc)

            # Cartao de agradecimento DO CANAL, colado logo apos a etiqueta.
            if com_cartao:
                alvo = caminho.replace(".pdf", "_cartao.pdf")
                try:
                    rc = ccc.intercalar_canal_unico(caminho, alvo, canal)
                    if rc.get("ok"):
                        caminho = alvo
                except Exception as exc:
                    log.warning("Cartao de %s falhou: %s", chave, exc)

            # TikTok nomeia por pacote; Shopee e ML ja' nomeiam pelo pedido
            arquivos.append((caminho, mapa_tt.get(chave, chave)))

    if not arquivos:
        return {
            "pdf": None,
            "total": 0,
            "fora_da_esteira": 0,
            "por_canal": baixado.get("por_canal") or {},
            "erros": baixado.get("erros") or ["Nenhuma etiqueta individual encontrada para montar a pilha."],
            "segundos": round(time.time() - inicio, 1),
            "resumo": "Nenhuma etiqueta disponível para montar a pilha",
        }

    # 3. Posicao de cada pedido na esteira + numero da Olist + SKU vendido
    try:
        _seq, mapa_olist, mapa_skus = _sequencia_e_mapa_olist()
        ordem = {num: i for i, num in enumerate(_seq)}
    except Exception as exc:
        log.warning("Sequencia da esteira indisponivel (%s); "
                    "mantendo a ordem original", exc)
        ordem = {}
        mapa_olist = {}
        mapa_skus = {}

    # Pedido fora da esteira vai para o fim — nunca some.
    FIM = 10_000
    arquivos.sort(key=lambda x: ordem.get(x[1], FIM))

    # ONDAS: tira o que ja' foi processado numa leva anterior.
    filtrados = 0
    if somente is not None:
        _alvo = {str(x).strip().upper() for x in somente}
        antes = len(arquivos)
        arquivos = [(c, n) for c, n in arquivos
                    if str(n).strip().upper() in _alvo]
        filtrados = antes - len(arquivos)
        if filtrados:
            log.info("Ondas: %d etiqueta(s) de pedido ja' processado ficaram "
                     "de fora desta pilha", filtrados)

    fora = sum(1 for _, num in arquivos if num not in ordem)

    # 4. Remonta na ordem certa em um novo documento limpo (doc_final)
    destino = Path(saida) if saida else Path(baixado["pdf"])
    doc_final = fitz.open()
    nomes_corrigidos = 0

    # 5. Nome civil ao lado do apelido
    mapa_pedido_civil = {}
    if nome_real:
        try:
            import core_etiqueta_nome_real as cnr
            ids_tt = [str(o) for o in mapa_tt.values()]
            mapa_pedido_civil = cnr.mapa_por_pedido_tiktok(ids_tt) or {}
        except Exception as exc:
            log.warning("Mapa de nomes civis indisponivel: %s", exc)

    for ordem_idx, (caminho, numero_pedido) in enumerate(arquivos, start=1):
        if not Path(caminho).exists():
            continue
        try:
            parcial = fitz.open(caminho)
            for pno in range(parcial.page_count):
                pag_orig = parcial[pno]
                largura, altura = pag_orig.rect.width, pag_orig.rect.height

                # So' a PRIMEIRA pagina de cada pedido recebe a faixa inferior e o carimbo
                if pno == 0 and numerar:
                    nova_pag = doc_final.new_page(width=largura, height=altura)
                    area_util = fitz.Rect(0, 0, largura, altura - FAIXA_NUMERO_PT)
                    nova_pag.show_pdf_page(area_util, parcial, pno)

                    # Nome civil se aplicavel (TikTok)
                    par_nome = mapa_pedido_civil.get(str(numero_pedido))
                    if par_nome:
                        try:
                            impresso, civil = par_nome
                            achados = nova_pag.search_for(impresso)
                            if achados:
                                caixa = achados[0]
                                texto = cnr.encurtar_para_caber(civil, caixa.x1, nova_pag.rect.width, "hebo", 8.3)
                                if texto:
                                    nova_pag.insert_text(fitz.Point(caixa.x1, caixa.y1), texto, fontsize=8.3, fontname="hebo", color=(0, 0, 0), overlay=True)
                                    nomes_corrigidos += 1
                        except Exception:
                            pass

                    # Carimbo #N  #m (Posição na Esteira + Número Sequencial Olist)
                    num_olist = mapa_olist.get(str(numero_pedido))
                    texto_num = f"#{ordem_idx}  #{num_olist}" if num_olist else f"#{ordem_idx}"
                    texto_sku = mapa_skus.get(str(numero_pedido), "")

                    largura_num = fitz.get_text_length(texto_num, fontname="hebo", fontsize=FONTE_NUMERO)
                    x_num = max(10, (largura - largura_num) / 2)
                    y_num = altura - FAIXA_NUMERO_PT + FONTE_NUMERO + 1.0
                    nova_pag.insert_text(fitz.Point(x_num, y_num), texto_num, fontsize=FONTE_NUMERO, fontname="hebo", color=COR)

                    if texto_sku:
                        largura_sku = fitz.get_text_length(texto_sku, fontname="helv", fontsize=FONTE_SKU)
                        x_sku = max(8, (largura - largura_sku) / 2)
                        y_sku = altura - 4.0
                        nova_pag.insert_text(fitz.Point(x_sku, y_sku), texto_sku, fontsize=FONTE_SKU, fontname="helv", color=COR_SKU)
                else:
                    # Demais páginas (ex: cartão de agradecimento) entram sem faixa
                    doc_final.insert_pdf(parcial, from_page=pno, to_page=pno)
            parcial.close()
        except Exception as exc:
            log.warning("Falha ao processar arquivo %s: %s", caminho, exc)

    if doc_final.page_count == 0:
        doc_final.close()
        return {
            "pdf": None,
            "total": 0,
            "fora_da_esteira": 0,
            "por_canal": baixado.get("por_canal") or {},
            "erros": ["Nenhuma página gerada nas etiquetas."],
            "segundos": round(time.time() - inicio, 1),
            "resumo": "Nenhuma página disponível",
        }

    doc_final.save(destino)
    doc_final.close()

    # 7. Limpa os PDFs por canal que o `baixar_tudo` deixou na pasta.
    # Sem isto cada clique enche o Downloads com 4-6 arquivos e o operador
    # perde qual e' o bom (Jota, 19/08: "baixou varias versoes na pasta").
    # O `destino` NUNCA entra na lista — e' justamente o que ficou pronto.
    for canal in ("tiktok", "shopee"):
        for sufixo in ("", "_com_cartao"):
            pdf_canal = (baixado.get("por_canal") or {}).get(canal, {}).get("pdf")
            if not pdf_canal:
                continue
            alvo = Path(str(pdf_canal).replace(".pdf", f"{sufixo}.pdf"))
            if alvo.exists() and alvo.resolve() != destino.resolve():
                try:
                    alvo.unlink()
                except OSError:
                    pass

    # O `_10x15` que a normalizacao cria como copia tambem sobra
    resto = destino.with_name(destino.stem + "_10x15.pdf")
    if resto.exists():
        try:
            resto.unlink()
        except OSError:
            pass

    segundos = round(time.time() - inicio, 1)
    resumo = f"{len(arquivos)} etiquetas na ordem da esteira"
    if nomes_corrigidos:
        resumo += f" · {nomes_corrigidos} com nome civil acrescentado"
    if fora:
        resumo += f" · {fora} fora da sequência (no fim da pilha)"

    return {
        "pdf": str(destino),
        "total": len(arquivos),
        # Quantas etiquetas ficaram de fora por ja' estarem numa onda.
        "filtrados_por_onda": filtrados,
        "fora_da_esteira": fora,
        "nomes_corrigidos": nomes_corrigidos,
        "por_canal": baixado.get("por_canal"),
        "erros": baixado.get("erros") or [],
        "segundos": segundos,
        "resumo": resumo,
    }


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    r = gerar()
    print()
    print(f"  {r['resumo']}  ({r['segundos']}s)")
    print(f"  {r['pdf']}")
    for e in r["erros"]:
        print(f"  ⚠️ {e}")
