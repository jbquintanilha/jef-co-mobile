# ==============================================================================
# NOME DO SCRIPT: core_scanner_resolver.py
# DESCRICAO: Motor de resolucao do Scanner de Conferencia J&F Co. Dado um codigo
#            lido da etiqueta (tracking ou numero de pedido), resolve o pedido
#            correspondente em cascata: indice SQLite -> Olist ao vivo.
# AUTOR: Conselho J&F Co. - Roo Code (sub-gerente operacional)
# VERSAO: 1.0
# DATA: 2026-08-02
# STATUS: Operacional
# REF: plans/scanner_conferencia_pedidos_2026-08-02.md
# ==============================================================================
"""Motor de resolucao rastreio/pedido -> pedido de conferencia.

Estrategia em cascata (``resolver_codigo``):

1. Normaliza o codigo (caixa alta, sem espacos extras).
2. Se o codigo contem ``Pedido:`` (etiqueta Shopee) -> extrai o numero do pedido.
3. Match exato por tracking no indice SQLite.
4. Match exato por pedido_ecommerce no indice SQLite.
5. Busca ao vivo no Olist por numeroPedidoEcommerce (pedido extraido ou codigo cru).
6. Retorna ``None`` se nada bater -> a UI mostra "nao encontrado".

Uso:
    from core_scanner_resolver import resolver_codigo
    r = resolver_codigo("BR266773820648X")   # tracking
    r = resolver_codigo("Pedido: 260802B4MD9MHU")  # etiqueta Shopee
"""

from __future__ import annotations

import logging
import re
import threading
import time

import core_scanner_db as db

log = logging.getLogger("core_scanner_resolver")

# Mapa de cores J&F (sufixo de 3 letras no SKU -> nome legivel). Auxiliar da UI.
_CORES = {
    "PRE": "Preto",
    "BEG": "Bege",
    "BRA": "Branco",
    "AZU": "Azul",          # faltava: Top Fitness usa AZU (relatos #1 e #2)
    "AZM": "Azul Marinho",
    "AZC": "Azul Claro",
    "CIN": "Cinza",
    "RUB": "Rubi",
    "SOR": "Sortido",
    "VER": "Verde",
    "VIN": "Vinho",
    "ROS": "Rosa",
    "LIL": "Lilas",
    "NUD": "Nude",
    "CRE": "Creme",
    "AME": "Amarelo",
    "LAR": "Laranja",
    "MAR": "Marrom",
    "MIS": "Misto",
    "DIV": "Diversos",
}

# Cliente Olist reutilizado (evita refresh de token a cada chamada).
_OLIST_CLIENT = None


def _client_olist():
    """Retorna o OlistClient unico do processo (lazy)."""
    global _OLIST_CLIENT
    if _OLIST_CLIENT is None:
        from core_olist import OlistClient

        _OLIST_CLIENT = OlistClient()
    return _OLIST_CLIENT


# ------------------------------------------------------------------ #
# Extração de dados auxiliares
# ------------------------------------------------------------------ #
def extrair_numero_pedido(codigo: str) -> str | None:
    """Extrai o numero do pedido de um codigo no formato da etiqueta Shopee.

    Formatos aceitos:
      - ``Pedido: 260802B4MD9MHU``
      - ``N do pedido 260802B4MD9MHU``
      - codigo que ja seja um numero alfanumerico razoavel (>= 8 chars)

    Retorna o numero ou None.
    """
    if not codigo:
        return None
    c = codigo.strip()
    # 1. Formato rotulado (ex: "Pedido: 260802B4MD9MHU")
    m = re.search(
        r"(?:pedido|pedido\s*n[ºo]?\.?|numero\s*do\s*pedido)\s*[:#\-]?\s*"
        r"([A-Z0-9]{6,})",
        c,
        re.IGNORECASE,
    )
    if m:
        return m.group(1)
    # 2. Codigo "nu" com cara de numero de pedido (alfanumerico com digitos)
    m2 = re.fullmatch(r"[A-Z0-9]{8,}", c, re.IGNORECASE)
    if m2:
        return m2.group(0).upper()
    return None


def _extrair_cor(item: dict) -> str:
    """Tenta extrair a cor do item (grade do Olist ou sufixo do SKU)."""
    prod = item.get("produto") or item
    # 1. Grade da variacao (fonte primaria)
    for g in prod.get("grade") or []:
        chave = str(g.get("chave") or "").lower()
        if chave in ("cor", "color", "cor_especifica"):
            return str(g.get("valor") or "").strip()
    # 2. Fallback: sufixo do SKU
    return _cor_do_sku(str(prod.get("sku") or ""))


