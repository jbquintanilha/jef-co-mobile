import streamlit as st
import pandas as pd
import os
import re

from core_sync import carregar_banco_local, sincronizar_nuvem_para_local
from core_imagens import THUMBS_DIR

# Configuração focada em Celular (Mobile First)
st.set_page_config(page_title="J&F Co. Mobile", layout="centered", page_icon="📱")

# ==========================================
# 🔒 CADEADO DIGITAL (Defina sua senha aqui)
# ==========================================
SENHA_MESTRA = "JF2026"

def limpar_codigo(texto):
    if not texto: return ""
    return re.sub(r'[^A-Z0-9]', '', str(texto).upper())

def tela_login():
    """Renderiza a barreira de segurança."""
    st.markdown("<br><br>", unsafe_allow_html=True)
    st.markdown("<h2 style='text-align: center;'>🔒 Acesso Restrito J&F Co.</h2>", unsafe_allow_html=True)
    st.markdown("<p style='text-align: center;'>Insira a credencial para acessar a vitrine operacional.</p>", unsafe_allow_html=True)
    
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
    # Verifica a Barreira de Segurança
    if 'autenticado' not in st.session_state:
        st.session_state['autenticado'] = False

    if not st.session_state['autenticado']:
        tela_login()
        return  # Trava a execução do resto do código aqui se não tiver senha

    # ==========================================
    # 🔓 ÁREA LOGADA (SÓ ENTRA COM A SENHA)
    # ==========================================
    if 'tela_atual' not in st.session_state: st.session_state['tela_atual'] = 'vitrine'
    if 'produto_id' not in st.session_state: st.session_state['produto_id'] = None

    # Menu lateral simplificado
    with st.sidebar:
        st.markdown("### 📱 J&F Co. Mobile")
        if st.button("🔄 Atualizar Dados", use_container_width=True, type="primary"):
            with st.spinner("Baixando novidades da nuvem..."):
                sincronizar_nuvem_para_local()
            st.rerun()
            
        st.markdown("---")
        st.caption("Acesso Logado: Consulta de Estoque e Ficha Técnica.")
        if st.button("Sair (Logout)", use_container_width=True):
            st.session_state['autenticado'] = False
            st.rerun()

    df_pai, df_sku, _, df_form = carregar_banco_local()
    if df_pai.empty:
        st.warning("Banco vazio. Atualize os dados no menu lateral.")
        return

    # --- TELA 1: VITRINE ---
    if st.session_state['tela_atual'] == 'vitrine':
        st.title("📱 Catálogo J&F")
        busca = st.text_input("🔍 Buscar Referência:", placeholder="Ex: 1112")
        
        df_f = df_pai[df_pai.astype(str).apply(lambda x: x.str.contains(busca, case=False, na=False)).any(axis=1)] if busca else df_pai.copy()
        
        cols = st.columns(2)
        for i, row in df_f.iterrows():
            with cols[i % 2]:
                with st.container(border=True):
                    ref_limpa = str(row.get('REF')).upper().strip()
                    thumb_path = os.path.join(THUMBS_DIR, f"{ref_limpa}_thumb.jpg")
                    
                    if os.path.exists(thumb_path): st.image(thumb_path, use_container_width=True)
                    else: st.info("📷 S/ Foto")
                    
                    st.markdown(f"**REF: {row.get('REF')}**")
                    if st.button("Ver Ficha", key=f"btn_{i}_{row.get('ID_PAI')}", use_container_width=True):
                        st.session_state['produto_id'] = row.get('ID_PAI')
                        st.session_state['tela_atual'] = 'detalhes'
                        st.rerun()

    # --- TELA 2: FICHA TÉCNICA ---
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
        st.markdown("---")

        st.markdown("#### 📊 Estoque")
        st.dataframe(skus[['TAMANHO', 'COR', 'ESTOQUE']], hide_index=True, use_container_width=True)
        
        st.markdown("---")
        st.markdown("#### 📋 Ficha Técnica")
        if not linha_form.empty:
            palavras_ig = ['email', 'e-mail', 'foto', 'imagem', 'link', 'carimbo']
            for col, val in linha_form.items():
                if any(ig in col.lower() for ig in palavras_ig): continue
                val_str = str(val).strip()
                if pd.notna(val) and val_str != "" and not val_str.startswith("http"):
                    st.markdown(f"**{col}:** {val_str}")

if __name__ == "__main__":
    main()
