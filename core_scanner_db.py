# ==============================================================================
# NOME DO SCRIPT: core_scanner_db.py
# DESCRICAO: Camada SQLite do Scanner de Conferencia de Pedidos J&F Co.
#            Gerencia o indice rastreio -> pedido (tabela rastreio_pedidos) e o
#            log de conferencias do dia (tabela conferencias).
# AUTOR: Conselho J&F Co. - Roo Code (sub-gerente operacional)
# VERSAO: 1.0
# DATA: 2026-08-02
# STATUS: Operacional
# REF: plans/scanner_conferencia_pedidos_2026-08-02.md
# ==============================================================================
"""Camada de persistencia local do Scanner de Conferencia.

Banco: ``local_db/rastreio_pedidos.db`` (SQLite — local, rapido, zero latencia).

Tabelas:
  rastreio_pedidos — indice rastreio -> pedido (canal, n pedido e-commerce,
                     SKU, produto, cor, kit, cliente, CEP, peso).
  conferencias     — log de bipagens conferidas (tracking + timestamp).

Uso tipico:
    import core_scanner_db as db
    db.init_db()
    db.upsert_rastreio({...})
    reg = db.buscar_por_tracking("BR266773820648X")
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
from datetime import date

log = logging.getLogger("core_scanner_db")

# Path canonico do banco (mesma pasta de config_prazo_ml.json / shopee_tokens.json).
_LOCAL_DB_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "local_db")
DB_PATH = os.path.join(_LOCAL_DB_DIR, "rastreio_pedidos.db")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS rastreio_pedidos (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    tracking TEXT NOT NULL,
    canal TEXT NOT NULL,             -- 'shopee' | 'ml' | 'tiktok' | 'correios' | 'manual'
    pedido_ecommerce TEXT NOT NULL,
    sku_principal TEXT,
    produto_nome TEXT,
    cor TEXT,
    kit TEXT,
    cliente_nome TEXT,
    cep TEXT,
    peso_kg REAL,
    imagem_url TEXT,                 -- miniatura da variacao vendida (Shopee/ML/TikTok)
    itens_json TEXT,                 -- TODOS os itens do pedido (JSON), nao so o primeiro
    alerta_volume TEXT,              -- '' | 'mesmo_kit_multiplo' | 'multi_itens' (core_separacao)
    shipment_id TEXT,                -- ML: o code128 GRANDE da etiqueta (47828318513)
    pack_id TEXT,                    -- ML: agrupador de varios pedidos (2000014650915375)
    criado_em TEXT DEFAULT (datetime('now','localtime')),
    atualizado_em TEXT DEFAULT (datetime('now','localtime')),
    UNIQUE(tracking, canal)
);

CREATE INDEX IF NOT EXISTS idx_rastreio_tracking ON rastreio_pedidos(tracking);
CREATE INDEX IF NOT EXISTS idx_rastreio_pedido ON rastreio_pedidos(pedido_ecommerce);
-- ⚠️ Os indices de shipment_id/pack_id NAO ficam aqui: em banco antigo as
-- colunas ainda nao existem quando este script roda, o executescript aborta
-- inteiro e a migracao abaixo nunca acontece. Eles sao criados em init_db(),
-- depois dos ALTER TABLE.

CREATE TABLE IF NOT EXISTS conferencias (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    tracking TEXT NOT NULL,
    canal TEXT,
    pedido_ecommerce TEXT,
    sku_principal TEXT,
    status TEXT DEFAULT 'conferido',
    sku_validado TEXT,
    validacao_nivel TEXT,
    conferido_em TEXT DEFAULT (datetime('now','localtime'))
);

CREATE INDEX IF NOT EXISTS idx_conferencias_tracking ON conferencias(tracking);
CREATE INDEX IF NOT EXISTS idx_conferencias_data ON conferencias(conferido_em);
"""


