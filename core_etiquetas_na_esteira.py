# ==============================================================================
# NOME DO SCRIPT: core_etiquetas_na_esteira.py
# DESCRICAO: PDF unico dos 2 canais, na ordem da esteira e numerado #1..#N
# FUNCAO: Junta Shopee + TikTok, REORDENA conforme a sequencia de embalagem
#         e carimba o numero de ordem em cada etiqueta.
# STATUS: ATIVO
# VERSAO: 1.0
# DATA: 19/08/2026
# AUTOR: Terminador (001) / Claude
# ==============================================================================
"""
Regras do Jota (2026-08-19):

    "lembrando que as etiquetas sao geradas em ordem de facilitar embalar...
     conforme separacao"
    "sim... precisa ser junto q e' melhor... embalo junto"

## O problema que isto resolve

`baixar_tudo` ja' junta os dois canais, mas na ordem que as APIs devolvem.
Medido em 19/08: a etiqueta #9 do PDF era o 2o pedido da sequencia de
embalagem. Numerar assim faria o operador garimpar etiqueta a cada caixa —
pior do que nao ter numero nenhum.

Aqui o PDF e' REMONTADO na ordem da esteira e so' entao numerado, de modo que
a pilha de etiquetas e a pilha de caixas andem juntas.

## Como cada pagina e' identificada

O nome do arquivo individual carrega o identificador do pedido:

    Shopee -> `{order_sn}.pdf`     casa direto com `numero_ecommerce`
    TikTok -> `{package_id}.pdf`   precisa traduzir pacote -> pedido

⚠️ Sem essa traducao o pedido do TikTok nunca casa e cai no fim da pilha
como "sem posicao". Por isso `_mapa_tiktok()` existe.

## O que sobra fora da esteira

Etiqueta cujo pedido nao esta' na sequencia (canal fora da fila, pedido que
mudou de situacao no meio) NAO e' descartada: vai para o FIM da pilha,
numerada normalmente. Sumir com etiqueta e' o unico erro pior que
desordena-la — a caixa ficaria sem envio.

Uso:
    from core_etiquetas_na_esteira import gerar
    r = gerar()   # baixa, ordena, numera
"""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)


# Faixa no rodape pros numeros #N #m e SKU do produto vendido.
#
# 27/08: fontes subiram de 8.5/6.0 para 12/10 -- o Comandante nao conseguia ler
# na bancada ("dificil de ler hj"). O padrao discreto da DANFE serve pra quem
# le' o documento na mao; aqui a leitura e' de pe', a um metro, com a caixa na
# frente. Legibilidade vale mais que economia de papel.
#
# A faixa cresceu junto (7mm -> 10.5mm): 12pt + 10pt + respiro nao cabem em
# 19.8pt, e forcar so' as fontes faria as duas linhas se sobreporem.
# 05/09: 10.5mm -> 12.5mm. Ao afastar o SKU da borda (MARGEM_RODAPE_PT
# abaixo), as duas linhas passaram a se sobrepor dentro de 10.5mm. O calculo
# de baixo pra cima -- margem + descida + corpo do SKU + respiro + descida +
# corpo do #N -- pede 11.3mm no minimo; 12.5mm deixa ~2.7pt de respiro real.
FAIXA_NUMERO_PT = 12.5 / 25.4 * 72   # 12.5mm ≈ 35.4pt
FONTE_NUMERO = 12.0
FONTE_SKU = 10.0

# ⚠️ MARGEM DE SEGURANCA no rodape (Jota, 05/09/2026: "coloque um limite pra
# evitar q ele trepe na pagina seguinte... esta saindo a parte do SKU na
# etiqueta de baixo").
#
# A baseline do SKU era um valor FIXO que ignorava a descida da fonte (a perna
# do "p", do "g"). Com fonte 10pt a descida come ~2.2pt e sobravam 1.8pt =
# 0,64mm ate' a borda -- medido no PDF de 05/09. Em rolo continuo essa sobra
# nao existe: a cabeca termica nao corta exatamente na picotagem e o texto
# vaza para a etiqueta seguinte.
#
# 2.5mm da folga real sem encolher a etiqueta de forma perceptivel -- mesma
# ordem da margem ja' usada em core_etiqueta_normalizar.MARGEM_PT.
MARGEM_RODAPE_PT = 2.5 / 25.4 * 72   # 2.5mm ≈ 7.1pt

# Fracao da altura da fonte ABAIXO da baseline (descida). 0.22 e' o tipico de
# Helvetica; derivar disso mantem a folga correta se a fonte mudar de tamanho.
DESCIDA_FONTE = 0.22


