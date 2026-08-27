# ==============================================================================
# NOME DO SCRIPT: core_etiqueta_merge.py
# DESCRICAO: Fallback de etiqueta casada (etiqueta logistica + DANFE simplificada)
#            numa unica folha termica 10x15, padrao Upseller. Usado SO quando a
#            etiqueta unificada nativa do Olist nao esta disponivel para o canal.
# AUTOR: Conselho J&F Co. - Terminador (001)
# VERSAO: 1.0
# DATA: 2026-06-17
# STATUS: Operacional (fallback)
# REF: exemplo I:\...\envios em 16-05.pdf pag.1 (300x442pt ~ 10x15cm)
# ==============================================================================
"""Compositor de etiqueta casada 10x15.

O padrao desejado (exemplo Upseller, pag.1): folha unica ~10x15cm com a etiqueta
logistica no topo e a DANFE simplificada na base. Tamanho observado: 300x442 pts
(300pt/72*2.54 = 10.58cm largura; 442pt = 15.6cm altura).

Estrategia (na ordem de preferencia):
  A) Se a etiqueta da plataforma JA vem casada (Olist unificado), nao usar este
     modulo — passar o PDF direto.
  B) Empilhar paginas: etiqueta (1 pag) + DANFE (1 pag) no mesmo PDF, cada uma
     em sua folha 10x15. Simples e robusto (merge_pdfs).
  C) Casar numa folha unica: etiqueta na metade de cima, DANFE na metade de
     baixo, ambas escaladas (compor_casada). Replica o layout Upseller.

Para fontes que vem em PNG/imagem (ex: alguns labels), convertemos para PDF
antes via reportlab+PIL.
"""

from __future__ import annotations

import io

from pypdf import PdfReader, PdfWriter, PageObject, Transformation
from reportlab.pdfgen import canvas
from reportlab.lib.units import cm

# Folha 10x15 em pontos (1cm = 28.346pt). Usamos 10x15 cheio.
LARGURA_10x15 = 10 * cm   # ~283.5pt
ALTURA_10x15 = 15 * cm    # ~425.2pt


# ------------------------------------------------------------------ #
# Conversao imagem -> PDF
# ------------------------------------------------------------------ #
def imagem_para_pdf(img_bytes: bytes, *, largura=LARGURA_10x15, altura=ALTURA_10x15) -> bytes:
    """Converte bytes de imagem (PNG/JPG) num PDF 10x15 de pagina unica."""
    from PIL import Image

    img = Image.open(io.BytesIO(img_bytes))
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=(largura, altura))
    # encaixa a imagem preservando proporcao, centralizada
    iw, ih = img.size
    escala = min(largura / iw, altura / ih)
    w, h = iw * escala, ih * escala
    x, y = (largura - w) / 2, (altura - h) / 2
    img_reader = _pil_para_reader(img)
    c.drawImage(img_reader, x, y, width=w, height=h, preserveAspectRatio=True, mask="auto")
    c.showPage()
    c.save()
    return buf.getvalue()


def _pil_para_reader(img):
    from reportlab.lib.utils import ImageReader
    buf = io.BytesIO()
    img.convert("RGB").save(buf, format="PNG")
    buf.seek(0)
    return ImageReader(buf)


# ------------------------------------------------------------------ #
# B) Empilhar paginas (robusto)
# ------------------------------------------------------------------ #
def merge_pdfs(*pdfs: bytes) -> bytes:
    """Concatena varios PDFs num unico arquivo (cada um em suas paginas).

    Ignora entradas vazias/None. Levanta ValueError se nada sobrar.
    """
    writer = PdfWriter()
    usados = 0
    for pdf in pdfs:
        if not pdf:
            continue
        reader = PdfReader(io.BytesIO(pdf))
        for pg in reader.pages:
            writer.add_page(pg)
        usados += 1
    if usados == 0:
        raise ValueError("merge_pdfs: nenhum PDF valido fornecido")
    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()


