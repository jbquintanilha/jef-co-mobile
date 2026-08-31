# ==============================================================================
# NOME DO SCRIPT: 17_Lista_Separacao.py
# DESCRICAO: Esteira de Expedicao J&F Co. — 7 fases sequenciais
# FUNCAO: Guia a expedicao do inicio ao fim, na ordem fisica real do trabalho.
#         Cada aba e' uma fase; o botao "avancar" leva para a proxima.
# STATUS: ATIVO
# VERSAO: 2.0
# DATA: 16/08/2026
# AUTOR: Terminador (001) / Claude  (v1.0: Violino/Gemini CLI)
# REF: fases definidas pelo Jota em 2026-08-16
# ==============================================================================
"""
As 7 fases (ordem definida pelo Jota):

    1  Baixar etiquetas e atualizar sistemas
    2  Separar itens          -> agrupado por SKU ATOMO, nao por kit
    3  Unificar etiquetas + cartoes de agradecimento
    4  Etiquetas 40x25 de identificacao  (OPCIONAL — botao, nao obrigacao)
    5  Embalar itens          -> com opcao de iniciar gravacao
    6  Bipagem                -> bipa a etiqueta e vai montando
    7  Conferencia final      -> duplicata, faltante, divergencia

⚠️ O painel de cruzamento das 4 fontes fica VISIVEL EM TODAS as fases.
   Politica do Jota: **alarme, nunca bloqueio**. O operador decide.

⚠️ Fase 2 agrupa por ATOMO porque a coleta e' fisica: quem busca no estoque
   pega "47 meias pretas 40/46", nao "8 kits de 6". O SKU V5 ja' carrega essa
   informacao — core_separacao_atomos.decompor_sku() so' le.
"""

import os
import sys
from datetime import datetime
from pathlib import Path

_RAIZ = Path(__file__).resolve().parent.parent
if str(_RAIZ) not in sys.path:
    sys.path.insert(0, str(_RAIZ))

import streamlit as st
import pandas as pd
import core_separacao as cs

# separador_etiquetas vive em tools/ — sem isto o botao de imprimir quebra
# com ModuleNotFoundError silencioso dentro do try/except.
_TOOLS = _RAIZ / "tools"
if _TOOLS.is_dir() and str(_TOOLS) not in sys.path:
    sys.path.insert(0, str(_TOOLS))


st.set_page_config(
    page_title="Esteira de Expedição — J&F Co.",
    page_icon="📦",
    layout="wide",
)

FASES = [
    "1️⃣ Etiquetas",
    "2️⃣ Separar",
    "3️⃣ Etiq + Cartão",
    "4️⃣ Etiq 40x25",
    "5️⃣ Embalar",
    "6️⃣ Bipagem",
    "7️⃣ Conferência",
]

# ---------------------------------------------------------------------------- #
# Estado
# ---------------------------------------------------------------------------- #
_DEFAULTS = {
    "dados_separacao": None,
    "pedidos_brutos": [],
    "fase_atual": 0,
    "cruzamento": None,
    "atomos_coleta": None,
    "erros": [],
}
for _k, _v in _DEFAULTS.items():
    if _k not in st.session_state:
        st.session_state[_k] = _v


# ---------------------------------------------------------------------------- #
# Coletor de erros — nada falha em silencio
# ---------------------------------------------------------------------------- #
# 🔴 LEI (Jota, 2026-08-16): erro ou coisa nao encontrada APARECE como erro no
# fim da pagina. Nunca engolir. "se ele so' ignorar, tipo esse do top, tem o
# risco de nao ser enviado" — item que o sistema nao entendeu pode sair
# faltando da caixa sem ninguem ver.
def registrar_erro(fase: str, titulo: str, detalhe: str = "",
                   grave: bool = True) -> None:
    """Guarda um problema para o painel do fim da pagina."""
    st.session_state.erros.append({
        "fase": fase,
        "titulo": titulo,
        "detalhe": str(detalhe)[:500],
        "grave": grave,
        "hora": datetime.now().strftime("%H:%M:%S"),
    })


def erro_visivel(fase: str, titulo: str, exc: Exception | str = "",
                 grave: bool = True) -> None:
    """Mostra o erro NA HORA e tambem registra para o painel do fim."""
    texto = f"{type(exc).__name__}: {exc}" if isinstance(exc, Exception) else str(exc)
    (st.error if grave else st.warning)(
        f"**{titulo}**" + (f"\n\n`{texto}`" if texto else ""))
    registrar_erro(fase, titulo, texto, grave)


# Zera a cada recarga da fila — senao acumula erro de lote antigo
def limpar_erros() -> None:
    st.session_state.erros = []


def avancar(destino: int, rotulo: str = "Avançar") -> None:
    """Botao de fim de fase que leva para a proxima.

    🔴 NAO usar com `st.tabs`: o Streamlit nao deixa trocar de aba por codigo,
    entao `fase_atual` mudava mas a tela ficava parada — o botao parecia morto
    (relatado pelo Jota, 2026-08-16). Por isso as fases sao um `st.radio`
    horizontal, que E' controlavel por estado.
    """
    st.divider()
    if st.button(f"➡️ {rotulo}", type="primary", use_container_width=True,
                 key=f"btn_avancar_{destino}"):
        # ⚠️ Nao escrever em `fase_atual` aqui: e' a key do radio, e o
        # Streamlit levanta StreamlitAPIException. O destino fica pendente e
        # o topo da pagina o aplica no proximo ciclo.
        st.session_state.proxima_fase = destino
        st.rerun()


st.title("📦 Esteira de Expedição — J&F Co.")
st.caption("As 7 fases na ordem física do trabalho. Cada aba tem o botão de avançar no fim.")

# ---------------------------------------------------------------------------- #
# Configuracao da fila
# ---------------------------------------------------------------------------- #
# ⚠️ Sem barra lateral: a fila e' sempre "Em separacao" e o botao de atualizar
# mora na fase 2, junto da lista que ele monta (Jota, 2026-08-16 — "nao tem por
# que estar ali... basta ter um botao de atualizar separacao").
# 🔴 SITUACAO 4 = "Preparando envio" — a fila do que AINDA NAO foi impresso.
#
# Regra do Jota (2026-08-19):
#   "ocorre q por vezes depois de lancar 20 etiquetas sai 5 vendas, ai ele
#    imprime as 20 novamente... assim eu consigo enviar la e imprimir apenas
#    estas q falei"
#
# O pedido MUDA de situacao quando a etiqueta e' emitida. Entao a propria
# situacao 4 e' o filtro do "que falta": pedido ja' impresso sai da lista
# sozinho, e as 5 vendas novas aparecem sem arrastar as 20 anteriores.
#
# Medido em producao (2026-08-19), confirmado na tela do Olist ("preparando
# envio 08"):
#     sit 2 -> 43 pedidos   (acumula velho/cancelado — o num 11 e' de 25/06)
#     sit 4 ->  8 pedidos   <- a fila a imprimir
#     sit 7 ->  8 pedidos   (Pronto para envio: JA tem etiqueta)
#
# 4 = Preparando envio (sem etiqueta emitida no Olist)
# 7 = Pronto para envio (com etiqueta emitida no Olist / gerada via API)
# Sincroniza AMBAS para que nenhum pedido fique de fora do lote de separação e bipagem.
SITUACAO_PADRAO = [4, 7]
LIMITE_PEDIDOS = 100            # o Olist recusa acima de 100

situacoes_sel = SITUACAO_PADRAO
max_pedidos = LIMITE_PEDIDOS

# Sobra da sidebar antiga: se ficou no estado, apaga para nao confundir
st.session_state.pop("situacoes_sel", None)


def atualizar_separacao(*, reset: bool = False) -> None:
    """Sincroniza a fila e remonta a lista de atomos.

    `reset=True` descarta o cache e rebaixa tudo — o botao vermelho.
    """
    limpar_erros()
    msg = ("Zerando o cache e baixando a fila inteira..."
           if reset else "Buscando só os pedidos que faltam...")

    with st.spinner(msg):
        try:
            import core_sync_expedicao as sync
            r = sync.sincronizar(situacoes_sel, reset=reset,
                                 max_pedidos=max_pedidos)
            pedidos = r["pedidos"]

            # A situacao 4 pode estar vazia (ver SITUACAO_FALLBACK). Sem isto
            # a tela dizia "0 pedidos" com 64 pedidos abertos no Olist.
            if not pedidos:
                r = sync.sincronizar(SITUACAO_FALLBACK, reset=reset,
                                     max_pedidos=max_pedidos)
                pedidos = r["pedidos"]
                if pedidos:
                    st.info(
                        "ℹ️ Nenhum pedido em **Preparando envio** — usando "
                        "**Em separação + Pronto para envio**. "
                        "Os que já têm etiqueta emitida aparecem com 🏷️."
                    )

            st.session_state["ultimo_sync"] = r

            if r.get("falhas"):
                registrar_erro(
                    "2️⃣ Separar",
                    f"{len(r['falhas'])} pedido(s) não detalhado(s)",
                    "IDs: " + ", ".join(r["falhas"][:20])
                    + " — entraram na fila só com os dados do resumo.",
                )

            if not pedidos:
                st.session_state.dados_separacao = None
                st.session_state.pedidos_brutos = []
                st.session_state.atomos_coleta = None
                return

            st.session_state.pedidos_brutos = pedidos
            st.session_state.dados_separacao = cs.processar_batch_picking(pedidos)

            try:
                import core_separacao_atomos as csa
                atomos_ = csa.consolidar_atomos(
                    st.session_state.dados_separacao["lista_coleta"])
                st.session_state.atomos_coleta = atomos_

                # SKU que o parser nao entendeu -> erro no painel do fim
                for prob in csa.problemas_da_coleta(atomos_):
                    registrar_erro(
                        "2️⃣ Separar",
                        f"SKU não reconhecido: {prob['atomo']} "
                        f"(contado como {prob['qtd']} un)",
                        " · ".join(prob["problemas"]),
                        grave=True,
                    )
            except Exception as exc:
                st.session_state.atomos_coleta = None
                erro_visivel("2️⃣ Separar",
                             "Decomposição em átomos falhou — lista sai por "
                             "SKU de venda, não por átomo", exc)

        except Exception as exc:
            erro_visivel("2️⃣ Separar", "Erro ao sincronizar com o Olist", exc)


dados = st.session_state.dados_separacao

# ---------------------------------------------------------------------------- #
# PAINEL DE CRUZAMENTO — visivel em TODAS as fases
# ---------------------------------------------------------------------------- #
# ⚠️ Fica fora das abas de proposito. A etiqueta nao sabe o que tem na caixa;
# so' o cruzamento Olist x NF x Etiqueta x Marketplace prova o item. O Jota
# definiu: alarme sempre a' vista, mas NUNCA bloqueia a operacao.
with st.expander("🔎 Cruzamento das 4 fontes — Olist × NF × Etiqueta × Marketplace",
                 expanded=False):
    st.caption(
        "A etiqueta só diz **para onde vai** — não diz o que tem na caixa. "
        "O item verdadeiro vem do pedido e a nota confirma. "
        "**Alarme, nunca bloqueio:** quem decide é você."
    )

    if st.button("🔍 Rodar cruzamento agora", key="btn_cruzar"):
        with st.spinner("Cruzando Olist, TikTok e Shopee..."):
            try:
                import core_cruzamento_expedicao as cce
                st.session_state.cruzamento = cce.cruzar(situacoes=situacoes_sel)
            except Exception as exc:
                erro_visivel("🔎 Cruzamento", "Cruzamento das fontes falhou", exc)

    cruz = st.session_state.cruzamento
    if cruz:
        f = cruz["fontes"]
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Olist", f["olist"])
        c2.metric("TikTok", f["tiktok"])
        c3.metric("Shopee", f["shopee"])
        c4.metric("Divergências", len(cruz["divergencias"]))

        if not cruz["divergencias"]:
            st.success("✅ Nenhuma divergência entre as fontes.")
        else:
            for d in cruz["divergencias"]:
                linha = f"{d['gravidade']} **{d['tipo']}** · `{d['chave']}` — {d['detalhe']}"
                if d["gravidade"] == "🔴":
                    st.error(linha)
                elif d["gravidade"] == "🟡":
                    st.warning(linha)
                else:
                    st.info(linha)
    else:
        st.caption("Clique acima para conferir se as fontes batem.")

st.divider()

