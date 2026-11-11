# Seamless Interiors — Fallout: New Vegas

Entrar em um prédio **sem tela de loading**. De verdade: o interior é montado dentro do prédio
real, no mundo aberto. A porta vira uma porta comum, que abre com animação, e você entra andando.

> **Estado: experimental.** A loja de Goodsprings funciona. O resto está em construção e a maior
> parte nunca foi aberta no jogo. Veja [Progresso](#progresso) e [Problemas conhecidos](#problemas-conhecidos).

## Como funciona

No jogo original, a porta de um prédio é um teleporte para outra célula. Aqui o pipeline:

1. copia todos os objetos do interior para dentro do prédio no mundo aberto, alinhando pela porta;
2. recorta o vão da porta na fachada do prédio e na parede do interior (visual e colisão);
3. troca a porta de loading por uma porta animada comum;
4. traz junto os NPCs, os pacotes de IA deles e a navmesh do interior.

Tudo é gerado por scripts Python (só biblioteca padrão), **sem GECK, FNVEdit ou NifSkope**. O
repositório não contém arquivos do jogo: o plugin e os modelos são gerados a partir da sua própria
instalação.

## Progresso

O jogo tem **283** interiores com porta de loading para o mundo aberto.

| Situação | Interiores |
|---|---:|
| Convertidos e aprovados nas checagens automáticas | 72 |
| Convertidos, mas reprovados (só cabem encolhendo demais) | 56 |
| Sem conversão possível hoje | 155 |

Dos 72 aprovados, o que já foi visto **no jogo**:

| Interior | Resultado |
|---|---|
| Goodsprings General Store | Funciona: entrar andando, Chet no balcão, comércio, rotina de dormir |
| Prospector Saloon (Goodsprings) | Dá para entrar; tem defeitos visuais (abaixo) |
| Victor's Shack | Interior carrega; porta ainda não avaliada |
| Os outros 69 | Nunca testados no jogo |

Por que 155 ficam de fora (números aproximados):

- **~65** têm o terreno acima do piso: porões, cavernas e esgotos ficariam enterrados.
- **~50** não têm um prédio único na porta: a construção é montada com várias peças
  (cassinos, prisão da NCR, hangares de Nellis, Big MT).
- **~21** têm mais de uma porta para fora em posições que não batem entre si.
- **~19** têm o interior muito maior que o prédio.

## Problemas conhecidos

**Requisito**
- **Precisa do executável com 4 GB de memória** (FNV 4GB Patcher, ou a versão GOG). Sem isso o
  jogo da Steam esgota os 2 GB e cai no pathfinding (`FalloutNV.exe+0x6A55A6`).

**Geral**
- O HUD mostra "Mojave Wasteland" dentro dos interiores convertidos.
- A iluminação do interior vem do exterior.
- Portas convertidas não são mais trancadas à noite.
- Objetos com rotação em mais de um eixo (livros, itens pequenos) podem ficar levemente tortos.
- Missões e diálogos que testam "estar na célula X" não são reescritos (2 interiores conhecidos).
- Conversões mexem em referências persistentes: use em jogo novo.

**Prospector Saloon**
- Com a porta aberta, visto de fora, o vão aparece como uma chapa amarela. Causa não identificada.
- Peças do interior atravessam a fachada perto de uma janela.
- Houve um crash dentro do saloon (`+0x704CC`, ligado a um ator) que não se repetiu e não foi
  investigado.

**Interiores encolhidos**
- Para caber no prédio, alguns interiores são reduzidos (mínimo 75%). Tetos e passagens ficam
  mais baixos.

## Requisitos

- Fallout: New Vegas 1.4.0.525 (testado na versão Steam, com as DLCs).
- Executável com 4 GB de memória.
- Python 3 (testado com 3.14), só biblioteca padrão. Windows.

## Como gerar

O caminho do jogo está em `tools/si/master.py` (padrão: instalação da Steam).

```bash
cd tools
py -I si/inventory.py --refresh     # inventário do FalloutNV.esm (uma vez)
py -I pipeline.py                   # só a loja de Goodsprings -> out/build/Data
py -I pipeline.py --all --out out/exp/todos   # os 72 configs (experimental)
py -I verify_build.py               # checagens geométricas
py -I verify_navmesh.py out/build/Data/SeamlessInteriors.esp
```

Para instalar, copie `SeamlessInteriors.esp` e a pasta `meshes/SeamlessInteriors` do build para a
pasta `Data` do jogo e ative o plugin.

## O que tem aqui

| Caminho | Conteúdo |
|---|---|
| `tools/si/` | Pipeline: leitura do master, alinhamento, recorte de modelos, plano e escrita do plugin |
| `tools/pipeline.py` | Gera o plugin e os modelos |
| `tools/buildings/` | Um config por interior aprovado |
| `tools/buildings_rejected/` | Configs reprovados, com o motivo |
| `tools/esplib.py`, `bsalib.py`, `niflib.py`, `nif_strips.py` | Leitores de ESP/ESM, BSA e NIF |
| `tools/validate_esp.py`, `verify_build.py`, `verify_navmesh.py` | Verificadores |
| `tools/triage.py` | Triagem dos 283 interiores |
| `tools/scriptc.py` | Compilador de scripts do jogo (não usado na versão atual) |
| `tools/crashwatch.py`, `memmap_now.py` | Captura de crash e de uso de memória do jogo |
| `tools/patch4gb.py` | Aplica o patch de 4 GB com backup |
| `CLAUDE.md` | Diário técnico completo: formatos, o que falhou e por quê |
| `PLANO-GERAL.md` | Plano por fases |

## Créditos

- FNV 4GB Patcher, de Roy Batty: `tools/patch4gb.py` aplica os mesmos bytes do patch publicado.
- Formato NIF: documentação do projeto NifTools (`nifxml`).
- Código escrito com Claude (Anthropic), dirigido e testado no jogo por Matheus Vitorino.

Fallout: New Vegas pertence à Bethesda Softworks / Obsidian Entertainment. Este projeto não é
afiliado a elas e não distribui arquivos do jogo.
