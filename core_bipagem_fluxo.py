# ==============================================================================
# NOME DO SCRIPT: core_bipagem_fluxo.py
# DESCRICAO: Fluxo de leitura do app dedicado de bipagem (resolver codigo, validar
#            peca, limpar leitura) sobre o estado da sessao do Streamlit.
# STATUS: ATIVO
# VERSAO: 1.0
# DATA: 04/10/2026
# AUTOR: Terminador (001) / Claude
# ==============================================================================
"""Logica de leitura do app dedicado (`app_bipagem.py`).

As tres funcoes (`processar_codigo`, `limpar_leitura`, `validar_produto`) sao
COPIA FIEL das de `pages/14_Scanner_Conferencia.py` -- mesmas regras de
sanitizacao, sons e verificacao dobrada. Ficam aqui, num modulo, para o app
dedicado nao depender de importar uma pagina inteira de 1.800 linhas. A pagina
Scanner continua com as suas.
"""

from __future__ import annotations

import streamlit as st

import core_scanner_auditoria as auditoria
import core_scanner_db as db
import core_scanner_decoder as decoder
import core_scanner_resolver as resolver
import core_scanner_som as som
import core_scanner_validador as validador

_DEFAULTS = {
    "scanner_resultado": None,
    "scanner_ultimo_codigo": "",
    "scanner_validacao": None,
    "scanner_sessao_conferidos": 0,
    "scanner_sessao_pulados": 0,
    "scanner_sessao_cancelados": 0,
    "scanner_sessao_validados": 0,
    "scanner_sessao_divergencias": 0,
    "scanner_msg_sync": "",
}


def iniciar_estado() -> None:
    for k, v in _DEFAULTS.items():
        if k not in st.session_state:
            st.session_state[k] = v


def limpar_leitura() -> None:
    """Limpa a ficha atual e deixa a camera pronta pro proximo pedido."""
    st.session_state.scanner_resultado = None
    st.session_state.scanner_ultimo_codigo = ""
    st.session_state.scanner_validacao = None


def processar_codigo(codigo: str) -> None:
    """Resolve o codigo lido e deixa a ficha pronta.

    Sanitiza antes de resolver: a pistola le tudo que estiver no campo de visao
    (rastreio + chave da NF-e + CEP). `sanitizar_codigo` descarta o que nunca
    identifica pedido e separa rastreios colados.
    """
    bruto = db.normalizar_codigo(codigo)
    if not bruto:
        return
    limpo = decoder.sanitizar_codigo(bruto)
    if not limpo:
        st.session_state.scanner_ultimo_codigo = bruto
        st.session_state.scanner_resultado = {
            "encontrado": False,
            "codigo_invalido": True,
            "motivo": "Esse código é da nota fiscal ou do CEP — não identifica "
                      "o pedido. Bipe o código de RASTREIO da etiqueta.",
        }
        st.session_state.scanner_som = som.ERRO
        return
    st.session_state.scanner_ultimo_codigo = limpo
    st.session_state.scanner_resultado = resolver.resolver_codigo(limpo)

    # Som marcado AQUI (na leitura nova), nao no render: o Streamlit re-executa a
    # pagina inteira a cada interacao e tocar no render repetiria o bip.
    _r = st.session_state.scanner_resultado or {}
    if not _r.get("encontrado"):
        st.session_state.scanner_som = som.ERRO
    elif len(db.desserializar_itens(_r)) > 1:
        # Multi-item: som proprio pra nao ser confundido com o "pode seguir".
        st.session_state.scanner_som = som.ATENCAO
    else:
        st.session_state.scanner_som = som.OK

    # LEI DA VERIFICACAO DOBRADA (Jota, 2026-08-12): confirma em SEGUNDA FONTE
    # (API do marketplace), em background, sem travar a bancada.
    auditoria.auditar_async(limpo)


def registrar_na_nuvem(tracking: str, *, cancelado: bool = False) -> bool:
    """Grava a conferencia no Supabase -- a MESMA base do PC e da Esteira.

    O `db.registrar_conferencia` grava so' no SQLite local, e na nuvem esse disco
    some quando o app reinicia (e o PC nunca ve). Aqui a conferencia fica duravel.
    Falha NAO e' silenciosa: deixa um aviso para a tela mostrar.
    """
    ok = False
    try:
        import core_scanner_supabase as cloud_db
        ok = bool(cloud_db.registrar_conferencia_nuvem(
            tracking, conferido_por="bipagem-cancelado" if cancelado else "bipagem"))
    except Exception:
        ok = False
    if not ok:
        st.session_state["bip_aviso_nuvem"] = (
            f"⚠️ A conferência de {tracking} NÃO foi gravada na nuvem. "
            "Confira a conexão e bipe de novo se precisar.")
    return ok


def sugestoes_na_base(codigo: str, limite: int = 4) -> list[dict]:
    """Pedidos PARECIDOS com um codigo nao encontrado (leitura com 1 digito errado).

    Procura na base compartilhada pelo COMECO e pelo FIM do codigo lido (8 caracteres) em
    rastreio, shipment e numero do pedido. A pagina Scanner antiga tinha a busca por parte
    do codigo; o app dedicado nao -- uma leitura ruim virava beco sem saida.
    """
    import re
    c = re.sub(r"[^A-Za-z0-9]", "", str(codigo or "")).upper()
    if len(c) < 5:
        return []
    pedacos = {c} if len(c) < 9 else {c[:8], c[-8:]}
    achados: dict[str, dict] = {}
    try:
        import core_scanner_supabase as cloud_db
        for p in pedacos:
            r = cloud_db._requisicao_supabase(
                "GET", "rastreio_pedidos_expedicao",
                params={"or": f"(tracking.ilike.*{p}*,shipment_id.ilike.*{p}*,pedido_ecommerce.ilike.*{p}*)",
                        "select": "tracking,canal,sku_principal,produto_nome", "limit": str(limite)})
            for x in (r if isinstance(r, list) else []):
                if x.get("tracking"):
                    achados.setdefault(x["tracking"], x)
    except Exception:
        return []
    return list(achados.values())[:limite]


def validar_produto(codigo_peca: str) -> None:
    """Cruza a etiqueta de produto bipada com o SKU do pedido em tela."""
    res_atual = st.session_state.scanner_resultado or {}
    st.session_state.scanner_validacao = validador.validar(
        res_atual.get("sku", ""), codigo_peca
    )
    _v = st.session_state.scanner_validacao or {}
    if _v.get("ok"):
        st.session_state.scanner_som = som.OK
    elif _v.get("nivel") == "sem_dados":
        st.session_state.scanner_som = som.ATENCAO
    else:
        st.session_state.scanner_som = som.ERRO