def _cor_do_sku(sku: str) -> str:
    """Mapeia o sufixo de cor do SKU J&F para nome legivel.

    Padroes: ``SPU-TAM-COR`` (CALMAY201P-MSOR), ``SPU-CORQTD_KIT``
    (MEMEDMAY1034046-PRE12_KIT12), ``...-BEG`` (CINLEL370079-KIT 2 PECAS-BEG).
    """
    if not sku:
        return ""
    s = sku.strip().upper()
    parte = s.split("_KIT")[0]
    candidatos = []
    # ultimo bloco apos separador (espaco, hifen, underscore)
    for sep in ("-", "_", " "):
        if sep in parte:
            candidatos.append(parte.split(sep)[-1])
    candidatos.append(parte)
    for bloco in candidatos:
        # cor+quantidade: PRE12 -> PRE
        m = re.fullmatch(r"([A-Z]{3})\d+", bloco)
        if m and m.group(1) in _CORES:
            return _CORES[m.group(1)]
        # cor pura
        if bloco in _CORES:
            return _CORES[bloco]
        # ultimos 3 chars (pega SOR de MSOR)
        if len(bloco) > 3 and bloco[-3:] in _CORES:
            return _CORES[bloco[-3:]]
    return ""


def _extrair_kit(sku: str) -> str:
    """Extrai a descricao do kit do SKU (ex: 'Kit 12' de '..._KIT12')."""
    if not sku:
        return ""
    s = sku.strip().upper()
    if "KIT" not in s:
        return ""
    m = re.search(r"KIT\s*(\d+)", s)
    if m:
        return f"Kit {m.group(1)}"
    return "Kit"


# ------------------------------------------------------------------ #
# Modelo / genero — derivados do SKU (pedido do Jota, 2026-08-03)
# ------------------------------------------------------------------ #
# Prefixo do SKU -> nome curto do modelo, pra bater o olho na bancada.
# ATENCAO: a Meia Invisivel se divide por SPU, nao pelo prefixo:
#   MEINV + MAY1014046 -> masculina (numeracao 40/46)
#   MEINV + MAY1013540 -> feminina  (numeracao 35/40)
# Foi exatamente essa distincao que motivou este bloco: no card so aparecia
# "Meia Invisivel" e nao dava pra saber se era a fem ou a masc na hora de separar.
_MODELOS = (
    # (prefixo,  trecho_spu,     rotulo,            genero)
    ("MEINV",    "MAY1014046",   "Meia Inv MASC",   "masc"),
    ("MEINV",    "MAY1013540",   "Meia Inv FEM",    "fem"),
    ("MEINV",    "",             "Meia Invisível",  "masc"),
    ("MEMED",    "",             "Meia Méd",        "masc"),
    # A Meia Soquete (MEBAI = cano BAIxo) faltava: o card e a lista de
    # montagem mostravam o SKU cru em vez do nome (achado 25/08).
    ("MEBAI",    "",             "Meia Soquete",    "masc"),
    ("TOP",      "",             "Top Fit",         "fem"),
    ("CAL",      "",             "Calcinha",        "fem"),
    ("CON",      "",             "Conjunto",        "fem"),
    ("BOD",      "",             "Body",            "fem"),
    ("FIO",      "",             "Fio",             "fem"),
    ("CAM",      "",             "Camisola",        "fem"),
)


def extrair_modelo(sku: str) -> tuple[str, str, str]:
    """Deriva (modelo, spu, genero) do SKU.

    Retorna, por exemplo:
        MEINVMAY1013540-BRA3_KIT3 -> ("Meia Inv FEM", "MAY1013540", "fem")
        MEMEDMAY1034046-PRE6_KIT6 -> ("Meia Méd",     "MAY1034046", "masc")

    ``genero`` define a cor do card na UI (fem = rosa, masc = verde).
    Sem correspondencia conhecida devolve ("", spu, "masc").
    """
    if not sku:
        return "", "", "masc"
    s = sku.strip().upper()

    # SPU = bloco marca+codigo que vem logo apos o prefixo do produto.
    # Duas familias de padrao convivem no cadastro:
    #   MEINVMAY1013540-...  -> MAY1013540 (marca + 7 digitos)
    #   TOPTAY016G_PRE1      -> TAY016     (marca + 3 digitos, com tamanho colado)
    # Por isso o \d{3,10}: aceitar so 6+ deixava o Top/Calcinha sem SPU.
    m_spu = re.search(r"([A-Z]{3}\d{3,10})", s)
    spu = m_spu.group(1) if m_spu else ""

    for prefixo, trecho_spu, rotulo, genero in _MODELOS:
        if not s.startswith(prefixo):
            continue
        if trecho_spu and trecho_spu not in s:
            continue
        return rotulo, spu, genero

    return "", spu, "masc"