def _get_conn() -> sqlite3.Connection:
    """Abre conexao SQLite (WAL para leitura concorrente com o Streamlit)."""
    os.makedirs(_LOCAL_DB_DIR, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db() -> None:
    """Cria o schema (tabelas + indices) se ainda nao existir. Idempotente."""
    try:
        with _get_conn() as conn:
            conn.executescript(_SCHEMA)
            # Migracao leve: SQLite nao tem ADD COLUMN IF NOT EXISTS. Confere
            # as colunas e adiciona `status` em bancos criados antes da v1.1.
            colunas = {r[1] for r in conn.execute("PRAGMA table_info(conferencias)").fetchall()}
            if "status" not in colunas:
                conn.execute("ALTER TABLE conferencias ADD COLUMN status TEXT DEFAULT 'conferido'")
            # v1.2: dupla conferencia — guarda o codigo bipado da etiqueta de
            # produto e o nivel de casamento (exato/atomo/spu_cor/...).
            if "sku_validado" not in colunas:
                conn.execute("ALTER TABLE conferencias ADD COLUMN sku_validado TEXT")
            if "validacao_nivel" not in colunas:
                conn.execute("ALTER TABLE conferencias ADD COLUMN validacao_nivel TEXT")
            # v1.3: miniatura da variacao vendida, pra conferencia visual na
            # busca (a foto que o cliente viu ao comprar).
            cols_rastreio = {r[1] for r in conn.execute("PRAGMA table_info(rastreio_pedidos)").fetchall()}
            if "imagem_url" not in cols_rastreio:
                conn.execute("ALTER TABLE rastreio_pedidos ADD COLUMN imagem_url TEXT")
            # v1.4: pedido multi-item. Guardar so o primeiro item fazia o
            # scanner mostrar 1 peca quando o cliente comprou varias sob a
            # MESMA etiqueta -- risco de despachar caixa incompleta.
            if "itens_json" not in cols_rastreio:
                conn.execute("ALTER TABLE rastreio_pedidos ADD COLUMN itens_json TEXT")
            # v1.5 (M4): indice de video da expedicao. `video_segundo` aponta o
            # instante exato da bipagem dentro da gravacao continua, para
            # extrair o trecho numa disputa. `print_arquivo` guarda o JPEG do
            # momento -- resposta rapida sem precisar abrir video.
            if "video_arquivo" not in colunas:
                conn.execute("ALTER TABLE conferencias ADD COLUMN video_arquivo TEXT")
            if "video_segundo" not in colunas:
                conn.execute("ALTER TABLE conferencias ADD COLUMN video_segundo INTEGER")
            if "print_arquivo" not in colunas:
                conn.execute("ALTER TABLE conferencias ADD COLUMN print_arquivo TEXT")
            # v1.6: codigos alternativos da etiqueta do Mercado Livre.
            # A etiqueta do ML NAO imprime o numero do pedido nem o rastreio
            # da transportadora no code128 principal -- imprime o SHIPMENT_ID
            # (ex: 47828318513). O `tracking_number` (888002469041186) so'
            # aparece em texto pequeno. Sem indexar o shipment o bipador lia o
            # codigo certo e nao achava nada.
            # `pack_id` (ex: 2000014650915375) agrupa varios pedidos numa
            # etiqueta so' -- mesmo risco de caixa incompleta que o multi-item.
            if "shipment_id" not in cols_rastreio:
                conn.execute("ALTER TABLE rastreio_pedidos ADD COLUMN shipment_id TEXT")
            if "pack_id" not in cols_rastreio:
                conn.execute("ALTER TABLE rastreio_pedidos ADD COLUMN pack_id TEXT")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_rastreio_shipment "
                         "ON rastreio_pedidos(shipment_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_rastreio_pack "
                         "ON rastreio_pedidos(pack_id)")
            # v1.7 (25/08): classificacao de risco de volume
            if "alerta_volume" not in cols_rastreio:
                conn.execute("ALTER TABLE rastreio_pedidos ADD COLUMN alerta_volume TEXT")
            # v1.8 (27/08): Identificação fiscal automática — Chave de Acesso da DANFE (44 dígitos)
            # e Número da Nota Fiscal (ex: 434) para bipagem via código de barras da NF da Receita.
            if "chave_nfe" not in cols_rastreio:
                conn.execute("ALTER TABLE rastreio_pedidos ADD COLUMN chave_nfe TEXT")
            if "numero_nf" not in cols_rastreio:
                conn.execute("ALTER TABLE rastreio_pedidos ADD COLUMN numero_nf TEXT")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_rastreio_chave_nfe ON rastreio_pedidos(chave_nfe)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_rastreio_numero_nf ON rastreio_pedidos(numero_nf)")
        log.info("Banco do scanner pronto: %s", DB_PATH)
    except sqlite3.Error as e:  # pragma: no cover - defensivo
        log.error("Falha ao inicializar banco do scanner: %s", e)


