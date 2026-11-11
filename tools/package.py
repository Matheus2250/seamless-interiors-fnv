"""Package a built plugin into dist/ (Fase 8): Data/ + LEIA-ME + zip."""

import argparse
import datetime
import glob
import json
import os
import shutil
import sys
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

README = """# Seamless Interiors - build de teste

Interiores do Fallout: New Vegas movidos para dentro do predio real, com a porta animada e
sem tela de loading. Este pacote e um BUILD DE TESTE: passou nos verificadores do pipeline,
mas nao foi jogado ainda.

## Instalar

1. Copie o conteudo de Data para a pasta Data do jogo (a mesma que tem FalloutNV.exe).
2. Garanta SeamlessInteriors.esp no plugins.txt, depois de FalloutNV.esm.
3. Para desinstalar: apague o .esp e a pasta meshes/SeamlessInteriors.

O jogo trava o ESP enquanto esta aberto: feche o jogo antes de copiar.

## O que tem

- SeamlessInteriors.esp: %(n)d interiores (%(refr)d referencias, %(navm)d navmeshes).
- meshes/SeamlessInteriors/<interior>/: %(meshes)d modelos com o vao da porta e as paredes
  internas recortadas.

## Como testar

O kit de posicoes esta em tools/out/testkit: copie os .txt para a raiz do jogo e use
bat <interior>_outside, _inside, _side1, _side2 no console.

Checklist: a porta abre com E e nao ha loading; andando para dentro o interior aparece; de
dentro ve-se o mundo pelo vao; nas laterais nada do interior atravessa o predio; NPCs nascem
dentro e conversam; sair para longe e voltar recarrega tudo.

## Limites conhecidos

- O nome do local no HUD continua sendo o do mundo aberto.
- A luz do interior vem do exterior (aceitavel nos testes da v1).
- 2 interiores citam a propria celula em condicoes de quest/pacote: GomorrahJoanaRoom (quest
  VMS21a) e HooverDamIntIntakeTower01.
- 3 interiores ficam sem navmesh ligada ao mundo; a variante com ligacao sai por
  pipeline.py --navlink.

Gerado por tools/package.py em %(date)s."""


def count_records(esp):
    import esplib
    p = esplib.Plugin(esp)
    counts = {"REFR": 0, "NAVM": 0, "ACHR": 0, "STAT": 0, "PACK": 0, "CELL": 0}
    total = 0
    for it in p.walk():
        if it.kind == "record":
            total += 1
            if it.sig in counts:
                counts[it.sig] += 1
    return counts, total


def main(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", default=os.path.join("out", "exp", "ab"))
    ap.add_argument("--name", default="SeamlessInteriors-v2-teste")
    args = ap.parse_args(argv)
    src = args.src if os.path.isabs(args.src) else os.path.join(HERE, args.src)
    esp = os.path.join(src, "SeamlessInteriors.esp")
    if not os.path.exists(esp):
        print("no plugin at", esp)
        return 1
    counts, total = count_records(esp)
    meshes = glob.glob(os.path.join(src, "meshes", "**", "*.nif"), recursive=True)
    out = os.path.join(ROOT, "dist", args.name)
    if os.path.isdir(out):
        shutil.rmtree(out)
    os.makedirs(out)
    shutil.copytree(os.path.join(src, "meshes"), os.path.join(out, "Data", "meshes"))
    shutil.copy2(esp, os.path.join(out, "Data", "SeamlessInteriors.esp"))
    # count what is in this build, not the configs folder (a subset build has fewer interiors)
    manifest = os.path.join(src, "manifest.json")
    if not os.path.exists(manifest):
        print("no manifest.json in %s: rebuild it with pipeline.py" % src)
        return 1
    with open(manifest, encoding="utf-8") as fh:
        n = len(json.load(fh)["interiors"])
    text = README % {"n": n, "refr": counts["REFR"], "navm": counts["NAVM"],
                     "meshes": len(meshes), "date": datetime.date.today().isoformat()}
    with open(os.path.join(out, "LEIA-ME.md"), "w", encoding="utf-8", newline="\r\n") as fh:
        fh.write(text)
    zip_path = os.path.join(ROOT, "dist", args.name + ".zip")
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as z:
        for base, _dirs, files in os.walk(out):
            for f in files:
                full = os.path.join(base, f)
                z.write(full, os.path.relpath(full, out))
    files = sum(len(f) for _b, _d, f in os.walk(out))
    print("packaged %s: %d files, %d meshes, %d records (REFR %d, NAVM %d, CELL %d) -> %s"
          % (out, files, len(meshes), total, counts["REFR"], counts["NAVM"], counts["CELL"], out))
    print("zip: %s (%.1f MB)" % (zip_path, os.path.getsize(zip_path) / 1e6))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))