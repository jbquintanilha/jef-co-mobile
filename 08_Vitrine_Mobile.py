import streamlit as st
import pandas as pd
import os
import re
import io
from PIL import Image

from core_sync import carregar_banco_local, sincronizar_nuvem_para_local
from core_imagens import THUMBS_DIR

st.set_page_config(page_title="J&F Co. Mobile", layout="centered", page_icon="📱")

# ==========================================
# 🔒 CONFIGURAÇÕES
# ==========================================
SENHA_MESTRA = "JF2026"
LINK_FORMULARIO = "https://docs.google.com/forms/d/e/1FAIpQLSf3B1Kh9B-MPhT4nyCaaL64iSV2mk66I2Trdbc1v21aVCoc0A/viewform?usp=header"

def limpar_codigo(texto):
    if not texto: return ""
    return re.sub(r'[^A-Z0-9]', '', str(texto).upper())

# ============================================================================
# 🌐 MOTOR DE IMAGEM HÍBRIDO + RESOLVEDOR DE ATALHOS + COMPRESSOR MOBILE
# ============================================================================
@st.cache_data(show_spinner=False, ttl=3600) 
def obter_imagem_hibrida(ref_limpa, id_drive):
    caminho_local = os.path.join(THUMBS_DIR, f"{ref_limpa}_thumb.jpg")
    if os.path.exists(caminho_local): return caminho_local 
    
    if pd.notna(id_drive) and str(id_drive).strip() != "" and str(id_drive).strip() != "nan":
        try:
            from core_conexoes import conectar_google
            from googleapiclient.http import MediaIoBaseDownload
            _, drive = conectar_google()
            
            # --- 🚀 O RESOLVEDOR DE ATALHOS ---
            arquivo_info = drive.files().get(fileId=str(id_drive), fields="mimeType, shortcutDetails").execute()
            
            if arquivo_info.get('mimeType') == 'application/vnd.google-apps.shortcut':
                id_real = arquivo_info['shortcutDetails']['targetId']
            else:
                id_real = str(id_drive)
            # ----------------------------------
            
            request = drive.files().get_media(fileId=id_real)
            fh = io.BytesIO()
            downloader = MediaIoBaseDownload(fh, request)
            done = False
            while done is False:
                _, done = downloader.next_chunk()
            fh.seek(0)
            
            # COMPRESSOR DE IMAGEM PARA NÃO GASTAR O 4G
            img = Image.open(fh)
            if img.mode in ("RGBA", "P"): img = img.convert("RGB")
            img.thumbnail((400, 400), Image.Resampling.LANCZOS)
            
            buf = io.BytesIO()
            img.save(buf, format="JPEG", quality=70, optimize=True)
            return buf.getvalue() 
        except Exception as e:
            return None
    return None

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
        
        if st.button("🔄 Atualizar Estoque", use_container_width=True, type="primary"):
            with st.spinner("Sincronizando com a Nuvem..."):
                sincronizar_nuvem_para_local()
                st.cache_data.clear() # Força baixar fotos novas e ler os atalhos
            st.rerun()
            
        st.markdown("---")
        if st.button("Sair (Logout)", use_container_width=True):
            st.session_state['autenticado'] = False
            st.rerun()

    df_pai, df_sku, _, df_form = carregar_banco_local()
    if df_pai.empty:
        st.warning("Banco vazio. Atualize os dados no menu lateral.")
        return

    # ============================================================================
    # TELA 1: VITRINE (COM FOTO E BARRA DE PROGRESSO)
    # ============================================================================
    if st.session_state['tela_atual'] == 'vitrine':
        st.title("📱 Catálogo J&F")
        busca = st.text_input("🔍 Buscar Referência:", placeholder="Ex: 1112")
        
        df_f = df_pai[df_pai.astype(str).apply(lambda x: x.str.contains(busca, case=False, na=False)).any(axis=1)] if busca else df_pai.copy()
        total_itens = len(df_f)
        
        # BARRA DE PROGRESSO REAL
        if total_itens > 0:
            barra_progresso = st.progress(0, text="Preparando vitrine...")
            
        cols = st.columns(2)
        for i, row in df_f.iterrows():
            if total_itens > 0:
                progresso_atual = (i + 1) / total_itens
                barra_progresso.progress(progresso_atual, text=f"Carregando imagem {i+1} de {total_itens}...")

            with cols[i % 2]:
                with st.container(border=True):
                    ref_limpa = str(row.get('REF')).upper().strip()
                    id_drive_capa = str(row.get('FOTO_CAPA_ID')).strip()
                    
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
                        
        if total_itens > 0:
            barra_progresso.empty() # Remove a barra ao terminar

    # ============================================================================
    # TELA 2: FICHA TÉCNICA (GRADE, CARACTERÍSTICAS E LINK EXTERNO)
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
        
        # FOTO DE CAPA NA FICHA
        id_drive_capa_ficha = str(produto.get('FOTO_CAPA_ID')).strip()
        img_data_ficha = obter_imagem_hibrida(limpar_codigo(produto.get('REF')), id_drive_capa_ficha)
        if img_data_ficha: 
            st.image(img_data_ficha, use_container_width=True)
        
        # BOTÃO PARA FOTOS PESADAS NO DRIVE
        if id_drive_capa_ficha and id_drive_capa_ficha != "nan":
            # Aqui no botão da ficha técnica a gente aponta para a imagem, mesmo se for atalho, o Drive sabe abrir!
            link_drive_ficha = f"https://drive.google.com/file/d/{id_drive_capa_ficha}/view"
            st.link_button("🖼️ Ver Fotos em Alta Resolução (Drive)", link_drive_ficha, use_container_width=True)
            
        st.markdown("---")
        st.markdown("#### 📊 Estoque e Grade")
        st.dataframe(skus[['TAMANHO', 'COR', 'ESTOQUE']], hide_index=True, use_container_width=True)
        
        st.markdown("---")
        st.markdown("#### 📋 Características do Item")
        if not linha_form.empty:
            palavras_ig = ['email', 'e-mail', 'foto', 'imagem', 'link', 'carimbo']
            for col, val in linha_form.items():
                if any(ig in col.lower() for ig in palavras_ig): continue
                val_str = str(val).strip()
                if pd.notna(val) and val_str != "" and not val_str.startswith("http"):
                    st.markdown(f"**{col}:** {val_str}")

if __name__ == "__main__":
    main()
