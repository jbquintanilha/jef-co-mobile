# ==============================================================================
# NOME DO SCRIPT: app_bipagem.py
# DESCRICAO: App DEDICADO de bipagem (celular): camera + ficha compacta +
#            CONFERIDO/PULAR, em tela cheia e sem menu. URL propria.
# STATUS: ATIVO
# VERSAO: 1.0
# DATA: 04/10/2026
# AUTOR: Terminador (001) / Claude
# ==============================================================================
"""App dedicado de bipagem -- um Streamlit separado do dashboard.

Publicar como segundo app no Streamlit Cloud: repo `jef-co-mobile`, arquivo
principal `app_bipagem.py`, e copiar os mesmos secrets do app principal. Localmente:
`streamlit run app_bipagem.py`.

Reusa as mesmas regras do Scanner (modulos `core_scanner_*`): o que muda e' a
CASCA -- tela cheia, sem menu, uma tela so', botoes na zona do polegar.
O PIN e' o mesmo do app de expedicao (`PIN_EXPEDICAO`, padrao "2026").
"""

import os
import sys

import streamlit as st

_ROOT = os.path.dirname(os.path.abspath(__file__))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

try:                                    # secrets da nuvem (so' existe no jef-co-mobile)
    import core_env_loader  # noqa: F401
except ImportError:
    pass

import streamlit.components.v1 as components

import core_bipagem_fluxo as fluxo
import core_bipagem_ui as ui
import core_scanner_auditoria as auditoria
import core_scanner_db as db
import core_scanner_foco as foco
import core_scanner_som as som
import scanner_camera_ao_vivo as camera_ao_vivo

st.set_page_config(page_title="Bipagem J&F", page_icon="📦", layout="centered",
                   initial_sidebar_state="collapsed")
ui.injetar_css()

# --------------------------------------------------------------------------- #
# PIN
# --------------------------------------------------------------------------- #
SENHA = os.getenv("PIN_EXPEDICAO", "2026")
if "autenticado" not in st.session_state:
    st.session_state.autenticado = False

if not st.session_state.autenticado:
    ui.render_html(
        '<div style="text-align:center; color:#e2e8f0; padding:30px 0 6px;">'
        '<div style="font-size:42px;">📦</div>'
        '<div style="font-size:20px; font-weight:900;">Bipagem · J&amp;F Co.</div></div>')
    pin = st.text_input("PIN", type="password", max_chars=6, label_visibility="collapsed",
                        placeholder="PIN de segurança")
    if st.button("🔓 Entrar", use_container_width=True, type="primary"):
        if pin == SENHA:
            st.session_state.autenticado = True
            st.rerun()
        else:
            st.error("PIN incorreto.")
    st.stop()

ui.injetar_tela_acesa()
fluxo.iniciar_estado()

# Bip da leitura: consome o sinal deixado pelo fluxo (sai da sessao ao tocar, senao
# repetiria a cada rerun).
_sinal = st.session_state.pop("scanner_som", None)
if _sinal:
    som.tocar(_sinal)

res = st.session_state.scanner_resultado
codigo_atual = st.session_state.scanner_ultimo_codigo
tem_leitura = bool(codigo_atual)
stats = db.stats_dia()


@st.cache_data(ttl=15, show_spinner=False)
def _conferidos_hoje_nuvem() -> int:
    """Conferencias de hoje no Supabase (na nuvem o SQLite local nasce vazio)."""
    try:
        import core_scanner_supabase as cloud_db
        return int((cloud_db.obter_metricas_dia_nuvem() or {}).get("conferidos", 0))
    except Exception:
        return 0


_hoje = max(int(stats.get("conferidos_hoje", 0) or 0), _conferidos_hoje_nuvem())


