import pandas as pd
import os
import re
from core_conexoes import conectar_google, ID_PASTA_MESTRE

DIR_ATUAL = os.path.dirname(os.path.abspath(__file__))
DB_LOCAL_DIR = os.path.join(DIR_ATUAL, 'local_db')
if not os.path.exists(DB_LOCAL_DIR): os.makedirs(DB_LOCAL_DIR)

PATH_PAI = os.path.join(DB_LOCAL_DIR, 'produtos_pai.csv')
PATH_SKU = os.path.join(DB_LOCAL_DIR, 'skus.csv')
PATH_AUX = os.path.join(DB_LOCAL_DIR, 'aux_estrutura.csv')
PATH_FORM = os.path.join(DB_LOCAL_DIR, 'respostas_form.csv')

def limpar_codigo(texto):
    """Remove traços, pontos, espaços e deixa tudo em maiúsculo para comparar."""
    if not texto: return ""
    return re.sub(r'[^A-Z0-9]', '', str(texto).upper())

def sincronizar_nuvem_para_local():
    """Baixa tudo do Google Sheets, incluindo a base imutável do Formulário."""
    try:
        planilha, _ = conectar_google()
        df_pai = pd.DataFrame(planilha.worksheet("PRODUTOS_PAI").get_all_records())
        df_sku = pd.DataFrame(planilha.worksheet("SKUS_VARIACOES").get_all_records())
        
        try:
            df_aux = pd.DataFrame(planilha.worksheet("AUX_ESTRUTURA").get_all_records())
            df_aux.to_csv(PATH_AUX, index=False)
        except: pass

        try:
            df_form = pd.DataFrame(planilha.worksheet("Respostas do Formulário 1").get_all_records())
            df_form.to_csv(PATH_FORM, index=False)
        except: pass

        df_pai.to_csv(PATH_PAI, index=False)
        df_sku.to_csv(PATH_SKU, index=False)
        
        return True, "Download concluído com sucesso."
    except Exception as e:
        return False, f"Erro ao sincronizar: {e}"

def carregar_banco_local():
    """Lê os dados locais na velocidade da luz."""
    try:
        df_pai = pd.read_csv(PATH_PAI) if os.path.exists(PATH_PAI) else pd.DataFrame()
        df_sku = pd.read_csv(PATH_SKU) if os.path.exists(PATH_SKU) else pd.DataFrame()
        df_aux = pd.read_csv(PATH_AUX) if os.path.exists(PATH_AUX) else pd.DataFrame()
        df_form = pd.read_csv(PATH_FORM) if os.path.exists(PATH_FORM) else pd.DataFrame()
        return df_pai, df_sku, df_aux, df_form
    except Exception as e:
        return pd.DataFrame(), pd.DataFrame(), pd.DataFrame(), pd.DataFrame()

def rodar_guardiao_fotos():
    """Lógica do Guardião acoplada com Blindagem de Subpastas e RENOMEAÇÃO EM MASSA."""
    try:
        from core_conexoes import conectar_google, ID_PASTA_MESTRE
        import re
        
        planilha, drive = conectar_google()
        NOME_PASTA_CAPAS = "00_CAPAS_VITRINE"
        
        q_capas = f"'{ID_PASTA_MESTRE}' in parents and name = '{NOME_PASTA_CAPAS}' and mimeType = 'application/vnd.google-apps.folder'"
        res_capas = drive.files().list(q=q_capas).execute().get('files', [])
        id_cofre_capas = res_capas[0]['id'] if res_capas else drive.files().create(body={'name': NOME_PASTA_CAPAS, 'mimeType': 'application/vnd.google-apps.folder', 'parents': [ID_PASTA_MESTRE]}, fields='id').execute().get('id')

        aba_pai = planilha.worksheet("PRODUTOS_PAI")
        dados = aba_pai.get_all_records()
        idx_foto = aba_pai.row_values(1).index("FOTO_CAPA_ID") + 1

        for idx, linha in enumerate(dados):
            ref_original = str(linha.get("REF", "")).strip()
            id_pai_original = str(linha.get("ID_PAI", "")).strip()
            
            def _limpar(t): return re.sub(r'[^A-Z0-9]', '', str(t).upper())
            ref_limpa = _limpar(ref_original)
            id_pai_limpo = _limpar(id_pai_original)
            
            if not ref_limpa and not id_pai_limpo: continue

            termo_busca = re.sub(r'[^0-9]', '', ref_original) if any(c.isdigit() for c in ref_original) else ref_original
            if not termo_busca: termo_busca = id_pai_limpo

            q_p = f"'{ID_PASTA_MESTRE}' in parents and name contains '{termo_busca}' and mimeType='application/vnd.google-apps.folder' and name != '{NOME_PASTA_CAPAS}'"
            pastas = drive.files().list(q=q_p, fields="files(id, name)").execute().get('files', [])

            pasta_alvo = None
            for p in pastas:
                if ref_limpa in _limpar(p['name']) or id_pai_limpo in _limpar(p['name']):
                    pasta_alvo = p
                    break

            if pasta_alvo:
                # BLINDAGEM: Só pega imagem e ignora as subpastas da Shopee/IA
                q_i = f"'{pasta_alvo['id']}' in parents and mimeType contains 'image/'"
                imagens = drive.files().list(q=q_i, fields="files(id, name, mimeType)").execute().get('files', [])

                if imagens:
                    # Ordena para manter a sequência correta das fotos
                    imagens.sort(key=lambda x: x['name'])
                    
                    # --------------------------------------------------------
                    # O MOTOR DE RENOMEAÇÃO QUE ESTAVA FALTANDO!
                    # --------------------------------------------------------
                    for i_img, img_obj in enumerate(imagens):
                        # Define a extensão correta
                        ext = ".png" if "png" in img_obj.get('mimeType', '') else ".jpg"
                        
                        novo_nome = f"IMG_{id_pai_original}_{str(i_img + 1).zfill(2)}{ext}"
                        
                        # Só consome cota de API se o nome realmente precisar mudar
                        if img_obj['name'] != novo_nome:
                            drive.files().update(fileId=img_obj['id'], body={'name': novo_nome}).execute()
                    # --------------------------------------------------------

                    # Pega o ID da primeira imagem para ser a capa
                    img_original = imagens[0]
                    nome_atalho = f"ATALHO_{id_pai_original}"
                    
                    if not drive.files().list(q=f"'{id_cofre_capas}' in parents and name = '{nome_atalho}'").execute().get('files', []):
                        drive.files().create(body={'name': nome_atalho, 'mimeType': 'application/vnd.google-apps.shortcut', 'parents': [id_cofre_capas], 'shortcutDetails': {'targetId': img_original['id']}}).execute()
                    
                    if str(linha.get("FOTO_CAPA_ID")) != img_original['id']:
                        aba_pai.update_cell(idx + 2, idx_foto, img_original['id'])
                        
        return True, "Guardião varreu, renomeou as imagens para o padrão J&F Co e blindou as subpastas com sucesso!"
    except Exception as e:
        return False, f"Erro no Guardião: {e}"