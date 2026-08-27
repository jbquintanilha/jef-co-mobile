# ==============================================================================
# NOME DO SCRIPT: pages/14_Scanner_Conferencia.py
# DESCRICAO: Scanner de Conferencia de Pedidos J&F Co. — bipagem de etiquetas
#            de envio (Shopee/ML/TikTok/Correios) via digitacao, pistola
#            Bluetooth (teclado HID) ou camera do celular. Mostra o card do
#            pedido (produto/SKU/cor/kit/cliente) e registra conferencias.
# AUTOR: Conselho J&F Co. - Roo Code (sub-gerente operacional)
# VERSAO: 1.0
# DATA: 2026-08-02
# STATUS: Operacional
# REF: plans/scanner_conferencia_pedidos_2026-08-02.md
# ==============================================================================
"""Página Streamlit do Scanner de Conferência de Pedidos.

Uso no celular (rede Wi-Fi): ``http://IP-DO-PC:8501`` -> aba "14_Scanner".

Fluxo:
  1. Bipa a etiqueta (pistola HID = digita + Enter) OU escaneia com a câmera.
  2. ``core_scanner_resolver.resolver_codigo`` resolve o código (indice + Olist).
  3. Card 🟢/🔴 com produto, SKU, cor, kit, cliente (mascarado), CEP e tracking.
  4. Botão "✅ Conferido" registra no log SQLite (tabela conferencias).
  5. Contador do dia: conferidos vs pendentes.
"""

import os
import sys

import streamlit as st

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import core_scanner_db as db
import core_scanner_decoder as decoder
import core_scanner_resolver as resolver
import core_scanner_populator as populator
import core_scanner_validador as validador
import core_scanner_feedback as feedback
import core_scanner_expedicao as expedicao
import core_scanner_auditoria as auditoria
import core_comprovante_conferencia as comprovante
import scanner_camera_ao_vivo as camera_ao_vivo
import streamlit.components.v1 as components

st.set_page_config(page_title="Scanner de Conferência — J&F Co.", layout="wide", page_icon="📷")

# ------------------------------------------------------------------ #
# CSS (mesmo tema escuro da esteira)
# ------------------------------------------------------------------ #
st.markdown(
    """
    <style>
    .scanner-card-ok {
        background-color: #052e16;
        border: 1px solid #16a34a;
        border-radius: 12px;
        padding: 18px 20px;
        box-shadow: 0 4px 6px -1px rgb(0 0 0 / 0.4);
        margin-bottom: 10px;
    }
    .scanner-card-erro {
        background-color: #450a0a;
        border: 1px solid #dc2626;
        border-radius: 12px;
        padding: 18px 20px;
        box-shadow: 0 4px 6px -1px rgb(0 0 0 / 0.4);
        margin-bottom: 10px;
    }
    .scanner-card-cancelado {
        background-color: #7f1d1d;
        border: 3px solid #f87171;
        border-radius: 12px;
        padding: 18px 20px;
        box-shadow: 0 0 24px rgb(239 68 68 / 0.45);
        margin-bottom: 10px;
        animation: scanner-pulso 1.2s ease-in-out infinite;
    }
    @keyframes scanner-pulso {
        0%, 100% { box-shadow: 0 0 10px rgb(239 68 68 / 0.35); }
        50%      { box-shadow: 0 0 28px rgb(239 68 68 / 0.75); }
    }
    .scanner-titulo-cancelado { font-size: 20px; font-weight: 800; color: #ffffff; }
    .scanner-status-cancelado {
        display: inline-block; padding: 3px 12px; border-radius: 20px;
        background-color: #ef4444; color: #ffffff; font-weight: 800; font-size: 13px;
    }
    .scanner-aviso-cancelado {
        margin-top: 12px; padding: 10px 12px; border-radius: 8px;
        background-color: #450a0a; border: 1px solid #ef4444; color: #fecaca; font-size: 14px;
    }
    a.btn-cancelado {
        display: block; text-align: center; padding: 12px 16px; border-radius: 8px;
        background: linear-gradient(135deg, #f59e0b, #d97706); color: #1c1917;
        font-weight: 800; font-size: 15px; text-decoration: none;
        border: 1px solid #b45309; box-shadow: 0 2px 6px rgb(0 0 0 / 0.4);
        margin-bottom: 6px;
    }
    a.btn-cancelado:hover { filter: brightness(1.1); }
    a.btn-ignorar {
        display: block; text-align: center; padding: 12px 16px; border-radius: 8px;
        background-color: #334155; color: #e2e8f0; font-weight: 700; font-size: 15px;
        text-decoration: none; border: 1px solid #475569; margin-bottom: 6px;
    }
    a.btn-ignorar:hover { background-color: #475569; }
    .scanner-titulo { font-size: 18px; font-weight: 700; color: #f1f5f9; }
    .scanner-linha  { font-size: 14px; color: #e2e8f0; margin: 6px 0; }
    .scanner-label  { color: #94a3b8; font-size: 12px; text-transform: uppercase; letter-spacing: .5px; }
    .scanner-tag {
        display: inline-block; padding: 2px 10px; border-radius: 20px;
        font-size: 12px; font-weight: 700; color: #0f172a; background-color: #e2e8f0;
    }
    .scanner-tag-ml { background-color: #ffe600; }
    .scanner-tag-shopee { background-color: #ee4d2d; color: white; }
    .scanner-tag-tiktok { background-color: #111827; color: white; border: 1px solid #374151; }
    .scanner-tag-correios { background-color: #2563eb; color: white; }
    .scanner-tag-manual { background-color: #64748b; color: white; }
    </style>
    """,
    unsafe_allow_html=True,
)


# ------------------------------------------------------------------ #
# Helpers
# ------------------------------------------------------------------ #
def mascarar_nome(nome: str) -> str:
    """Mascara o nome do cliente (ex: 'Maria da Silva' -> 'M*** d*** S***')."""
    if not nome:
        return "—"
    partes = str(nome).split()
    return " ".join(f"{p[0]}***" for p in partes if p)


def mascarar_cep(cep: str) -> str:
    """Mascara o CEP mantendo os 2 ultimos digitos (ex: ******78)."""
    if not cep:
        return "—"
    digitos = "".join(ch for ch in str(cep) if ch.isdigit())
    if len(digitos) < 2:
        return "******"
    return "*" * max(1, len(digitos) - 2) + digitos[-2:]


def tag_canal(canal: str) -> str:
    """HTML do badge do canal."""
    c = (canal or "").lower()
    if "mercado" in c:
        return '<span class="scanner-tag scanner-tag-ml">ML</span>'
    if "shopee" in c:
        return '<span class="scanner-tag scanner-tag-shopee">SHOPEE</span>'
    if "tiktok" in c:
        return '<span class="scanner-tag scanner-tag-tiktok">TIKTOK</span>'
    if "correio" in c:
        return '<span class="scanner-tag scanner-tag-correios">CORREIOS</span>'
    return f'<span class="scanner-tag scanner-tag-manual">{str(canal or "MANUAL").upper()}</span>'


# Decodificacao de QR/barcode vem de core_scanner_decoder (reutilizavel/testavel).
# Motores: zxing-cpp -> pyzbar (fallback) -> OpenCV QR/barcode.
decodificar_imagem = decoder.decodificar_imagem




# ------------------------------------------------------------------ #
# Estado de sessao — PAGINA UNICA: camera em cima, ficha do produto embaixo.
#
# Por que pagina unica e nao duas telas: o iframe do componente de camera roda
# num sandbox SEM 'allow-top-navigation', entao ele nao consegue trocar a tela
# do app. Mantendo tudo numa pagina so, a leitura apenas rola a visao ate a
# ficha -- mais simples e mais rapido pro uso na bancada.
# ------------------------------------------------------------------ #
if "scanner_resultado" not in st.session_state:
    st.session_state.scanner_resultado = None
if "scanner_ultimo_codigo" not in st.session_state:
    st.session_state.scanner_ultimo_codigo = ""
if "scanner_upload_codigo" not in st.session_state:
    st.session_state.scanner_upload_codigo = ""
if "scanner_sessao_conferidos" not in st.session_state:
    st.session_state.scanner_sessao_conferidos = 0
