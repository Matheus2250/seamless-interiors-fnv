# Seamless Interiors (Fallout: New Vegas)

Mod que elimina a tela de loading **de verdade** ao entrar em prédios: o interior é montado
dentro do prédio real no mundo aberto, a porta vira uma porta comum (animada) e o jogador entra
andando. Primeira versão: **Goodsprings General Store** (loja do Chet).

Tudo é gerado por scripts Python próprios (stdlib apenas), sem GECK, FNVEdit, NifSkope, JIP ou
NVSE. Este arquivo registra todo o conhecimento acumulado: formatos, armadilhas, decisões,
o que foi tentado e falhou, e como testar.

Idioma do usuário: português (BR). Ele joga por um teclado ABNT2.

---

## 1. Estado atual (31/10/2026)

> Pipeline v2 (`tools/si/` + `pipeline.py`) reproduz a v1 e passou na triagem dos 283
> interiores; estado por fase nas seções 14, 15 e 16. A versão instalada/jogada continua a v1.

Versão final v1 empacotada em `dist/`. Funciona e foi testado no jogo (por Claude, via
computer-use):
- Porta da loja abre (animação original) e o jogador entra andando, sem loading e sem teleporte.
- Interior completo (197 objetos) dentro do prédio, alinhado à porta real.
- Vista de dentro para fora pela porta aberta e de fora para dentro: correta, sem parede atravessada.
- Fachada, laterais e fundo por fora: nada do interior atravessando (raios de luz removidos).
- Chet nasce atrás do balcão (lugar original), conversa normal, comércio funciona (707 caps).
- Itens da loja mostram **Steal** (vermelho), como no original.
- Noite: o Chet anda do salão até a cama do quartinho e dorme (processo de IA 4), sem crash,
  inclusive depois de sair para longe e voltar (sequência que antes crashava).
- Sair para longe (`cow WastelandNV -10 0`) e voltar: loja e Chet recarregam corretamente.
- Validador: PASS, 0 erros, 0 avisos.

Limitações conhecidas (aceitas na v1):
1. Nome do local no HUD mostra "Mojave Wasteland" em vez de "Goodsprings General Store"
   (a loja agora é parte do mundo aberto).
2. A porta da loja **não é mais trancada à noite** (flags de trancar portas removidas do pacote
   de dormir; ver seção 10).
3. Iluminação: o interior recebe luz/ambiente do exterior. Nos testes ficou aceitável, de dia e
   de noite.
4. ~50 refs com rotação x/y não nula: a rotação z é somada (exata só se Z for a rotação mais
   externa na convenção de Euler do motor; não verificado). São objetos pequenos (livros etc.).
5. O Chet não planeja caminho para fora da loja (navmesh da loja é uma ilha). Ele não sai da
   loja no vanilla, então não faz diferença na prática.
6. Um travamento ("não respondendo") aconteceu uma vez ao viajar com `cow` à noite **enquanto eu
   digitava comandos durante o carregamento**. Repetido sem digitar: não travou. Provável causa
   do teste, não do mod, mas não provado.

---

## 2. Ambiente

| Item | Valor |
|---|---|
| Jogo | `C:\Program Files (x86)\Steam\steamapps\common\Fallout New Vegas` (Steam, FalloutNV.exe 1.4.0.525) |
| Data | `...\Fallout New Vegas\Data` (FalloutNV.esm 245 MB + DLCs: DeadMoney, HonestHearts, OldWorldBlues, LonesomeRoad, GunRunnersArsenal, Caravan/Classic/Mercenary/TribalPack) |
| xNVSE | 6.3.10 instalado (`nvse_1_4.dll`, `nvse_steam_loader.dll`). **Não carrega quando o jogo é aberto pelo launcher via Steam** (`GetModIndex` não existe no console). O mod não depende dele. |
| JIP LN NVSE | **não instalado** (só sai pelo Nexus). O "Script Runner" (`gr_*.txt`) é do JIP, não do xNVSE. |
| plugins.txt | `%LOCALAPPDATA%\FalloutNV\plugins.txt` = `FalloutNV.esm` + `SeamlessInteriors.esp`. Backup do original (vazio): `plugins.txt.bak-seamless`. |
| Saves | `%USERPROFILE%\Documents\My Games\FalloutNV\Saves\` (`SITest.fos` = save de teste) |
| Python | `py` (3.14). `python.exe` não está no PATH. Sempre `py -I script.py`. **Com `-I` o diretório do script não entra no sys.path**: os scripts fazem `sys.path.insert(0, dirname(__file__))`. |
| Shell | Git Bash + PowerShell 5.1 (Windows 11). Caminhos com espaço: aspas. |

**Load order real no jogo:** o launcher da Steam carrega também as DLCs (aparecem avisos
"Honest Hearts Loaded" etc.). Por isso o índice do nosso plugin em runtime **não é 01**, e o
console não acha refs do mod por `prid 01xxxxxx` ("Invalid object reference"). Refs do
FalloutNV.esm (`00xxxxxx`) funcionam no console.

---

## 3. Estrutura do projeto

```
seamless-interiors-fnv/
  CLAUDE.md                 este arquivo
  reference/nif.xml         especificação NifTools (baixada do GitHub niftools/nifxml)
  tools/
    esplib.py               leitor de plugins TES4 (FO3/FNV): mmap, walk, groups, records, subrecords
    build_esp.py            builder ANTIGO (abordagem "loja escondida"); helpers reutilizados
    build_open_esp.py       BUILDER ATUAL: gera tools/out/SeamlessInteriors.esp
    validate_esp.py         validador estrutural do ESP (tamanhos, refs, ONAM, MODL)
    si/                     pacote do pipeline genérico (master, geom, meshes, plan, writer,
                            formids, inventory, triage, terrain)
    pipeline.py             gera o ESP + meshes de todos os configs habilitados
    triage.py               inventário e triagem dos 283 interiores -> out/triage.csv
    make_configs.py         cria tools/buildings/<id>.json a partir do triage
    verify_build.py         checagens geométricas (corredor, colisão, fachada, encaixe)
    regress_v1.py           compara o pipeline com a v1 publicada (PASS)
    scriptc.py              compilador de scripts ObScript (subconjunto vanilla) -> SCDA
    extract_commands.py     extrai a tabela de comandos do FalloutNV.exe -> out/commands.json
    test_scriptc.py         testa o compilador contra os 2.576 scripts do ESM
    slgs_demo.py            compila os scripts da abordagem antiga (demo)
    dump_store.py           dump da célula GSGeneralStore -> out/gsstore.json
    bsalib.py               leitor de BSA v104
    niflib.py               leitor de NIF 20.2.0.7 / BS 34
    nif_strips.py           leitor/escritor completo de NiTriStripsData (round-trip byte a byte)
    nif_report.py           relatório de NIF -> out/nif_report.txt
    cut_door_nif.py         recorta o buraco da porta na fachada visual do prédio
    open_collision.py       anula triângulos de colisão internos do prédio (parede x=608)
    cut_wall_nif.py         recorta a porta nas 2 peças de parede do interior (visual + colisão)
    verify_nif_edit.py      verificador do NIF do prédio
    scripts/                fontes ObScript da abordagem antiga (não usados hoje)
    out/                    saídas (ESP, NIFs, relatórios, caches)