def extrair_cores_detalhadas(sku: str) -> str:
    """Descreve a(s) cor(es) do kit, incluindo os mistos.

    Kit de cor unica  : MEMEDMAY1034046-PRE6_KIT6      -> "Preto"
    Kit misto         : MEMEDMAY1034046-BRA3-PRE3_KIT6 -> "3 Branco + 3 Preto"

    Sem isto o card mostrava so a primeira cor de um kit misto -- risco real de
    separar a caixa errada.
    """
    if not sku:
        return ""
    s = sku.strip().upper()
    corpo = s.split("_KIT")[0]

    # Quebra por "-" E por "_": o Top usa underscore como separador de cor
    # (TOPTAY016GG_AZU1), enquanto as meias usam hifen. Antes so o hifen era
    # tratado e a cor do Top sumia do card (relato #2, 2026-08-09).
    partes = []
    for bloco in re.split(r"[-_]", corpo):
        m = re.fullmatch(r"([A-Z]{3})(\d+)", bloco)
        if m and m.group(1) in _CORES:
            partes.append((_CORES[m.group(1)], int(m.group(2))))
            continue
        # Cor colada no fim do bloco (TOPTAY016GG_AZU1 -> bloco "TOPTAY016GG"
        # nao casa, mas "AZU1" sim; ja o CONCLI10739MRUB traz a cor grudada).
        m2 = re.search(r"([A-Z]{3})(\d*)$", bloco)
        if m2 and m2.group(1) in _CORES and len(bloco) > 3:
            qtd = int(m2.group(2)) if m2.group(2) else 1
            partes.append((_CORES[m2.group(1)], qtd))

    if len(partes) > 1:
        return " + ".join(f"{qtd} {cor}" for cor, qtd in partes)
    if len(partes) == 1:
        return partes[0][0]
    return _cor_do_sku(sku)


def extrair_tamanho(sku: str) -> str:
    """Extrai o tamanho do SKU. Vazio quando o produto nao tem grade.

    Dois padroes convivem no cadastro J&F:
      * numerico colado no SPU  -> MEMEDMAY103|4046|PRE      -> "40/46"
      * letra(s) apos o SPU     -> TOPTAY016|GG|_AZU1        -> "GG"
                                   CONCLI10739|M|RUB         -> "M"

    Pedido do Comandante (relatos #1 e #3 de 2026-08-09): sem o tamanho no
    card, o separador precisa abrir o SKU pra saber que peca pegar.
    """
    if not sku:
        return ""
    s = sku.strip().upper().split("_KIT")[0]

    # 1) faixa numerica de 4 digitos (meias): 3540 -> 35/40, 4046 -> 40/46
    m = re.search(r"[A-Z]{3}\d{3,6}?(\d{4})(?=[-_A-Z]|$)", s)
    if m:
        faixa = m.group(1)
        return f"{faixa[:2]}/{faixa[2:]}"

    # 2) letra de grade logo apos o SPU (marca + digitos): G, GG, M, P, U.
    #    O que vem depois pode ser separador, fim, ou a cor colada -- com
    #    quantidade (AZU1) ou sem (RUB, no CONCLI10739MRUB).
    m = re.search(r"[A-Z]{3}\d{3,10}(PP|GG|XG|[PMGU])(?=[-_]|[A-Z]{3}|$)", s)
    if m:
        return m.group(1)

    return ""


