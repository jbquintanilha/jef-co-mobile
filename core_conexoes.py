import gspread
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
import os

DIRETORIO_ATUAL = os.path.dirname(os.path.abspath(__file__))
ARQUIVO_CREDENCIAS = os.path.join(DIRETORIO_ATUAL, 'credentials.json')
PLANILHA_ID = "1W8SjACUcNMlyG_zq9DppOt7MahSwIhuMecv2pwuEQVw"
ID_PASTA_MESTRE = "1xhO35NUC2eKrtdAcJPCR4-csG2cDomjN"

def conectar_google():
    escopos = ["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"]
    creds = Credentials.from_service_account_file(ARQUIVO_CREDENCIAS, scopes=escopos)
    planilha = gspread.authorize(creds).open_by_key(PLANILHA_ID)
    drive = build('drive', 'v3', credentials=creds)
    return planilha, drive