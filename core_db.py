# ==============================================================================
# NOME DO SCRIPT: core_db.py
# DESCRICAO: Biblioteca principal de funcoes/classes core.
# FUNCAO:
# STATUS: PENDENTE_REVISAO
# MOTOR: Monge (003)
# VERSAO: 1.0
# DATA: 16/05/2026
# AUTOR: Violino (000)
# ==============================================================================

import os
from contextlib import contextmanager
from sqlalchemy import create_engine, Column, String, Float, Integer, ForeignKey, DateTime, Date, Text, event, JSON, UniqueConstraint, Numeric
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, relationship
from datetime import datetime
import uuid
from dotenv import load_dotenv

load_dotenv()

Base = declarative_base()

class Fornecedor(Base):
    __tablename__ = 'fornecedores'
    id = Column(Integer, primary_key=True, autoincrement=True)
    nome = Column(String(100), unique=True, nullable=False)
    contato = Column(String(100))
    whatsapp = Column(String(20))
    prazo_entrega_dias = Column(Integer, default=1)
    status = Column(String(50), default='ATIVO')

class SPU(Base):
    __tablename__ = 'spus'
    spu = Column(String(50), primary_key=True)
    ref = Column(String(50), nullable=False)
    fornecedor = Column(String(100))
    categoria = Column(String(100))
    subcategoria = Column(String(100))
    material = Column(String(100))
    composicao = Column(String(200))
    cor_predominante = Column(String(50))
    ncm = Column(String(20))
    cest = Column(String(20))
    titulo_seo = Column(String(200))
    desc_marketing = Column(Text)
    prompt_imagem = Column(Text)
    preco_custo = Column(Float, default=0.0)
    status = Column(String(50), default='ATIVO')
    tags = Column(String(200))
    origem = Column(String(1), default='0')
    unidade = Column(String(10), default='UN')

    variacoes = relationship("SKU", back_populates="pai_spu", cascade="all, delete-orphan", lazy="selectin")
    dnas = relationship("SPUDNAMarketing", back_populates="pai_spu", cascade="all, delete-orphan")

class SPUDNAMarketing(Base):
    __tablename__ = 'spu_dna_marketing'
    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    spu = Column(String(50), ForeignKey('spus.spu'), nullable=False)
    plataforma = Column(String(20), nullable=False)
    titulo_base = Column(String(200))
    descricao_base = Column(Text)
    regra_calculo = Column(JSON)
    preco_sugerido = Column(Float, default=0.0)
    preco_cadastro_base = Column(Float, default=0.0)
    ultima_atualizacao = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    pai_spu = relationship("SPU", back_populates="dnas")
    __table_args__ = (UniqueConstraint('spu', 'plataforma', name='_spu_plataforma_uc'),)

class SKU(Base):
    __tablename__ = 'skus'
    sku = Column(String(100), primary_key=True)
    spu = Column(String(50), ForeignKey('spus.spu'), nullable=False)
    tamanho = Column(String(10))
    cor_especifica = Column(String(50))
    estoque = Column(Integer, default=0)
    preco_custo = Column(Float, default=0.0)
    ean_gtin = Column(String(20))
    peso_g = Column(Integer, default=20)
    comprimento_cm = Column(Integer, default=0)
    largura_cm = Column(Integer, default=0)
    altura_cm = Column(Integer, default=0)
    ncm = Column(String(20))
    cest = Column(String(20))
    origem = Column(String(1), default='0')
    unidade = Column(String(10), default='UN')
    status = Column(String(50), default='ATIVO')
    pai_spu = relationship("SPU", back_populates="variacoes")
    componente_de_kits = relationship("ItemKit", back_populates="produto_sku")

class Kit(Base):
    __tablename__ = 'kits'
    sku_kit = Column(String(100), primary_key=True)
    nome_kit = Column(String(200))
    categoria = Column(String(100))
    ativo = Column(Integer, default=1)
    custo_adicional_kit = Column(Float, default=0.0)
    shopee_preco_venda = Column(Float, default=0.0)
    ml_preco_venda = Column(Float, default=0.0)
    itens = relationship("ItemKit", back_populates="kit", cascade="all, delete-orphan", lazy="selectin")

