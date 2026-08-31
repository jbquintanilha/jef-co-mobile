# ==============================================================================
# NOME DO SCRIPT: core_scanner_card.py
# DESCRICAO: Renderizador universal de Ficha e Card Visual do Scanner J&F Co.
# FUNCAO: Compartilha a interface rica do Scanner (pages/14) com a Fase 6 da
#         Esteira de Expedicao (pages/17), mantendo fotos, cores, kits, volumes
#         e badges identicos em qualquer tela.
# STATUS: ATIVO
# VERSAO: 1.0
# DATA: 29/08/2026
# AUTOR: Violino (000) / Gemini CLI
# ==============================================================================
"""Renderizador visual reutilizável de pedidos bipados.

Suporta:
- Badges de canal (Shopee, Mercado Livre, TikTok, Correios)
- Foto da variação vendida em alta definição
- Destaque por cor / gênero
- Box de especificações (Tamanho, Cor, Kit, Volumes)
- Alerta visual para pedidos multi-itens (mesma etiqueta, vários produtos)
- Alerta visual para pedidos cancelados (não despachar)
"""

from __future__ import annotations

import re
import streamlit as st


def _render_html(html_str: str) -> None:
    """Renderiza HTML no Streamlit sem risco de ser interpretado como código Markdown."""
    if not html_str:
        return
    linhas = [re.sub(r"^\s+", "", l) for l in html_str.strip().splitlines() if l.strip()]
    st.markdown("".join(linhas), unsafe_allow_html=True)


def injetar_css_scanner() -> None:
    """Injeta os estilos CSS modernos do Scanner de Conferência."""
    st.markdown(
        """
        <style>
        .scanner-card-ok {
            background: linear-gradient(145deg, #064e3b 0%, #022c22 100%);
            border: 2px solid #10b981;
            border-radius: 14px;
            padding: 18px 20px;
            box-shadow: 0 10px 25px -5px rgba(0, 0, 0, 0.5), 0 0 15px rgba(16, 185, 129, 0.2);
            margin-bottom: 12px;
        }
        .scanner-card-erro {
            background: linear-gradient(145deg, #450a0a 0%, #2a0404 100%);
            border: 2px solid #ef4444;
            border-radius: 14px;
            padding: 18px 20px;
            box-shadow: 0 10px 25px -5px rgba(0, 0, 0, 0.5), 0 0 15px rgba(239, 68, 68, 0.25);
            margin-bottom: 12px;
        }
        .scanner-card-cancelado {
            background: linear-gradient(145deg, #7f1d1d 0%, #450a0a 100%);
            border: 3px solid #f87171;
            border-radius: 14px;
            padding: 18px 20px;
            box-shadow: 0 0 28px rgba(239, 68, 68, 0.5);
            margin-bottom: 12px;
            animation: scanner-pulso 1.2s ease-in-out infinite;
        }
        @keyframes scanner-pulso {
            0%, 100% { box-shadow: 0 0 12px rgba(239, 68, 68, 0.35); }
            50%      { box-shadow: 0 0 32px rgba(239, 68, 68, 0.85); }
        }
        .scanner-titulo-cancelado { font-size: 20px; font-weight: 900; color: #ffffff; letter-spacing: .5px; }
        .scanner-status-cancelado {
            display: inline-block; padding: 4px 14px; border-radius: 20px;
            background-color: #ef4444; color: #ffffff; font-weight: 800; font-size: 13px;
            box-shadow: 0 2px 6px rgba(0, 0, 0, 0.3);
        }
        .scanner-aviso-cancelado {
            margin-top: 14px; padding: 12px 14px; border-radius: 10px;
            background-color: #450a0a; border: 1.5px solid #ef4444; color: #fecaca; font-size: 14px;
        }
        .scanner-titulo { font-size: 19px; font-weight: 800; color: #f8fafc; letter-spacing: .5px; }
        .scanner-label { color: #94a3b8; font-size: 11px; font-weight: 700; text-transform: uppercase; letter-spacing: .6px; margin-bottom: 2px; }
        
        .scanner-grid-especs {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(110px, 1fr));
            gap: 8px;
            margin: 14px 0 10px 0;
        }
        .scanner-box-spec {
            border-radius: 10px;
            padding: 10px 12px;
            text-align: center;
            display: flex;
            flex-direction: column;
            justify-content: center;
        }
        .scanner-meta-panel {
            background: rgba(15, 23, 42, 0.75);
            border: 1px solid rgba(255, 255, 255, 0.1);
            border-radius: 10px;
            padding: 12px 16px;
            margin-top: 14px;
        }
        .scanner-meta-row {
            display: flex;
            justify-content: space-between;
            align-items: center;
            padding: 6px 0;
            border-bottom: 1px solid rgba(255, 255, 255, 0.05);
            font-size: 13.5px;
        }
        .scanner-meta-row:last-child {
            border-bottom: none;
        }
        .scanner-meta-lbl {
            color: #94a3b8;
            font-size: 12px;
            font-weight: 600;
            display: flex;
            align-items: center;
            gap: 6px;
        }
        .scanner-meta-val {
            color: #f1f5f9;
            font-weight: 600;
            text-align: right;
        }
        .scanner-tag {
            display: inline-flex; align-items: center; gap: 5px;
            padding: 4px 12px; border-radius: 20px;
            font-size: 12px; font-weight: 800; letter-spacing: .4px;
            box-shadow: 0 2px 5px rgba(0, 0, 0, 0.25);
        }
        .scanner-tag-ml { background: linear-gradient(135deg, #ffe600, #facc15); color: #1e293b; border: 1px solid #eab308; }
        .scanner-tag-shopee { background: linear-gradient(135deg, #ee4d2d, #ff5722); color: #ffffff; border: 1px solid #ea580c; }
        .scanner-tag-tiktok { background: linear-gradient(135deg, #09090b, #18181b); color: #00f2fe; border: 1.5px solid #00f2fe; box-shadow: 0 0 10px rgba(0, 242, 254, 0.35); }
        .scanner-tag-correios { background: linear-gradient(135deg, #1d4ed8, #2563eb); color: #ffffff; border: 1px solid #3b82f6; }
        .scanner-tag-manual { background: linear-gradient(135deg, #475569, #64748b); color: #ffffff; }
        </style>
        """,
        unsafe_allow_html=True,
    )