```

### Pipeline completo (ordem importa)

```bash
cd tools
py -I dump_store.py                 # opcional: out/gsstore.json (usado por relatórios)
py -I cut_door_nif.py               # out/meshes/SeamlessInteriors/nv_generalstore_open.nif (do BSA... via arquivo extraído)
py -I open_collision.py             # MESMO arquivo, in-place: degenera a parede de colisão x=608
py -I verify_nif_edit.py            # deve dar "all checks passed"
py -I cut_wall_nif.py               # nvgs_gs_rmcorin01_door.nif e nvgs_gs_rmcorin02_door.nif
py -I build_open_esp.py             # out/SeamlessInteriors.esp
py -I validate_esp.py               # deve dar PASS (avisos de spill para (-17,1) são esperados)
```

**Atenção:** `cut_door_nif.py` reescreve `nv_generalstore_open.nif` a partir do original, então
**sempre rode `open_collision.py` depois dele**. `cut_door_nif.py` lê
`out/meshes/architecture/goodsprings/nv_generalstore.nif` (extraído do BSA por bsalib).

### Versão empacotada

`dist/SeamlessInteriors-v1/` contém `Data/` pronto para copiar e `LEIA-ME.txt`; o mesmo vai em
`dist/SeamlessInteriors-v1.zip`. Gerar de novo depois de mudar algo: rodar o pipeline e copiar os
4 arquivos para `dist/SeamlessInteriors-v1/Data/`.

### Instalação

Copiar para `Data\`:
- `SeamlessInteriors.esp`
- `meshes\SeamlessInteriors\nv_generalstore_open.nif`
- `meshes\SeamlessInteriors\nvgs_gs_rmcorin01_door.nif`
- `meshes\SeamlessInteriors\nvgs_gs_rmcorin02_door.nif`

e ter `SeamlessInteriors.esp` no plugins.txt. **O jogo trava o ESP enquanto está aberto**
("Device or resource busy"): fechar o jogo (`Stop-Process -Name FalloutNV -Force`) antes de copiar.
Conferir com hash (`Get-FileHash`) que a cópia é idêntica.

---

## 4. Histórico de abordagens (o que falhou e por quê)

1. **Esconder a tela de loading** (estilo Clean Loading Transitions do Skyrim, plugin SKSE que
   mantém o último frame). Descartado pelo usuário: ele quer loading **zero de verdade**.
2. **"Loja escondida" debaixo da terra** (build_esp.py): copiar o interior para z ~ -4000 abaixo da
   célula externa e trocar a porta por um script `SetPos`. **Falhou**: o motor puxa o jogador de
   volta para a superfície quando ele fica abaixo do terreno (testado: x/y corretos, z voltou a
   8364).
3. **Loja escondida no céu** (z 20000): gerado, mas o usuário rejeitou o conceito (quer abrir a
   porta e entrar normalmente, sem loja escondida).
4. **Atual: interior dentro do prédio real** (estilo Open Cities): funciona.

Ferramentas dessas fases que continuam úteis: o compilador `scriptc.py` (scripts sem GECK) e o
validador.

---

## 5. Formato TES4 (FO3/FNV) — verificado no FalloutNV.esm

- Header de record: 24 bytes `<4sIIIIHH`: type, dataSize, flags, formID, vcInfo, formVersion (15;
  TES4 usa 2), unknown.
- Header de GRUP: 24 bytes `<4sI4siII`: 'GRUP', groupSize (**inclui** os 24 do header), label,
  groupType, stamp (u32), unknown (u32).
- Tipos de grupo: 0 top, 1 world children (label = WRLD formID), 2/3 interior block/sub-block,
  4/5 exterior block/sub-block, 6 cell children (label = CELL), 7 topic children,
  8 persistent, 9 temporary, 10 visible distant.
- Labels de bloco externo: (int16 y, int16 x). Copiar sempre do master.
- Flag 0x00040000 = comprimido: dados = u32 tamanho descomprimido + zlib. (LAND 00150FC0 tem
  Adler-32 errado; esplib cai para deflate cru.) Escrever sempre **descomprimido** o que mudar.
- Subrecord: `<4sH` + dados. `XXXX` (tamanho 4) dá o tamanho real do próximo subrecord (só em
  um WRLD).
- FormIDs no ESM têm byte alto 00. Novos forms do nosso ESP: 0x01000800 em diante (1 master).
- TES4 do ESP: HEDR (float 1.34, numRecords = records+groups, nextObjectID), CNAM, MAST
  "FalloutNV.esm" + DATA (8 zeros), **ONAM** = lista de formIDs de refs **temporárias** do master
  que o ESP sobrescreve (aqui: 00101CA6, o prédio).
- Ordem dos top groups deve seguir o master: ... STAT ... NAVI, CELL, WRLD, DIAL, QUST, IDLE,
  PACK ... (o builder escreve STAT, NAVI, WRLD, PACK).
- Para sobrescrever algo dentro de um worldspace é preciso escrever a hierarquia toda:
  top WRLD > WRLD (override) > world children (tipo 1) > [CELL persistente do mundo + grupo 6 >
  grupo 8] e [bloco 4 > sub-bloco 5 > CELL > grupo 6 > grupo 8/9].
- **WRLD override sem OFST**: OFST é uma tabela de offsets válida só dentro do master.
- Refs persistentes do mundo aberto ficam todas na **célula persistente do mundo**
  (WastelandNV: CELL 000846EA, direto no world children, sem EDID). Refs temporárias ficam na
  célula da grade.

### Subrecords de REFR/ACHR usados
- NAME base; DATA 6 floats (pos x,y,z; rot x,y,z em radianos); XSCL escala; XTEL 32 bytes
  (porta destino u32, 6 floats pos/rot, flags u32); XNDP (portal de navmesh da porta, só com
  XTEL); XOWN dono (u32); XLOC fechadura; XESP enable parent; XLKR linked ref; XEZN, XRGD, XPRM.
- Ordem padrão (xEdit FNV): EDID, NAME, XEZN, XRGD, XRGB, XPRM, XTRI, XMBO, XTEL, MMRK/FULL/
  CNAM/BNAM/MNAM/NNAM, XSRF, XSRD, XTRG, XLCM, XRDO, **XOWN**, XRNK, XLOC, XCNT, XRDS, XHLP,
  XRAD, ..., XLKR, ..., XESP, XEMI, ..., XIBS, XNDP, ..., XSCL, DATA.
- Propriedade: refs sem XOWN herdam o dono da **célula**. Ao mover para o exterior (célula sem
  dono) é preciso pôr XOWN explícito (senão vira "Take" em vez de "Steal").

### CELL
- DATA 1 byte (0x01 = interior). XCLC 12 bytes no exterior (grid x, y, + 4 bytes).
- XCLL 40 bytes (luz do interior), LTMP template de luz, XOWN dono da célula.

---

## 6. Dados do Goodsprings General Store

| O quê | FormID | Detalhes |
|---|---|---|
| Célula interior | 001057F1 `GSGeneralStore` | XOWN = 00104C78 (GSChet). 208 REFR, 1 ACHR, 1 NAVM. LTMP 0008F584 WastelandInteriorTemplate |
| Porta interior | 00105BD6 | base 000FB730 NVCraftsmanHomeDoor; pos (49.35, 526.74, 544.0) rz 0; XTEL -> 00106373 |
| Porta exterior | 00106373 | mesma base; pos (-69255.27, 3594.0, 8379.29) rz 4.72318696975708; persistente em 000846EA; XTEL -> 00105BD6 dest (-13.77, 598.76, 544.53) |
| Prédio | REFR 00101CA6 | base STAT 00101CA3 `NVGeneralStore` (`architecture\Goodsprings\NV_GeneralStore.NIF`); pos (-69289.6, 3875.4, 8362.1) rz 4.7124 (270°); **temporário** na célula 000DAEB9 |
| Célula externa (-17,0) | 000DAEB9 | WRLD 000DA726 WastelandNV; x [-69632,-65536), y [0,4096) |
| Célula externa (-17,1) | 000DAEB8 | mesmo bloco/sub-bloco que (-17,0); vem antes dela no master |
| Célula persistente do mundo | 000846EA | |
| Chet (ref) | ACHR 00104C79 `GSChetRef` | persistente; XMRC baú de mercador 00104C7A (em célula auxiliar 0008EAE7, não mexer) |
| Chet (NPC) | 00104C78 `GSChet` | script 00161873 GSChetScript (só objetivos no OnDeath); pacotes abaixo |
| Pacotes | 00168FAC GSChetStoreSandbox, 00168FAD GSChetSleep22x8 | ambos PLDT tipo 1 "in cell GSGeneralStore" |
| Navmesh interior | 00107719 | 39 vértices, 41 triângulos, 1 door portal (tri 3, porta 00105BD6) |
| NAVI | 00014B92 | índice global de navmesh |
| Mesh da porta | `dungeons\NV_CraftsmanHomesInterior\NVCraftsmanRmDoorAnimated.NIF` | anim "Open"/"Close"; colisão convexa que acompanha a animação |
| Batente | STAT 0010AC4D NVCraftsDoorframe | **sem colisão** |
| Terreno sob a loja | plano em z 8352 | piso do interior em 8379 (27 acima) |

Só os 2 pacotes do Chet (e o NAVI/NAVM) referenciam a célula 001057F1 no ESM inteiro. Quests
(VMS16, VMS16b) referenciam o Chet pelo formID, que não muda.

---

## 7. Geometria e alinhamento

### Convenção de rotação (Gamebryo)
`world = P + Rz(-rz) · local`, com `Rz(a) = [[cos a, -sin a],[sin a, cos a]]` (matemática padrão).
Equivalente: `local = Rz(+rz) · (world - P)`. Verificado: só essa convenção põe a porta na
fachada do prédio (a outra cai no meio do prédio).

### Alinhamento interior -> prédio
- Centro da folha da porta no espaço do mesh: `c = (-3.0, 64.45, 88.7)` (bounds x -11..5,
  y 0..128.9, z 1..176.4).
- `ext_leaf = ext_pos + Rz(-ext_rz)·c`, `int_leaf = int_pos + Rz(-int_rz)·c`.
- **Δ = (ext_rz - int_rz) + π = 450.62° (= 90.62°)**. A porta interior é a mesma porta vista do
  outro lado (girada 180°). O candidato sem +π põe o interior na rua.
- Para cada ref do interior: `world = ext_leaf + Rz(-Δ)·(p - int_leaf)` (z: `ext_leaf.z + p.z -
  int_leaf.z`), `rz_novo = rz + Δ`.
- Resultado: interior em coordenadas do prédio x [-323, 728], y [-249, 317]; piso em z local 17
  (porch 11..20). O interior praticamente cabe no prédio (o prédio é ~776 × 1610 × 560; caso raro
  e favorável).

### Planta (coordenadas locais do prédio)
- Fachada/porta em x ≈ -285. Salão principal (peças `NVGSGSRm*`) de x ≈ -300 a 465.
- Quartinho dos fundos (peças `Shack*`) de x ≈ 467 a 790, cama em (659, 62), passagem
  `ShackExitRightReg01`.
- Anexo dos fundos do prédio (x > 608, fora do interior): sem piso. Atores que caem ali afundam
  até o terreno.
- Chet atrás do balcão em (358, 76) local = (-69365.6, 4233.3, 8379.6) mundo.

---

## 8. NIF (20.2.0.7, user 11, BS 34) — verificado

- Header: linha "Gamebryo File Format, Version 20.2.0.7\n", u32 versão, u8 endian, u32 user
  version, u32 num blocks, u32 BS version, 3 short strings, u16 num block types + nomes (u32 len +
  chars), u16 índice de tipo por bloco, **u32 tamanho por bloco** (permite fatiar sem entender o
  bloco), strings, grupos.
- NiAVObject flags = **u32** em BS34. NiGeometryData sem material CRC; tangentes/bitangentes
  quando flag 0x1000; UV count = flags & 1 (BSGeometryDataFlags).
- hkPackedNiTriStripsData: u32 num tris, triângulos de 8 bytes (3×u16 + u16 welding), u32 num
  vértices, u8 flag, vértices vec3 em **unidades Havok (× 7 = unidades do jogo)**, trailer de
  sub-parts (50 bytes no prédio, não decodificado).
- bhkPackedNiTriStripsShape: ref de data é o último i32. bhkRigidBodyT: transform assumido
  identidade (bounds batem com o visual a ~6 unidades).
- Cadeia de colisão do prédio: bhkCollisionObject -> bhkRigidBodyT -> bhkMoppBvTreeShape ->
  bhkPackedNiTriStripsShape -> hkPackedNiTriStripsData (295 tris).
- **Truque para remover colisão sem refazer o MOPP:** tornar o triângulo degenerado (os 3
  índices iguais) no lugar. Índices e MOPP continuam válidos; triângulo de área zero não colide.
- **Recorte visual:** clipar os triângulos da parede contra o retângulo da porta no plano da
  parede (4 regiões convexas fora do retângulo, Sutherland-Hodgman em baricêntricas),
  fan-triangulate, novos vértices interpolam posição/normal/tangente/bitangente/cor/UV.
  Escrito mantendo NiTriStrips com **uma strip de 3 por triângulo** (não muda tipos de bloco).
  Só o block-size table do header é corrigido.
- BSA v104: flags 0x87 no Meshes.bsa; flag 0x100 = nome embutido no início do arquivo (bstring a
  pular); bit 0x40000000 do tamanho inverte a compressão padrão. Busca por caminho minúsculo com
  barra invertida.

### Modelos editados
1. `nv_generalstore_open.nif` (prédio): 15 triângulos da parede atrás da porta recortados
   (plano x=-283.18 e as duas faces de x=-284.56/-272.09), retângulo y[-34, 93.5] z[18.5, 193];
   4 triângulos de colisão degenerados (parede interna x=608 e y=-32 no anexo). **A colisão do
   prédio já tinha o vão da porta** (y -34.2..93.7, z até 192.7, plano x=-288.4); quem fecha é a
   colisão da própria porta.
2. `nvgs_gs_rmcorin01_door.nif` / `nvgs_gs_rmcorin02_door.nif`: no interior original a porta de
   loading ficava **encostada numa parede sólida** formada por dois cantos do kit
   (NVGSGSRmCorIn01/02); porta de loading nunca abre, então não há buraco. Essa parede (visual e
   colisão) bloqueava a passagem (o jogador parava sempre em y 3568.13 = plano 3586 - raio ~18).
   Recorte: CorIn02 parede no plano local x≈110-112, rect y[-192,-65.5] z[1.5,175.5]; CorIn01
   plano y≈110-112, rect x[-190.5,-64] z[1.5,175.5]. Inclui faixas inclinadas (rodapé/moldura) da
   mesma banda. 2 triângulos de colisão degenerados em cada. A fachada de colisão do prédio passa
   no mesmo plano (y 3586.6 vs 3588) e segura a parede.
   Esses modelos são aplicados **só** às duas peças da porta (STATs novos SLGSRmCorIn01Door/02Door);
   outras lojas que usam os mesmos modelos não mudam.

---

## 9. Navmesh

### NAVM (FNV)
- Subrecords: NVER (u32 = 11), DATA 24 bytes `<6I`: célula, nº vértices, nº triângulos, nº
  conexões externas, nº cover, nº door portals. NVVX vértices vec3. NVTR 16 bytes por triângulo
  `<3H3hHH`: 3 vértices, 3 arestas (vizinho ou -1), flags, cover flags. NVCA cover (u16 cada).
  NVDP door portals 8 bytes (u32 porta, u16 triângulo, pad). NVGD grade. NVEX conexões externas
  10 bytes `<IIH` (0, navmesh, triângulo).
- Flags do triângulo: 0x0800 comum; **0x0400 = triângulo de porta** (ligado a NVDP);
  **0x1/0x2/0x4 = aresta 0/1/2 é externa**, e então o valor da aresta é índice em NVEX. O outro
  lado aponta de volta (recíproco).
- NVGD: `<Iff3f3f` divisor, tamanho da célula x/y = (max-min)/div, bounds min, bounds max; depois
  div×div células, **x varia mais rápido** (índice = cy*div + cx), cada uma u16 count + u16
  triângulos. Incluir triângulo em toda célula que a bbox xy toca dá um superconjunto seguro.
  **O divisor é função do nº de triângulos** (`1 se <=16, senão min(12, n//50 + 2)`, exato em todo
  o vanilla); nunca copiar o da navmesh de origem (ver seção 19, crash 0x6A55A6).
