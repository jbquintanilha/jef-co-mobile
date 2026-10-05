# ==============================================================================
# NOME DO SCRIPT: core_bipagem_ui.py
# DESCRICAO: Interface do app dedicado de bipagem (app_bipagem.py): CSS de tela
#            cheia, ficha compacta, selos de canal e script de tela acesa.
# STATUS: ATIVO
# VERSAO: 1.0
# DATA: 04/10/2026
# AUTOR: Terminador (001) / Claude
# ==============================================================================
"""Interface do app dedicado de bipagem.

A ficha compacta (`ficha_compacta_html`) e o CSS `.fc-*` foram COPIADOS da pagina
`pages/14_Scanner_Conferencia.py` (modo "Camera rapida", aprovado pelo Jota em
28/09, revisado por DeepSeek + Gemini). A pagina Scanner nao e' alterada por
este modulo.
"""

from __future__ import annotations

import re

import streamlit as st
import streamlit.components.v1 as components


def render_html(html_str: str) -> None:
    """HTML no Streamlit sem virar bloco de codigo Markdown."""
    if not html_str:
        return
    linhas = [re.sub(r"^\s+", "", l) for l in html_str.strip().splitlines() if l.strip()]
    st.markdown("".join(linhas), unsafe_allow_html=True)


def mascarar_cep(cep: str) -> str:
    if not cep:
        return "—"
    digitos = "".join(ch for ch in str(cep) if ch.isdigit())
    if len(digitos) < 2:
        return "******"
    return "*" * max(1, len(digitos) - 2) + digitos[-2:]


def tag_canal(canal: str) -> str:
    """Selo do canal (inclui Amazon, que a pagina Scanner ja' tinha)."""
    c = (canal or "").lower()
    if "mercado" in c:
        return '<span class="scanner-tag scanner-tag-ml">🤝 MERCADO LIVRE</span>'
    if "shopee" in c:
        return '<span class="scanner-tag scanner-tag-shopee">🛍️ SHOPEE</span>'
    if "tiktok" in c:
        return '<span class="scanner-tag scanner-tag-tiktok">🎵 TIKTOK SHOP</span>'
    if "correio" in c:
        return '<span class="scanner-tag scanner-tag-correios">📮 CORREIOS</span>'
    if "amazon" in c:
        return '<span class="scanner-tag scanner-tag-amazon">📦 AMAZON</span>'
    return f'<span class="scanner-tag scanner-tag-manual">📦 {str(canal or "MANUAL").upper()}</span>'


# CSS do app dedicado: tela cheia, sem menu/cabecalho do Streamlit, fundo escuro,
# selos de canal e cartoes de estado.
CSS = """
<style>
/* ---- tela cheia: some o chrome do Streamlit ---- */
#MainMenu, header[data-testid="stHeader"], footer, [data-testid="stToolbar"],
[data-testid="stDecoration"], [data-testid="stStatusWidget"] { display: none !important; }
html, body, .stApp, [data-testid="stAppViewContainer"] { background: #0b1220 !important; }
.block-container { padding: 6px 10px 90px !important; max-width: 560px !important; }
h1, h2, h3 { margin: 0 !important; }
[data-testid="stVerticalBlock"] { gap: 0.45rem !important; }
.bip-topo { display:flex; justify-content:space-between; align-items:center;
    color:#e2e8f0; font-weight:800; font-size:14px; padding:2px 2px 0; }
.bip-topo small { color:#94a3b8; font-weight:600; }
/* ---- selos de canal ---- */
.scanner-tag { display:inline-block; padding:3px 10px; border-radius:999px; font-size:11px; font-weight:900; letter-spacing:.3px; }
.scanner-tag-ml { background: linear-gradient(135deg, #ffe600, #facc15); color: #1e293b; }
.scanner-tag-shopee { background: linear-gradient(135deg, #ee4d2d, #ff5722); color: #ffffff; }
.scanner-tag-tiktok { background: linear-gradient(135deg, #09090b, #18181b); color: #00f2fe; border: 1.5px solid #00f2fe; }
.scanner-tag-correios { background: linear-gradient(135deg, #1d4ed8, #2563eb); color: #ffffff; }
.scanner-tag-manual { background: linear-gradient(135deg, #475569, #64748b); color: #ffffff; }
.scanner-tag-amazon { background: linear-gradient(135deg, #232f3e, #131a22); color: #ff9900; border: 1.5px solid #ff9900; }
/* ---- cartoes de estado ---- */
.bip-cancelado { background: linear-gradient(145deg,#7f1d1d,#450a0a); border:3px solid #ef4444;
    border-radius:12px; padding:12px; color:#fee2e2; }
.bip-cancelado .t { font-size:20px; font-weight:900; }
.bip-erro { background:#450a0a; border:2px solid #ef4444; border-radius:12px; padding:12px; color:#fecaca; }
.bip-invalido { background:#431407; border:2px solid #ea580c; border-radius:12px; padding:12px; color:#fed7aa; }
.bip-erro .t, .bip-invalido .t { font-size:17px; font-weight:900; }
.bip-erro code, .bip-invalido code, .bip-cancelado code { background:rgba(0,0,0,.35); padding:1px 5px; border-radius:4px; }
.bip-aguardando { color:#94a3b8; text-align:center; padding:6px; font-size:13px; }
/* ---- botoes grandes (zona do polegar) ---- */
.stButton > button, .stFormSubmitButton > button { min-height: 52px; font-size: 16px; font-weight: 800; }
[data-testid="stForm"] { padding: 6px 8px !important; border-color:#1e293b !important; }
</style>
"""

