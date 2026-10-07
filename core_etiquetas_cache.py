# ==============================================================================
# NOME DO SCRIPT: core_etiquetas_cache.py
# DESCRICAO: Reaproveita as etiquetas que a Fase 1 ja' baixou
# FUNCAO: Fase 1 e Fase 3 chamavam `baixar_tudo()` cada uma -- duas viagens
#         as APIs pelo mesmo PDF (~38s dos ~62s da pilha). Este modulo
#         devolve o resultado da Fase 1 quando ele COBRE o que a Fase 3
#         precisa, e manda baixar quando nao cobre.
# STATUS: ATIVO
# VERSAO: 1.0 | DATA: 24/09/2026
# AUTOR: Terminador (001) / Claude
# ==============================================================================
"""Cache de etiquetas entre as fases da esteira.

⛔ **Leia `pages/ESTEIRA_EXPEDICAO.md` antes de mexer aqui.**

## Por que nao e' um cache comum

A esteira trabalha por **ONDA**: um lote travado que as 7 fases percorrem.
Reaproveitar "a ultima coisa baixada" quebraria isso de duas formas:

1. **Escopos diferentes.** A Fase 1 baixa por `ciclo_selecionado`; a Fase 3
   filtra pela onda travada. Sao conjuntos que podem divergir.
2. **Pedido novo.** Se chegou pedido depois da Fase 1, devolver o cache
   imprimiria a pilha sem ele -- ou, pior, um pedido novo entraria numa onda
   ja' separada e ficaria "perdido" nela.

Por isso a regra e' **cobertura exata**, nao "tem cache?": so' serve quando
o cache contem todos os pedidos pedidos e os arquivos ainda existem em
disco. Qualquer duvida -> baixa de novo. Errar para o lado de baixar custa
segundos; errar para o lado de reaproveitar imprime pilha errada.

## Por que este cache NAO vai para o Supabase

A onda e' estado compartilhado e ja' vive no Supabase
(`core_ondas_supabase`): quais pedidos, quais fases concluidas. Isso
sincroniza entre o PC da bancada e o app mobile, e deve continuar assim.

Este cache e' outra coisa: ele aponta para **arquivos PDF em disco**
(`~/Downloads/...`). Gravar esses caminhos no Supabase seria pior que
inutil -- o mobile leria um caminho da maquina do Jota, `Path.exists()`
daria falso na nuvem, e no limite alguem "consertaria" isso reaproveitando
um cache que nao existe daquele lado.

**A divisao e' proposital:**

| O que | Onde | Sincroniza |
|---|---|---|
| Onda: pedidos, fases, ordem | Supabase | ✅ sim |
| Etiqueta: o PDF baixado | disco local | ❌ e' arquivo fisico |

No mobile (Streamlit Cloud) a Fase 1 baixa para o container e o cache vale
so' dentro daquela sessao -- que e' exatamente o comportamento correto,
porque o PDF tambem e' local la'. `session_state` basta nos dois lados.

## O lixo no Downloads

Regra do Jota (24/09): *"pode focar desde que limpe a cada nova rodada,
quando ele nao for mais necessario"*.

Por isso o cache **nao vive no Downloads**: fica em `~/.cache/jf_etiquetas`,
e `limpar()` roda a cada `guardar()` -- rodada nova leva a anterior junto.
O operador so' ve' o PDF final; nenhum intermediario sobra a' vista.

Uso:
    import core_etiquetas_cache as cache
    r = cache.baixar_ou_reaproveitar(
        canais=["tiktok", "shopee", "ml"],
        com_cartao=True,
        somente=numeros_da_onda,          # None = fila livre
        cache=st.session_state.get("etq_tudo"),
    )
"""

from __future__ import annotations

import logging
import shutil
import time
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

# Os PDFs do cache moram FORA do Downloads. O Jota (24/09): "antes ele
# salvava diversos PDF nos downloads e ia remontando la', gerando diversos
# documentos de lixo... o lixo nao deveria ficar".
#
# Pasta propria resolve os dois lados: o operador nunca ve' estes arquivos,
# e a limpeza pode ser agressiva sem risco de apagar algo que o Jota baixou
# na mao.
PASTA_CACHE = Path.home() / ".cache" / "jf_etiquetas"

# Rodada nova zera a anterior. Cache de etiqueta nao tem valor historico --
# se o lote mudou, o conteudo velho so' atrapalha.
VALIDADE_HORAS = 12