def normalizar_codigo(codigo: str) -> str:
    """Limpa um codigo lido (tracking/n pedido): remove espacos e normaliza caixa."""
    if not codigo:
        return ""
    return " ".join(str(codigo).split()).strip().upper()


def _serializar_itens(itens) -> str | None:
    """Serializa a lista de itens do pedido pra coluna `itens_json`.

    Devolve None quando nao ha lista — assim o COALESCE do upsert preserva o
    que ja estava gravado em vez de apagar.
    """
    if not itens:
        return None
    try:
        return json.dumps(itens, ensure_ascii=False)
    except (TypeError, ValueError) as e:
        log.warning("Falha ao serializar itens do pedido: %s", e)
        return None


def desserializar_itens(registro: dict) -> list[dict]:
    """Le `itens_json` de um registro do indice. Sempre devolve lista.

    Registros antigos (gravados antes da v1.4) nao tem a coluna preenchida:
    nesse caso monta uma lista de 1 item com os campos legados, pra UI poder
    tratar todo pedido do mesmo jeito.
    """
    bruto = (registro or {}).get("itens_json")
    if bruto:
        try:
            dados = json.loads(bruto)
            if isinstance(dados, list) and dados:
                return dados
        except (TypeError, ValueError) as e:
            log.warning("itens_json invalido em %s: %s", registro.get("tracking"), e)
    sku = (registro or {}).get("sku_principal")
    if not sku:
        return []
    return [{
        "sku": sku,
        "nome": registro.get("produto_nome") or "",
        "cor": registro.get("cor") or "",
        "kit": registro.get("kit") or "",
        "quantidade": 1,
        "imagem_url": registro.get("imagem_url") or "",
    }]


def upsert_rastreio(registro: dict) -> bool:
    """Insere ou atualiza um vinculo rastreio -> pedido no indice.

    Retorna True quando inseriu/atualizou, False em caso de erro.
    """
    try:
        tracking = normalizar_codigo(registro.get("tracking", ""))
        if not tracking:
            return False
        canal = (registro.get("canal") or "manual").lower().strip()
        with _get_conn() as conn:
            conn.execute(
                """
                INSERT INTO rastreio_pedidos
                    (tracking, canal, pedido_ecommerce, sku_principal,
                     produto_nome, cor, kit, cliente_nome, cep, peso_kg,
                     imagem_url, itens_json, alerta_volume, shipment_id, pack_id,
                     chave_nfe, numero_nf,
                     criado_em, atualizado_em)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                        datetime('now','localtime'), datetime('now','localtime'))
                ON CONFLICT(tracking, canal) DO UPDATE SET
                    pedido_ecommerce = excluded.pedido_ecommerce,
                    shipment_id      = COALESCE(excluded.shipment_id, rastreio_pedidos.shipment_id),
                    pack_id          = COALESCE(excluded.pack_id, rastreio_pedidos.pack_id),
                    chave_nfe        = COALESCE(excluded.chave_nfe, rastreio_pedidos.chave_nfe),
                    numero_nf        = COALESCE(excluded.numero_nf, rastreio_pedidos.numero_nf),
                    sku_principal    = COALESCE(excluded.sku_principal, rastreio_pedidos.sku_principal),
                    produto_nome     = COALESCE(excluded.produto_nome, rastreio_pedidos.produto_nome),
                    cor              = COALESCE(excluded.cor, rastreio_pedidos.cor),
                    kit              = COALESCE(excluded.kit, rastreio_pedidos.kit),
                    cliente_nome     = COALESCE(excluded.cliente_nome, rastreio_pedidos.cliente_nome),
                    cep              = COALESCE(excluded.cep, rastreio_pedidos.cep),
                    peso_kg          = COALESCE(excluded.peso_kg, rastreio_pedidos.peso_kg),
                    imagem_url       = COALESCE(excluded.imagem_url, rastreio_pedidos.imagem_url),
                    itens_json       = COALESCE(excluded.itens_json, rastreio_pedidos.itens_json),
                    alerta_volume    = COALESCE(NULLIF(excluded.alerta_volume, ''), rastreio_pedidos.alerta_volume),
                    atualizado_em    = datetime('now','localtime')
                """,
                (
                    tracking,
                    canal,
                    str(registro.get("pedido_ecommerce") or ""),
                    registro.get("sku_principal"),
                    registro.get("produto_nome"),
                    registro.get("cor"),
                    registro.get("kit"),
                    registro.get("cliente_nome"),
                    registro.get("cep"),
                    registro.get("peso_kg"),
                    registro.get("imagem_url"),
                    _serializar_itens(registro.get("itens")),
                    registro.get("alerta_volume") or "",
                    normalizar_codigo(str(registro.get("shipment_id") or "")) or None,
                    normalizar_codigo(str(registro.get("pack_id") or "")) or None,
                    normalizar_codigo(str(registro.get("chave_nfe") or "")) or None,
                    str(registro.get("numero_nf") or "").strip() or None,
                ),
            )
        _espelhar_na_nuvem(registro)
        return True
    except sqlite3.Error as e:
        log.error("Erro ao inserir rastreio %s: %s", registro.get("tracking"), e)
        return False