if "scanner_sessao_pulados" not in st.session_state:
    st.session_state.scanner_sessao_pulados = 0
if "scanner_sessao_cancelados" not in st.session_state:
    st.session_state.scanner_sessao_cancelados = 0
if "scanner_encerrado" not in st.session_state:
    st.session_state.scanner_encerrado = False
if "scanner_msg_sync" not in st.session_state:
    st.session_state.scanner_msg_sync = ""
# Dupla conferencia: resultado do cruzamento SKU do pedido x etiqueta da peca.
if "scanner_validacao" not in st.session_state:
    st.session_state.scanner_validacao = None
if "scanner_sessao_validados" not in st.session_state:
    st.session_state.scanner_sessao_validados = 0
if "scanner_sessao_divergencias" not in st.session_state:
    st.session_state.scanner_sessao_divergencias = 0
# Caixa de relato de erro/melhoria (fica fechada ate o operador clicar).
if "scanner_form_feedback" not in st.session_state:
    st.session_state.scanner_form_feedback = False
if "scanner_msg_feedback" not in st.session_state:
    st.session_state.scanner_msg_feedback = ""
# Conferencia FINAL da expedicao (modo a parte, independente do fluxo do dia).
if "exp_modo" not in st.session_state:
    st.session_state.exp_modo = False
if "exp_esperados" not in st.session_state:
    st.session_state.exp_esperados = []
if "exp_bipados" not in st.session_state:
    st.session_state.exp_bipados = {}
if "exp_fora_lista" not in st.session_state:
    st.session_state.exp_fora_lista = []
if "exp_ignorados" not in st.session_state:
    st.session_state.exp_ignorados = []
if "exp_ultimo" not in st.session_state:
    st.session_state.exp_ultimo = None
if "exp_relatorio" not in st.session_state:
    st.session_state.exp_relatorio = None
if "exp_msg" not in st.session_state:
    st.session_state.exp_msg = ""


def _processar_codigo(codigo: str) -> None:
    """Resolve o codigo lido e deixa a ficha pronta logo abaixo da camera.

    Sanitiza antes de resolver: a pistola le tudo que estiver no campo de visao
    (rastreio + chave da NF-e + CEP) e digita junto. `sanitizar_codigo` descarta
    o que nunca identifica pedido e separa rastreios colados.
    """
    bruto = db.normalizar_codigo(codigo)
    if not bruto:
        return
    limpo = decoder.sanitizar_codigo(bruto)
    if not limpo:
        # Codigo reconhecidamente inutil (chave de NF-e ou CEP): avisa o
        # operador em vez de buscar no indice e dizer "nao encontrado".
        st.session_state.scanner_ultimo_codigo = bruto
        st.session_state.scanner_resultado = {
            "encontrado": False,
            "codigo_invalido": True,
            "motivo": "Esse código é da nota fiscal ou do CEP — não identifica "
                      "o pedido. Bipe o código de RASTREIO da etiqueta.",
        }
        return
    st.session_state.scanner_ultimo_codigo = limpo
    st.session_state.scanner_resultado = resolver.resolver_codigo(limpo)

    # LEI DA VERIFICACAO DOBRADA (Jota, 2026-08-12): confirmar em SEGUNDA FONTE
    # (API do marketplace) que o pedido em tela bate com o que o cliente
    # comprou. Roda em background pra nao travar a bancada; divergencia fica
    # fixa na tela ate o Comandante dar OK.
    auditoria.auditar_async(limpo)


def _limpar_leitura() -> None:
    """Limpa a ficha atual e deixa a camera pronta pro proximo pedido."""
    st.session_state.scanner_resultado = None
    st.session_state.scanner_ultimo_codigo = ""
    st.session_state.scanner_validacao = None


def _validar_produto(codigo_peca: str) -> None:
    """Cruza a etiqueta de produto bipada com o SKU do pedido em tela."""
    res_atual = st.session_state.scanner_resultado or {}
    st.session_state.scanner_validacao = validador.validar(
        res_atual.get("sku", ""), codigo_peca
    )


# ------------------------------------------------------------------ #
# Codigo vindo por URL (?cod=XXXX) — atalho pra teste/atalho de navegador.
# A camera ao vivo NAO usa este caminho: o iframe do componente nao tem
# permissao de navegar na URL do pai, entao ele entrega pelo formulario.
# ------------------------------------------------------------------ #
_cod_url = st.query_params.get("cod")
if _cod_url:
    # Consome o parametro pra nao reprocessar a cada rerun.
    del st.query_params["cod"]
    _processar_codigo(_cod_url)


# ------------------------------------------------------------------ #
# Sidebar — manutencao do indice + info
# ------------------------------------------------------------------ #
with st.sidebar:
    st.title("📷 Scanner")
    st.caption("Bipagem de etiquetas de envio")
    st.divider()

    if st.button("🔄 Atualizar índice de rastreios", use_container_width=True):
        with st.spinner("Buscando trackings nas APIs (Shopee + ML + TikTok)..."):
            try:
                res = populator.popular_todos(force=True)
                st.success(
                    f"Índice atualizado → Shopee: {res.get('shopee', 0)} · "
                    f"ML: {res.get('ml', 0)} · TikTok: {res.get('tiktok', 0)}"
                )
            except Exception as e:
                st.error(f"Falha ao atualizar índice: {e}")
        st.rerun()

    st.caption("📡 Índice local (SQLite)")
    st.caption(f"`{db.DB_PATH}`")

    st.divider()
    st.caption(
        "**Como usar no celular:**\n\n"
        "1. Abra `http://IP-DO-PC:8501` no Wi-Fi\n"
        "2. Aponte a câmera para a etiqueta — lê sozinha\n"
        "3. Confira produto/SKU/cor/kit na ficha\n"
        "4. **PRÓXIMO** volta pra câmera · **ENCERRAR** finaliza\n\n"
        "Pistola Bluetooth funciona como teclado: digita o código e dá Enter."
    )


# ================================================================== #
# TELA FINAL — encerrado
# ================================================================== #
if st.session_state.scanner_encerrado:
    st.title("✅ Expedição encerrada")
    conf = st.session_state.scanner_sessao_conferidos
    pul = st.session_state.scanner_sessao_pulados
    can = st.session_state.scanner_sessao_cancelados
    val_ok = st.session_state.scanner_sessao_validados
    val_div = st.session_state.scanner_sessao_divergencias
    c1, c2, c3 = st.columns(3)
    c1.metric("✅ Conferidos nesta sessão", conf)
    c2.metric("⚠️ Pulados", pul)
    c3.metric("🚨 Cancelados", can)

    c4, c5 = st.columns(2)
    c4.metric("🏷️ Peça validada por SKU", val_ok,
              help="Pedidos em que a etiqueta do produto foi bipada e casou com o pedido")
    c5.metric("❗ Despachados com divergência", val_div,
              help="A etiqueta da peça não casou, mas o operador seguiu mesmo assim")
    if val_div:
        st.error(
            f"{val_div} pedido(s) foram despachados mesmo com divergência entre a "
            "etiqueta da peça e o SKU do pedido. Vale revisar."
        )

    if pul:
        st.warning(f"{pul} pedido(s) foram pulados e não entraram na conferência.")
    if can:
        st.error(f"{can} pedido(s) CANCELADO(s) identificado(s) e registrados — NÃO foram despachados.")
    st.success("Pode fechar a página. Bom trabalho!")

    if st.button("🔄 Iniciar nova sessão", type="primary", use_container_width=True):
        st.session_state.scanner_encerrado = False
        st.session_state.scanner_sessao_conferidos = 0
        st.session_state.scanner_sessao_pulados = 0
        st.session_state.scanner_sessao_cancelados = 0
        st.session_state.scanner_sessao_validados = 0
        st.session_state.scanner_sessao_divergencias = 0
        _limpar_leitura()
        st.rerun()

    with st.expander("📜 Conferências registradas hoje", expanded=True):
        ultimas = db.ultimas_conferencias(30)
        if ultimas:
            st.dataframe(
                [
                    {
                        "Tracking": u.get("tracking"),
                        "Canal": u.get("canal"),
                        "Pedido": u.get("pedido_ecommerce"),
                        "SKU": u.get("sku_principal"),
                        "Status": (u.get("status") or "conferido").upper(),
                        "Quando": u.get("conferido_em"),
                    }
                    for u in ultimas
                ],
                use_container_width=True,
                hide_index=True,
            )
        else:
            st.info("Nenhuma conferência registrada hoje.")
    st.stop()