- NAVM do exterior fica no grupo **temporário** da célula.

### NAVI
- Subrecords: NVER, NVMI (1 por navmesh), NVCI (1 por navmesh).
- NVMI (32 bytes no exterior): u32 flags (0), u32 navmesh, u32 worldspace, int16 grid y, int16
  grid x, 3 floats centro aproximado, u32 0. (Interior: navmesh, célula, u32 0, centro, 0.)
- NVCI: u32 navmesh, u32 n + n navmeshes adjacentes, u32 n + ids (0), u32 n + portas.
- **O motor soma as entradas de NAVI de todos os plugins.** As DLCs sobrescrevem NAVI só com as
  próprias entradas (Gun Runners: NVER + 1 NVMI + 1 NVCI). Fazemos o mesmo: nosso NAVI só tem
  nossas entradas; nada das DLCs é apagado.

### O que fazemos
- A navmesh do interior é transformada para o mundo, o door portal é removido (e a flag 0x400),
  e ela é **dividida em duas pela divisa de células y = 4096** (centroide do triângulo):
  uma na (-17,0) com 29 tris e outra na (-17,1) com 12 tris (formIDs alocados após as cópias;
  hoje 010008C8/010008C9), ligadas por 1 NVEX recíproca. Ilha isolada: não liga com a navmesh
  externa.

---

## 10. NPC (Chet): o que foi necessário, na ordem em que descobrimos

1. Mover o ACHR (mesmo formID) para a célula persistente do mundo, posição alinhada.
2. Pacotes: PLDT tipo 1 (in cell) -> **tipo 3 "near editor location"** (`<iIi` 3, 0, raio).
   Sandbox raio 512, sono raio 1024. Tipos de PLDT (contados no ESM): 0 near ref, 1 in cell,
   2 near current location, 3 near editor location, 6 near linked ref.
3. **Sem navmesh no prédio o motor empurra o ator para a navmesh mais próxima (fora).**
   Copiar a navmesh do interior.
4. **NAVI**: registrar a navmesh (seção 9).
5. **Ordem de carregamento**: o Chet é persistente e carrega com o mundo; peças temporárias só
   com a célula. Sem chão ele caía até o terreno (z 8355) e era empurrado. Solução: as 10 peças
   estruturais (`NVGSGSRm*`, `ShackCornerIn*`, `ShackExit*` e as 2 peças da porta) são
   **persistentes** (grupo 8 da célula persistente do mundo).
6. **Divisa de células**: o motor procura a navmesh **da célula em que o ator está**. O Chet
   (y 4233) está na (-17,1); com a navmesh registrada só na (-17,0) ele era jogado para a
   navmesh externa da (-17,1). Teste que provou: mover o ponto dele para y 4056 -> nasceu dentro.
   Solução definitiva: dividir a navmesh por célula (seção 9). Testado: nasce atrás do balcão.
7. **Crash à noite** (`0xC0000005` em `FalloutNV.exe+0x6A55A6`, sempre o mesmo offset, registrado
   no Visualizador de Eventos, Application, Id 1000/1001): acontecia quando o pacote de dormir
   começava e o Chet precisava calcular caminho do salão até a cama. Causa: **refs temporárias
   gravadas no grupo da célula errada**. A cama (e ~70 peças) fica fisicamente na (-17,1) mas
   estava no grupo da (-17,0); o motor resolve a navmesh do alvo (a cama) pela célula-mãe dele, não
   acha triângulo e lê ponteiro nulo. Correção: cada ref temporária vai para o grupo da célula
   em que está (`c.new_pos[1] < 4096` -> (-17,0), senão (-17,1)). Testado várias vezes: sem crash.
   (Também removi as flags de trancar portas do pacote de dormir, PKDT 0x88 -> 0: lock doors at
   start / unlock at end. Suspeita inicial; um teste com elas removidas ainda crashou, então não
   eram a causa, mas foram mantidas fora porque a porta não é mais porta de loading.)
   O FalloutNV.exe da Steam tem **DRM SteamStub** (código cifrado no arquivo): análise estática
   do endereço com Ghidra/REA não funciona nele (bytes aleatórios). A `fnv-rea` do usuário usa o
   exe da GOG.

### Regra geral derivada
**Toda ref temporária deve ficar no grupo da célula que contém a sua posição**, exatamente como o
GECK faz. Atores e pacotes resolvem navmesh/célula a partir disso.

---

## 11. Compilador de scripts (scriptc.py) — para quando precisar de scripts

Não é usado na versão atual, mas funciona e é a forma de ter scripts sem GECK.
- Tabela de comandos do FalloutNV.exe: CommandInfo de 40 bytes (longName, shortName, opcode,
  help, needsParent u16, numParams u16, params*, execute, parse, eval, flags), ParamInfo 12
  bytes; início no offset de arquivo 0xD8BEF0, fim no sentinela "ADD NEW FUNCTIONS BEFORE THIS
  ONE!!!". 884 comandos -> out/commands.json. Blocos 0x00-0x25, funções a partir de 0x1000.
- Bytecode: `opcode u16, len u16, data`. scn = `1D 00 00 00`; Begin `10 00 len blockType u16
  blkLen u32 ...`; End `11 00 00 00`; return `1E`; if/elseif `16/18 len jmpOps exprLen expr`;
  else `17 00 02 00 jmpOps`; endif `19`. Ref prefix `1C 00 refIdx`. Expressões RPN (literais e
  operadores em ASCII com espaço; var `20 66|73 idx`; ref `20 72 idx` + `58 opcode len ...`).
  Params: axis 1 byte; int `6E`+i32; float `7A`+double ou `66`+var; forms `72`+refIdx.
- SCRO: refs locais primeiro, depois SCRO 1-based. `player` e `PlayerRef` = 0x14 (0x14 não existe
  como record; o validador tem exceção).
- Teste: 1.168 scripts no subconjunto; com a ordem SCRO original, SCDA idêntico em 1.053
  (resto é histórico do GECK: ordem de SCRO, varCount).

---

## 12. Testes no jogo (como Claude testa)

- Usar computer-use com acesso a `Fallout New Vegas`, `Steam`, `falloutnvlauncher.exe` e
  `falloutnv.exe`.
- Abrir: `Start-Process "steam://rungameid/22380"` -> launcher -> clicar **Play** em (916, 346)
  no frame 1456×819 (tirar screenshot antes, senão o clique "cai no desktop"). Se o launcher não
  vier para frente: ShowWindow/SetForegroundWindow via PowerShell (Add-Type user32).
- **Teclado:** o console é a tecla `'` (ABNT2). O jogo lê teclado por DirectInput e perde
  toques rápidos: usar `hold_key` com 0.08–0.15 s. `type` (texto) **não funciona**.
- **Mouse** é relativo: clique por coordenada só funciona em menus; para botões OK de avisos,
  mover o mouse até o botão (dois mouse_move) e `left_click`. Enter às vezes não fecha aviso.
- **Arquivos de comando (`bat`)**: escrever `.txt` com CRLF na pasta do jogo e rodar
  `bat <nome>` no console. Existentes: `g` (cow WastelandNV -17 0), `l` (load SITest),
  `s` (save SITest), `o` (na frente da porta, olhando para ela), `p` (getpos do jogador),
  `k` (posição e pacote do Chet), `h` (repõe o Chet no lugar), `c`, `f` (de frente para o
  Chet), `i` (mirar num item), `v1` (dentro olhando a porta), `v2` (fachada), `v3` (lado oeste),
  `v4` (lado leste), `n` (set gamehour to 23), `r` (cow WastelandNV -10 0), `j` (moveto Chet),
  `1`-`6`, `d`, `q`, `m`, `t` (testes antigos de disable/tcl).
- `scof "arquivo"` precisa de aspas (sem aspas: "Missing parameter String").
- `setpos` só aparece no `getpos` no quadro seguinte.
- `tcl` com um ref selecionado alterna só a colisão daquele ref (aparece "Ref '...' Collision");
  para noclip do jogador não pode haver seleção. `disable` é mais confiável para testes.
- **Avisos das DLCs** aparecem a cada `cow` a partir do menu. Usar o save `SITest` (`bat l`), que
  já passou por eles. Cuidado: o save guarda a posição de refs **persistentes** (como o Chet);
  para testar mudança de posição de persistente, começar do zero com `bat g`.
- Os botões "E) Steal/Take/Talk" no HUD mostram propriedade e interação.
- A janela do Claude pode roubar o foco do teclado; trazer o jogo de volta com
  SetForegroundWindow antes de digitar.

---

## 13. Generalização para todos os interiores (plano)

Para cada interior com porta de loading para o mundo:
1. **Encaixe:** alinhar pela porta (seção 7) e medir se cabe no prédio (OBND das peças vs. bounds
   visuais/colisão do prédio). Muitos não cabem ("TARDIS"); ficam de fora ou ganham prédio maior.
2. **Prédio:** recortar a fachada visual atrás da porta; checar se a colisão já tem o vão (aqui
   tinha); degenerar paredes internas de colisão que cortem o interior.
3. **Interior:** achar a parede sólida atrás da antiga porta de loading e recortá-la (visual e
   colisão) nas peças do kit; usar STATs/modelos novos só nessas refs.
4. **Refs:** copiar com transformação; manter XOWN/XLOC; pôr o dono da célula nos sem dono; não
   copiar NorthMarker, portas de loading e efeitos de luz de janela (`IndFXLightRays*`,
   `FXLightBeam*`); gravar cada ref temporária na célula que contém sua posição.
   Pacotes: remover flags de trancar portas (PKDT 0x08-0x100) dos pacotes do local.
5. **NPCs:** mover ACHRs (mesmo formID) para a célula persistente; pacotes "in cell" -> "near
   editor location"; peças estruturais persistentes.
6. **Navmesh:** transformar, remover door portals, dividir por célula, registrar no NAVI aditivo.
7. Testar cidade por cidade no jogo (Goodsprings, depois Primm, Novac).

Riscos em escala: interiores que não cabem, prédios com vários interiores/andares, scripts e
quests que testam "estar na célula", iluminação.

---

## 14. Fase 0 (pipeline genérico) — FEITA (31/10/2026)

Novo pacote `tools/si/` + `tools/pipeline.py` (ver PLANO-GERAL.md):
- `si/master.py` (lookups no ESM, células por grade), `si/geom.py` (convenção de rotação,
  alinhamento com escolha automática do flip), `si/formids.py` (registro estável
  `tools/formids.json`), `si/meshes.py` (BSA, colisão em mundo, `doorway_cut` automático,
  `cut_visual`, `degenerate_collision`, `visual_bounds`), `si/plan.py` (BuildingPlan: tudo
  detectado a partir de `tools/buildings/<id>.json`), `si/writer.py` (ESP genérico, N interiores).
- Config da loja: `tools/buildings/GSGeneralStore.json` (porta externa, prédio, raios dos pacotes).
- `py -I pipeline.py` -> `tools/out/build/Data/` (ESP + meshes em
  `meshes/SeamlessInteriors/<id>/`), valida (PASS, 0 avisos).
- Detecção automática reproduz a v1: 197 cópias, mesmos 11 pulados, mesmas 2 paredes da porta
  (detectadas por segmentos atravessando a porta, não por caixa — senão um pilar na borda era
  recortado), parede interna x=608 removida pelo teste "colisão sobre a área caminhável da
  navmesh" (a de y=-32 no anexo não é removida; correto), Chet e pacotes iguais.
- Centro da folha da porta medido nos vértices do modelo (`visual_bounds`); OBND arredondado
  causava ~1 unidade de erro (dobrado pelo flip de 180°).