def _baseline_sku(altura: float) -> float:
    """Baseline do SKU: da BORDA para cima, descontando margem e descida.

    NUNCA um valor fixo -- foi o fixo que deixou a perna das letras
    encostando no papel e vazando para a etiqueta de baixo.
    """
    return altura - MARGEM_RODAPE_PT - (FONTE_SKU * DESCIDA_FONTE)
COR = (0.0, 0.0, 0.0)
COR_SKU = (0.30, 0.30, 0.30)


def _formatar_itens_sku(itens: list[dict]) -> str:
    """Formata '1x SKU' ou '1x SKU_A + 2x SKU_B' para o rodape."""
    partes = []
    for it in itens:
        qtd = it.get("quantidade") or 1
        sku = (it.get("sku") or "").strip()
        if sku:
            partes.append(f"{qtd}x {sku}")
    return " + ".join(partes)


def _recompor_com_faixa_numero(doc, idx: int, texto_num: str, texto_sku: str = "") -> None:
    """Reescreve a pagina `idx` do `doc`: encolhe sutilmente e abre faixa compacta embaixo."""
    import fitz

    from core_etiqueta_numerar import COR

    original = doc[idx]
    largura, altura = original.rect.width, original.rect.height

    temp = fitz.open()
    temp.insert_pdf(doc, from_page=idx, to_page=idx)

    doc.delete_page(idx)
    nova = doc.new_page(pno=idx, width=largura, height=altura)

    # Area util = preserva a etiqueta original com reducao minima
    area_util = fitz.Rect(0, 0, largura, altura - FAIXA_NUMERO_PT)
    nova.show_pdf_page(area_util, temp, 0)
    temp.close()

    # Linha 1: #N  #m (Ordem na esteira + Numero no Olist)
    largura_num = fitz.get_text_length(texto_num, fontname="hebo", fontsize=FONTE_NUMERO)
    x_num = max(10, (largura - largura_num) / 2)
    # Baseline da 1a linha = topo da faixa + a altura da propria fonte. Antes
    # era um 9.5 fixo, calibrado pra fonte 8.5; com 12pt o texto encostava na
    # borda de cima. Derivar da fonte mantem o respiro em qualquer tamanho.
    y_num = altura - FAIXA_NUMERO_PT + FONTE_NUMERO + 1.0
    nova.insert_text(fitz.Point(x_num, y_num), texto_num, fontsize=FONTE_NUMERO,
                     fontname="hebo", color=COR)

    # Linha 2: SKU do produto vendido
    if texto_sku:
        largura_sku = fitz.get_text_length(texto_sku, fontname="helv", fontsize=FONTE_SKU)
        x_sku = max(8, (largura - largura_sku) / 2)
        y_sku = _baseline_sku(altura)
        nova.insert_text(fitz.Point(x_sku, y_sku), texto_sku, fontsize=FONTE_SKU,
                         fontname="helv", color=COR_SKU)



def _mapa_tiktok(pacotes_presentes: set[str] | None = None) -> dict[str, str]:
    """package_id -> numero do pedido no marketplace.

    A etiqueta do TikTok e' nomeada pelo PACOTE, mas a esteira raciocina em
    PEDIDO. Sem esta ponte as etiquetas do TikTok nunca casariam.

    ⚠️ `somente_imprimivel=False` de proposito. O padrao (`True`) devolve so'
    os pacotes em PROCESSING -- os unicos que ainda aceitam IMPRIMIR
    etiqueta. Mas aqui nao se imprime nada: so' se traduz pacote -> pedido,
    e isso vale para qualquer status. Com o filtro ligado a API devolvia
    **3 pacotes** contra **300** sem ele (medido 23/09/2026); todo pedido
    ja' despachado perdia a traducao, caia no fim da pilha como "sem
    posicao" e ficava sem o nome civil na etiqueta.

    Args:
        pacotes_presentes: ids dos pacotes que REALMENTE tem etiqueta neste
            lote. Para de paginar assim que todos forem encontrados -- sem
            isso a varredura completa custava ~16s para traduzir 3 pacotes
            (regressao medida 24/09, depois de tirar o filtro).
    """
    try:
        import core_etiquetas_tiktok_api as tt
    except Exception as exc:
        log.warning("Mapa TikTok indisponivel: %s", exc)
        return {}

    mapa: dict[str, str] = {}
    try:
        lista = tt.listar_pacotes_a_enviar(
            somente_imprimivel=False,
            parar_ao_achar=pacotes_presentes or None) or []
        for pacote in lista:
            pid = str(pacote.get("id") or "")
            for pedido in (pacote.get("orders") or []):
                oid = str(pedido.get("id") or "")
                if pid and oid:
                    mapa[pid] = oid
    except Exception as exc:
        log.warning("Falha ao mapear pacotes TikTok: %s", exc)

    return mapa