def limpar(tudo: bool = False) -> int:
    """Apaga o cache em disco. Devolve quantos arquivos saíram.

    `tudo=False` (padrao) poupa o que ainda esta' dentro da validade --
    e' a limpeza de rotina, chamada a cada rodada nova. `tudo=True` zera,
    para o botao "rebaixar" e para o encerramento.
    """
    if not PASTA_CACHE.is_dir():
        return 0

    limite = time.time() - VALIDADE_HORAS * 3600
    apagados = 0
    for arq in PASTA_CACHE.rglob("*"):
        if not arq.is_file():
            continue
        if tudo or arq.stat().st_mtime < limite:
            try:
                arq.unlink()
                apagados += 1
            except OSError:
                pass

    # Diretorios vazios que sobraram das rodadas antigas
    for d in sorted((p for p in PASTA_CACHE.rglob("*") if p.is_dir()),
                    reverse=True):
        try:
            d.rmdir()
        except OSError:
            pass

    if apagados:
        log.info("Cache de etiquetas: %d arquivo(s) apagado(s)", apagados)
    return apagados


def guardar(resultado: dict[str, Any]) -> dict[str, Any]:
    """Move os PDFs do resultado para a pasta de cache e reaponta os caminhos.

    Chamado pela Fase 1 depois de baixar. Sem isto o cache apontaria para o
    Downloads, e a limpeza de la' (que o operador espera) invalidaria tudo.
    """
    limpar()                       # rodada nova: leva a anterior junto
    PASTA_CACHE.mkdir(parents=True, exist_ok=True)

    movidos = 0
    for info in (resultado.get("por_canal") or {}).values():
        novos: list[str] = []
        for arq in (info.get("arquivos") or []):
            origem = Path(arq)
            if not origem.exists():
                continue
            destino = PASTA_CACHE / origem.name
            try:
                shutil.copy2(origem, destino)
                novos.append(str(destino))
                movidos += 1
            except OSError as exc:
                log.warning("Nao consegui guardar %s: %s", origem.name, exc)
                novos.append(str(origem))   # mantem o original como plano B
        if novos:
            info["arquivos"] = novos

    if movidos:
        log.info("Cache de etiquetas: %d arquivo(s) guardado(s) em %s",
                 movidos, PASTA_CACHE)
    return resultado


def _pedidos_no_cache(cache: dict[str, Any]) -> set[str]:
    """Identificadores que o cache realmente tem, so' com arquivo em disco.

    O `stem` de cada arquivo e' o identificador do canal (Shopee=order_sn,
    TikTok=package_id, ML=shipment_id) -- mesma convencao que
    `core_etiquetas_na_esteira` usa para casar etiqueta com pedido.
    """
    achados: set[str] = set()
    for info in (cache.get("por_canal") or {}).values():
        for arq in (info.get("arquivos") or []):
            if Path(arq).exists():
                achados.add(Path(arq).stem)
    return achados


def _canais_do_cache(cache: dict[str, Any]) -> set[str]:
    """Canais que o cache cobre, ignorando os que falharam."""
    return {c for c, info in (cache.get("por_canal") or {}).items()
            if not info.get("erro")}


def cache_serve(cache: dict[str, Any] | None,
                canais: list[str],
                somente: set[str] | None,
                mapa_tt: dict[str, str] | None = None) -> tuple[bool, str]:
    """(serve?, motivo). O motivo entra no log e na tela — nunca decidir mudo.

    Args:
        mapa_tt: {package_id: order_id}. A etiqueta do TikTok e' nomeada pelo
            PACOTE, mas `somente` fala em PEDIDO; sem esta ponte todo pedido
            do TikTok pareceria ausente do cache.
    """
    if not cache or not isinstance(cache, dict):
        return False, "sem cache da Fase 1"

    if not cache.get("pdf"):
        return False, "cache da Fase 1 nao tem PDF"

    faltando = set(canais) - _canais_do_cache(cache)
    if faltando:
        return False, f"cache nao cobre o(s) canal(is) {', '.join(sorted(faltando))}"

    no_cache = _pedidos_no_cache(cache)
    if not no_cache:
        return False, "arquivos do cache nao estao mais em disco"

    if somente is None:
        # Fila livre: nao da' pra provar que o cache esta' completo, porque
        # nao ha' lista de referencia. Pedido novo desde a Fase 1 passaria
        # despercebido -- baixa.
        return False, "sem onda travada (fila livre baixa sempre, para nao perder pedido novo)"

    # `somente` fala em PEDIDO; o cache do TikTok guarda PACOTE.
    traduzidos = set(no_cache)
    if mapa_tt:
        traduzidos |= {mapa_tt[p] for p in no_cache if p in mapa_tt}

    ausentes = {p for p in somente if p} - traduzidos
    if ausentes:
        exemplo = ", ".join(sorted(ausentes)[:3])
        return False, (f"{len(ausentes)} pedido(s) da onda nao estao no cache "
                       f"(ex: {exemplo})")

    return True, f"cache cobre os {len(somente)} pedido(s) da onda"