@st.cache_data(ttl=30, show_spinner=False)
def _frescor_da_base() -> dict:
    """Por canal: (quantidade, 'dd/mm HH:MM' da ultima atualizacao em horario de Brasilia).

    A base e' a MESMA da Esteira e do bipador fisico: o PC monta (com o codigo J&T do
    TikTok vindo do Olist) e espelha no Supabase. Este app so' le.
    """
    import datetime as _dt
    try:
        import core_scanner_supabase as cloud_db
        linhas = cloud_db._requisicao_supabase(
            "GET", "rastreio_pedidos_expedicao",
            params={"select": "canal,atualizado_em", "order": "atualizado_em.desc",
                    "limit": "5000"}) or []
    except Exception:
        return {}
    por = {}
    for r in linhas if isinstance(linhas, list) else []:
        c = (r.get("canal") or "?").lower()
        n, ult = por.get(c, (0, None))
        try:
            quando = _dt.datetime.fromisoformat(str(r.get("atualizado_em")).replace("Z", "+00:00"))
        except ValueError:
            quando = None
        if quando and (ult is None or quando > ult):
            ult = quando
        por[c] = (n + 1, ult)
    brt = _dt.timezone(_dt.timedelta(hours=-3))
    return {c: (n, (u.astimezone(brt).strftime("%d/%m %H:%M") if u else "—"))
            for c, (n, u) in por.items()}

# --------------------------------------------------------------------------- #
# Topo compacto + ajustes
# --------------------------------------------------------------------------- #
ui.render_html(
    f'<div class="bip-topo"><span>📦 Bipagem</span>'
    f'<small>✅ {st.session_state.scanner_sessao_conferidos} nesta sessão · '
    f'hoje {_hoje}</small></div>')

_aviso = st.session_state.pop("bip_aviso_nuvem", None)
if _aviso:
    st.error(_aviso)

with st.expander("⚙️ Ajustes", expanded=bool(st.session_state.get("scanner_msg_sync"))):
    continuo = st.toggle(
        "Leitura contínua (sem tocar a cada etiqueta)", value=False, key="bip_continuo",
        help="Ligado: a câmera escaneia sozinha. Mais rápido, mas pode ler "
             "outra etiqueta se a mira passar perto.")
    _fr = _frescor_da_base()
    if _fr:
        st.caption("🗂️ Base compartilhada com a Esteira e o bipador físico:")
        for _c, (_n, _quando) in sorted(_fr.items()):
            st.caption(f"• {_c}: {_n} pedidos · atualizada {_quando}")
    else:
        st.caption("🗂️ Não consegui ler a base agora.")
    st.caption("Pedido novo não aparece? Atualize a fila no PC (Esteira → Atualizar fila) "
               "e toque em Recarregar.")
    if st.button("🔄 Recarregar", use_container_width=True):
        st.cache_data.clear()
        st.rerun()
    if st.button("🔒 Sair (bloquear)", use_container_width=True):
        st.session_state.autenticado = False
        st.rerun()

continuo = st.session_state.get("bip_continuo", False)

# --------------------------------------------------------------------------- #
# Alarme de divergencia (verificacao dobrada) -- fica na tela ate dar OK
# --------------------------------------------------------------------------- #
_divs = auditoria.listar_abertas()
_graves = [d for d in _divs if d.get("tipo") != "multi_item"]
if _graves:
    st.error(f"🚨 {len(_graves)} DIVERGÊNCIA(S) ENTRE O SCANNER E O MARKETPLACE")
    for _d in _graves:
        with st.container(border=True):
            st.markdown(f"**{_d.get('tracking')}** · `{(_d.get('canal') or '').upper()}`")
            st.warning(_d.get("detalhe") or "")
            if st.button("✅ OK, resolvido", key=f"okdiv_{_d['id']}", use_container_width=True):
                auditoria.dar_ok(_d["id"])
                st.rerun()

# --------------------------------------------------------------------------- #
# Camera + campo de codigo
# --------------------------------------------------------------------------- #
camera_ao_vivo.render_camera(altura=240, botao_submit="Resolver", rearmar=True,
                             continuo=continuo)

# O leitor de camera entrega o codigo preenchendo ESTE campo (procura o 1o campo
# de texto com "codigo" no rotulo) e clicando no botao "Resolver". Tem que ficar
# sempre na tela. Serve tambem para digitar e para a pistola Bluetooth.
with st.form("form_bipagem_dedicado", clear_on_submit=True):
    codigo_digitado = st.text_input(
        "Código da etiqueta", label_visibility="collapsed",
        placeholder="Ex: AP296430628BR  ou  260802B4MD9MHU", key="inp_bipagem_dedicado")
    if st.form_submit_button("🔍 Resolver", use_container_width=True):
        if codigo_digitado and codigo_digitado.strip():
            fluxo.processar_codigo(codigo_digitado)
            st.rerun()