def _espelhar_na_nuvem(registro: dict) -> None:
    """Replica o vinculo pro Supabase, de onde o celular le.

    O SQLite so' existe na maquina que rodou o populator. O app no Streamlit
    Cloud nao tem esse arquivo -- antes ele vinha COMMITADO no repo, o que
    trazia de volta dado velho a cada deploy (foi assim que um vinculo errado
    sobreviveu a duas correcoes de codigo e o scanner abriu uma calcinha no
    lugar de uma meia). Com o banco fora do git, a nuvem passa a ser a ponte.

    Best-effort de proposito: a bancada nao pode parar porque a internet caiu.
    Falha vira aviso no log, o indice local segue valendo, e o proximo
    populator tenta de novo.
    """
    try:
        import core_scanner_supabase as nuvem
    except Exception:
        return
    if not (getattr(nuvem, "SUPABASE_URL", "") and getattr(nuvem, "SUPABASE_KEY", "")):
        return
    try:
        payload = dict(registro)
        # A nuvem guarda os itens ja' serializados (o SQLite serializa na hora
        # do INSERT); sem isso o mobile recebe a lista crua e nao sabe ler.
        if "itens" in payload and "itens_json" not in payload:
            payload["itens_json"] = _serializar_itens(payload.get("itens")) or "[]"
        if not nuvem.salvar_rastreio_nuvem(payload):
            log.warning("Nao consegui espelhar %s na nuvem (segue so' no indice local).",
                        registro.get("tracking"))
    except Exception as e:
        log.warning("Falha ao espelhar %s na nuvem: %s", registro.get("tracking"), e)


def buscar_por_tracking(tracking: str) -> dict | None:
    """Retorna o registro do indice cujo tracking bate exatamente. None se nao achar."""
    t = normalizar_codigo(tracking)
    if not t:
        return None
    try:
        with _get_conn() as conn:
            row = conn.execute(
                "SELECT * FROM rastreio_pedidos WHERE tracking = ? ORDER BY id LIMIT 1",
                (t,),
            ).fetchone()
        return dict(row) if row else None
    except sqlite3.Error as e:
        log.error("Erro ao buscar tracking %s: %s", t, e)
        return None


