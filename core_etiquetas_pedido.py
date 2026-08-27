# ==============================================================================
# NOME DO SCRIPT: core_etiquetas_pedido.py
# DESCRICAO: Motor de sincronizacao de etiquetas de SKU 40x25mm com a folha 10x15
# FUNCAO: Garante que a ordem fisica das etiquetas pequenas bata 1:1 com a 10x15
# STATUS: ATIVO
# VERSAO: 1.0
# DATA: 16/08/2026
# AUTOR: Violino (000) / Gemini CLI
# REF: plans/expedicao_master_2026-08-09.md (Modulo M2)
# ==============================================================================

from __future__ import annotations
import core_env_loader

import io
import os
import re
import logging
from typing import Any, Dict, List, Optional, Tuple

import fitz  # PyMuPDF
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas
from reportlab.graphics.barcode import code128

import core_scanner_db as db
import core_scanner_resolver as resolver

log = logging.getLogger("core_etiquetas_pedido")

LABEL_WIDTH_MM = 40.0
LABEL_HEIGHT_MM = 25.0
MARGEM_MM = 2.5
LOGOTIPO_PATH = "c:\\JF_Automacoes\\JEF_CO_semfundo.png"


def extrair_trackings_pdf(pdf_path: str) -> List[str]:
    """Extrai os códigos de rastreamento de um PDF 10x15 na ordem física exata."""
    trackings: List[str] = []
    if not os.path.isfile(pdf_path):
        return trackings

    doc = fitz.open(pdf_path)
    # Regex para trackings padrão Shopee (BR/SPX), ML (MLB/Correios) e TikTok
    padrao_tracking = re.compile(
        r"\b(BR\d{12}[A-Z0-9]?|SPXBR\d+|MLB\d+|[A-Z]{2}\d{9}[A-Z]{2}|TTC\d+)\b",
        re.IGNORECASE,
    )

    for page_num in range(len(doc)):
        page = doc[page_num]
        texto = page.get_text("text")
        matches = padrao_tracking.findall(texto)
        if matches:
            track = matches[0].strip().upper()
            if track not in trackings:
                trackings.append(track)
        else:
            # Fallback para extrair palavras que pareçam códigos de rastreio
            linhas = [l.strip() for l in texto.splitlines() if l.strip()]
            encontrado = False
            for linha in linhas:
                if (linha.startswith("BR") or linha.startswith("SPX") or linha.startswith("MLB")) and len(linha) >= 10:
                    trackings.append(linha.upper())
                    encontrado = True
                    break
            if not encontrado:
                trackings.append(f"DESCONHECIDO_PAG_{page_num+1}")

    return trackings