# ------------------------------------------------------------------ #
# Montagem do resultado
# ------------------------------------------------------------------ #
def _montar_resultado_do_registro(reg: dict, *, origem: str) -> dict:
    """Converte um registro do indice SQLite no dict padrao de resultado."""
    tracking = reg.get("tracking") or ""
    sku = reg.get("sku_principal") or ""
    modelo, spu, genero = extrair_modelo(sku)
    # Pack do ML: uma etiqueta que cobre varios pedidos. A bancada precisa
    # saber ANTES de fechar a caixa -- mesma classe de falha do multi-item
    # ([[armadilha_scanner_pedido_multiitem_1_de_4]]).
    pack_id = reg.get("pack_id") or ""
    pedidos_no_pack = db.contar_por_pack(pack_id) if pack_id else 0
    return {
        "encontrado": True,
        "pedido_ecommerce": reg.get("pedido_ecommerce") or "",
        "canal": reg.get("canal") or "",
        "tracking": tracking,
        "shipment_id": reg.get("shipment_id") or "",
        "pack_id": pack_id,
        "pedidos_no_pack": pedidos_no_pack,
        "sku": sku,
        "modelo": modelo,
        "spu": spu,
        "genero": genero,
        "produto": reg.get("produto_nome") or "",
        "cor": extrair_cores_detalhadas(sku) or reg.get("cor") or "",
        "kit": reg.get("kit") or "",
        "tamanho": extrair_tamanho(sku),
        "cliente": reg.get("cliente_nome") or "",
        "cep": reg.get("cep") or "",
        "peso_kg": reg.get("peso_kg"),
        "imagem_url": reg.get("imagem_url") or "",
        # Pedido multi-item: TODAS as pecas da mesma etiqueta. A UI precisa
        # listar tudo, senao a bancada fecha a caixa faltando peca.
        "itens": db.desserializar_itens(reg),
        # '' | 'mesmo_kit_multiplo' | 'multi_itens' — calculado pela MESMA
        # funcao que a Esteira usa (core_separacao.processar_batch_picking),
        # gravado no indice pelo populator. A UI usa isto como fonte
        # primaria do alerta de volume; se vier vazio (indice antigo, ainda
        # sem popular de novo), ela recalcula localmente por `itens` como
        # fallback — nunca deixa de avisar por falta do campo novo.
        "alerta_volume": reg.get("alerta_volume") or "",
        "origem": origem,
        "conferido_hoje": db.ja_conferido_hoje(tracking),
    }


def _montar_resultado_do_olist(pedido: dict, detalhe: dict | None,
                               *, tracking: str = "", origem: str) -> dict | None:
    """Monta o resultado a partir do pedido do Olist (resumo + detalhe opcional)."""
    if not pedido:
        return None
    ecom = pedido.get("ecommerce") or {}
    cliente = pedido.get("cliente") or {}
    end = cliente.get("endereco") or {}

    itens = []
    if detalhe:
        itens = detalhe.get("itens") or []
    primeiro = itens[0] if itens else {}
    prod = primeiro.get("produto") or primeiro
    sku = str(prod.get("sku") or "")
    if not sku and itens:
        sku = str(prod.get("codigo") or "")

    modelo, spu, genero = extrair_modelo(sku)
    return {
        "encontrado": True,
        "pedido_ecommerce": ecom.get("numeroPedidoEcommerce") or "",
        "canal": ecom.get("nome") or "",
        "tracking": tracking or "",
        "sku": sku,
        "modelo": modelo,
        "spu": spu,
        "genero": genero,
        "produto": str(prod.get("descricao") or prod.get("nome") or ""),
        "cor": extrair_cores_detalhadas(sku) or (_extrair_cor(primeiro) if primeiro else ""),
        "kit": _extrair_kit(sku),
        "tamanho": extrair_tamanho(sku),
        "cliente": cliente.get("nome") or "",
        "cep": end.get("cep") or "",
        "peso_kg": None,
        "origem": origem,
        "conferido_hoje": db.ja_conferido_hoje(tracking) if tracking else False,
    }


# ------------------------------------------------------------------ #
# Verificação de cancelamento (2026-08-03)
# ------------------------------------------------------------------ #
# Objetivo: AVISAR, nunca BLOQUEAR. Se a API falhar (timeout/rede), o status
# vira "ERRO_VERIFICACAO" e o fluxo de bipagem segue — o Comandante decide.
# Cache em memoria por 10min evita bombardear a API a cada bip do mesmo pedido.
_CACHE_STATUS_TTL = 600      # 10 minutos
_CANCEL_TIMEOUT = 5          # segundos
_cache_status: dict[str, dict] = {}

# Status Shopee que indicam cancelamento (get_order_detail -> order_status).
_SHOPEE_STATUS_CANCELADO = ("CANCELLED", "IN_CANCEL")


def _agora_iso() -> str:
    """Timestamp ISO (UTC) do momento real da verificacao."""
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat()


def _normalizar_canal(canal: str) -> str:
    """Mapa canal legivel -> plataforma de verificacao."""
    c = (canal or "").lower()
    if "shopee" in c:
        return "shopee"
    if "mercado" in c or c in ("ml", "mercadolivre"):
        return "ml"
    if "tiktok" in c:
        return "tiktok"
    return "olist"


