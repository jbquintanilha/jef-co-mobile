import streamlit as st
import pandas as pd
import os
import re
from datetime import datetime
import pytz

from core_sync import carregar_banco_local, sincronizar_nuvem_para_local

st.set_page_config(page_title="J&F Co. Mobile", layout="centered", page_icon="📱")

# ==========================================
# 🔒 CONFIGURAÇÕES E FORÇA BRUTA
# ==========================================
SENHA_MESTRA = "JF2026"
LINK_FORMULARIO = "https://docs.google.com/forms/d/e/1FAIpQLSf3B1Kh9B-MPhT4nyCaaL64iSV2mk66I2Trdbc1v21aVCoc0A/viewform?usp=header"
PASTA_THUMBS_NUVEM = "thumbs" # A pasta que você vai criar no GitHub

def limpar_codigo(texto):
    if not texto: return ""
    return re.sub(r'[^A-Z0-9]', '', str(texto).upper())

def pegar_data_hora():
    fuso = pytz.timezone('America/Sao_Paulo')
    agora = datetime.now(fuso)
    return agora.strftime("%d/%m/%Y às %H:%M")

def tela_login():
    st.markdown("<br><br>", unsafe_allow_html=True)
    st.markdown("<h2 style='text-align: center;'>🔒 Acesso Restrito J&F Co.</h2>", unsafe_allow_html=True)
    st.markdown("<p style='text-align: center;'>Insira a credencial para acessar a vitrine.</p>", unsafe_allow_html=True)
    
    col1, col2, col3 = st.columns([1, 2, 1])
    with col2:
        senha_digitada = st.text_input("Senha:", type="password", label_visibility="collapsed", placeholder="Digite a senha...")
        if st.button("ENTRAR", use_container_width=True, type="primary"):
            if senha_digitada == SENHA_MESTRA:
                st.session_state['autenticado'] = True
                st.rerun()
            else:
                st.error("❌ Credencial inválida.")

def main():
    if 'autenticado' not in st.session_state: st.session_state['autenticado'] = False
    if not st.session_state['autenticado']:
        tela_login()
        return

    if 'tela_atual' not in st.session_state: st.session_state['tela_atual'] = 'vitrine'
    if 'produto_id' not in st.session_state: st.session_state['produto_id'] = None

    with st.sidebar:
        st.markdown("### 📱 J&F Co. Mobile")
        st.link_button("➕ Cadastrar Novo Item", LINK_FORMULARIO, use_container_width=True)
        st.markdown("---")
        
        # O botão agora atualiza os textos/estoque e crava a hora
        if st.button("🔄 Sincronizar Estoque/Textos", use_container_width=True, type="primary"):
            with st.spinner("Atualizando dados da planilha..."):
                sincronizar_nuvem_para_local()
                st.session_state['ultima_atualizacao'] = pegar_data_hora()
            st.rerun()
            
        if 'ultima_atualizacao' in st.session_state:
            st.caption(f"⏱️ **Última Att:** {st.session_state['ultima_atualizacao']}")
        else:
            st.caption("⏱️ **Última Att:** Pendente (Clique Atualizar)")
            
        st.markdown("---")
        if st.button("Sair (Logout)", use_container_width=True):
            st.session_state['autenticado'] = False
            st.rerun()

    df_pai, df_sku, _, df_form = carregar_banco_local()
    if df_pai.empty:
        st.warning("Banco vazio. Sincronize os dados no menu lateral.")
        return

    # ============================================================================
    # TELA 1: VITRINE (LEITURA DIRETA DO GITHUB - ZERO TRAVAMENTOS)
    # ============================================================================
    if st.session_state['tela_atual'] == 'vitrine':
        st.title("📱 Catálogo J&F")
        busca = st.text_input("🔍 Buscar Referência:", placeholder="Ex: 1112")
        
        df_f = df_pai[df_pai.astype(str).apply(lambda x: x.str.contains(busca, case=False, na=False)).any(axis=1)] if busca else df_pai.copy()
        
        cols = st.columns(2)
        for i, row in df_f.iterrows():
            with cols[i % 2]:
                with st.container(border=True):
                    ref_limpa = str(row.get('REF')).upper().strip()
                    
                    # Procura a foto na pasta 'thumbs' dentro do próprio GitHub
                    caminho_foto = f"{PASTA_THUMBS_NUVEM}/{ref_limpa}_thumb.jpg"
                    
                    if os.path.exists(caminho_foto): 
                        st.image(caminho_foto, use_container_width=True)
                    else: 
                        st.info("📷 S/ Foto")
                    
                    st.markdown(f"**REF: {row.get('REF')}**")
                    if st.button("Ver Ficha", key=f"btn_{i}_{row.get('ID_PAI')}", use_container_width=True):
                        st.session_state['produto_id'] = row.get('ID_PAI')
                        st.session_state['tela_atual'] = 'detalhes'
                        st.rerun()

    # ============================================================================
    # TELA 2: FICHA TÉCNICA E DADOS DA SHOPEE
    # ============================================================================
    elif st.session_state['tela_atual'] == 'detalhes':
        id_sel = st.session_state['produto_id']
        produto = df_pai[df_pai['ID_PAI'] == id_sel].iloc[0]
        skus = df_sku[df_sku['ID_PAI'] == id_sel]
        
        linha_form = df_form[df_form['Código / Referência na Etiqueta'].astype(str) == str(produto.get('REF'))]
        linha_form = linha_form.iloc[0] if not linha_form.empty else pd.Series()

        if st.button("⬅️ VOLTAR"):
            st.session_state['tela_atual'] = 'vitrine'
            st.rerun()
            
        st.subheader(f"📦 REF: {produto.get('REF')}")
        
        # FOTO ESTÁTICA
        ref_limpa = str(produto.get('REF')).upper().strip()
        caminho_foto_ficha = f"{PASTA_THUMBS_NUVEM}/{ref_limpa}_thumb.jpg"
        if os.path.exists(caminho_foto_ficha): 
            st.image(caminho_foto_ficha, use_container_width=True)
        
        # BOTÃO PARA O DRIVE (ABRIR NATIVO)
        id_drive_capa_ficha = str(produto.get('FOTO_CAPA_ID')).strip()
        if id_drive_capa_ficha and id_drive_capa_ficha != "nan":
            link_drive_ficha = f"https://drive.google.com/file/d/{id_drive_capa_ficha}/view"
            st.link_button("🖼️ Abrir Foto/Pasta Original no Drive", link_drive_ficha, use_container_width=True)
            
        st.markdown("---")
        st.markdown("#### 📊 Grade de Estoque")
        st.dataframe(skus[['TAMANHO', 'COR', 'ESTOQUE']], hide_index=True, use_container_width=True)
        
        st.markdown("---")
        st.markdown("#### 📝 Resumo Técnico (Padrão Anúncio)")
        
        # GERA O BLOCO TÉCNICO ORGANIZADO
        resumo_texto = ""
        if not linha_form.empty:
            palavras_ig = ['email', 'e-mail', 'foto', 'imagem', 'link', 'carimbo']
            for col, val in linha_form.items():
                if any(ig in col.lower() for ig in palavras_ig): continue
                val_str = str(val).strip()
                if pd.notna(val) and val_str != "" and not val_str.startswith("http"):
                    resumo_texto += f"**{col}:** {val_str}\n\n"
            
            if resumo_texto:
                st.info(resumo_texto)
            else:
                st.write("Sem dados técnicos cadastrados.")

if __name__ == "__main__":
    main()