def baixar_ou_reaproveitar(
    canais: list[str],
    *,
    com_cartao: bool = False,
    somente: set[str] | None = None,
    cache: dict[str, Any] | None = None,
    mapa_tt: dict[str, str] | None = None,
    forcar: bool = False,
) -> dict[str, Any]:
    """Devolve o mesmo formato de `core_etiquetas_todas.baixar_tudo()`.

    Acrescenta duas chaves para a tela poder ser honesta com o operador:
        `reaproveitado` (bool) e `motivo_cache` (str).

    Args:
        forcar: ignora o cache e baixa. O botao "rebaixar" da tela usa isto.
    """
    import core_etiquetas_todas as cet

    if forcar:
        motivo = "rebaixar pedido pelo operador"
        serve = False
    else:
        serve, motivo = cache_serve(cache, canais, somente, mapa_tt)

    if serve and cache:
        log.info("Etiquetas reaproveitadas da Fase 1: %s", motivo)
        r = dict(cache)
        r["reaproveitado"] = True
        r["motivo_cache"] = motivo
        return r

    log.info("Baixando etiquetas (%s)", motivo)
    r = cet.baixar_tudo(canais=canais, com_cartao=com_cartao, somente=somente)
    r["reaproveitado"] = False
    r["motivo_cache"] = motivo
    return r


# --------------------------------------------------------------------------- #
# FASE 1 INCREMENTAL: baixar so' o que falta da onda
# --------------------------------------------------------------------------- #
#
# Jota, 07/10/2026: "essa opcao sempre baixa tudo... ideal seria baixar os que
# estao na onda selecionada... ou ver quais ja' estao e baixar apenas as que
# faltam". Antes o botao refazia o download de TODO canal marcado, mesmo com
# onda travada e com etiquetas ja' guardadas (~25-70s por canal).
#
# Regra: com um escopo (onda travada / ciclo) so' se baixa a DIFERENCA. Sem
# escopo (fila livre) nada muda -- nao ha' lista de referencia, entao nao da'
# para provar que o cache esta' completo (pedido novo passaria despercebido).

def _pedido_do_arquivo(canal: str, stem: str, mapa_tt: dict[str, str]) -> str:
    """Numero do pedido a que um arquivo de etiqueta pertence."""
    return mapa_tt.get(stem, stem) if canal == "tiktok" else stem