class ItemKit(Base):
    __tablename__ = 'itens_kit'
    id = Column(Integer, primary_key=True, autoincrement=True)
    sku_kit = Column(String(100), ForeignKey('kits.sku_kit'), nullable=False)
    sku_componente = Column(String(100), ForeignKey('skus.sku'), nullable=False)
    quantidade = Column(Integer, default=1)
    kit = relationship("Kit", back_populates="itens")
    produto_sku = relationship("SKU", back_populates="componente_de_kits")


# ============================================================================
# TABELAS DE ESTOQUE POR DEPÓSITO (2026-06-17)
# ============================================================================

class EstoqueDeposito(Base):
    """Estoque fracionado por depósito (Teresópolis, ML Full SC, etc.)"""
    __tablename__ = 'estoque_depositos'
    id         = Column(Integer, primary_key=True, autoincrement=True)
    sku        = Column(String(100), nullable=False, index=True)
    deposito   = Column(String(50), nullable=False)
    quantidade = Column(Integer, nullable=False, default=0)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    __table_args__ = (UniqueConstraint('sku', 'deposito', name='_sku_deposito_uc'),)


class MovimentacaoEstoque(Base):
    """Registro de auditoria de movimentações entre depósitos"""
    __tablename__ = 'movimentacoes_estoque'
    id            = Column(Integer, primary_key=True, autoincrement=True)
    sku           = Column(String(100), nullable=False, index=True)
    de_deposito   = Column(String(50))
    para_deposito = Column(String(50))
    quantidade    = Column(Integer, nullable=False)
    referencia    = Column(String(200))
    observacao    = Column(Text)
    created_at    = Column(DateTime, default=datetime.utcnow)


# ============================================================================
# TABELAS FINANCEIRAS — MÓDULO CONTROLE PATRIMONIAL (2026-06-05)
# ============================================================================

class FinanceiroCategoria(Base):
    """Categorias de despesa/receita/investimento"""
    __tablename__ = 'financeiro_categorias'
    id = Column(Integer, primary_key=True, autoincrement=True)
    nome = Column(String(100), nullable=False, unique=True)
    tipo = Column(String(10), nullable=False)  # 'receita' | 'despesa' | 'investimento'
    cor = Column(String(7), default='#6366f1')
    created_at = Column(DateTime, default=datetime.utcnow)


class FinanceiroCentroCusto(Base):
    """Centros de custo (canais de venda/operação)"""
    __tablename__ = 'financeiro_centros_custo'
    id = Column(Integer, primary_key=True, autoincrement=True)
    nome = Column(String(100), nullable=False, unique=True)
    descricao = Column(String(255))
    ativo = Column(Integer, default=1)
    created_at = Column(DateTime, default=datetime.utcnow)


class FinanceiroContaPagar(Base):
    """Contas a pagar"""
    __tablename__ = 'financeiro_contas_pagar'
    id = Column(Integer, primary_key=True, autoincrement=True)
    descricao = Column(Text, nullable=False)
    fornecedor = Column(String(200))
    valor = Column(Float, nullable=False)
    data_vencimento = Column(Date, nullable=False)
    data_pagamento = Column(Date)
    status = Column(String(20), default='pendente')
    categoria_id = Column(Integer, ForeignKey('financeiro_categorias.id'))
    centro_custo_id = Column(Integer, ForeignKey('financeiro_centros_custo.id'))
    comprovante_path = Column(String(500))
    observacao = Column(Text)
    parcela = Column(String(20))
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class FinanceiroContaReceber(Base):
    """Contas a receber"""
    __tablename__ = 'financeiro_contas_receber'
    id = Column(Integer, primary_key=True, autoincrement=True)
    descricao = Column(Text, nullable=False)
    origem = Column(String(50))  # 'ml' | 'shopee' | 'whatsapp' | 'outro'
    valor_previsto = Column(Float, nullable=False)
    valor_recebido = Column(Float)
    data_prevista = Column(Date, nullable=False)
    data_recebimento = Column(Date)
    status = Column(String(20), default='previsto')
    pedido_ref = Column(String(100))
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class FinanceiroAtivo(Base):
    """Ativos fixos — equipamentos, máquinas, móveis"""
    __tablename__ = 'financeiro_ativo'
    id = Column(Integer, primary_key=True, autoincrement=True)
    nome = Column(Text, nullable=False)
    tipo = Column(String(50))  # 'equipamento' | 'moveis' | 'veiculo' | 'maquina' | 'outro'
    valor_aquisicao = Column(Float, nullable=False)
    data_aquisicao = Column(Date, nullable=False)
    vida_util_anos = Column(Integer, default=5)
    valor_residual = Column(Float, default=0.0)
    depreciacao_mensal = Column(Float)
    localizacao = Column(String(100))
    notas = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class FinanceiroInsumo(Base):
    """Insumos de produção/embalagem"""
    __tablename__ = 'financeiro_insumos'
    id = Column(Integer, primary_key=True, autoincrement=True)
    nome = Column(Text, nullable=False)
    tipo = Column(String(50))  # 'embalagem' | 'etiqueta' | 'sacos' | 'fita' | 'outro'
    quantidade_estoque = Column(Float, default=0.0)
    unidade = Column(String(20))  # 'un' | 'kg' | 'm' | 'rolo'
    custo_unitario = Column(Float, default=0.0)
    consumo_medio_diario = Column(Float, default=0.0)
    alerta_minimo = Column(Float)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class FinanceiroDiario(Base):
    """Diário financeiro — lançamentos livres diários"""
    __tablename__ = 'financeiro_diario'
    id = Column(Integer, primary_key=True, autoincrement=True)
    data = Column(Date, nullable=False, default=datetime.utcnow().date)
    descricao = Column(Text, nullable=False)
    valor = Column(Float, nullable=False)
    tipo = Column(String(10))  # 'entrada' | 'saida'
    categoria_id = Column(Integer, ForeignKey('financeiro_categorias.id'))
    centro_custo_id = Column(Integer, ForeignKey('financeiro_centros_custo.id'))
    comprovante_path = Column(String(500))
    created_at = Column(DateTime, default=datetime.utcnow)


