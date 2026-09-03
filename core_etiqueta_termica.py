# ==============================================================================
# NOME DO SCRIPT: core_etiqueta_termica.py
# DESCRICAO: Converte o PDF final de etiquetas para o formato mais compativel
#            possivel com impressora termica generica (as de ~R$400).
# FUNCAO: Rasteriza cada pagina em bitmap cinza/preto puro -- sem fonte, sem
#         transparencia, sem vetor. E' o denominador comum que qualquer driver
#         termica aceita.
# STATUS: ATIVO
# VERSAO: 1.0
# DATA: 31/08/2026
# AUTOR: Terminador (001) / Claude
# ==============================================================================
"""Blindagem do PDF de etiquetas para termica barata.

Contexto (Jota, 31/08/2026): "comportamento estranho da impressora, mantem
apresentando o erro... ao baixar a etiqueta direto no site e imprimir
funcionou". Ou seja: o PDF CRU do canal imprime bem; o nosso, montado, nao.

Diferenca medida entre os dois:

    CRU (site, imprime OK)   -> 2 fontes TrueType EMBUTIDAS
    NOSSO (falha)            -> as 2 do canal
                              + Helvetica / Helvetica-Bold Type1 NAO embutidas
                                (carimbo #N #m e SKU, inseridos por nos)
                              + no cartao: 9 fontes NAO embutidas com nome
                                VAZIO (PDF malformado vindo do Chromium)

Fonte nao embutida obriga a impressora a substituir por conta propria. Termica
generica nao tem catalogo de fonte nenhum: dependendo do firmware ela pula a
pagina, imprime em branco ou aborta o job. Somado a isso, o alpha/SMask do logo
(ver `core_etiqueta_normalizar.achatar_transparencia`) exige blending que o
driver 1-bit nao faz.

A saida que elimina TODAS essas variaveis de uma vez: rasterizar. Depois de
virar bitmap, nao existe mais fonte para substituir, transparencia para compor
nem vetor para interpretar -- e' exatamente o que a impressora produziria
internamente, entregue pronto.

Uso:
    from core_etiqueta_termica import blindar_para_termica
    blindar_para_termica("etiquetas.pdf")            # sobrescreve
    blindar_para_termica("in.pdf", "out.pdf", modo="1bit")
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

# 203 DPI e' a resolucao nativa da termica (8 dots/mm), e gerar no nativo
# evita reamostragem do driver. MAS: a 203 DPI a barra mais fina da etiqueta
# TikTok (0,125mm) cai em EXATAMENTE 1 pixel -- e 1 pixel nao sobrevive a
# impressao: some ou engorda conforme o arredondamento da cabeca termica.
# A Shopee escapava porque a barra dela tem 0,250mm (2 px).
#
# ⚠️ Achado 02/09/2026 (Jota: "nao le o horizontal" do TikTok). Medido no PDF
# real: a 203 DPI o Code128 do rastreio nao decodifica depois de impresso; a
# 406 a mesma barra vira 2 px e sobrevive.
#
# 406 = DOBRO EXATO do nativo, de proposito. Multiplo inteiro mantem cada dot
# da cabeca alinhado com o pixel da imagem. Testado 300 DPI (nao-multiplo) e a
# chave da NF-e saiu CORROMPIDA na leitura (33260930746304... em vez de
# 33260965746389...) -- exatamente a reamostragem que o comentario original
# alertava. Nao trocar por um valor que nao seja multiplo de 203.
DPI_TERMICA = 406

# Acima deste brilho o pixel vira branco no modo 1bit. 128 e' o meio da
# escala; subir preserva tinta (linha fina sobrevive), descer limpa fundo.
LIMIAR_1BIT = 160

# Paginas cujo conteudo e' ARTE (cartao de agradecimento) saem em cinza, nao
# em 1-bit. Ver `_e_arte()` e o achado do emoji abaixo.
#
# ⚠️ Achado real 01/09/2026: o cartao tem o emoji 🎁 colorido (vermelho e
# amarelo). Cortado em 1-bit por limiar, as areas claras viraram branco, o
# contorno sumiu e a silhueta virou um "T" torto no papel (Jota: "as
# etiquetas de agradecimento estao com erro"). Em cinza o mesmo emoji sai
# perfeitamente legivel -- a termica resolve o meio-tom com o proprio
# dithering do driver, que e' aceitavel em ARTE (nao em codigo de barras).
#
# A etiqueta continua em 1-bit: la' o que importa e' barra solida e fundo
# limpo, e dithering em codigo de barras gera leitura falha.


def _para_1bit(pix, limiar: int = LIMIAR_1BIT):
    """Pixmap cinza -> preto/branco puro, sem meio-tom.

    Termica nao tem meio-tom real: ela simula com dithering, e dithering em
    codigo de barras/QR gera leitura falha. Cortando no limiar, barra fica
    solida e fundo fica limpo.

    ⚠️ SEM numpy de proposito (achado 01/09/2026). A versao anterior usava
    `numpy` e o `requirements-deploy.txt` (que a nuvem instala) nao tem
    numpy: na Streamlit Cloud dava ImportError, caia no `except` de quem
    chamava e o PDF saia SEM blindagem, em silencio -- o Jota recebeu um PDF
    com 10 fontes, 21 SMasks e 2 tamanhos de pagina achando que estava
    corrigido. `bytes.translate` faz o mesmo corte por limiar usando so' a
    biblioteca padrao, entao nao ha' mais o que faltar na nuvem.
    """
    import fitz

    # Garante 1 canal (cinza) antes de binarizar
    if pix.n > 1:
        pix = fitz.Pixmap(fitz.csGRAY, pix)

    # Tabela de 256 posicoes: abaixo do limiar -> 0, senao -> 255.
    # translate() aplica isso no buffer inteiro em C, sem laco Python.
    tabela = bytes(0 if i < limiar else 255 for i in range(256))
    return fitz.Pixmap(
        fitz.csGRAY, pix.width, pix.height, pix.samples.translate(tabela), 0
    )


def _e_arte(pagina) -> bool:
    """A pagina e' ARTE (cartao) em vez de etiqueta com codigo de barras?

    Serve para escolher entre cinza (preserva desenho) e 1-bit (preserva
    leitura de codigo de barras) -- ver o comentario de LIMIAR_1BIT.

    Criterio: a etiqueta de envio SEMPRE carrega numeros longos de rastreio
    (o codigo de barras vem acompanhado do numero impresso) ou a chave de
    NF-e de 44 digitos. O cartao de agradecimento nao tem nada disso -- e'
    texto corrido de agradecimento.

    ⚠️ NAO da' para detectar por "tem imagem grande": quando a pagina ja'
    chega rasterizada (caso do PDF que a Esteira monta), TODA pagina e' uma
    imagem grande unica e o teste classificaria tudo como etiqueta.
    """
    try:
        texto = pagina.get_text("text") or ""
        if not texto.strip():
            # Pagina sem texto extraivel (ja' rasterizada): sem como decidir,
            # trata como etiqueta -- 1-bit nunca prejudica leitura.
            return False
        import re
        # rastreio/chave de NF-e: sequencia longa de digitos
        if re.search(r"\d{15,}", texto.replace(" ", "")):
            return False
        # rastreio dos Correios (AA123456789BR)
        if re.search(r"\b[A-Z]{2}\d{9}[A-Z]{2}\b", texto):
            return False
        return True
    except Exception:
        return False


def blindar_para_termica(
    pdf: str | Path,
    saida: str | Path | None = None,
    *,
    dpi: int = DPI_TERMICA,
    modo: str = "1bit",
) -> dict[str, Any]:
    """Reescreve o PDF como bitmap cinza/preto, pagina a pagina.

    Args:
        pdf: PDF de entrada (o final, ja montado e numerado).
        saida: destino. Default = sobrescreve a entrada.
        dpi: resolucao do raster. Default 203 (nativo da termica).
        modo: "1bit" (preto/branco puro, recomendado) ou "cinza"
            (mantem tons -- so' se a arte precisar de gradiente).

    Retorna:
        {"saida", "paginas", "modo", "dpi", "mb"}
    """
    import fitz

    entrada = Path(pdf)
    if not entrada.exists():
        raise FileNotFoundError(f"PDF nao encontrado: {entrada}")

    destino = Path(saida) if saida else entrada

    origem = fitz.open(str(entrada))
    novo = fitz.open()

    # Tamanho UNICO para todas as paginas. Rasterizar preserva o tamanho de
    # cada pagina, e o PDF chega aqui com dois: a etiqueta em 283.46x425.20pt
    # (10x15cm exato) e o cartao em 282.96x425.04pt (Chromium). Alternar
    # tamanho a cada folha e' lido como "media size mismatch" pela termica,
    # que avanca papel/pula pagina na troca. Uniformizando aqui, o problema
    # morre mesmo que a origem volte a divergir.
    larguras = [origem[i].rect.width for i in range(origem.page_count)]
    alturas = [origem[i].rect.height for i in range(origem.page_count)]
    largura_alvo = max(larguras) if larguras else 0
    altura_alvo = max(alturas) if alturas else 0

    paginas_arte = 0

    for pno in range(origem.page_count):
        pag = origem[pno]
        # csGRAY + alpha=False: ja sai sem cor e sem transparencia, entao o
        # logo em SMask e' composto AQUI em vez de virar problema do driver.
        pix = pag.get_pixmap(dpi=dpi, colorspace=fitz.csGRAY, alpha=False)
        # Pagina de ARTE (cartao) fica em cinza: o 1-bit destruia o emoji 🎁
        # colorido, que virava um "T" torto no papel. Etiqueta continua em
        # 1-bit, onde barra solida importa mais que meio-tom.
        if modo == "1bit":
            if _e_arte(pag):
                paginas_arte += 1
            else:
                pix = _para_1bit(pix)

        nova = novo.new_page(width=largura_alvo, height=altura_alvo)
        # Encaixa preservando a proporcao (a diferenca e' de ~0.2%, entao
        # visualmente nada muda; o que importa e' o MediaBox ficar igual).
        escala = min(largura_alvo / pag.rect.width,
                     altura_alvo / pag.rect.height)
        larg = pag.rect.width * escala
        alt = pag.rect.height * escala
        destino_rect = fitz.Rect(
            (largura_alvo - larg) / 2,
            (altura_alvo - alt) / 2,
            (largura_alvo - larg) / 2 + larg,
            (altura_alvo - alt) / 2 + alt,
        )
        nova.insert_image(destino_rect, pixmap=pix)

    # deflate obrigatorio: bitmap cru de 203 DPI passa de 1 MB por pagina.
    # Em preto/branco puro o Flate comprime muito bem (grandes areas iguais).
    temporario = destino.with_name(f"{destino.stem}.__term__.pdf")
    novo.save(str(temporario), deflate=True, garbage=4)
    paginas = novo.page_count
    novo.close()
    origem.close()

    if destino.exists():
        destino.unlink()
    temporario.replace(destino)

    mb = round(destino.stat().st_size / 1024 / 1024, 2)
    log.info(
        "Blindado para termica: %s (%d paginas, %d em cinza por serem arte, "
        "%s, %d DPI, %s MB)",
        destino.name, paginas, paginas_arte, modo, dpi, mb,
    )

    return {
        "saida": str(destino),
        "paginas": paginas,
        "paginas_arte": paginas_arte,
        "modo": modo,
        "dpi": dpi,
        "mb": mb,
    }


if __name__ == "__main__":
    import sys

    logging.basicConfig(level=logging.INFO, format="%(message)s")

    if len(sys.argv) < 2:
        print("uso: python core_etiqueta_termica.py <arquivo.pdf> [saida.pdf]")
        raise SystemExit(1)

    destino = sys.argv[2] if len(sys.argv) > 2 else None
    r = blindar_para_termica(sys.argv[1], destino)
    print(f"{r['saida']}")
    print(f"  {r['paginas']} paginas — {r['modo']} @ {r['dpi']} DPI — {r['mb']} MB")
