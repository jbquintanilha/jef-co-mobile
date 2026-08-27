# ==============================================================================
# NOME DO SCRIPT: core_etiquetas_ml_api.py
# DESCRICAO: Baixa as etiquetas de envio do Mercado Livre via API oficial
# FUNCAO: O ML era o unico canal fora da esteira de etiquetas -- TikTok e
#         Shopee ja' baixavam sozinhos, o ML so' pelo modal do Olist.
# STATUS: ATIVO
# VERSAO: 1.0
# DATA: 24/08/2026
# AUTOR: Terminador (001) / Claude
# ==============================================================================
"""Etiquetas do Mercado Livre — mesmo contrato de Shopee e TikTok.

## Por que existia essa lacuna

A esteira (`pages/17_Lista_Separacao.py`) tinha botao para TikTok e Shopee.
O ML ficava de fora: o operador abria o modal do Olist, que dispara 1 pop-up
por etiqueta e apanha do bloqueador do Chrome.

O motor ja' existia — `core_esteira.baixar_etiqueta_ml()`, validado em prod.
Faltava a camada que lista os envios pendentes e unifica o PDF.

## ⚠️ Por que existe pedido ML que NENHUMA API imprime (apurado 25/08)

Testado em prod com o pedido 2000014650915375 (shipment 47847608424):

| Caminho | Resposta |
|---|---|
| API do ML `/shipment_labels` | `400 SHPLAB0200 NOT_PRINTABLE_STATUS` -- envio `pending` |
| API do Olist `/expedicao/{id}/etiquetas` | recusa: forma de envio "Mercado Envios" nao tem recurso de etiqueta |
| Tela do Olist (modal) | ✅ imprime, com DANFE embutida |

Ou seja: enquanto o envio esta' `pending`, a etiqueta so' sai pela TELA do
Olist. Nao e' limitacao deste modulo -- e' regra das duas plataformas.
`pending` = envio ainda represado pelo ML, antes de virar `ready_to_ship`.

Este modulo cobre o que E' automatizavel: os envios `ready_to_ship`.
Para o resto, a bancada usa o modal do Olist.

## O que e' diferente no ML

O ML entrega **varias etiquetas num PDF so'** quando se passa mais de um
`shipment_id` em `shipment_ids` (separados por virgula). Nao precisa baixar
uma a uma como na Shopee. Mantemos o download em lotes por seguranca: a URL
tem limite de tamanho e um lote gigante falha inteiro.

⚠️ A etiqueta do ML traz DANFE simplificada embutida e sai em formato proprio
(nao e' A4 com etiqueta no canto, como a Shopee). Por isso NAO passa pelo
`normalizar_10x15` por padrao — recortar quebraria a nota.

⚠️ `ready_to_print` e' o status que importa. Envio ja' despachado devolve
etiqueta, mas reimprimir gera divergencia na coleta.

Uso:
    import core_etiquetas_ml_api as mla
    r = mla.baixar_etiquetas()
    # {"pdf": ..., "total": n, "arquivos": [...], "falhas": [(id, motivo)]}
"""

from __future__ import annotations
import core_env_loader

import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

PASTA_SAIDA = Path(os.path.expanduser("~")) / "Downloads"

# Quantos shipments por chamada. O endpoint aceita lista, mas URL longa
# demais falha inteira -- em lote pequeno a falha fica isolada.
LOTE = 10

# Preenchido por `listar_envios_a_despachar`: envios ML `pending`, que
# NENHUMA API imprime (nem ML nem Olist) e saem so' pelo modal do Olist.
_ULTIMOS_REPRESADOS: list[dict[str, Any]] = []

# Status do SHIPMENT que o ML aceita imprimir.
#
# ⚠️ Nao basta o pedido estar "aberto" no Olist: em teste real (24/08) a lista
# trouxe um envio `delivered` e um `cancelled`, porque a situacao do Olist
# estava atrasada em relacao ao ML.
#
# ⚠️ `pending` NAO imprime, apesar do nome sugerir "a fazer". Testado em prod
# com o shipment 47847608424 (pending/buffered), o ML respondeu:
#     HTTP 400  SHPLAB0200  NOT_PRINTABLE_STATUS
#     "Shipment 47847608424 status is pending"
# `pending` e' o envio ainda represado pelo ML (substatus `buffered`), antes
# de virar `ready_to_ship`. So' este ultimo gera etiqueta.
STATUS_IMPRIMIVEL = ("ready_to_ship",)

