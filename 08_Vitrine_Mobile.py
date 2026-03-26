import streamlit as st
import pandas as pd
import os
import re
import io

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

# ============================================================================
# 🌐 MOTOR DE IMAGEM HÍBRIDO (A GRANDE SACADA DO CEO)
# ============================================================================
@st.cache_data(show_spinner=False, ttl=3600) # Guarda na memória por 1 hora para não gastar dados
def obter_imagem_hibrida(ref_limpa, id_drive):
    # 1. MODO OFFLINE (Rápido - Computador Local)
    caminho_local = os.path.join(THUMBS_DIR, f"{ref_limpa}_thumb.jpg")
    if os.path.exists(caminho_local):
        return caminho_local 
    
    # 2. MODO ONLINE (Nuvem/Celular - Busca direto do Google Drive via API)
    if pd.notna(id_drive) and str(id_drive).strip() != "":
        try:
            from core_conexoes import conectar_google
            from googleapiclient.http import MediaIoBaseDownload
            _, drive = conectar_google()
            request = drive.files().get_media(fileId=str(id_drive))
            fh = io.BytesIO()
            downloader = MediaIoBaseDownload(fh, request)
            done = False
            while done is False:
                _, done = downloader.next_chunk()
            fh.seek(0)
            return fh.read() # Retorna a imagem pura para o celular
        except Exception as e:
            return None
    return None

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
    if 'autenticado' not in st.session_state:
        st.session_state['autenticado'] = False

    if not st.session_state['autenticado']:
        tela_login()
        return

    # ==========================================
    # 🔓 ÁREA LOGADA
    # ==========================================
    if 'tela_atual' not in st.session_state: st.session_state['tela_atual'] = 'vitrine'
    if 'produto_id' not in st.session_state: st.session_state['produto_id'] = None

    with st.sidebar:
        st.markdown("### 📱 J&F Co. Mobile")
        if st.button("🔄 Atualizar Dados", use_container_width=True, type="primary"):
            with st.spinner("Baixando novidades do Drive..."):
                sincronizar_nuvem_para_local()
                st.cache_data.clear() # Limpa o cache para baixar fotos novas se houver
            st.rerun()
            
        st.markdown("---")
        st.caption("Acesso Logado: Consulta Híbrida de Estoque e Fotos.")
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
                    id_drive_capa = row.get('FOTO_CAPA_ID')
                    
                    # Chama o motor híbrido para resolver se usa HD local ou Nuvem
                    img_data = obter_imagem_hibrida(ref_limpa, id_drive_capa)
                    
                    if img_data: 
                        st.image(img_data, use_container_width=True)
                    else: 
                        st.info("📷 S/ Foto")
                    
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
        
        # Mostra a foto grande na ficha técnica também (usando o motor híbrido)
        img_data_ficha = obter_imagem_hibrida(limpar_codigo(produto.get('REF')), produto.get('FOTO_CAPA_ID'))
        if img_data_ficha:
            st.image(img_data_ficha, use_container_width=True)
            
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
