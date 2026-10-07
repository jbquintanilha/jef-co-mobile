# 📦 Esteira de Expedição — como a página funciona

> **Leia antes de mexer em `pages/17_Lista_Separacao.py`.**
> Cada regra aqui custou um incidente real na bancada. Mudar sem entender
> quebra coisa que levou meses pra assentar.
>
> ⛔ **Toda alteração escreve uma entrada no HISTÓRICO DE VERSÕES, no fim
> deste arquivo.** Sem exceção.

**Versão atual:** v1.1 · 24/09/2026

---

## 1. O que a página é

Sete fases que levam o pedido do marketplace até a caixa lacrada. Não é um
wizard rígido: o operador entra e sai de qualquer fase, porque a bancada
não trabalha em linha reta.

| # | Fase | O que faz |
|---|---|---|
| 1 | Etiquetas | Baixa etiqueta dos 3 canais (TikTok, Shopee, ML) |
| 2 | Separar | Lista o que pegar no estoque, **por átomo** (não por kit) |
| 3 | Etiq + Cartão | Monta a pilha numerada na ordem da bancada |
| 4 | Etiq 40x25 | Etiqueta de identificação da peça (opcional) |
| 5 | Embalar | Acompanha a montagem dos pedidos |
| 6 | Bipagem | Confere item a item com pistola/câmera |
| 7 | Conferência | Fechamento final |

---

## 2. ⭐ ONDAS — o conceito central

**Se você entender só uma coisa deste documento, que seja esta seção.**

### O problema que ela resolve

> *"hoje ele imprime todas... porém às vezes fazemos outra onda... se eu
> clicar em onda salva, entenda que aqueles pedidos, mesmo constando como
> pendente de envio já foram processados naquela onda... os novos serão uma
> segunda onda"* — Jota, 25/08/2026

O Olist só muda a situação do pedido quando ele é **de fato despachado**.
Entre imprimir a etiqueta e dar baixa passam horas — e nesse meio a lista
continua mostrando tudo como pendente. Quem imprime de novo gasta etiqueta
e se perde na bancada.

### A virada de 27/08 — onda deixou de ser carimbo

Antes, "onda" significava *"já processado"*: um carimbo. As fases só liam
os pendentes, então pedido marcado **sumia da fila**.

Hoje a onda é um **LOTE DE TRABALHO**: trava-se uma, e as 7 fases passam a
operar **só sobre ela**, em qualquer ordem de fase.

```
Sem trava  →  _alvo = fila livre (tudo que ainda não entrou em onda)
Com trava  →  _alvo = exatamente os pedidos daquela onda
```

### As regras invioláveis da onda

| Regra | Por quê |
|---|---|
| **A trava ESCONDE o resto** | Meia-trava traz de volta a confusão de não saber em que conjunto se está mexendo (decisão do Jota) |
| **Pedido novo NÃO entra sozinho numa onda travada** | A onda define o escopo do trabalho. Se pedido novo entrasse, ele ficaria "perdido" dentro de um lote que já foi separado |
| **Só entra por ação explícita** | `salvar_slot(slot, pedidos, modo="somar")` — o operador decide |
| **A 1ª marca vale** | Pedido já em outra onda não é regravado: reimprimir moveria o pedido de onda e o histórico perderia o sentido |
| **A marca dura até sair do Olist** | Não zera por dia. Voltou a aparecer pendente semana que vem? Continua marcado |
| **A seleção persiste entre as fases** | `key="onda_travada"` **sem** sufixo de fase — com uma key por fase, a escolha não seguia o operador (Jota, 31/08) |

### Onde vive

- `core_ondas_supabase.py` — **5 slots fixos** (1-5), fonte atual
- `core_ondas_expedicao.py` — SQLite local, mecanismo original
- `_widget_ondas(chave)` na página — o painel, instanciado nas fases 1 e 3

**Por que não `session_state`:** ele morre no F5. Onda precisa sobreviver a
recarga, reinício do dashboard e troca de máquina na bancada.

---

## 3. O fluxo de dados — o que alimenta o quê

⚠️ **Confundir estas duas fontes já causou otimização errada (24/09).**

