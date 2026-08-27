# ==============================================================================
# NOME DO SCRIPT: core_scanner_feedback.py
# DESCRICAO: Registra erros/melhorias reportados pelo operador direto da tela
#            do Scanner, com o contexto completo do item em tela no momento do
#            clique. Grava em Markdown legivel por humano e por agente.
# AUTOR: Terminador (001)
# VERSAO: 1.0 | DATA: 2026-08-09
# STATUS: Operacional
# ==============================================================================
"""Caderno de erros/melhorias do Scanner de Conferencia.

Por que existe: quando algo da errado na bancada, o relato costuma chegar horas
depois e sem contexto ("aquele pedido nao achou"). Aqui o operador clica no
botao na hora, escreve em uma frase, e o sistema anexa **sozinho** tudo que
estava em tela -- codigo lido, pedido resolvido, SKU, canal, resultado da
validacao. Isso transforma "nao funcionou" em um caso reproduzivel.

Formato: Markdown com um bloco por ocorrencia, mais recente no topo. Escolhido
por ser legivel direto no editor e facil de um agente varrer sem parser.

Uso pelo agente: ler `REGISTRO_PATH` quando o Comandante pedir "verificar
erros". Cada bloco tem status ABERTO/RESOLVIDO -- marcar como resolvido via
`marcar_resolvido(id)` depois de corrigir.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime
from pathlib import Path

log = logging.getLogger("core_scanner_feedback")

RAIZ = Path(r"C:\JF_Automacoes")
REGISTRO_PATH = RAIZ / "SCANNER_ERROS.md"

_CABECALHO = """# 🐛 Scanner — Erros e Melhorias Reportados

> Registrado pelo operador direto da tela do Scanner de Conferência.
> Cada bloco traz o contexto automático do item que estava em tela.
>
> **Para o agente:** ao corrigir um item, marque-o como `✅ RESOLVIDO` e
> descreva o que foi feito. Não apague blocos — o histórico é a memória do
> que já quebrou.