foco.injetar_guarda_foco()

if tem_leitura:
    # Ancora: depois da leitura a pagina rola ate a ficha.
    st.markdown('<div id="ficha-produto"></div>', unsafe_allow_html=True)
    components.html(
        """<script>(function(){try{const a=window.parent.document.getElementById('ficha-produto');
        if(a){setTimeout(function(){a.scrollIntoView({behavior:'smooth',block:'start'});},120);}}
        catch(e){}})();</script>""", height=0)

_resumo_frescor = " · ".join(f"{c} {q}" for c, (_n, q) in sorted(_frescor_da_base().items())) or "indisponível"

# --------------------------------------------------------------------------- #
# Resultado
# --------------------------------------------------------------------------- #
if res and res.get("encontrado"):
    badge = ui.tag_canal(res.get("canal") or "")
    conferido = res.get("conferido_hoje", False)
    primeiro_nome = (res.get("cliente") or "").strip().split(" ")[0] or "—"

    # A verificacao de cancelamento roda em BACKGROUND: a ficha aparece na hora com o
    # dado local e este fragmento re-consulta a cada 1,5s ate o status chegar.
    if res.get("status_pendente"):
        import core_scanner_resolver as _rsv

        @st.fragment(run_every=1.5)
        def _aguardar_status():
            info = _rsv.status_em_cache(res.get("pedido_ecommerce") or "", res.get("canal") or "")
            if info is None:
                st.caption("🔄 Verificando cancelamento na plataforma…")
                return
            fluxo.processar_codigo(st.session_state.scanner_ultimo_codigo)
            st.rerun()

        _aguardar_status()

    if res.get("cancelado"):
        ui.render_html(
            f'<div class="bip-cancelado"><div class="t">🚨 PEDIDO CANCELADO — NÃO ENVIAR</div>'
            f'<div style="margin:6px 0;">{badge}</div>'
            f'<div>Produto: <b>{res.get("produto") or res.get("modelo") or "—"}</b></div>'
            f'<div>SKU: <code>{res.get("sku") or "—"}</code> · Cliente: {primeiro_nome}</div>'
            f'<div>CEP: {ui.mascarar_cep(res.get("cep"))}</div></div>')
        c1, c2 = st.columns(2)
        with c1:
            if st.button("⚠️ CONFERIDO (CANCELADO)", use_container_width=True, type="primary"):
                db.registrar_conferencia(res.get("tracking", ""), res.get("pedido_ecommerce", ""),
                                         res.get("canal", ""), res.get("sku", ""),
                                         status="cancelado")
                fluxo.registrar_na_nuvem(res.get("tracking", ""), cancelado=True)
                st.session_state.scanner_sessao_cancelados += 1
                fluxo.limpar_leitura()
                st.rerun()
        with c2:
            if st.button("📷 LER OUTRO", use_container_width=True):
                st.session_state.scanner_sessao_pulados += 1
                fluxo.limpar_leitura()
                st.rerun()
    else:
        _fem = res.get("genero") == "fem"
        cor_destaque = "#f472b6" if _fem else "#34d399"
        borda = "#9d174d" if _fem else "#059669"
        fundo = "#2a0a1c" if _fem else "#064e3b"
        _itens = res.get("itens") or []
        _volumes = sum(int(i.get("quantidade") or 1) for i in _itens) or 1
        ui.render_html(ui.ficha_compacta_html(
            res, badge=badge, primeiro_nome=primeiro_nome, conferido=conferido,
            modelo=res.get("modelo") or res.get("produto") or "—", itens=_itens,
            volumes=_volumes, img=res.get("imagem_url") or "",
            cor_destaque=cor_destaque, borda=borda, fundo=fundo))

        # Confirmar a peca (opcional): fechado, abre sozinho se ja' houver resultado.
        val = st.session_state.scanner_validacao
        with st.expander("🏷️ Confirmar a peça (opcional)", expanded=bool(val)):
            with st.form("form_validacao_sku", clear_on_submit=True):
                cod_peca = st.text_input(
                    "Código da peça", label_visibility="collapsed",
                    placeholder="Ex: MEINVMAY1013540PRE  ou  TOPTAY016-AZUL", key="inp_validacao")
                if st.form_submit_button("🔎 Validar peça", use_container_width=True):
                    if cod_peca and cod_peca.strip():
                        fluxo.validar_produto(cod_peca)
                        st.rerun()
        # Resultado FORA do expander: divergencia nao pode ficar escondida.
        if val:
            if val["ok"]:
                st.success(f"**{val['titulo']}**\n\n{val['detalhe']}")
            elif val["nivel"] == "sem_dados":
                st.warning(f"**{val['titulo']}**\n\n{val['detalhe']}")
            else:
                st.error(f"**{val['titulo']}**\n\n{val['detalhe']}\n\n"
                         f"- Pedido: `{val['esperado'] or '—'}`\n"
                         f"- Etiqueta lida: `{val['lido'] or '—'}`")
                st.caption("Confira a peça na caixa. Se estiver errada, troque antes de despachar.")

        # Botoes fixos no rodape (container com key -> classe .st-key-rodape_conferir).
        with st.container(key="rodape_conferir"):
            col_ok, col_pular = st.columns(2)
            with col_ok:
                if val and val.get("ok"):
                    rotulo = "✅ PODE DESPACHAR → PRÓXIMO"
                elif val and val["nivel"] not in ("sem_dados",):
                    rotulo = "⚠️ CONFERIR MESMO ASSIM → PRÓXIMO"
                else:
                    rotulo = "✅ CONFERIDO → PRÓXIMO"
                if st.button(rotulo, type="primary", use_container_width=True):
                    db.registrar_conferencia(
                        res.get("tracking", ""), res.get("pedido_ecommerce", ""),
                        res.get("canal", ""), res.get("sku", ""),
                        sku_validado=(val or {}).get("lido", ""),
                        validacao_nivel=(val or {}).get("nivel", ""))
                    fluxo.registrar_na_nuvem(res.get("tracking", ""))
                    st.session_state.scanner_sessao_conferidos += 1
                    if val and val.get("ok"):
                        st.session_state.scanner_sessao_validados += 1
                    elif val and val["nivel"] not in ("sem_dados",):
                        st.session_state.scanner_sessao_divergencias += 1
                    fluxo.limpar_leitura()
                    st.rerun()
            with col_pular:
                if st.button("⚠️ PULAR", use_container_width=True):
                    st.session_state.scanner_sessao_pulados += 1
                    fluxo.limpar_leitura()
                    st.rerun()