def buscar_por_codigo_ml(codigo: str) -> dict | None:
    """Resolve o code128 da etiqueta do Mercado Livre (shipment ou pack).

    A etiqueta do ML e' a unica que NAO traz o numero do pedido: o codigo de
    barras grande e' o ``shipment_id`` (ex: 47828318513) e o ``Pack ID``
    (2000014650915375) aparece so' em texto. Nenhum dos dois casava com
    tracking nem com pedido_ecommerce -- o bipador lia o codigo certo e
    respondia "nao encontrado".

    Ordem: shipment primeiro (e' o que a pistola le), pack depois.
    """
    c = normalizar_codigo(codigo)
    if not c:
        return None
    try:
        with _get_conn() as conn:
            for coluna in ("shipment_id", "pack_id"):
                row = conn.execute(
                    f"SELECT * FROM rastreio_pedidos WHERE {coluna} = ? ORDER BY id LIMIT 1",
                    (c,),
                ).fetchone()
                if row:
                    return dict(row)
        return None
    except sqlite3.Error as e:
        log.error("Erro ao buscar codigo ML %s: %s", c, e)
        return None


def contar_por_pack(pack_id: str) -> int:
    """Quantos pedidos distintos compartilham o mesmo Pack ID do ML.

    Pack agrupa varias compras do mesmo cliente numa etiqueta so'. Se a
    bancada fechar a caixa com um pedido quando o pack tem tres, sai
    incompleta -- mesma classe de falha do multi-item.
    """
    p = normalizar_codigo(pack_id)
    if not p:
        return 0
    try:
        with _get_conn() as conn:
            row = conn.execute(
                "SELECT COUNT(DISTINCT pedido_ecommerce) FROM rastreio_pedidos "
                "WHERE pack_id = ?",
                (p,),
            ).fetchone()
        return int(row[0]) if row else 0
    except sqlite3.Error as e:
        log.error("Erro ao contar pack %s: %s", p, e)
        return 0


def _colunas_fiscais_ok(conn: sqlite3.Connection) -> bool:
    """True se as colunas fiscais da v1.8 existem no banco.

    ⚠️ Existe porque a migracao v1.8 (`chave_nfe`/`numero_nf`) so' roda quando
    `init_db()` e' chamado. Em processo que subiu antes da migracao -- ou em
    banco de outra maquina -- as colunas faltam e todo SELECT nelas explode com
    "no such column". Antes, o `except sqlite3.Error` engolia isso e devolvia
    None: a identificacao fiscal parecia "nao achou" quando na verdade nunca
    tinha sido possivel consultar. Falha silenciosa em scanner de conferencia
    e' inaceitavel -- aqui ela vira aviso no log.
    """
    cols = {r[1] for r in conn.execute("PRAGMA table_info(rastreio_pedidos)").fetchall()}
    faltando = {"chave_nfe", "numero_nf"} - cols
    if faltando:
        log.warning(
            "Colunas fiscais ausentes no banco (%s). Rode db.init_db() para migrar "
            "-- busca por chave DANFE/numero de NF esta INDISPONIVEL ate' la.",
            ", ".join(sorted(faltando)),
        )
        return False
    return True


def buscar_por_chave_nfe(chave_nfe: str) -> dict | None:
    """Retorna o registro do indice correspondente a chave de 44 digitos da DANFE.

    So' casa pela chave COMPLETA (44 digitos) -- identificador unico de verdade.
    O fallback antigo, que caia pro numero curto da NF, foi removido daqui: ver
    a nota em `buscar_por_numero_nf` sobre por que numero de NF sozinho nao
    identifica pedido com seguranca.
    """
    c = normalizar_codigo(chave_nfe)
    if not c or len(c) != 44 or not c.isdigit():
        return None
    try:
        with _get_conn() as conn:
            if not _colunas_fiscais_ok(conn):
                return None
            row = conn.execute(
                "SELECT * FROM rastreio_pedidos WHERE chave_nfe = ? ORDER BY id LIMIT 1",
                (c,),
            ).fetchone()
        return dict(row) if row else None
    except sqlite3.Error as e:
        log.error("Erro ao buscar chave NF-e %s: %s", c, e)
        return None