# ---------------------------------------------------------------------------- #
# AS 7 FASES
# ---------------------------------------------------------------------------- #
# 🔴 st.radio em vez de st.tabs: `st.tabs` nao pode ser trocada por codigo, e
# o botao "avancar" precisa disso.
#
# ⚠️ O radio escreve direto em `fase_atual` via `key`. Antes ele tinha key
# propria ("nav_fase") e `index=fase_atual`: no rerun o widget restaurava o
# valor ANTIGO dele e desfazia o que o botao avancar tinha acabado de setar —
# o botao parecia morto. Com a key apontando para o mesmo estado, os dois
# caminhos (clicar na fase ou avancar) mexem no mesmo lugar.
# `avancar()` nao pode escrever na chave do widget (o Streamlit proibe), entao
# deixa o destino em `proxima_fase` e o radio o consome aqui, ANTES de existir.
if st.session_state.get("proxima_fase") is not None:
    st.session_state.fase_atual = st.session_state.pop("proxima_fase")

st.radio(
    "Fase:", options=list(range(len(FASES))),
    format_func=lambda i: FASES[i],
    horizontal=True, key="fase_atual", label_visibility="collapsed",
)

st.divider()


# ⚠️ `st.empty().container()` NAO esconde — verificado com AppTest: os dois
# conteudos apareciam na tela. A unica forma confiavel e' nao executar o bloco.
# Por isso cada fase e' guardada por `if fase(N):` mais abaixo.
def fase(indice: int) -> bool:
    """True quando esta fase e' a selecionada."""
    return st.session_state.fase_atual == indice


def _filtro_onda() -> set[str] | None:
    """Os `numero_ecommerce` da onda travada, ou None quando nao ha' trava.

    Existe para as fases que leem `dados_separacao` direto (separacao,
    embalagem) respeitarem a onda sem repetir a consulta ao banco. `None`
    significa "sem trava" -- o chamador mostra tudo, como sempre fez.
    """
    n = st.session_state.get("onda_travada")
    if n is None:
        return None
    import core_ondas_expedicao as ondas
    return ondas.pedidos_da_onda(n)


def _so_da_onda(pedidos: list, filtro: set[str] | None) -> list:
    """Recorta uma lista de pedidos pela onda travada (ou devolve inteira)."""
    if not filtro:
        return pedidos
    return [p for p in pedidos
            if str(p.get("numero_ecommerce") or "").upper() in filtro]


def _widget_ondas(chave: str) -> tuple[list, list, list]:
    """Painel de Ondas de Expedição — seleciona a onda de trabalho e a trava.

    Devolve (_alvo, _pend, _feitos):
      * com onda travada -> `_alvo` sao os pedidos DAQUELA onda;
      * sem trava        -> `_alvo` e' a fila livre (os ainda nao processados),
                            exatamente como funcionava antes.

    Extraido pra funcao em 25/08 pra aparecer TAMBEM na Fase 1 (Jota: "era
    ideal a gente ter logo nesse comeco... eu poder ver a ultima etiqueta
    que ja consta no sistema"), alem da Fase 3 onde ja existia. `chave` (ex:
    "f1", "f3") evita colisao de `key=` entre as duas instancias do widget.

    ## A virada de 27/08

    Antes "onda" queria dizer "ja' processado" — um carimbo. Como as fases so'
    liam os pendentes, pedido marcado sumia da fila e o Comandante so'
    conseguia "travar de fazer as de n para n+frente". Agora a onda e' um LOTE
    de trabalho: trava-se uma e a esteira inteira passa a operar so' sobre
    ela, em qualquer ordem de fase (separar, reimprimir etiqueta, cartao...).

    A trava ESCONDE o resto (decisao do Jota): meia-trava traria de volta a
    confusao de nao saber em que conjunto se esta' mexendo.
    """
    _dados_onda = st.session_state.get("dados_separacao")
    if not _dados_onda or not isinstance(_dados_onda, dict):
        st.caption(
            "🌊 Sincronize a fila (botão abaixo) para ver as ondas já "
            "processadas e marcar até onde você já imprimiu."
        )
        return [], [], []

    try:
        import core_ondas_expedicao as ondas
    except Exception as err:
        st.warning(f"Aviso ao carregar módulo de ondas: {err}")
        return [], [], []

    _todos = (
        _dados_onda.get("pedidos_simples_1un", [])
        + _dados_onda.get("pedidos_simples_multi_un", [])
        + _dados_onda.get("pedidos_multi_itens", [])
    )
    # A marca vale ate' o pedido sair do Olist: quem nao esta' mais na
    # fila pendente e' descartado do banco de ondas.
    try:
        ondas.limpar_ausentes({str(p.get("numero_ecommerce") or "").upper()
                               for p in _todos})
        ondas.marcar(_todos)
    except Exception:
        pass

    _pend = [p for p in _todos if not p.get("onda")]
    _feitos = [p for p in _todos if p.get("onda")]

    # ---------------- Seletor de onda (trava a esteira) ---------------- #
    try:
        _lista = ondas.listar_ondas() if hasattr(ondas, "listar_ondas") else []
    except Exception:
        _lista = []

    _opcoes = [None] + [o.get("onda") for o in _lista if isinstance(o, dict) and "onda" in o]
    _por_num = {o["onda"]: o for o in _lista if isinstance(o, dict) and "onda" in o}

    def _rotulo(n):
        if n is None:
            return f"🔓 Fila livre — {len(_pend)} pendente(s)"
        o = _por_num.get(n, {})
        marca = "✅" if o.get("concluida") else "🔵"
        return (f"{marca} Onda {n} · {o.get('pedidos', 0)} pedidos · "
                f"{o.get('total_fases', 0)}/7 fases")

    st.markdown("#### 🌊 Ondas de expedição")

    _travada = st.selectbox(
        "Onda de trabalho", options=_opcoes, format_func=_rotulo,
        key=f"onda_travada_{chave}",
        help="Ao escolher uma onda, TODAS as fases passam a trabalhar só "
             "com os pedidos dela. Escolha 'Fila livre' para voltar ao "
             "fluxo normal.",
    )
    st.session_state["onda_travada"] = _travada

    if _travada is not None:
        try:
            _num_onda = ondas.pedidos_da_onda(_travada)
        except Exception:
            _num_onda = set()
        _alvo = [p for p in _todos if str(p.get("numero_ecommerce") or "").upper()
                 in _num_onda]
        try:
            _feitas = ondas.fases_da_onda(_travada)
        except Exception:
            _feitas = {}
        _nomes_ok = [FASES[i] for i in sorted(_feitas) if _feitas[i]]
        st.success(
            f"🔒 **Onda {_travada} travada** — {len(_alvo)} pedido(s). "
            "As 7 fases estão operando só sobre ela."
            + (f"\n\nJá concluído: {' · '.join(_nomes_ok)}" if _nomes_ok else "")
        )
        # Marca a fase corrente como feita — e' o que permite retomar a onda
        # depois sem perder de vista o que ja' passou.
        _fase_ix = st.session_state.get("fase_atual", 0)
        _ja_feita = bool(_feitas.get(_fase_ix))
        c_f1, c_f2, c_f3 = st.columns(3)
        with c_f1:
            if st.button(
                    ("↩️ Desmarcar esta fase" if _ja_feita
                     else f"✅ Concluir '{FASES[_fase_ix].split(' ', 1)[-1]}'"),
                    key=f"btn_fase_ok_{chave}", use_container_width=True,
                    type="secondary" if _ja_feita else "primary"):
                ondas.marcar_fase(_travada, _fase_ix, not _ja_feita)
                st.rerun()
        with c_f2:
            if st.button("🏁 Concluir onda inteira", key=f"btn_onda_fim_{chave}",
                         use_container_width=True,
                         help="Marca as 7 fases de uma vez."):
                ondas.concluir(_travada)
                st.rerun()
        with c_f3:
            if st.button("🔄 Reabrir onda", key=f"btn_onda_reabrir_{chave}",
                         use_container_width=True,
                         help="Zera o progresso das fases. Os pedidos "
                              "continuam na onda.",
                         disabled=not _nomes_ok):
                ondas.reabrir(_travada)
                st.rerun()
        st.divider()
        return _alvo, _pend, _feitos

    # ---------------- Fila livre (comportamento de sempre) ---------------- #
    c_o1, c_o2, c_o3 = st.columns([2, 1, 1])
    with c_o1:
        _res = ondas.resumo()
        if _res:
            st.caption("Hoje: " + " · ".join(
                f"**onda {r['onda']}** {r['pedidos']} ped" for r in _res))
        st.caption(
            f"⏳ **{len(_pend)} a processar** · ✅ {len(_feitos)} já em onda"
        )
    with c_o2:
        if st.button(f"💾 Salvar onda {ondas.proxima_onda()}",
                     type="primary", use_container_width=True,
                     key=f"btn_salvar_onda_{chave}",
                     help="Marca os pendentes como processados. Eles "
                          "continuam visíveis, com o número da onda.",
                     disabled=not _pend):
            r_o = ondas.salvar_onda(_pend)
            st.success(f"✅ Onda {r_o['onda']} salva — "
                       f"{r_o['gravados']} pedido(s).")
            st.rerun()
    with c_o3:
        if st.button("↩️ Desfazer última", use_container_width=True,
                     key=f"btn_desfazer_onda_{chave}", disabled=not _res):
            r_d = ondas.desfazer_ultima()
            if r_d["removidos"]:
                st.info(f"Onda {r_d['onda']} desfeita — "
                        f"{r_d['removidos']} pedido(s) voltaram.")
            st.rerun()

    # Onda impressa ANTES deste mecanismo (ou fora do sistema): o
    # operador olha a última etiqueta da pilha e informa o número.
    with st.expander("📌 Já imprimi antes — informar o último pedido processado",
                      expanded=(chave == "f1" and bool(_pend))):
        st.caption(
            "Olhe a **última etiqueta da pilha** que você já imprimiu e "
            "veja o `#` do Olist. Tudo até ele entra numa onda; o resto "
            "fica pendente."
        )
        _nums = sorted(
            int(str(p.get("numero_olist")).lstrip("#"))
            for p in _todos
            if str(p.get("numero_olist") or "").strip().lstrip("#").isdigit()
        )
        if _nums:
            st.caption(f"Nesta fila: **#{_nums[0]}** até **#{_nums[-1]}**")
        c_u1, c_u2 = st.columns([2, 1])
        with c_u1:
            _ult = st.number_input(
                "Último pedido JÁ processado (nº Olist)",
                min_value=0, step=1,
                value=int(_nums[0]) if _nums else 0,
                key=f"num_ultimo_processado_{chave}")
        with c_u2:
            st.write("")
            if st.button("💾 Marcar até aqui", use_container_width=True,
                         key=f"btn_onda_ate_{chave}", disabled=not _ult):
                r_a = ondas.salvar_ate_pedido(_todos, _ult)
                if r_a.get("erro"):
                    st.error(r_a["erro"])
                else:
                    st.success(
                        f"✅ Onda {r_a['onda']}: {r_a['gravados']} pedido(s) "
                        f"até **#{r_a['limite']}** marcados como processados."
                    )
                    st.rerun()

    # Montar onda escolhendo (em vez de levar tudo que esta' pendente).
    # Pedido do Jota (27/08): "selecionando tudo, porem gere a possibilidade
    # de escolher e filtros por plataforma".
    if _pend:
        with st.expander("🎯 Montar onda escolhendo os pedidos"):
            _canais = sorted({str(p.get("canal") or "?") for p in _pend})
            _fil = st.multiselect(
                "Filtrar por plataforma", options=_canais, default=_canais,
                key=f"filtro_canal_onda_{chave}")
            _cand = [p for p in _pend if str(p.get("canal") or "?") in _fil]
            st.caption(f"{len(_cand)} pedido(s) no filtro. "
                       "Desmarque os que NÃO entram nesta onda.")

            _escolhidos = []
            for p in _cand:
                n = str(p.get("numero_ecommerce") or "")
                it = (p.get("itens") or [{}])[0]
                rot = (f"#{p.get('numero_olist') or '?'} · {p.get('canal')} · "
                       f"{it.get('quantidade', 1)}x {it.get('sku', '')}")
                if st.checkbox(rot, value=True, key=f"sel_{chave}_{n}"):
                    _escolhidos.append(n)

            if st.button(f"💾 Criar onda {ondas.proxima_onda()} com "
                         f"{len(_escolhidos)} pedido(s)",
                         key=f"btn_onda_sel_{chave}", type="primary",
                         disabled=not _escolhidos, use_container_width=True):
                r_s = ondas.salvar_onda_selecionada(_todos, set(_escolhidos))
                st.success(f"✅ Onda {r_s['onda']} criada — "
                           f"{r_s['gravados']} pedido(s).")
                st.rerun()

    if _feitos:
        with st.expander(f"✅ {len(_feitos)} pedido(s) já processados"):
            for p in sorted(_feitos, key=lambda x: (x.get("onda") or 0)):
                it = (p.get("itens") or [{}])[0]
                st.caption(
                    f"**onda {p['onda']}** · `{p.get('numero_ecommerce')}` "
                    f"· {p.get('canal')} · {it.get('quantidade', 1)}x "
                    f"{it.get('sku', '')}"
                )
    st.divider()
    # Sem onda travada o alvo e' a fila livre -- mesmo comportamento de antes,
    # em que as fases consumiam os pendentes.
    return _pend, _pend, _feitos