- Peça estrutural = STAT com colisão e modelo em `meshes\dungeons\` ou `meshes\architecture\`
  (inclui pilares, uma cadeira e uma lâmpada a mais que a v1; inofensivo).

Regressão contra a v1 (`py -I regress_v1.py`, PASS):
- As 197 cópias são pareadas por proximidade (a v1 usava posição arredondada a 0,1 e o desvio
  real é 0,047, então o arredondamento separava os pares). Mesmo assim 3 falhas que o script
  antigo listava eram falso positivo: o pareamento, a comparação de navmesh (mesmo
  arredondamento) e o casamento de meshes (o script pegava o primeiro mesh cujo nome começava
  com "nvgs", então comparava a CorIn02 com a CorIn01!).
- Casamento de mesh agora por nome canônico (sem pasta, sem .nif, sem sufixo _door ou _<formID>):
  v1 nvgs_gs_rmcorin02_door.nif <-> novo nvgs_gs_rmcorin02_0010801E.nif.
- As 2 meshes das paredes da porta removem os mesmos triângulos de colisão da v1. No prédio a
  v1 degenerava [39,40,220,275]; o pipeline remove só [39,40] e mantém 220/275 (parede do
  anexo em y=-32), que é o correto: o teste de colisão sobre a área caminhável da navmesh.
- 14 refs ficaram persistentes a mais que na v1 (pilares, cadeira, lâmpada com modelo em
  meshes\architecture\ ou dungeons\); aceito (inofensivo).
- Falta só o teste no jogo com os caminhos novos de mesh. O usuário pediu para **não abrir o
  jogo** por enquanto; a v1 continua sendo a versão instalada.

Cuidado com escapes: substituições via heredoc+Python quebraram strings com \ duas vezes;
preferir a ferramenta Edit para literais com barras invertidas.

---

## 15. Fase 1 (inventário e triagem) — FEITA (31/10/2026)

### Ferramentas

```bash
cd tools
py -I si/inventory.py --refresh --stats   # out/inventory.json (8 s, ~18 MB)
py -I triage.py                           # out/triage.csv (283 linhas, ~2,5 min)
py -I triage.py --only 001057F1 --verbose # um interior, com detalhe
```

`si/inventory.py` varre o ESM uma vez: bases DOOR/STAT/SCOL, todas as refs de porta (2.758),
as células internas com porta para um worldspace (**283**, 247 no WastelandNV), todas as refs
dessas células (94.595), os STATs/SCOLs exteriores a até 2.500 de cada porta (30.236) e as
referências à célula (pacotes PLDT/PTDT tipo 1, scripts com SCRO apontando para refs da célula,
menções da célula em QUST/INFO, portas que entram).

`si/triage.py` para cada interior: acha o prédio, alinha pela porta (mesma convenção da seção
7), mede o encaixe, o terreno e a complexidade.

### Como o prédio é escolhido (e onde isso falha)

- Candidatos: STAT/SCOL exteriores cujo AABB (bounds reais do NIF, não OBND) contém o centro da
  folha da porta, com mínimo de 100x100x100. Entre eles, ganha o que **contém mais do interior**
  (overflow do casco convexo das peças com colisão), com preferência para modelos de
  architecture/dungeons/scol sobre rochas.
- **Muitas construções do FNV não são uma mesh única** (prisão de NCR, Big MT, hangares de
  Nellis, cassinos): são montadas com peças de kit. Nesses casos o candidato é uma peça pequena
  (doca, entulho, rocha) e o overflow medido é grande por construção. A triagem marca isso como
  C/X, mas o motivo real é "não há prédio único".
- O casco do interior é o casco convexo das peças **com colisão** (paredes, piso, rochas).
  Decalques, planos de luz, marcadores e triggers não têm colisão e são ignorados no encaixe
  (vão para a coluna fit_other).

### Terreno: LAND/VHGT (decodificação nova, verificada)

- VHGT = float offset + 33x33 bytes int8 + 3 bytes. A linha 0 acumula em x; cada linha seguinte
  começa com o delta em relação ao primeiro valor da linha anterior e o resto acumula em x.
  A linha 0 é o lado y mínimo da célula. **altura = (offset + acumulado) * 8**.
- Interpolação bilinear; erro mediano de 0,25 unidade contra refs apoiados no chão de
  Goodsprings, e o ponto da loja dá exatamente 8352 (o valor medido no jogo).
- **É por isso que o piso do interior é comparado com o terreno**: se o terreno está acima do
  piso, o interior fica enterrado (porões, cavernas, esgotos).

### Resultado (283 interiores)

A=12, B=23, C=131, X=117. Colunas do CSV: célula, EDID, mundo, grade, portas, refs, atores,
portas para fora, portas para outros interiores, pacotes, scripts, quests, portas que entram,
prédio (ref, EDID, modelo), número de candidatos, encaixe (fit_xy = casco com colisão,
fit_other = decalques/props), z acima/abaixo, tamanhos e notas.

Achados que mudam o plano:
1. **TARDIS já em Goodsprings.** A loja é a exceção: casa 02 tem interior 1.274 de
   profundidade contra 887 de construção (sobra 380 no fundo); o saloon é 2.025x1.427 dentro de
   1.941x1.194 (sobra 140). Ou seja: os outros interiores de Goodsprings não passam só pelo
   pipeline — precisam encolher, aceitar sobra ou ganhar prédio maior.
2. Terreno acima do piso é o sinal mais comum de inviabilidade (64 dos 283): porões, esgotos,
   cavernas.
3. 62 células têm mais de uma porta para fora; a triagem testa até 4 portas e usa a de melhor
   encaixe (nota "best of N doors").

---

## 16. Fase 2 (geometria) — em andamento (31/10/2026)

### Ferramentas novas

```bash
cd tools
py -I make_configs.py --categories A B      # tools/buildings/<id>.json a partir do triage
py -I pipeline.py                           # só os configs sem "enabled": false (release)
py -I pipeline.py --all --out out/exp/ab    # inclui os experimentais
py -I verify_build.py [--only ID ...]       # checagens geométricas, sem gravar nada
```

Config novo nasce com `"enabled": false` quando é experimental (make_configs). O pipeline
padrão ignora esses; `--all` inclui. Assim a release (`out/build/Data`) continua só a loja.

### O que `verify_build.py` checa (por config, sobre o plano, sem gerar arquivos)
1. **Vão livre**: raios através da abertura (referencial da porta, ±35% da largura, alturas do
   corpo 18–66 acima do limiar) disparados contra as **meshes recortadas** que serão publicadas;
   peças baixas/finas (arame de armadilha) e armadilhas não contam.
2. **Parede invisível**: nenhum triângulo de colisão do prédio, depois dos cortes, fica dentro
   do volume caminhável (mesma conta da navmesh do interior).
3. **Fachada aberta**: cobertura visual do retângulo da porta no mesh do prédio ~0.
4. **Encaixe**: peças com colisão (casca) dentro dos bounds do prédio; decoração à parte.
5. **Atores**: nota (não falha) para ator que não está sobre um triângulo da navmesh ou cuja
   altura difere >40 — no vanilla há atores em cima de móveis; o que quebrava na v1 era não ter
   navmesh nenhuma, e isso os outros checks pegam.

### Bugs corrigidos nesta rodada
- **Escala do REFR do prédio (XSCL)**: alguns prédios do master estão em 0,9 ou 1,1
  (BlackMountainPrison, SLCoyoteMineHouse, SLniptonRoadMarket). O teste de colisão e o cálculo
  do retângulo da porta ignoravam a escala — removiam triângulos errados e testavam no lugar
  errado. Agora `BuildingPlan.bld_scale` entra em `to_world` e no retângulo local.
- **Parede da porta mais afastada que a folha**: o pipeline só recortava na faixa de ±20 ao
  redor do plano da folha; em vários interiores a parede sólida fica 30–120 atrás (recesso).
  Agora a banda é adaptativa: calculada das próprias faces que cruzam o corredor (±10).
- **Corredor da porta**: profundidade 60 -> 130 e largura proporcional à folha. Ficou
  superado pelos raios da abertura (abaixo), que são a definição usada hoje.
- **Writer**: recusa converter dois interiores no mesmo REFR de prédio (o mesh é sobrescrito).
  `make_configs.py` já escolhe um por prédio (o de melhor encaixe).
- **Validador generalizado**: aceita qualquer worldspace do master (antes era só 000DA726) e
  verifica a regra "ref temporária no grupo da célula que contém a posição" em qualquer célula
  externa (antes só a (-17,0) da loja). Os avisos de spill sumiram.

### Experimento de escala (29 configs de categoria A/B com 1 porta e sem links) — 30/30

- Um ESP com 30 interiores (3.536 REFR, 37 navmeshes, 4 worldspaces) passa no validador
  estrutural (PASS, 0 erros) **e** no `verify_build` (30/30).
- O problema dos 6 reprovados era o **referencial**: o corredor da porta era alinhado aos eixos
  do mundo, mas as portas das shacks ficam a 30–40°. O corredor então cortava o vão torto:
  selecionava peças ao lado da porta e deixava a parede real no caminho.

### Porta: referencial e raios (a correção que fechou os 6)

- `BuildingPlan.door_frame_world()`: normal e lado da folha a partir do OBND (o eixo fino é a
  normal). Tudo que é perpendicular à porta usa esse referencial, não os eixos do mundo.
- `door_opening_paths()`: 5 posições laterais (+-35% da largura) x 4 alturas do **corpo do
  jogador** (18, 34, 50 e 66 unidades acima do limiar), +-130 ao longo da normal. É a mesma
  definição de "passagem livre" usada para escolher o que cortar e para verificar depois.
  Altura do corpo importa: um pilar ou viga a 129 unidades do chão não impede ninguém de entrar
  (a coluna BasRmFreeCol01 da loja é assim — a primeira versão dos raios a cortava por engano).
- `doorway_cut(..., normal, side)`: quando a peça está girada mais de 1,5° em relação à porta,
  o retângulo é calculado no referencial da porta (produto escalar), não no AABB da peça.
  Para a loja (0,62°) o caminho antigo continua valendo, e as 3 meshes saíram **byte a byte
  iguais** (hash conferido) — a release não mudou.
- Filtros de parede: só STAT/SCOL, altura do casco >= 100 e EDID que não comece com
  `Trap`/`Tripwire` (um arame de armadilha atravessa o vão e não se corta buraco nele).
- `door_box()`: portas com **OBND zerado** (OffDoorWoodStatic do WinsResidence tem
  (0,0,0,0,0,0)) passam a usar os bounds do modelo; antes a folha era um ponto e os raios
  saíam degenerados.
- `verify_build` agora dispara os raios contra as **meshes que vão ser publicadas** (as
  recortadas), não contra o plano usado para escolher os cortes.

### Várias portas por célula (31/10/2026)

- Config aceita `"exterior_doors": [fid, ...]` (ou o antigo `"exterior_door"`). Todas as portas
  da célula são convertidas: cada uma ganha o vão na fachada do mesmo mesh, e a parede atrás
  recebe um corte por porta (uma peça atingida por duas portas acumula os dois cortes).
- **O interior é um corpo rígido**: uma única transformação tem que levar cada porta interior
  para baixo da sua porta exterior. O vanilla frequentemente "trapaceia" (a porta de loading
  teleporta e gira o jogador), e aí nenhuma colocação rígida funciona. Quando a segunda porta
  cai a mais de 30 unidades ou 5° da sua interior, o pipeline **recusa** a célula com mensagem
  clara em vez de gerar conversão torta (`door_agreement` / `door_turn` na config afrouxam).
- `py -I door_pairs.py` lista as 62 células com mais de uma porta, o pior desvio de cada uma e
  se são rígidas (`out/door_pairs.csv`). Resultado da varredura: **quase nenhuma é**. Entre as
  que cabem, só `CampMCTermInt04` (2 portas, 18 unidades) e `CottonwoodCoveHQ` (16 unidades,
  mas folga 162) são rígidas. A maioria erra 100–9000 unidades ou 180°.
- Por isso a regra nova: célula multi-porta só entra com `--multi-door` no `make_configs.py` e
  passa pelo teste; caso contrário fica de fora (categoria C).

### Visitantes (pacotes de fora, Fase 4)

- Pacotes de NPCs de fora que mandam o ator "na célula X" (PLDT/PTDT tipo 1) apontavam para
  uma célula que não existe mais no mundo aberto. Agora viram **tipo 0 (near ref)** apontando
  para um **XMarker persistente** (STAT 0000003B) no centro da navmesh do interior, raio 1024.
- O marcador vai para a célula persistente do mundo e recebe formID do registro
  (`<id>:visitor_marker`), como todo o resto.
- Pacotes dos próprios atores da célula continuam virando tipo 3 (near editor location), com o
  raio da config.
- Afetou 3 dos 30 interiores do experimento (CrimsonCaravanBarracks01, NovacCliffsRoom,
  QJBarracks). Conferido no ESP: 3 marcadores persistentes e 4 pacotes com tipo 0 e raio 1024.

### Release reprodutível

- `Registry.next_object_id()` agora conta só os formIDs que **este build** escreveu. Antes usava
  o registro inteiro, então o `nextObjectID` do TES4 (e o hash do ESP) mudava toda vez que um
  experimento alocava um id. Testado: build da loja, build `--all`, build da loja de novo →
  hash idêntico nos dois builds da loja.

### Vários interiores no mesmo prédio (31/10/2026)

- Configs que apontam para o **mesmo `building_ref`** passam a compartilhar um mesh: o pipeline
  agrupa os planos por REFR de prédio (`si/plan.SharedBuilding`), cada plano calcula só os seus
  `facade_cuts` e o grupo recorta todos os vãos num mesh só e remove a colisão interna do volume
  caminhável de **todos** os interiores.
- O writer emite o STAT do prédio e a sobrescrita do REFR **uma vez** (líder = primeiro config do
  grupo, para os formIDs continuarem estáveis) e valida que os demais apontam para o mesmo mesh.
- `verify_build.py` agrupa igual e agora checa a cobertura da fachada **por interior** (antes
  usava o máximo do grupo, o que esconderia um vão não recortado).
- `make_configs.py` deixou de escolher um interior por prédio: agora todas as salas de um prédio
  viram config e o pipeline junta.
- Resultado no conjunto A/B: 36 interiores em 30 prédios (antes 30). Cinco prédios são
  compartilhados: NoVacMotel 000864EA com 3 salas (23 faces recortadas, 56 triângulos de
  colisão internos removidos), NoVacMotel 00177837 com 2, os dois NVHooverDamUpper com 2 cada e
  OverpassCurveLo01 com 2.
- Conferido no ESP do experimento: 0 formIDs duplicados, 1 sobrescrita do REFR do motel
  (000864EA) apontando para o STAT compartilhado, e os STATs das paredes da porta continuam um
  por interior.

### Navmesh ligada ao exterior (Fase 5.3, 31/10/2026)

- `tools/navlink_report.py`: para cada config, a distância da porta até a navmesh **externa** da
  célula da grade (não a persistente!) e até a borda da navmesh **interna**. Todas as 52 ficam
  a 5–116 unidades de uma e 3–85 da outra → link viável em 52/52 (`out/navlink.csv`).
- `building` agora tem `"navlink": true` (o pipeline põe em todo mundo com `--navlink`):
  `si/navlink.py` escolhe a aresta mais próxima da porta nos dois lados.
- **Interior**: a peça que contém o triângulo escolhido ganha a flag de aresta externa e uma
  entrada NVEX apontando para (0, navmesh externa, triângulo externo).
- **Exterior**: uma **sobrescrita da NAVM** da célula (mesmo formID) com a aresta marcada e o
  NVEX recíproco. Vários interiores da mesma célula compartilham a mesma NAVM: as ligações são
  acumuladas e escritas **numa sobrescrita só** (`flush_navlinks`), senão sairiam duas NAVMs com
  o mesmo formID — foi exatamente o erro (11 erros do validador) da primeira versão.
- Aresta já usada por outro interior é recusada com nota (`_claimed_edges`), para não haver duas
  ligações no mesmo par triângulo/aresta.
- Resultado: **49 dos 52** interiores ficam ligados. Os 3 restantes (GomorrahJoanaRoom,
  HooverDamIntIntakeTower02/03) não têm aresta de borda perto da porta (a escolha cai numa
  aresta interna, que não pode ser ligada) e ficam sem ligação, com nota no build.
- `tools/verify_navmesh.py` (novo): confere **reciprocidade** de toda aresta externa do plugin,
  seguindo o NVEX até a navmesh de destino (no ESP ou no master) e exigindo a volta.
  No build com links: 100 navmeshes, 1.122 arestas externas, **0 erros**.
  Ele também separa o que é herdado do master: o vanilla tem 1 aresta unilateral
(00137AC2 tri 168 → 0013449A tri 316) e ela é ignorada (nota, não falha).
### Navmesh: flags herdadas, grade e ilhas (31/10/2026)

- **Bug real encontrado pelo verificador:** ao copiar um triângulo, as flags de aresta
  externa (`0x1/0x2/0x4`) e o valor da aresta vinham da navmesh original — mas o NVEX é
  **reconstruído** no nosso registro, então aquelas arestas apontavam para índices que não
  existem mais (restos de portal de porta). Correção: limpar `0x400` (porta) e `0x7` (arestas
  externas) e recriar só o que o nosso split/link justifica; aresta de borda fica `-1`.
  O release da loja saiu **byte a byte igual** (o portal dela não usava aresta externa).
- `verify_navmesh.py` agora também confere a **NVGD** (divisor, 36 bytes de cabeçalho, célula
  = u16 n + n índices; x varia mais rápido): todo triângulo do plugin está listado na grade.
- Conectividade virou **contagem de ilhas** (nota, não falha): 11 peças têm mais de uma ilha,
  várias com 1 triângulo só. Isso é o efeito esperado do split **pelo centroide** (Fase 5.2,
  ainda pendente: recortar de verdade na divisa). Como as ilhas se ligam por NVEX, o
  caminho continua existindo.
- O plugin com links sai de um build separado (`pipeline.py --navlink --out ...`), como o plano
  pede: sobrescrever NAVM do master conflita com mods de navmesh.

### Triagem: o prédio tem que tocar a porta (31/10/2026)

- A regra antiga (AABB do candidato contém a folha) aceitava **postes, placas e entulho**: um
  poste de utilidade tem bounding box de 1.620 x 3.099 e "continha" a porta de uma sala de
  Novac, virando o prédio da conversão.
- Agora o candidato precisa ter **colisão a até 90 unidades da folha** (`WALL_REACH`): a parede
  de um prédio está no plano da porta, a de um poste não. Distância ponto-triângulo (Ericson)
  com índice de triângulos em células de 128 unidades (`CollisionIndex`), cacheado por ref.
- Efeito: a sala de Novac voltou a ser do **NoVacMotel 000864EA** (que já hospedava 3 salas —
  agora 4) e ~40 células perderam candidatos falsos.
- Distribuição nova do triage: **A=18, B=14, C=104, X=147** (antes A=21/B=32/C=124/X=106).
  O X cresceu porque "não há prédio com parede na porta" passou a ser medido, não suposto.
- Com isso o conjunto de configs A/B subiu para **52 interiores**; o build `--all` gera
  6.007 REFR, 63 navmeshes, 58 atores e passa no validador e no `verify_build` (PASS/PASS).

### Condições e scripts que citam a célula (Fase 4.3, 31/10/2026)

- `si/inventory.py` agora decodifica **CTDA** (28 bytes: `<u8 tipo, u8, u16, f valor, u32 função,
  u32 param1, u32 param2, u8 runOn, u8x3, u32 referência>`) e guarda toda condição cujo
  parâmetro é a célula interior **ou uma ref dela** (QUST/INFO e PACK), mais os scripts cujo
  SCRO cita a célula ou uma ref dela.
- **A tabela de funções do CTDA não é a mesma dos comandos de script**: os opcodes conhecidos
  (GetInCell 4163, GetIsID 4168...) não aparecem em nenhuma posição do registro. O que funciona
  é olhar o **parâmetro** (formID): uma condição que recebe a célula é quase sempre GetInCell.
  Nos 4.000 CTDA amostrados, o índice 67 domina entre os que recebem célula (é o GetInCell);
  o relatório agrupa por índice em vez de adivinhar nomes.
- `py -I cell_refs.py` -> `out/cell_refs.csv` com célula, registro (QUST/INFO/PACK/SCPT),
  EDID, índice de função, parâmetro e se é célula ou ref. 69 células, 310 referências.
- O triage ganhou `n_cell_conds`, `n_cell_scripts` e `cell_refs`: condição de quest/diálogo
  rebaixa para **C** (−25 na nota), condição de pacote ou script citando a célula rebaixa para
  **B** (−8 / −5). O pipeline imprime `WARNING: N condition(s) and M script(s) name this cell`.
- Entre os 36 configs, só **2** são afetados: `GomorrahJoanaRoom` (10 condições da quest
  VMS21a na célula; virou C) e `HooverDamIntIntakeTower01` (4 condições de pacote sobre refs da
  célula + 2 scripts; B). Os outros 34 não têm nenhuma referência.
- Ainda não há **correção automática**: reescrever `GetInCell` exigiria trocar função/parâmetros
  de bytecode de condição; o caminho é revisar caso a caso (ou excluir), como os dois acima.

### Test kit para o jogo (Fase 7)

- `py -I testkit.py` gera `out/testkit/` com 4 arquivos `.bat` por interior (fora da porta,
  dentro olhando para a porta, duas vistas laterais) e um `LEIA-ME.md` com a tabela e o
  checklist. 36 interiores, 0 recusados.
- Coordenadas calculadas do plano: a de fora é `porta - normal*260` (z = piso+5), a de dentro é
  o centróide da navmesh (z do triângulo+5). O ângulo segue a convenção **já testada no jogo**
  (bats da v1): `forward = (sin z, cos z)`, então `z = atan2(dx, dy)` em graus — conferido
  contra os bats `o` (-69320, 3500, z 0) e `v1` (-69320, 3880, z 180) da loja.
- Arquivos em CRLF (o console exige) e ASCII.

### Empacotamento dos builds (Fase 8, 31/10/2026)

- `py -I package.py` monta `dist/SeamlessInteriors-v2-teste/` (Data + LEIA-ME) e o `.zip`
  (14,1 MB): 52 interiores, 84 modelos, 6.007 REFR, 63 navmeshes. O ESP empacotado tem o
  **mesmo hash** do build (`C9E77E8B…`) e passa no validador.
- O LEIA-ME é gerado com os números do próprio build (contagem de registros pelo esplib),
  então não desatualiza; os limites conhecidos (HUD, luz, 2 interiores com condição de
  célula, 3 sem navmesh ligada) estão listados nele.
- `dist/SeamlessInteriors-v1/` continua sendo a única versão jogada; a v2-teste é o
  candidato a teste no jogo.

### Escala do interior (Fase 9, 31/10/2026)

Objetivo do usuário: converter **todas** as portas. Medição que define o caminho: para as 181
células com `fit > 0`, o fator de escala uniforme necessário tem mediana **0,52** (88 células
abaixo de 0,50) — encolher não resolve a maioria; só 33 células ficam com `s >= 0,80`, onde
o encolhimento é discreto.

- `geom.Alignment` ganhou `scale` (posições relativas à folha da porta):
  `world = leaf + s·Rz(-delta)·(p - int_leaf)`.
- `BuildingPlan.int_scale` vem da config (`"scale": 0.9`), padrão 1.0; vai para
  `choose_alignment(..., scale=...)`.
- `writer.copy_record` multiplica o `XSCL` das refs por `int_scale` e insere um `XSCL` quando a
  ref não tinha e `s != 1` (antes do `DATA`).
- Verificado com `ScaleTest09` (cópia da config de NellisBarracks03 com `scale: 0.9`): build
  PASS, `verify_build` PASS, validador PASS, 147 refs com XSCL (0,216 a 1,188 = escala
  original × 0,9). A config de teste foi apagada depois para não duplicar o prédio.
- Build da loja segue **byte a byte** igual (`5980B0D0…`) com `s = 1`: o caminho antigo não
  muda.
- `tools/make_scaled.py` lê a triagem e cria config com `"scale": s` para as células em que
  `0,80 <= s < 1` (prédio real, sobra pequena): **22 configs** criados (PrimmSheriffStation
  0,806, NovacGiftShop 0,811, **GSProspectorSaloonInterior 0,82** — o saloon de Goodsprings —,
  ElReyMotelRoom01 0,858, Nipton 0,85-0,88, Lucky38CasinoFloor01 0,983, SLJeanSkyDivingINT
  0,949...). Total de configs: 74.
- Build dos 74: **PASS no validador** (pipeline segue mesmo com plano recusado).
- `verify_build`: 5 dos novos reprovam por **casca passando do prédio** (48–113 unidades):
  BlackMountainRadio (-49), FreesideFortInterior (-48), GSAbandonedShack (-113), Gomorrah01
  (-60); NovacBoonesRoom nem constrói (`novac_motel03.nif` não existe no BSA).
- **Causa do defeito**: o `verify_build` media a casca pelo **OBND sem aplicar o `XSCL`** do ref,
  enquanto as posições já vinham encolhidas — parecia que o interior transbordava. Corrigido:
  `s_ref = (XSCL or 1) * plan.int_scale` dentro do check. Com isso as reprovações caíram de 5
  para 2.
- `verify_build` agora isola erro por plano (`FAIL (check: ...)`) em vez de abortar a rodada.
- `tools/fit_scale.py`: para cada config reprovado, **encolhe iterativamente** (×0,95) até a
  casca caber. Resultados: NovacGiftShop 0,732, NovacMcBrideHouse 0,67, NovacJeannieMayHouse
  0,667, PrimmSheriffStation 0,727, QJOffice 0,763, SLJeanSkyDivingINT 0,902, GSAbandonedShack
  0,72, Lucky38CasinoFloor01 0,843. Ou seja: o `fit` da triagem **subestimava** o encaixe; a
  escala real fica em 0,67-0,90 nesses casos.
- **Estado final do conjunto**: 73 configs (NovacBoonesRoom removido, modelo `novac_motel03.nif`
  não existe no BSA), `verify_build` = **72 PASS, 1 FAIL** — só `HELIOSOneDeck`, que esbarra num
  NIF ilegível (`read past end at 770853 (+4) of 770856`, niflib).
- Tentativas de leitura tolerante (todas insuficientes): `collision_tris_local`,
  `visual_bounds` e todos os `niflib.NifFile` de `si/meshes` passaram por um helper `_parse`
  que devolve `None` em erro, e `verify_build` passou a ignorar peça cujo `ref_collision_world`
  falhe. O erro **persiste** e o traceback mostra a origem: `niflib.parse_tri_strips_data` ->
  `_parse_geom_data` -> `_take` (UV sets truncados, `read past end at 770853`). Ou seja, a
  falha acontece na leitura **preguiçosa** da geometria, fora dos pontos protegidos.
- Correção certa (próximo passo): envolver o corpo de `niflib.parse_tri_strips_data` em
  `try/except NifError` e devolver bloco vazio; isso resolve HELIOSOneDeck e qualquer NIF
  truncado do BSA de uma vez.
- **Corrigido de verdade**: `niflib.parse_tri_strips_data` agora envolve `_parse_geom_data` em
  `try/except NifError` e devolve geometria vazia — o NIF truncado do HELIOSOneDeck deixa de
  derrubar a medição. Com isso ele entrou no conjunto e, depois de `fit_scale` (s = 0,827),
  passa.
- **Faixa 0,6-0,8** (`tools/make_band.py 0.6 0.8`): **32 configs** criados (PrimmGenericHouse01-04,
  NovacAndysRoom/GenericHouse02, TrainStationInterior, BCSaloonInterior, FreesideSilverRush,
  NiptonHouse1-4, CottonwoodCoveHQ, JacobstownBungalow02, SLNHPStationINT...). Verificação: 21
  passaram direto, 11 reprovaram; `fit_scale` convergiu em 7 (1ECisternA 0,643,
  2CCampGolfMisfitsTent 0,596, CottonwoodCoveHQ 0,751, NiptonHouse4 0,714,
  NovacGenericHouse02 0,521, PrimmTentsHayes 0,596) e **5 não convergiram nem a 0,45-0,57**
  (CampSearchlightChurch, CampSearchlightChurchNCR, CottonwoodCoveAurelius, HooverDamIntODeck,
  MonteCarloSuitesInterior) — esses configs foram removidos.
- **Estado atual (31/10/2026)**: **100 configs, `verify_build` = 100 PASS**; build
  `--all --out out/exp/scaled` = 1.765.245 bytes, **173 meshes**, 18.566 REFR, 139 navmeshes,
  8 worldspaces, validador **PASS**. Ou seja: **100 das 283 portas (35%)** convertidas e
  verificadas.

### Limites conhecidos para escalar
- Vários interiores no mesmo prédio: funciona quando as salas **cabem** e as portas existem no
  mesmo mesh; prédios montados por peças (sem mesh única) continuam de fora.
- Célula multi-porta só converte quando os pares de porta são rígidos (ver acima); o resto
  precisa de decisão manual (mover a porta interior ou aceitar o erro).
- Andares: portas internas para outros interiores não são copiadas (categoria C).

---

## 17. Altura livre e escala mínima (Claude, 01/11/2026)

Revisão do estado deixado pela sessão anterior (DeepSeek). Achados e correções:

### Bugs de escala corrigidos
- `si/plan.py`: `meshes_scale(c)` usava só o XSCL do ref, **sem `int_scale`**. A detecção das
  paredes da porta e o retângulo de recorte (dividido pela escala) eram calculados no tamanho
  original nos interiores encolhidos. Agora `Copy.scale = XSCL do ref × int_scale` (exatamente o
  que o writer grava) e `meshes_scale` devolve isso.
- `verify_build.py`, checagem de encaixe: lia `XSCL` da **base** (`base.get("XSCL")`), mas XSCL
  fica no **ref**; usava sempre 1. Agora `c.scale`.
- `verify_build.py`, raios da porta: colisão com escala fixa 1.0; agora `c.scale`.
- Com escala 1 nada muda: a release da loja continua **byte a byte** igual (`5980b0d0…`).

### Checagem 6: altura livre (`verify_build.headroom`)
- Para cada triângulo da navmesh do interior (centroide + 3 pontos a 75% para os cantos), raio
  vertical até a primeira colisão acima de piso+20, contra toda a geometria publicada (cópias
  com os meshes recortados e a escala real, mais o prédio). Triângulos binados em células de 64.
- **Calibração nos 52 interiores sem escala (vanilla, jogáveis por definição):** mediana da
  altura livre 171–330; 0–18% dos pontos são baixos (vão de escada, passarelas: torres da
  Hoover Dam 12–17%, SLHellsMotel 18%). Por isso só a **mediana** reprova.
- Regras: **mediana ≥ 150** (jogador ≈ 128 + folga) e **escala ≥ 0,75** (portas internas
  vanilla têm ~176 de altura: 176 × 0,75 = 132, logo acima do jogador). % de pontos baixos é nota.
- O `verify_build` antigo só checava se a casca cabia no prédio, **não se uma pessoa cabia no
  interior**: 31 configs estavam abaixo de 0,60 (mínimo 0,374, JacobstownLodge — teto ~95).

### Resultado
- 128 configs → **72 aprovados**, 56 reprovados (todos com escala < 0,75; 36 também com mediana
  baixa; 2 também com casca para fora, que só apareceram depois do bug do XSCL corrigido).
- Os 56 foram **movidos** para `tools/buildings_rejected/` com o campo `"rejected"` (motivo),
  não apagados. `verify_build` dos 72: PASS. Build `--all`: 1,25 MB, 111 meshes, validador PASS.

### DeepSeek como ajudante
- `tools/deepseek.py`: cliente mínimo (`deepseek-flash`, `reasoning_effort: "max"`, escolha do
  usuário). A chave vem de `DEEPSEEK_API_KEY` (ambiente, ou registro do usuário
  `HKCU\Environment` quando o app não foi reiniciado); nunca é impressa. Saldo inicial US$ 2,86.
- Uso: `py -I deepseek.py --prompt-file p.md --file a.py --out resposta.md`. Esforço máximo é
  lento (>10 min numa revisão de ~600 linhas): rodar em segundo plano.
- Papel: revisão e trabalho mecânico em volume; Claude verifica tudo o que ele produz.

### Revisão do DeepSeek (primeiro uso) e o que foi aceito
- 4 achados sobre 2 problemas, ambos procedentes (custo: ~123 mil tokens, 116 mil de raciocínio):
  1. Teste "parede x soleira" com limite fixo de 100 no mundo: numa sala encolhida a parede
     encolhe. Agora a altura é comparada no tamanho original (÷ `int_scale`).
  2. A altura vinha de **todos** os triângulos da peça: numa peça composta (SCOL), uma soleira
     no vão virava "parede" por causa de uma parte alta em outro lugar.
- A correção sugerida para o 2 (medir só os triângulos atingidos pelos raios) **regrediu**:
  paredes feitas de muitos triângulos pequenos (HELIOSOneDeck `UtlRmWall03`, esgoto do
  SLOldWellINT) deixaram de ser recortadas, e o verificador, com a mesma regra, as aprovaria
  como "soleira". Detectado comparando os cortes antes/depois nos 72 configs.
- Regra adotada (`plan.corridor_tris` + `plan.is_wall_height`, usada pelo plano e pelo
  verificador): altura dos triângulos da peça cuja caixa xy cruza a caixa xy dos caminhos da
  porta, **em qualquer altura**, dividida pela escala. Resultado: cortes dos 72 **idênticos** aos
  de antes, release da loja byte a byte igual, `verify_build` 72/72 PASS.
- Lição: sugestão do ajudante sempre passa por comparação antes/depois em todo o conjunto.

### Manifesto do build
- `pipeline.py` grava `manifest.json` na pasta de saída (lista de interiores, escalas, falhas);
  `package.py` conta os interiores por ele (antes contava os arquivos de `buildings/`, por isso o
  pacote subset dizia "52 interiores" tendo 2). O manifesto não vai para o pacote.

### Pendente
- **Nada além da v1 foi testado no jogo.** Próximo passo recomendado: subset pequeno (General
  Store, Victor's Shack, 2–3 interiores de escala 1 e 1–2 de escala 0,75–0,85).

---

## 18. Primeiro teste da v2 no jogo (01/11/2026, madrugada)

Subset: GSGeneralStore, GSShackVictorInt, GSProspectorSaloonInterior (0,82), NovacMotelLobby,
NiptonGeneralStore (`out/exp/subset`). Antes de instalar, o build grande que estava no jogo
(hash `efc49e0b…`, nem v1 nem v2) foi movido para `dist/game-backup-2026-11-01/`. Bats curtos na
raiz do jogo: `a1..a5` fora, `b1..b5` dentro, `c/d` laterais (1 loja, 2 Victor, 3 saloon,
4 Novac, 5 Nipton); `e3` = frente da porta do Victor + `00109CA1.activate player`; `q` = `qqq`.
Instalado no fim: loja + Victor (`out/exp/subset_sv`, com as correções abaixo).

### Achados
1. **testkit: lado "fora" invertido.** A normal da moldura da porta (`door.normal`) aponta para
   qualquer lado (na loja, para dentro; no Victor/Novac/Nipton, para fora). `testkit.py` agora vira
   a normal para o centróide das cópias. O `marker` de fallback usava a mesma normal.
2. **Portal de porta que sobra na navmesh externa.** A porta externa perde XTEL/XNDP, mas a navmesh
   externa ainda tinha a porta no NVDP (triângulo com flag 0x400) e o NAVI ainda listava a porta no
   NVCI (3ª lista = portas). Agora `writer.py` sobrescreve a navmesh externa (tira o NVDP, limpa
   0x400 se nenhuma outra porta usa o triângulo, ajusta DATA[5]) e reemite NVMI/NVCI dela sem a
   porta (`ext_unports`). Também existia na v1 (loja: tri 124 da 0x177F71). Correção mantida,
   mas **não** era a causa do crash abaixo.
3. **Crash 0x6A55A6 perto do Victor's Shack, 1–4 min depois de carregar o SITest** (5 vezes),
   parado na varanda. **Sem o mod (plugins.txt só com FalloutNV.esm) o SITest crasha igual**
   (00:49:39, mesmo offset). Então não é a v2: é o save SITest (estado gravado por versões antigas)
   ou o jogo vanilla naquele ponto (o Victor patrulha até o marcador 0x154154 na varanda).
   Bissecções anteriores ("sem saloon", "só loja 2,5 min", "Victor sem navmesh 3 min") foram curtas
   demais e não provam nada. `no_navmesh: true` no config ficou como chave de experimento.
4. Teste vanilla limpo (`bat g` sem save) não terminou: o aviso de DLC "Tribal Pack" não fechou com
   Enter/E/clique. Pendente.

### Próximo
- Fazer um save novo limpo em Goodsprings (sem mod, depois dos avisos de DLC), esperar 6 min no
  Victor: se não crashar, refazer o SITest a partir dele e repetir os testes do subset.
- Cada teste de estabilidade precisa de ≥ 6 min para valer (os crashes vieram entre 1 e 4 min).

---

## 19. Crash 0x6A55A6 com a navmesh copiada (01/11/2026, noite)

### Save limpo
- `SIClean.fos`: feito **sem o mod** (`bat g` a partir do menu, avisos de DLC fechados, `bat sc`).
  Bats: `sc` = save SIClean, `lc` = load SIClean. O truque dos avisos: **fechar o console antes do
  carregamento** (`bat g` e `'` na mesma sequência); depois clicar OK com `left_click` direto no
  botão (730, 440) — o Enter não fecha.