def buscar_por_numero_nf(numero_nf: str, canal: str | None = None) -> dict | None:
    """Retorna o registro do indice pelo numero sequencial da NF (ex: 434).

    ⚠️ SO' casa na coluna `numero_nf`, nunca por aproximacao. Numero de NF tem
    1-9 digitos e NAO e' identificador global: duas notas de series/canais
    diferentes podem repetir o mesmo sequencial, e um numero curto colide com o
    final de qualquer pedido de marketplace (incidente 27/08 -- ver
    `buscar_parcial`). Por isso:

      * comparacao e' exata na coluna dedicada, com e sem zeros a esquerda;
      * `canal` (opcional) estreita ainda mais quando o chamador souber a origem;
      * se houver MAIS DE UM candidato, devolve None -- ambiguidade nao pode
        virar bipagem errada; o operador resolve pelo tracking.
    """
    bruto = str(numero_nf).strip()
    n = bruto.lstrip("0")
    if not n:
        return None
    try:
        with _get_conn() as conn:
            if not _colunas_fiscais_ok(conn):
                return None
            sql = "SELECT * FROM rastreio_pedidos WHERE (numero_nf = ? OR numero_nf = ?)"
            params: list = [n, bruto]
            if canal:
                sql += " AND canal = ?"
                params.append(str(canal).strip().lower())
            sql += " ORDER BY id LIMIT 2"
            rows = conn.execute(sql, tuple(params)).fetchall()
        if len(rows) != 1:
            if len(rows) > 1:
                log.warning(
                    "Numero de NF %s ambiguo (%d candidatos) -- ignorado por seguranca.",
                    n, len(rows),
                )
            return None
        return dict(rows[0])
    except sqlite3.Error as e:
        log.error("Erro ao buscar numero NF %s: %s", n, e)
        return None


def buscar_por_pedido(pedido_ecommerce: str) -> dict | None:
    """Retorna o registro do indice cujo numero de pedido e-commerce bate."""
    p = normalizar_codigo(pedido_ecommerce)
    if not p:
        return None
    try:
        with _get_conn() as conn:
            row = conn.execute(
                "SELECT * FROM rastreio_pedidos WHERE pedido_ecommerce = ? ORDER BY id LIMIT 1",
                (p,),
            ).fetchone()
        return dict(row) if row else None
    except sqlite3.Error as e:
        log.error("Erro ao buscar pedido %s: %s", p, e)
        return None


def buscar_parcial(fragmento: str, limit: int = 8) -> list[dict]:
    """Busca por PEDACO do codigo (>=3 chars), em qualquer posicao.

    Serve pro autocomplete da digitacao manual: o espaco amostral do dia e'
    pequeno (dezenas de pedidos), entao um LIKE '%frag%' resolve sem indice
    especial. Casa tanto com o tracking quanto com o numero do pedido do
    e-commerce -- o operador pode digitar o final do rastreio ou o meio do
    numero do pedido, o que estiver mais legivel na etiqueta.

    Ordena PENDENTES primeiro (o que ainda falta bipar hoje e' o que ele
    quer), depois os ja conferidos. Dentro de cada grupo, os mais recentes.

    ⚠️ ANCORAGEM ANTI-COLISAO (27/08, incidente real): fragmento puramente
    NUMERICO curto casava no MEIO de qualquer numero e trazia o pedido errado.
    Caso real: a "identificacao fiscal" extraiu o numero da NF `434` da chave
    DANFE e o `LIKE '%434%'` casou com o pedido ML `2000017946805434` (termina
    em 434) -- o scanner abriu uma CALCINHA quando a etiqueta era de uma meia
    invisivel. Numero de NF tem 1-9 digitos; pedido de marketplace tem 16-18.
    Colisao era questao de tempo, nao azar.

    Regra agora:
      * fragmento numerico com < 6 digitos  -> so casa por PREFIXO (`frag%`).
        Sufixo tambem foi cortado: `434` casa como final de
        `2000017946805434` -- exatamente a colisao do incidente. Quem digita
        pouco digito ve poucos resultados, e completa ate' desambiguar.
      * fragmento >= 6 chars ou com letras  -> mantem `%frag%` (espaco amostral
        pequeno o suficiente, e rastreio alfanumerico nao colide na pratica).
        Digitar o final do rastreio continua funcionando a partir de 6 digitos.
    """
    frag = normalizar_codigo(fragmento)
    if len(frag) < 3:
        return []

    # Numero curto: so' prefixo. Qualquer outra coisa: busca livre.
    if frag.isdigit() and len(frag) < 6:
        clausula = (
            "WHERE UPPER(r.tracking) LIKE ? "
            "   OR UPPER(COALESCE(r.pedido_ecommerce,'')) LIKE ?"
        )
        params = (f"{frag}%", f"{frag}%")
    else:
        clausula = (
            "WHERE UPPER(r.tracking) LIKE ? "
            "   OR UPPER(COALESCE(r.pedido_ecommerce,'')) LIKE ?"
        )
        params = (f"%{frag}%", f"%{frag}%")

    try:
        with _get_conn() as conn:
            rows = conn.execute(
                f"""
                SELECT r.*,
                       CASE WHEN c.tracking IS NULL THEN 0 ELSE 1 END AS ja_conferido
                  FROM rastreio_pedidos r
                  LEFT JOIN conferencias c
                         ON c.tracking = r.tracking
                        AND date(c.conferido_em) = date('now','localtime')
                 {clausula}
                 GROUP BY r.tracking
                 ORDER BY ja_conferido ASC, r.id DESC
                 LIMIT ?
                """,
                (*params, max(1, int(limit))),
            ).fetchall()
        return [dict(r) for r in rows]
    except sqlite3.Error as e:
        log.error("Erro na busca parcial '%s': %s", frag, e)
        return []