# =========================== FASE 1 — ETIQUETAS ============================= #
if fase(0):
    st.subheader("1️⃣ Baixar etiquetas e atualizar sistemas")
    st.caption(
        "Puxa as etiquetas direto das APIs oficiais — dispensa o modal do Olist, "
        "que abre 1 pop-up por etiqueta é bloqueado pelo Chrome. "
        "Tudo sai em **10x15**, 1 por página."
    )

    # ---- ONDAS: ja' logo no comeco, antes de baixar nada ------------------ #
    # Jota (25/08): "era ideal a gente ter logo nesse comeco... eu poder ver
    # a ultima etiqueta ali que ja consta no sistema... colocar o numeral a
    # partir de qual considerar". Precisa da fila do Olist (numero_olist,
    # sequencial e confiavel) pra isso — sincroniza sozinho se ainda nao
    # tiver rodado nesta sessao, sem esperar a Fase 2.
    if not st.session_state.get("dados_separacao"):
        with st.spinner("Sincronizando fila do Olist para checar as ondas..."):
            atualizar_separacao()
    _widget_ondas("f1")

    # ---- CICLO: escolher AGORA quais pedidos entram, antes de baixar ------ #
    # Jota (25/08): "o ideal e' na fase um a gente fazer ja' essa selecao dos
    # pedidos... eles entram no circuito em todas as demais fases". Reusa a
    # mesma mecanica do range de onda (ver `numero_olist`), so' que aplicada
    # ANTES do download em vez de depois — filtra na ORIGEM (Shopee/TikTok/ML
    # so' buscam o que estiver no range), nao baixa o resto a toa.
    # `None` = sem filtro, baixa tudo pendente (comportamento de sempre).
    _dados_ciclo = st.session_state.get("dados_separacao")
    with st.expander("🎯 Escolher quais pedidos entram neste ciclo (opcional)",
                      expanded=False):
        if not _dados_ciclo:
            st.caption("Sincronize a fila para poder escolher um range.")
        else:
            _todos_ciclo = (_dados_ciclo["pedidos_simples_1un"]
                            + _dados_ciclo["pedidos_simples_multi_un"]
                            + _dados_ciclo["pedidos_multi_itens"])
            _nums_ciclo = sorted(
                int(str(p.get("numero_olist")).lstrip("#"))
                for p in _todos_ciclo
                if str(p.get("numero_olist") or "").strip().lstrip("#").isdigit()
            )
            if _nums_ciclo:
                st.caption(f"Nesta fila: **#{_nums_ciclo[0]}** até **#{_nums_ciclo[-1]}**"
                           f" ({len(_todos_ciclo)} pedidos)")
            c_c1, c_c2 = st.columns([2, 1])
            with c_c1:
                _ate_ciclo = st.number_input(
                    "Baixar SÓ até este pedido (nº Olist) — 0 = todos",
                    min_value=0, step=1, value=0, key="num_ate_ciclo")
            with c_c2:
                st.write("")
                if st.button("🎯 Aplicar range", use_container_width=True,
                             key="btn_aplicar_ciclo", disabled=not _ate_ciclo):
                    def _n_olist_ciclo(p):
                        try:
                            return int(str(p.get("numero_olist") or 0).lstrip("#"))
                        except (TypeError, ValueError):
                            return 0
                    _sel = {str(p.get("numero_ecommerce") or "")
                            for p in _todos_ciclo
                            if 0 < _n_olist_ciclo(p) <= _ate_ciclo}
                    st.session_state["ciclo_selecionado"] = _sel
                    st.success(f"✅ Ciclo travado: {len(_sel)} pedido(s) até "
                               f"**#{_ate_ciclo}**. Vale para esta e as "
                               "próximas fases, até você limpar.")
                    st.rerun()
            if st.session_state.get("ciclo_selecionado") is not None:
                st.info(f"🎯 Ciclo ativo: **{len(st.session_state['ciclo_selecionado'])} "
                        "pedido(s)** selecionados — só eles serão baixados/"
                        "carregados nas próximas fases.")
                if st.button("🧹 Limpar seleção (voltar a baixar tudo)",
                             key="btn_limpar_ciclo"):
                    st.session_state["ciclo_selecionado"] = None
                    st.rerun()

    # ---- Os dois canais de uma vez, em paralelo -------------------------- #
    # ⚡ APIs independentes: nao ha motivo para esperar uma terminar para
    # comecar a outra. 23 etiquetas em ~24s contra ~1min40s sequencial.
    st.markdown("#### ⚡ Baixar tudo de uma vez")

    com_cartao_tudo = st.checkbox(
        "🎁 Já intercalar o cartão de agradecimento",
        value=True, key="chk_cartao_tudo",
        help="Cada etiqueta recebe o cartão do seu próprio canal.",
    )

    c_tudo, c_dl = st.columns([1, 1])

    with c_tudo:
        if st.button("⚡ Baixar TikTok + Shopee + ML juntos", type="primary",
                     use_container_width=True, key="btn_etq_tudo"):
            with st.spinner("Baixando os três canais ao mesmo tempo..."):
                try:
                    # A fase 1 so' BAIXA. Ordenar pela sequencia de embalagem e
                    # numerar #1..#N e' trabalho da FASE 3, que roda depois da
                    # separacao — antes dela nao existe sequencia definida.
                    # (Jota, 19/08: "temos fase... o numerar deveria ser na
                    #  fase 3 apos ordenar por produto na sequencia")
                    import core_etiquetas_todas as cet
                    r_tudo = cet.baixar_tudo(canais=["tiktok", "shopee", "ml"],
                                             com_cartao=com_cartao_tudo,
                                             somente=st.session_state.get("ciclo_selecionado"))
                    st.session_state["etq_tudo"] = r_tudo

                    if r_tudo["pdf"]:
                        with open(r_tudo["pdf"], "rb") as fh:
                            st.session_state["pdf_tudo_bytes"] = fh.read()
                        st.session_state["pdf_tudo_path"] = r_tudo["pdf"]
                        st.success(f"✅ {r_tudo['resumo']}")
                    else:
                        st.warning("Nenhuma etiqueta disponível nos três canais.")

                    # Envio ML `pending` nao imprime por API NENHUMA (nem ML nem
                    # Olist): so' pelo modal do Olist. Sem avisar aqui, o pedido
                    # some da pilha e parece que nao existe.
                    rep = ((r_tudo.get("por_canal") or {}).get("ml") or {}
                           ).get("represados") or []
                    if rep:
                        st.info(
                            f"📋 {len(rep)} pedido(s) do **Mercado Livre** ainda "
                            "estão `pending` — nenhuma API imprime nesse estado. "
                            "Use o modal do Olist para esses: "
                            + ", ".join(f"`{x['pedido']}`" for x in rep[:5])
                        )

                    for e in r_tudo["erros"]:
                        registrar_erro("1️⃣ Etiquetas", "Etiqueta não baixada", e)
                except Exception as exc:
                    erro_visivel("1️⃣ Etiquetas",
                                 "Download dos três canais falhou", exc)

    with c_dl:
        if st.session_state.get("pdf_tudo_bytes"):
            rt = st.session_state.get("etq_tudo", {})
            st.download_button(
                f"⬇️ Baixar {rt.get('total', 0)} etiquetas",
                data=st.session_state["pdf_tudo_bytes"],
                file_name=f"etiquetas_todas_{datetime.now():%Y%m%d_%H%M}.pdf",
                mime="application/pdf", type="primary",
                use_container_width=True, key="dl_etq_tudo",
            )
            # ⚠️ NAO ha' botao de ordenar aqui de proposito (25/08).
            # A FASE 3 ja' entrega a pilha ordenada e numerada via
            # `core_etiquetas_na_esteira`, que usa `core_sequencia_embalagem`
            # (familia -> produto -> cor, sortida fechando o grupo). Ter dois
            # caminhos de ordenacao so' confunde a bancada.

            if st.button("🖨️ Imprimir na LABEL 2", use_container_width=True,
                         key="btn_print_tudo"):
                try:
                    import separador_etiquetas as se
                    pgs = se.imprimir_pdf_direto(
                        st.session_state["pdf_tudo_path"], impressora="LABEL 2")
                    st.toast(f"🖨️ {pgs} páginas enviadas.", icon="✅")
                except Exception as exc:
                    erro_visivel("1️⃣ Etiquetas", "Impressão na LABEL 2 falhou", exc)

    if st.session_state.get("etq_tudo"):
        _pc = st.session_state["etq_tudo"].get("por_canal", {})
        st.caption(" · ".join(
            f"**{c}**: {d['total']} em {d['segundos']}s" for c, d in _pc.items()))

    st.divider()
    st.markdown("#### Ou um canal de cada vez")

    col_tt, col_sp = st.columns(2)

    # -------------------------------- TIKTOK -------------------------------- #
    with col_tt:
        st.markdown("#### 🎵 TikTok Shop")
        if st.button("🔍 Buscar etiquetas do TikTok", use_container_width=True,
                     key="btn_etq_tiktok_api"):
            with st.spinner("Consultando pacotes no TikTok Shop..."):
                try:
                    import core_etiquetas_tiktok_api as tta
                    res = tta.baixar_etiquetas()

                    if res.get("aviso"):
                        st.info(res["aviso"])
                    elif res["total"] == 0:
                        st.warning("Nenhuma etiqueta disponível.")
                    else:
                        with open(res["pdf"], "rb") as fh:
                            st.session_state["pdf_tiktok_api"] = fh.read()
                        st.session_state["pdf_tiktok_api_qtd"] = res["total"]
                        st.session_state["pdf_tiktok_path"] = res["pdf"]
                        st.success(f"✅ {res['total']} etiquetas.")

                    if res.get("falhas"):
                        with st.expander(f"⚠️ {len(res['falhas'])} sem etiqueta"):
                            for pid, motivo in res["falhas"]:
                                st.caption(f"`{pid}` — {motivo}")
                except Exception as exc:
                    erro_visivel("1️⃣ Etiquetas", "Etiquetas do TikTok não vieram", exc)

        if st.session_state.get("pdf_tiktok_api"):
            st.download_button(
                f"⬇️ Baixar {st.session_state.get('pdf_tiktok_api_qtd', 0)} etiquetas",
                data=st.session_state["pdf_tiktok_api"],
                file_name=f"etiquetas_tiktok_{datetime.now():%Y%m%d_%H%M}.pdf",
                mime="application/pdf",
                use_container_width=True,
                key="dl_etq_tiktok_api",
            )

        st.caption(
            "ℹ️ Só pacotes em **PROCESSING** têm etiqueta. Depois de coletados "
            "(FULFILLING/COMPLETED) o TikTok recusa a impressão."
        )

    # -------------------------------- SHOPEE -------------------------------- #
    with col_sp:
        st.markdown("#### 🛒 Shopee")
        if st.button("🔍 Buscar etiquetas da Shopee", use_container_width=True,
                     key="btn_etq_shopee_api"):
            with st.spinner("Consultando pedidos a despachar na Shopee..."):
                try:
                    import core_etiquetas_shopee_api as spa
                    res = spa.baixar_etiquetas()

                    if res.get("aviso"):
                        st.info(res["aviso"])
                    elif res["total"] == 0:
                        st.warning("Nenhuma etiqueta disponível.")
                    else:
                        with open(res["pdf"], "rb") as fh:
                            st.session_state["pdf_shopee_api"] = fh.read()
                        st.session_state["pdf_shopee_api_qtd"] = res["total"]
                        st.session_state["pdf_shopee_path"] = res["pdf"]
                        extra = ""
                        if res.get("normalizado"):
                            extra = f" · {res.get('paginas_recortadas', 0)} recortadas p/ 10x15"
                        st.success(f"✅ {res['total']} etiquetas{extra}.")

                    if res.get("falhas"):
                        with st.expander(f"⚠️ {len(res['falhas'])} sem etiqueta"):
                            for sn, motivo in res["falhas"]:
                                st.caption(f"`{sn}` — {motivo}")
                except Exception as exc:
                    erro_visivel("1️⃣ Etiquetas", "Etiquetas da Shopee não vieram", exc)

        if st.session_state.get("pdf_shopee_api"):
            st.download_button(
                f"⬇️ Baixar {st.session_state.get('pdf_shopee_api_qtd', 0)} etiquetas",
                data=st.session_state["pdf_shopee_api"],
                file_name=f"etiquetas_shopee_{datetime.now():%Y%m%d_%H%M}.pdf",
                mime="application/pdf",
                use_container_width=True,
                key="dl_etq_shopee_api",
            )

        st.caption(
            "ℹ️ Cobre **READY_TO_SHIP + PROCESSED**. Na Shopee o pedido sai de "
            "READY_TO_SHIP assim que a etiqueta é gerada — buscar só esse "
            "status devolveria lista vazia. A folha A4 é recortada para 10x15."
        )

    # ---------------------------- MERCADO LIVRE ----------------------------- #
    # O ML era o unico canal fora da esteira: so' dava pra imprimir pelo modal
    # do Olist, que abre 1 pop-up por etiqueta e apanha do bloqueador.
    st.divider()
    st.markdown("#### 🛍️ Mercado Livre")
    if st.button("🔍 Buscar etiquetas do Mercado Livre", use_container_width=True,
                 key="btn_etq_ml_api"):
        with st.spinner("Consultando envios no Mercado Livre..."):
            try:
                import core_etiquetas_ml_api as mla
                res = mla.baixar_etiquetas()

                if res.get("aviso"):
                    st.info(res["aviso"])
                elif res["total"] == 0:
                    st.warning("Nenhuma etiqueta disponível.")
                else:
                    with open(res["pdf"], "rb") as fh:
                        st.session_state["pdf_ml_api"] = fh.read()
                    st.session_state["pdf_ml_api_qtd"] = res["total"]
                    st.session_state["pdf_ml_path"] = res["pdf"]
                    st.success(f"✅ {res['total']} etiquetas.")

                # Envios `pending`: existem, precisam ser despachados, mas
                # NENHUMA API imprime (ML recusa `NOT_PRINTABLE_STATUS`, Olist
                # recusa "Mercado Envios"). Sem listar aqui, some da tela e
                # parece que o pedido não existe.
                rep = res.get("represados") or []
                if rep:
                    with st.expander(f"📋 {len(rep)} pedido(s) ML só pelo modal do Olist",
                                     expanded=True):
                        st.caption(
                            "O Mercado Livre ainda não liberou estes envios "
                            "(`pending`). Nem a API do ML nem a do Olist "
                            "imprimem nesse estado — **use o modal do Olist**."
                        )
                        for x in rep:
                            st.write(f"• `{x['pedido']}` — {x['cliente']}")

                if res.get("falhas"):
                    with st.expander(f"⚠️ {len(res['falhas'])} sem etiqueta"):
                        for sid, motivo in res["falhas"]:
                            st.caption(f"`{sid}` — {motivo}")
            except Exception as exc:
                erro_visivel("1️⃣ Etiquetas", "Etiquetas do ML não vieram", exc)

    if st.session_state.get("pdf_ml_api"):
        st.download_button(
            f"⬇️ Baixar {st.session_state.get('pdf_ml_api_qtd', 0)} etiquetas",
            data=st.session_state["pdf_ml_api"],
            file_name=f"etiquetas_ml_{datetime.now():%Y%m%d_%H%M}.pdf",
            mime="application/pdf",
            use_container_width=True,
            key="dl_etq_ml_api",
        )

    st.caption(
        "ℹ️ Só envios em **ready_to_ship** têm etiqueta. `pending` (represado "
        "pelo ML, antes de liberar) devolve `NOT_PRINTABLE_STATUS` — não é "
        "erro nosso. A etiqueta do ML já vem com DANFE simplificada embutida, "
        "por isso **não** passa pelo recorte 10x15."
    )

    avancar(1, "Etiquetas prontas — ir para a Separação")