- O `SITest` antigo crasha até sem o mod: está contaminado por versões antigas. **Não usar mais.**

### Bisseção (6 min cada, `SIClean`, jogador na varanda do Victor = `bat a2`, célula (-18,-2))
| Build | Resultado |
|---|---|
| vanilla (plugins.txt só com o ESM) | estável 6 min |
| subset 5 interiores | crash ~1 min |
| só a loja (atual e o build antigo 5980b0) | crash |
| só a loja **sem a navmesh copiada** (`"no_navmesh": true`) | **estável 6 min** |
| loja sem as entradas no NAVI (`"no_navi"`) | crash |
| loja com NVMI de ilha (flag 0x20 + bounds) | crash |
| loja sem NVEX entre as duas metades (`"no_nvex"`) | crash |
| loja com metades recortadas na divisa (`"drop_border_tris"`) | crash |
| Chet desativado (`prid 00104C79` + `disable`) | crash |
| Victor desativado (`prid 001073E8` + `disable`) | crash |
| loja, jogador ao norte (-69000, 9000), as 2 células da loja carregadas | estável 6 min (1 vez) |

Conclusão: o gatilho são **NAVMs novas** do plugin nas células (-17,0)/(-17,1). Não é o NAVI,
nem a ligação entre as metades, nem os atores conhecidos. Parece depender de a célula (-17,1)
não estar carregada (uGridsToLoad 5) — uma amostra só, falta confirmar.
`config` aceita as chaves de experimento acima (`no_navmesh`, `no_navi`, `no_nvex`,
`drop_border_tris`); nenhuma vai para release.