def baixar_so_o_que_falta(canais: list[str], *, com_cartao: bool = False,
                          somente: set[str] | None = None,
                          cache: dict[str, Any] | None = None) -> dict[str, Any]:
    """`baixar_tudo()` que reaproveita o que ja' esta' no cache da Fase 1.

    Devolve o mesmo formato de `core_etiquetas_todas.baixar_tudo()` mais
    `incremental`: {"escopo", "ja_tinha", "baixadas", "faltavam"} para a tela
    ser honesta sobre o que foi (ou nao) baixado.
    """
    import core_etiquetas_todas as cet

    if not somente:
        r = cet.baixar_tudo(canais=canais, com_cartao=com_cartao, somente=None)
        r["incremental"] = None
        return r

    inicio = time.time()
    alvo = {str(x).strip() for x in somente if str(x).strip()}

    # ---- 1. O que o cache ja' tem, traduzido para PEDIDO ------------------- #
    mapa_tt: dict[str, str] = {}
    tem_arquivos = False
    if cache and isinstance(cache, dict):
        stems_tt = {Path(a).stem for a in
                    (((cache.get("por_canal") or {}).get("tiktok") or {})
                     .get("arquivos") or []) if Path(a).exists()}
        if stems_tt:
            try:
                import core_etiquetas_na_esteira as cne
                mapa_tt = cne._mapa_tiktok(stems_tt)
            except Exception as exc:
                log.warning("Incremental: sem ponte pacote->pedido TikTok (%s)", exc)

    antigos: dict[str, list[str]] = {c: [] for c in canais}    # arquivos reaproveitados
    tem: set[str] = set()                                      # pedidos ja' cobertos
    if cache and isinstance(cache, dict) and not any(
            i.get("erro") for c, i in (cache.get("por_canal") or {}).items()
            if c in canais):
        for canal in canais:
            for arq in (((cache.get("por_canal") or {}).get(canal) or {})
                        .get("arquivos") or []):
                if not Path(arq).exists():
                    continue
                pedido = _pedido_do_arquivo(canal, Path(arq).stem, mapa_tt)
                if pedido in alvo:
                    antigos[canal].append(str(arq))
                    tem.add(pedido)
                    tem_arquivos = True

    faltam = alvo - tem

    # ---- 2. Baixa so' a diferenca ------------------------------------------ #
    novo: dict[str, Any] = {"por_canal": {}, "erros": []}
    if faltam:
        novo = cet.baixar_tudo(canais=canais, com_cartao=com_cartao, somente=faltam)
        novo = guardar(novo)         # leva os individuais para a pasta de cache

    if not tem_arquivos:
        # Nada reaproveitavel: e' o download normal, so' que ja' escopado.
        if "amazon" in canais:        # onda filtrada nao audita sozinha
            try:
                import core_etiquetas_amazon_olist as _am
                _am_arqs = (((novo.get("por_canal") or {}).get("amazon") or {})
                            .get("arquivos") or [])
                novo.setdefault("erros", []).extend(
                    _am.auditar_amazon_pendentes({Path(a).stem for a in _am_arqs}))
            except Exception as exc:
                log.warning("Auditoria Amazon (escopo) falhou: %s", exc)
        novo["incremental"] = {"escopo": len(alvo), "ja_tinha": 0,
                               "baixadas": novo.get("total", 0),
                               "faltavam": len(faltam)}
        return novo

    # ---- 3. Junta: reaproveitados + recem-baixados (so' do escopo) --------- #
    por_canal: dict[str, Any] = {}
    for canal in canais:
        info_novo = dict((novo.get("por_canal") or {}).get(canal) or {})
        arqs = list(antigos.get(canal) or [])
        for a in (info_novo.get("arquivos") or []):
            if a not in arqs:
                arqs.append(a)
        info = dict(info_novo)
        info["arquivos"] = arqs
        info["total"] = len(arqs)
        por_canal[canal] = info

    erros = list(novo.get("erros") or [])

    # PDF unico do que ficou valendo: refaz a partir dos individuais (mesmos
    # passos do `gerar()`: normaliza 10x15 e, se pedido, intercala o cartao).
    pdf_final = None
    try:
        import fitz
        import core_etiqueta_normalizar as norm
        from datetime import datetime

        doc = fitz.open()
        for canal in canais:
            for arq in por_canal[canal]["arquivos"]:
                caminho = arq
                try:
                    res = norm.normalizar_10x15(caminho)
                    if res.get("saida") and Path(res["saida"]).exists():
                        caminho = res["saida"]
                except Exception as exc:
                    erros.append(f"{canal}: normalização 10x15 falhou — {exc}")
                if com_cartao:
                    try:
                        import core_etiqueta_com_cartao as ccc
                        alvo_pdf = caminho.replace(".pdf", "_cartao.pdf")
                        if ccc.intercalar_canal_unico(caminho, alvo_pdf, canal).get("ok"):
                            caminho = alvo_pdf
                    except Exception as exc:
                        erros.append(f"{canal}: cartão falhou — {exc}")
                parcial = fitz.open(caminho)
                doc.insert_pdf(parcial)
                parcial.close()
        if doc.page_count:
            destino = (Path.home() / "Downloads"
                       / f"etiquetas_todas_{datetime.now():%Y%m%d_%H%M}.pdf")
            destino.parent.mkdir(parents=True, exist_ok=True)
            doc.save(destino)
            pdf_final = str(destino)
        doc.close()
    except Exception as exc:
        erros.append(f"PDF único não montado: {exc}")

    total = sum(i["total"] for i in por_canal.values())
    n_ja = len(tem)
    n_novas = max(total - n_ja, 0)

    # Auditoria Amazon (SP-API) sobre TUDO o que ficou valendo: pedido Amazon
    # pendente fora desta pilha tambem precisa de alerta visivel.
    if "amazon" in canais:
        try:
            import core_etiquetas_amazon_olist as _am
            ja_alerta = {str(e) for e in erros}
            for al in _am.auditar_amazon_pendentes(
                    {Path(a).stem for a in por_canal["amazon"]["arquivos"]}):
                if al not in ja_alerta:
                    erros.append(al)
        except Exception as exc:
            log.warning("Auditoria Amazon (incremental) falhou: %s", exc)

    nomes = {"tiktok": "tiktok", "shopee": "shopee", "ml": "ml", "amazon": "amazon"}
    partes = [f"{total} etiquetas"] + [f"{nomes[c]} {por_canal[c]['total']}"
                                       for c in canais]
    partes.append(f"♻️ {n_ja} já baixadas · ⬇️ {n_novas} novas")
    if erros:
        partes.append(f"⚠️ {len(erros)} problema(s)")
    segundos = round(time.time() - inicio, 1)
    return {
        "pdf": pdf_final,
        "total": total,
        "por_canal": por_canal,
        "erros": erros,
        "segundos": segundos,
        "com_cartao": com_cartao,
        "resumo": " · ".join(partes) + f" em {segundos}s",
        "incremental": {"escopo": len(alvo), "ja_tinha": n_ja,
                        "baixadas": n_novas, "faltavam": len(faltam)},
    }