def listar_rastreios(limit: int = 50) -> list[dict]:
    """Lista os vinculos mais recentes do indice (para diagnostico/UI)."""
    try:
        with _get_conn() as conn:
            rows = conn.execute(
                "SELECT * FROM rastreio_pedidos ORDER BY id DESC LIMIT ?",
                (max(1, int(limit)),),
            ).fetchall()
        return [dict(r) for r in rows]
    except sqlite3.Error as e:
        log.error("Erro ao listar rastreios: %s", e)
        return []


def _indice_video(tracking: str) -> tuple[str, int | None, str]:
    """Devolve (video_arquivo, video_segundo, print_arquivo) da gravacao ativa.

    M4: a bipagem NUNCA pode falhar por causa do video. Qualquer problema
    (modulo ausente, sem sessao, camera ocupada) devolve vazio e a conferencia
    segue normal.
    """
    try:
        import core_video_expedicao as _video
    except Exception:
        return "", None, ""
    try:
        info = _video.sinalizar_atividade_e_obter_indice(tracking) or {}
        if not info.get("sessao_ativa"):
            return "", None, ""
        arquivo = info.get("video_arquivo") or ""
        segundo = info.get("video_segundo")
        print_arq = ""
        try:
            print_arq = _video.capturar_print(tracking) or ""
        except Exception:
            pass  # print e' bonus; ausencia dele nao invalida o video
        return arquivo, segundo, print_arq
    except Exception as e:
        log.warning("Indice de video indisponivel para %s: %s", tracking, e)
        return "", None, ""


