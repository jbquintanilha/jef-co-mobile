# ==============================================================================
# NOME DO SCRIPT: core_etiquetas.py
# DESCRICAO: Motor de geracao de etiquetas de SKU termicas 40x25mm (paisagem)
#            Gera PDFs vetoriais perfeitos para impressoras termicas e mockups HTML.
# STATUS: OPERACIONAL — layout 40x25mm VALIDADO em impressao fisica (2026-08-09)
# VERSAO: 3.3 | DATA: 2026-08-09
# AUTOR: Violino (000) | calibracao 40x25mm: Terminador (001)
#
# ATENCAO — armadilha documentada (ver memoria
# reference_etiqueta_produto_40x25_layout): NAO usar PRINTER_X_OFFSET_MM
# negativo para "corrigir" corte na direita. Isso empurra o conteudo pra fora
# na esquerda. O layout ja nasce centralizado com MARGEM_MM=2.5 simetrica.
# Offset e calibracao de HARDWARE, nao conserto de layout.
# ==============================================================================

import io
import os
import base64
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas
from reportlab.graphics.barcode import code128

# Constantes de hardware da impressora LABEL 2
# Etiqueta fisica: 40mm largura x 25mm altura (paisagem)
# A bobina alimenta pelo eixo de 40mm — impressora imprime 1 etiqueta por vez
# Pagina PDF = 1 etiqueta = 40mm x 25mm, SEM empilhamento
LABEL_WIDTH_MM    = 40.0   # largura da etiqueta (eixo X do PDF)
LABEL_HEIGHT_MM   = 25.0   # altura da etiqueta  (eixo Y do PDF)
PRINTER_PAGE_H    = 150.0  # referencia driver (nao usado para empilhamento)
LABELS_PER_PAGE   = 1      # 1 etiqueta por pagina — driver cuida do avanço
LOGOTIPO_PATH     = "c:\\JF_Automacoes\\JEF_CO_semfundo.png"

# Offset de calibracao de hardware (mm). Ajustar SO se a impressora fisica
# estiver deslocada. Layout desenhado com margem simetrica de 2.5mm, entao o
# padrao e ZERO — offset negativo empurrava o conteudo pra fora da etiqueta,
# que era a causa real dos cortes na esquerda (corrigido 2026-08-09).
PRINTER_X_OFFSET_MM = 0.0

# Margem de seguranca interna: area util = 40 - (2 x 2.5) = 35mm de largura
MARGEM_MM = 2.5