# ============================ FASE 2 — SEPARAR ============================== #
if fase(1):
    st.subheader("2️⃣ Separar itens — por SKU átomo")
    st.caption(
        "Agrupado por **átomo**, não por kit: quem busca no estoque pega "
        "*6 meias pretas 40/46*, não *1 kit de 6*. O SKU V5 já diz isso."
    )

    # ---- Botoes de atualizacao: moram aqui, junto da lista que montam ---- #
    c_at, c_rs = st.columns([2, 1])

    with c_at:
        if st.button("🔄 Atualizar separação", type="primary",
                     use_container_width=True, key="btn_atualizar_sep",
                     help="Busca só os pedidos novos — o resto vem do cache."):
            atualizar_separacao()
            st.rerun()

    with c_rs:
        if st.button("🔴 Zerar e baixar tudo", use_container_width=True,
                     key="btn_reset_sep",
                     help="Descarta o cache de 15 dias e rebaixa a fila inteira."):
            atualizar_separacao(reset=True)
            st.rerun()

    _us = st.session_state.get("ultimo_sync")
    if _us:
        st.caption(
            f"⚡ {_us['resumo']}"
            + (f" · {len(_us['sairam'])} saiu/saíram da fila" if _us.get("sairam") else "")
        )
        # Referencia para conferir contra a tela do Olist — o ERP nao mostra
        # sequencial, entao sem isto nao da' para saber se a base esta' em dia.
        if _us.get("ultimo_pedido"):
            st.info(
                f"📌 **Último pedido sincronizado: nº {_us['ultimo_pedido']}** "
                f"· {_us.get('ultimo_canal') or '?'} "
                f"· `{_us.get('ultimo_ecommerce') or '?'}` "
                f"· {_us.get('ultimo_data') or '?'}\n\n"
                "Se o Olist já tem pedido mais novo que este, clique em "
                "**Atualizar separação**."
            )

    if not dados:
        st.info("Clique em **Atualizar separação** para montar a lista.")
    else:
        m1, m2, m3, m4 = st.columns(4)
        m1.metric("Pedidos", dados["total_pedidos"])
        # ⚠️ `total_pecas` conta UNIDADES DE VENDA (1 kit = 1). A peca fisica
        # que se pega na prateleira vem da soma dos atomos — 23 kits podem ser
        # 179 pecas. Mostrar o numero de venda aqui confundia a bancada.
        _pecas = (sum(a["qtd"] for a in st.session_state.atomos_coleta)
                  if st.session_state.atomos_coleta else dados["total_pecas"])
        m2.metric("Peças a pegar", _pecas,
                  help="Unidades físicas na prateleira, não kits vendidos")
        m3.metric("SKUs de venda", dados["total_skus_distintos"])

        atomos = st.session_state.atomos_coleta
        if atomos:
            m4.metric("SKUs de átomo", len(atomos),
                      help="O que você realmente busca na prateleira")

            st.divider()

            import core_separacao_atomos as csa

            def _tabela(grupo, chave):
                st.data_editor(
                    pd.DataFrame([
                        {
                            "Coletado": False,
                            # ⚠️ marca o item cuja quantidade pode estar errada
                            "!": "⚠️" if a.get("suspeito") else "",
                            "Qtd": a["qtd"],
                            "Pacotes": (csa.em_pacotes(a["qtd"])["texto"]
                                        if csa.e_meia(a["atomo"]) else "—"),
                            "SKU Átomo": a["atomo"],
                            "Família": a["familia"],
                            "Pedidos": a["total_pedidos"],
                        }
                        for a in grupo
                    ]),
                    column_config={
                        "Coletado": st.column_config.CheckboxColumn("✓", default=False),
                        "!": st.column_config.TextColumn(
                            "!", width="small",
                            help="Quantidade suspeita — ver o painel de erros no fim"),
                        "Qtd": st.column_config.NumberColumn("Qtd", format="%d un"),
                        "Pacotes": st.column_config.TextColumn(
                            "Como pegar", width="medium",
                            help="Meia vem em pacote fechado de 12"),
                        "SKU Átomo": st.column_config.TextColumn("SKU Átomo", width="medium"),
                        "Família": st.column_config.TextColumn("Família", width="small"),
                        "Pedidos": st.column_config.NumberColumn("Nº peds"),
                    },
                    use_container_width=True,
                    hide_index=True,
                    key=chave,
                )

            # ⚠️ Lista UNICA: SOR e' so' mais uma cor do atomo (a meia
            # invisivel tem SOR e tambem PRE/BRA/CIN). Nao separar em pilhas.
            st.markdown("#### 🛒 Lista de coleta física")
            _tabela(atomos, "editor_atomos")

            # ---- Lista de MONTAGEM em A4 (papel, nao etiqueta) ---------- #
            # ⚠️ Vem ANTES das etiquetas no fluxo real: o Jota leva esta folha
            # a' bancada para saber o que pegar E como montar. Depois e' que
            # as etiquetas vao casando (relato 25/08).
            # Uma linha por pedido, agrupada por produto/cor -- a MESMA ordem
            # de `core_etiquetas_na_esteira`, entao a pilha de papel e a de
            # etiqueta conversam.
            st.markdown("#### 📄 Lista de montagem (A4 — levar à bancada)")
            c_lm, c_lm2 = st.columns([2, 1])
            with c_lm:
                if st.button("📄 Gerar lista de separação + montagem",
                             type="primary", use_container_width=True,
                             key="btn_lista_montagem"):
                    try:
                        import core_lista_montagem_pdf as clm
                        r_lm = clm.gerar_lista_montagem(dados)
                        st.session_state["lista_montagem_pdf"] = r_lm["bytes"]
                        st.success(
                            f"✅ {r_lm['paginas']} página(s) · {r_lm['skus']} SKUs · "
                            f"{r_lm['pedidos']} pedidos · {r_lm['total_pecas']} peças"
                        )
                    except Exception as exc:
                        erro_visivel("2️⃣ Separar",
                                     "Falha ao gerar a lista de montagem", exc)
            with c_lm2:
                # Mesma parte 2, em etiqueta 10x15 — mesmo rolo da LABEL 2,
                # para ler do lado da pilha de etiquetas (Jota, 25/08).
                if st.button("🏷️ Só montagem (10x15)", use_container_width=True,
                             key="btn_montagem_1015"):
                    try:
                        import core_lista_montagem_pdf as clm
                        r_15 = clm.gerar_montagem_10x15(dados)
                        st.session_state["montagem_1015_pdf"] = r_15["bytes"]
                        st.success(f"✅ {r_15['paginas']} etiqueta(s) · "
                                   f"{r_15['pedidos']} pedidos")
                    except Exception as exc:
                        erro_visivel("2️⃣ Separar",
                                     "Falha ao gerar a montagem 10x15", exc)

            # ---- Checagem de gap na sequencia — botao separado ----------- #
            # Jota (26/08): "eles são sequenciais... do primeiro ao último
            # deve estar aí na lista". So' roda sob pedido (bate o Olist de
            # novo pelas situacoes 0-4) — nao a cada geracao, pra nao pagar
            # o custo de API toda vez.
            if st.button("🔎 Conferir se falta algum #número na sequência",
                         use_container_width=True, key="btn_checar_gap"):
                with st.spinner("Comparando com o Olist..."):
                    try:
                        import core_lista_montagem_pdf as clm
                        r_gap = clm.checar_gaps_sequencia(dados)
                        if r_gap["min"] is None:
                            st.info("Sem pedidos com número Olist nesta lista.")
                        elif not r_gap["faltando_pendente"]:
                            st.success(
                                f"✅ Sequência completa de #{r_gap['min']} a "
                                f"#{r_gap['max']} — nada pendente ficou de fora."
                            )
                        else:
                            st.error(
                                f"🔴 {len(r_gap['faltando_pendente'])} pedido(s) "
                                f"faltando na faixa #{r_gap['min']}–#{r_gap['max']}, "
                                "e AINDA estão pendentes:"
                            )
                            for f in r_gap["faltando_pendente"]:
                                st.caption(
                                    f"**#{f['numero_olist']}** · {f['cliente']} · "
                                    f"{f['canal']} · situação: {f['situacao']}"
                                )
                    except Exception as exc:
                        erro_visivel("2️⃣ Separar",
                                     "Falha ao checar gap na sequência", exc)

            c_d1, c_d2 = st.columns(2)
            with c_d1:
                if st.session_state.get("lista_montagem_pdf"):
                    st.download_button(
                        "⬇️ Baixar A4",
                        data=st.session_state["lista_montagem_pdf"],
                        file_name=f"lista_montagem_{datetime.now():%Y%m%d_%H%M}.pdf",
                        mime="application/pdf", use_container_width=True,
                        key="dl_lista_montagem")
            with c_d2:
                if st.session_state.get("montagem_1015_pdf"):
                    st.download_button(
                        "⬇️ Baixar 10x15",
                        data=st.session_state["montagem_1015_pdf"],
                        file_name=f"montagem_10x15_{datetime.now():%Y%m%d_%H%M}.pdf",
                        mime="application/pdf", use_container_width=True,
                        key="dl_montagem_1015")
                    if st.button("🖨️ Imprimir na LABEL 2",
                                 use_container_width=True, key="btn_print_mont1015"):
                        try:
                            import tempfile

                            import separador_etiquetas as se
                            with tempfile.NamedTemporaryFile(delete=False,
                                                             suffix=".pdf") as th:
                                th.write(st.session_state["montagem_1015_pdf"])
                                cam = th.name
                            pgs = se.imprimir_pdf_direto(cam, impressora="LABEL 2")
                            st.toast(f"🖨️ {pgs} página(s) enviada(s).", icon="✅")
                        except Exception as exc:
                            erro_visivel("2️⃣ Separar",
                                         "Impressão da montagem falhou", exc)
            st.caption(
                "Duas partes: **1) pegar no estoque** por SKU · "
                "**2) montar os pedidos**, 1 linha cada, na ordem da bancada."
            )

            # ---- Lista 10x15: opcional (etiqueta, nao papel) ------------- #
            with st.expander("🏷️ Versão etiqueta 10x15 (opcional)",
                             expanded=False):
                st.caption(
                    "Gera a lista em etiqueta **10x15** (mesmo rolo da LABEL 2), "
                    "espremendo o máximo de itens por folha para gastar menos papel."
                )

                c_ger, c_prt = st.columns(2)

                with c_ger:
                    if st.button("📄 Gerar lista 10x15", use_container_width=True,
                                 key="btn_gerar_lista_pdf"):
                        try:
                            import core_lista_separacao_pdf as clp
                            r_pdf = clp.gerar_lista_10x15(atomos)
                            st.session_state["lista_pdf"] = r_pdf["bytes"]
                            st.success(
                                f"✅ {r_pdf['paginas']} etiqueta(s) · "
                                f"{r_pdf['itens']} itens · {r_pdf['total_pecas']} peças."
                            )
                        except Exception as exc:
                            erro_visivel("2️⃣ Separar",
                                         "Lista para impressão não foi gerada", exc)

                with c_prt:
                    if st.session_state.get("lista_pdf"):
                        st.download_button(
                            "⬇️ Baixar",
                            data=st.session_state["lista_pdf"],
                            file_name=f"lista_separacao_{datetime.now():%Y%m%d_%H%M}.pdf",
                            mime="application/pdf",
                            use_container_width=True,
                            key="dl_lista_pdf",
                        )

                if st.session_state.get("lista_pdf"):
                    if st.button("🖨️ Imprimir na LABEL 2", use_container_width=True,
                                 key="btn_print_lista"):
                        try:
                            import tempfile
                            import separador_etiquetas as se
                            with tempfile.NamedTemporaryFile(
                                    delete=False, suffix=".pdf") as th:
                                th.write(st.session_state["lista_pdf"])
                                caminho = th.name
                            pgs = se.imprimir_pdf_direto(caminho, impressora="LABEL 2")
                            st.toast(f"🖨️ {pgs} página(s) enviada(s).", icon="✅")
                        except Exception as exc:
                            erro_visivel("2️⃣ Separar",
                                         "Impressão da lista na LABEL 2 falhou", exc)

            with st.expander("📄 Versão texto — para a prancheta"):
                try:
                    import core_separacao_atomos as csa
                    st.text_area("Lista de coleta:", value=csa.resumo_texto(atomos),
                                 height=320, key="txt_atomos")
                except Exception as exc:
                    erro_visivel("2️⃣ Separar", "Resumo em texto indisponível", exc, grave=False)
        else:
            st.warning("Decomposição em átomos indisponível — mostrando por SKU de venda.")
            st.dataframe(
                pd.DataFrame([
                    {"Qtd": i["total_unidades"], "SKU": i["sku"],
                     "Descrição": i["descricao"]}
                    for i in dados["lista_coleta"]
                ]),
                use_container_width=True, hide_index=True,
            )

        # Com onda travada, o mapa mostra so' os pedidos dela -- senao a
        # bancada confere contra uma lista maior do que a caixa que tem na
        # frente.
        _f_onda = _filtro_onda()
        _simples_onda = _so_da_onda(dados["pedidos_simples_1un"], _f_onda)
        _multi_onda = _so_da_onda(dados["pedidos_multi_itens"], _f_onda)
        if _f_onda:
            st.caption(f"🔒 Mostrando só a **onda "
                       f"{st.session_state.get('onda_travada')}**.")

        with st.expander("📋 Mapa pedido ↔ itens (conferência)"):
            c_simp, c_multi = st.columns(2)
            with c_simp:
                st.markdown(f"##### 🟢 Simples ({len(_simples_onda)})")
                for p in _simples_onda[:40]:
                    it = p["itens"][0] if p["itens"] else {}
                    # #459 (sequencial Olist) na frente: curto, bate o olho na
                    # bancada e denuncia duplicata. O numero do marketplace
                    # continua ao lado, porque e' o que casa com a etiqueta.
                    _n = f"#{p['numero_olist']} " if p.get("numero_olist") else ""
                    _et = " 🏷️" if p.get("etiqueta_emitida") else ""
                    st.caption(f"**{_n}**`{p['numero_ecommerce']}`{_et} — {it.get('sku')}")
            with c_multi:
                st.markdown(f"##### ⚠️ Multi-itens ({len(_multi_onda)})")
                for p in _multi_onda:
                    _n = f"#{p['numero_olist']} " if p.get("numero_olist") else ""
                    _et = " 🏷️" if p.get("etiqueta_emitida") else ""
                    with st.expander(f"{_n}{p['numero_ecommerce']}{_et} — {p['qtd_total']} peças"):
                        for it in p["itens"]:
                            st.markdown(f"- **{it.get('quantidade')}x** `{it.get('sku')}`")

    avancar(2, "Itens separados — ir para Etiqueta + Cartão")