def registrar_conferencia(tracking: str, pedido_ecommerce: str = "",
                          canal: str = "", sku_principal: str = "",
                          status: str = "conferido", sku_validado: str = "",
                          validacao_nivel: str = "") -> bool:
    """Registra uma bipagem no log do dia. Evita duplicata por tracking/dia.

    ``status`` registra o tipo de conferencia: ``conferido`` (normal) ou
    ``cancelado`` (pedido marcado como cancelado — nao despachar).

    ``sku_validado`` e ``validacao_nivel`` guardam a dupla conferencia: o
    codigo bipado da etiqueta de produto e como ele casou com o pedido
    (exato/atomo/spu_cor). Vazios quando o operador conferiu so pela
    etiqueta de envio, sem bipar a peca.

    Retorna True se registrou, False se duplicado ou erro.
    """
    t = normalizar_codigo(tracking)
    if not t:
        return False
    try:
        with _get_conn() as conn:
            duplicado = conn.execute(
                """
                SELECT 1 FROM conferencias
                WHERE tracking = ? AND date(conferido_em) = date('now','localtime')
                LIMIT 1
                """,
                (t,),
            ).fetchone()
            if duplicado:
                return False
            # M4: indice do video no MESMO insert (evita corrida e linha orfa).
            # Se nao houver gravacao ativa, entra vazio e nada muda.
            video_arquivo, video_segundo, print_arquivo = _indice_video(t)
            conn.execute(
                """
                INSERT INTO conferencias (tracking, canal, pedido_ecommerce, sku_principal,
                                          status, sku_validado, validacao_nivel,
                                          video_arquivo, video_segundo, print_arquivo)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (t, (canal or "").lower(), str(pedido_ecommerce or ""),
                 sku_principal or "", status or "conferido",
                 sku_validado or "", validacao_nivel or "",
                 video_arquivo or None, video_segundo, print_arquivo or None),
            )
        return True
    except sqlite3.Error as e:
        log.error("Erro ao registrar conferencia %s: %s", t, e)
        return False


def ja_conferido_hoje(tracking: str) -> bool:
    """True se o tracking ja foi conferido hoje."""
    t = normalizar_codigo(tracking)
    if not t:
        return False
    try:
        with _get_conn() as conn:
            row = conn.execute(
                """
                SELECT 1 FROM conferencias
                WHERE tracking = ? AND date(conferido_em) = date('now','localtime')
                LIMIT 1
                """,
                (t,),
            ).fetchone()
        return row is not None
    except sqlite3.Error:
        return False


def contar_conferidos_hoje() -> int:
    """Quantas bipagens foram conferidas hoje."""
    try:
        with _get_conn() as conn:
            row = conn.execute(
                "SELECT COUNT(*) AS n FROM conferencias WHERE date(conferido_em) = date('now','localtime')",
            ).fetchone()
        return int(row["n"]) if row else 0
    except sqlite3.Error as e:
        log.error("Erro ao contar conferidos: %s", e)
        return 0


def contar_pendentes() -> int:
    """Rastreios no indice ainda nao conferidos hoje."""
    try:
        with _get_conn() as conn:
            row = conn.execute(
                """
                SELECT COUNT(*) AS n FROM (
                    SELECT tracking FROM rastreio_pedidos
                    EXCEPT
                    SELECT tracking FROM conferencias
                    WHERE date(conferido_em) = date('now','localtime')
                )
                """,
            ).fetchone()
        return int(row["n"]) if row else 0
    except sqlite3.Error as e:
        log.error("Erro ao contar pendentes: %s", e)
        return 0


def stats_dia() -> dict:
    """Resumo do dia: conferidos, pendentes e total no indice."""
    try:
        with _get_conn() as conn:
            total = conn.execute("SELECT COUNT(DISTINCT tracking) AS n FROM rastreio_pedidos").fetchone()
            conf = conn.execute(
                "SELECT COUNT(*) AS n FROM conferencias WHERE date(conferido_em) = date('now','localtime')",
            ).fetchone()
        total_n = int(total["n"]) if total else 0
        conf_n = int(conf["n"]) if conf else 0
        return {
            "conferidos_hoje": conf_n,
            "pendentes": max(0, total_n - conf_n),
            "total_indice": total_n,
            "data": date.today().isoformat(),
        }
    except sqlite3.Error as e:
        log.error("Erro ao calcular stats do dia: %s", e)
        return {"conferidos_hoje": 0, "pendentes": 0, "total_indice": 0,
                "data": date.today().isoformat()}


def ultimas_conferencias(limit: int = 10) -> list[dict]:
    """Ultimas conferencias registradas (para historico na UI)."""
    try:
        with _get_conn() as conn:
            rows = conn.execute(
                "SELECT * FROM conferencias ORDER BY id DESC LIMIT ?",
                (max(1, int(limit)),),
            ).fetchall()
        return [dict(r) for r in rows]
    except sqlite3.Error as e:
        log.error("Erro ao listar conferencias: %s", e)
        return []


# Inicializa o schema no import (idempotente).
init_db()