def _executar_com_timeout(fn, timeout: int = _CANCEL_TIMEOUT):
    """Executa fn em thread daemon. Retorna (resultado, erro|None).

    Se estourar o timeout, retorna (None, TimeoutError) — a chamada continua
    em background e nunca trava a bipagem.
    """
    saida: dict = {}

    def _alvo():
        try:
            saida["r"] = fn()
        except Exception as e:  # noqa: BLE001 — qualquer falha vira erro controlado
            saida["erro"] = e

    t = threading.Thread(target=_alvo, daemon=True)
    t.start()
    t.join(timeout)
    if t.is_alive():
        return None, TimeoutError(f"verificacao estourou {timeout}s")
    if "erro" in saida:
        return None, saida["erro"]
    return saida.get("r"), None


def _situacao_olist_nome(cod: str) -> str:
    """Nome legivel da situacao Olist (import lazy p/ nao pagar o custo sempre)."""
    from core_olist import SITUACAO_PEDIDO

    return SITUACAO_PEDIDO.get(cod, "")


def _status_legivel(status: str, plataforma: str) -> str:
    """Normaliza o status cru da plataforma em rotulo padronizado da UI."""
    s = (status or "").strip().upper()
    if not s:
        return "DESCONHECIDO"
    if s in _SHOPEE_STATUS_CANCELADO or s == "9":
        return "CANCELADO"
    if plataforma == "shopee":
        if s in ("SHIPPED", "COMPLETED"):
            return "ENVIADO"
        if s in ("UNPAID", "PROCESSING", "READY_TO_SHIP", "PROCESSED"):
            return "PENDENTE"
        return "DESCONHECIDO"
    # ⚠️ ML tem branch PROPRIO (26/08) -- status cru da API do ML
    # ("paid", "confirmed"...) e' texto, nao bate com `_situacao_olist_nome`
    # (que so' entende os codigos NUMERICOS do Olist "0".."9"). Antes o ML
    # caia no branch "ml"/"olist" combinado, sempre devolvia DESCONHECIDO
    # pra status nao-cancelado.
    if plataforma == "ml":
        if s in ("SHIPPED", "DELIVERED"):
            return "ENVIADO"
        if s in ("PAID", "CONFIRMED", "PAYMENT_REQUIRED"):
            return "PENDENTE"
        return "DESCONHECIDO"
    if plataforma == "olist":
        nome = _situacao_olist_nome(s)
        if not nome:
            return "DESCONHECIDO"
        if nome in ("Enviado", "Entregue"):
            return "ENVIADO"
        return "PENDENTE"
    # tiktok e outras plataformas sem regra propria de status-nao-cancelado
    # (o campo que importa de verdade, `cancelado`, ja' foi decidido antes
    # de chegar aqui -- este fallback so' afeta o ROTULO exibido).
    return "DESCONHECIDO"


def _status_shopee(pedido_ecommerce: str) -> dict:
    """Consulta a API Shopee (get_order_detail) e monta o status do pedido."""
    from core_shopee import ShopeeClient

    client = ShopeeClient()
    det = client.get_order_detail(pedido_ecommerce)
    status = ""
    motivo = None
    cancelado_em = None
    if det and isinstance(det, list) and det:
        d = det[0]
        status = str(d.get("order_status") or "")
        motivo = d.get("cancel_reason") or d.get("reason") or None
        ts_cancel = d.get("cancel_date") or d.get("ship_canceled_date")
        if ts_cancel:
            try:
                from datetime import datetime

                cancelado_em = datetime.fromtimestamp(int(ts_cancel)).strftime("%d/%m/%Y %H:%M")
            except (ValueError, TypeError, OSError):
                cancelado_em = None
    cancelado = status in _SHOPEE_STATUS_CANCELADO
    return {
        "cancelado": cancelado,
        "status": "CANCELADO" if cancelado else _status_legivel(status, "shopee"),
        "motivo": motivo if cancelado else None,
        "cancelado_em": cancelado_em,
        "plataforma": "shopee",
        "verificado_em": _agora_iso(),
    }