def preparar_etiquetas_da_fila_olist(pedidos: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Gera a sequência física de etiquetas 40x25mm (#1, #2...) DIRETO dos pedidos da API.

    Não necessita de upload manual de PDF 10x15: consome a ordem oficial da fila do Olist.
    """
    itens_etiquetas: List[Dict[str, Any]] = []
    seq = 1

    for ped in pedidos:
        num_ped = str(ped.get("numeroPedidoEcommerce") or ped.get("numero") or ped.get("id") or "")
        cliente = (ped.get("cliente") or {}).get("nome") or ped.get("cliente_nome") or "Cliente"
        itens = ped.get("itens") or []

        if not itens and ped.get("produto"):
            itens = [{"produto": ped.get("produto"), "quantidade": ped.get("quantidade", 1)}]

        for it in itens:
            prod = it.get("produto") or {}
            sku_bruto = prod.get("sku") or it.get("sku") or "SEM_SKU"
            descricao = prod.get("descricao") or prod.get("nome") or it.get("descricao") or sku_bruto
            qtd = int(it.get("quantidade") or 1)

            # Para cada unidade solicitada no pedido, emite 1 etiqueta com o número de sequência
            for _ in range(qtd):
                itens_etiquetas.append({
                    "sequencia": seq,
                    "tracking": num_ped,
                    "resolvido": bool(sku_bruto and sku_bruto != "SEM_SKU"),
                    "sku": sku_bruto,
                    "descricao": descricao,
                    "quantidade": 1,
                    "cliente": cliente,
                    "aviso": "" if sku_bruto != "SEM_SKU" else "SEM SKU",
                })
        seq += 1

    return itens_etiquetas


def resolver_sequencia_pedidos(trackings: List[str]) -> List[Dict[str, Any]]:

    """Cruza cada tracking com o banco do scanner e prepara as etiquetas com sequencial (#1, #2...)."""
    db.init_db()
    itens_etiquetas: List[Dict[str, Any]] = []

    for seq, track in enumerate(trackings, start=1):
        info = {
            "sequencia": seq,
            "tracking": track,
            "resolvido": False,
            "sku": "",
            "descricao": "",
            "quantidade": 1,
            "cliente": "",
            "aviso": "",
        }

        # Busca no banco local do scanner
        reg = db.buscar_por_tracking(track)
        if not reg:
            # Tenta resolver via resolver_codigo (Olist / Supabase)
            try:
                res = resolver.resolver_codigo(track)
                if res and res.get("encontrado"):
                    reg = res
            except Exception as e:
                log.warning("Erro ao resolver tracking %s: %s", track, e)

        if reg:
            info["resolvido"] = True
            info["sku"] = reg.get("sku_principal") or reg.get("sku") or "SEM_SKU"
            info["descricao"] = reg.get("produto_nome") or reg.get("descricao") or info["sku"]
            info["cliente"] = reg.get("cliente_nome") or ""
        else:
            info["aviso"] = "NÃO RESOLVIDO"
            info["sku"] = f"SEQ_{seq:03d}"
            info["descricao"] = "TRACKING NÃO IDENTIFICADO"

        itens_etiquetas.append(info)

    return itens_etiquetas


def _desenhar_etiqueta_sincronizada(
    c: canvas.Canvas,
    sequencia: int,
    sku: str,
    descricao: str,
    resolvido: bool = True,
):
    """Desenha 1 etiqueta 40x25mm com o número de sequência no topo (#N)."""
    c.saveState()
    cx = LABEL_WIDTH_MM / 2
    mx_l = MARGEM_MM
    mx_r = LABEL_WIDTH_MM - MARGEM_MM

    # 1. Cabeçalho compacto — o espaço economizado vai para o código de barras
    if os.path.exists(LOGOTIPO_PATH):
        c.drawImage(LOGOTIPO_PATH, mx_l * mm, 20.6 * mm,
                    width=3.0 * mm, height=3.0 * mm, mask='auto')

    c.setFont("Helvetica-Bold", 6.0)
    c.drawString((mx_l + 4.0) * mm, 21.2 * mm, "J&F Co.")

    # Tag de Sequência destacada no topo direito
    c.setFont("Helvetica-Bold", 7.5)
    c.drawRightString(mx_r * mm, 21.2 * mm, f"#{sequencia}")

    # Linha divisória
    c.setLineWidth(0.3)
    c.line(mx_l * mm, 20.0 * mm, mx_r * mm, 20.0 * mm)

    # 2. Código de Barras / Aviso
    if resolvido and sku and not sku.startswith("SEQ_"):
        try:
            # ⚠️ O código de barras ocupa quase toda a etiqueta. Antes usava
            # 80% da largura (32mm de 40mm) e afinava a barra até 0.08mm —
            # MENOS que o ponto da impressora térmica (203 dpi = 0.125mm).
            # Barra menor que o ponto vira borrão: era por isso que o celular
            # não lia (Jota, 2026-08-16).
            FOLGA_LATERAL_MM = 1.0
            largura_alvo = (LABEL_WIDTH_MM - 2 * FOLGA_LATERAL_MM) * mm

            # 🔴 Piso físico: 1.5x o ponto da impressora, o mínimo para a
            # barra sair definida. Nunca baixar disto — abaixo daqui a
            # etiqueta imprime mas não é legível.
            BW_MINIMO = 0.19 * mm
            BW_MAXIMO = 0.50 * mm

            # Começa largo e afina só até caber: SKU curto sai com barra
            # grossa em vez de pequeno e centralizado no meio da etiqueta.
            bw = BW_MAXIMO
            barcode_draw = code128.Code128(sku, barWidth=bw, barHeight=1,
                                           humanReadable=False)
            while barcode_draw.width > largura_alvo and bw > BW_MINIMO:
                bw -= 0.005 * mm
                barcode_draw = code128.Code128(sku, barWidth=bw, barHeight=1,
                                               humanReadable=False)

            # SKU longo não cabe nem na barra mínima (18 chars pedem ~54mm).
            # Nesse caso o código é ESTICADO na altura: barra alta compensa a
            # estreita, porque o leitor tem mais superfície para amostrar.
            estourou = barcode_draw.width > largura_alvo

            altura_barra = 12.5 * mm if estourou else 11.0 * mm
            y_barra = 6.6 * mm if estourou else 7.6 * mm

            barcode_draw = code128.Code128(sku, barWidth=bw,
                                           barHeight=altura_barra,
                                           humanReadable=False)

            if estourou:
                # Encolhe proporcionalmente para caber na etiqueta, mantendo
                # a altura — melhor um código apertado e alto do que um
                # código que sai cortado pela borda.
                fator = largura_alvo / barcode_draw.width
                c.saveState()
                c.translate(FOLGA_LATERAL_MM * mm, y_barra)
                c.scale(fator, 1.0)
                barcode_draw.drawOn(c, 0, 0)
                c.restoreState()
            else:
                x_pos = (LABEL_WIDTH_MM * mm - barcode_draw.width) / 2
                barcode_draw.drawOn(c, x_pos, y_barra)
        except Exception:
            c.setFont("Helvetica-Bold", 6)
            c.drawCentredString(cx * mm, 12 * mm, "[ERRO NO CÓDIGO]")
    else:
        # Etiqueta de Alerta (não pula posição)
        c.setFont("Helvetica-Bold", 7)
        c.setFillColorRGB(0.8, 0, 0)
        c.drawCentredString(cx * mm, 12 * mm, "⚠️ NÃO RESOLVIDO")
        c.setFillColorRGB(0, 0, 0)

    # 3. Rodapé com o SKU escrito — o operador confere a olho e, se o leitor
    # falhar, ainda dá para digitar (a busca aceita 3+ caracteres).
    c.setFont("Helvetica-Bold", 5.0)
    c.drawCentredString(cx * mm, 2.2 * mm, sku[:34])

    c.restoreState()


def gerar_pdf_etiquetas_sincronizadas(
    itens: List[Dict[str, Any]],
    output_path: Optional[str] = None,
) -> Any:
    """Gera o arquivo PDF contendo todas as etiquetas 40x25mm na ordem física exata.
    Se output_path for fornecido, salva no disco e retorna total de páginas.
    Se output_path for None, retorna os bytes do PDF em memória.
    """
    import io
    
    buf = io.BytesIO() if output_path is None else None
    target = output_path if output_path else buf
    
    if output_path:
        os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
        
    c = canvas.Canvas(target, pagesize=(LABEL_WIDTH_MM * mm, LABEL_HEIGHT_MM * mm))

    total = 0
    for item in itens:
        _desenhar_etiqueta_sincronizada(
            c,
            sequencia=item["sequencia"],
            sku=item["sku"],
            descricao=item["descricao"],
            resolvido=item["resolvido"],
        )
        c.showPage()
        total += 1

    c.save()
    
    if output_path is None:
        buf.seek(0)
        return buf.getvalue()
        
    return total