class FinanceiroHorasTrabalhadas(Base):
    """Registro de horas trabalhadas por tarefa"""
    __tablename__ = 'financeiro_horas_trabalhadas'
    id = Column(Integer, primary_key=True, autoincrement=True)
    data = Column(Date, nullable=False, default=datetime.utcnow().date)
    tarefa = Column(Text, nullable=False)
    horas = Column(Float, nullable=False)
    custo_hora = Column(Float)
    custo_total = Column(Float)  # horas * custo_hora
    created_at = Column(DateTime, default=datetime.utcnow)


class FinanceiroParametro(Base):
    """Parâmetros globais do módulo financeiro"""
    __tablename__ = 'financeiro_parametros'
    id = Column(Integer, primary_key=True, autoincrement=True)
    chave = Column(String(100), unique=True, nullable=False)
    valor = Column(Float, nullable=False)
    descricao = Column(String(255))
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class FinanceiroAuditLog(Base):
    """Trilha de auditoria — todas alterações em tabelas financeiras"""
    __tablename__ = 'financeiro_audit_log'
    id = Column(Integer, primary_key=True, autoincrement=True)
    tabela = Column(String(100), nullable=False)
    registro_id = Column(Integer)
    acao = Column(String(10), nullable=False)  # 'INSERT' | 'UPDATE' | 'DELETE'
    campo = Column(String(100))
    valor_anterior = Column(Text)
    valor_novo = Column(Text)
    usuario = Column(String(50), default='sistema')
    created_at = Column(DateTime, default=datetime.utcnow)


# ============================================================================
# TABELAS PÓS-VENDA SHOPEE (2026-06-08)
# ============================================================================

class ShopeeOrderTracking(Base):
    __tablename__ = 'shopee_orders_tracking'
    order_sn = Column(String(50), primary_key=True)
    buyer_user_id = Column(Integer)
    buyer_username = Column(String(100))
    order_status = Column(String(50))
    cidade_destino = Column(String(100))
    tracking_info = Column(JSON, default=list)
    notifications_sent = Column(JSON, default=lambda: {"boas_vindas": False, "postagem": False, "ultima_milha": False})
    criado_em = Column(DateTime, default=datetime.utcnow)
    atualizado_em = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

def upsert_shopee_order_tracking(db, order_sn, buyer_user_id=None, buyer_username=None, order_status=None, cidade_destino=None, tracking_info=None, notifications_sent=None):
    tracking = db.query(ShopeeOrderTracking).filter(ShopeeOrderTracking.order_sn == order_sn).first()
    if not tracking:
        tracking = ShopeeOrderTracking(order_sn=order_sn)
        db.add(tracking)

    if buyer_user_id is not None: tracking.buyer_user_id = buyer_user_id
    if buyer_username is not None: tracking.buyer_username = buyer_username
    if order_status is not None: tracking.order_status = order_status
    if cidade_destino is not None: tracking.cidade_destino = cidade_destino
    if tracking_info is not None: tracking.tracking_info = tracking_info

    if notifications_sent is not None:
        current_notif = tracking.notifications_sent or {"boas_vindas": False, "postagem": False, "ultima_milha": False}
        current_notif.update(notifications_sent)
        tracking.notifications_sent = current_notif

    db.commit()
    return tracking


