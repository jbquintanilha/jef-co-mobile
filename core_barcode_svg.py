# ==============================================================================
# NOME DO SCRIPT: core_barcode_svg.py
# DESCRICAO: Gera codigo de barras Code 39 em SVG puro (sem dependencia externa)
# FUNCAO: Barcode de COMANDO exibido na tela, lido pela pistola da bancada
# STATUS: ATIVO
# VERSAO: 1.0
# DATA: 02/09/2026
# AUTOR: Terminador (Claude) / J&F Co.
# ==============================================================================
"""Code 39 em SVG.

Por que Code 39 e nao Code 128 (mais compacto):
  - Nao tem digito verificador -- perdoa leitura ruim em tela de LCD.
  - Barras mais largas para a mesma quantidade de dados legivel.
  - Todo leitor a laser/CCD ja' vem com Code 39 habilitado de fabrica.

Uso: barcode gerado aqui NAO e' etiqueta de produto. E' um comando que o
operador bipa na TELA para o sistema agir (ex.: devolver o foco ao campo
de bipagem sem precisar pegar no mouse).
"""

# Code 39: cada caractere = 9 elementos (5 barras + 4 espacos).
# 1 = elemento largo, 0 = elemento estreito. Sempre 3 largos por caractere.
_C39 = {
    "0": "000110100", "1": "100100001", "2": "001100001", "3": "101100000",
    "4": "000110001", "5": "100110000", "6": "001110000", "7": "000100101",
    "8": "100100100", "9": "001100100", "A": "100001001", "B": "001001001",
    "C": "101001000", "D": "000011001", "E": "100011000", "F": "001011000",
    "G": "000001101", "H": "100001100", "I": "001001100", "J": "000011100",
    "K": "100000011", "L": "001000011", "M": "101000010", "N": "000010011",
    "O": "100010010", "P": "001010010", "Q": "000000111", "R": "100000110",
    "S": "001000110", "T": "000010110", "U": "110000001", "V": "011000001",
    "W": "111000000", "X": "010010001", "Y": "110010000", "Z": "011010000",
    "-": "010000101", ".": "110000100", " ": "011000100", "$": "010101000",
    "/": "010100010", "+": "010001010", "%": "000101010", "*": "010010100",
}


def code39_svg(dado: str, altura: int = 90, estreita: int = 3,
               razao: int = 3, cor: str = "#000000",
               fundo: str = "#ffffff", legenda: bool = True) -> str:
    """Devolve o SVG (string) de um Code 39 com o conteudo `dado`.

    ⚠️ Defaults propositalmente GROSSOS (estreita=3px, razao 3:1). O leitor
    da bancada le' de um monitor, nao de papel: barra fina some no pixel do
    LCD e o brilho da tela borra a borda. Codigo curto + barra larga e' o
    que faz a leitura pegar de primeira (Jota, 02/09).

    `razao` = quantas vezes a barra larga e' maior que a estreita (2 a 3).
    """
    dado = (dado or "").upper()
    for ch in dado:
        if ch not in _C39 or ch == "*":
            raise ValueError(f"Caractere invalido para Code 39: {ch!r}")

    # '*' delimita inicio e fim -- exigencia do Code 39.
    seq = "*" + dado + "*"
    larga = estreita * razao
    margem = estreita * 10  # zona muda: sem ela o leitor nao engata

    partes = []
    x = margem
    for i, ch in enumerate(seq):
        for j, bit in enumerate(_C39[ch]):
            w = larga if bit == "1" else estreita
            if j % 2 == 0:  # posicao par = barra; impar = espaco
                partes.append(
                    f'<rect x="{x}" y="0" width="{w}" height="{altura}" fill="{cor}"/>'
                )
            x += w
        if i < len(seq) - 1:
            x += estreita  # espaco separador entre caracteres

    largura = x + margem
    alt_total = altura + (26 if legenda else 0)

    txt = ""
    if legenda:
        txt = (
            f'<text x="{largura/2}" y="{altura + 19}" text-anchor="middle" '
            f'font-family="monospace" font-size="15" font-weight="bold" '
            f'letter-spacing="2" fill="{cor}">{dado}</text>'
        )

    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{largura}" '
        f'height="{alt_total}" viewBox="0 0 {largura} {alt_total}" '
        f'role="img" aria-label="Codigo de barras {dado}">'
        f'<rect width="{largura}" height="{alt_total}" fill="{fundo}"/>'
        f'{"".join(partes)}{txt}</svg>'
    )