# ================================================================== #
# PAGINA UNICA — camera em cima, ficha do produto logo abaixo
# ================================================================== #
res = st.session_state.scanner_resultado
codigo_atual = st.session_state.scanner_ultimo_codigo
tem_leitura = bool(codigo_atual)

# Ações disparadas pelos botões HTML do card CANCELADO (Streamlit não tem
# botão laranja nativo — usamos links estilizados com ?acao=...). Consome o
# parâmetro pra não reprocessar na rerun seguinte.
_acao = st.query_params.get("acao")
if _acao == "cancelado_ok" and res:
    del st.query_params["acao"]
    db.registrar_conferencia(
        res.get("tracking", ""),
        res.get("pedido_ecommerce", ""),
        res.get("canal", ""),
        res.get("sku", ""),
        status="cancelado",
    )
    st.session_state.scanner_sessao_cancelados += 1
    _limpar_leitura()
    st.rerun()
elif _acao == "ignorar":
    del st.query_params["acao"]
    st.session_state.scanner_sessao_pulados += 1
    _limpar_leitura()
    st.rerun()

# ================================================================== #
# 🔍 CONFERENCIA FINAL DA EXPEDICAO — modo exclusivo
#
# Roda antes de fechar a bolsa. E' INDEPENDENTE do fluxo do dia de proposito:
# refaz tudo do zero, mesmo o que ja foi bipado na bancada (decisao do Jota).
# Uma conferencia final que confia no resultado anterior nao confere nada.
#
# Desenhada pra dia de volume: a bipagem cai no campo, mostra o veredito e ja
# devolve o foco pro proximo -- sem clique extra entre uma etiqueta e outra.
# ================================================================== #
if st.session_state.exp_modo:
    st.title("🔍 Conferência Final da Expedição")

    esperados = st.session_state.exp_esperados
    bipados = st.session_state.exp_bipados
    total = len(esperados)
    feitos = len([1 for e in esperados
                  if bipados.get(e.get("tracking") or e.get("pedido_ecommerce"))])
    restam = total - feitos

    m1, m2, m3 = st.columns(3)
    m1.metric("📦 A expedir", total)
    m2.metric("✅ Conferidos", feitos)
    m3.metric("⏳ Faltam", restam)

    if total:
        st.progress(feitos / total)

    if st.session_state.exp_msg:
        st.info(st.session_state.exp_msg)
        st.session_state.exp_msg = ""

    # ---- resultado da ultima bipagem ----
    ult = st.session_state.exp_ultimo
    if ult:
        if ult["status"] == "ok":
            it = ult.get("item", {})
            st.success(f"**{ult['titulo']}** — {it.get('canal','')} · "
                       f"{it.get('produto') or it.get('sku') or ''}")
        elif ult["status"] == "duplicado":
            st.warning(f"**{ult['titulo']}**\n\n{ult['detalhe']}")
        else:
            st.error(f"**{ult['titulo']}**\n\n{ult['detalhe']}\n\n"
                     f"Código: `{ult.get('codigo','')}`")

    # ---- camera ao vivo: le a etiqueta e dispara o "Conferir" sozinha ----
    # Compacta (280px): aqui o operador ja sabe o que esta fazendo, o espaco
    # vale mais pro contador e pro veredito da leitura.
    # rearmar=True: aqui a bipagem e' continua (uma etiqueta atras da outra).
    # Sem isso a camera morre depois do 1o codigo e o 2o bip trava.
    camera_ao_vivo.render_camera(altura=280, botao_submit="Conferir",
                                 rearmar=True)

    # ---- campo de bipagem: recebe da camera, da pistola ou digitado ----
    with st.form("form_exp_bipagem", clear_on_submit=True):
        cod_exp = st.text_input(
            "Código da etiqueta",
            placeholder="Ex: AP296430628BR — bipe, digite ou use a câmera acima",
            label_visibility="collapsed",
            key="inp_exp",
        )
        if st.form_submit_button("🔍 Conferir", type="primary",
                                 use_container_width=True) and cod_exp.strip():
            r = expedicao.conferir(cod_exp, esperados, bipados)
            if r["status"] == "ok":
                it = r["item"]
                chave = it.get("tracking") or it.get("pedido_ecommerce")
                bipados[chave] = 1
            elif r["status"] == "duplicado":
                it = r["item"]
                chave = it.get("tracking") or it.get("pedido_ecommerce")
                bipados[chave] = bipados.get(chave, 1) + 1
            elif r["status"] == "fora_lista":
                st.session_state.exp_fora_lista.append(r)
            st.session_state.exp_ultimo = r
            st.rerun()

    st.divider()

    # ---- relatorio ----
    col_r1, col_r2 = st.columns(2)
    with col_r1:
        if st.button("📋 GERAR RELATÓRIO", type="primary", use_container_width=True):
            _rel = expedicao.montar_relatorio(
                esperados, bipados, st.session_state.exp_fora_lista,
                st.session_state.exp_ignorados)
            st.session_state.exp_relatorio = _rel
            # Salva JA, sem depender de clique: na primeira versao o relatorio
            # so ia pro arquivo se o operador clicasse em "Salvar", e o do dia
            # 09/08 se perdeu ao sair da tela (relato do Comandante).
            if expedicao.salvar_relatorio(_rel, "salvo automaticamente ao gerar"):
                st.session_state.exp_msg = "💾 Relatório salvo automaticamente."
            st.rerun()
    with col_r2:
        if st.button("↩️ SAIR DA CONFERÊNCIA", use_container_width=True):
            st.session_state.exp_modo = False
            st.session_state.exp_ultimo = None
            st.session_state.exp_relatorio = None
            st.rerun()

    rel = st.session_state.exp_relatorio
    if rel:
        st.divider()
        if rel["ok"]:
            st.success("✅ **TUDO CONFERIDO** — pode fechar a bolsa e despachar.")
        else:
            st.error("⚠️ **HÁ PENDÊNCIAS** — resolva antes de despachar.")

        if rel["faltando"]:
            st.markdown(f"#### 🔴 Não conferidos ({len(rel['faltando'])})")
            st.dataframe(
                [{"Canal": f.get("canal", ""),
                  "Pedido": f.get("pedido_ecommerce", ""),
                  "Tracking": f.get("tracking", "") or "—",
                  "Produto": (f.get("produto") or f.get("sku") or "")[:45],
                  "Cliente": (f.get("cliente") or "").split(" ")[0]}
                 for f in rel["faltando"]],
                use_container_width=True, hide_index=True,
            )

        if rel.get("faltando_ja_visto"):
            st.markdown(
                f"#### 🟡 Não bipados aqui, mas já conferidos hoje "
                f"({len(rel['faltando_ja_visto'])})")
            st.caption("Passaram pelo Scanner da bancada. Confirme se estão na bolsa.")
            st.dataframe(
                [{"Canal": f.get("canal", ""),
                  "Pedido": f.get("pedido_ecommerce", ""),
                  "Tracking": f.get("tracking", "") or "—",
                  "Produto": (f.get("produto") or f.get("sku") or "")[:45],
                  "Cliente": (f.get("cliente") or "").split(" ")[0]}
                 for f in rel["faltando_ja_visto"]],
                use_container_width=True, hide_index=True,
            )

        if rel["duplicados"]:
            st.markdown(f"#### ⚠️ Bipados mais de uma vez ({len(rel['duplicados'])})")
            st.caption("Retire a caixa repetida da bolsa antes de despachar.")
            st.dataframe(
                [{"Leituras": f"{d['vezes']}x",
                  "Tracking": d["chave"],
                  "Pedido": d.get("pedido_ecommerce", "") or "—",
                  "Produto": (d.get("produto") or "")[:40] or "—",
                  "SKU": d.get("sku", "") or "—",
                  "Cliente": (d.get("cliente") or "").split(" ")[0] or "—"}
                 for d in rel["duplicados"]],
                use_container_width=True, hide_index=True,
            )

        if rel["fora_lista"]:
            st.markdown(f"#### 🟠 Fora da lista de hoje ({len(rel['fora_lista'])})")
            st.dataframe(
                [{"Código": f.get("codigo", "")} for f in rel["fora_lista"]],
                use_container_width=True, hide_index=True,
            )

        obs = st.text_input(
            "Observação (opcional) — salva uma cópia com a sua nota",
            key="inp_obs_exp",
            placeholder="Ex: os 2 faltantes estavam na bolsa, etiqueta não bipou",
        )
        if st.button("💾 SALVAR CÓPIA COM OBSERVAÇÃO", use_container_width=True):
            if expedicao.salvar_relatorio(rel, obs):
                st.session_state.exp_msg = (
                    "✅ Cópia salva em `EXPEDICAO_RELATORIOS.md`.")
            else:
                st.session_state.exp_msg = "❌ Falha ao salvar."
            st.rerun()

    # ---- histórico: ver e zerar (evita acumular lixo) ----
    st.divider()
    _qtd_rel = expedicao.contar_relatorios()
    with st.expander(f"📚 Relatórios salvos ({_qtd_rel})", expanded=False):
        if not _qtd_rel:
            st.caption("Nenhum relatório salvo ainda.")
        else:
            st.markdown(expedicao.ler_relatorios())
            st.divider()
            st.caption(
                "Ao zerar, uma cópia de segurança é gravada como "
                "`EXPEDICAO_RELATORIOS_backup_<data>.md` — relatório de "
                "expedição pode servir de prova em disputa."
            )
            if st.checkbox("Confirmo que as pendências foram sanadas",
                           key="ck_zerar_rel"):
                if st.button("🗑️ ZERAR RELATÓRIOS", use_container_width=True):
                    if expedicao.limpar_relatorios():
                        st.session_state.exp_msg = (
                            "🗑️ Relatórios zerados (backup gravado).")
                    else:
                        st.session_state.exp_msg = "❌ Falha ao zerar."
                    st.rerun()

    st.stop()