def _status_tiktok(pedido_ecommerce: str) -> dict:
    """Consulta a API do TikTok Shop e monta o status do pedido.

    ⚠️ Substitui o antigo STUB (26/08). Achado real: a `situacao` que o
    Olist devolve pela API fica DESATUALIZADA quando o comprador cancela
    no TikTok — a TELA do Olist ja mostra "Cancelado" no cabecalho, mas
    `/pedidos/{id}` continua devolvendo `situacao=2` ("Em separacao") por
    tempo indeterminado (casos reais: pedidos #527 e #544, 26/08 -- os
    dois com `cancellation_initiator: BUYER` no TikTok ha' dias, Olist
    nunca refletiu). So' a API do proprio canal tem o dado certo.
    """
    import core_etiquetas_tiktok_api as tt

    resp = tt._get("/order/202309/orders", {"ids": str(pedido_ecommerce)})
    orders = ((resp or {}).get("data") or {}).get("orders") or []
    status = ""
    motivo = None
    cancelado_em = None
    if orders:
        o = orders[0]
        status = str(o.get("status") or "")
        itens = o.get("line_items") or []
        if itens:
            motivo = itens[0].get("cancel_reason") or None
        ts = o.get("cancel_time") or o.get("update_time")
        if ts:
            try:
                from datetime import datetime

                cancelado_em = datetime.fromtimestamp(int(ts)).strftime("%d/%m/%Y %H:%M")
            except (ValueError, TypeError, OSError):
                cancelado_em = None
    cancelado = status.upper() == "CANCELLED"
    return {
        "cancelado": cancelado,
        "status": "CANCELADO" if cancelado else _status_legivel(status, "tiktok"),
        "motivo": motivo if cancelado else None,
        "cancelado_em": cancelado_em if cancelado else None,
        "plataforma": "tiktok",
        "verificado_em": _agora_iso(),
    }


def _status_ml(pedido_ecommerce: str) -> dict:
    """Consulta a API do Mercado Livre e monta o status do pedido.

    ⚠️ Antes o ML caia no mesmo caminho do Olist (`_status_olist_do_pedido`,
    que so' olha `situacao` do Olist) — mesma classe de bug do TikTok: se
    o comprador cancela no ML, nada garante que o Olist ja refletiu. Usa
    `/orders/{id}` do proprio ML (`status: "cancelled"` quando cancelado),
    que ja' e' a fonte usada por `core_esteira.obter_prazo_despacho_ml`
    para outros dados do mesmo pedido.
    """
    import core_esteira as est

    pedido = est._ml_get(f"/orders/{pedido_ecommerce}")
    status = str((pedido or {}).get("status") or "")
    cancel_detail = (pedido or {}).get("cancel_detail") or {}
    motivo = cancel_detail.get("code") or None
    cancelado_em = cancel_detail.get("date") or None
    cancelado = status.lower() == "cancelled"
    return {
        "cancelado": cancelado,
        "status": "CANCELADO" if cancelado else _status_legivel(status, "ml"),
        "motivo": motivo if cancelado else None,
        "cancelado_em": cancelado_em if cancelado else None,
        "plataforma": "ml",
        "verificado_em": _agora_iso(),
    }


def _status_olist_do_pedido(pedido: dict | None, *, plataforma: str) -> dict:
    """Monta o status a partir do dict resumido do Olist (situacao do pedido)."""
    p = pedido or {}
    sit = str(p.get("situacao") or "")
    cancelado = sit == "9"
    cancelado_em = None
    for chave in ("dataCancelamento", "data_cancelamento", "dataCancelada"):
        val = p.get(chave)
        if val:
            cancelado_em = str(val)
            break
    return {
        "cancelado": cancelado,
        "status": "CANCELADO" if cancelado else _status_legivel(sit, plataforma),
        "motivo": None,
        "cancelado_em": cancelado_em,
        "plataforma": plataforma,
        "verificado_em": _agora_iso(),
    }


def _status_olist(pedido_ecommerce: str) -> dict:
    """Consulta o Olist (V3) ao vivo e monta o status do pedido."""
    client = _client_olist()
    pedido = client.buscar_pedido_por_ecommerce(pedido_ecommerce)
    return _status_olist_do_pedido(pedido, plataforma="olist")


def _resultado_erro(plataforma: str, erro) -> dict:
    """Status de erro controlado — nunca bloqueia o fluxo de bipagem."""
    log.warning("[cancelamento] falha na API (%s): %s", plataforma, erro)
    return {
        "cancelado": False,
        "status": "ERRO_VERIFICACAO",
        "motivo": f"Falha na API: {erro}",
        "cancelado_em": None,
        "plataforma": plataforma,
        "verificado_em": _agora_iso(),
    }


