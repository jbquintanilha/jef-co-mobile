# ==============================================================================
# NOME DO SCRIPT: gerar_cartao_amazon_brinde.py
# DESCRICAO: Gera o cartao de agradecimento da Amazon com anuncio de Brinde (Meia Invisivel)
# FUNCAO: Modelo de teste para os primeiros 15 pedidos -- foco em unboxing e prova social
# STATUS: ATIVO (MODELO PILOTO)
# MOTOR: Playwright Chromium + PyMuPDF Normalizacao Termica
# VERSAO: 2.0 (Padrao Chamego + Brinde Surpresa)
# DATA: 27/09/2026
# AUTOR: Violino (000) / J&F Co.
# ==============================================================================

import os
import sys
import base64
import shutil
from pathlib import Path
from playwright.sync_api import sync_playwright
import fitz

RAIZ = Path(r"c:\JF_Automacoes")
ASSETS_DIR = RAIZ / "scratch" / "cartao_assets"
CARTOES_DIR = RAIZ / "cartoes"
DOWNLOADS_DIR = Path(os.path.expanduser("~")) / "Downloads"

def carregar_b64(caminho: Path) -> str:
    with open(caminho, "rb") as f:
        return "data:image/png;base64," + base64.b64encode(f.read()).decode()

def montar_html_amazon_brinde() -> str:
    logo_b64 = carregar_b64(ASSETS_DIR / "flatten_clean_20.png")
    mimo_b64 = carregar_b64(ASSETS_DIR / "flatten_clean_22.png")
    # Sem QR code nem WhatsApp no rodape (Jota, 03/10/2026): o QR levava fluxo
    # para um canal que ainda nao esta pronto. Duvidas so' pelo atendimento da Amazon.

    return f"""<!DOCTYPE html>
<html lang="pt-BR">
<head>
<meta charset="UTF-8">
<style>
  @page {{
    size: 100mm 150mm;
    margin: 0;
  }}
  *, *::before, *::after {{
    box-sizing: border-box;
    margin: 0;
    padding: 0;
  }}
  html, body {{
    width: 100mm;
    height: 150mm;
    background: #ffffff;
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Arial, sans-serif;
    color: #000000;
    overflow: hidden;
    -webkit-print-color-adjust: exact;
    print-color-adjust: exact;
  }}
  .moldura-externa {{
    position: absolute;
    top: 4mm;
    left: 4mm;
    right: 4mm;
    bottom: 4mm;
    border: 3.2px solid #000000;
    display: flex;
    flex-direction: column;
    align-items: center;
    padding: 2.2mm 4mm 2.2mm 4mm;
    justify-content: space-between;
    text-align: center;
  }}
  .header-logo {{
    width: 32mm;
    height: auto;
    max-height: 20mm;
    object-fit: contain;
    margin-top: 0.3mm;
  }}
  .linha-divisoria {{
    width: 100%;
    height: 1.5px;
    background-color: #000000;
    margin: 1mm 0;
  }}
  .titulo-chegou {{
    font-size: 18pt;
    font-weight: 900;
    letter-spacing: 0.5px;
    line-height: 1.1;
    margin: 0.2mm 0 0.6mm 0;
    text-transform: uppercase;
  }}
  .texto-carinho {{
    font-size: 8.1pt;
    font-weight: 700;
    line-height: 1.3;
    color: #111111;
    padding: 0 1mm;
  }}
  .bloco-mimo {{
    width: 100%;
    padding: 1mm 2.5mm;
    display: flex;
    flex-direction: column;
    align-items: center;
    margin: 0.6mm 0;
  }}
  .linha-mimo-titulo {{
    display: flex;
    align-items: center;
    justify-content: center;
    gap: 1.8mm;
    margin-bottom: 0.8mm;
  }}
  .icone-mimo {{
    width: 12mm;
    height: 12mm;
    object-fit: contain;
  }}
  .titulo-mimo {{
    font-size: 13pt;
    font-weight: 900;
    text-transform: uppercase;
    letter-spacing: 0.3px;
  }}
  .texto-mimo {{
    font-size: 8pt;
    font-weight: 800;
    line-height: 1.28;
    margin-bottom: 1.2mm;
  }}
  .estrelas {{
    font-size: 15pt;
    letter-spacing: 2px;
    line-height: 1;
    margin: 0.5mm 0;
  }}
  .titulo-avaliacao {{
    font-size: 9.5pt;
    font-weight: 900;
    text-transform: uppercase;
    letter-spacing: 0.4px;
    margin-bottom: 0.5mm;
  }}
  .texto-avaliacao {{
    font-size: 7.8pt;
    font-weight: 700;
    line-height: 1.28;
    padding: 0 1mm;
  }}
  .bloco-rodape {{
    width: 100%;
    display: flex;
    align-items: center;
    justify-content: center;
    gap: 2mm;
    text-align: center;
    margin-top: 0.5mm;
  }}
  .texto-suporte {{
    font-size: 7.4pt;
    font-weight: 700;
    line-height: 1.26;
    flex: 1;
  }}
  .assinatura-maite {{
    font-size: 8.8pt;
    font-weight: 800;
    margin-top: 0.8mm;
    margin-bottom: 0.2mm;
  }}
</style>
</head>
<body>
  <div class="moldura-externa">
    <img src="{logo_b64}" class="header-logo" alt="J&F Co.">
    
    <div class="linha-divisoria"></div>
    
    <div class="titulo-chegou">SEU PEDIDO CHEGOU!</div>
    
    <div class="texto-carinho">
      Cada detalhe foi pensado para trazer conforto, beleza e carinho ao seu dia a dia! Estamos muito felizes com a sua compra e preparamos seu pacote com todo o cuidado para superar suas expectativas.
    </div>
    
    <div class="bloco-mimo">
      <div class="linha-mimo-titulo">
        <img src="{mimo_b64}" class="icone-mimo" alt="Presente com Corações">
        <div class="titulo-mimo">QUER UM CHAMEGO?</div>
      </div>
      <div class="texto-mimo">
        Para celebrar nossa chegada à Amazon e agradecer pela confiança na J&F Co., enviamos junto do seu pedido um mimo, outro modelo de meia da nossa linha.
      </div>
    </div>
    
    <div class="estrelas">★★★★★</div>
    <div class="titulo-avaliacao">SUA OPINIÃO IMPORTA DEMAIS!</div>
    
    <div class="texto-avaliacao">
      Como estamos iniciando na Amazon, sua avaliação sincera é o nosso maior presente. Se o produto atendeu às suas expectativas, pedimos que deixe sua avaliação com fotos e fale sobre sua experiência. Seu feedback ajuda outros clientes a conhecerem a qualidade J&F Co.!
    </div>
    
    <div class="linha-divisoria"></div>
    
    <div class="bloco-rodape">
      <div class="texto-suporte">
        Dúvidas? Nos chame no atendimento ao cliente da Amazon. Estamos aqui para lhe entregar a melhor experiência possível!
      </div>
    </div>
    
    <div class="assinatura-maite">Com carinho, Maitê 🖤</div>
  </div>
</body>
</html>
"""

