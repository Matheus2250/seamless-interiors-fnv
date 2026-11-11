# Plano: Seamless Interiors para todos os interiores

Objetivo: aplicar a técnica validada na General Store de Goodsprings (ver `CLAUDE.md`) a todo
interior viável, de forma automática, com uma lista clara do que precisa de ajuste manual.

## Tamanho do problema (medido no FalloutNV.esm)

| Item | Quantidade |
|---|---|
| Interiores com porta de loading para um worldspace | 288 |
| ... no WastelandNV | 249 (o resto: Freeside, Strip, Westside etc.) |
| ... com mais de uma porta externa | 62 |
| ... que também levam a outros interiores (andares, porões, cofres) | 43 |

Expectativa realista: só uma parte cabe no prédio de fora (efeito "TARDIS": interiores maiores
que o prédio). A Fase 1 mede isso antes de qualquer outra coisa.

---

## Fase 0: transformar o protótipo num pipeline genérico — **FEITA** (31/10/2026)

Hoje `build_open_esp.py` e os cortadores têm constantes da General Store (formIDs, retângulos,
planos de parede). Trocar por:

- **Um arquivo de configuração por interior** (`buildings/<edid>.json`), gerado automaticamente
  na Fase 1 e editável à mão: célula interior, porta interior/exterior, ref e mesh do prédio, Δ,
  retângulos de corte, listas de exceção, NPCs, decisões manuais.
- **Um builder que aceita N interiores** e gera um ESP só (ou um por cidade), com alocação de
  formIDs **determinística e estável** (registro `formids.json` por chave = interior + ref de
  origem). Assim uma versão nova não quebra saves de quem já usa a anterior.
- Módulos separados: `align.py`, `cut_visual.py` (generaliza `cut_door_nif`/`cut_wall_nif`),
  `collision.py` (degenerar, detectar vãos), `navmesh.py` (transformar/dividir/registrar),
  `actors.py` (NPCs e pacotes), `refs.py` (cópia e regras), `report.py`.
- Testes de regressão: rodar o pipeline para a General Store e comparar com a v1 atual
  (mesmo resultado de posições, cortes e validação).

**Pronto quando:** a General Store é gerada pelo pipeline novo, só a partir da config, e passa
nos mesmos testes no jogo.

### Resultado

- `py -I pipeline.py` gera `out/build/Data/` (ESP + 3 meshes) e o validador dá PASS (0 erros,
  0 avisos).
- `py -I regress_v1.py` compara com a v1 publicada: **PASS**. 197/197 cópias pareadas (por
  proximidade, não por posição arredondada), desvio máximo 0,047 unidade (o pipeline mede o
  centro da folha nos vértices do modelo; a v1 media à mão), 41/41 triângulos de navmesh iguais,
  as mesmas 6 sobrescritas, os mesmos cortes de colisão. A v1 removia 2 triângulos de colisão a
  mais no prédio (paredes do anexo em y=-32); o pipeline novo acertadamente os mantém.