# Bloco `.fc-*` abaixo: copiado da pagina Scanner (linhas 183-214).
FC_CSS = """
<style>
    /* ---- Ficha COMPACTA (modo "⚡ Câmera rápida") -- cabe no celular sem rolar.
       Revisado por DeepSeek + Gemini 3.8 (Sala de Guerra, 28/09). */
    .fc-card { background: linear-gradient(145deg, #064e3b 0%, #022c22 100%);
        border: 2px solid #10b981; border-radius: 12px; padding: 10px 12px; margin-bottom: 8px; }
    .fc-topo { display:flex; justify-content:space-between; align-items:center; gap:6px; }
    .fc-titulo { font-size: 16px; font-weight: 900; color: #f8fafc; }
    .fc-avisos { display:flex; flex-wrap:wrap; gap:4px; margin-top:6px; }
    .fc-aviso { font-size: 11px; font-weight: 800; padding: 2px 8px; border-radius: 10px; }
    .fc-prod { display:flex; gap:10px; align-items:center; margin-top:8px; }
    .fc-foto { width:64px; height:64px; border-radius:10px; object-fit:cover; flex-shrink:0; }
    .fc-nome { font-size: 16px; font-weight: 900; line-height:1.2;
        display:-webkit-box; -webkit-line-clamp:2; -webkit-box-orient:vertical; overflow:hidden; }
    .fc-qtd { font-size: 34px; font-weight: 900; line-height:1; flex-shrink:0; margin-left:auto; }
    .fc-chips { display:grid; grid-template-columns: repeat(3, 1fr); gap:6px; margin-top:8px; }
    .fc-chip { border-radius:8px; padding:5px 8px; }
    .fc-chip-lbl { font-size:10px; font-weight:700; color:#94a3b8; text-transform:uppercase; }
    .fc-chip-val { font-size:18px; font-weight:900; line-height:1.15;
        display:block; overflow:hidden; white-space:nowrap; text-overflow:ellipsis; }
    .fc-swatch { display:inline-block; width:14px; height:14px; border-radius:3px;
        border:1px solid #cbd5e1; margin-right:5px; vertical-align:-2px; }
    .fc-itens { max-height: 150px; overflow-y:auto; margin-top:8px; background:#450a0a;
        border:2px solid #ef4444; border-radius:10px; padding:6px 8px; }
    .fc-itens-tit { font-size:13px; font-weight:900; color:#fca5a5; margin-bottom:4px; }
    .fc-item { display:flex; gap:8px; align-items:center; padding:3px 0; font-size:13px; color:#fecaca; }
    .fc-item img, .fc-item .fc-sem-foto { width:36px; height:36px; border-radius:6px; object-fit:cover; flex-shrink:0; }
    .fc-meta { font-size:12px; color:#cbd5e1; margin-top:6px; }
    .fc-meta code { font-size:12px; }
    /* Botoes no rodape, na zona do polegar. A classe vem do key do container
       (`st.container(key="rodape_conferir")` -> `.st-key-rodape_conferir`). */
    .st-key-rodape_conferir { position: sticky; bottom: 0; z-index: 50; background: #0b1220;
        padding: 8px 0 calc(8px + env(safe-area-inset-bottom)); border-top: 1px solid #1e293b; }
    .st-key-rodape_conferir button { min-height: 56px; font-size: 16px; font-weight: 800; }
</style>
"""