# Status que comprovadamente NAO deve gerar etiqueta.
STATUS_BLOQUEADO = ("cancelled", "delivered", "not_delivered", "shipped",
                    "pending")


def listar_envios_a_despachar(*, dias: int = 30) -> list[dict[str, Any]]:
    """Envios do ML aguardando despacho, a partir dos pedidos abertos no Olist.

    Usa o Olist como fonte da lista (mesma logica do populador do scanner) e
    o ML so' para resolver o shipment. Assim o que aparece aqui e' exatamente
    o que esta' na lista de separacao -- nao um recorte diferente do ML.

    ``dias`` existe so' para manter a assinatura igual a de Shopee/TikTok
    (o chamador generico passa o mesmo argumento pros tres). Aqui a janela
    quem define e' a lista de pedidos abertos do Olist.

    Devolve [{"pedido", "shipment_id", "pack_id", "status", "cliente"}].
    """
    del dias  # ver docstring: compatibilidade de assinatura
    from core_esteira import obter_prazo_despacho_ml
    from core_scanner_populator import _pedido_pendente, _pedidos_olist

    envios: list[dict[str, Any]] = []
    represados: list[dict[str, Any]] = []   # `pending`: so' saem pelo modal do Olist
    vistos: set[str] = set()

    for p in _pedidos_olist():
        ecom = p.get("ecommerce") or {}
        canal = ecom.get("nome") or ""
        num = ecom.get("numeroPedidoEcommerce") or ""
        if "mercado" not in canal.lower() or not num:
            continue
        if not _pedido_pendente(p):
            continue
        try:
            info = obter_prazo_despacho_ml(num)
        except Exception as exc:
            log.warning("ML %s: shipment nao resolvido: %s", num, exc)
            continue
        sid = str(info.get("shipment_id") or "")
        if not sid or sid in vistos:
            continue
        # Corte pelo status REAL do envio no ML, nao pela situacao do Olist.
        status = str(info.get("status") or "").lower()
        if status in STATUS_BLOQUEADO or (status and status not in STATUS_IMPRIMIVEL):
            log.info("ML %s ignorado: shipment %s esta' '%s'", num, sid, status)
            # `pending` nao e' erro nem pedido perdido: e' envio represado pelo
            # ML. A bancada precisa saber que ele existe e sai pelo modal do
            # Olist, senao parece que o pedido sumiu da lista.
            if status == "pending":
                represados.append({
                    "pedido": num,
                    "shipment_id": sid,
                    "cliente": (p.get("cliente") or {}).get("nome") or "",
                })
            continue
        # Pack: varios pedidos, UMA etiqueta. Sem este corte a mesma folha
        # sairia repetida, uma vez por pedido do pack.
        pack = str(info.get("pack_id") or "")
        if pack and pack in vistos:
            continue
        vistos.add(sid)
        if pack:
            vistos.add(pack)
        envios.append({
            "pedido": num,
            "shipment_id": sid,
            "pack_id": pack,
            "status": info.get("status") or "",
            "cliente": (p.get("cliente") or {}).get("nome") or "",
        })
    # Guarda os represados para `baixar_etiquetas` reportar sem precisar
    # varrer o Olist de novo. Lista separada, nao marcador dentro de envio:
    # misturar os dois faria envio represado parecer imprimivel.
    global _ULTIMOS_REPRESADOS
    _ULTIMOS_REPRESADOS = represados
    return envios


def _baixar_lote(shipment_ids: list[str]) -> bytes | None:
    """Baixa um lote de etiquetas num PDF unico. None quando falha."""
    from core_esteira import baixar_etiqueta_ml

    # O endpoint aceita varios ids separados por virgula e devolve 1 PDF.
    return baixar_etiqueta_ml(",".join(shipment_ids), formato="pdf")