- Diferenças aceitas: 14 refs a mais entram como persistentes (peças com colisão em
  `meshes\architecture\` / `dungeons\`: pilares, uma cadeira, uma lâmpada) e o rádio acima do
  telhado é escondido sob o piso em vez de ficar no ar.
- Falta só o teste no jogo com os caminhos novos de mesh (`SeamlessInteriors\GSGeneralStore\...`).
  O usuário pediu para não abrir o jogo por enquanto; o teste fica pendente, e a v1 continua
  sendo a versão instalada.

## Fase 1: inventário e triagem automática — **FEITA** (31/10/2026)

Para cada interior com porta para o mundo:

1. **Porta e prédio:** porta exterior (XTEL), porta interior correspondente, e o **prédio**:
   STAT/SCOL perto da porta cuja fachada contém a porta (bounds do NIF + convenção de rotação).
2. **Alinhamento:** Δ = ext_rz − int_rz (+π), escolhendo o candidato que põe o interior atrás
   da fachada.
3. **Encaixe:** OBND das peças do interior (rotacionadas) contra o volume do prédio (visual e
   colisão). Medir:
   - quanto passa para fora (por lado);
   - **terreno acima do piso** (amostrar LAND/VHGT na área): o terreno atravessaria o chão;
   - **outras refs do exterior** dentro do volume (cercas, entulho, árvores encostadas).
4. **Complexidade:**
   - número de portas externas;
   - links para outros interiores (andares);
   - NPCs morando ou visitando;
   - quests e scripts que citam a célula: CTDA `GetInCell`/`GetInSameCell`, SCRO apontando
     para a célula ou para refs dela, PLDT/PTDT tipo 1, refs com XTEL para lá.
5. **Saída:** `triage.csv` com uma nota (0–100) e a categoria:
   - **A, automático:** cabe (folga ≤ 40 unidades), 1 porta, 1 célula, sem scripts de célula.
   - **B, revisão:** cabe com pequenas sobras, ou tem NPCs, ou tem condições de célula.
   - **C, manual:** várias portas, vários andares, ou sobra moderada.
   - **X, inviável:** não cabe, quest-crítico, ou interior gigante (Vaults, cassinos).

**Pronto quando:** existe o CSV para os 249 do WastelandNV e eu reviso a categoria de uns 10 por
amostragem no jogo.

### Resultado

Ferramentas novas:
- `tools/si/inventory.py` — varre o ESM uma vez (8 s) e cacheia em `tools/out/inventory.json`:
  2.758 portas, **283 células internas** com porta para um worldspace (247 no WastelandNV),
  94.595 refs internas, 1.053 atores, 30.236 STATs/SCOLs exteriores perto das portas, e as
  referências (pacotes PLDT/PTDT tipo 1, scripts com SCRO apontando para refs da célula,
  menções da célula em QUST/INFO, portas que entram).
- `tools/si/triage.py` — para cada interior: acha o prédio (STAT/SCOL cujo AABB contém a folha
  da porta, ranqueado por quanto do interior cabe dentro dele), alinha, mede encaixe, terreno e
  complexidade.
- `tools/si/terrain.py` — altura do terreno a partir de LAND/VHGT (**decodificação nova**, ver
  CLAUDE.md): bate com os refs do chão com erro mediano de 0,25 unidade.
- `tools/triage.py` — gera `tools/out/triage.csv` (283 linhas, ~2,5 min).

Saída (medida final, 31/10/2026): **A=18, B=14, C=104, X=147** — o X cresceu quando o
candidato a prédio passou a exigir colisão encostada na porta (postes e placas deixaram de
ser "o prédio"). 52 interiores entraram no conjunto A/B.

O que a medição revelou (importante para o resto do plano):
1. **Só uma parte dos interiores tem prédio único de mesh.** FNV monta muitas construções com
   peças de kit (prisão de NCR, Big MT, hangares de Nellis, cassinos). Nesses casos não existe
   "o prédio" para encaixar; a triagem marca X ("sem prédio") ou mede contra uma peça pequena
   (doca, entulho) e o resultado é grande por construção.
2. **O efeito TARDIS aparece já em Goodsprings**: a loja cabe (folga 0), mas a casa 02 tem
   1.274 de profundidade interior contra 887 de construção — sobra 380 no fundo. O saloon é
   2.025x1.427 dentro de 1.941x1.194 (sobra 140). Ou seja, o próximo interior de Goodsprings
   não é "só rodar o pipeline": precisa de decisão de projeto (encolher o interior, aceitar a
   sobra, ou trocar o prédio).
3. **Decalques e planos de luz não contam para o encaixe** (a casca é o que interessa); eles
   saem numa coluna separada (`fit_other`).
4. Terreno acima do piso é o sinal mais comum de inviabilidade (64 dos 283): são porões,
   cavernas e esgotos, cujo piso está abaixo do terreno no lugar da porta.

## Fase 2: geometria (prédio e paredes)

Generalizar o que foi feito à mão:

1. **Fachada visual:** achar os triângulos da parede atrás da porta (plano da fachada, faixa de
   espessura) e recortar o retângulo da porta. Incluir faixas inclinadas (rodapé/moldura).
2. **Colisão da fachada:**
   - Se já houver vão (caso da General Store), não mexer.
   - Se não houver: degenerar os triângulos que cruzam a porta e **repor a parede em volta
     com primitivas de colisão** (refs com XPRM tipo caixa), em vez de refazer o MOPP. Validar
     essa técnica num prédio de teste antes de escalar.
3. **Paredes internas de colisão do prédio:** degenerar os triângulos que cortam o volume
   caminhável do interior (como a parede x = 608).
4. **Parede atrás da antiga porta de loading, no interior:** achar as peças do kit cuja colisão
   cruza o corredor da porta, recortar visual e colisão, e gerar variantes de mesh só para
   essas refs (`meshes/SeamlessInteriors/<interior>/...`).
5. **Verificadores automáticos:** vão da porta livre (raios), resto da fachada intacto, nenhum
   triângulo de colisão no corredor da porta.

### Estado (31/10/2026)

- `tools/verify_build.py` cobre o item 5 (corredor livre, sem colisão do prédio no volume
  caminhável, fachada recortada, encaixe da casca) e roda sobre o plano, sem gerar ESP.
- Recorte da fachada, remoção de colisão interna e paredes da porta funcionam de forma genérica,
  com três correções desta rodada: escala do REFR do prédio (XSCL 0,9/1,1), banda adaptativa
  para parede recuada (30–120 atrás da folha) e corredor com profundidade 130 e largura
  proporcional à folha.
- `tools/make_configs.py` gera as configs a partir do `triage.csv`; experimentais nascem com
  `"enabled": false` e só entram com `pipeline.py --all`, para a release continuar só a loja.
- Experimento com 29 configs A/B (1 porta, sem links): ESP com 30 interiores passa no validador
  e **30/30 passam no `verify_build`**. O que faltava era o referencial da porta: corredor,
  retângulo de corte e raios agora usam a normal/lado da folha, não os eixos do mundo; peças
  giradas cortam pelo retângulo orientado, portas com OBND zerado usam os bounds do modelo, e
  armadilhas/arames são ignorados. A loja continua com as meshes byte a byte iguais.
- Vários interiores no mesmo prédio: resolvido (mesh único com todos os vãos; 36 interiores em
  30 prédios no conjunto A/B).
- Células com mais de uma porta para fora: todas as portas são convertidas, mas só quando os
  pares são rígidos; o resto é recusado com mensagem (`door_pairs.py` mede as 62).

## Fase 3: referências do interior

Regras (já validadas):
- Copiar com transformação (posição, rz + Δ). Manter XOWN/XLOC. Pôr o dono da célula (XOWN da
  CELL, e XRNK se for facção) nas refs sem dono.
- Não copiar: portas de loading, NorthMarker, efeitos de luz de janela (`IndFXLightRays*`,
  `FXLightBeam*`), roombounds/portais/occlusion planes, marcadores de teleporte.
- **Cada ref temporária no grupo da célula que contém a posição dela.**
- Peças estruturais do kit (as que dão piso e parede) **persistentes**, para carregarem junto
  com NPCs persistentes.
- Refs com XESP/XLKR apontando para outras refs do interior: remapear para as cópias em vez de
  descartar (precisa para luzes que acendem juntas, armadilhas, terminais).
- Rotação x/y: implementar a composição correta de rotação (matriz) em vez de somar rz,
  testando a convenção de Euler do motor com um objeto inclinado.

### Estado (31/10/2026)

- Cópia com transformação, XOWN/XLOC herdados e dono da célula nas refs sem dono: feito e
  coberto pela regressão contra a v1.
- Não copiados: portas de loading, NorthMarker, `IndFXLightRays*`, `FXLightBeam*`; ativadores
  acima do telhado vão para baixo do piso.
- Cada ref temporária vai para o grupo da célula que contém a posição — agora o validador
  verifica isso em qualquer célula externa, não só na da loja.
- Peças estruturais (STAT com colisão em architecture/dungeons) persistentes.
- Pendente: refs com XESP/XLKR apontando para outras refs do interior (luzes que acendem
  juntas, armadilhas, terminais) ainda são descartadas em vez de remapeadas; e a composição
  de rotação x/y (item acima).

## Fase 4: NPCs, pacotes e scripts

1. **Moradores** (ACHR na célula): mover o ref (mesmo formID) para a célula persistente do mundo.
2. **Pacotes** com local "na célula X" (PLDT/PTDT tipo 1):
   - do próprio morador: "perto da posição de editor" com raio do tamanho do interior;
   - de **visitantes** (outros NPCs que vão até a loja): "perto de uma referência" apontando
     para um XMarker persistente novo, no centro do interior.
   - Remover flags de trancar/destrancar portas (PKDT 0x08–0x100).
3. **Condições e scripts que dependem da célula** (`GetInCell`, refs da célula em SCRO, `MoveTo`
   para marcadores do interior): listar automaticamente. Os casos simples podem ser corrigidos
   (por exemplo, trocar `GetInCell X` por distância a um marcador); o resto vai para revisão manual.
   Interiores com quest crítica que dependa disso ficam como C/X.
4. **Mercadores:** baú de mercador (XMRC) não muda; testar a abertura do comércio.

### Estado (31/10/2026)

- Moradores: ACHR movido com o mesmo formID para a célula persistente do mundo (feito para
  os 30 interiores do experimento).
- Pacotes dos moradores: PLDT tipo 1 -> tipo 3 (near editor location), raio da config.
- **Visitantes**: pacotes de NPCs de fora com PLDT/PTDT tipo 1 agora viram tipo 0 (near ref)
  apontando para um XMarker persistente no centro da navmesh do interior (raio 1024).
  `Master.packages_in_cell()` acha esses pacotes; o writer cria o marcador.
- Flags de trancar/destrancar portas removidas do PKDT.
- **Condições/scripts que testam estar na célula**: detectados (`si/inventory.py` decodifica
  CTDA e olha o parâmetro; `cell_refs.py` gera `out/cell_refs.csv`). 69 células, 310
  referências; entre os 36 convertidos, 2 (GomorrahJoanaRoom, quest VMS21a; e
  HooverDamIntIntakeTower01, pacotes+scripts). O triage rebaixa a categoria e o pipeline
  imprime aviso. Falta a correção automática (reescrever a função/parâmetros da condição) —
  hoje é revisão manual.
- Pendente: `MoveTo` para marcadores do interior em scripts.

## Fase 5: navmesh

1. Transformar a navmesh do interior, tirar os door portals e a flag 0x400.
2. **Dividir por célula com recorte de verdade:** cortar os triângulos na divisa, com vértices
   compartilhados, em vez de separar pelo centroide (que deixa triângulos atravessando a divisa).
   Ligar as metades com NVEX recíprocas.
3. **Ligar à navmesh externa na porta**, para NPCs e companheiros poderem entrar e sair. Exige
   sobrescrever a NAVM externa da célula (conflito com outros mods): oferecer isso como opção
   separada (`SeamlessInteriors - NPC Access.esp`).
4. Registrar tudo no NAVI de forma aditiva (NVMI + NVCI só nossos).
5. Verificador: conectividade (todo triângulo alcançável), divisas recíprocas, grade NVGD cobre
   todos os triângulos, nenhuma ref de furniture/idle marker fora da navmesh.

### Estado (31/10/2026)

- Itens 1, 3 e 4 feitos: navmesh transformada, sem door portal, dividida por célula (pelo
  centroide, ver abaixo), registrada no NAVI aditivo e **ligada à navmesh externa** (NVEX
  recíproco nos dois lados) em 52/52 interiores. A sobrescrita da NAVM externa é acumulada
  por navmesh (uma só por célula) e sai apenas com `--navlink`, num build separado.
- `verify_navmesh.py` cobre reciprocidade **e** NVGD: 100 navmeshes, 1.122 arestas externas,
  0 erros; todo triângulo está na grade da NVGD. (1 aresta unilateral herdada do vanilla é
  anotada, não falha.)
- Bug corrigido no caminho: as flags de aresta externa herdadas da navmesh original (portas)
  sobreviviam ao split com índices NVEX inválidos. Hoje são limpas e recriadas.
- Conectividade: medida como número de ilhas (11 peças com mais de uma, várias de 1
  triângulo) — evidência de que o split por centroide precisa virar recorte de verdade.
- Pendente do item 2: o recorte de verdade na divisa (hoje é por centroide).
- Pendente do item 5: furniture/idle markers fora da navmesh.

## Fase 6: detalhes de experiência

- **Nome do local no HUD:** pesquisar uma forma de mostrar o nome da loja dentro dela (por
  exemplo uma região ou zona de encontro com nome). Hoje aparece "Mojave Wasteland".
- **Iluminação:** testar volumes que escurecem o ambiente dentro do prédio (luzes com raio
  negativo ou ImageSpace modifier em gatilho). Validar o custo em FPS.
- **Desempenho:** cada interior acrescenta ~200 objetos ao mundo aberto. Medir FPS numa cidade
  inteira convertida; se necessário, usar enable parent para ligar o interior só por perto.
- **Map markers e quests** que apontavam para a porta de loading: conferir que os marcadores
  de objetivo continuam apontando para o lugar certo.

## Fase 7: testes automatizados no jogo — em andamento (31/10/2026)

Para cada interior convertido, gerar automaticamente:
- arquivos `bat` de posição: na porta, dentro olhando a porta, cada fachada, os NPCs;
- um roteiro executado por Claude com computer-use:
  1. entrar andando;
  2. medir a posição com `scof` (saída para arquivo, lida e verificada pelo script);
  3. falar e negociar com o mercador;
  4. pegar um item (Steal);
  5. noite → NPC dorme;
  6. sair para longe e voltar;
  7. salvar e carregar;
- **detecção de crash** pelo Visualizador de Eventos (Application, Id 1000) após cada passo;
- screenshots de fachada e de dentro, revisados por amostragem.

Saves de teste por cidade, para pular os avisos das DLCs.

### Estado (31/10/2026)

- `tools/testkit.py` gera os arquivos `bat` de posição de cada interior (fora da porta, dentro
  olhando para a porta, duas laterais) a partir do plano, mais um `LEIA-ME.md` com a tabela e
  o roteiro de conferência. Convenção de ângulo validada contra os bats da v1.
- Falta o roteiro executado (computer-use) e a detecção de crash pelo Visualizador de Eventos;
  ambos dependem de abrir o jogo, que o usuário pediu para não fazer por enquanto.

## Fase 8: liberação por cidade

1. **Goodsprings** inteira: saloon, escola, casas. A casa do Doc Mitchell é a da criação de
   personagem e fica por último, ou é excluída.
2. **Primm** e **Novac**: cidades pequenas, boas para validar o pipeline.
3. Westside e Freeside: worldspaces próprios, com cidades densas.
4. Strip e Vaults: provavelmente excluídos (interiores enormes, quests críticas, vários andares).

Cada cidade é publicada como versão nova: ESP + meshes + LEIA-ME + lista do que foi convertido e
do que foi excluído (com o motivo).

### Estado (31/10/2026)

- `tools/package.py` monta um pacote completo a partir de um build: `dist/<nome>/Data` +
  LEIA-ME gerado (números lidos do próprio ESP) + `.zip`. Primeiro pacote:
  `dist/SeamlessInteriors-v2-teste` — 52 interiores, 84 modelos, 6.007 REFR, 63 navmeshes,
  14,1 MB, ESP com o mesmo hash do build e PASS no validador.
- **Corte por cidade demonstrado** (31/10/2026): `pipeline.py --all --only A B --out <dir>` +
  `package.py --src <dir> --name <nome>` gera um pacote independente só com os interiores
  escolhidos. Testado com GSGeneralStore + GSShackVictorInt: build PASS, validador PASS,
  `verify_build` PASS, pacote de 8 arquivos (6 modelos, 292 REFR, 3 NAVM, 4 CELL) e 0,4 MB.
  O LEIA-ME do pacote herda os números do subset.
- Falta escolher a lista por cidade (Goodsprings/Primm/Novac) e repetir; a mecânica está pronta.

---

## Fase 9: todas as 283 portas (em aberto)

Meta do usuario (31/10/2026): converter **todas** as portas de interior, nao so as 52 atuais.

### Inventario de bloqueios (triagem atual, 283 celulas)

| Bloqueio | Celulas |
|---|---:|
| Terreno acima do piso (porao/caverna) | 102 |
| Sem predio candidato | 54 |
| Interior nao cabe (TARDIS) | 51 |
| Celula multi-porta (nao rigida) | 43 |
| Predio por pecas / encaixe ruim | 1 |
| Convertivel hoje (A/B) | 32 |

### Descobertas que mudam o plano

- **Nao e "predio montado por pecas"**: a sonda `tools/probe_pieces.py` mede a colisao de
  cada REFR vizinho e mostrou **exatamente 1 peca** encostada na porta em todos os casos
  testados (loja, prisao, Gomorrah, Nellis). O problema e a **escolha** da peca.
- **O gargalo e encaixe, nao escolha de predio** (sondado em 000841C8 e 00081CFA): a porta
  fica a 0,4 de `NoVacHouse01` (1.144 x 955 x 327) e a 0,8 de um pod `IndPodSmEndEx01`
  (232 x 256 x 237) — os dois sao as construcoes REAIS. O interior e que e maior que o predio
  (o truque TARDIS do vanilla). Ou seja: o caminho para 100% e **escala do interior**
  (posicoes relativas a porta + XSCL das refs + navmesh), com predio ampliado como alternativa
  nos casos em que encolher deformaria demais.
- A ordenacao da triagem passou a ser **encaixe primeiro, arquitetura como desempate**
  (antes arquitetura vinha antes): 0 dos 52 configs mudaram, 1 celula X->C. Sem regressao.
- `probe_pieces.py` aceita id de celula (acha a porta externa pelo `xtel`).

- **172 celulas C/X tem `fit_xy` > 5** com candidatos que nao sao predios: `IndPodSmEndEx01`
  (sobra 2.320), `IndExtSmDockExSm01` (1.064), `scolRepconnFac01` (2.681), `NoVacHouse01`
  (485). Sao docas, pods e galpoes que contem a porta no AABB.

### Escala necessaria: medida (31/10/2026)

Para as 181 celulas com `fit > 0`, o fator de escala uniforme do interior (`s = dim predio /
(dim predio + fit)`, eixo que mais aperta):

| s necessario | celulas |
|---|---:|
| >= 0,90 (ajuste pequeno) | 16 |
| 0,80-0,90 | 17 |
| 0,70-0,80 | 14 |
| 0,50-0,70 | 46 |
| < 0,50 (menos da metade) | 88 |

Mediana **0,52**. Consequencia dura: **escala nao resolve a maioria** — a 0,52 a porta teria
~104 unidades de altura (o jogador tem ~100) e os moveis ficariam de brinquedo. Encolher so
faz sentido nas 33 celulas com s >= 0,80; as outras 148 exigem **predio ampliado/gerado**
(casca nova em volta do existente), que muda a arquitetura do mapa e e decisao de produto.

### Proximos passos (nesta ordem)

1. **Rejeitar candidato com fit absurdo** (ex.: sobra > 50% da maior dimensao do interior) na
   triagem; re-rodar e **provar por diff que nenhum dos 52 configs muda de predio**. E o passo
   de menor risco e derruba os 172 casos para "sem candidato".
2. **Interior nao cabe (51)**: escala uniforme do interior (refs, navmesh, porta) ou predio
   ampliado.
3. **Multi-porta (43)**: reposicionar a porta interior para coincidir com a externa; o desvio
   de cada uma ja esta em `out/door_pairs.csv`.
4. **Terreno acima do piso (102)**: elevar piso + degrau/rampa; decisao de produto (cavernas e
   esgotos nao tem predio em volta).

### Ferramentas

- `tools/probe_pieces.py <interior>`: distancia da porta a colisao de cada REFR vizinho e
  quantas pecas encostam nela. Base para decidir entre "peca errada" e "sem predio".

## Riscos e decisões em aberto

| Risco | Mitigação |
|---|---|
| Interior não cabe no prédio | Triagem automática (Fase 1); excluir, ou ampliar o prédio (mesh novo) num passo futuro |
| Prédio sem vão na colisão | Degenerar + primitivas de colisão (validar a técnica primeiro) |
| Quests que testam "estar na célula" | Detecção automática; corrigir os simples, excluir os críticos |
| Interiores com vários andares | Categoria C/X; talvez só o térreo, mantendo o loading para os outros andares |
| Conflito com outros mods (navmesh externa, células) | Separar a ligação de navmesh num plugin opcional |
| Desempenho com muitos interiores | Medir por cidade; enable parent por proximidade |
| Saves de versões antigas | FormIDs determinísticos; documentar que é preciso um save antes de instalar |

## Ordem sugerida de trabalho

1. Fase 0, o pipeline genérico, regenerando a General Store idêntica.
2. Fase 1, a triagem dos 249 interiores. **Aqui decidimos o escopo real.**
3. Fases 2–5 aplicadas ao Prospector Saloon (tem NPCs, quests e visitantes: o próximo degrau
   de dificuldade).
4. Resto de Goodsprings e liberação da v2.
5. Primm e Novac, já com a Fase 7 automatizada.