### Pesquisa (web + DeepSeek)
- Endereço 0x6A55A6: nada na web.
- **"Navmesh bug" do FO3/FNV**: navmesh vinda de **.esp sem a flag de master** não funciona
  direito; a comunidade manda ligar a flag ESM (o arquivo continua .esp). Mods de navmesh vêm
  ESM-flagged; o Navmesh Overhaul pede para desinstalar mods de navmesh em ESP sem a flag.
- **Nenhuma DLC cria NAVM nova em célula do FalloutNV.esm** (verificado nas 9): elas só
  sobrescrevem a navmesh existente. As DLCs também listam no ONAM **toda** NAVM/ACHR/ACRE/LAND
  temporária sobrescrita (HonestHearts: 93 REFR, 1 NAVM, 12 ACRE, 3 ACHR...).
- 0 de 600 navmeshes principais do WastelandNV têm região solta: o GECK transforma região solta
  em NAVM de ilha separada (NVMI flag 0x20).
- NVMI de ilha (flag 0x20), layout verificado nas 1.687 entradas: base 28 bytes + bounds min/max
  (24) + `u16 nverts, u16 ntris` + vértices + triângulos + `u32 0`; 363 têm geometria vazia.
- Open Tents (Nexus 92500) faz o mesmo que nós nas tendas; usa o Navmesh Overhaul como base
  (edita a navmesh das células) e exige jogo novo (move refs persistentes).

