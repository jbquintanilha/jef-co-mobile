import streamlit as st
import pandas as pd
import os
import re

st.set_page_config(page_title="J&F Co. Mobile", layout="centered", page_icon="📱")

# ==========================================
# 🔒 CONFIGURAÇÕES E TRAVAS
# ==========================================
SENHA_MESTRA = "JF2026"
LINK_FORMULARIO = "https://docs.google.com/forms/d/e/1FAIpQLSf3B1Kh9B-MPhT4nyCaaL64iSV2mk66I2Trdbc1v21aVCoc0A/viewform?usp=header"
PASTA_PACOTE = "BANCO_MOBILE"

# Campos autorizados para a vendedora
CAMPOS_PERMITIDOS = [
    "Fornecedor", 
    "Para qual estoque esta mercadoria vai?", 
    "Código / Referência na Etiqueta", 
    "Categoria", 
    "Material Principal", 
    "Desenho do Tecido",
    "Cor"
]

def formatar_moeda(valor):
    try:
        # Limpa o valor para garantir que 7.9 não vire 79.00
        v_limpo = str(valor).replace('R$', '').replace(' ', '').replace(',', '.').strip()
        v = float(v_limpo)
        # Formata para padrão brasileiro 7,90
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
        st.error(f"🚨 ERRO DE DADOS: {e}")
        return pd.DataFrame(), pd.DataFrame(), pd.DataFrame(), "Erro"

def main():
    if 'autenticado' not in st.session_state: st.session_state['autenticado'] = False
    if not st.session_state['autenticado']:
        st.markdown("<h2 style='text-align: center;'>🔒 J&F Co. Mobile</h2>", unsafe_allow_html=True)
        senha = st.text_input("Senha:", type="password")
        if st.button("ENTRAR", use_container_width=True, type="primary"):
            if senha == SENHA_MESTRA:
                st.session_state['autenticado'] = True
                st.rerun()
        return

    df_pai, df_sku, df_form, ultima_att = carregar_banco_estatico()
    if 'tela_atual' not in st.session_state: st.session_state['tela_atual'] = 'vitrine'

    # --- TELA 1: VITRINE (BUSCA SUPER INTELIGENTE) ---
    if st.session_state['tela_atual'] == 'vitrine':
        st.title("📱 Catálogo J&F")
        
        with st.sidebar:
            st.success(f"⏱️ **Dados de:**\n{ultima_att}")
            st.link_button("➕ Novo Cadastro", LINK_FORMULARIO, use_container_width=True)
            if st.button("Sair"):
                st.session_state['autenticado'] = False
                st.rerun()

        busca = st.text_input("🔍 Buscar:", placeholder="Ref, Categoria, Cor, Material...")
        
        df_f = df_pai.copy()
        if busca:
            termo = busca.lower().strip()
            # Busca na REF do PAI
            mask_pai = df_pai['REF'].astype(str).str.lower().str.contains(termo, na=False)
            
            # Busca no FORMULÁRIO (Fornecedor, Cor, etc)
            refs_form = []
            if not df_form.empty:
                mask_form = df_form.astype(str).apply(lambda x: x.str.lower().str.contains(termo)).any(axis=1)
                refs_form = df_form[mask_form]['Código / Referência na Etiqueta'].astype(str).unique()
            
            df_f = df_pai[mask_pai | df_pai['REF'].astype(str).isin(refs_form)]

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

    # --- TELA 2: DETALHES (COM RESGATE DE COR E TRAVA DE PREÇO) ---
    elif st.session_state['tela_atual'] == 'detalhes':
        if st.button("⬅️ VOLTAR PARA VITRINE", type="primary", use_container_width=True, key="voltar_det"):
            st.session_state['tela_atual'] = 'vitrine'
            st.rerun()

        id_sel = st.session_state['produto_id']
        produto = df_pai[df_pai['ID_PAI'] == id_sel].iloc[0]
        skus = df_sku[df_sku['ID_PAI'] == id_sel]
        linha_form = df_form[df_form['Código / Referência na Etiqueta'].astype(str) == str(produto.get('REF'))]
        
        st.subheader(f"📦 REF: {produto.get('REF')}")
        
        # Link do Drive
        id_pasta = str(produto.get('PASTA_DRIVE_ID')).strip()
        if id_pasta and id_pasta != "nan":
            st.link_button("📂 Abrir Pasta de Fotos no Drive", f"https://drive.google.com/drive/folders/{id_pasta}", use_container_width=True)

        st.markdown("---")
        st.markdown("#### 📊 Grade de Estoque")
        if not skus.empty:
            df_v = skus[['TAMANHO', 'COR', 'ESTOQUE']].copy()
            
            # 🎨 MOTOR DE RESGATE DE COR (Se o SKU estiver sem cor, busca no Form)
            if not linha_form.empty and 'Cor' in linha_form.columns:
                cor_resgate = str(linha_form.iloc[0]['Cor'])
                df_v['COR'] = df_v['COR'].astype(str).replace(['None', 'nan', 'NaN', '', ' '], cor_resgate)
            
            df_v['COR'] = df_v['COR'].replace(['None', 'nan', ''], '-')
            st.dataframe(df_v, hide_index=True, use_container_width=True)
        else:
            st.write("Sem grade disponível.")

        st.markdown("---")
        st.markdown("#### 📝 Resumo Técnico")
        st.markdown(f"**SKU_PAI:** {produto.get('ID_PAI')}")
        
        if not linha_form.empty:
            linha = linha_form.iloc[0]
            for col in CAMPOS_PERMITIDOS:
                if col in linha.index and pd.notna(linha[col]):
                    st.markdown(f"**{col}:** {linha[col]}")
            
            # BLOCO FINANCEIRO CORRIGIDO
            st.markdown("---")
            st.markdown(f"**Preço Pago:** R$ 0,00")
            
            for col in linha.index:
                if 'preço de pago' in col.lower() or 'custo unitário' in col.lower():
                    # Pega o valor e garante a formatação
                    v_original = linha[col]
                    # Se o valor na planilha for 79 e deveria ser 7.9, 
                    # você precisará corrigir na PLANILHA. 
                    # O Python aqui vai mostrar EXATAMENTE o que está na célula.
                    st.markdown(f"**Preço de Pago (Custo Unitário):** {formatar_moeda(v_original)}")

        st.button("⬅️ VOLTAR NO FINAL", use_container_width=True, key="voltar_fim", on_click=lambda: st.session_state.update({"tela_atual": "vitrine"}))

if __name__ == "__main__":
    main()