# ======================= FASE 3 — ETIQUETA + CARTAO ========================= #
if fase(2):
    st.subheader("3️⃣ Unificar etiquetas + cartões de agradecimento")
    st.caption("Sai na ordem física da bancada: etiqueta → cartão → etiqueta → cartão…")

    # ---- ONDAS: o que ja' foi processado --------------------------------- #
    # O Olist so' muda a situacao do pedido quando ele e' de fato despachado.
    # Entre imprimir a etiqueta e dar baixa passam horas, e nesse meio a lista
    # continua mostrando tudo como pendente — quem imprime de novo gasta
    # etiqueta e se perde (Jota, 25/08: "as vezes fazemos outra onda").
    # Widget compartilhado com a Fase 1 -- ver `_widget_ondas()`.
    # `_alvo` = os pedidos da onda travada; sem trava, a fila livre.
    _dados_onda = st.session_state.get("dados_separacao")
    _alvo, _pend, _feitos = _widget_ondas("f3")

    # ---- pilha unica, na ordem da bancada -------------------------------- #
    # Este e' o caminho principal: um PDF so' com os dois canais, REORDENADO
    # pela sequencia de embalagem e numerado #1..#N. So' faz sentido aqui na
    # fase 3 — antes da separacao (fase 2) nao existe sequencia definida.
    st.markdown("#### 🔢 Pilha única na ordem da bancada")
    st.caption("Os dois canais juntos, na sequência de embalagem, "
               "com **#1, #2, #3…** no canto inferior direito de cada etiqueta.")

    col_p, col_o = st.columns([2, 1])
    with col_o:
        cartao_esteira = st.checkbox("🎁 Com cartão", value=True,
                                     key="chk_cartao_esteira")
        # Os Correios devolvem ao remetente quando o nome do rótulo não
        # corresponde a ninguém no endereço. O TikTok imprime o apelido do
        # comprador; aqui o nome civil (o mesmo do CPF da NF-e) é ACRESCENTADO
        # entre parênteses, sem apagar o que a plataforma emitiu.
        # Escopo da pilha. Com onda travada o padrao e' a propria onda --
        # reimprimir dentro dela e' livre (decisao do Jota, 27/08: "livre,
        # geralmente é só gerar o pdf"), sem pedir confirmacao.
        _onda_lock = st.session_state.get("onda_travada")
        _imprimir_tudo = st.checkbox(
            "📚 Imprimir TUDO", value=False, key="chk_onda_tudo",
            help=("Desmarcado: só os pedidos da onda travada."
                  if _onda_lock else
                  "Desmarcado: só os pedidos que ainda não entraram em uma "
                  "onda. Marcado: a pilha inteira, inclusive os já feitos."))
        nome_real_esteira = st.checkbox(
            "🪪 Nome civil", value=True, key="chk_nome_real",
            help='Apelido vira "Thata (Aurora Machado)". '
                 "Nome já correto ou abreviado não é tocado.")
    with col_p:
        if st.button("🔢 Gerar pilha numerada na ordem da esteira",
                     type="primary", use_container_width=True,
                     key="btn_pilha_esteira"):
            prog_bar = st.progress(5, text="⏳ [1/4] Conectando APIs e baixando etiquetas (TikTok, Shopee, ML)... (5%)")
            try:
                import core_etiquetas_na_esteira as cne
                _so = None
                if _dados_onda and not _imprimir_tudo:
                    # `_alvo` ja' respeita a onda travada; sem trava ele e' a
                    # fila livre, igual ao comportamento anterior.
                    _so = {str(p.get("numero_ecommerce") or "") for p in _alvo}

                prog_bar.progress(35, text="⏳ [2/4] Normalizando formato térmico 10x15 e intercalando cartões... (35%)")

                r_es = cne.gerar(com_cartao=cartao_esteira,
                                 nome_real=nome_real_esteira,
                                 somente=_so)

                prog_bar.progress(80, text="⏳ [3/4] Aplicando sequência da esteira e numeração #1..#N... (80%)")

                if r_es.get("pdf") and Path(r_es["pdf"]).exists():
                    with open(r_es["pdf"], "rb") as fh:
                        st.session_state["pdf_esteira_bytes"] = fh.read()
                    st.session_state["pdf_esteira_path"] = r_es["pdf"]
                    prog_bar.progress(100, text="✅ [4/4] Pilha numerada pronta para impressão! (100%)")
                    st.success(f"✅ {r_es['resumo']}")
                    if r_es.get("nomes_corrigidos"):
                        st.info(
                            f"🪪 {r_es['nomes_corrigidos']} etiqueta(s) "
                            "ganharam o nome civil ao lado do apelido.")
                    if r_es.get("filtrados_por_onda"):
                        st.caption(
                            f"🌊 {r_es['filtrados_por_onda']} etiqueta(s) "
                            "de pedido já processado ficaram de fora."
                        )
                    if r_es.get("fora_da_esteira"):
                        st.warning(
                            f"⚠️ {r_es['fora_da_esteira']} etiqueta(s) sem "
                            "posição na sequência — foram para o FIM da "
                            "pilha, não se perderam.")
                else:
                    prog_bar.empty()
                    st.warning(f"⚠️ [ERR-001] Nenhuma etiqueta disponível para gerar a pilha. ({r_es.get('resumo', '')})")

                for e in r_es.get("erros") or []:
                    st.warning(f"⚠️ [ERR-003] {e}")
            except Exception as exc:
                prog_bar.empty()
                erro_visivel("3️⃣ Etiq + Cartão",
                             "[ERR-002] Falha ao gerar a pilha da esteira", exc)

    if st.session_state.get("pdf_esteira_bytes"):
        st.download_button(
            "⬇️ Baixar pilha numerada",
            data=st.session_state["pdf_esteira_bytes"],
            file_name=f"etiquetas_esteira_{datetime.now():%Y%m%d_%H%M}.pdf",
            mime="application/pdf", use_container_width=True,
            key="dl_pilha_esteira")

    st.divider()
    st.markdown("#### 🎁 A partir das etiquetas já baixadas na fase 1")

    col_a, col_b = st.columns(2)
    for coluna, canal, rotulo in [(col_a, "tiktok", "🎵 TikTok"),
                                  (col_b, "shopee", "🛒 Shopee")]:
        with coluna:
            caminho = st.session_state.get(f"pdf_{canal}_path")
            if not caminho or not Path(caminho).exists():
                st.caption(f"{rotulo} — baixe as etiquetas na fase 1 primeiro.")
                continue

            qtd = st.session_state.get(f"pdf_{canal}_api_qtd", 0)
            if st.button(f"{rotulo} — intercalar {qtd} cartões",
                         use_container_width=True, key=f"btn_cartao_{canal}"):
                with st.spinner("Intercalando cartões..."):
                    try:
                        import core_etiqueta_com_cartao as ccc
                        alvo = caminho.replace(".pdf", "_com_cartao.pdf")
                        r = ccc.intercalar_canal_unico(caminho, alvo, canal)
                        if r.get("ok"):
                            with open(alvo, "rb") as fh:
                                st.session_state[f"pdf_{canal}_cartao_bytes"] = fh.read()
                            st.session_state[f"pdf_{canal}_cartao_path"] = alvo
                            st.success(
                                f"✅ {r['etiquetas']} etiquetas + cartões "
                                f"= {r['paginas']} páginas."
                            )
                        else:
                            st.error(f"Falhou: {r.get('erro')}")
                    except Exception as exc:
                        erro_visivel("3️⃣ Etiq + Cartão", f"Falha ao intercalar cartões ({canal})", exc)

            if st.session_state.get(f"pdf_{canal}_cartao_bytes"):
                st.download_button(
                    f"⬇️ Baixar {rotulo} com cartões",
                    data=st.session_state[f"pdf_{canal}_cartao_bytes"],
                    file_name=f"etiquetas_{canal}_cartao_{datetime.now():%Y%m%d_%H%M}.pdf",
                    mime="application/pdf",
                    use_container_width=True,
                    key=f"dl_cartao_{canal}",
                )
                if st.button(f"🖨️ Imprimir {rotulo} na LABEL 2",
                             use_container_width=True, key=f"btn_print_{canal}"):
                    try:
                        import separador_etiquetas as se
                        pgs = se.imprimir_pdf_direto(
                            st.session_state[f"pdf_{canal}_cartao_path"],
                            impressora="LABEL 2")
                        st.toast(f"🖨️ {pgs} páginas enviadas à LABEL 2.", icon="✅")
                    except Exception as exc:
                        erro_visivel("3️⃣ Etiq + Cartão", "Impressão na LABEL 2 falhou", exc)

    st.divider()
    with st.expander("📂 Ou solte PDFs baixados na mão (Shopee + ML + TikTok juntos)"):
        st.caption("Detecta o canal de cada etiqueta e intercala o cartão certo.")
        enviados = st.file_uploader(
            "Solte os PDFs 10x15:", type=["pdf"], accept_multiple_files=True,
            key="upload_1015_multi")

        if enviados:
            import tempfile
            import fitz

            doc = fitz.open()
            for arq in enviados:
                tmp = fitz.open(stream=arq.read(), filetype="pdf")
                doc.insert_pdf(tmp)
                tmp.close()
            with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as th:
                doc.save(th.name)
                entrada_tmp = th.name
            doc.close()

            st.info(f"📂 {len(enviados)} arquivo(s) carregado(s).")

            if st.button("📑 Compilar e intercalar", use_container_width=True,
                         key="btn_compilar_upload"):
                with st.spinner("Detectando canais e intercalando..."):
                    try:
                        import core_etiqueta_com_cartao as ccc
                        saida_tmp = entrada_tmp.replace(".pdf", "_com_cartoes.pdf")
                        r = ccc.montar_pdf(entrada_tmp, saida_tmp)
                        st.session_state["pdf_1015_pronto"] = saida_tmp
                        st.success(
                            f"✅ {r.get('total_etiquetas', 0)} etiquetas "
                            f"({r.get('com_cartao', 0)} com cartão)."
                        )
                    except Exception as exc:
                        erro_visivel("3️⃣ Etiq + Cartão", "Falha ao compilar os PDFs enviados", exc)

        pronto = st.session_state.get("pdf_1015_pronto")
        if pronto and Path(pronto).exists():
            c1, c2 = st.columns(2)
            with c1:
                with open(pronto, "rb") as fh:
                    st.download_button(
                        "📄 Baixar compilado", data=fh.read(),
                        file_name=f"etiquetas_10x15_{datetime.now():%Y%m%d_%H%M}.pdf",
                        mime="application/pdf", use_container_width=True,
                        key="dl_1015_compilado")
            with c2:
                if st.button("🖨️ Imprimir na LABEL 2", type="primary",
                             use_container_width=True, key="btn_print_compilado"):
                    try:
                        import separador_etiquetas as se
                        pgs = se.imprimir_pdf_direto(pronto, impressora="LABEL 2")
                        st.toast(f"🖨️ {pgs} páginas enviadas.", icon="✅")
                    except Exception as exc:
                        erro_visivel("3️⃣ Etiq + Cartão", "Impressão na LABEL 2 falhou", exc)

    avancar(3, "Etiquetas montadas — ir para as etiquetas 40x25")