stats = db.stats_dia()
cab_a, cab_b = st.columns([3, 2])
with cab_a:
    st.title("📷 Scanner de Conferência")
with cab_b:
    st.metric(
        "✅ Nesta sessão",
        st.session_state.scanner_sessao_conferidos,
        help=f"Conferidos hoje no total: {stats['conferidos_hoje']}",
    )

# ------------------------------------------------------------------ #
# ALARME DE DIVERGENCIA — verificacao dobrada (indice local x marketplace)
# Regra 3 da lei: FICA NA TELA ate o Comandante dar OK. Nao some sozinho.
# ------------------------------------------------------------------ #
_divs = auditoria.listar_abertas()
if _divs:
    # ⚠️ Separa AVISO de ERRO. `multi_item` nao e' divergencia — e' pedido com
    # mais de um atomo, que so' pede conferencia peca a peca. Tratar tudo como
    # 🚨 fazia o operador ignorar o alerta de verdade (Jota, 18/08:
    # "esta muito sensivel").
    _graves = [d for d in _divs if d.get("tipo") != "multi_item"]
    _avisos = [d for d in _divs if d.get("tipo") == "multi_item"]

    if _graves:
        st.error(f"### 🚨 {len(_graves)} DIVERGÊNCIA(S) ENTRE O SCANNER E O MARKETPLACE")
    if _avisos:
        st.info(f"### 📦 {len(_avisos)} pedido(s) MULTI-ITEM — conferir peça a peça")

    for _d in _graves + _avisos:
        _e_aviso = _d.get("tipo") == "multi_item"
        with st.container(border=True):
            st.markdown(
                f"{'📦' if _e_aviso else '🚨'} **{_d.get('tracking')}** · "
                f"`{(_d.get('canal') or '').upper()}` · {_d.get('detectado_em') or ''}"
            )
            (st.info if _e_aviso else st.warning)(_d.get("detalhe") or "")
            _ca, _cb = st.columns([1, 4])
            with _ca:
                if st.button("✅ OK, resolvido", key=f"okdiv_{_d['id']}",
                             use_container_width=True):
                    auditoria.dar_ok(_d["id"])
                    st.rerun()
            with _cb:
                with st.expander("Ver detalhe técnico"):
                    st.caption("No Scanner (índice local):")
                    st.code(_d.get("itens_local") or "—", language="json")
                    st.caption("No marketplace (fonte de verdade):")
                    st.code(_d.get("itens_canal") or "—", language="json")
    if len(_divs) > 1 and st.button("✅ Dar OK em todas", type="secondary"):
        auditoria.dar_ok_todas()
        st.rerun()
    st.divider()

# Botao de sincronia AQUI na tela principal (nao so na sidebar): no celular a
# sidebar fica escondida atras do ">>" e, quando sai venda nova, o rastreio
# ainda nao esta no indice -- o scanner acusa "nao encontrado" e o operador
# precisa sincronizar na hora, sem sair da tela de bipagem.
col_sync, col_info = st.columns([2, 3])
with col_sync:
    if st.button("🔄 ATUALIZAR BASE (Shopee · ML · TikTok)",
                 use_container_width=True, type="secondary"):
        with st.spinner("Buscando pedidos e rastreios nas APIs…"):
            try:
                # ⚡ Versao paralela: os 3 canais ao mesmo tempo e a Shopee com
                # 8 conexoes. Medido 17/08: 230s -> 68s, mesmos 31 registros.
                # O `populator` original segue intacto como fallback.
                try:
                    import core_scanner_populator_rapido as populator_rapido
                    r_sync = populator_rapido.popular_todos_rapido(force=True)
                except Exception as exc_rapido:
                    # Cai no original em vez de falhar: atualizar devagar e'
                    # melhor que nao atualizar.
                    st.warning(f"Modo rápido indisponível ({exc_rapido}) — "
                               "usando o método antigo, ~4 min.")
                    r_sync = populator.popular_todos(force=True)

                st.session_state.scanner_msg_sync = (
                    f"✅ Base atualizada → Shopee {r_sync.get('shopee', 0)} · "
                    f"ML {r_sync.get('ml', 0)} · TikTok {r_sync.get('tiktok', 0)}"
                    + (f" · {r_sync['segundos']}s" if r_sync.get("segundos") else "")
                )
                # Verificacao dobrada em lote: cruza o indice inteiro contra os
                # marketplaces em background, pra divergencia aparecer ANTES de
                # a caixa ser montada.
                auditoria.auditar_pendentes_async()
            except Exception as e:
                st.session_state.scanner_msg_sync = f"❌ Falha ao atualizar: {e}"
        st.rerun()
with col_info:
    st.caption(
        f"🗂️ {stats['total_indice']} rastreios na base · "
        "toque em **Atualizar** quando sair venda nova"
    )

if st.session_state.get("scanner_msg_sync"):
    _msg = st.session_state.scanner_msg_sync
    (st.success if _msg.startswith("✅") else st.error)(_msg)
    st.session_state.scanner_msg_sync = ""

# ------------------------------------------------------------------ #
# CAMERA — encolhe depois da leitura pra dar espaco a ficha
# ------------------------------------------------------------------ #
# ⚠️ ALTURA FIXA de proposito. Antes era `200 if tem_leitura else 420`, e a
# mudanca de altura fazia o Streamlit DESTRUIR e recriar o iframe a cada
# leitura — a camera reiniciava do zero e o operador esperava. Com altura
# constante o iframe sobrevive ao rerun.
#
# rearmar=True: a camera religa sozinha apos entregar o codigo, entao da'
# para bipar a proxima etiqueta sem esperar o salvamento da anterior
# (pedido do Jota, 24/08). Ja' era usado na conferencia final; faltava aqui.
camera_ao_vivo.render_camera(altura=320, botao_submit="Resolver",
                             rearmar=True)

if not tem_leitura:
    st.caption("Aponte a câmera para a etiqueta e toque em **📸 LER CÓDIGO** para realizar a leitura.")