# ------------------------------------------------------------------ #
# C) Casar numa folha unica (layout Upseller)
# ------------------------------------------------------------------ #
def compor_casada(
    etiqueta_pdf: bytes,
    danfe_pdf: bytes,
    *,
    largura=LARGURA_10x15,
    altura=ALTURA_10x15,
    proporcao_etiqueta: float = 0.58,
) -> bytes:
    """Casa etiqueta (topo) + DANFE (base) numa unica folha 10x15.

    proporcao_etiqueta: fracao da altura para a etiqueta logistica (resto = DANFE).
    Pega a 1a pagina de cada PDF, escala para caber na sua faixa e sobrepoe.
    """
    et_page = _primeira_pagina(etiqueta_pdf)
    df_page = _primeira_pagina(danfe_pdf)

    folha = PageObject.create_blank_page(width=largura, height=altura)
    h_et = altura * proporcao_etiqueta
    h_df = altura - h_et

    # Etiqueta no topo (y = h_df, ocupa faixa superior)
    _mesclar_escalado(folha, et_page, largura, h_et, y_base=h_df)
    # DANFE na base (y = 0, faixa inferior)
    _mesclar_escalado(folha, df_page, largura, h_df, y_base=0)

    writer = PdfWriter()
    writer.add_page(folha)
    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()


def _primeira_pagina(pdf: bytes) -> PageObject:
    return PdfReader(io.BytesIO(pdf)).pages[0]


def _mesclar_escalado(destino: PageObject, origem: PageObject, larg_faixa, alt_faixa, *, y_base, x_base=0):
    """Escala `origem` para caber em (larg_faixa x alt_faixa) e mescla em destino.

    x_base = deslocamento horizontal base (margem). A origem e centralizada dentro
    de larg_faixa e entao deslocada por x_base — assim ML e faixa compartilham a
    mesma margem lateral e terminam com a mesma largura no papel.
    """
    ow = float(origem.mediabox.width)
    oh = float(origem.mediabox.height)
    escala = min(larg_faixa / ow, alt_faixa / oh)
    tx = x_base + (larg_faixa - ow * escala) / 2
    t = Transformation().scale(escala, escala).translate(tx, y_base)
    destino.merge_transformed_page(origem, t)


# ------------------------------------------------------------------ #
# Gerador de DANFE simplificada (quando o Olist so da viewer HTML)
# ------------------------------------------------------------------ #
# Altura da faixa "DANFE SIMPLIFICADO - ETIQUETA" (base da etiqueta unificada).
# Enxuta: titulo+dados em 1 linha cada, barras ocupando a largura toda, chave
# legivel. ~2.0cm e suficiente e respeita o limite de impressao termica.
ALTURA_FAIXA_DANFE = 2.0 * cm  # ~57pt

# Margem lateral unica — usada pela faixa E pela etiqueta ML, garantindo que as
# duas tenham EXATAMENTE a mesma largura util no papel.
MARGEM_LATERAL = 6  # pt


def faixa_danfe_etiqueta(
    nota: dict,
    *,
    largura=LARGURA_10x15,
    altura=ALTURA_FAIXA_DANFE,
) -> bytes:
    """Gera a FAIXA 'DANFE SIMPLIFICADO - ETIQUETA' (base da etiqueta unificada).

    Layout otimizado para leitura termica:
    - Code128 da chave ocupa TODA a largura util (margem lateral minima).
    - Numeros da chave em fonte maior, centralizados sob as barras.
    - Cabecalho compacto (titulo + NF/serie/emissao numa linha).
    - Mesma margem lateral da etiqueta ML -> larguras identicas no papel.

    Retorna PDF de pagina unica com a altura da faixa (para casar na base).
    """
    from reportlab.graphics.barcode import createBarcodeDrawing
    from reportlab.graphics import renderPDF

    chave = (nota.get("chaveAcesso") or "").replace(" ", "")
    serie = nota.get("serie", "")
    numero = nota.get("numero", "")
    emissao = nota.get("dataEmissao", "")
    tipo = "1-Saida" if str(nota.get("tipo", "S")).upper() == "S" else nota.get("tipo", "")

    m = MARGEM_LATERAL
    largura_util = largura - 2 * m

    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=(largura, altura))
    c.rect(1, 1, largura - 2, altura - 2)

    # cabecalho compacto numa linha so
    y = altura - 9
    c.setFont("Helvetica-Bold", 6.5)
    c.drawString(m, y, "DANFE SIMPLIFICADO")
    c.setFont("Helvetica", 6)
    c.drawRightString(largura - m, y, f"NF {numero}  Serie {serie}  {tipo}  {emissao}")
    y -= 4

    if chave:
        # barras ocupam a largura util inteira; altura aproveita o espaco restante
        altura_barras = max(y - 13, 18)
        d = createBarcodeDrawing(
            "Code128", value=chave, barHeight=altura_barras,
            width=largura_util, humanReadable=False,
        )
        renderPDF.draw(d, c, m, 12)
        # chave em texto maior, centralizada, monoespacada p/ leitura
        c.setFont("Courier-Bold", 6.2)
        c.drawCentredString(largura / 2, 4, chave)

    c.showPage()
    c.save()
    return buf.getvalue()