def verificar_cancelamento(pedido_ecommerce: str, canal: str,
                           *, _pedido_olist: dict | None = None) -> dict:
    """Verifica se um pedido foi cancelado na plataforma.

    Retorna:
        {
            "cancelado": bool,
            "status": str,        # CANCELADO | ENVIADO | PENDENTE | ATIVO |
                                  # DESCONHECIDO | NAO_VERIFICADO | ERRO_VERIFICACAO
            "motivo": str | None, # motivo do cancelamento se disponivel
            "cancelado_em": str | None,
            "plataforma": str,    # shopee | ml | tiktok | olist
            "verificado_em": str, # ISO timestamp (UTC)
        }

    Estrategia:
    - Shopee : ``ShopeeClient.get_order_detail()`` (timeout 5s em thread).
    - ML/Olist: situacao no Olist V3 (``_pedido_olist`` evita chamada extra).
    - TikTok : stub — retorna ``NAO_VERIFICADO`` (nao bloqueia).
    - Falha/timeout de API: ``cancelado=False, status="ERRO_VERIFICACAO"``.
    - Cache em memoria por 10min (chave: ``plataforma:pedido``).
    """
    if not pedido_ecommerce or not str(pedido_ecommerce).strip():
        return {
            "cancelado": False,
            "status": "DESCONHECIDO",
            "motivo": None,
            "cancelado_em": None,
            "plataforma": _normalizar_canal(canal),
            "verificado_em": _agora_iso(),
        }

    plataforma = _normalizar_canal(canal)
    chave = f"{plataforma}:{str(pedido_ecommerce).strip().upper()}"

    agora = time.time()
    hit = _cache_status.get(chave)
    if hit and (agora - hit["_ts"]) < _CACHE_STATUS_TTL:
        log.info("[cancelamento] cache hit %s -> %s", chave, hit["status"])
        return {k: v for k, v in hit.items() if k != "_ts"}

    if plataforma == "tiktok":
        # ⚠️ Antes era STUB (sempre NAO_VERIFICADO). Corrigido 26/08: achado
        # real, `situacao` do Olist fica desatualizada apos cancelamento no
        # TikTok (pedidos #527/#544 continuavam "Em separacao" na API dias
        # depois do comprador cancelar). Consulta a API do proprio TikTok.
        resultado, erro = _executar_com_timeout(lambda: _status_tiktok(pedido_ecommerce))
        if erro is not None or resultado is None:
            resultado = _resultado_erro(plataforma, erro)
    elif plataforma == "shopee":
        resultado, erro = _executar_com_timeout(lambda: _status_shopee(pedido_ecommerce))
        if erro is not None or resultado is None:
            resultado = _resultado_erro(plataforma, erro)
    elif plataforma == "ml":
        # ⚠️ Antes usava so' a `situacao` do Olist (mesmo bug do TikTok,
        # generalizado 26/08: "todos cancelam" -- Jota). Consulta a API do
        # proprio ML (`/orders/{id}`, status "cancelled").
        resultado, erro = _executar_com_timeout(lambda: _status_ml(pedido_ecommerce))
        if erro is not None or resultado is None:
            resultado = _resultado_erro(plataforma, erro)
    else:
        # olist puro (canal desconhecido/manual) — so' resta a situacao do
        # proprio Olist, nao ha outra API pra cruzar.
        if _pedido_olist is not None:
            resultado = _status_olist_do_pedido(_pedido_olist, plataforma=plataforma)
        else:
            resultado, erro = _executar_com_timeout(lambda: _status_olist(pedido_ecommerce))
            if erro is not None or resultado is None:
                resultado = _resultado_erro(plataforma, erro)

    resultado["_ts"] = agora
    _cache_status[chave] = resultado
    log.info(
        "[cancelamento] pedido=%s plataforma=%s -> status=%s cancelado=%s",
        pedido_ecommerce, plataforma, resultado["status"], resultado["cancelado"],
    )
    return {k: v for k, v in resultado.items() if k != "_ts"}