def _sequencia_e_mapa_olist() -> tuple[list[str], dict[str, str], dict[str, str]]:
    """(ordem_da_esteira, {numero_ecommerce: numero_olist}, {numero_ecommerce: sku_formatado})."""
    import core_separacao as cs
    import core_sequencia_embalagem as seq
    import core_sync_expedicao as sync

    dados = sync.sincronizar([4, 7], max_pedidos=100)
    processado = cs.processar_batch_picking(dados["pedidos"])
    resultado = seq.sequenciar(processado)

    ordem = [str(p.get("numero_ecommerce") or "") for p in resultado["sequencia"]]
    mapa_olist = {
        str(p.get("numero_ecommerce") or ""): str(p.get("numero_olist") or "")
        for p in resultado["sequencia"]
    }
    mapa_skus = {
        str(p.get("numero_ecommerce") or ""): _formatar_itens_sku(p.get("itens") or [])
        for p in resultado["sequencia"]
    }
    return ordem, mapa_olist, mapa_skus



def _ordem_da_esteira() -> list[str]:
    """Numeros de pedido na ordem de embalagem definida pela esteira.

    Mantida por compatibilidade com quem so' precisa da ordem (nao do mapa
    Olist) — chama `_sequencia_e_mapa_olist()` e descarta o mapa.
    """
    ordem, _ = _sequencia_e_mapa_olist()
    return ordem


def _escrever_nome_civil(pagina, impresso: str, civil: str, cnr) -> int:
    """Acrescenta o nome civil na etiqueta. Devolve quantos nomes escreveu.

    Regra do Jota (01/09/2026, refinada):

        "ao completar o nome da pessoa q estiver nick, coloque sempre o
         primeiro nome e o ultimo nome... nao precisa colocar o nome todo
         assim dificilmente vai estourar[;] se ver q vai estourar colocar na
         lacuna limpa"

    PRIMEIRO + ULTIMO nome, sempre -- e' o que identifica a pessoa para o
    carteiro ("Rosilene Silva"), e curto o bastante para caber ao lado do
    nick na maioria dos casos. Nome do meio nao acrescenta identificacao e e'
    justamente o que fazia o texto estourar.

    Quando nem assim cabe, o nome vai inteiro para a LACUNA LIMPA: a faixa
    vazia entre o fim do endereco e o proximo elemento da etiqueta (na J&T,
    entre o endereco e o numero grande do pacote). Escreve-se logo acima da
    margem inferior dessa area, nao colado no endereco.

    ⚠️ A primeira tentativa escrevia o nome completo na mesma linha do nick e
    SOBREPUNHA o endereco (achado 31/08: "user9945697717580 (Nilson Oliveira
    Do Nascimen̶t̶o̶)"). Causa: `_limite_direito_real` so' enxerga obstaculos
    na MESMA faixa vertical da linha, e o endereco fica na linha de baixo --
    entao "cabia" no calculo e atropelava no papel.
    """
    import fitz

    achados = pagina.search_for(impresso)
    if not achados:
        return 0

    caixa = achados[0]
    partes = civil.split()
    if not partes:
        return 0

    # Primeiro + ultimo. Nome de uma palavra so' usa ela mesma.
    curto = partes[0] if len(partes) == 1 else f"{partes[0]} {partes[-1]}"

    # --- 1) ao lado do nick, se couber de verdade ---------------------- #
    limite = cnr._limite_direito_real(pagina, caixa.y0, caixa.y1, caixa.x1)
    texto = cnr.encurtar_para_caber(curto, caixa.x1, limite, "hebo", 8.3)
    if texto:
        pagina.insert_text(fitz.Point(caixa.x1, caixa.y1), texto,
                           fontsize=8.3, fontname="hebo", color=(0, 0, 0),
                           overlay=True)
        return 1

    # --- 2) nao coube: vai para a lacuna limpa ------------------------- #
    pos = _lacuna_limpa(pagina, caixa)
    if pos is None:
        return 0

    y_base, limite_dir = pos
    na_lacuna = cnr.encurtar_para_caber(curto, caixa.x0, limite_dir, "hebo", 7.5)
    if not na_lacuna:
        return 0

    pagina.insert_text(fitz.Point(caixa.x0, y_base), na_lacuna.strip(),
                       fontsize=7.5, fontname="hebo", color=(0, 0, 0),
                       overlay=True)
    return 1