# Este formulario precisa ficar SEMPRE VISIVEL (nao dentro de expander
# fechado): e' por ele que a camera entrega o codigo lido. O iframe do
# componente nao pode navegar na URL do pai (falta allow-top-navigation no
# sandbox do Streamlit), entao ele preenche este campo e dispara o submit.
# Serve tambem pra digitacao manual e pra pistola Bluetooth.
with st.form("form_bipagem", clear_on_submit=True):
    codigo_digitado = st.text_input(
        "Código da etiqueta",
        placeholder="Ex: AP296430628BR  ou  260802B4MD9MHU  ou só 3 letras/números do código",
        label_visibility="collapsed",
        key="inp_bipagem",
    )
    if st.form_submit_button("🔍 Resolver", use_container_width=True, type="primary"):
        if codigo_digitado and codigo_digitado.strip():
            _processar_codigo(codigo_digitado)
            st.rerun()

# ---- autocomplete: a partir de 3 caracteres, sugere de qualquer parte ----
# Campo SEPARADO do form acima de proposito. O form usa clear_on_submit=True
# (necessario pra pistola/camera nao repetirem a leitura anterior), o que apaga
# o texto antes do rerun -- ali o autocomplete nunca conseguiria ler o
# fragmento. Fora do form, o Streamlit re-executa a cada digitacao e a busca
# responde na hora.
# Uso pratico: operador confere pelo PC digitando 3-4 caracteres de qualquer
# parte do codigo, enquanto o celular segue filmando a bancada.
_ciclo = st.session_state.get("busca_parcial_ciclo", 0)
_frag = st.text_input(
    "Buscar por parte do código",
    placeholder="Digite 3+ caracteres de qualquer parte do código ou do nº do pedido…",
    label_visibility="collapsed",
    key=f"inp_busca_parcial_{_ciclo}",
).strip()

if len(_frag) >= 3:
    # O Streamlit recarrega so o arquivo da pagina; modulos importados ficam
    # em cache no processo. Se o servidor subiu antes desta funcao existir,
    # `db` em memoria e' a versao antiga -- recarrega uma vez em vez de exigir
    # restart do dashboard.
    if not hasattr(db, "buscar_parcial"):
        import importlib
        db = importlib.reload(db)
    _sugestoes = db.buscar_parcial(_frag, limit=8)
    if _sugestoes:
        st.caption(f"🔎 {len(_sugestoes)} resultado(s) para **{_frag.upper()}** — clique para resolver:")
        for _s in _sugestoes:
            _trk = _s.get("tracking") or ""
            _feito = bool(_s.get("ja_conferido"))
            _nome = (_s.get("produto_nome") or _s.get("sku_principal") or "")[:42]
            _cli = (_s.get("cliente_nome") or "")[:22]
            _img = _s.get("imagem_url") or ""
            # Avisa ja na busca quando o pedido tem mais de uma peca na mesma
            # etiqueta — o operador ve antes mesmo de abrir a ficha.
            _n_itens = len(db.desserializar_itens(_s))
            _rot = f"{'✅' if _feito else '📦'} {_trk}"
            if _n_itens > 1:
                _rot += f" · ⚠️ {_n_itens} ITENS"
            if _nome:
                _rot += f" · {_nome}"
            if _cli:
                _rot += f" · {_cli}"

            # Miniatura da variacao vendida ao lado do botao: confirmacao
            # VISUAL da peca (cor/kit) antes mesmo de resolver — e' a mesma
            # foto que o cliente viu no anuncio.
            _c_img, _c_btn = st.columns([1, 9], vertical_alignment="center")
            with _c_img:
                if _img:
                    st.image(_img, width=52)
                else:
                    st.markdown(
                        "<div style='width:52px;height:52px;border-radius:8px;"
                        "background:#1e293b;display:flex;align-items:center;"
                        "justify-content:center;font-size:20px;'>📦</div>",
                        unsafe_allow_html=True,
                    )
            with _c_btn:
                _clicou = st.button(
                    _rot, key=f"sug_{_ciclo}_{_trk}", use_container_width=True,
                    type="secondary" if _feito else "primary",
                )
            if _clicou:
                _processar_codigo(_trk)
                # Nao da pra zerar `inp_busca_parcial` direto: o Streamlit
                # proibe escrever na chave de um widget ja instanciado no
                # mesmo run. Marca a intencao e o proximo run recria o campo
                # com key nova (vazio) antes de qualquer widget existir.
                st.session_state.busca_parcial_ciclo = (
                    st.session_state.get("busca_parcial_ciclo", 0) + 1
                )
                st.rerun()
    else:
        st.caption(f"🔎 Nenhum pedido encontrado com **{_frag.upper()}**.")

# ------------------------------------------------------------------ #
# FICHA DO PRODUTO — aparece abaixo da camera assim que le
# ------------------------------------------------------------------ #
if tem_leitura:
    # Ancora: apos a leitura a pagina rola sozinha ate aqui.
    st.markdown('<div id="ficha-produto"></div>', unsafe_allow_html=True)
    components.html(
        """
        <script>
        (function () {
          try {
            const alvo = window.parent.document.getElementById('ficha-produto');
            if (alvo) {
              setTimeout(function () {
                alvo.scrollIntoView({ behavior: 'smooth', block: 'start' });
              }, 120);
            }
          } catch (e) {}
        })();
        </script>
        """,
        height=0,
    )

st.divider()