def injetar_css() -> None:
    st.markdown(CSS, unsafe_allow_html=True)
    st.markdown(FC_CSS, unsafe_allow_html=True)


# Tela sempre acesa + modo "app": o Wake Lock mantem a tela ligada enquanto a aba
# estiver visivel (re-pede ao voltar do segundo plano) e as meta tags pedem modo de
# app ao navegador. O componente roda num iframe do MESMO dominio, entao consegue
# mexer no <head> da pagina pai (mesma tecnica do leitor de camera). O pedido de
# tela cheia exige um gesto do usuario: acontece no 1o toque na tela.
# Brave/Chrome Android: Wake Lock exige HTTPS; a instalacao como app depende do
# navegador (no Brave pode abrir com a barra) -- ver planejamento.
_JS_TELA_ACESA = """
<script>
(function () {
  const doc = window.parent.document;
  try {
    const metas = {
      'theme-color': '#0b1220',
      'mobile-web-app-capable': 'yes',
      'apple-mobile-web-app-capable': 'yes',
      'apple-mobile-web-app-status-bar-style': 'black-translucent',
      'apple-mobile-web-app-title': 'Bipagem J&F'
    };
    for (const [n, c] of Object.entries(metas)) {
      let m = doc.querySelector('meta[name="' + n + '"]');
      if (!m) { m = doc.createElement('meta'); m.name = n; doc.head.appendChild(m); }
      m.content = c;
    }
  } catch (e) {}
  let lock = null;
  async function pedir() {
    try {
      if ('wakeLock' in window.parent.navigator && doc.visibilityState === 'visible') {
        lock = await window.parent.navigator.wakeLock.request('screen');
      }
    } catch (e) {}
  }
  pedir();
  doc.addEventListener('visibilitychange', function () {
    if (doc.visibilityState === 'visible') pedir();
  });
  let feito = false;
  doc.addEventListener('click', function () {
    if (feito) return;
    feito = true;
    try {
      const el = doc.documentElement;
      if (el.requestFullscreen && !doc.fullscreenElement) {
        el.requestFullscreen({ navigationUI: 'hide' });
      }
    } catch (e) {}
  });
})();
</script>
"""


def injetar_tela_acesa() -> None:
    components.html(_JS_TELA_ACESA, height=0)


# --- Ficha compacta (copiada de pages/14_Scanner_Conferencia.py) ---------------
# Cor -> amostra visual na ficha compacta (erro de cor e' o erro nº1 de
# separacao -- revisao do DeepSeek, 28/09). Cor desconhecida: sem amostra.
_SWATCH_COR = {
    "preto": "#111111", "preta": "#111111", "branco": "#ffffff", "branca": "#ffffff",
    "cinza": "#9ca3af", "azul marinho": "#1e3a8a", "marinho": "#1e3a8a",
    "azul": "#2563eb", "rosa": "#f472b6", "bege": "#d6c3a3", "nude": "#e0bfa6",
    "vermelho": "#dc2626", "verde": "#16a34a", "amarelo": "#facc15",
    "chocolate": "#5b3a29", "marrom": "#6b4226", "vinho": "#7f1d1d", "rubi": "#9b111e",
}


def _swatch(cor: str) -> str:
    c = (cor or "").strip().lower()
    hexa = _SWATCH_COR.get(c) or next(
        (v for k, v in _SWATCH_COR.items() if k in c), "")
    return f'<span class="fc-swatch" style="background:{hexa};"></span>' if hexa else ""


