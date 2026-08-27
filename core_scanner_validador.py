# ==============================================================================
# NOME DO SCRIPT: core_scanner_validador.py
# DESCRICAO: Cruza o codigo da etiqueta de PRODUTO (SKU/SPU) com o SKU do
#            pedido resolvido pela etiqueta de ENVIO. Segunda barreira da
#            conferencia: garante que a peca na mao e' a do pedido.
# AUTOR: Terminador (001)
# VERSAO: 1.0 | DATA: 2026-08-09
# STATUS: Operacional
# ==============================================================================
"""Validacao cruzada SKU do pedido x SKU bipado da etiqueta de produto.

Por que nao e' uma comparacao de string simples: o SKU do pedido carrega o
sufixo de kit (``MEINVMAY1013540-PRE12_KIT12``), enquanto a etiqueta fisica
colada na peca traz o SKU do atomo (``MEINVMAY1013540PRE``). Alem disso as
etiquetas de produto sem tamanho usam o formato ``SPU-COR``
(``TOPTAY016-AZUL``). Os tres formatos precisam casar entre si.

Niveis de resultado (do mais forte pro mais fraco):
    exato   -> codigos identicos
    atomo   -> etiqueta e' o atomo do SKU de kit do pedido
    spu_cor -> mesmo SPU e mesma cor, tamanho nao confirmado
    spu     -> mesmo SPU, cor/tamanho nao batem
    erro    -> produto diferente
"""

from __future__ import annotations

import re

# Cores conhecidas no padrao J&F (3 letras) e seus nomes por extenso, que
# aparecem nas etiquetas SPU-COR do Top (TOPTAY016-AZUL, ...-PRETO, ...-ROSA).
_CORES = {
    "BRA": ("BRANCA", "BRANCO"),
    "PRE": ("PRETA", "PRETO"),
    "CIN": ("CINZA",),
    "ROS": ("ROSA",),
    "SOR": ("SORTIDA", "SORTIDO"),
    "AZM": ("AZUL MARINHO", "AZULMARINHO"),
    "AZU": ("AZUL",),
    "BEG": ("BEGE",),
    "VER": ("VERMELHA", "VERMELHO"),
    "VRD": ("VERDE",),
    "AMA": ("AMARELA", "AMARELO"),
    "NUD": ("NUDE",),
}

# nome por extenso -> sigla de 3 letras
_COR_POR_EXTENSO = {
    nome: sigla for sigla, nomes in _CORES.items() for nome in nomes
}


def normalizar(codigo: str) -> str:
    """Uppercase, sem espacos nas pontas e sem caracteres invisiveis."""
    if not codigo:
        return ""
    return re.sub(r"\s+", "", str(codigo)).strip().upper()


def extrair_spu(codigo: str) -> str:
    """SPU = prefixo do produto + marca + numeracao (ex: MEINVMAY1013540).

    Aceita tanto o SKU cru (``MEINVMAY1013540PRE``) quanto o de kit
    (``MEINVMAY1013540-PRE12_KIT12``) e o formato SPU-COR
    (``TOPTAY016-AZUL``).
    """
    s = normalizar(codigo)
    if not s:
        return ""
    # corta no primeiro separador de variacao/kit
    s = s.split("-")[0].split("_")[0]
    # PREFIXO(3+ letras) + MARCA(3 letras) + digitos
    m = re.match(r"([A-Z]{3,6}[A-Z]{3}\d{3,10})", s)
    if m:
        return m.group(1)
    # fallback: tudo ate o ultimo digito
    m = re.match(r"([A-Z]+\d+)", s)
    return m.group(1) if m else s