def _unificar_pdfs(arquivos: list[Path], saida: str | Path) -> str:
    try:
        from pypdf import PdfWriter
    except ImportError:
        from PyPDF2 import PdfWriter  # type: ignore

    writer = PdfWriter()
    for arq in arquivos:
        writer.append(str(arq))

    saida = Path(saida)
    saida.parent.mkdir(parents=True, exist_ok=True)
    with open(saida, "wb") as fh:
        writer.write(fh)
    return str(saida)


def baixar_etiquetas(
    shipment_ids: list[str] | None = None,
    *,
    dias: int = 30,
    saida: str | Path | None = None,
    unificar: bool = True,
) -> dict[str, Any]:
    """Baixa as etiquetas do ML e devolve um PDF unico.

    shipment_ids=None -> descobre sozinho pelos pedidos ML abertos no Olist.

    Retorna (mesmo formato de Shopee/TikTok):
        {"pdf": caminho, "total": n, "arquivos": [...], "falhas": [(id, motivo)]}
    """
    envios: list[dict[str, Any]] = []
    if shipment_ids is None:
        envios = listar_envios_a_despachar(dias=dias)
        shipment_ids = [e["shipment_id"] for e in envios]

    if not shipment_ids:
        rep = list(_ULTIMOS_REPRESADOS)
        if rep:
            aviso = (f"{len(rep)} pedido(s) do ML aguardando despacho, mas o "
                     f"envio ainda esta' 'pending' no Mercado Livre -- nenhuma "
                     f"API imprime nesse estado. Use o modal do Olist.")
        else:
            aviso = "Nenhum pedido do Mercado Livre aguardando despacho."
        return {"pdf": None, "total": 0, "arquivos": [], "falhas": [],
                "aviso": aviso, "represados": rep}

    PASTA_SAIDA.mkdir(parents=True, exist_ok=True)
    tmp_dir = PASTA_SAIDA / f"_ml_etiquetas_{datetime.now():%Y%m%d_%H%M%S}"
    tmp_dir.mkdir(exist_ok=True)

    arquivos: list[Path] = []
    falhas: list[tuple[str, str]] = []

    for i in range(0, len(shipment_ids), LOTE):
        lote = shipment_ids[i:i + LOTE]
        try:
            conteudo = _baixar_lote(lote)
        except Exception as exc:
            conteudo = None
            log.warning("Lote ML %s falhou: %s", lote, exc)
        if not conteudo:
            # Lote inteiro falhou: tenta uma a uma pra nao perder as boas
            # por causa de um shipment problematico.
            for sid in lote:
                try:
                    um = _baixar_lote([sid])
                except Exception as exc:
                    falhas.append((sid, f"{type(exc).__name__}: {exc}"[:120]))
                    continue
                if um:
                    destino = tmp_dir / f"{sid}.pdf"
                    destino.write_bytes(um)
                    arquivos.append(destino)
                else:
                    falhas.append((sid, "etiqueta indisponivel"))
            continue
        destino = tmp_dir / f"lote_{i // LOTE:02d}.pdf"
        destino.write_bytes(conteudo)
        arquivos.append(destino)

    resultado: dict[str, Any] = {
        "pdf": None,
        "total": len(shipment_ids) - len(falhas),
        "arquivos": [str(a) for a in arquivos],
        "falhas": falhas,
        "envios": envios,
        "represados": list(_ULTIMOS_REPRESADOS),
    }

    if unificar and arquivos:
        resultado["pdf"] = _unificar_pdfs(
            arquivos,
            saida or PASTA_SAIDA / f"etiquetas_ml_{datetime.now():%Y%m%d_%H%M}.pdf",
        )
    return resultado


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    envios = listar_envios_a_despachar()
    print(f"{len(envios)} envios ML a despachar")
    for e in envios:
        pack = f" pack={e['pack_id']}" if e["pack_id"] else ""
        print(f"  pedido={e['pedido']:16} shipment={e['shipment_id']:14}"
              f"{pack}  {e['status']:12} {e['cliente'][:28]}")
