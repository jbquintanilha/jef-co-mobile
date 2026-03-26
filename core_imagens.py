from PIL import Image, ImageOps
import io
import os

# Define o caminho do cache local de miniaturas
THUMBS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'thumbs')

# Garante que a pasta de thumbs exista
if not os.path.exists(THUMBS_DIR):
    os.makedirs(THUMBS_DIR)

def tratar_foto_ultra_fidelity(arquivo_upload, alta_fidelidade=True):
    """
    Normaliza fotos para o Drive. Mantém PNG Lossless para IA se alta_fidelidade=True.
    """
    img = Image.open(arquivo_upload)
    
    # Normalização de Cor
    if img.mode in ("RGBA", "P"):
        img = img.convert("RGB")
    
    buffer = io.BytesIO()
    
    if alta_fidelidade:
        # PNG LOSSLESS (Para análise têxtil da IA)
        # Thumbnail apenas para evitar que arquivos de 20MB travem o upload, limitando a 4K
        img.thumbnail((3840, 3840), Image.Resampling.LANCZOS)
        img.save(buffer, format="PNG", optimize=True)
        return buffer, "image/png", ".png"
    else:
        # JPEG (Standard para registro rápido)
        img.thumbnail((2048, 2048), Image.Resampling.LANCZOS)
        img.save(buffer, format="JPEG", quality=85, optimize=True)
        return buffer, "image/jpeg", ".jpg"

def gerar_thumbnail_local(bytes_in, ref_produto, max_size=(800, 800)):
    """
    Pega os bytes da imagem original, cria um quadrado perfeito otimizado (800x800)
    e salva no cache local para a vitrine rápida.
    """
    try:
        img = Image.open(io.BytesIO(bytes_in))
        
        # Converte para RGB para garantir compatibilidade JPEG
        if img.mode in ("RGBA", "P"):
            img = img.convert("RGB")
            
        # Cria um quadrado perfeito mantendo a proporção (padd or crop)
        # Usando 'fit' para crop central, mas mantendo dimensão 1x1
        thumb = ImageOps.fit(img, max_size, Image.Resampling.LANCZOS)
        
        caminho_thumb = os.path.join(THUMBS_DIR, f"{ref_produto}_thumb.jpg")
        
        # Salva como JPEG otimizado e leve (cerca de 50KB-100KB)
        thumb.save(caminho_thumb, format="JPEG", quality=75, optimize=True)
        
        return caminho_thumb
    except Exception as e:
        print(f"Erro ao gerar thumbnail local para {ref_produto}: {e}")
        return None