def tag_canal(canal: str) -> str:
    """HTML do badge do canal."""
    c = (canal or "").lower()
    if "mercado" in c:
        return '<span class="scanner-tag scanner-tag-ml">🤝 MERCADO LIVRE</span>'
    if "shopee" in c:
        return '<span class="scanner-tag scanner-tag-shopee">🛍️ SHOPEE</span>'
    if "tiktok" in c:
        return '<span class="scanner-tag scanner-tag-tiktok">🎵 TIKTOK SHOP</span>'
    if "correio" in c:
        return '<span class="scanner-tag scanner-tag-correios">📮 CORREIOS</span>'
    return f'<span class="scanner-tag scanner-tag-manual">📦 {str(canal or "MANUAL").upper()}</span>'


def mascarar_cep(cep: str) -> str:
    """Mascara o CEP mantendo os 2 ultimos digitos (ex: ******78)."""
    if not cep:
        return "—"
    digitos = "".join(ch for ch in str(cep) if ch.isdigit())
    if len(digitos) < 2:
        return "******"
    return "*" * max(1, len(digitos) - 2) + digitos[-2:]


def render_ficha_pedido(res: dict, *, ja_conferido: bool = False) -> None:
    """Renderiza a ficha visual completa do pedido."""
    injetar_css_scanner()
    badge = tag_canal(res.get("canal") or "")
    primeiro_nome = (res.get("cliente") or "").strip().split(" ")[0] or "—"

    if res.get("cancelado"):
        canal_nome = res.get("canal") or "plataforma"
        cancelado_em = res.get("cancelado_em") or "—"
        if cancelado_em != "—":
            try:
                import datetime as _dt_cancel
                cancelado_em = _dt_cancel.datetime.fromisoformat(cancelado_em).strftime("%d/%m/%Y %H:%M")
            except (ValueError, TypeError):
                pass
        
        card_cancel_html = f"""
        <div class="scanner-card-cancelado">
            <div style="display:flex; justify-content:space-between; align-items:center; flex-wrap:wrap; gap:8px;">
                <span class="scanner-titulo-cancelado">🚨 PEDIDO CANCELADO — NÃO ENVIAR</span>
                {badge}
            </div>
            <div class="scanner-aviso-cancelado">
                ⚠️ Este pedido foi <b>CANCELADO</b> pelo comprador ou marketplace. <b>NÃO EMBALAR / NÃO DESPACHAR.</b>
            </div>
            <div class="scanner-meta-panel" style="background:rgba(0,0,0,0.4); border-color:rgba(239,68,68,0.3);">
                <div class="scanner-meta-row">
                    <span class="scanner-meta-lbl">📦 Produto</span>
                    <span class="scanner-meta-val">{res.get('produto') or res.get('modelo') or '—'}</span>
                </div>
                <div class="scanner-meta-row">
                    <span class="scanner-meta-lbl">🏷️ SKU</span>
                    <span class="scanner-meta-val"><code>{res.get('sku') or '—'}</code></span>
                </div>
                <div class="scanner-meta-row">
                    <span class="scanner-meta-lbl">👤 Cliente</span>
                    <span class="scanner-meta-val">{primeiro_nome}</span>
                </div>
                <div class="scanner-meta-row">
                    <span class="scanner-meta-lbl">📍 CEP</span>
                    <span class="scanner-meta-val">{mascarar_cep(res.get('cep'))}</span>
                </div>
                <div class="scanner-meta-row">
                    <span class="scanner-meta-lbl">⏰ Cancelado em</span>
                    <span class="scanner-meta-val" style="color:#fca5a5;">{cancelado_em}</span>
                </div>
                <div class="scanner-meta-row">
                    <span class="scanner-meta-lbl">📊 Status</span>
                    <span class="scanner-meta-val"><span class="scanner-status-cancelado">CANCELADO ({canal_nome.upper()})</span></span>
                </div>
            </div>
        </div>
        """
        _render_html(card_cancel_html)
        return

    if ja_conferido or res.get("conferido_hoje"):
        st.warning("⚠️ Este tracking JÁ FOI CONFERIDO hoje. Confira se não é caixa repetida.")

    _fem = res.get("genero") == "fem"
    cor_destaque = "#f472b6" if _fem else "#34d399"
    borda_destaque = "#9d174d" if _fem else "#059669"
    fundo_destaque = "#2a0a1c" if _fem else "#064e3b"

    modelo = res.get("modelo") or res.get("produto") or "—"
    spu = res.get("spu") or ""

    _img_ficha = res.get("imagem_url") or ""
    _bloco_img = (
        f'<div style="width:105px; height:105px; border-radius:12px; overflow:hidden; border:2px solid {borda_destaque}; flex-shrink:0; box-shadow:0 4px 10px rgba(0,0,0,0.4);">'
        f'<img src="{_img_ficha}" alt="Foto do produto" style="width:100%; height:100%; object-fit:cover;">'
        f'</div>'
        if _img_ficha else
        f'<div style="width:105px; height:105px; border-radius:12px; background:#0f172a; border:2px solid {borda_destaque}; display:flex; align-items:center; justify-content:center; font-size:36px; flex-shrink:0;">📦</div>'
    )

    _itens_ped = res.get("itens") or []
    _volumes = sum(int(i.get("quantidade") or 1) for i in _itens_ped) or 1
    
    _card_volumes_html = ""
    if _volumes > 1:
        _card_volumes_html = f"""
        <div class="scanner-box-spec" style="background:#450a0a; border:2.5px solid #ef4444;">
            <div class="scanner-label" style="color:#fca5a5;">Volumes</div>
            <div style="font-size:28px; font-weight:900; color:#fecaca; line-height:1.1;">{_volumes}x</div>
        </div>
        """

    _bloco_itens_html = ""
    if _volumes > 1 and len(_itens_ped) <= 1:
        _it0 = _itens_ped[0] if _itens_ped else {}
        _bloco_itens_html = f"""
        <div style="background:#450a0a; border:2.5px solid #ef4444; border-radius:12px; padding:14px 16px; margin:12px 0;">
            <div style="font-size:22px; font-weight:900; color:#fca5a5; display:flex; align-items:center; gap:8px;">
                ⚠️ {_volumes} UNIDADES DO MESMO KIT
            </div>
            <div style="font-size:14px; color:#fecaca; margin-top:6px;">
                O cliente comprou <b>{_volumes}x</b> {_it0.get('sku') or res.get('sku') or ''}.
                Coloque <b>{_volumes} kits</b> na caixa, não um só.
            </div>
        </div>
        """
    elif len(_itens_ped) > 1:
        _linhas = []
        for _i, _it in enumerate(_itens_ped, 1):
            _im = _it.get("imagem_url") or ""
            _var = _it.get("variacao") or _it.get("cor") or ""
            _qtd = int(_it.get("quantidade") or 1)
            _thumb = (
                f'<img src="{_im}" style="width:48px;height:48px;object-fit:cover;border-radius:8px;flex-shrink:0;">'
                if _im else
                '<div style="width:48px;height:48px;border-radius:8px;background:#1e293b;display:flex;align-items:center;justify-content:center;font-size:18px;flex-shrink:0;">📦</div>'
            )
            _linhas.append(
                f'<div style="display:flex;gap:10px;align-items:center;padding:8px 10px;background:rgba(0,0,0,.3);border-radius:8px;margin-bottom:6px;">'
                f'{_thumb}'
                f'<div style="min-width:0;flex:1;">'
                f'<div style="font-size:14px;font-weight:700;color:#fecaca;">'
                f'{_i}. {_it.get("sku") or "—"}'
                + (f' · {_var}' if _var else '')
                + (f' · <b>{_qtd}x</b>' if _qtd > 1 else '')
                + '</div>'
                f'<div style="font-size:12px;color:#cbd5e1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">'
                f'{(_it.get("nome") or "")[:65]}</div>'
                f'</div></div>'
            )
        _total_un = sum(int(i.get("quantidade") or 1) for i in _itens_ped)
        _bloco_itens_html = f"""
        <div style="background:#450a0a; border:2.5px solid #ef4444; border-radius:12px; padding:14px 16px; margin:12px 0;">
            <div style="font-size:19px; font-weight:900; color:#fca5a5; margin-bottom:4px;">
                ⚠️ PEDIDO COM {len(_itens_ped)} ITENS — SEPARE TODOS
            </div>
            <div style="font-size:13px; color:#fecaca; margin-bottom:10px;">
                Mesma etiqueta, <b>{_total_un} volume(s)</b> no pacote. Confira item por item.
            </div>
            {''.join(_linhas)}
        </div>
        """

    card_sucesso_html = f"""
    <div class="scanner-card-ok">
        <div style="display:flex; justify-content:space-between; align-items:center; flex-wrap:wrap; gap:8px;">
            <span class="scanner-titulo">🟢 SEPARAR ESTE PEDIDO</span>
            {badge}
        </div>
        {_bloco_itens_html}
        <div style="display:flex; gap:12px; align-items:center; margin:14px 0 10px; flex-wrap:wrap;">
            {_bloco_img}
            <div style="flex:1; min-width:180px; background:{fundo_destaque}; border:1.5px solid {borda_destaque}; border-radius:12px; padding:12px 16px;">
                <div class="scanner-label">Produto</div>
                <div style="font-size:22px; font-weight:900; color:{cor_destaque}; line-height:1.2;">{modelo}</div>
                <div style="font-size:13px; color:#cbd5e1; margin-top:4px;">
                    SPU <b style="color:#f8fafc;">{spu or '—'}</b>
                </div>
            </div>
        </div>
        <div class="scanner-grid-especs">
            <div class="scanner-box-spec" style="background:{fundo_destaque}; border:1.5px solid {borda_destaque};">
                <div class="scanner-label">Tamanho</div>
                <div style="font-size:24px; font-weight:900; color:{cor_destaque}; line-height:1.2;">{res.get('tamanho') or '—'}</div>
            </div>
            <div class="scanner-box-spec" style="background:{fundo_destaque}; border:1.5px solid {borda_destaque};">
                <div class="scanner-label">Cor</div>
                <div style="font-size:22px; font-weight:900; color:{cor_destaque}; line-height:1.2;">{res.get('cor') or '—'}</div>
            </div>
            <div class="scanner-box-spec" style="background:{fundo_destaque}; border:1.5px solid {borda_destaque};">
                <div class="scanner-label">Kit</div>
                <div style="font-size:24px; font-weight:900; color:{cor_destaque}; line-height:1.2;">{res.get('kit') or 'Unitário'}</div>
            </div>
            {_card_volumes_html}
        </div>
        <div class="scanner-meta-panel">
            <div class="scanner-meta-row">
                <span class="scanner-meta-lbl">🏷️ SKU Principal</span>
                <span class="scanner-meta-val"><code>{res.get('sku') or '—'}</code></span>
            </div>
            <div class="scanner-meta-row">
                <span class="scanner-meta-lbl">👤 Cliente</span>
                <span class="scanner-meta-val">{primeiro_nome}</span>
            </div>
            <div class="scanner-meta-row">
                <span class="scanner-meta-lbl">📍 CEP Destino</span>
                <span class="scanner-meta-val">{mascarar_cep(res.get('cep'))}</span>
            </div>
            <div class="scanner-meta-row">
                <span class="scanner-meta-lbl">🚚 Rastreio</span>
                <span class="scanner-meta-val"><code>{res.get('tracking') or '—'}</code></span>
            </div>
            <div class="scanner-meta-row">
                <span class="scanner-meta-lbl">🛒 Pedido E-commerce</span>
                <span class="scanner-meta-val">{res.get('pedido_ecommerce') or '—'}</span>
            </div>
        </div>
    </div>
    """
    _render_html(card_sucesso_html)