if res and res.get("encontrado"):
    badge = tag_canal(res.get("canal") or "")
    conferido = res.get("conferido_hoje", False)
    # Primeiro nome, sem mascarar (decisao do Jota 2026-08-03): na bancada o
    # nome ajuda a casar a caixa com a etiqueta, e o sobrenome nao acrescenta.
    primeiro_nome = (res.get("cliente") or "").strip().split(" ")[0] or "—"

    if res.get("cancelado"):
        # ----- 🚨 PEDIDO CANCELADO — NÃO DESPACHAR -----
        canal_nome = res.get("canal") or "plataforma"
        cancelado_em = res.get("cancelado_em") or "—"
        if cancelado_em != "—":
            try:
                import datetime as _dt_cancel
                cancelado_em = _dt_cancel.datetime.fromisoformat(cancelado_em).strftime("%d/%m/%Y %H:%M")
            except (ValueError, TypeError):
                pass
        st.markdown(
            f"""
            <div class="scanner-card-cancelado">
                <div style="display:flex; justify-content:space-between; align-items:center;">
                    <span class="scanner-titulo-cancelado">🚨 PEDIDO CANCELADO — NÃO ENVIAR</span>
                    {badge}
                </div>
                <div class="scanner-linha" style="margin-top:12px;">
                    <span class="scanner-label">Produto</span><br>
                    <b style="font-size:17px;">{res.get('produto') or res.get('modelo') or '—'}</b>
                </div>
                <div class="scanner-linha"><span class="scanner-label">SKU</span><br><code>{res.get('sku') or '—'}</code></div>
                <div class="scanner-linha"><span class="scanner-label">Cliente</span><br>{primeiro_nome}</div>
                <div class="scanner-linha"><span class="scanner-label">CEP</span><br>{mascarar_cep(res.get('cep'))}</div>
                <div class="scanner-linha"><span class="scanner-label">Cancelado em</span><br>{cancelado_em}</div>
                <div class="scanner-linha"><span class="scanner-label">Status</span><br>
                    <span class="scanner-status-cancelado">CANCELADO ({canal_nome})</span></div>
                <div class="scanner-aviso-cancelado">
                    ⚠️ Este pedido foi cancelado pelo comprador ou pela plataforma.
                    <b>NÃO DESPACHAR.</b>
                </div>
            </div>
            """,
            unsafe_allow_html=True,
        )

        col_c1, col_c2 = st.columns(2)
        with col_c1:
            st.markdown(
                '<a class="btn-cancelado" href="?acao=cancelado_ok">⚠️ MARCAR COMO CONFERIDO (CANCELADO)</a>',
                unsafe_allow_html=True,
            )
        with col_c2:
            st.markdown(
                '<a class="btn-ignorar" href="?acao=ignorar">📷 LER OUTRO</a>',
                unsafe_allow_html=True,
            )
    else:
        if conferido:
            st.warning("⚠️ Este tracking JÁ FOI CONFERIDO hoje. Confira se não é caixa repetida.")

        if res.get("status_pedido") == "NAO_VERIFICADO":
            st.warning(res.get("alerta") or "⚠️ Status do pedido não verificado. Confirme antes de enviar.")

        # Cor do destaque conforme o genero da peca (pedido do Jota): as femininas
        # saem em rosa e as masculinas em verde, pra diferenciar de relance na
        # bancada sem precisar ler o texto.
        _fem = res.get("genero") == "fem"
        cor_destaque = "#f472b6" if _fem else "#22c55e"
        borda_destaque = "#9d174d" if _fem else "#334155"
        fundo_destaque = "#2a0a1c" if _fem else "#0f172a"

        modelo = res.get("modelo") or "—"
        spu = res.get("spu") or ""

        # Foto da variacao vendida (a mesma do anuncio). Confere de relance se a
        # peca na mao bate com o que o cliente comprou — cor e kit errados saltam
        # aos olhos na imagem antes de qualquer leitura de texto.
        _img_ficha = res.get("imagem_url") or ""
        _bloco_img = (
            f'<img src="{_img_ficha}" alt="Foto do produto vendido" '
            f'style="width:96px;height:96px;object-fit:cover;border-radius:10px;'
            f'border:2px solid {borda_destaque};flex-shrink:0;">'
            if _img_ficha else ""
        )

        # -------------------------------------------------------------- #
        # PEDIDO MULTI-ITEM — varias pecas na MESMA etiqueta.
        # Fica DENTRO do card verde, no topo: a ficha abaixo mostra so a peca
        # principal, e sem esta lista a bancada fecha caixa faltando item
        # (incidente AP341455035BR: 4 itens, aparecia 1).
        # -------------------------------------------------------------- #
        _itens_ped = res.get("itens") or []

        # ---------------------------------------------------------------- #
        # 🔴 MESMO KIT COMPRADO N VEZES — o caso que passava batido
        #
        # O bloco vermelho abaixo so' dispara com `len(itens) > 1`, ou seja,
        # LINHAS diferentes. Mas o cliente que compra 2x o MESMO kit gera UMA
        # linha com `quantidade: 2` -- `len()` continua 1, nenhum alerta
        # aparecia, e o card grande mostrava so' "Kit 3 / Preto".
        # A bancada embalava UM (pedido 530 / 2608259F0CQBNS, achado 25/08).
        #
        # Historico: 8 pedidos assim desde maio, 10 pecas que poderiam ter
        # faltado -- um deles com quantidade 4.
        #
        # ⚠️ CALCULADO AQUI (soma de `quantidade`) — nao a partir de
        # `res["alerta_volume"]`. Motivo: `alerta_volume` vem do INDICE
        # (gravado quando o populator rodou), pode estar VAZIO se o pedido
        # entrou no indice antes desta coluna existir ou antes do proximo
        # "Atualizar Base". Somar aqui e' auto-suficiente e nunca falha por
        # indice desatualizado. O papel de `alerta_volume` e' outro: ele vem
        # da MESMA funcao (`core_separacao.processar_batch_picking`) que a
        # Esteira usa pra classificar, entao os dois sistemas NUNCA discordam
        # sobre o criterio — so' pode haver defasagem de quando cada um
        # rodou por ultimo, nunca de regra (Jota, 25/08: "importar a mesma
        # lista de separação... conjuntos com mais de 1 item").
        _volumes = sum(int(i.get("quantidade") or 1) for i in _itens_ped) or 1
        _card_volumes = ""
        if _volumes > 1:
            _card_volumes = (
                '<div style="flex:1; min-width:130px; background:#450a0a;'
                'border:3px solid #ef4444; border-radius:10px; padding:12px 16px;">'
                '<div class="scanner-label" style="color:#fca5a5;">Volumes</div>'
                f'<div style="font-size:34px; font-weight:900; color:#fecaca;'
                f'line-height:1.1;">{_volumes}x</div></div>'
            )

        _bloco_itens = ""
        if _volumes > 1 and len(_itens_ped) <= 1:
            # Uma linha só, mas várias unidades: aviso próprio, porque o
            # bloco de multi-item abaixo não cobre este caso.
            _it0 = _itens_ped[0] if _itens_ped else {}
            _bloco_itens = (
                '<div style="background:#450a0a;border:3px solid #ef4444;'
                'border-radius:10px;padding:16px;margin:14px 0;">'
                '<div style="font-size:26px;font-weight:900;color:#fca5a5;">'
                f'⚠️ {_volumes} UNIDADES DO MESMO KIT</div>'
                '<div style="font-size:16px;color:#fecaca;margin-top:6px;">'
                f'O cliente comprou <b>{_volumes}x</b> '
                f'{_it0.get("sku") or res.get("sku") or ""}. '
                f'Coloque <b>{_volumes} kits</b> na caixa, não um.</div></div>'
            )
        elif len(_itens_ped) > 1:
            _linhas = []
            for _i, _it in enumerate(_itens_ped, 1):
                _im = _it.get("imagem_url") or ""
                _var = _it.get("variacao") or _it.get("cor") or ""
                _qtd = int(_it.get("quantidade") or 1)
                _thumb = (
                    f'<img src="{_im}" style="width:54px;height:54px;object-fit:cover;'
                    f'border-radius:8px;flex-shrink:0;">'
                    if _im else
                    '<div style="width:54px;height:54px;border-radius:8px;background:#1e293b;'
                    'display:flex;align-items:center;justify-content:center;font-size:22px;'
                    'flex-shrink:0;">📦</div>'
                )
                _linhas.append(
                    f'<div style="display:flex;gap:10px;align-items:center;padding:8px 10px;'
                    f'background:rgba(0,0,0,.25);border-radius:8px;margin-bottom:6px;">'
                    f'{_thumb}'
                    f'<div style="min-width:0;">'
                    f'<div style="font-size:15px;font-weight:700;color:#fecaca;">'
                    f'{_i}. {_it.get("sku") or "—"}'
                    + (f' · {_var}' if _var else '')
                    + (f' · {_qtd}x' if _qtd > 1 else '')
                    + '</div>'
                    f'<div style="font-size:12px;color:#cbd5e1;overflow:hidden;'
                    f'text-overflow:ellipsis;white-space:nowrap;">'
                    f'{(_it.get("nome") or "")[:70]}</div>'
                    f'</div></div>'
                )
            _total_un = sum(int(i.get("quantidade") or 1) for i in _itens_ped)
            _bloco_itens = (
                '<div style="background:#450a0a;border:2px solid #ef4444;border-radius:10px;'
                'padding:14px 16px;margin:14px 0;">'
                f'<div style="font-size:19px;font-weight:800;color:#fca5a5;margin-bottom:4px;">'
                f'⚠️ PEDIDO COM {len(_itens_ped)} ITENS — SEPARE TODOS</div>'
                f'<div style="font-size:13px;color:#fecaca;margin-bottom:10px;">'
                f'Mesma etiqueta, <b>{_total_un} volume(s)</b> na caixa. '
                'Confira item por item antes de fechar.</div>'
                + "".join(_linhas) +
                '</div>'
            )

        # Ficha grande: modelo, cor e quantidade em destaque, porque esta parte e'
        # lida de relance com a caixa na mao.
        st.markdown(
            f"""
            <div class="scanner-card-ok">
                <div style="display:flex; justify-content:space-between; align-items:center;">
                    <span class="scanner-titulo">🟢 SEPARAR ESTE PEDIDO</span>
                    {badge}
                </div>{_bloco_itens}
                <div style="display:flex; gap:10px; flex-wrap:wrap; margin:16px 0 12px; align-items:stretch;">{_bloco_img}<div style="flex:1; min-width:150px; background:{fundo_destaque}; border:1px solid {borda_destaque}; border-radius:10px; padding:12px 16px;">
                        <div class="scanner-label">Produto</div>
                        <div style="font-size:24px; font-weight:800; color:{cor_destaque}; line-height:1.15;">{modelo}</div>
                        <div style="font-size:13px; color:#cbd5e1; margin-top:6px; letter-spacing:.5px;">
                            SPU <b style="color:#f8fafc;">{spu or '—'}</b>
                        </div>
                    </div>
                    <div style="flex:1; min-width:110px; background:{fundo_destaque}; border:1px solid {borda_destaque}; border-radius:10px; padding:12px 16px;">
                        <div class="scanner-label">Tamanho</div>
                        <div style="font-size:24px; font-weight:800; color:{cor_destaque}; line-height:1.2;">{res.get('tamanho') or '—'}</div>
                    </div>
                    <div style="flex:1; min-width:130px; background:{fundo_destaque}; border:1px solid {borda_destaque}; border-radius:10px; padding:12px 16px;">
                        <div class="scanner-label">Cor</div>
                        <div style="font-size:22px; font-weight:800; color:{cor_destaque}; line-height:1.2;">{res.get('cor') or '—'}</div>
                    </div>
                    <div style="flex:1; min-width:120px; background:{fundo_destaque}; border:1px solid {borda_destaque}; border-radius:10px; padding:12px 16px;">
                        <div class="scanner-label">Kit</div>
                        <div style="font-size:24px; font-weight:800; color:{cor_destaque};">{res.get('kit') or 'Unitário'}</div>
                    </div>
                    {_card_volumes}
                </div>
                <div class="scanner-linha" style="color:#cbd5e1;">{res.get('produto') or '—'}</div>
                <div class="scanner-linha"><span class="scanner-label">SKU</span><br><code>{res.get('sku') or '—'}</code></div>
                <div class="scanner-linha"><span class="scanner-label">Cliente</span><br>{primeiro_nome}</div>
                <div class="scanner-linha"><span class="scanner-label">CEP</span><br>{mascarar_cep(res.get('cep'))}</div>
                <div class="scanner-linha"><span class="scanner-label">Tracking</span><br><code>{res.get('tracking') or '—'}</code></div>
                <div class="scanner-linha"><span class="scanner-label">Pedido e-commerce</span><br>{res.get('pedido_ecommerce') or '—'}</div>
            </div>
            """,
            unsafe_allow_html=True,
        )

        st.info("Confira o produto, a cor e a quantidade antes de fechar a caixa.")

        # -------------------------------------------------------------- #
        # DUPLA CONFERENCIA — bipar a etiqueta de SKU da peca (opcional).
        # Fica sempre visivel, mas nao bloqueia: com pouco pedido, ou quando a
        # peca nao tem etiqueta de SKU, o operador segue direto no
        # "CONFERIDO -> PROXIMO" como sempre fez.
        # -------------------------------------------------------------- #
        val = st.session_state.scanner_validacao

        with st.form("form_validacao_sku", clear_on_submit=True):
            st.markdown("**🏷️ Confirmar a peça** — bipe a etiqueta de SKU do produto")
            cod_peca = st.text_input(
                "Código da peça",
                placeholder="Ex: MEINVMAY1013540PRE  ou  TOPTAY016-AZUL",
                label_visibility="collapsed",
                key="inp_validacao",
            )
            if st.form_submit_button("🔎 Validar peça", use_container_width=True):
                if cod_peca and cod_peca.strip():
                    _validar_produto(cod_peca)
                    st.rerun()

        if val:
            if val["ok"]:
                st.success(f"**{val['titulo']}**\n\n{val['detalhe']}")
            elif val["nivel"] == "sem_dados":
                st.warning(f"**{val['titulo']}**\n\n{val['detalhe']}")
            else:
                st.error(
                    f"**{val['titulo']}**\n\n{val['detalhe']}\n\n"
                    f"- Pedido: `{val['esperado'] or '—'}`\n"
                    f"- Etiqueta lida: `{val['lido'] or '—'}`"
                )
                st.caption(
                    "Confira a peça na caixa. Se estiver errada, troque antes de despachar."
                )

        col_prox, col_pular = st.columns(2)
        with col_prox:
            # Rotulo muda conforme a validacao pra dar a confirmacao explicita
            # que o Jota pediu ("pode despachar") sem criar um botao a mais.
            if val and val.get("ok"):
                rotulo_ok = "✅ PODE DESPACHAR → PRÓXIMO"
            elif val and val["nivel"] not in ("sem_dados",):
                rotulo_ok = "⚠️ CONFERIR MESMO ASSIM → PRÓXIMO"
            else:
                rotulo_ok = "✅ CONFERIDO → PRÓXIMO"

            if st.button(rotulo_ok, type="primary", use_container_width=True):
                db.registrar_conferencia(
                    res.get("tracking", ""),
                    res.get("pedido_ecommerce", ""),
                    res.get("canal", ""),
                    res.get("sku", ""),
                    sku_validado=(val or {}).get("lido", ""),
                    validacao_nivel=(val or {}).get("nivel", ""),
                )
                st.session_state.scanner_sessao_conferidos += 1
                if val and val.get("ok"):
                    st.session_state.scanner_sessao_validados += 1
                elif val and val["nivel"] not in ("sem_dados",):
                    st.session_state.scanner_sessao_divergencias += 1
                _limpar_leitura()
                st.rerun()
        with col_pular:
            if st.button("⚠️ PULAR", use_container_width=True):
                st.session_state.scanner_sessao_pulados += 1
                _limpar_leitura()
                st.rerun()

