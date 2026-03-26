import streamlit as st
import pandas as pd
import os
import re

st.set_page_config(page_title="J&F Co. Mobile", layout="centered", page_icon="📱")

# ==========================================
# 🔒 CONFIGURAÇÕES OFFLINE
# ==========================================
SENHA_MESTRA = "JF2026"
LINK_FORMULARIO = "https://docs.google.com/forms/d/e/1FAIpQLSf3B1Kh9B-MPhT4nyCaaL64iSV2mk66I2Trdbc1v21aVCoc0A/viewform?usp=header"
PASTA_PACOTE = "BANCO_MOBILE"

def limpar_codigo(texto):
    if not texto: return ""
    return re.sub(r'[^A-Z0-9]', '', str(texto).upper())

# 💰 FORMATADOR DE MOEDA
def formatar_moeda(valor):
    try:
        v_str = str(valor).replace(',', '.').strip()
        v_str = re.sub(r'[^\d.]', '', v_str)
        v = float(v_str)
        return f"R$ {v:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    except:
        return str(valor)

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
        st.error(f"🚨 ERRO DE LEITURA (Verifique se a pasta {PASTA_PACOTE} está no GitHub): {e}")
        return pd.DataFrame(), pd.DataFrame(), pd.DataFrame(), "Erro"

