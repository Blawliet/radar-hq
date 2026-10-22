"""Confere se cada loja de config/lojas.yaml responde e qual plataforma usa.

Uso:  python verificar_lojas.py
"""
import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).parent / "src"))
from stores.base import Fetcher  # noqa: E402
from stores.plataformas import detectar_plataforma  # noqa: E402

f = Fetcher(delay=1)
cfg = yaml.safe_load(Path("config/lojas.yaml").read_text(encoding="utf-8"))
print(f"{'LOJA':<22} {'STATUS':<10} {'PLATAFORMA':<14} BASE")
for c in cfg["lojas"]:
    base = c.get("base")
    if not base:
        print(f"{c['nome']:<22} {'-':<10} {c.get('adaptador'):<14} (coletor próprio)")
        continue
    r = f.get(base)
    plat = detectar_plataforma(f, base) if r.status == 200 else "-"
    aviso = "" if r.status == 200 else "  <- confira o endereço"
    print(f"{c['nome']:<22} {r.status:<10} {plat:<14} {r.url_final}{aviso}")
f.fechar()