```
         ┌─ API Olist (pedidos) ──→ core_sync_expedicao ─→ FASE 2 (separar)
         │                              cache 15 dias
Pedido ──┤
         └─ API dos canais (PDF) ─→ core_etiquetas_todas ─→ FASES 1 e 3
                                        SEM cache
```

**São independentes.** A Fase 2 não depende da Fase 1: ela usa dados de
**pedido** vindos do Olist, não os PDFs de etiqueta.

### ✅ A repetição foi resolvida (v1.1)

Fases 1 e 3 chamavam `baixar_tudo()` cada uma — duas viagens às APIs pelo
mesmo PDF. `core_etiquetas_cache.py` resolve **respeitando a trava**:

**Regra: cobertura exata, não "tem cache?"**

| Situação | Decisão |
|---|---|
| Onda travada e cache cobre **todos** os pedidos dela | ✅ reaproveita |
| Falta qualquer pedido da onda | ⬇️ baixa |
| **Fila livre** (sem trava) | ⬇️ **baixa sempre** |
| Arquivo sumiu do disco | ⬇️ baixa |
| Canal ausente/com erro no cache | ⬇️ baixa |
| Operador marcou "Rebaixar etiquetas" | ⬇️ baixa |

⚠️ **Fila livre baixa sempre, de propósito.** Sem lista de referência não
dá pra provar que o cache está completo — pedido novo desde a Fase 1
passaria despercebido. Errar para o lado de baixar custa segundos; errar
para o lado de reaproveitar imprime pilha errada.

