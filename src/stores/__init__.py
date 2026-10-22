"""Monta os coletores a partir de config/lojas.yaml."""
from __future__ import annotations

import json
import logging
from pathlib import Path

import yaml

from stores.amazon import AmazonLoja
from stores.base import Fetcher, Loja
from stores.mercadolivre import MercadoLivreLoja
from stores.plataformas import HtmlLoja, ShopifyLoja, VtexLoja, WooLoja, detectar_plataforma

log = logging.getLogger("coleta")
CACHE = Path(__file__).resolve().parents[2] / "data" / "plataformas.json"


def _cache() -> dict:
    try:
        return json.loads(CACHE.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def montar_lojas(caminho: str | Path) -> list[Loja]:
    """Cada loja ganha seu próprio Fetcher: assim rodam em paralelo com segurança."""
    cfg = yaml.safe_load(Path(caminho).read_text(encoding="utf-8"))
    cache = _cache()
    lojas: list[Loja] = []
    for c in cfg.get("lojas", []):
        if not c.get("ativo", True):
            continue
        fetcher = Fetcher()
        adapt = c.get("adaptador", "auto")
        if adapt == "amazon":
            lojas.append(AmazonLoja(fetcher, c))
            continue
        if adapt == "mercadolivre":
            lojas.append(MercadoLivreLoja(fetcher, c))
            continue
        if adapt == "auto":
            adapt = cache.get(c["base"]) or detectar_plataforma(fetcher, c["base"])
            if adapt.startswith("erro"):
                log.warning("%s: não respondeu (%s). Confira a URL em lojas.yaml.", c["nome"], adapt)
                continue
            cache[c["base"]] = adapt
            log.info("%s: plataforma %s", c["nome"], adapt)
        classe = {"vtex": VtexLoja, "shopify": ShopifyLoja, "woocommerce": WooLoja}.get(adapt)
        lojas.append(classe(fetcher, c) if classe else HtmlLoja(fetcher, c, adapt))
    CACHE.parent.mkdir(exist_ok=True)
    CACHE.write_text(json.dumps(cache, indent=2))
    return lojas