def _anexar_status(result: dict | None, *, pedido_olist: dict | None = None) -> dict | None:
    """Anexa os campos de cancelamento ao dict de resultado do scanner.

    Campos adicionados: ``cancelado``, ``status_pedido``, ``alerta``,
    ``cancelado_em``, ``motivo_cancelamento``. Nunca falha por causa da API.
    """
    if result is None:
        return None
    if not result.get("pedido_ecommerce"):
        result.update({
            "cancelado": False,
            "status_pedido": "DESCONHECIDO",
            "alerta": None,
            "cancelado_em": None,
            "motivo_cancelamento": None,
        })
        return result

    info = verificar_cancelamento(
        result["pedido_ecommerce"], result["canal"], _pedido_olist=pedido_olist
    )
    cancelado = bool(info.get("cancelado"))
    status = info.get("status") or "DESCONHECIDO"
    alerta = None
    if cancelado:
        plataforma = info.get("plataforma") or "plataforma"
        alerta = f"🚨 PEDIDO CANCELADO ({plataforma}). NÃO ENVIAR."
        motivo = info.get("motivo")
        if motivo:
            alerta += f" Motivo: {motivo}"
    elif status in ("ERRO_VERIFICACAO", "NAO_VERIFICADO"):
        alerta = ("⚠️ Status do pedido não verificado (falha na API). "
                  "Confirme na plataforma antes de enviar.")
        status = "NAO_VERIFICADO"

    # Pack do ML com mais de um pedido: avisa SEM sobrescrever o alerta de
    # cancelamento, que tem prioridade (nao enviar > enviar incompleto).
    n_pack = int(result.get("pedidos_no_pack") or 0)
    if n_pack > 1:
        aviso_pack = (f"📦 PACK ML com {n_pack} pedidos nesta etiqueta. "
                      f"Separe TODOS antes de fechar a caixa.")
        alerta = f"{alerta} | {aviso_pack}" if alerta else aviso_pack

    result.update({
        "cancelado": cancelado,
        "status_pedido": status,
        "alerta": alerta,
        "cancelado_em": info.get("cancelado_em"),
        "motivo_cancelamento": info.get("motivo") if cancelado else None,
    })
    return result


# ------------------------------------------------------------------ #
# Resolver principal
# ------------------------------------------------------------------ #
def resolver_codigo(codigo: str) -> dict | None:
    """Resolve um codigo lido da etiqueta para o pedido de conferencia.

    Cascata:
      1. ``Pedido: XXXX`` (Shopee) -> extrai numero e busca no Olist.
      2. Match exato por tracking no indice SQLite.
      3. Match exato por pedido_ecommerce no indice SQLite.
      4. Busca ao vivo no Olist por numeroPedidoEcommerce.
      5. None (UI mostra "nao encontrado").

    Apos encontrar, anexa a verificacao de cancelamento (``cancelado``,
    ``status_pedido``, ``alerta``, ``cancelado_em``, ``motivo_cancelamento``).
    A verificacao NUNCA bloqueia: se a API falhar, ``cancelado=False`` e
    ``status_pedido="NAO_VERIFICADO"`` — o Comandante decide.

    Retorna dict padronizado (pedido_ecommerce, sku, produto, cor, kit,
    cliente, cep, tracking, canal, origem, conferido_hoje, cancelado,
    status_pedido, alerta) ou None.
    """
    if not codigo or not str(codigo).strip():
        return None

    codigo_limpo = db.normalizar_codigo(codigo)
    pedido_extraido = extrair_numero_pedido(codigo_limpo)

    # 1. Match por tracking no indice
    reg = db.buscar_por_tracking(codigo_limpo)
    if reg:
        return _anexar_status(_montar_resultado_do_registro(reg, origem="indice"))

    # 2. Match por numero de pedido no indice (extraido ou codigo cru)
    for alvo in (pedido_extraido, codigo_limpo):
        if not alvo:
            continue
        reg = db.buscar_por_pedido(alvo)
        if reg:
            return _anexar_status(_montar_resultado_do_registro(reg, origem="indice"))

    # 2b. Etiqueta do Mercado Livre: o code128 grande e' o shipment_id
    # (ex: 47828318513), nao o pedido nem o rastreio. Sem esta etapa o ML
    # nunca resolvia -- caia direto na busca ao vivo do Olist com um numero
    # que `numeroPedidoEcommerce` jamais casa.
    reg = db.buscar_por_codigo_ml(codigo_limpo)
    if reg:
        return _anexar_status(_montar_resultado_do_registro(reg, origem="indice_ml"))

    # 3. Busca ao vivo no Olist por numeroPedidoEcommerce
    alvo_olist = pedido_extraido or codigo_limpo
    try:
        client = _client_olist()
        pedido = client.buscar_pedido_por_ecommerce(alvo_olist)
        if pedido:
            detalhe = None
            try:
                detalhe = client.obter_pedido(pedido.get("id"))
            except Exception:
                log.warning("Falha ao obter detalhe do pedido %s", pedido.get("id"))
            return _anexar_status(
                _montar_resultado_do_olist(
                    pedido, detalhe, tracking=codigo_limpo, origem="olist"
                ),
                pedido_olist=pedido,
            )
    except Exception as e:
        log.error("Erro ao consultar Olist para %s: %s", alvo_olist, e)

    return None