def gerar_danfe_completa(
    nota: dict,
    *,
    largura=LARGURA_10x15,
    altura=ALTURA_10x15,
) -> bytes:
    """DANFE simplificada COMPLETA em folha 10x15 (padrao Upseller pag.3).

    Documento auxiliar com titulo, NF, chave+barras, destinatario e itens. Usado
    quando se quer a DANFE em folha separada (modo 'empilhada'), nao a faixa.
    """
    from reportlab.graphics.barcode import createBarcodeDrawing
    from reportlab.graphics import renderPDF

    chave = (nota.get("chaveAcesso") or "").replace(" ", "")
    serie = nota.get("serie", "")
    numero = nota.get("numero", "")
    emissao = nota.get("dataEmissao", "")
    cli = nota.get("cliente") or {}
    end = cli.get("endereco") or {}
    cidade_uf = f"{end.get('municipio', '')} - {end.get('uf', '')}".strip(" -")
    itens = nota.get("itens") or []

    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=(largura, altura))
    y = altura - 18

    c.setFont("Helvetica-Bold", 9)
    c.drawString(8, y, "DANFE SIMPLIFICADA"); y -= 14
    c.setFont("Helvetica", 6.5)
    c.drawString(8, y, f"{nota.get('tipo','S')} Saida   Serie: {serie}   Numero: {numero}   Emissao: {emissao}")
    y -= 16

    if chave:
        d = createBarcodeDrawing("Code128", value=chave, barHeight=34, width=largura - 16)
        renderPDF.draw(d, c, 8, y - 34)
        y -= 42
        c.setFont("Helvetica", 5.5)
        c.drawCentredString(largura / 2, y, chave); y -= 14

    c.setFont("Helvetica-Bold", 6.5)
    c.drawString(8, y, "DESTINATARIO:"); y -= 10
    c.setFont("Helvetica", 6.5)
    c.drawString(8, y, f"{cli.get('nome','')}  {cidade_uf}"); y -= 14

    c.setFont("Helvetica-Bold", 6)
    c.drawString(8, y, "SKU"); c.drawString(110, y, "Descricao"); c.drawString(largura - 30, y, "QTD")
    y -= 9
    c.setFont("Helvetica", 6)
    total = 0
    for it in itens:
        prod = it.get("produto") or it
        sku = str(prod.get("sku") or prod.get("codigo") or "")[:18]
        desc = str(prod.get("descricao") or prod.get("nome") or "")[:30]
        qtd = it.get("quantidade") or prod.get("quantidade") or 1
        try:
            total += float(qtd)
        except (TypeError, ValueError):
            pass
        c.drawString(8, y, sku); c.drawString(110, y, desc); c.drawString(largura - 30, y, str(qtd))
        y -= 9
        if y < 20:
            break
    c.setFont("Helvetica-Bold", 6)
    c.drawString(8, max(y - 4, 8), f"Total itens: {total:g}")

    c.rect(3, 3, largura - 6, altura - 6)
    c.showPage()
    c.save()
    return buf.getvalue()


