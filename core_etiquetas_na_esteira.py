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
import core_env_loader

import logging
import time
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)


# Faixa compacta no rodape pros numeros #N #m e SKU do produto vendido.
# Tamanho reduzido (7mm) com fontes discretas no padrao do codigo da DANFE
# para economizar espaco e nao cortar nenhuma margem/borda da etiqueta original.
FAIXA_NUMERO_PT = 7 / 25.4 * 72   # 7mm ≈ 19.8pt
FONTE_NUMERO = 8.5
FONTE_SKU = 6.0
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
    largura_num = fitz.get_text_length(texto_num, fontname="helv", fontsize=FONTE_NUMERO)
    x_num = max(10, (largura - largura_num) / 2)
    y_num = altura - FAIXA_NUMERO_PT + 9.5
    nova.insert_text(fitz.Point(x_num, y_num), texto_num, fontsize=FONTE_NUMERO,
                     fontname="hebo", color=COR)

    # Linha 2: SKU do produto vendido (tamanho compacto DANFE)
    if texto_sku:
        largura_sku = fitz.get_text_length(texto_sku, fontname="helv", fontsize=FONTE_SKU)
        x_sku = max(8, (largura - largura_sku) / 2)
        y_sku = altura - 3.0
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

    # 4. Remonta na ordem certa, anotando onde comeca cada PEDIDO
    destino = Path(saida) if saida else Path(baixado["pdf"])
    doc = fitz.open()
    primeira_pagina: list[int] = []

    for caminho, _ in arquivos:
        if not Path(caminho).exists():
            continue
        try:
            parcial = fitz.open(caminho)
            if parcial.page_count > 0:
                primeira_pagina.append(doc.page_count)   # onde este pedido comeca
                doc.insert_pdf(parcial)
            parcial.close()
        except Exception as exc:
            log.warning("Falha ao abrir PDF individual %s: %s", caminho, exc)

    if doc.page_count == 0:
        doc.close()
        return {
            "pdf": None,
            "total": 0,
            "fora_da_esteira": 0,
            "por_canal": baixado.get("por_canal") or {},
            "erros": ["Nenhuma página válida encontrada nas etiquetas para gerar o PDF."],
            "segundos": round(time.time() - inicio, 1),
            "resumo": "Nenhuma página de etiqueta disponível",
        }

    # 5. Nome civil ao lado do apelido, ANTES de numerar.
    #
    # A ordem importa: `completar_nomes` usa `search_for` para achar o nome na
    # pagina. Rodando depois do carimbo o "#N" ja' estaria la', sem prejuizo
    # direto — mas gravar o PDF duas vezes dobra o tempo a toa.
    #
    # ⚠️ CORRIGIDO 25/08 (achado real, Jota): a versao antiga testava CADA
    # pagina contra o mapa INTEIRO (`for pagina: for impresso, civil in
    # mapa.items()`) — cruzamento por TEXTO/semelhanca. Nick curto de 1
    # pedido ("jo") casava via `search_for` (substring, nao palavra inteira)
    # dentro do texto de paginas de OUTROS pedidos — "Jocinete Neri De Lima"
    # (civil de "jo") vazou pra 4 outras etiquetas so' porque "jo" aparecia
    # em algum canto do texto delas.
    #
    # Correcao real: o cruzamento agora e' por DADO CONHECIDO, o
    # `numero_pedido` de `arquivos[i]` — ja' sabemos com certeza de quem e'
    # cada pagina, porque fomos NOS quem montou `arquivos`/`primeira_pagina`
    # nessa ordem (secao 4, acima). Nao ha' "achar nome parecido": o
    # `mapa_pedido.get(numero_pedido)` decide QUAL par usar antes de tocar a
    # pagina; o `search_for(impresso)` abaixo so' acha a POSICAO x/y do nick
    # original DENTRO da pagina certa — nunca decide se aplica ou nao.
    nomes_corrigidos = 0
    if nome_real:
        try:
            import core_etiqueta_nome_real as cnr

            # Direto da API do TikTok (`cpf_name`), NAO do Olist — mesma
            # informacao, mas uma chamada em lote em vez de uma por pedido.
            # Media medida em 19/08: 4,9s para 5 pedidos, contra ~200s pelo
            # Olist (o gargalo dominava o tempo total da geracao).
            ids_tt = [str(o) for o in mapa_tt.values()]
            mapa_pedido = cnr.mapa_por_pedido_tiktok(ids_tt)
            if mapa_pedido:
                # `arquivos[i]` e `primeira_pagina[i]` andam pareados (mesmo
                # loop na secao 4) — reusa o INDICE, nao busca por nome.
                for i, (_caminho, numero_pedido) in enumerate(arquivos):
                    par = mapa_pedido.get(str(numero_pedido))
                    if not par:
                        continue
                    impresso, civil = par
                    pagina = doc[primeira_pagina[i]]

                    # Restringe a busca ao PROPRIO pedido: mesmo com o par
                    # certo, so' escreve se o nick aparecer NESTA pagina
                    # especificamente — nunca aplica em pagina alheia.
                    achados = pagina.search_for(impresso)
                    if not achados:
                        continue
                    caixa = achados[0]
                    texto = cnr.encurtar_para_caber(
                        civil, caixa.x1, pagina.rect.width, "hebo", 8.3)
                    if texto is None:
                        continue
                    pagina.insert_text(fitz.Point(caixa.x1, caixa.y1),
                                       texto, fontsize=8.3,
                                       fontname="hebo", color=(0, 0, 0),
                                       overlay=True)
                    nomes_corrigidos += 1
        except Exception as exc:
            # Nome e' melhoria; nunca pode impedir a etiqueta de sair
            log.warning("Nome civil nao aplicado: %s", exc)

    # 6. Numera so' a pagina de abertura de cada pedido
    #
    # ⚠️ "#N #m" desde 26/08 (pedido do Jota): #N e' a posicao na pilha
    # (igual sempre foi, casa com a etiqueta 40x25); #m e' o numero
    # SEQUENCIAL da Olist (ex: #546) — confere direto contra o pedido de
    # venda/NF sem trocar de tela. `mapa_olist` vem de
    # `_sequencia_e_mapa_olist()`; pedido sem match (fora da esteira, ou
    # sequencia indisponivel) carimba so' o #N, nunca quebra a numeracao.
    #
    # ⚠️ FAIXA PROPRIA em vez de sobrepor (mudanca 26/08, achado real: o
    # `overlay=True` colocava o "#20" colado no codigo de barras da etiqueta
    # do TikTok — legivel, mas arriscado). Agora a pagina de abertura de
    # cada pedido e' RECOMPOSTA: a etiqueta original encolhe e sobe pro
    # topo, abrindo uma faixa vazia de 12mm no rodape, dedicada so' aos
    # numeros — nunca mais sobre conteudo real. So' a 1a pagina de cada
    # pedido (a que leva o carimbo) passa por isto; cartao e demais paginas
    # ficam intocados.
    if numerar:
        for ordem_pedido, idx in enumerate(primeira_pagina, start=1):
            numero_ecommerce = arquivos[ordem_pedido - 1][1]
            num_olist = mapa_olist.get(str(numero_ecommerce))
            texto_num = f"#{ordem_pedido}  #{num_olist}" if num_olist else f"#{ordem_pedido}"
            texto_sku = mapa_skus.get(str(numero_ecommerce), "")
            _recompor_com_faixa_numero(doc, idx, texto_num, texto_sku)


    doc.save(destino)
    doc.close()

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