def extrair_cor(codigo: str) -> str:
    """Devolve a sigla de 3 letras da cor, ou "" se nao identificar.

    Entende as tres formas em circulacao:
        MEINVMAY1013540PRE          -> PRE  (sufixo colado)
        MEINVMAY1013540-PRE12_KIT12 -> PRE  (bloco de kit)
        TOPTAY016-AZUL              -> AZU  (nome por extenso)
    """
    s = normalizar(codigo)
    if not s:
        return ""

    # 1) sufixo por extenso depois do hifen (etiquetas SPU-COR)
    if "-" in s:
        sufixo = s.split("-", 1)[1].split("_")[0]
        # tira quantidade colada (PRE12 -> PRE)
        sufixo_sem_num = re.sub(r"\d+$", "", sufixo)
        if sufixo_sem_num in _COR_POR_EXTENSO:
            return _COR_POR_EXTENSO[sufixo_sem_num]
        if sufixo_sem_num in _CORES:
            return sufixo_sem_num

    # 2) nome por extenso colado no fim, apos o SPU (SKUs do Top:
    #    TOPTAY016 + M + AZUL). Testa do mais longo pro mais curto pra
    #    "AZUL MARINHO" nao ser confundido com "AZUL".
    resto = s[len(extrair_spu(s)):] if extrair_spu(s) else s
    for nome in sorted(_COR_POR_EXTENSO, key=len, reverse=True):
        if resto.endswith(nome):
            return _COR_POR_EXTENSO[nome]

    # 3) sigla de 3 letras no fim do codigo (SKU cru)
    m = re.search(r"([A-Z]{3})$", s)
    if m and m.group(1) in _CORES:
        return m.group(1)

    # 4) qualquer sigla conhecida em bloco de kit (-PRE12_, -BRA3-)
    for sigla in _CORES:
        if re.search(rf"[-_]{sigla}\d*[-_]", s) or re.search(rf"[-_]{sigla}\d*$", s):
            return sigla

    return ""


def atomo_do_sku(sku: str) -> str:
    """Reduz o SKU de kit ao atomo: SPU + cor colada.

    ``MEINVMAY1013540-PRE12_KIT12`` -> ``MEINVMAY1013540PRE``
    ``MEINVMAY1013540PRE``          -> ``MEINVMAY1013540PRE`` (ja e' atomo)
    """
    s = normalizar(sku)
    if not s:
        return ""
    spu = extrair_spu(s)
    cor = extrair_cor(s)
    return f"{spu}{cor}" if spu and cor else spu or s


def validar(sku_pedido: str, codigo_lido: str) -> dict:
    """Cruza o SKU do pedido com o codigo bipado da etiqueta de produto.

    Retorna dict com:
        ok        -> bool, True se pode despachar
        nivel     -> exato | atomo | spu_cor | spu | erro | sem_dados
        titulo    -> texto curto pro card
        detalhe   -> explicacao pro operador
        esperado  -> SKU do pedido (normalizado)
        lido      -> codigo bipado (normalizado)
    """
    esperado = normalizar(sku_pedido)
    lido = normalizar(codigo_lido)

    if not esperado or not lido:
        return {
            "ok": False,
            "nivel": "sem_dados",
            "titulo": "⚠️ SEM DADOS PARA COMPARAR",
            "detalhe": "Pedido sem SKU cadastrado ou código não lido.",
            "esperado": esperado,
            "lido": lido,
        }

    if esperado == lido:
        return {
            "ok": True,
            "nivel": "exato",
            "titulo": "🟢 CONFERE — PODE DESPACHAR",
            "detalhe": "Código idêntico ao SKU do pedido.",
            "esperado": esperado,
            "lido": lido,
        }

    atomo_pedido = atomo_do_sku(esperado)
    atomo_lido = atomo_do_sku(lido)

    if atomo_pedido and atomo_pedido == atomo_lido:
        return {
            "ok": True,
            "nivel": "atomo",
            "titulo": "🟢 CONFERE — PODE DESPACHAR",
            "detalhe": f"Peça {atomo_lido} bate com o pedido (kit).",
            "esperado": esperado,
            "lido": lido,
        }

    spu_pedido = extrair_spu(esperado)
    spu_lido = extrair_spu(lido)
    cor_pedido = extrair_cor(esperado)
    cor_lida = extrair_cor(lido)

    if spu_pedido and spu_pedido == spu_lido:
        if cor_pedido and cor_lida and cor_pedido == cor_lida:
            return {
                "ok": True,
                "nivel": "spu_cor",
                "titulo": "🟢 CONFERE — PODE DESPACHAR",
                "detalhe": f"Mesmo produto e cor ({cor_lida}). Confira o tamanho.",
                "esperado": esperado,
                "lido": lido,
            }
        return {
            "ok": False,
            "nivel": "spu",
            "titulo": "🟡 PRODUTO CERTO, VARIAÇÃO DIFERENTE",
            "detalhe": (
                f"Mesmo produto ({spu_pedido}), mas a cor não confere: "
                f"pedido {cor_pedido or '—'} × etiqueta {cor_lida or '—'}."
            ),
            "esperado": esperado,
            "lido": lido,
        }

    return {
        "ok": False,
        "nivel": "erro",
        "titulo": "🔴 PRODUTO ERRADO — NÃO DESPACHAR",
        "detalhe": "O código da peça não corresponde ao pedido.",
        "esperado": esperado,
        "lido": lido,
    }