def ficha_compacta_html(res: dict, *, badge: str, primeiro_nome: str,
                         conferido: bool, modelo: str, itens: list, volumes: int,
                         img: str, cor_destaque: str, borda: str, fundo: str) -> str:
    """Ficha do pedido no modo "⚡ Câmera rápida": tudo que importa pra embalar
    cabe na tela do celular, sem rolar (pedido do Jota, 28/09).

    Template PROPRIO (nao override do card grande) -- revisao DeepSeek +
    Gemini: override com !important sobre style inline quebra a cada versao do
    Streamlit. Mesmos dados ja' calculados pela pagina; so' o HTML muda.

    Mantem: produto, tamanho, cor (com amostra), kit, quantidade grande, lista
    de itens (rolagem interna se for longa), avisos como etiquetas, 1o nome do
    cliente (decisao do Jota 03/08: casa caixa com etiqueta) e o FINAL do
    rastreio (unica defesa se a camera ler a etiqueta de outra caixa).
    Tira: barcode de comando (so' serve pra pistola), CEP, e o aviso generico.

    Todo texto vindo de fora (nome de cliente/afiliado, produto, SKU, URL da
    foto) passa por `_e()` -- vai para `unsafe_allow_html` (revisao DeepSeek).
    """
    import html as _html

    def _e(v) -> str:
        return _html.escape(str(v or ""), quote=True)

    avisos = []
    if conferido:
        avisos.append(("⚠️ JÁ CONFERIDO HOJE", "#78350f", "#fde68a"))
    if res.get("status_pedido") == "NAO_VERIFICADO":
        avisos.append(("⚠️ STATUS NÃO VERIFICADO", "#78350f", "#fde68a"))
    if res.get("is_sample"):
        avisos.append(("🎁 AMOSTRA CRIADOR", "#3b0764", "#e9d5ff"))
    if res.get("is_affiliate") and not res.get("is_sample"):
        nome_af = (res.get("afiliado_nome") or "").strip()
        avisos.append((f"🎯 AFILIADO{': ' + _e(nome_af[:18]) if nome_af else ''}",
                       "#451a03", "#fde68a"))
    avisos_html = ""
    if avisos:
        avisos_html = '<div class="fc-avisos">' + "".join(
            f'<span class="fc-aviso" style="background:{bg}; color:{fg};">{t}</span>'
            for t, bg, fg in avisos) + "</div>"

    foto = (f'<img class="fc-foto" src="{_e(img)}" style="border:2px solid {borda};">' if img
            else f'<div class="fc-foto" style="background:#0f172a; border:2px solid {borda};'
                 f' display:flex; align-items:center; justify-content:center; font-size:26px;">📦</div>')

    # Quantidade: o erro mais caro da bancada e' a caixa sair com 1 kit quando
    # eram 2. Numero grande, lido de braco estendido.
    multi = len(itens) > 1
    qtd_txt = f"{len(itens)} itens" if multi else f"{volumes}x"
    qtd_cor = "#fca5a5" if (multi or volumes > 1) else cor_destaque

    itens_html = ""
    if multi:
        linhas = []
        for i, it in enumerate(itens, 1):
            im = it.get("imagem_url") or ""
            thumb = (f'<img src="{_e(im)}">' if im else
                     '<div class="fc-sem-foto" style="background:#1e293b; display:flex;'
                     ' align-items:center; justify-content:center;">📦</div>')
            q = int(it.get("quantidade") or 1)
            var = it.get("variacao") or it.get("cor") or ""
            linhas.append(
                f'<div class="fc-item">{thumb}<div><b>{i}. {q}x {_e(it.get("sku") or "—")}</b>'
                + (f' · {_e(var)}' if var else '') + '</div></div>')
        itens_html = (f'<div class="fc-itens"><div class="fc-itens-tit">⚠️ SEPARE TODOS — '
                      f'{volumes} volume(s)</div>{"".join(linhas)}</div>')

    cor = res.get("cor") or "—"

    def _chip(lbl: str, val: str) -> str:
        return (f'<div class="fc-chip" style="background:{fundo}; border:1.5px solid {borda};">'
                f'<div class="fc-chip-lbl">{lbl}</div>'
                f'<div class="fc-chip-val" style="color:{cor_destaque};">{val}</div></div>')

    chips = (_chip("Tamanho", _e(res.get("tamanho") or "—"))
             + _chip("Cor", _swatch(cor) + _e(cor))
             + _chip("Kit", _e(res.get("kit") or "Unitário")))

    rastreio = str(res.get("tracking") or "")
    fim_rastreio = f"…{rastreio[-6:]}" if len(rastreio) > 6 else (rastreio or "—")

    return f"""
    <div class="fc-card">
        <div class="fc-topo"><span class="fc-titulo">🟢 SEPARAR</span>{badge}</div>
        {avisos_html}
        <div class="fc-prod">
            {foto}
            <div class="fc-nome" style="color:{cor_destaque};">{_e(modelo)}</div>
            <div class="fc-qtd" style="color:{qtd_cor};">{qtd_txt}</div>
        </div>
        <div class="fc-chips">{chips}</div>
        {itens_html}
        <div class="fc-meta">👤 {_e(primeiro_nome)} · 🚚 <code>{_e(fim_rastreio)}</code>
            · SKU <code>{_e(res.get('sku') or '—')}</code></div>
    </div>
    """