elif res and res.get("codigo_invalido"):
    ui.render_html(f'<div class="bip-invalido"><div class="t">🟠 CÓDIGO ERRADO DA ETIQUETA</div>'
                   f'<div style="margin:6px 0;">{res.get("motivo")}</div>'
                   f'<div>Lido: <code>{codigo_atual}</code></div></div>')
    if st.button("📷 LER O CÓDIGO DE RASTREIO", type="primary", use_container_width=True):
        fluxo.limpar_leitura()
        st.rerun()

elif tem_leitura:
    ui.render_html(f'<div class="bip-erro"><div class="t">🔴 PEDIDO NÃO ENCONTRADO</div>'
                   f'<div style="margin:6px 0;">Nenhum pedido casou com <code>{codigo_atual}</code>.</div>'
                   f'<div style="font-size:13px;">💡 Venda recente? A base vem da Esteira: atualize a fila no PC e '
                   f'toque em TENTAR DE NOVO.<br>Base: {_resumo_frescor}</div></div>')
    c1, c2 = st.columns(2)
    with c1:
        if st.button("🔄 TENTAR DE NOVO", type="primary", use_container_width=True):
            st.cache_data.clear()
            fluxo.processar_codigo(codigo_atual)
            st.rerun()
    with c2:
        if st.button("📷 LER OUTRO", use_container_width=True):
            fluxo.limpar_leitura()
            st.rerun()

else:
    ui.render_html('<div class="bip-aguardando">Aguardando leitura… aponte a câmera para a etiqueta.</div>')