def _lacuna_limpa(pagina, caixa_nome, altura_min: float = 9.0):
    """(y_da_linha_de_base, limite_direito) da faixa vazia sob o endereco.

    A caixa do destinatario termina antes do proximo elemento da etiqueta
    (na J&T, o numero grande do pacote). Medido em etiqueta real: endereco
    acaba em y=154.1 e o numero comeca em y=166.9 -- ~12.8pt de espaco
    limpo.

    Escreve logo ACIMA da margem inferior dessa faixa (pedido do Jota), nao
    colado no endereco: assim o nome nao parece continuacao do endereco nem
    encosta no elemento de baixo.

    `None` quando a folga nao comporta a linha.
    """
    x_esq = caixa_nome.x0

    # Onde o bloco do destinatario (nome + endereco) termina: ultima linha
    # alinhada a` mesma margem esquerda.
    fim_bloco = caixa_nome.y1
    for bloco in pagina.get_text("dict")["blocks"]:
        for linha in bloco.get("lines", []):
            for trecho in linha.get("spans", []):
                bx0, by0, _, by1 = trecho["bbox"]
                if abs(bx0 - x_esq) < 12 and by0 >= caixa_nome.y0:
                    fim_bloco = max(fim_bloco, by1)

    # Primeiro elemento QUALQUER abaixo disso -- e' o teto da lacuna.
    proximo_y = pagina.rect.height
    for bloco in pagina.get_text("dict")["blocks"]:
        for linha in bloco.get("lines", []):
            for trecho in linha.get("spans", []):
                by0 = trecho["bbox"][1]
                if by0 > fim_bloco + 0.5:
                    proximo_y = min(proximo_y, by0)

    if (proximo_y - fim_bloco) < altura_min:
        return None

    # Base do texto 2pt acima do proximo elemento: "pouco acima da margem
    # inferior dessa caixa", como pedido.
    y_base = proximo_y - 2.0

    # Limite horizontal: qualquer conteudo a` direita nessa mesma faixa
    # (a coluna do codigo de barras vertical, por exemplo).
    limite = pagina.rect.width - 6.0
    for bloco in pagina.get_text("dict")["blocks"]:
        for linha in bloco.get("lines", []):
            for trecho in linha.get("spans", []):
                bx0, by0, _, by1 = trecho["bbox"]
                if bx0 > x_esq + 20 and by0 < y_base + 2 and by1 > y_base - 9:
                    limite = min(limite, bx0 - 3)
    return y_base, limite

    return escritos


