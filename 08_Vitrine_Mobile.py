import streamlit as st
import pandas as pd
import os
import re

st.set_page_config(page_title="J&F Co. Mobile", layout="centered", page_icon="📱")

# ==========================================
# 🔒 CONFIGURAÇÕES E FILTROS DE ELITE
# ==========================================
SENHA_MESTRA = "JF2026"
LINK_FORMULARIO = "https://docs.google.com/forms/d/e/1FAIpQLSf3B1Kh9B-MPhT4nyCaaL64iSV2mk66I2Trdbc1v21aVCoc0A/viewform?usp=header"
PASTA_PACOTE = "BANCO_MOBILE"

# CAMPOS QUE VOCÊ DEFINIU COMO NECESSÁRIOS
CAMPOS_AUTORIZADOS = [
    "Fornecedor", 
    "Para qual estoque esta mercadoria vai?", 
    "Código / Referência na Etiqueta", 
    "Categoria", 
    "Material Principal", 
    "Desenho do Tecido",
    "Cor"
]

def transcrever_valor(valor):
    """Apenas transcreve o valor do banco trocando ponto por vírgula, sem 'limpar' decimais."""
    if pd.isna(valor) or str(valor).strip() == "": return "0,00"
    # Transcreve exatamente o que está lá
    return f"R$ {str(valor).replace('.', ',')}"

def carregar_banco_estatico():
    try:
        df_pai = pd.read_csv(f"{PASTA_PACOTE}/dados/df_pai.csv")
        df_sku = pd.read_csv(f"{PASTA_PACOTE}/dados/df_sku.csv")
        df_form = pd.read_csv(f"{PASTA_PACOTE}/dados/df_form.csv")
        with open(f"{PASTA_PACOTE}/dados/timestamp.txt", "r", encoding="utf-8") as f:
            ultima_att = f.read()
        if not df_pai.empty:
            df_pai = df_pai.drop_duplicates(subset=['REF'])
        return df_pai, df_sku, df_form, ultima_att
    except Exception as e:
        st.error(f"🚨 ERRO DE BANCO: {e}")
        return pd.DataFrame(), pd.DataFrame(), pd.DataFrame(), "Erro"

def main():
    if 'autenticado' not in st.session_state: st.session_state['autenticado'] = False
    if not st.session_state['autenticado']:
        st.markdown("<h2 style='text-align: center;'>🔒 J&F Master Mobile</h2>", unsafe_allow_html=True)
        senha = st.text_input("Senha:", type="password")
        if st.button("ACESSAR VITRINE", use_container_width=True, type="primary"):
            if senha == SENHA_MESTRA:
                st.session_state['autenticado'] = True
                st.rerun()
        return

    df_pai, df_sku, df_form, ultima_att = carregar_banco_estatico()
    if 'tela_atual' not in st.session_state: st.session_state['tela_atual'] = 'vitrine'

    # --- TELA 1: VITRINE ---
    if st.session_state['tela_atual'] == 'vitrine':
        st.title("📱 Vitrine Operacional")
        
        with st.sidebar:
            st.success(f"⏱️ **Última Sincro:**\n{ultima_att}")
            st.link_button("➕ Novo Cadastro", LINK_FORMULARIO, use_container_width=True)
            if st.button("Logoff"):
                st.session_state['autenticado'] = False
                st.rerun()

        busca = st.text_input("🔍 Pesquisar REF ou Categoria:")
        df_f = df_pai[df_pai['REF'].astype(str).str.contains(busca, case=False, na=False)] if busca else df_pai.copy()

        cols = st.columns(2)
        for i, row in df_f.iterrows():
            with cols[i % 2]:
                with st.container(border=True):
                    ref = str(row.get('REF')).upper()
                    caminho_foto = f"{PASTA_PACOTE}/thumbs/{ref}_thumb.jpg"
                    if os.path.exists(caminho_foto): st.image(caminho_foto)
                    else: st.info("📷 Sem foto")
                    st.markdown(f"**REF: {ref}**")
                    if st.button("Ver Ficha", key=f"v_{ref}", use_container_width=True):
                        st.session_state['produto_id'] = row.get('ID_PAI')
                        st.session_state['tela_atual'] = 'detalhes'
                        st.rerun()

    # --- TELA 2: FICHA (MESCLADA COM LOGICA DASHBOARD) ---
    elif st.session_state['tela_atual'] == 'detalhes':
        if st.button("⬅️ VOLTAR PARA VITRINE", type="primary", use_container_width=True):
            st.session_state['tela_atual'] = 'vitrine'
            st.rerun()

        id_sel = st.session_state['produto_id']
        produto = df_pai[df_pai['ID_PAI'] == id_sel].iloc[0]
        skus = df_sku[df_sku['ID_PAI'] == id_sel]
        linha_form = df_form[df_form['Código / Referência na Etiqueta'].astype(str) == str(produto.get('REF'))]
        
        st.subheader(f"📦 REF: {produto.get('REF')}")
        
        # Link para Pasta do Drive
        id_pasta = str(produto.get('PASTA_DRIVE_ID')).strip()
        if id_pasta and id_pasta != "nan":
            st.link_button("📂 Abrir Pasta de Fotos no Drive", f"https://drive.google.com/drive/folders/{id_pasta}", use_container_width=True)

        st.markdown("---")
        st.markdown("#### 📊 Grade de Estoque (Dados Dashboard)")
        
        if not skus.empty:
            df_v = skus[['TAMANHO', 'COR', 'ESTOQUE']].copy()
            # 🎨 RESGATE DE COR DO FORMULÁRIO (Igual ao seu Dashboard deveria fazer)
            if not linha_form.empty and 'Cor' in linha_form.columns:
                cor_vinda_do_form = str(linha_form.iloc[0]['Cor'])
                df_v['COR'] = df_v['COR'].astype(str).replace(['None', 'nan', 'nan', '', ' '], cor_vinda_do_form)
            
            df_v['COR'] = df_v['COR'].replace(['None', 'nan', ''], '-')
            st.dataframe(df_v, hide_index=True, use_container_width=True)

        st.markdown("---")
        st.markdown("#### 📝 Resumo Técnico")
        st.markdown(f"**SKU_PAI:** {produto.get('ID_PAI')}")
        
        if not linha_form.empty:
            linha = linha_form.iloc[0]
            # Exibe apenas os campos que você pediu
            for col in CAMPOS_AUTORIZADOS:
                if col in linha.index:
                    st.markdown(f"**{col}:** {linha[col]}")
            
            # Bloqueio de Preço Total
            st.markdown(f"**Preço Pago:** R$ 0,00")
            
            # 💰 Transcrição Real do Custo (Conforme sua ordem)
            for col in linha.index:
                if 'preço de pago' in col.lower() or 'custo unitário' in col.lower():
                    st.markdown(f"**Preço de Pago (Custo Unitário):** {transcrever_valor(linha[col])}")

        st.markdown("---")
        if st.button("⬅️ VOLTAR NO FINAL DA FICHA", use_container_width=True):
            st.session_state['tela_atual'] = 'vitrine'
            st.rerun()

if __name__ == "__main__":
    main()