---
"""


def _agora() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _proximo_id(conteudo: str) -> int:
    """Le os IDs ja usados e devolve o proximo. Comeca em 1."""
    ids = [int(n) for n in re.findall(r"^## #(\d+) ", conteudo, re.MULTILINE)]
    return (max(ids) + 1) if ids else 1


def _garantir_arquivo() -> str:
    """Cria o arquivo com cabecalho se nao existir. Devolve o conteudo atual."""
    if not REGISTRO_PATH.exists():
        REGISTRO_PATH.write_text(_CABECALHO, encoding="utf-8")
        return _CABECALHO
    return REGISTRO_PATH.read_text(encoding="utf-8")


def _linha_contexto(rotulo: str, valor) -> str:
    if valor in (None, "", [], {}):
        return ""
    return f"- **{rotulo}:** `{valor}`\n"


def montar_contexto(*, codigo_lido: str = "", resultado: dict | None = None,
                    validacao: dict | None = None,
                    extras: dict | None = None) -> dict:
    """Consolida o que estava em tela num dict plano, pronto pra gravar.

    Nao levanta excecao: contexto ausente vira campo vazio, porque perder o
    relato do operador por causa de contexto faltando seria pior.
    """
    resultado = resultado or {}
    validacao = validacao or {}
    return {
        "codigo_lido": codigo_lido or "",
        "encontrado": resultado.get("encontrado"),
        "canal": resultado.get("canal") or "",
        "tracking": resultado.get("tracking") or "",
        "pedido_ecommerce": resultado.get("pedido_ecommerce") or "",
        "sku": resultado.get("sku") or "",
        "modelo": resultado.get("modelo") or "",
        "cor": resultado.get("cor") or "",
        "kit": resultado.get("kit") or "",
        "spu": resultado.get("spu") or "",
        "cancelado": resultado.get("cancelado"),
        "status_pedido": resultado.get("status_pedido") or "",
        "validacao_nivel": validacao.get("nivel") or "",
        "validacao_ok": validacao.get("ok"),
        "validacao_lido": validacao.get("lido") or "",
        "validacao_esperado": validacao.get("esperado") or "",
        **(extras or {}),
    }


def registrar(texto: str, contexto: dict | None = None,
              *, tipo: str = "erro") -> int | None:
    """Grava um relato no caderno. Devolve o ID gerado (ou None se falhar).

    ``tipo``: ``erro`` | ``melhoria``. So muda o icone e o rotulo.
    """
    texto = (texto or "").strip()
    if not texto:
        return None

    try:
        conteudo = _garantir_arquivo()
        novo_id = _proximo_id(conteudo)
        icone = "🐛" if tipo == "erro" else "💡"
        rotulo = "ERRO" if tipo == "erro" else "MELHORIA"

        ctx = contexto or {}
        linhas_ctx = "".join([
            _linha_contexto("Código lido", ctx.get("codigo_lido")),
            _linha_contexto("Canal", ctx.get("canal")),
            _linha_contexto("Tracking", ctx.get("tracking")),
            _linha_contexto("Pedido e-commerce", ctx.get("pedido_ecommerce")),
            _linha_contexto("SKU", ctx.get("sku")),
            _linha_contexto("Modelo", ctx.get("modelo")),
            _linha_contexto("Cor", ctx.get("cor")),
            _linha_contexto("Kit", ctx.get("kit")),
            _linha_contexto("Resolveu?", ctx.get("encontrado")),
            _linha_contexto("Validação (nível)", ctx.get("validacao_nivel")),
            _linha_contexto("Validação (lido)", ctx.get("validacao_lido")),
            _linha_contexto("Validação (esperado)", ctx.get("validacao_esperado")),
        ]) or "- _(sem item em tela no momento do relato)_\n"

        bloco = (
            f"\n## #{novo_id} {icone} {rotulo} — {_agora()}\n\n"
            f"**Status:** 🔴 ABERTO\n\n"
            f"**Relato do operador:**\n\n> {texto}\n\n"
            f"**Contexto automático:**\n\n{linhas_ctx}\n"
            f"<details><summary>contexto completo (json)</summary>\n\n"
            f"```json\n{json.dumps(ctx, ensure_ascii=False, indent=2)}\n```\n"
            f"</details>\n\n---\n"
        )

        # Insere logo apos o cabecalho: mais recente no topo.
        marcador = "---\n"
        pos = conteudo.find(marcador)
        if pos == -1:
            novo = conteudo + bloco
        else:
            corte = pos + len(marcador)
            novo = conteudo[:corte] + bloco + conteudo[corte:]

        REGISTRO_PATH.write_text(novo, encoding="utf-8")
        log.info("Feedback #%s registrado em %s", novo_id, REGISTRO_PATH)
        return novo_id
    except Exception as e:  # pragma: no cover - defensivo
        log.error("Falha ao registrar feedback: %s", e)
        return None


def contar_abertos() -> int:
    """Quantos relatos ainda estao com status ABERTO."""
    if not REGISTRO_PATH.exists():
        return 0
    try:
        return REGISTRO_PATH.read_text(encoding="utf-8").count("🔴 ABERTO")
    except Exception:
        return 0


def marcar_resolvido(item_id: int, nota: str = "") -> bool:
    """Marca um item como resolvido. Usado pelo agente apos corrigir."""
    if not REGISTRO_PATH.exists():
        return False
    try:
        conteudo = REGISTRO_PATH.read_text(encoding="utf-8")
        padrao = re.compile(
            rf"(## #{item_id} .*?\n\n\*\*Status:\*\* )🔴 ABERTO", re.DOTALL)
        if not padrao.search(conteudo):
            return False
        extra = f"\n\n**Correção ({_agora()}):** {nota}" if nota else ""
        novo = padrao.sub(rf"\g<1>✅ RESOLVIDO{extra}", conteudo, count=1)
        REGISTRO_PATH.write_text(novo, encoding="utf-8")
        return True
    except Exception as e:  # pragma: no cover
        log.error("Falha ao marcar #%s como resolvido: %s", item_id, e)
        return False
