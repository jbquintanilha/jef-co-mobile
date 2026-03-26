import streamlit as st
import pandas as pd
import os
import re

st.set_page_config(page_title="J&F Co. Mobile", layout="centered", page_icon="📱")

# ==========================================
# 🔒 CONFIGURAÇÕES E FILTROS DE EXIBIÇÃO
# ==========================================
SENHA_MESTRA = "JF2026"
LINK_FORMULARIO = "https://docs.google.com/forms/d/e/1FAIpQLSf3B1Kh9B-MPhT4nyCaaL64iSV2mk66I2Trdbc1v21aVCoc0A/viewform?usp=header"
PASTA_PACOTE = "BANCO_MOBILE"

# CAMPOS QUE VOCÊ AUTORIZOU A VENDEDORA VER (Resumo Técnico)
CAMPOS_PERMITIDOS = [
    "Fornecedor", 
    "Para qual estoque esta mercadoria vai?", 
    "Código / Referência na Etiqueta", 
    "Categoria", 
    "Material Principal", 
    "Desenho do Tecido",
    "Cor"
]

def formatar_moeda(valor, forcar_zero=False):
    if forcar_zero: return "R$ 0,00"
    try:
        # Garante que 7.9 ou 7,90 vire R$ 7,90 corretamente
        v_str = str(valor).replace('R$', '').replace(' ', '').replace(',', '.').strip()
        v = float(v_str)
        return f"R$ {v:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    except:
        return "R$ 0,00"

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
        st.error(f"🚨 ERRO DE LEITURA: {e}")
        return pd.DataFrame(), pd.DataFrame(), pd.DataFrame(), "Erro"

def main():
    if 'autenticado' not in st.session_state: st.session_state['autenticado'] = False
    if not st.session_state['autenticado']:
        st.markdown("<h2 style='text-align: center;'>🔒 J&F Co.</h2>", unsafe_allow_html=True)
        senha = st.text_input("Senha:", type="password")
        if st.button("ENTRAR", use_container_width=True, type="primary"):
            if senha == SENHA_MESTRA:
                st.session_state['autenticado'] = True
                st.rerun()
        return

    df_pai, df_sku, df_form, ultima_att = carregar_banco_estatico()

    if 'tela_atual' not in st.session_state: st.session_state['tela_atual'] = 'vitrine'
    if 'produto_id' not in st.session_state: st.session_state['produto_id'] = None

    # --- TELA 1: VITRINE (BUSCA SUPER INTELIGENTE) ---
    if st.session_state['tela_atual'] == 'vitrine':
        st.title("📱 Catálogo J&F")
        busca = st.text_input("🔍 Buscar:", placeholder="Ref, Categoria, Material...")
        
        df_f = df_pai.copy()
        if busca:
            termo = busca.lower().strip()
            # Busca na referência e cruza com os dados do formulário
            mask_pai = df_pai['REF'].astype(str).str.lower().str.contains(termo, na=False)
            df_f = df_pai[mask_pai]

        cols = st.columns(2)
        for i, row in df_f.iterrows():
            with cols[i % 2]:
                with st.container(border=True):
                    ref = str(row.get('REF')).upper()
                    caminho_foto = f"{PASTA_PACOTE}/thumbs/{ref}_thumb.jpg"
                    if os.path.exists(caminho_foto): st.image(caminho_foto)
                    else: st.info("📷 S/ Foto")
                    st.markdown(f"**REF: {ref}**")
                    if st.button("Ver Detalhes", key=f"btn_{ref}", use_container_width=True):
                        st.session_state['produto_id'] = row.get('ID_PAI')
                        st.session_state['tela_atual'] = 'detalhes'
                        st.rerun()

    # --- TELA 2: FICHA TÉCNICA (DETALHES) ---
    elif st.session_state['tela_atual'] == 'detalhes':
        # Botão Voltar Superior
        if st.button("⬅️ VOLTAR PARA VITRINE", type="primary", use_container_width=True, key="topo"):
            st.session_state['tela_atual'] = 'vitrine'
            st.rerun()

        id_sel = st.session_state['produto_id']
        produto = df_pai[df_pai['ID_PAI'] == id_sel].iloc[0]
        skus = df_sku[df_sku['ID_PAI'] == id_sel]
        linha_form = df_form[df_form['Código / Referência na Etiqueta'].astype(str) == str(produto.get('REF'))]
        
        st.subheader(f"📦 REF: {produto.get('REF')}")
        
        # Caçador de Pastas do Drive
        id_pasta = str(produto.get('PASTA_DRIVE_ID')).strip()
        if id_pasta and id_pasta != "nan":
            st.link_button("📂 Abrir Pasta Completa no Drive", f"https://drive.google.com/drive/folders/{id_pasta}", use_container_width=True)

        st.markdown("---")
        st.markdown("#### 📊 Grade de Estoque")
        if not skus.empty:
            df_v = skus[['TAMANHO', 'COR', 'ESTOQUE']].copy()
            
            # 🎨 LÓGICA DE RECUPERAÇÃO DA COR
            if not linha_form.empty and 'Cor' in linha_form.columns:
                cor_resgate = str(linha_form.iloc[0]['Cor'])
                df_v['COR'] = df_v['COR'].astype(str).replace(['None', 'nan', 'nan', ''], cor_resgate)
            
            df_v['COR'] = df_v['COR'].replace(['None', 'nan', ''], '-')
            st.dataframe(df_v, hide_index=True, use_container_width=True)

        st.markdown("---")
        st.markdown("#### 📝 Resumo Técnico")
        st.markdown(f"**SKU_PAI:** {produto.get('ID_PAI')}")
        
        if not linha_form.empty:
            linha = linha_form.iloc[0]
            # Filtro de campos permitidos
            for col in CAMPOS_PERMITIDOS:
                if col in linha.index and pd.notna(linha[col]):
                    st.markdown(f"**{col}:** {linha[col]}")
            
            st.markdown("---")
            # Bloqueio de Preço Pago
            st.markdown(f"**Preço Pago:** R$ 0,00")
            
            # Custo Unitário com Formatação Brasileira (7,90)
            for col in linha.index:
                if 'preço de pago' in col.lower() or 'custo unitário' in col.lower():
                    custo = formatar_moeda(linha[col])
                    st.markdown(f"**Preço de Pago (Custo Unitário):** {custo}")

        # Botão Voltar Inferior
        st.markdown("---")
        if st.button("⬅️ VOLTAR PARA VITRINE", use_container_width=True, key="base"):
            st.session_state['tela_atual'] = 'vitrine'
            st.rerun()

if __name__ == "__main__":
    main()