def _desenhar_uma_etiqueta(c: canvas.Canvas, sku: str, cor: str, tamanho: str,
                           categoria: str, offset_y: float, modo_full: bool = False,
                           spu: str = "", so_barcode: bool = False):
    """so_barcode=True: nada escrito abaixo do codigo de barras, e o rodape
    mostra 'SKU: <sku>' no lugar de 'SPU: <spu>'. Usado nas etiquetas de
    meia (produto tem SKU unico por cor+tamanho); o Top mantem o padrao
    normal (SPU no rodape) porque nao tem SKU unico sem o tamanho."""
    """Desenha uma unica etiqueta paisagem 40x25mm na posicao Y informada."""
    c.saveState()
    if PRINTER_X_OFFSET_MM:
        c.translate(PRINTER_X_OFFSET_MM * mm, 0)

    base_y = offset_y
    cx = LABEL_WIDTH_MM / 2          # centro horizontal = 20.0mm
    mx_l = MARGEM_MM                 # margem esquerda = 2.5mm
    mx_r = LABEL_WIDTH_MM - MARGEM_MM  # margem direita  = 37.5mm

    # 1. Cabecalho — Logo + nome empresa (condicional para Meias)
    is_meia = "meia" in categoria.lower() or "meia" in sku.lower() or "mei" in sku.lower()
    header_text = "J&F Co." if is_meia else "J&F Co. Premium"

    if os.path.exists(LOGOTIPO_PATH):
        c.drawImage(LOGOTIPO_PATH, mx_l * mm, base_y + 19.6 * mm,
                    width=3.4 * mm, height=3.4 * mm, mask='auto')
    c.setFont("Helvetica-Bold", 6.0)
    c.drawCentredString(cx * mm, base_y + 20.4 * mm, header_text)

    # Linha divisoria
    c.setLineWidth(0.3)
    c.line(mx_l * mm, base_y + 18.9 * mm, mx_r * mm, base_y + 18.9 * mm)

    # 2. Codigo de Barras Code 128 / Modo Full
    # Padrao UNICO pra todos os produtos: barras grandes ocupando 80% da
    # largura (32mm de 40mm) e toda a altura livre ate a divisoria do
    # rodape, nada escrito abaixo (facilita leitura/impressao). O que muda
    # por produto e so o rotulo do rodape (SKU ou SPU), via so_barcode.
    if not modo_full:
        try:
            largura_alvo = LABEL_WIDTH_MM * 0.8 * mm  # 32mm
            altura_barra = 10.0 * mm
            y_barra = base_y + 7.4 * mm  # sobe ate quase a divisoria do rodape

            # comeca com um barWidth generoso e reduz ate caber na largura alvo
            # (Code128 nao tem largura previsivel por char — precisa medir)
            bw = 0.30 * mm
            barcode_draw = code128.Code128(sku, barWidth=bw, barHeight=altura_barra,
                                           humanReadable=False)
            while barcode_draw.width > largura_alvo and bw > 0.04 * mm:
                bw -= 0.01 * mm
                barcode_draw = code128.Code128(sku, barWidth=bw, barHeight=altura_barra,
                                               humanReadable=False)

            # centraliza de verdade na etiqueta inteira
            x_pos = (LABEL_WIDTH_MM * mm - barcode_draw.width) / 2
            barcode_draw.drawOn(c, x_pos, y_barra)
        except Exception:
            c.setFont("Helvetica-Bold", 6)
            c.drawCentredString(cx * mm, base_y + 13 * mm, "[ERRO BARRAS]")
    else:
        # Modo Full: SKU grande, sem barcode
        c.setFont("Helvetica-Bold", 10.0)
        c.drawCentredString(cx * mm, base_y + 11.5 * mm, sku)

    # Linha separadora rodape
    c.setLineWidth(0.3)
    c.line(mx_l * mm, base_y + 6.3 * mm, mx_r * mm, base_y + 6.3 * mm)

    # 3. Rodape — Tam/Cor (tamanho omitido se vazio) e SKU ou SPU. SEM categoria/material.
    c.setFont("Helvetica-Bold", 6.0)
    linha_tam_cor = f"Cor: {cor}" if not tamanho else f"Tam: {tamanho}  |  Cor: {cor}"
    _texto_ajustado(c, linha_tam_cor, cx, base_y + 3.6, 6.0)

    if so_barcode:
        _texto_ajustado(c, f"SKU: {sku}", cx, base_y + 1.4, 5.5)
    elif spu:
        _texto_ajustado(c, f"SPU: {spu}", cx, base_y + 1.4, 5.5)
    c.restoreState()


def _texto_ajustado(c: canvas.Canvas, texto: str, cx: float, y_mm: float,
                    tamanho_fonte: float, fonte: str = "Helvetica-Bold") -> None:
    """Escreve centralizado, ENCOLHENDO a fonte ate caber na area util.

    Sem isso o texto longo e' cortado silenciosamente: um SKU de kit misto
    (`MEMEDMAY1034046-BRA6-PRE6_KIT12`) saia impresso como `..._KIT1`, com o
    ultimo caractere comido -- etiqueta com SKU errado, pior que etiqueta sem
    SKU (bug real achado no teste de lote, 2026-08-10).
    """
    largura_util = (LABEL_WIDTH_MM - 2 * MARGEM_MM) * mm
    fonte_atual = tamanho_fonte
    while fonte_atual > 3.0:
        c.setFont(fonte, fonte_atual)
        if c.stringWidth(texto, fonte, fonte_atual) <= largura_util:
            break
        fonte_atual -= 0.25
    c.setFont(fonte, fonte_atual)
    c.drawCentredString(cx * mm, y_mm * mm, texto)