# ============================================================================
# TABELAS INSTAGRAM & GROWTH (2026-06-08)
# ============================================================================

class MaiteIGLead(Base):
    __tablename__ = 'maite_ig_leads'
    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    ig_username = Column(String(100), nullable=False)
    ig_user_id = Column(String(100), unique=True)
    intencao = Column(String(200))
    origem_post_id = Column(String(100))
    status_funil = Column(String(50), default='novo_lead')
    dados_extras = Column(JSON, default=dict)
    criado_em = Column(DateTime, default=datetime.utcnow)
    atualizado_em = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

class IGCampaignMetric(Base):
    __tablename__ = 'ig_campaign_metrics'
    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    campaign_id = Column(String(100), unique=True, nullable=False)
    campaign_name = Column(String(200), nullable=False)
    canva_design_id = Column(String(100))
    impressions = Column(Integer, default=0)
    clicks = Column(Integer, default=0)
    spend = Column(Float, default=0.0)
    leads_generated = Column(Integer, default=0)
    criado_em = Column(DateTime, default=datetime.utcnow)
    atualizado_em = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

def upsert_ig_lead(db, ig_user_id, ig_username, intencao=None, origem_post_id=None, status_funil='novo_lead', dados_extras=None):
    lead = db.query(MaiteIGLead).filter(MaiteIGLead.ig_user_id == ig_user_id).first()
    if not lead:
        lead = MaiteIGLead(ig_user_id=ig_user_id, ig_username=ig_username)
        db.add(lead)

    if intencao: lead.intencao = intencao
    if origem_post_id: lead.origem_post_id = origem_post_id
    if status_funil: lead.status_funil = status_funil
    if dados_extras: lead.dados_extras = dados_extras
    db.commit()
    return lead

def upsert_ig_campaign_metric(db, campaign_id, campaign_name, canva_design_id=None, impressions=0, clicks=0, spend=0.0, leads_generated=0):
    metric = db.query(IGCampaignMetric).filter(IGCampaignMetric.campaign_id == campaign_id).first()
    if not metric:
        metric = IGCampaignMetric(campaign_id=campaign_id, campaign_name=campaign_name)
        db.add(metric)

    if canva_design_id: metric.canva_design_id = canva_design_id
    metric.impressions = impressions
    metric.clicks = clicks
    metric.spend = spend
    metric.leads_generated = leads_generated
    db.commit()
    return metric


DB_URL = os.getenv("DATABASE_URL")
LOCAL_DB_URL = "sqlite:///" + os.path.abspath(os.path.join(os.path.dirname(__file__), "local_db", "erp_jf_v2.db")).replace("\\", "/")

engine = None
SessionLocal = None

def inicializar_banco():
    global engine, SessionLocal
    usar_local = True
    if DB_URL:
        try:
            from sqlalchemy import text
            # Tenta criar o engine Postgres com um timeout curto de conexao (3 segundos) e pre-ping ativo para evitar conexoes mortas
            temp_engine = create_engine(
                DB_URL,
                connect_args={"connect_timeout": 3},
                pool_pre_ping=True,
                pool_recycle=1800,
                echo=False
            )
            with temp_engine.connect() as conn:
                conn.execute(text("SELECT 1"))
            engine = temp_engine
            usar_local = False
            print("[INFO]: Conectado ao PostgreSQL (Supabase) com sucesso.")
        except Exception as e:
            print(f"[AVISO]: Conexao com Supabase (PostgreSQL) falhou. Fazendo fallback para o SQLite local...")

    if usar_local:
        engine = create_engine(LOCAL_DB_URL, echo=False)
        @event.listens_for(engine, "connect")
        def set_sqlite_pragma(dbapi_connection, connection_record):
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()
        print(f"[INFO]: Conectado ao SQLite local em '{LOCAL_DB_URL}'.")

    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

inicializar_banco()

@contextmanager
def get_db_session():
    db = SessionLocal()
    try:
        yield db
        db.commit()
    except Exception as e:
        db.rollback()
        raise
    finally:
        db.close()