def gerar_cartao_amazon_brinde():
    print("[CARTAO AMAZON BRINDE] Montando layout 'QUER UM CHAMEGO?' com brinde especial...")
    html_content = montar_html_amazon_brinde()
    temp_html = RAIZ / "scratch" / "cartao_amazon_brinde_temp.html"
    temp_html.write_text(html_content, encoding="utf-8")

    pdf_bruto = DOWNLOADS_DIR / "cartao_agradecimento_amazon_brinde_10x15_teste.pdf"

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto(f"file:///{str(temp_html).replace(os.sep, '/')}", wait_until="load")
        page.emulate_media(media="print")
        page.pdf(
            path=str(pdf_bruto),
            width="100mm",
            height="150mm",
            print_background=True,
            prefer_css_page_size=True,
            margin={"top": "0", "bottom": "0", "left": "0", "right": "0"}
        )
        browser.close()

    print(f"[CARTAO AMAZON BRINDE] PDF gerado: {pdf_bruto}")

    # Normalizar para termica
    sys.path.insert(0, str(RAIZ))
    import core_etiqueta_normalizar as norm
    pdf_norm = DOWNLOADS_DIR / "cartao_agradecimento_amazon_brinde_10x15_norm.pdf"
    norm.normalizar_10x15(str(pdf_bruto), str(pdf_norm), forcar=True)

    # Preview PNG
    doc = fitz.open(str(pdf_norm))
    page = doc[0]
    pix = page.get_pixmap(dpi=150)
    preview_png = DOWNLOADS_DIR / "cartao_agradecimento_amazon_brinde_preview.png"
    pix.save(str(preview_png))
    print(f"[CARTAO AMAZON BRINDE] Preview salvo em: {preview_png}")

if __name__ == "__main__":
    gerar_cartao_amazon_brinde()