def gerar_pdf_etiquetas_40x25(etiquetas_lista: list[dict], modo_full: bool = False) -> bytes:
    """
    Gera PDF com etiquetas 40x25mm paisagem (1 etiqueta por pagina).
    etiquetas_lista = lista de dicts:
      [{"sku": str, "cor": str, "tamanho": str, "spu": str, "categoria": str, "quantidade": int}]
    """
    page_width  = LABEL_WIDTH_MM  * mm   # 40mm
    page_height = LABEL_HEIGHT_MM * mm   # 25mm

    buffer = io.BytesIO()
    c = canvas.Canvas(buffer, pagesize=(page_width, page_height))

    for item in etiquetas_lista:
        sku      = str(item.get("sku", "")).strip()
        cor      = str(item.get("cor", "Diversos")).strip()
        tamanho  = str(item.get("tamanho", "U")).strip()
        categoria = str(item.get("categoria", "Lingerie")).strip()
        spu      = str(item.get("spu", "")).strip()
        so_barcode = bool(item.get("so_barcode", False))
        quantidade = int(item.get("quantidade", 1))

        for _ in range(quantidade):
            _desenhar_uma_etiqueta(c, sku, cor, tamanho, categoria, offset_y=0,
                                   modo_full=modo_full, spu=spu, so_barcode=so_barcode)
            c.showPage()

    c.save()
    buffer.seek(0)
    return buffer.getvalue()


def renderizar_mockup_etiqueta_html(sku: str, cor: str, tamanho: str, spu: str, categoria: str, modo_full: bool = False) -> str:
    """
    Retorna uma string contendo codigo HTML/CSS de visualizacao previa de alta fidelidade
    simulando perfeitamente a etiqueta fisica paisagem 40x25mm com borda, logotipo J&F Co. em base64 e centralizacao.
    """
    logo_base64 = ""
    logo_path = "c:\\JF_Automacoes\\JEF_CO_semfundo.png"
    if os.path.exists(logo_path):
        try:
            with open(logo_path, "rb") as img_file:
                logo_base64 = base64.b64encode(img_file.read()).decode("utf-8")
        except Exception:
            pass

    if logo_base64:
        logo_html = f'<img src="data:image/png;base64,{logo_base64}" style="width: 18px; height: 18px; object-fit: contain;">'
    else:
        logo_html = '<span style="font-size: 8px; font-weight: bold;">⭐</span>'

    if modo_full:
        mid_content = f"""<div style="flex:1; display:flex; align-items:center; justify-content:center; width:100%; padding:2px; box-sizing:border-box; overflow:hidden;">
<span style="font-size:14px; font-weight:bold; text-align:center; word-break:break-all; line-height:1.2;">{sku}</span>
</div>"""
    else:
        mid_content = f"""<div style="flex:1; display:flex; flex-direction:column; align-items:center; justify-content:center; width:100%; padding:2px 4px; box-sizing:border-box; overflow:hidden; gap:2px;">
<div style="display:flex; align-items:flex-end; justify-content:center; height:45px; width:100%; overflow:hidden;">
{"".join(f'<div style="width:{int(c)%3+1}px; height:100%; background:#000; margin-right:1px; flex-shrink:0;"></div>' for c in str(abs(hash(sku)))[:40])}
{"".join(f'<div style="width:{int(c)%2+1}px; height:100%; background:#000; margin-right:1px; flex-shrink:0;"></div>' for c in str(abs(hash(spu)))[:16])}
</div>
<span style="font-size:9px; font-weight:bold; text-align:center; word-break:break-all; line-height:1.1;">{sku}</span>
</div>"""

    is_meia = "meia" in categoria.lower() or "meia" in sku.lower() or "mei" in sku.lower()
    header_text = "J&amp;F Co." if is_meia else "J&amp;F Co. Premium"
    
    return f"""<div style="width:256px; height:160px; background:#fff; border:2px solid #000; border-radius:4px; padding:4px 6px; box-sizing:border-box; font-family:'Courier New',Courier,monospace; color:#000; display:flex; flex-direction:column; margin:10px auto; box-shadow:0 4px 10px rgba(0,0,0,0.3);">
<div style="display:flex; justify-content:space-between; align-items:center; border-bottom:1px solid #000; padding-bottom:2px; flex-shrink:0;">
{logo_html}
<span style="font-size:9px; font-weight:bold;">{header_text}</span>
</div>
{mid_content}
<div style="border-top:1px solid #ccc; padding-top:2px; flex-shrink:0; display:flex; flex-direction:column; align-items:center; justify-content:center;">
<div style="font-size:8px; font-weight:bold; text-align:center; white-space:nowrap; overflow:hidden; text-overflow:ellipsis;">Tam: {tamanho} | Cor: {cor}</div>
<div style="font-size:7px; text-align:center; overflow:hidden; text-overflow:ellipsis;">{categoria}</div>
</div>
</div>"""