# ========================= FASE 4 — ETIQUETA 40x25 ========================== #
if fase(3):
    st.subheader("4️⃣ Etiquetas 40x25mm de identificação")
    st.info(
        "🔵 **Fase opcional.** Serve para identificar a peça na bancada — "
        "gere só se for usar hoje. Pode pular direto para a fase 5."
    )

    if not st.session_state.pedidos_brutos:
        st.caption("Rode **Atualizar separação** na fase 2 para gerar as "
                   "etiquetas de identificação.")
    else:
        try:
            import core_etiquetas_pedido as cep
            itens_etq = cep.preparar_etiquetas_da_fila_olist(st.session_state.pedidos_brutos)

            if not itens_etq:
                st.warning("Nenhum item válido para etiqueta 40x25mm.")
            else:
                st.caption(
                    f"Carimbo sequencial **#1 a #{len(itens_etq)}**, na mesma "
                    "ordem da fila — casa com a bipagem da fase 6."
                )
                if st.button(f"🏷️ Gerar {len(itens_etq)} etiquetas 40x25mm",
                             use_container_width=True, key="btn_gerar_40x25"):
                    with st.spinner("Montando o PDF térmico..."):
                        st.session_state["pdf_40x25"] = cep.gerar_pdf_etiquetas_sincronizadas(itens_etq)
                        st.session_state["pdf_40x25_qtd"] = len(itens_etq)
                        st.success(f"✅ {len(itens_etq)} etiquetas geradas.")

                if st.session_state.get("pdf_40x25"):
                    st.download_button(
                        f"⬇️ Baixar {st.session_state['pdf_40x25_qtd']} etiquetas 40x25mm",
                        data=st.session_state["pdf_40x25"],
                        file_name=f"etiquetas_sku_40x25_{datetime.now():%Y%m%d_%H%M}.pdf",
                        mime="application/pdf",
                        type="primary",
                        use_container_width=True,
                        key="dl_40x25",
                    )
        except Exception as exc:
            erro_visivel("4️⃣ Etiq 40x25", "Etiquetas 40x25 não foram geradas", exc)

    # ---- Sequência de embalagem: a ordem das caixas na bancada ---------- #
    # A coleta traz tudo junto por átomo. Montar as caixas nesta mesma ordem
    # faz a pilha da bancada casar com a pilha de etiquetas — sem garimpar
    # peça a cada pedido (Jota, 2026-08-16).
    st.divider()
    st.markdown("#### 📑 Sequência de embalagem")
    st.caption(
        "Ordem mais inteligente para montar as caixas: **tudo de um átomo "
        "junto**, sortida fechando cada linha, multi-item no fim agrupado por "
        "combinação. Imprima as etiquetas 10x15 nesta ordem e a pilha bate "
        "com a bancada."
    )

    if not dados:
        st.caption("Rode **Atualizar separação** na fase 2 primeiro.")
    else:
        if st.button("📑 Gerar sequência de embalagem", use_container_width=True,
                     key="btn_gerar_sequencia"):
            try:
                import core_sequencia_embalagem as cse
                st.session_state["sequencia"] = cse.sequenciar(dados)
            except Exception as exc:
                erro_visivel("4️⃣ Etiq 40x25", "Sequência de embalagem falhou", exc)

        seq = st.session_state.get("sequencia")
        if seq:
            c1, c2, c3 = st.columns(3)
            c1.metric("Caixas", seq["total"])
            c2.metric("Peças", seq["total_pecas"])
            c3.metric("Multi-item", seq["multi_itens"],
                      help="Vão no fim, agrupados por combinação")

            for grupo in seq["grupos"]:
                span = (f"#{grupo['de']}" if grupo["de"] == grupo["ate"]
                        else f"#{grupo['de']}–{grupo['ate']}")
                titulo = (f"{span} · **{grupo['rotulo']}** — "
                          f"{len(grupo['pedidos'])} caixa(s), {grupo['pecas']} peças")
                with st.expander(titulo, expanded=False):
                    st.dataframe(
                        pd.DataFrame([
                            {
                                "#": p["posicao"],
                                "Peças": p["total_pecas"],
                                "Cliente": str(p.get("cliente") or "")[:28],
                                "Canal": (p.get("canal") or {}).get("nome", "")
                                         if isinstance(p.get("canal"), dict)
                                         else str(p.get("canal") or ""),
                                "Pedido": p.get("numero_ecommerce", ""),
                            }
                            for p in grupo["pedidos"]
                        ]),
                        use_container_width=True, hide_index=True,
                    )

            with st.expander("📄 Versão texto — para a prancheta"):
                try:
                    import core_sequencia_embalagem as cse
                    st.text_area("Sequência:", value=cse.resumo_texto(seq),
                                 height=320, key="txt_sequencia")
                except Exception as exc:
                    erro_visivel("4️⃣ Etiq 40x25", "Resumo da sequência falhou",
                                 exc, grave=False)

    avancar(4, "Pular / concluído — ir para Embalar")

# ============================ FASE 5 — EMBALAR ============================== #
if fase(4):
    st.subheader("5️⃣ Embalar itens")
    st.caption("Monta as caixas. A gravação é opcional e serve como prova em disputa.")

    try:
        import core_video_expedicao as cv
        estado = cv.estado_sessao()
        # ⚠️ A chave e' `sessao_ativa`. Antes o codigo lia `ativa`/`gravando`,
        # que nao existem — dava sempre False e o botao de PARAR nunca
        # aparecia (Jota, 2026-08-16: "só vi a de iniciar").
        ativa = bool(estado.get("sessao_ativa"))
        pausada = bool(estado.get("pausada"))

        c1, c2 = st.columns([1, 2])
        with c1:
            if ativa and pausada:
                st.warning("⏸️ Pausada — não está gravando")
                if st.button("▶️ Retomar", type="primary",
                             use_container_width=True, key="btn_retomar_video"):
                    r = cv.retomar_sessao()
                    if r.get("ok"):
                        st.rerun()
                    else:
                        erro_visivel("5️⃣ Embalar", "Não foi possível retomar",
                                     r.get("erro", ""))
                if st.button("⏹️ Parar e salvar", use_container_width=True,
                             key="btn_parar_video_pausada"):
                    r = cv.parar_sessao(motivo="manual")
                    st.info(f"Gravação encerrada: {r.get('arquivo') or 'ok'}")
                    st.rerun()

            elif ativa:
                st.success(f"🔴 Gravando · {estado.get('segundos_ativos', 0)}s")
                if st.button("⏸️ Pausar", use_container_width=True,
                             key="btn_pausar_video",
                             help="Para de gravar sem fechar o arquivo — "
                                  "o vídeo continua no mesmo arquivo ao retomar."):
                    r = cv.pausar_sessao()
                    if r.get("ok"):
                        st.rerun()
                    else:
                        erro_visivel("5️⃣ Embalar", "Não foi possível pausar",
                                     r.get("erro", ""))
                if st.button("⏹️ Parar e salvar", type="primary",
                             use_container_width=True, key="btn_parar_video"):
                    r = cv.parar_sessao(motivo="manual")
                    st.info(f"Gravação encerrada: {r.get('arquivo') or 'ok'}")
                    st.rerun()

            else:
                st.caption("⚪ Câmera parada")
                if st.button("🎥 Iniciar gravação", type="primary",
                             use_container_width=True, key="btn_iniciar_video"):
                    try:
                        r = cv.iniciar_sessao()
                        st.success(f"Gravando: {r.get('arquivo') or 'sessão iniciada'}")
                        st.rerun()
                    except Exception as exc:
                        erro_visivel("5️⃣ Embalar", "Gravação não iniciou — embale sem vídeo ou use o gravador PROVA_*", exc)

        with c2:
            if ativa:
                d1, d2, d3 = st.columns(3)
                d1.metric("Quadros", estado.get("quadros_gravados", 0))
                d2.metric("Resolução",
                          "×".join(str(x) for x in (estado.get("resolucao") or ("?",))))
                if estado.get("segundos_pausados"):
                    d3.metric("Pausado", f"{estado['segundos_pausados']}s")
                st.caption(f"Arquivo: `{estado.get('video_arquivo', '?')}`")

        # ---- Preview: mostra o que ESTA sendo gravado -------------------- #
        # ⚠️ Sem isto grava-se as cegas — nao da' para saber se a camera pegou
        # a bancada ou o teto, nem ajustar o enquadramento (Jota, 2026-08-16).
        if ativa:
            st.markdown("##### 📹 O que está sendo gravado"
                        + (" *(pausado — imagem ao vivo, mas não grava)*"
                           if pausada else ""))

            col_img, col_ctl = st.columns([2, 1])

            with col_ctl:
                ao_vivo = st.toggle(
                    "▶️ Ao vivo", value=True, key="tgl_preview_vivo",
                    help="Atualiza sozinho a cada segundo. Desligue para "
                         "congelar a imagem.",
                )
                st.caption("Ajuste a câmera até a bancada ficar enquadrada.")
                if estado.get("dimensao_real"):
                    st.caption(f"Resolução: {estado['dimensao_real']}")

            with col_img:
                # ⚠️ O preview NAO passa pelo servidor. Antes era um
                # `st.fragment(run_every=1)`: cada quadro fazia round-trip
                # Streamlit (encode JPEG -> websocket -> redesenho), o que dava
                # ~1s de atraso e imagem travada (Jota, 2026-08-16: "lag muito
                # grande e lenta").
                #
                # Agora o <video> abre a camera direto no navegador: fluido,
                # sem atraso e sem carregar o servidor. Como o ffmpeg NAO
                # retem exclusividade da webcam no Windows, os dois convivem.
                @st.fragment(run_every=2 if ao_vivo else None)
                def _preview_ao_vivo():
                    try:
                        jpeg = cv.frame_atual_jpeg()
                        if jpeg:
                            st.image(
                                jpeg, use_container_width=True,
                                caption=f"quadro gravado · {datetime.now():%H:%M:%S}")
                        else:
                            st.info("Gravando, mas nenhum quadro chegou ainda.")
                    except Exception as exc:
                        st.warning(f"Preview indisponível: {exc}")

                _preview_ao_vivo()

        st.caption(
            "ℹ️ Grava em `E:\\Videos de PROVA - JEFCO`, retenção de 30 dias, "
            "pela webcam **USB** (`WEB CAMER`). "
            "O gravador antigo (`PROVA_*.mp4`) continua intacto como backup."
        )
    except Exception as exc:
        erro_visivel("5️⃣ Embalar", "Módulo de vídeo indisponível", exc, grave=False)

    if dados:
        st.divider()
        st.markdown("#### 📦 Pedidos a embalar")
        st.caption("Multi-itens primeiro — são os que mais erram na bancada.")
        _emb = _so_da_onda(dados["pedidos_multi_itens"], _filtro_onda())
        if st.session_state.get("onda_travada") is not None:
            st.caption(f"🔒 Só a **onda {st.session_state['onda_travada']}** "
                       f"— {len(_emb)} multi-item(ns).")
        for p in _emb:
            with st.expander(f"🚨 {p['numero_ecommerce']} — {p['cliente'][:28]} "
                             f"({p['qtd_total']} peças)"):
                for it in p["itens"]:
                    st.markdown(f"- **{it.get('quantidade')}x** `{it.get('sku')}` — "
                                f"{it.get('descricao', '')[:60]}")

    avancar(5, "Itens embalados — ir para a Bipagem")