A tela sempre diz o que aconteceu (*"⚡ reaproveitadas"* ou *"⬇️ baixou:
{motivo}"*) — decisão de performance não pode ser silenciosa.

**Onde o cache mora:** `~/.cache/jf_etiquetas`, **fora do Downloads**.
`guardar()` limpa a rodada anterior a cada nova. O operador só vê o PDF
final.

---

## 4. Como cada etiqueta é identificada

O nome do arquivo individual carrega o identificador:

```
Shopee →  {order_sn}.pdf      casa direto com numero_ecommerce
TikTok →  {package_id}.pdf    precisa traduzir pacote → pedido
ML     →  {shipment_id}.pdf
```

`_mapa_tiktok()` faz a ponte pacote→pedido. Sem ela, todo pedido do TikTok
cai no fim da pilha como "sem posição".

⚠️ **O número impresso na etiqueta J&T (`999882...`) é o RASTREIO**, não o
pacote nem o pedido. Não serve como chave.

### Etiqueta fora da sequência nunca é descartada

Vai para o **FIM da pilha**, numerada normalmente. Sumir com etiqueta é o
único erro pior que desordená-la — a caixa ficaria sem envio.

---

## 5. Nome civil na etiqueta (TikTok)

O TikTok emite a etiqueta com o **apelido** do comprador. Os Correios
devolvem quando o nome não corresponde a ninguém no endereço.

**Regra:** o nome civil é **ACRESCENTADO entre parênteses**, nunca
substitui. `Gustavo (Wagner Schmitt)`.

- **Primeiro + último nome**, sempre. Nome do meio não identifica ninguém
  e é justamente o que faz o texto estourar
- Não coube ao lado do nick? Vai para a **lacuna limpa** abaixo do endereço
- Não cabe nem lá? **Não escreve** — nunca vaza da margem

### A fonte do nome (mudou 3 vezes — cuidado)

| Fonte | Estado |
|---|---|
| `cliente.nome` no **Olist** | ✅ **vigente** — casado por `ecommerce.numeroPedidoEcommerce` |
| NF-e (`xNome`) | ⚠️ serve p/ Shopee/ML; **no TikTok sai com o apelido** |
| `cpf_name` da API TikTok | 🔴 morta desde 01/09 |
| `enderecoEntrega.nomeDestinatario` | 🔴 vem `null` desde 23/09 |

⚠️ **Sinal de alerta:** `gerar()` devolve `nomes_corrigidos`. Se a **fonte**
vier vazia num lote com TikTok, o resumo avisa. Zero correções com fonte
cheia é **normal** — significa que ninguém no lote usava apelido.

---

## 6. Impressão

`IMPRESSORA_ETIQUETA = "LABEL 2 BT"` — fila Bluetooth (porta COM6).
`LABEL 2` (USB001) segue instalada como alternativa.

O link RFCOMM dorme quando fica ocioso e derruba a **primeira** tentativa
com *"tempo limite do semáforo expirou"*. Por isso `imprimir_pdf_direto`
repete **3× com 3s** — o operador não precisa reconectar nada.

⚠️ **Na nuvem (`jef-co-mobile`) o botão não funciona e nunca funcionou** —
`win32print` não existe em Linux e a impressora é pareada com o PC. Não é
bug, é limite de arquitetura. No celular o Jota usa o app do fabricante.

---

## 7. Armadilhas já documentadas (não repetir)

| Armadilha | Detalhe |
|---|---|
| **`limpar_ausentes` varria a onda** | Rodava a cada render e apagava o que não estivesse na lista recebida. Com sync parcial, apagava a onda inteira em silêncio. Era a causa nº 1 do *"às vezes está, às vezes não"*. Hoje a limpeza é **botão explícito** |
| **Key por fase quebrava a seleção** | `onda_travada_{chave}` dava um seletor por tela; a escolha não seguia o operador |
| **F5 apaga tudo** | Por isso existe "Atualizar a fila" — re-busca sem zerar `session_state` |
| **Casar nome por string impressa** | Colisão por substring: `"jo"` vazou `"Jocinete Neri De Lima"` pra 4 etiquetas de destinatários diferentes (25/08). **Casar por número de pedido** |
| **Filtro `somente_imprimivel`** | Devolve só `PROCESSING` — 3 de 300 pacotes. Usar `False` quando o objetivo é **traduzir**, não imprimir |
| **Barra 1px a 203 DPI não imprime** | Usar 406 DPI na blindagem térmica |
| **Ordem da pilha** | Normal `#1..#N`. A inversão física testada em 30/08 foi **revertida** a pedido do Jota (31/08) |

---

## 8. ⛔ LEI DOS 2 REPOS

O app que o Jota usa de qualquer lugar roda do repo **`jef-co-mobile`**,
não do `JF_Automacoes`. Mexeu nesta página ou nos `core_*` que ela usa?
**Vai nos dois.**

```bash
git log origin/main..mobile/main --oneline -- <arquivo>   # SEMPRE antes
```

Há melhorias que nasceram só no app (scanner de câmera nas fases 6 e 7,
`core_env_loader`, painel de anotações). **Fundir, nunca sobrescrever.**

---

## 9. HISTÓRICO DE VERSÕES

> ⛔ **Regra permanente:** toda alteração na esteira escreve uma entrada
> aqui — o que mudou, por quê, e o que foi medido. Versão nova a cada
> mudança de comportamento; correção pontual incrementa o terceiro número.

### v1.0 — 24/09/2026 · Terminador
Documento criado a pedido do Jota, depois de eu quase otimizar o download
da Fase 3 sem considerar a trava de onda.

> *"lembre que temos a lógica de ondas... se chegar pedido novo esse não
> deve quebrar a onda e entrar 'perdido' nela... documente a lógica do
> nosso painel para você não esquecer mais e irmos sempre aprimorando e
> não destruindo o que foi levado tempo para desenvolver"*

Estado registrado (não é mudança, é o retrato de hoje):
- 7 fases, ondas em 5 slots fixos, trava que esconde o resto
- Fonte de nome civil: Olist (`cliente.nome`)
- Impressão: `LABEL 2 BT` via Bluetooth, com retry 3×
- `gerar()` leva ~62s, dos quais ~38s são download repetido da Fase 1

**Pendência aberta:** eliminar o download repetido Fase 1 → Fase 3
respeitando a trava de onda. **Não implementado** — precisa de desenho que
não reintroduza o problema que a onda resolve.

### v1.1 — 24/09/2026 · Terminador
**Resolve a pendência da v1.0:** cache de etiquetas entre Fase 1 e Fase 3.

`core_etiquetas_cache.py` (novo). A regra é **cobertura exata**: só
reaproveita quando há onda travada **e** o cache contém todos os pedidos
dela, com os arquivos ainda em disco. Fila livre baixa sempre — sem lista
de referência não dá pra provar completude, e pedido novo passaria batido.

Junto, duas correções que o Jota pediu na mesma conversa:

- **O cache mora fora do Downloads** (`~/.cache/jf_etiquetas`) e `guardar()`
  limpa a rodada anterior. Motivo: *"antes ele salvava diversos PDF nos
  downloads e ia remontando lá, gerando diversos documentos de lixo... o
  lixo não deveria ficar"*.
- **A limpeza do Downloads estava incompleta:** pegava 2 dos 6 arquivos
  intermediários por rodada. Os `_10x15` e `_10x15_com_cartao` escapavam
  porque não estavam na lista de sufixos, e o canal `ml` nem era varrido.

**Sobre o mobile / Supabase** (pergunta do Jota): o cache **não** vai pro
Supabase, e é proposital. A onda é estado compartilhado e já sincroniza;
o cache aponta para **arquivo PDF em disco**. Gravar esses caminhos na
nuvem seria pior que inútil — o mobile leria um caminho da máquina do
Jota. No Streamlit Cloud a Fase 1 baixa pro container e o cache vale
dentro da sessão, que é o comportamento certo lá também.

**Medido:** `gerar()` de ~62s → **31,1s** com cache válido. Fase 1 continua
baixando normal (~52s), mas só uma vez.

### v1.2 — 05/10/2026 · Terminador
**A fila de separação passa a viver no Supabase (instantâneo): carregar no PC, continuar no celular.**

Pedido do Jota: *"uma base só... eu podia carregar tudo usando o PC e depois continuar no celular...
a perda é maior ao fazer 2x a mesma coisa, o mesmo download"*.

- **Tabela nova `esteira_fila_snapshot`** (id, criado_em, criado_por, situacoes, total, `dados` jsonb). RLS ligado e
  **nenhuma política pública**: o instantâneo guarda o pedido BRUTO do Olist (endereço, CPF), então só a chave de
  serviço entra (no app da nuvem ela vem no mesmo bloco embutido, `SUPABASE_SERVICE_KEY`). Mantém os 5 últimos.
- **O que se guarda:** só o resultado do download (`pedidos` do `sincronizar`). A lista de separação e os átomos
  são **recalculados** ao abrir (`processar_batch_picking` é determinístico) — não se grava o derivado.
- **Quando grava:** no fim de toda sincronização que baixou pedidos (`atualizar_separacao`).
- **Quando abre:** sessão vazia (F5, ou celular abrindo o que o PC carregou). **Celular/nuvem:** o instantâneo de até
  12h. **PC (Windows):** só um instantâneo com menos de 10 min — a Fase 1 já re-sincroniza sozinha e barato (cache
  incremental), e um instantâneo velho faria o PC mostrar fila desatualizada. A tela avisa: *"Fila aberta do
  instantâneo salvo pelo pc às HH:MM"*; **Atualizar** baixa de novo.
- **Falha de rede nunca derruba a Esteira:** gravar/ler o instantâneo é "melhor esforço".
- **Armadilha:** a chave **anônima** da nuvem NÃO enxerga a tabela (de propósito). Quem usar `core_esteira_snapshot`
  fora do app precisa da chave de serviço, senão lê vazio sem erro.
- Arquivos: `core_esteira_snapshot.py` (novo), `pages/17_Lista_Separacao.py`. Testes: 7 cenários (AppTest + tabela real).

### v1.3 — 05/10/2026 · Terminador
**Nome civil na etiqueta do TikTok (J&T) saía em cima do código de barras — corrigido.**

Achado pelo Jota no pedido **1158** (`etiquetas_esteira_20261004_2242.pdf`, p.19): "(Washington Cardoso)" apareceu
sobre o código de barras do meio da etiqueta. Varredura de 45 etiquetas TikTok de 9 PDFs (28/09 a 04/10): só essa.

**Causa (a mesma nos 2 caminhos de "2ª linha": `_lacuna_limpa` na pilha e `_linha_livre_abaixo` no módulo do nome):**
quando o nome não cabe ao lado do apelido, o código procura a lacuna abaixo do endereço. O "fim do bloco do
destinatário" era medido como *"a última linha alinhada à mesma margem esquerda"* — e o bloco do **REMETENTE** tem a
MESMA margem. Resultado: o destinatário "terminava" no fim do remetente (y≈233) e o nome era escrito a ~270pt, logo
acima do número do código de barras de baixo. Agravante: o limite à direita só olhava TEXTO; a borda da coluna do
código de barras é um DESENHO (linha vertical), então o nome "cabia" no cálculo e cruzava a linha da caixa.

**Correção** (`core_etiqueta_nome_real.py` + `core_etiquetas_na_esteira.py`):
- `_fim_do_bloco()`: só conta texto CONTÍGUO abaixo do nome (salto vertical > 6pt encerra o bloco; o remetente
  começa ~40pt depois).
- `_regua_horizontal_abaixo()`: a linha que fecha a caixa do destinatário é o TETO — nunca escrever além dela
  (4pt de folga, para não encostar).
- `_regua_vertical_a_direita()`: a borda da coluna do código de barras entra no limite à direita.
- Efeito no 1158: o nome passa a ficar dentro da caixa, abaixo do endereço e acima da linha; longe do código de barras.
- Testes: etiqueta real do 1158 + 12 casos com outras etiquetas TikTok (nome longo e curto): 0 fora da caixa.
- **Regra para o futuro:** geometria de etiqueta se mede com TEXTO **e** DESENHOS (`page.get_drawings()`); alinhamento
  de margem não identifica bloco (destinatário e remetente compartilham a esquerda).

### v1.4 — 06/10/2026 · Terminador
**"Gerar pilha numerada" mais rápido, PDF idêntico.** Marco de retorno: tag `esteira-pre-velocidade-20261005`
(+ branch `backup/esteira-pre-velocidade`); nos repos remotos, tag `esteira-pre-velocidade-origin` / `-app`.

Medido (`gerar()` com cache, mesmo input, antigo × novo): **onda (cache) 32,3 s → 5,2 s**; fila livre
~40–55 s → ~31–35 s (o resto é o download das etiquetas nos marketplaces, que a fila livre refaz DE PROPÓSITO).
PDFs conferidos página a página (texto + pixels a 100 dpi): **idênticos** (8/8, 4 rodadas).

Causas e correções:
- **Nome civil do Olist (era ~4–37 s):** `cnr.mapa_por_pedido_olist([4,7])` refazia `GET /pedidos` logo após a rajada
  do sync e batia no 429 do Olist (`core_olist.request` dorme 15 s por tentativa). Agora o mapa vem da MESMA fila já
  baixada para sequenciar (`_sequencia_completa()`, 4º item). Conferido: 7/7 nomes iguais. O texto-reserva "Cliente"
  de `processar_batch_picking` é descartado (não é nome civil). Rede de segurança: só vai à API se a fila bateu o teto
  de 100 pedidos e faltar nome. `mapa_por_pedido_olist` permanece intacta.
- **TikTok pacote→pedido (era ~19 s ×2 no caminho de onda):** a API pagina ~360 pacotes para traduzir 2. A relação é
  imutável → cache em `%TEMP%/jf_tiktok_pacote_pedido.json` (gravação atômica; falha de disco nunca derruba o lote).
  Pacote desconhecido continua indo à API; a 1ª chamada reaproveita na 2ª (`_mapa_tt_cache`).
- **Índice de NF-e (~1,7 s):** `core_nome_civil_nfe.indexar()` memoizado por assinatura (nome+mtime+tamanho dos XMLs).
- **Instrumentação:** `gerar()` devolve `tempos` por etapa e loga `Gerar pilha: Xs | etapas: {...}`.
- **Não alterado de propósito:** blindagem térmica (406 dpi/1-bit), download de etiquetas, ordem/numeração.
- **Possível próximo passo (não feito, ganho ~4 s):** a página 17 passar a fila já carregada ao `gerar()` e pular o 2º sync.

### v1.5 — 07/10/2026 · Terminador
**🔴 Etiqueta da Amazon "sumia" da Esteira (risco de punição por envio atrasado) — corrigido.**

Sintoma: Esteira baixou 15 etiquetas, `amazon 0 · ⚠️ 1 problema`: "nota sem agrupamento de expedição Amazon no Olist" (pedido 701-6136600-3262668, NF 913).
**Causa:** `mapa_expedicoes_amazon` olhava só os **60 agrupamentos mais recentes** do Olist, assumindo "1 por dia e forma de envio".
Falso: o Olist cria **1 agrupamento por ONDA** (medido: 300 = 208 Shopee, 85 TikTok, 6 Amazon). O agrupamento
Amazon de 03/10 (id 582447973) ficou fora da janela e o pedido ficou sem etiqueta — a etiqueta EXISTIA no Olist
(TBR441190855, idêntica à baixada do Seller Central).
**Correção** (`core_etiquetas_amazon_olist.py`): janela por DATA (21 dias), paginando a listagem (100/pág); `mapa_expedicoes_amazon(client, notas)`
para ao achar todas as notas pendentes (6,6 s em vez de 14 s). Teste real: etiqueta da Nadia baixada, TBR confere.
**Regra:** nunca limitar listagem do Olist por QUANTIDADE quando canais de volume diferente dividem a mesma lista — limitar por data.
**Armadilha relacionada:** mapeamento de situações da Amazon no Olist — `Shipped` marca "Enviado" antes do transportador coletar (`EasyShipShipmentStatus=PendingDropOff`); conferir status real via SP-API, não pelo Olist.

### v1.6 — 07/10/2026 · Terminador
**Alerta visível de pedido Amazon pendente sem etiqueta (anti-punição).**
`auditar_amazon_pendentes()` (`core_etiquetas_amazon_olist.py`) consulta a SP-API (verdade da Amazon, não o Olist) a cada
download de etiquetas: pedido MFN pago e ainda não entregue ao transportador (`Unshipped`/`PartiallyShipped`/`InvoiceUnconfirmed`,
ou `Shipped` + Easy Ship `PendingSchedule/PendingPickUp/PendingDropOff`) que NÃO entrou nas etiquetas baixadas vira
`🚨 AMAZON sem etiqueta na Esteira: <pedido> (<status>) ATRASADO há Nh / prazo de envio em Nh`. Aparece como `st.error`
na Fase 1 e na Fase 3 (`pages/17_Lista_Separacao.py`), não como "N problema(s)". Roda também quando o Olist não tem fila
(nota não emitida). Se a SP-API falhar, o próprio alerta avisa que a auditoria não rodou (nunca silencioso). Não roda em
onda filtrada (`pedidos` informado). Teste real: Nadia 701-6136600-3262668 → "ATRASADO há 37h".

### v1.7 — 07/10/2026 · Terminador
**Fase 1 "Baixar tudo de uma vez" agora baixa só o que FALTA da onda.**
Antes: o botão refazia o download de todo canal marcado (25–70 s por canal), mesmo com onda travada e etiquetas já guardadas.
Agora (`core_etiquetas_cache.baixar_so_o_que_falta`): escopo = ciclo (range) > onda travada > fila livre. Com escopo, traduz o cache
para PEDIDO (TikTok via ponte pacote→pedido), baixa só a diferença (`baixar_tudo(somente=faltam)`), junta com o que já tinha
(só pedidos do escopo) e remonta o PDF único (normaliza 10x15 + cartão, como o `gerar()`). A tela mostra
"🎯 Escopo de N: ♻️ X já baixados · ⬇️ Y faltavam". Fila livre (sem escopo) NÃO muda: sem lista de referência não dá para provar
que o cache está completo (pedido novo passaria despercebido). Auditoria Amazon (SP-API) roda também aqui.
Medido: onda de 6 pedidos com tudo já baixado **31,9 s → 3,1 s**; PDF único com as 12 páginas (6 etiquetas + 6 cartões); pedido
faltando → só ele é buscado. Fase 3 reaproveita o cache mesclado (`cache_serve` cobre a onda).