### Correções que ficaram (corretas mesmo sem resolver o crash)
- Portal da porta removido da navmesh externa e do NAVI (seção 18) e agora a NAVM sobrescrita
  entra no **ONAM**. Atores temporários movidos também entram no ONAM (37 no conjunto completo;
  bug latente fora da loja). `validate_esp.py` passou a exigir REFR/ACHR/ACRE/NAVM/LAND
  temporários sobrescritos no ONAM (regra das DLCs).
- NVMI das nossas peças sai como ilha (0x20 + bounds), como no vanilla.
- `regress_v1.py`: compara só a navmesh copiada e as entradas de NAVI das nossas navmeshes (a
  sobrescrita da navmesh externa é intencional). PASS.
- Release (`out/build/Data`, só a loja) agora tem hash `915afe5c…` (antes `5980b0d0…`): mudou
  pelas correções acima. **Não testado no jogo** nesta forma.

### Candidatos prontos para o próximo teste (não testados)
- **Plano A — flag ESM**: `pipeline.py --esm` (TES4 flags 0x1). Builds:
  `out/exp/store_esm` (só a loja) e `out/exp/subset_esm` (5 interiores).
- **Plano B — mesclar na navmesh da célula**: `pipeline.py --esm --merge-navmesh`. Em vez de
  NAVM nova, os triângulos do interior são **acrescentados à navmesh principal vanilla da célula**
  (sobrescrita, como as DLCs), a grade NVGD é refeita mantendo os limites vanilla, as metades em
  células vizinhas se ligam por NVEX entre as duas principais e a peça é **ligada ao exterior na
  porta** por aresta interna (para a navmesh continuar uma região só, como no vanilla).
  Célula sem navmesh principal (Gomorrah, SLHellsMotel) cai no caminho antigo com nota.
  Builds: `out/exp/store_merge`, `out/exp/subset_merge`, `out/exp/all_merge` (72 configs).
  Todos: validador PASS, `verify_navmesh` PASS (0 erros de reciprocidade).
  Ressalva: a metade de trás da loja em (-17,1) só se liga pelo NVEX da outra célula (o fundo
  do prédio não encosta em chão externo) — sem precedente exato no vanilla.

### Roteiro do próximo teste (6 min cada, `bat lc` e depois `bat a2`)
1. `store_esm`. Se estável: repetir 1x e passar para `subset_esm`.
2. Se crashar: `store_merge`; se estável, `subset_merge`.
3. Com o vencedor: checklist da v1 na loja (entrar andando, Chet no balcão, Steal, noite) e nos
   outros 4 interiores (bats `a1..a5`, `b1..b5`).
Instalar: fechar o jogo, copiar `SeamlessInteriors.esp` e `meshes/SeamlessInteriors` do build
para `Data/` (apagar a pasta de meshes antiga antes).

### Resultado dos planos A e B no jogo (01–02/11/2026)
| Build | Resultado |
|---|---|
| `store_esm` (Plano A, hash `a55f8bab…`) | **crash 0x6A55A6** depois de ~4-5 min de jogo ativo |
| `store_merge` (Plano B, hash `891f4c9f…`) | **congela** (não respondendo, CPU parada) em <1,5 min, 2 de 3 vezes; na 3ª rodou 2 min normal até o teste ser interrompido |

- A flag ESM sozinha **não resolve**. Mesclar na navmesh vanilla troca o crash por travamento
  (provavelmente laço no pathfinding com a nossa sobrescrita da navmesh principal).
- **Cuidado ao medir:** se o jogo perde o foco ele pausa (menu de pausa) e o relógio do teste não
  vale. Conferir com screenshots a cada ~90 s; idle camera girando = jogo vivo; quadros idênticos
  com o nome do local ainda no HUD = travado (`(Get-Process FalloutNV).Responding`).
- Instalado no fim: `store_merge` (trocar antes de jogar).

### Causa provável: divisor da grade NVGD (02/11/2026, offline)
- 0x6A55A6 = VA 0x00AA55A6, dentro de `FUN_00AA54A0` na análise do exe GOG
  (`~/dev/fnv-rea/analysis/inventory-v5/0x845050-asm.json`): é chamada como **alocador**
  (`ECX = obj+0x10`, push heap `[0x010A2720]`, push `n*8`; resultado vai para memcpy 0x401460).
  Crash dentro do alocador = **heap corrompido** antes por alguém.
- **Regra do GECK para o divisor da NVGD**: `div = 1 se ntris <= 16, senão min(12, ntris // 50 + 2)`.
  Vale para **todas** as 6.138 NAVMs do FalloutNV.esm + DeadMoney/HonestHearts/OldWorldBlues/
  LonesomeRoad (0 exceções). Os limites da grade = limites dos vértices.
- O writer copiava o divisor da navmesh de origem: a metade de 12 triângulos da loja saía com
  div 2 (deveria 1) — **inclusive na v1 publicada** (`dist/SeamlessInteriors-v1`, 0x010008C9) e no
  build 5980b0 que crashou. No Plano B a navmesh vanilla ficou com 518 triângulos e div 11
  (deveria 12) — e o jogo **congelou** em vez de crashar.
- Hipótese: o motor calcula o divisor pela contagem e lê a grade gravada com o divisor gravado:
  maior que o calculado = estouro de heap (crash no alocador); menor = grade inconsistente (trava).
  Não provado; casa com os dois sintomas e com "sem navmesh = estável".
- Correção: `writer.grid_divisor(ntris)` sempre (o argumento `div` de `navmesh_grid` é ignorado);
  `verify_navmesh.py` reprova divisor fora da regra (e agora **soma** as falhas da grade — antes a
  contagem era descartada). Builds antigos `store_esm`/`store_merge` reprovam; novos passam.
- Conferidos e iguais ao vanilla: cabeçalho do record (versão 15, flags 0), NVER 11, ordem dos
  subrecords, flags dos triângulos. Nossa lista por célula é superconjunto da do GECK (nunca falta
  triângulo).
- Builds novos (validador PASS, verify_navmesh PASS): `out/exp/store_div` (só a loja, sem flag
  ESM, sem mesclar), `out/exp/subset_div` (5 interiores), `out/exp/all_div` (72), release
  `out/build/Data` (= store_div). **Não testados no jogo.**