# ============================ FASE 6 — BIPAGEM ============================== #
# ⚠️ Bipagem IRMÃ do Scanner (pages/14). Grava no MESMO banco
# (`local_db/rastreio_pedidos.db`) pelos MESMOS motores — as duas telas se
# enxergam e o celular abastece o mesmo lugar que o PC.
#
# 🔵 Sem os alertas de divergência de valor que o Scanner mostra: aqui o
# controle já vem das fases anteriores (cruzamento das 4 fontes, lista por
# átomo, sequência de embalagem). Repetir alarme atrapalharia (Jota, 2026-08-16).
#
# 🔴 NÃO alterar o pages/14 — ele fica como backup, mais simples e testado.
if fase(5):
    st.subheader("6️⃣ Bipagem — montar conferindo")
    st.caption(
        "Bipa a etiqueta de cada caixa. Funciona **na tela ou pelo celular** — "
        "os dois gravam no mesmo lugar. Pistola Bluetooth também: digita e dá Enter."
    )

    try:
        import core_scanner_db as sdb

        sdb.init_db()
        stats = sdb.stats_dia()

        # ⚠️ A chave e' `conferidos_hoje`, nao `conferidos`.
        feitos = stats.get("conferidos_hoje", 0)

        m1, m2, m3 = st.columns(3)
        m1.metric("Bipados hoje", feitos)
        if dados:
            m2.metric("Caixas no lote", dados["total_pedidos"])
            m3.metric("Faltam", max(dados["total_pedidos"] - feitos, 0))
        else:
            m2.metric("Na base", stats.get("total_indice", 0))
            m3.metric("Pendentes", stats.get("pendentes", 0))

        st.divider()

        # 📱 Na prática 99% das bipagens são pelo celular ou pela pistola
        # (Jota, 2026-08-16) — por isso o endereço do celular vem primeiro e a
        # câmera do PC é só fallback.
        # ⚠️ `gethostbyname(gethostname())` NAO serve: devolve o IP da rede
        # virtual do WSL/Docker (172.18.x.x), que o celular nao alcanca.
        # O truque do socket UDP revela o IP da placa que fala com o roteador.
        def _ip_da_rede() -> str:
            import socket

            try:
                s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                s.settimeout(0.3)
                s.connect(("8.8.8.8", 80))      # nao envia nada, so' resolve a rota
                ip = s.getsockname()[0]
                s.close()
                return ip
            except Exception:
                return ""

        _ip = _ip_da_rede()
        if _ip:
            st.info(
                f"📱 **Pelo celular:** abra **`https://{_ip}:8501`** no mesmo "
                "Wi-Fi → vá em *Lista Separacao* → fase **6 Bipagem**.\n\n"
                "O celular vai avisar que a conexão não é segura (certificado "
                "próprio): toque em **Avançado → Continuar**. Sem aceitar isso "
                "o navegador bloqueia a câmera.\n\n"
                "🔫 **Pistola Bluetooth:** clique no campo abaixo e bipe — ela "
                "digita e dá Enter sozinha."
            )
        else:
            st.info("📱 Pelo celular: abra o mesmo endereço no Wi-Fi. "
                    "🔫 Pistola: clique no campo abaixo e bipe.")

        # ---- Scanner de Câmera Oficial Consagrado (AO VIVO + Foto Nativa) ---- #
        try:
            import scanner_camera_ao_vivo as cam_ao_vivo
            cam_ao_vivo.render_camera(altura=320, botao_submit="Bipar", rearmar=True)
        except Exception as exc:
            st.warning(f"Câmera ao vivo indisponível: {exc}")

        import core_scanner_resolver as s_resolver
        import core_scanner_card as s_card

        # Estado para manter a ficha do pedido aberta na Fase 6
        if "fase6_resultado" not in st.session_state:
            st.session_state.fase6_resultado = None
        if "fase6_ultimo_codigo" not in st.session_state:
            st.session_state.fase6_ultimo_codigo = ""

        # ---- Formulário de Bipagem / Pistola Bluetooth / Digitação Manual ---- #
        with st.form("form_bipagem_fase6", clear_on_submit=True):
            col_inp, col_btn = st.columns([3, 1])
            with col_inp:
                codigo = st.text_input(
                    "Código de rastreio, nº do pedido ou 3+ caracteres:",
                    key="bip_codigo",
                    placeholder="Ex: AP296430628BR · 260802B4MD9MHU · ou pistola Bluetooth",
                    label_visibility="collapsed",
                )
            with col_btn:
                btn_bipar = st.form_submit_button("🔍 Bipar", use_container_width=True, type="primary")

        if codigo and len(codigo.strip()) >= 3:
            termo = codigo.strip()
            # 1. Tenta resolver diretamente como pedido oficial
            res_direto = s_resolver.resolver_codigo(termo)
            if res_direto and res_direto.get("encontrado"):
                st.session_state.fase6_resultado = res_direto
                st.session_state.fase6_ultimo_codigo = termo
            else:
                st.session_state.fase6_ultimo_codigo = termo
                achados = sdb.buscar_parcial(termo, limit=8)
                if not achados:
                    st.warning(f"Nada encontrado para `{termo}`. "
                               "Rode **Atualizar separação** se a venda é nova.")
                    st.session_state.fase6_resultado = None
                elif len(achados) == 1:
                    # Match único -> já resolve direto
                    res_unico = s_resolver.resolver_codigo(achados[0].get("tracking") or termo)
                    if res_unico and res_unico.get("encontrado"):
                        st.session_state.fase6_resultado = res_unico
                    else:
                        st.session_state.fase6_resultado = None
                else:
                    st.session_state.fase6_resultado = None
                    st.caption(f"{len(achados)} resultados — escolha qual abrir:")
                    for achado in achados:
                        _trk = achado.get("tracking") or ""
                        _ped = achado.get("pedido_ecommerce") or ""
                        _img = achado.get("imagem_url") or ""
                        _nome = (achado.get("produto_nome") or achado.get("sku_principal") or "")[:42]
                        _cli = (achado.get("cliente_nome") or "")[:20]
                        _ja = sdb.ja_conferido_hoje(_trk)

                        c_img, c_btn = st.columns([1, 9], vertical_alignment="center")
                        with c_img:
                            if _img:
                                st.image(_img, width=48)
                            else:
                                st.markdown("<div style='width:48px;height:48px;border-radius:6px;background:#1e293b;display:flex;align-items:center;justify-content:center;font-size:18px;'>📦</div>", unsafe_allow_html=True)
                        with c_btn:
                            rotulo = f"{'✅ ' if _ja else '📦 '}{_trk} · {achado.get('canal', '').upper()} · {_nome}" + (f" · {_cli}" if _cli else "")
                            if st.button(rotulo, key=f"btn_sug_f6_{_trk}", use_container_width=True):
                                res_sug = s_resolver.resolver_codigo(_trk)
                                if res_sug and res_sug.get("encontrado"):
                                    st.session_state.fase6_resultado = res_sug
                                    st.session_state.fase6_ultimo_codigo = _trk
                                    st.rerun()

        # ---- RENDERIZAÇÃO DA FICHA VISUAL DO PEDIDO (CARD COMPLETO DO SCANNER) ---- #
        res_f6 = st.session_state.get("fase6_resultado")
        if res_f6 and res_f6.get("encontrado"):
            _trk_f6 = res_f6.get("tracking") or st.session_state.get("fase6_ultimo_codigo") or ""
            _ja_conf = sdb.ja_conferido_hoje(_trk_f6) if _trk_f6 else False

            s_card.render_ficha_pedido(res_f6, ja_conferido=_ja_conf)

            # Botões de Ação da Ficha
            c_act1, c_act2 = st.columns([2, 1])
            with c_act1:
                rot_btn = "⚠️ JÁ BIPADO HOJE (Bipar novamente)" if _ja_conf else "✅ MARCAR COMO CONFERIDO / BIPADO"
                if st.button(rot_btn, key="btn_confirmar_bip_f6", type="primary", use_container_width=True):
                    ok = sdb.registrar_conferencia(
                        tracking=_trk_f6,
                        pedido_ecommerce=res_f6.get("pedido_ecommerce") or "",
                        canal=(res_f6.get("canal") or "").upper(),
                        sku_principal=res_f6.get("sku") or "",
                        status="cancelado" if res_f6.get("cancelado") else "conferido",
                    )
                    if ok:
                        try:
                            import core_video_expedicao as cvx
                            cvx.sinalizar_atividade_e_obter_indice(tracking=_trk_f6, auto_iniciar=False)
                        except Exception:
                            pass
                        st.success(f"✅ Pedido `{_trk_f6}` conferido e registrado!")
                        st.session_state.fase6_resultado = None
                        st.session_state.fase6_ultimo_codigo = ""
                        st.rerun()
                    else:
                        erro_visivel("6️⃣ Bipagem", f"Não foi possível registrar {_trk_f6}", "registrar_conferencia devolveu False")
            with c_act2:
                if st.button("📷 Ler outro / Limpar", key="btn_limpar_ficha_f6", use_container_width=True):
                    st.session_state.fase6_resultado = None
                    st.session_state.fase6_ultimo_codigo = ""
                    st.rerun()

        # ---- Últimas bipagens ------------------------------------------- #
        ultimas = sdb.ultimas_conferencias(limit=12)
        if ultimas:
            with st.expander(f"🕒 Últimas {len(ultimas)} bipagens"):
                st.dataframe(
                    pd.DataFrame([
                        {
                            "Hora": str(u.get("conferido_em") or "")[11:19],
                            "Rastreio": u.get("tracking", ""),
                            "Canal": (u.get("canal") or "").upper(),
                            "Pedido": u.get("pedido_ecommerce", ""),
                        }
                        for u in ultimas
                    ]),
                    use_container_width=True, hide_index=True,
                )
    except Exception as exc:
        erro_visivel("6️⃣ Bipagem", "Bipagem indisponível", exc)
        st.info("Use o **Scanner de Conferência** (página 14) como alternativa.")

    avancar(6, "Bipagem feita — ir para a Conferência final")