def tela_login():
    st.markdown("<br><br>", unsafe_allow_html=True)
    st.markdown("<h2 style='text-align: center;'>🔒 Acesso Restrito J&F Co.</h2>", unsafe_allow_html=True)
    col1, col2, col3 = st.columns([1, 2, 1])
    with col2:
        senha = st.text_input("Senha:", type="password", label_visibility="collapsed")
        if st.button("ENTRAR", use_container_width=True, type="primary"):
            if senha == SENHA_MESTRA:
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

    df_pai, df_sku, df_form, ultima_att = carregar_banco_estatico()

    with st.sidebar:
        st.markdown("### 📱 J&F Co. Mobile")
        st.link_button("➕ Cadastrar Novo Item", LINK_FORMULARIO, use_container_width=True)
        st.markdown("---")
        
        if ultima_att != "Erro":
            st.success(f"⏱️ **Dados de:**\n{ultima_att}")
            st.caption("A atualização é feita via central no PC do CEO.")
            
        st.markdown("---")
        if st.button("Sair (Logout)", use_container_width=True):
            st.session_state['autenticado'] = False
            st.rerun()

    if df_pai.empty:
        st.warning(f"⚠️ O pacote de dados '{PASTA_PACOTE}' está incompleto ou não foi encontrado.")
        return

    # --- TELA 1: VITRINE ---
    if st.session_state['tela_atual'] == 'vitrine':
        st.title("📱 Catálogo J&F")
        
        busca = st.text_input("🔍 Buscar (Ref, Categoria, Fornecedor...):", placeholder="Ex: Leluc, Calcinha, 350832...")
        
        if busca:
            termo = busca.lower().strip()
            mask_pai = df_pai['REF'].astype(str).str.lower().str.contains(termo, na=False)
            
            if not df_form.empty:
                df_form_copy = df_form.copy()
                if ',' in termo and re.match(r'^\d+,\d{2}$', termo):
                    termo_preço = termo.replace(',', '.')
                    for col in df_form_copy.columns:
                        if 'preço' in col.lower() or 'custo' in col.lower() or 'valor' in col.lower():
                            df_form_copy[f"{col}_BUSCA_PREÇO"] = df_form_copy[col].apply(lambda x: str(x).replace(',', '.'))
                
                mask_form = df_form_copy.astype(str).apply(lambda x: x.str.lower().str.contains(termo if not ',' in termo else termo_preço)).any(axis=1)
                refs_encontradas = df_form[mask_form]['Código / Referência na Etiqueta'].astype(str).unique()
                
                df_f = df_pai[mask_pai | df_pai['REF'].astype(str).isin(refs_encontradas)]
            else:
                df_f = df_pai[mask_pai]
        else:
            df_f = df_pai.copy()
        
        cols = st.columns(2)
        for i, row in df_f.iterrows():
            with cols[i % 2]:
                with st.container(border=True):
                    ref_limpa = str(row.get('REF')).upper().strip()
                    caminho_foto = f"{PASTA_PACOTE}/thumbs/{ref_limpa}_thumb.jpg"
                    
                    if os.path.exists(caminho_foto): 
                        st.image(caminho_foto, use_container_width=True)
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

        # BOTÃO VOLTAR NO TOPO
        if st.button("⬅️ VOLTAR PARA A VITRINE", key="btn_voltar_topo", type="primary"):
            st.session_state['tela_atual'] = 'vitrine'
            st.rerun()
            
        st.subheader(f"📦 REF: {produto.get('REF')}")
        
        ref_limpa = str(produto.get('REF')).upper().strip()
        caminho_foto = f"{PASTA_PACOTE}/thumbs/{ref_limpa}_thumb.jpg"
        if os.path.exists(caminho_foto): 
            st.image(caminho_foto, use_container_width=True)
        
        link_pasta_drive = None
        
        if 'PASTA_DRIVE_ID' in produto.index and pd.notna(produto['PASTA_DRIVE_ID']):
            val_str = str(produto['PASTA_DRIVE_ID']).strip()
            if val_str and val_str != "nan":
                if "drive.google.com" in val_str:
                    link_pasta_drive = val_str
                else:
                    link_pasta_drive = f"https://drive.google.com/drive/folders/{val_str}"
        
        if not link_pasta_drive and not linha_form.empty:
            for val in linha_form.values:
                if pd.notna(val):
                    val_str = str(val).strip()
                    if "drive.google.com/drive/folders/" in val_str:
                        link_pasta_drive = val_str
                        break

        id_foto_capa = str(produto.get('FOTO_CAPA_ID')).strip()
        
        if link_pasta_drive:
            st.link_button("📂 Abrir Pasta Completa no Drive", link_pasta_drive, use_container_width=True)
        elif id_foto_capa and id_foto_capa != "nan" and id_foto_capa != "None":
            link_drive_foto = f"https://drive.google.com/file/d/{id_foto_capa}/view"
            st.link_button("🖼️ Ver Foto (⚠️ Pasta não localizada)", link_drive_foto, use_container_width=True)
            
        st.markdown("---")
        st.markdown("#### 📊 Grade de Estoque")
        
        # 🧹 TRATAMENTO DE CORES PARA EXIBIÇÃO
        if not skus.empty:
            df_skus_exibicao = skus[['TAMANHO', 'COR', 'ESTOQUE']].copy()
            # Substitui "None", "nan", vazios por um traço
            df_skus_exibicao['COR'] = df_skus_exibicao['COR'].astype(str).replace(['None', 'nan', '', 'NaN'], '-')
            st.dataframe(df_skus_exibicao, hide_index=True, use_container_width=True)
        else:
            st.write("Sem estoque cadastrado para este item.")
        
        st.markdown("---")
        st.markdown("#### 📝 Resumo Técnico (Padrão Anúncio)")
        resumo_texto = ""
        if not linha_form.empty:
            palavras_ig = ['email', 'e-mail', 'foto', 'imagem', 'link', 'carimbo']
            for col, val in linha_form.items():
                if any(ig in col.lower() for ig in palavras_ig): continue
                
                if pd.notna(val) and str(val).strip() != "":
                    val_str = str(val).strip()
                    
                    if 'preço' in col.lower() or 'custo' in col.lower() or 'valor' in col.lower():
                        val_str = formatar_moeda(val_str)
                        
                    if not val_str.startswith("http"):
                        resumo_texto += f"**{col}:** {val_str}\n\n"
            
            if resumo_texto: st.info(resumo_texto)
            else: st.write("Sem dados técnicos cadastrados.")

        # BOTÃO VOLTAR NO RODAPÉ (A SOLUÇÃO DE NAVEGAÇÃO!)
        st.markdown("---")
        if st.button("⬅️ VOLTAR PARA A VITRINE", key="btn_voltar_rodape", use_container_width=True, type="primary"):
            st.session_state['tela_atual'] = 'vitrine'
            st.rerun()

if __name__ == "__main__":
    main()