- Próximo teste: `store_div`, `bat lc` + `bat a2`, 6 min com screenshots a cada ~90 s.
- Revisão do DeepSeek (`out/review/crash5_answer.md`): plausível, não provado. Ele lembra que a regra
  pode ser só convenção do GECK (o motor pode confiar no divisor gravado) e que o `n*8` do
  alocador pode ser outra coisa — atenção: ele supõe NVTR de 8 bytes, mas no FNV são 16.
  O congelamento do Plano B também pode vir da sobrescrita da navmesh vanilla em si.
- Em aberto: a lista por célula do GECK não é nem o teste por bbox (nosso, superconjunto) nem o
  SAT exato (o SAT deixa de fora triângulos que o vanilla inclui em 229/478 navmeshes); provável
  SAT com tolerância. Só vale perseguir se o `store_div` ainda crashar.

### Resultado do `store_div` no jogo (02/11/2026, 17:25)
- Instalado (ESP `875F2D0A…`, hashes conferidos), `bat lc` + `bat a2`: **crash 0x6A55A6 ~10 s
  depois** de chegar na varanda do Victor (Evento 1000 às 17:26:00). Mais rápido que antes.
- **O divisor não era a causa** (ou não a única). A correção fica (é o que o GECK faz), mas a
  hipótese de estouro pelo divisor está descartada.
- Continua valendo: sem NAVM nova do plugin = estável; com qualquer NAVM nova = crash.

### Ilhas no NAVI e capturador de crash (09/11/2026, offline)
- Ilhas vanilla (NVMI flag 0x20) no exterior: 1.223 com geometria embutida, 310 vazias; a maioria
  tem NVEX (para principais e para outras ilhas) e 0–2 adjacentes no NVCI. Nada separa as vazias
  das com geometria, então o nosso NVMI (vazio) não foge do vanilla. Triângulo embutido = 6 bytes.
- Nossas NAVMs novas não têm cover (DATA ncover 0, sem NVCA, cover flags 0), igual à origem.
- Comparação de formato esgotada: falta saber **o que o motor estava fazendo** no crash.
- `tools/crashcatch.py` (novo, ctypes): anexa como depurador ao FalloutNV.exe em execução, salva a
  imagem **decifrada** da memória (`out/crash/image.bin`, serve para análise estática do exe da
  Steam) e, a cada access violation, registradores, código em volta do EIP, pilha, ponteiros de
  retorno e o que os registradores apontam (`out/crash/av_NN_*.json`). Não mata o jogo ao sair.
  Testado num PING.EXE de 32 bits (`--snapshot`). **Ainda não usado no jogo.**
- Próximo teste: abrir o jogo com `store_div`, no menu rodar `py -I crashcatch.py` em segundo
  plano, `bat lc` + `bat a2`, esperar o crash e analisar a pilha.

### Crash 0x6A55A6 capturado: falta de espaço de endereçamento (09/11/2026)
- `crashcatch.py` (depurador) **não serve**: o SteamStub esconde as threads do depurador; com ele
  anexado o jogo morreu em outro ponto (0x7EC12) sem evento. Serve só para tirar `image.bin`.
- `tools/crashwatch.py` (sem depurador): espera o `WerFault.exe` aparecer, suspende o jogo e grava
  contexto + pilha de todas as threads, o CONTEXT da exceção, a imagem e o mapa de memória; enquanto
  espera, registra a cada ~3 s commit/reservado/livre/maior bloco livre. `memmap_now.py` resume as
  alocações de um processo vivo. Captura de 22:03 em `out/crash1/`.
- Desmontar: `llvm-objdump -d -M intel --start-address=.. --stop-address=..` (em
  `~/tools/llvm-mingw-*/bin`) sobre `out/crash*/FalloutNV_unpacked.exe` (dump com os cabeçalhos de
  seção corrigidos para raw = RVA). O Ghidra via REA estoura o tempo nesse arquivo.
- **O crash**: escrita em endereço 0 dentro de `0xAA54A0` = alocação num heap temporário (bump
  allocator: `[0]` base, `[4]` atual, `[8]` fim). O heap é um objeto na pilha criado por `0x6B9400`
  (`0xAA5410`, tamanho inicial 0x4800 -> 0x10000) numa thread de pathfinding
  (`6C9600 -> 6B8C50 -> 6B8EC0 -> 6B8FE0 -> 6B9420 -> 6A1F90 -> AA54A0`). No crash o heap tinha
  **base 0 e fim 0x10000**: a reserva de memória (8 MB) falhou e ninguém conferiu.
- O exe da Steam **não é large-address-aware** (Characteristics 0x0102): 2 GB. Medido em jogo na
  varanda do Victor com `store_div`: commit ~1.180 MB, **livre ~123 MB, maior bloco livre 16 MB**,
  estável (não cresce) — o jogo fica encostado no teto e cai quando falta um bloco contíguo.
  Três imagens grandes ocupam ~310 MB (122 + 102 + 85 MB; não identificadas, provável driver de
  vídeo/overlays).
- Ou seja: não é formato de navmesh. As NAVMs novas só fazem o pathfinding pedir mais desses heaps.
  Falta a linha de base **sem o mod** (mesmo lugar) para saber quanto o mod consome a mais.
- Correção provável: exe com 4 GB (FNV 4GB Patcher; a versão GOG já vem assim). Não testado.
- **Congelamento (22:30, `out/freeze1/`)**: mesmo teste, o jogo parou de responder com CPU zerada
  em vez de crashar. A thread principal estava bloqueada **dentro do `d3d9.dll`** (espera), não no
  código do jogo; memória no mesmo teto (livre 121 MB, maior bloco 16 MB). Não dá para separar,
  só com isso, falta de memória no driver de perda de foco da janela (houve interrupções do
  controle do computador nesse teste). O "congelou" do Plano B pode ter sido a mesma coisa.
- As imagens grandes são do **driver AMD**: `amdxc32.dll` 122 MB e `amdvlk32.dll` 102 + 85 MB
  (310 MB dos 2 GB), mais `steamclient.dll` 21 MB.

### 4GB Patch: `store_div` estável (09/11/2026, 22:37–22:44)
- `tools/patch4gb.py`: aplica os mesmos bytes do FNV 4GB Patch (Nexus 62552 v1.4, fonte lida) em
  vez de rodar o exe dele; confere SHA1 (Steam US `D068F394…` -> `0021023E…`, o hash "já
  patcheado" oficial), guarda `FalloutNV_backup.exe`; `--restore` desfaz. O patch também faz o
  exe carregar `nvse_steam_loader.dll`: **o xNVSE 6.3.10 agora carrega** (aparece no console).
- Resultado: `store_div`, `bat lc` + `bat a2`, **6,5 min sem crash nem travamento**. Memória
  estável: commit ~1.185 MB, livre ~2.120 MB, maior bloco 2.013 MB. Uma rodada só.
- `bAlwaysActive=1` no `Fallout.ini` (backup `.bak-seamless`) **não** impede a pausa quando a janela
  é minimizada; o jogo pausa quando o turno do Claude termina. Quem espera precisa manter o
  controle (`computer_batch` com `wait`, no máximo ~280 s por chamada, senão dá timeout de 300 s).
- Consequência para o mod: **exigir o 4GB Patch** (como quase todo mod grande de FNV).

### `subset_div` (5 interiores) com 4 GB (09/11/2026, 22:46–22:53)
- Instalado `out/exp/subset_div` (ESP `E19ADE82…`). Varanda do Victor: **6,5 min sem crash**,
  memória estável (commit ~1.250 MB, livre ~2.100 MB).
- `bat b1` (dentro da loja): OK, interior completo, Chet atrás do balcão.
- `bat b2` (dentro do Victor's Shack): carrega, mas o ponto do bat cai em cima de um móvel/viga
  (câmera na altura do teto). Interior visível; não avaliado além disso.
- `bat b3` (dentro do saloon, escala 0,82): **crash novo, `FalloutNV.exe+0x704CC`**, na thread
  principal, segundos depois. Captura em `out/crash2_saloon/`. Não é memória (2,1 GB livres).
  `0x470470` esvazia uma lista (`tList::RemoveAll`) cujo ponteiro vem de `[ator + 0x268]` e vale
  `0x00020002` (lixo): chamado por `0x8F63E0` (método de ator, vtable +0x504/+0x428) <- `0x96C710`
  <- laço principal (`0x86E650`). Ou seja, um **ator com estado inválido** perto do saloon.
  Suspeitos: atores do saloon movidos (Trudy, Sunny, Cheyenne = ACRE), escala 0,82 em ator, ou
  pacote convertido. Próximo: build só com o saloon, depois saloon sem atores / sem escala.
- Novac (`b4`) e Nipton (`b5`) não chegaram a ser testados.

### Portas com escala própria (10/11/2026) — o usuário testou no jogo
- Sintoma (saloon, `subset_div`): porta "entrando no chão", vão desalinhado e jogador sem
  conseguir passar. Causa: **a porta externa do saloon tem XSCL 0,82 no master** e o alinhamento
  usava o centro da folha em tamanho cheio (erro de ~16 em z e ~12 de lado). 27 dos 72 configs têm
  porta externa e/ou interna com XSCL (Victor 0,88; El Rey 1,05; QJ 1,10 int...).
- Correção (`geom.Alignment`, `plan.Door.set_scale`, `writer`): a folha é medida com a escala do
  ref; a porta externa passa a ter **XSCL = escala da porta interna × escala do interior** (gravado
  na sobrescrita), e o interior alinha nessa folha. Piso e batentes coincidem. Config sem escala e
  sem XSCL nas portas (loja) não muda.
- Testado pelo usuário com `out/exp/subset_door` (ESP `AC48C946…`): **a porta do saloon encosta
  no chão e dá para entrar andando**. Sem crash nessa visita (o crash 0x704CC de ontem não se
  repetiu; não foi investigado).
- Ainda errado no saloon (prints do usuário):
  1. Com a porta aberta, **visto de fora o vão é uma chapa amarela chapada**; de dentro para fora
     está normal. Origem **não achada**: não é a porta (1 shape), nem a fachada (a face atrás da
     porta foi recortada), nem `NVGSRmWallDoor` (sem quad no vão), nem MSTT/ACTI/LIGH perto.
     Próximo passo: no jogo, console, clicar na chapa amarela e anotar o ID/base.
  2. De fora, tábuas e linhas finas do interior atravessam a fachada junto da janela à direita
     (`verify_build`: casca passa 50 do prédio depois do realinhamento). Refazer `fit_scale`.
  3. Sobre a porta fechada aparece um contorno de moldura vazio (bandeira) — a conferir contra o
     vanilla.
  4. `bat b3` cai dentro de um móvel (centróide da navmesh); `testkit` precisa de ponto melhor.
- **Decalques na fachada**: `plan._collect_door_backings` desativa (flag 0x800 + ONAM) STAT externo
  com faces no plano da porta cobrindo >= 50% do vão. Pegou `NVStain02` 001090D7 na **loja** (mancha
  de parede que cruza a porta; com a porta aberta fica uma folha suja no ar — provável "algo
  parecido" que o usuário viu lá). O build da loja mudou por isso (`eeba0fb7…`). Não testado.
  `geom.to_world_euler` (rotações x/y/z) é exato só para rotação em um eixo.
- `plan._collect_door_wall_cuts` também recorta peça **sem colisão no vão** cujas faces cubram
  >= 50% dele (até 400 da porta). Não achou nada no saloon; nos outros configs não foi medido.
- Builds: `out/exp/subset_door` (instalado, testado) e `out/exp/subset_door2` (com os decalques
  desativados, não instalado). Falta rodar `verify_build` nos 72 e comparar cortes antes/depois.