# ========================= FASE 7 — CONFERENCIA FINAL ======================= #
if fase(6):
    st.subheader("7️⃣ Conferência final — fechar a bolsa de despacho")
    st.caption("Última checagem antes de colocar na bolsa: bipe cada pacote para garantir 100% de precisão.")

    # Inicializa estados de conferência final se necessário
    if "conf_final_bipados" not in st.session_state:
        st.session_state.conf_final_bipados = {}
    if "conf_final_fora_lista" not in st.session_state:
        st.session_state.conf_final_fora_lista = []
    if "conf_final_ultimo" not in st.session_state:
        st.session_state.conf_final_ultimo = None

    # Monta a lista de esperados a partir dos pedidos do lote
    pedidos_lote = st.session_state.get("pedidos_brutos", [])
    esperados = []
    for p in pedidos_lote:
        esperados.append({
            "tracking": p.get("tracking") or p.get("codigo_rastreamento") or "",
            "pedido_ecommerce": str(p.get("numero_ecommerce") or p.get("numero") or ""),
            "canal": p.get("canal") or p.get("origem") or "MARKETPLACE",
            "produto": p.get("descricao") or p.get("produto_nome") or "",
            "sku": p.get("sku") or p.get("codigo") or "",
            "cliente": (p.get("cliente") or {}).get("nome") if isinstance(p.get("cliente"), dict) else str(p.get("cliente") or ""),
        })

    total_esperados = len(esperados)
    bipados_dict = st.session_state.conf_final_bipados
    conferidos_qtd = len([1 for e in esperados if (e["tracking"] and e["tracking"] in bipados_dict) or (e["pedido_ecommerce"] and e["pedido_ecommerce"] in bipados_dict)])
    faltam_qtd = max(total_esperados - conferidos_qtd, 0)

    # Métricas de progresso
    m1, m2, m3 = st.columns(3)
    m1.metric("📦 No lote a despachar", total_esperados)
    m2.metric("✅ Conferidos na bolsa", conferidos_qtd)
    m3.metric("⏳ Faltam conferir", faltam_qtd)

    if total_esperados > 0:
        st.progress(min(conferidos_qtd / total_esperados, 1.0))

    # Feedback do último bip
    ult = st.session_state.conf_final_ultimo
    if ult:
        if ult.get("status") == "ok":
            it = ult.get("item", {})
            st.success(f"✅ **CONFERIDO COM SUCESSO**: `{ult.get('codigo')}` — {it.get('canal','')} · {it.get('produto') or it.get('sku')}")
        elif ult.get("status") == "duplicado":
            st.warning(f"⚠️ **PACOTE JÁ CONFERIDO ANTERIORMENTE**: `{ult.get('codigo')}` (Lido {ult.get('vezes', 2)}x)")
        else:
            st.error(f"🚨 **PACOTE NÃO ENCONTRADO NO LOTE DE HOJE**: `{ult.get('codigo')}`")

    # ---- Câmera ao Vivo Consagrada para Conferência Final ---- #
    try:
        import scanner_camera_ao_vivo as cam_ao_vivo
        cam_ao_vivo.render_camera(altura=280, botao_submit="Conferir Final", rearmar=True)
    except Exception as exc:
        st.warning(f"Câmera ao vivo indisponível: {exc}")

    # ---- Formulário de Bipagem / Pistola Bluetooth ---- #
    with st.form("form_conf_final_bipagem", clear_on_submit=True):
        c_inp, c_btn = st.columns([3, 1])
        with c_inp:
            cod_final = st.text_input(
                "Código da etiqueta",
                placeholder="Ex: AP296430628BR — bipe com a câmera, pistola ou digite",
                label_visibility="collapsed",
                key="inp_conf_final",
            )
        with c_btn:
            btn_sub = st.form_submit_button("🔍 Conferir Final", use_container_width=True, type="primary")

        if btn_sub and cod_final and cod_final.strip():
            termo = cod_final.strip()
            # Procura nos esperados por tracking ou pedido
            match = None
            for e in esperados:
                t = e["tracking"]
                p = e["pedido_ecommerce"]
                if (t and termo.lower() in t.lower()) or (p and termo.lower() in p.lower()):
                    match = e
                    break

            if match:
                chave = match["tracking"] or match["pedido_ecommerce"]
                if chave in bipados_dict:
                    bipados_dict[chave] += 1
                    st.session_state.conf_final_ultimo = {"status": "duplicado", "codigo": termo, "item": match, "vezes": bipados_dict[chave]}
                else:
                    bipados_dict[chave] = 1
                    st.session_state.conf_final_ultimo = {"status": "ok", "codigo": termo, "item": match}
            else:
                st.session_state.conf_final_fora_lista.append(termo)
                st.session_state.conf_final_ultimo = {"status": "fora_lista", "codigo": termo}
            st.rerun()

    st.divider()

    # ---- Ações e Relatório de Cruzamento ---- #
    col_a1, col_a2 = st.columns(2)
    with col_a1:
        if st.button("🔍 Cruzar Auditoria Completa (4 Fontes)", use_container_width=True):
            with st.spinner("Auditando..."):
                try:
                    import core_cruzamento_expedicao as cce
                    st.session_state.cruzamento = cce.cruzar(situacoes=situacoes_sel)
                    st.success("Auditoria realizada!")
                except Exception as exc:
                    st.error(f"Falha na auditoria: {exc}")
    with col_a2:
        if st.button("🧹 Zerar Bipagens da Conferência", use_container_width=True):
            st.session_state.conf_final_bipados = {}
            st.session_state.conf_final_fora_lista = []
            st.session_state.conf_final_ultimo = None
            st.rerun()

    if st.session_state.get("cruzamento"):
        cruz = st.session_state.cruzamento
        st.markdown(f"**{cruz.get('resumo', '')}**")
        for d in cruz.get("divergencias", []):
            st.warning(f"{d.get('gravidade', '🟡')} **{d.get('tipo', '')}** · `{d.get('chave', '')}` — {d.get('detalhe', '')}")

    st.divider()
    if st.button("🔄 Concluir Expedição e Começar Novo Lote", type="primary", use_container_width=True, key="btn_novo_lote"):
        st.session_state.fase_atual = 0
        st.session_state.conf_final_bipados = {}
        st.session_state.conf_final_ultimo = None
        limpar_erros()
        st.rerun()


# ---------------------------------------------------------------------------- #
# PAINEL DE ERROS — fim da pagina, fora das abas
# ---------------------------------------------------------------------------- #
# 🔴 Fica FORA das abas para aparecer em qualquer fase. Nada de erro escondido
# dentro de um try/except mudo: o que o sistema nao entendeu vem para ca'.
st.divider()

# --------------------------- LOG DE SINCRONIZACAO --------------------------- #
with st.expander("🔄 Log de sincronizações — o que foi baixado e quando"):
    ultimo = st.session_state.get("ultimo_sync")
    if ultimo:
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Na fila", ultimo["total"])
        c2.metric("Do cache", ultimo["do_cache"],
                  help="Não precisou de rede")
        c3.metric("Baixados", ultimo["baixados"],
                  help="Só o que faltava")
        c4.metric("Tempo", f"{ultimo['segundos']}s")

        if ultimo.get("sairam"):
            st.caption(f"↩️ {len(ultimo['sairam'])} pedido(s) saíram da fila "
                       "(mudaram de situação no Olist).")

    try:
        import core_sync_expedicao as sync_mod
        linhas = sync_mod.historico(limite=20)
        if linhas:
            st.dataframe(
                pd.DataFrame([
                    {
                        "Quando": h["quando"],
                        "Tipo": "🔴 RESET" if h.get("reset") else "⚡ incremental",
                        "Fila": h["total"],
                        "Cache": h["do_cache"],
                        "Baixou": h["baixados"],
                        "Novos": h["novos"],
                        "Saíram": h["sairam"],
                        "Seg": h["segundos"],
                    }
                    for h in linhas
                ]),
                use_container_width=True, hide_index=True,
            )
        else:
            st.caption("Nenhuma sincronização registrada ainda.")
    except Exception as exc:
        st.caption(f"Histórico indisponível: {exc}")

st.divider()

_erros = st.session_state.erros
_graves = [e for e in _erros if e["grave"]]

if not _erros:
    st.success("✅ Nenhum erro registrado nesta sessão.")
else:
    st.subheader(f"🔴 Erros e itens não reconhecidos ({len(_erros)})")
    st.caption(
        "Tudo que o sistema não entendeu ou não conseguiu fazer. "
        "**Não some sozinho** — se for item de pedido, confira antes de "
        "despachar: peça não reconhecida pode sair faltando da caixa."
    )

    if _graves:
        st.error(f"**{len(_graves)} erro(s) grave(s)** — confira antes de fechar o lote.")

    for e in _erros:
        cabecalho = f"{'🔴' if e['grave'] else '🟡'} [{e['fase']}] {e['titulo']}"
        with st.expander(cabecalho, expanded=e["grave"]):
            st.caption(f"às {e['hora']}")
            if e["detalhe"]:
                st.code(e["detalhe"], language=None)

    c_err1, c_err2 = st.columns([1, 1])
    with c_err1:
        if st.button("🧹 Limpar lista de erros", key="btn_limpar_erros", use_container_width=True):
            limpar_erros()
            st.rerun()
    with c_err2:
        if st.button("📸 Gravar Snapshot dos Erros Atuais", key="btn_snap_erros_auto", use_container_width=True, type="secondary"):
            try:
                import core_log_manutencao as clm
                msg_auto = f"Registro automático: {len(_graves)} erro(s) grave(s), total {len(_erros)} erro(s) na sessão."
                snap = clm.capturar_snapshot(st.session_state, msg_auto, "🔴 Erro / Bug Operacional")
                if clm.salvar_log(snap):
                    st.success("✅ **Snapshot dos erros capturado e gravado com sucesso!**")
            except Exception as e_snap:
                st.error(f"Falha ao gravar snapshot: {e_snap}")

# ---------------------------------------------------------------------------- #
# 📝 ANOTAÇÕES E SNAPSHOT DE MANUTENÇÃO (PARA VIOLINO / CLAUDE)
# ---------------------------------------------------------------------------- #
st.divider()
st.subheader("📝 Anotar Erros e Melhorias para Próxima Manutenção")
st.caption(
    "Registre observações, falhas ou sugestões. Ao salvar, o sistema **raspa e "
    "grava o snapshot completo da tela** (fila, erros, sync e estado da esteira) "
    "para que a IA (Violino / Claude) possa analisar e agir na manutenção."
)

with st.container(border=True):
    c_m1, c_m2 = st.columns([1, 3])
    with c_m1:
        tipo_log = st.selectbox(
            "Tipo de Registro:",
            ["🔴 Erro / Bug Operacional", "💡 Melhoria / Sugestão", "🔑 Alerta de Token / API", "📌 Nota Geral"],
            key="sel_tipo_manutencao",
        )
    with c_m2:
        nota_txt = st.text_area(
            "Descreva o ocorrido ou a melhoria desejada:",
            placeholder="Ex: Olist deu token expirado na Fase 1; ou: Ajustar ordenação da lista...",
            key="txt_nota_manutencao",
            height=85,
        )

    c_b1, c_b2 = st.columns([2, 1])
    with c_b1:
        if st.button("📸 Salvar Anotação + Snapshot Completo da Tela", key="btn_salvar_snapshot_manut", type="primary", use_container_width=True):
            if not nota_txt.strip():
                st.warning("⚠️ Por favor, escreva uma breve descrição antes de salvar.")
            else:
                try:
                    import core_log_manutencao as clm
                    snap = clm.capturar_snapshot(st.session_state, nota_txt, tipo_log)
                    if clm.salvar_log(snap):
                        st.success("✅ **Anotação e Snapshot gravados com sucesso!** O Violino e o Claude terão acesso a todo o contexto da tela na próxima manutenção.")
                    else:
                        st.error("❌ Falha ao gravar snapshot no banco local.")
                except Exception as e_snap:
                    st.error(f"❌ Erro ao capturar snapshot: {e_snap}")

    with c_b2:
        ver_historico = st.toggle("📂 Ver Histórico de Logs", key="tgl_ver_logs_manut")

    if ver_historico:
        try:
            import core_log_manutencao as clm
            logs_gravados = clm.listar_logs(limite=10)
            if not logs_gravados:
                st.info("Nenhum log gravado ainda.")
            else:
                for lg in logs_gravados:
                    with st.expander(f"{lg['data_hora']} · {lg['tipo']} · {lg['nota_usuario'][:50]}..."):
                        st.write(f"**Nota:** {lg['nota_usuario']}")
                        st.write(f"**Fase:** {lg['fase_atual']} | **Onda:** {lg['onda_travada']} | **Erros na Tela:** {lg['total_erros']}")
                        st.json(lg.get("snapshot", {}))
        except Exception as e_hist:
            st.caption(f"Não foi possível carregar histórico: {e_hist}")