elif res and res.get("codigo_invalido"):
    # ----- 🟠 CODIGO LIDO NAO SERVE (chave de NF-e, CEP...) -----
    # Nao adianta mandar "atualizar base": o problema nao e' base desatualizada,
    # e' que a pistola pegou o codigo errado da etiqueta.
    st.markdown(
        f"""
        <div class="scanner-card-erro" style="background-color:#431407; border-color:#ea580c;">
            <div class="scanner-titulo">🟠 CÓDIGO ERRADO DA ETIQUETA</div>
            <div class="scanner-linha">{res.get('motivo')}</div>
            <div class="scanner-linha" style="color:#94a3b8;">
                Lido: <code>{codigo_atual}</code>
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )
    if st.button("📷 LER O CÓDIGO DE RASTREIO", type="primary", use_container_width=True):
        _limpar_leitura()
        st.rerun()

elif tem_leitura:
    # ----- 🔴 NAO ENCONTRADO -----
    st.markdown(
        f"""
        <div class="scanner-card-erro">
            <div class="scanner-titulo">🔴 NÃO ENCONTRADO</div>
            <div class="scanner-linha">
                Nenhum pedido casou com <code>{codigo_atual}</code>.
            </div>
            <div class="scanner-linha" style="color:#94a3b8;">
                Causa mais comum: <b>venda nova</b> — a etiqueta foi gerada depois da
                última sincronização, então o rastreio ainda não está na base.
                Toque em <b>🔄 Atualizar e tentar de novo</b> abaixo.
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    col_re, col_outro = st.columns(2)
    with col_re:
        # Atalho que resolve o caso comum sem sair da tela: sincroniza e ja
        # re-resolve o MESMO codigo, sem precisar bipar a etiqueta outra vez.
        if st.button("🔄 ATUALIZAR E TENTAR DE NOVO", type="primary", use_container_width=True):
            with st.spinner("Buscando vendas novas nas APIs…"):
                try:
                    populator.popular_todos(force=True)
                except Exception as e:
                    st.error(f"Falha ao atualizar: {e}")
            _processar_codigo(codigo_atual)
            st.rerun()
    with col_outro:
        if st.button("📷 LER OUTRO CÓDIGO", use_container_width=True):
            _limpar_leitura()
            st.rerun()

else:
    st.caption("Aguardando leitura… aponte a câmera para a etiqueta.")

# ------------------------------------------------------------------ #
# Entrada alternativa + encerramento
# ------------------------------------------------------------------ #
with st.expander("🖼️ Enviar foto da etiqueta (se a câmera falhar)", expanded=False):
    arquivo = st.file_uploader(
        "Foto da etiqueta",
        type=["png", "jpg", "jpeg", "webp", "bmp"],
        key="up_etiqueta",
        label_visibility="collapsed",
    )
    if arquivo is not None:
        cod_arq = decodificar_imagem(arquivo.getvalue())
        if cod_arq:
            if st.session_state.scanner_upload_codigo != cod_arq:
                st.session_state.scanner_upload_codigo = cod_arq
                _processar_codigo(cod_arq)
                st.rerun()
        else:
            st.warning("Nenhum código encontrado na imagem enviada.")

# ------------------------------------------------------------------ #
# 🛡️ Comprovante de conferência — defesa contra falsa denúncia de "não enviei".
# Usa o `conferido_em` que o Scanner JÁ grava em toda bipagem (data + hora com
# precisão de segundo). Paliativo enquanto a webcam não chega (M4 do plano):
# quando ela chegar, este mesmo painel passa a apontar o trecho do vídeo.
# ------------------------------------------------------------------ #
st.divider()

with st.expander("🛡️ Comprovante de conferência (defesa contra falsa denúncia)",
                 expanded=False):
    st.caption(
        "Busque pelo código de rastreio ou número do pedido para gerar o "
        "comprovante com a data e hora exatas em que ele foi conferido aqui."
    )
    _termo = st.text_input(
        "Rastreio ou número do pedido",
        key="inp_comprovante",
        placeholder="Ex: BR2644727324797  ou  2608101WN8S06J",
    )
    if _termo and _termo.strip():
        _achados = comprovante.buscar(_termo, limite=20)
        if not _achados:
            st.warning(
                "Nenhuma conferência encontrada para esse código. "
                "Isso significa que o pedido não passou pelo Scanner — "
                "não que ele não foi enviado."
            )
        else:
            st.success(f"{len(_achados)} conferência(s) encontrada(s).")
            for _r in _achados:
                st.markdown(
                    f"**{comprovante._fmt_br(_r['conferido_em'])}** · "
                    f"`{_r['tracking']}` · {(_r.get('sku_principal') or '—')}"
                )
                st.code(comprovante.texto_defesa(_r), language=None)

    st.divider()
    _c1, _c2 = st.columns([2, 3])
    with _c1:
        _dia_exp = st.date_input("Exportar conferências do dia",
                                 key="dt_export_comprovante")
    with _c2:
        st.caption("Gera um CSV com todas as conferências do dia escolhido, "
                   "com data e hora de cada uma.")
        if st.button("📄 EXPORTAR CSV DO DIA", use_container_width=True):
            _regs = comprovante.buscar(dia=str(_dia_exp), limite=1000)
            if not _regs:
                st.warning("Nenhuma conferência nesse dia.")
            else:
                _arq = comprovante.exportar_csv(_regs)
                if _arq:
                    st.success(f"✅ {len(_regs)} registro(s) → `{_arq}`")
                else:
                    st.error("Falha ao exportar.")

    # ---- retenção: guarda 30 dias, depois pode apagar ----
    _idade = comprovante.contar_por_idade()
    st.divider()
    st.caption(
        f"📦 {_idade['total']} comprovante(s) guardado(s) · retenção "
        f"{_idade['dias_retencao']} dias · {_idade['vencidos']} já passaram do prazo"
    )
    if _idade["vencidos"]:
        if st.button(f"🗑️ APAGAR {_idade['vencidos']} COMPROVANTE(S) VENCIDO(S)",
                     use_container_width=True):
            _n = comprovante.limpar_antigos()
            st.success(f"🗑️ {_n} comprovante(s) com mais de "
                       f"{_idade['dias_retencao']} dias removido(s).")
            st.rerun()

# ------------------------------------------------------------------ #
# 🐛 Relatar erro / melhoria — grava em SCANNER_ERROS.md com o contexto
# do item que está em tela AGORA (código lido, pedido, SKU, validação).
# Fica no rodapé de propósito: sempre acessível, nunca no caminho da bipagem.
# ------------------------------------------------------------------ #
st.divider()

if st.session_state.get("scanner_msg_feedback"):
    st.success(st.session_state.scanner_msg_feedback)
    st.session_state.scanner_msg_feedback = ""

_abertos = feedback.contar_abertos()

if not st.session_state.scanner_form_feedback:
    _rotulo_fb = "🐛 RELATAR ERRO / MELHORIA"
    if _abertos:
        _rotulo_fb += f"  ({_abertos} em aberto)"
    if st.button(_rotulo_fb, use_container_width=True):
        st.session_state.scanner_form_feedback = True
        st.rerun()
else:
    with st.form("form_feedback", clear_on_submit=True):
        st.markdown("**🐛 O que aconteceu?**")
        if res:
            _ident = (res.get("tracking") or codigo_atual or "—")
            _sku_ctx = res.get("sku") or "—"
            st.caption(f"Será anexado o item em tela: `{_ident}` · SKU `{_sku_ctx}`")
        else:
            st.caption("Nenhum item em tela — será registrado sem contexto de pedido.")

        _tipo = st.radio(
            "Tipo", ["Erro", "Melhoria"], horizontal=True,
            label_visibility="collapsed", key="rb_tipo_fb",
        )
        _texto = st.text_area(
            "Descrição",
            placeholder="Ex: bipei a etiqueta e apareceu o pedido errado / "
                        "seria bom tocar um som ao confirmar",
            label_visibility="collapsed",
            key="ta_feedback",
            height=110,
        )
        _c1, _c2 = st.columns(2)
        with _c1:
            _enviar = st.form_submit_button(
                "💾 Salvar relato", type="primary", use_container_width=True)
        with _c2:
            _cancelar = st.form_submit_button("Cancelar", use_container_width=True)

    if _enviar:
        if not (_texto or "").strip():
            st.warning("Escreva o que aconteceu antes de salvar.")
        else:
            _ctx = feedback.montar_contexto(
                codigo_lido=codigo_atual,
                resultado=res,
                validacao=st.session_state.get("scanner_validacao"),
                extras={
                    "conferidos_na_sessao": st.session_state.scanner_sessao_conferidos,
                    "total_indice": stats.get("total_indice"),
                },
            )
            _id = feedback.registrar(
                _texto, _ctx, tipo="erro" if _tipo == "Erro" else "melhoria")
            if _id:
                st.session_state.scanner_msg_feedback = (
                    f"✅ Relato #{_id} salvo em `SCANNER_ERROS.md` com o contexto do item."
                )
            else:
                st.session_state.scanner_msg_feedback = "❌ Falha ao salvar o relato."
            st.session_state.scanner_form_feedback = False
            st.rerun()
    elif _cancelar:
        st.session_state.scanner_form_feedback = False
        st.rerun()

st.divider()

# Entrada da conferencia final: carrega a lista do Olist e troca de modo.
if st.button("🔍 VERIFICAR EXPEDIÇÃO (conferência final)",
             use_container_width=True, type="secondary"):
    with st.spinner("Carregando pedidos prontos para envio no Olist…"):
        try:
            para, ign = expedicao.carregar_esperados_olist()
            # Enriquece com o indice local (traz produto/cliente ja resolvidos)
            idx = {e["tracking"]: e for e in expedicao.carregar_esperados()
                   if e.get("tracking")}
            for p in para:
                extra = idx.get(p.get("tracking"))
                if extra:
                    p.setdefault("produto", extra.get("produto"))
                    p.setdefault("sku", extra.get("sku"))
                    p["cliente"] = p.get("cliente") or extra.get("cliente")
            st.session_state.exp_esperados = para
            st.session_state.exp_ignorados = ign
            st.session_state.exp_bipados = {}
            st.session_state.exp_fora_lista = []
            st.session_state.exp_ultimo = None
            st.session_state.exp_relatorio = None
            st.session_state.exp_modo = True
        except Exception as e:
            st.error(f"Falha ao carregar a lista de expedição: {e}")
    st.rerun()

# Histórico dos relatórios acessível SEM entrar no modo conferência (que
# leva ~20s carregando o Olist só pra consultar algo já salvo).
_qtd_rel_home = expedicao.contar_relatorios()
if _qtd_rel_home:
    with st.expander(f"📚 Relatórios de expedição salvos ({_qtd_rel_home})",
                     expanded=False):
        st.markdown(expedicao.ler_relatorios())
        st.divider()
        st.caption("Ao zerar, uma cópia é gravada como "
                   "`EXPEDICAO_RELATORIOS_backup_<data>.md`.")
        if st.checkbox("Confirmo que as pendências foram sanadas",
                       key="ck_zerar_rel_home"):
            if st.button("🗑️ ZERAR RELATÓRIOS", use_container_width=True,
                         key="btn_zerar_home"):
                if expedicao.limpar_relatorios():
                    st.session_state.exp_msg = "🗑️ Relatórios zerados (backup gravado)."
                st.rerun()

if st.button("🏁 ENCERRAR EXPEDIÇÃO", use_container_width=True):
    st.session_state.scanner_encerrado = True
    st.rerun()