def gerar(*, com_cartao: bool = False,
          nome_real: bool = True,
          somente: set[str] | None = None,
          saida: str | Path | None = None,
          numerar: bool = True,
          cache_fase1: dict[str, Any] | None = None,
          rebaixar: bool = False) -> dict[str, Any]:
    """PDF unico, dois canais, ordem da esteira, numerado #1..#N.

    Args:
        nome_real: acrescenta o nome civil entre parenteses quando a etiqueta
            veio com apelido do comprador ("Thata" -> "Thata (Aurora
            Machado)"). Os Correios devolvem ao remetente quando o nome nao
            corresponde a ninguem no endereco.
        somente: numeros de pedido a INCLUIR. `None` = todos.
            Usado pelas ONDAS de expedicao: a segunda leva do dia imprime so'
            o que ainda nao foi processado, em vez de repetir a pilha inteira
            ([[core_ondas_expedicao]]).
        numerar: False pula o carimbo #N #m na faixa. Uso interno de teste
            (`scratch/testar_faixa_com_sku.py`) — o script recompoe a faixa
            do zero (com SKU) e duplicaria o numero se este `gerar()` ja'
            tivesse carimbado antes. Producao sempre usa o default True.
    """
    import fitz

    import core_etiqueta_normalizar as norm
    import core_etiqueta_nome_real as cnr
    import core_etiquetas_todas as todas
    import core_nome_civil_nfe as nfe

    inicio = time.time()

    # 1. Etiquetas dos três canais — reaproveitando a Fase 1 quando ela
    #    COBRE este lote. Ver `core_etiquetas_cache`: a regra e' cobertura
    #    exata, nao "tem cache?", porque a onda define o escopo e pedido
    #    novo nao pode entrar num lote ja' separado.
    import core_etiquetas_cache as cache_etq

    # A ponte pacote->pedido precisa existir ANTES de validar o cache: o
    # cache do TikTok guarda package_id, mas `somente` fala em order_id.
    _mapa_tt_cache = None
    if somente and cache_fase1 and not rebaixar:
        _info_tt_c = (cache_fase1.get("por_canal") or {}).get("tiktok") or {}
        _pac_c = {Path(a).stem for a in (_info_tt_c.get("arquivos") or [])}
        if _pac_c:
            _mapa_tt_cache = _mapa_tiktok(_pac_c)

    baixado = cache_etq.baixar_ou_reaproveitar(
        canais=["tiktok", "shopee", "ml", "amazon"], com_cartao=com_cartao,
        somente=somente, cache=cache_fase1, mapa_tt=_mapa_tt_cache,
        forcar=rebaixar)
    if not baixado.get("pdf"):
        # Mesmas chaves do caminho feliz: a tela le `resumo` e `erros` sem
        # checar, e um dict curto aqui quebraria a pagina com KeyError.
        return {"pdf": None, "total": 0, "fora_da_esteira": 0,
                "por_canal": baixado.get("por_canal") or {},
                "erros": baixado.get("erros") or ["Nenhuma etiqueta baixada nos canais"],
                "segundos": round(time.time() - inicio, 1),
                "resumo": "Nenhuma etiqueta disponível"}

    # 2. De qual pedido e' cada arquivo individual
    import core_etiqueta_com_cartao as ccc

    # So' os pacotes que tem etiqueta neste lote precisam de traducao.
    _info_tt = (baixado.get("por_canal") or {}).get("tiktok") or {}
    _presentes = {Path(a).stem for a in (_info_tt.get("arquivos") or [])}
    mapa_tt = _mapa_tiktok(_presentes or None)
    arquivos: list[tuple[str, str]] = []   # (caminho, numero_do_pedido)

    for canal in ("tiktok", "shopee", "ml", "amazon"):
        info = (baixado.get("por_canal") or {}).get(canal) or {}
        arqs_canal = info.get("arquivos") or []
        # Se nao houver arquivos individuais mas houver PDF consolidado do canal
        if not arqs_canal and info.get("pdf") and Path(info["pdf"]).exists():
            arqs_canal = [info["pdf"]]

        for caminho in arqs_canal:
            if not Path(caminho).exists():
                continue
            chave = Path(caminho).stem

            # Recorta a folha A4 da Shopee para 10x15. O TikTok e ML ja' vem no
            # tamanho e a funcao devolve sem mexer.
            try:
                res_norm = norm.normalizar_10x15(caminho)
                if res_norm.get("saida") and Path(res_norm["saida"]).exists():
                    caminho = res_norm["saida"]
            except Exception as exc:
                log.warning("Normalizacao de %s falhou: %s", chave, exc)

            # Cartao de agradecimento DO CANAL, colado logo apos a etiqueta.
            if com_cartao:
                alvo = caminho.replace(".pdf", "_cartao.pdf")
                try:
                    rc = ccc.intercalar_canal_unico(caminho, alvo, canal)
                    if rc.get("ok"):
                        caminho = alvo
                except Exception as exc:
                    log.warning("Cartao de %s falhou: %s", chave, exc)

            # TikTok nomeia por pacote; Shopee e ML ja' nomeiam pelo pedido
            arquivos.append((caminho, mapa_tt.get(chave, chave)))

    if not arquivos:
        return {
            "pdf": None,
            "total": 0,
            "fora_da_esteira": 0,
            "por_canal": baixado.get("por_canal") or {},
            "erros": baixado.get("erros") or ["Nenhuma etiqueta individual encontrada para montar a pilha."],
            "segundos": round(time.time() - inicio, 1),
            "resumo": "Nenhuma etiqueta disponível para montar a pilha",
        }

    # 3. Posicao de cada pedido na esteira + numero da Olist + SKU vendido
    try:
        _seq, mapa_olist, mapa_skus = _sequencia_e_mapa_olist()
        ordem = {num: i for i, num in enumerate(_seq)}
    except Exception as exc:
        log.warning("Sequencia da esteira indisponivel (%s); "
                    "mantendo a ordem original", exc)
        ordem = {}
        mapa_olist = {}
        mapa_skus = {}

    # Pedido fora da esteira vai para o fim — nunca some.
    FIM = 10_000
    arquivos.sort(key=lambda x: ordem.get(x[1], FIM))

    # ONDAS: tira o que ja' foi processado numa leva anterior.
    filtrados = 0
    if somente is not None:
        _alvo = {str(x).strip().upper() for x in somente}
        antes = len(arquivos)
        arquivos = [(c, n) for c, n in arquivos
                    if str(n).strip().upper() in _alvo]
        filtrados = antes - len(arquivos)
        if filtrados:
            log.info("Ondas: %d etiqueta(s) de pedido ja' processado ficaram "
                     "de fora desta pilha", filtrados)

    fora = sum(1 for _, num in arquivos if num not in ordem)

    # 4. Remonta na ordem certa em um novo documento limpo (doc_final)
    destino = Path(saida) if saida else Path(baixado["pdf"])
    doc_final = fitz.open()
    nomes_corrigidos = 0

    # 5. Nome civil ao lado do apelido
    #
    # ⚠️ A API do TikTok parou de devolver `cpf_name` (medido 01/09/2026:
    # /order/202309/orders responde `code: 0 Success` mas o pedido vem so'
    # com {"has_updated_recipient_address": false, "packages": []}). Como
    # mapa vazio era tratado como "nenhum apelido a corrigir", a etiqueta
    # saia com o nick sem avisar ninguem -- o Jota so' percebeu no papel.
    #
    # A NF-e passa a ser a fonte primaria: traz o nome como consta no CPF,
    # que e' exatamente o criterio que os Correios usam para entregar. A API
    # fica como reserva, para o caso de voltar a responder.
    mapa_pedido_civil = {}
    if nome_real:
        # A pasta de XMLs era alimentada a MAO e ficou 23 dias parada
        # (31/08 -> 23/09/2026): todo pedido de setembro saiu com o apelido
        # do TikTok porque o mapa vinha vazio, sem erro nenhum.
        #
        # ⚡ Sincroniza no MAXIMO 1x por dia. Varrer 800 notas custa ~10s e
        # nao muda nada entre duas geracoes seguidas -- o Jota reportou a
        # esteira lenta em 24/09 e este era um dos culpados. O Olist ja' e'
        # a fonte primaria do nome (abaixo); a NF-e cobre o historico.
        try:
            import core_nfe_sync as sync_nfe
            _idade, _ = sync_nfe.idade_do_acervo()
            if _idade is None or _idade >= 1:
                r_sync = sync_nfe.sincronizar(dias=45)
                if r_sync["baixados"]:
                    log.info("NF-e sincronizadas: %d nova(s)", r_sync["baixados"])
        except Exception as exc:
            log.warning("Sync de NF-e falhou (segue com o que ja' esta' em disco): %s", exc)

        try:
            mapa_pedido_civil = nfe.mapa_por_pedido() or {}
            log.info("Nomes civis da NF-e: %d pedido(s)", len(mapa_pedido_civil))
        except Exception as exc:
            log.warning("Nomes civis via NF-e indisponiveis: %s", exc)

        # ⭐ O Olist SOBRESCREVE a NF-e, de proposito.
        #
        # No TikTok a nota e' emitida com o APELIDO no `xNome` (medido
        # 23/09/2026: "Deliiciasdapri77" e "Gustavo", ambos com CPF valido
        # ao lado), enquanto `cliente.nome` no Olist traz o nome do
        # cadastro -- o mesmo do CPF:
        #
        #   586221382076499950  NF-e "Deliiciasdapri77"
        #                       Olist "Priscila Cristina Alves"  <- correto
        #
        # Como a NF-e so' erra para menos (nunca inventa nome que o cadastro
        # nao tenha), deixar o Olist ganhar e' seguro: onde a nota ja' esta'
        # certa os dois dizem a mesma coisa.
        try:
            do_olist = cnr.mapa_por_pedido_olist([4, 7]) or {}
            mapa_pedido_civil.update(do_olist)
            log.info("Nomes civis do Olist: %d pedido(s)", len(do_olist))
        except Exception as exc:
            log.warning("Nomes civis via Olist indisponiveis: %s", exc)
            do_olist = {}

        if not mapa_pedido_civil:
            try:
                ids_tt = [str(o) for o in mapa_tt.values()]
                mapa_pedido_civil = cnr.mapa_por_pedido_tiktok(ids_tt) or {}
                log.info("Nomes civis da API TikTok: %d pedido(s)",
                         len(mapa_pedido_civil))
            except Exception as exc:
                log.warning("Mapa de nomes civis indisponivel: %s", exc)

    # Ordem NORMAL #1..#N. A inversão física testada em 30/08 foi revertida
    # a pedido do Jota (31/08) — segue a ordem natural da esteira.
    for ordem_idx, (caminho, numero_pedido) in enumerate(arquivos, start=1):
        if not Path(caminho).exists():
            continue
        try:
            parcial = fitz.open(caminho)
            for pno in range(parcial.page_count):
                pag_orig = parcial[pno]
                largura, altura = pag_orig.rect.width, pag_orig.rect.height

                # So' a PRIMEIRA pagina de cada pedido recebe a faixa inferior e o carimbo
                if pno == 0 and numerar:
                    nova_pag = doc_final.new_page(width=largura, height=altura)
                    area_util = fitz.Rect(0, 0, largura, altura - FAIXA_NUMERO_PT)
                    nova_pag.show_pdf_page(area_util, parcial, pno)

                    # Nome civil ao lado do apelido.
                    #
                    # O nome IMPRESSO vem da propria etiqueta, lido por
                    # posicao (`nome_impresso_na_pagina`), porque a API do
                    # TikTok nao devolve mais `recipient_address`. O nome
                    # CIVIL vem da NF-e, que e' o nome como consta no CPF.
                    #
                    # Regra do Jota (01/09): "ao lado do nick coloca os 2
                    # primeiros nomes sempre limitando caracter, se ele
                    # possuir mais q isso de nome ai vc replica o nome
                    # completo abaixo do endereco". Assim a linha do
                    # destinatario nao estoura (foi o que sobrepos texto na
                    # tentativa anterior) e o nome completo continua na
                    # etiqueta, atendendo ao lado legal.
                    dados_nfe = mapa_pedido_civil.get(str(numero_pedido))
                    if dados_nfe:
                        try:
                            _, civil = dados_nfe
                            impresso = nfe.nome_impresso_na_pagina(nova_pag)
                            if impresso and cnr.e_apelido(impresso, civil):
                                nomes_corrigidos += _escrever_nome_civil(
                                    nova_pag, impresso, civil, cnr)
                        except Exception as exc:
                            log.debug("Nome civil do pedido %s: %s",
                                      numero_pedido, exc)

                    # Carimbo #N  #m (Posição na Esteira + Número Sequencial Olist)
                    num_olist = mapa_olist.get(str(numero_pedido))
                    texto_num = f"#{ordem_idx}  #{num_olist}" if num_olist else f"#{ordem_idx}"
                    texto_sku = mapa_skus.get(str(numero_pedido), "")

                    largura_num = fitz.get_text_length(texto_num, fontname="hebo", fontsize=FONTE_NUMERO)
                    x_num = max(10, (largura - largura_num) / 2)
                    y_num = altura - FAIXA_NUMERO_PT + FONTE_NUMERO + 1.0
                    nova_pag.insert_text(fitz.Point(x_num, y_num), texto_num, fontsize=FONTE_NUMERO, fontname="hebo", color=COR)

                    if texto_sku:
                        largura_sku = fitz.get_text_length(texto_sku, fontname="helv", fontsize=FONTE_SKU)
                        x_sku = max(8, (largura - largura_sku) / 2)
                        y_sku = _baseline_sku(altura)
                        nova_pag.insert_text(fitz.Point(x_sku, y_sku), texto_sku, fontsize=FONTE_SKU, fontname="helv", color=COR_SKU)
                else:
                    # Demais páginas (ex: cartão de agradecimento) entram sem faixa
                    doc_final.insert_pdf(parcial, from_page=pno, to_page=pno)
            parcial.close()
        except Exception as exc:
            log.warning("Falha ao processar arquivo %s: %s", caminho, exc)

    if doc_final.page_count == 0:
        doc_final.close()
        return {
            "pdf": None,
            "total": 0,
            "fora_da_esteira": 0,
            "por_canal": baixado.get("por_canal") or {},
            "erros": ["Nenhuma página gerada nas etiquetas."],
            "segundos": round(time.time() - inicio, 1),
            "resumo": "Nenhuma página disponível",
        }

    doc_final.save(destino)
    doc_final.close()

    # 6.5 Blinda o PDF para a impressora termica generica.
    # A etiqueta CRUA do canal imprime bem; a nossa, montada, falhava (Jota,
    # 31/08). Diferenca medida: nos acrescentamos Helvetica/Helvetica-Bold
    # Type1 NAO embutidas (carimbo #N e SKU) e o cartao traz 9 fontes sem
    # nome, tambem nao embutidas. Termica barata nao tem catalogo de fonte:
    # substituir falha e ela pula/aborta a pagina. Rasterizando em 1-bit nao
    # sobra fonte, alpha nem vetor — so' bitmap, que todo firmware aceita.
    #
    # ⚠️ A falha aqui NAO pode ser silenciosa (achado 01/09/2026): faltava
    # `numpy` no requirements-deploy.txt, a blindagem estourava ImportError
    # na nuvem e este except engolia — o Jota baixou um PDF sem blindagem
    # acreditando que era o corrigido. O erro agora sobe em `erros`, que a
    # tela ja' mostra.
    erro_blindagem = None
    try:
        import core_etiqueta_termica as termica
        r_term = termica.blindar_para_termica(destino)
        log.info("PDF blindado para termica: %s MB", r_term.get("mb"))
    except Exception as exc:      # PDF normal ainda serve — nunca derrubar o lote
        log.warning("Blindagem para termica falhou: %s", exc)
        erro_blindagem = (
            f"⚠️ PDF gerado SEM blindagem para térmica ({exc}). "
            "Ele deve imprimir, mas se a impressora falhar/pular página, "
            "é este o motivo."
        )

    # 7. Limpa os PDFs intermediarios que sobram no Downloads.
    #
    # Sem isto cada clique enche a pasta e o operador perde qual e' o bom
    # (Jota, 19/08: "baixou varias versoes na pasta"; 24/09: "o lixo nao
    # deveria ficar"). Medido em 24/09: **6 arquivos por rodada**, e a
    # versao anterior desta limpeza pegava so' 2 — os `_10x15` e
    # `_10x15_com_cartao` escapavam porque nao estavam na lista de sufixos.
    #
    # O `destino` NUNCA entra na lista — e' justamente o que ficou pronto.
    _sufixos = ("", "_com_cartao", "_10x15", "_10x15_com_cartao")
    for canal in ("tiktok", "shopee", "ml"):
        pdf_canal = (baixado.get("por_canal") or {}).get(canal, {}).get("pdf")
        if not pdf_canal:
            continue
        base = str(pdf_canal)[:-4] if str(pdf_canal).endswith(".pdf") else str(pdf_canal)
        for sufixo in _sufixos:
            alvo = Path(f"{base}{sufixo}.pdf")
            if alvo.exists() and alvo.resolve() != destino.resolve():
                try:
                    alvo.unlink()
                except OSError:
                    pass

    # O `_10x15` que a normalizacao cria como copia do PDF FINAL tambem sobra
    resto = destino.with_name(destino.stem + "_10x15.pdf")
    if resto.exists():
        try:
            resto.unlink()
        except OSError:
            pass

    segundos = round(time.time() - inicio, 1)
    resumo = f"{len(arquivos)} etiquetas na ordem da esteira"
    if nomes_corrigidos:
        resumo += f" · {nomes_corrigidos} com nome civil acrescentado"
    elif nome_real and mapa_tt and not mapa_pedido_civil:
        # ⚠️ ARMADILHA DOCUMENTADA (_INBOX/Aprendizado_20260923_Etiqueta_
        # Nome_Civil_3_Falhas.md): a FONTE vazia e' o sinal de que quebrou de
        # novo -- ficou 23 dias assim sem ninguem ver.
        #
        # Zero correcoes com a fonte CHEIA e' normal: quer dizer que ninguem
        # no lote usava apelido (`e_apelido()` recusa nome ja' civil). Alertar
        # nesse caso era falso positivo -- apareceu na primeira geracao depois
        # da correcao, com os 3 pedidos do lote tendo nome real.
        resumo += " · ⚠️ fonte de nome civil vazia (pode ter quebrado)"
        log.warning("Fonte de nome civil vazia em lote com %d pacote(s) "
                    "TikTok — conferir mapa_por_pedido_olist()", len(mapa_tt))
    if fora:
        resumo += f" · {fora} fora da sequência (no fim da pilha)"

    return {
        "pdf": str(destino),
        "total": len(arquivos),
        # Quantas etiquetas ficaram de fora por ja' estarem numa onda.
        "filtrados_por_onda": filtrados,
        "fora_da_esteira": fora,
        "nomes_corrigidos": nomes_corrigidos,
        "por_canal": baixado.get("por_canal"),
        "erros": (baixado.get("erros") or [])
                 + ([erro_blindagem] if erro_blindagem else []),
        "segundos": segundos,
        "resumo": resumo,
        # A tela avisa se a pilha veio do cache da Fase 1 ou de download —
        # decisao de performance nao pode ser silenciosa.
        "reaproveitado": baixado.get("reaproveitado", False),
        "motivo_cache": baixado.get("motivo_cache", ""),
    }


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    r = gerar()
    print()
    print(f"  {r['resumo']}  ({r['segundos']}s)")
    print(f"  {r['pdf']}")
    for e in r["erros"]:
        print(f"  ⚠️ {e}")