# Alias retrocompat (chamado por core_esteira). Aponta para a versao completa.
gerar_danfe_simplificada = gerar_danfe_completa


# ------------------------------------------------------------------ #
# Unificada padrao Upseller pag.1: faixa DANFE fina no topo + etiqueta ML abaixo
# ------------------------------------------------------------------ #
def compor_unificada(
    etiqueta_ml: bytes,
    nota: dict,
    *,
    largura=LARGURA_10x15,
    altura=ALTURA_10x15,
    altura_faixa=ALTURA_FAIXA_DANFE,
) -> bytes:
    """Replica a etiqueta unificada Upseller (pag.1): faixa fiscal no topo + etiqueta.

    A faixa 'DANFE SIMPLIFICADO - ETIQUETA' (NF/serie/emissao + chave + barras)
    ocupa uma tarja fina no topo; a etiqueta logistica do ML preenche o resto.
    """
    faixa_pdf = faixa_danfe_etiqueta(nota, largura=largura, altura=altura_faixa)
    faixa_page = _primeira_pagina(faixa_pdf)
    et_page = _primeira_pagina(etiqueta_ml)

    folha = PageObject.create_blank_page(width=largura, height=altura)
    alt_etiqueta = altura - altura_faixa
    # Mesma largura util para ML e faixa -> larguras identicas no papel.
    largura_util = largura - 2 * MARGEM_LATERAL
    # etiqueta ML no topo (alinhada a margem lateral), faixa fiscal na base (full).
    _mesclar_escalado(folha, et_page, largura_util, alt_etiqueta,
                      y_base=altura_faixa, x_base=MARGEM_LATERAL)
    _mesclar_escalado(folha, faixa_page, largura, altura_faixa, y_base=0, x_base=0)

    writer = PdfWriter()
    writer.add_page(folha)
    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()


# ------------------------------------------------------------------ #
# API de alto nivel
# ------------------------------------------------------------------ #
def etiqueta_casada(
    etiqueta: bytes,
    danfe: bytes,
    *,
    modo: str = "casada",
    etiqueta_eh_imagem: bool = False,
) -> bytes:
    """Gera a etiqueta final no padrao desejado.

    modo='casada'  -> folha unica etiqueta(topo)+DANFE(base) [padrao Upseller]
    modo='empilhada' -> etiqueta e DANFE em paginas 10x15 separadas
    etiqueta_eh_imagem=True converte a etiqueta (PNG/JPG) p/ PDF antes.
    """
    if etiqueta_eh_imagem:
        etiqueta = imagem_para_pdf(etiqueta)
    if modo == "empilhada":
        return merge_pdfs(etiqueta, danfe)
    return compor_casada(etiqueta, danfe)


# ------------------------------------------------------------------ #
# Diagnostico / autoteste
# ------------------------------------------------------------------ #
def _pdf_dummy(texto: str, w=LARGURA_10x15, h=ALTURA_10x15) -> bytes:
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=(w, h))
    c.setFont("Helvetica-Bold", 14)
    c.drawString(20, h - 40, texto)
    c.rect(5, 5, w - 10, h - 10)
    c.showPage()
    c.save()
    return buf.getvalue()


if __name__ == "__main__":
    et = _pdf_dummy("ETIQUETA LOGISTICA (topo)")
    df = _pdf_dummy("DANFE SIMPLIFICADA (base)")
    casada = etiqueta_casada(et, df, modo="casada")
    r = PdfReader(io.BytesIO(casada))
    pg = r.pages[0]
    print(f"Casada: {len(r.pages)} pag, {float(pg.mediabox.width):.0f}x{float(pg.mediabox.height):.0f}pt")
    empilhada = etiqueta_casada(et, df, modo="empilhada")
    print(f"Empilhada: {len(PdfReader(io.BytesIO(empilhada)).pages)} pag")
    with open("scratch/_teste_etiqueta_casada.pdf", "wb") as f:
        f.write(casada)
    print("Salvo: scratch/_teste_etiqueta_casada.pdf")
