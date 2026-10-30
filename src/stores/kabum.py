"""KaBuM! pela API pública do catálogo (a mesma que o site usa).

Busca:   GET servicespub.../catalog/v2/products?query=...
Produto: GET servicespub.../catalog/v2/products/{id}

Cada produto já vem com preço no PIX, preço cheio, oferta relâmpago e estoque,
então não precisa abrir navegador (a página da KaBuM! é pesada e cheia de JavaScript).
"""
from __future__ import annotations

import logging
import re
from urllib.parse import quote_plus

from models import Oferta
from stores.base import Loja
from stores.plataformas import relevante

log = logging.getLogger("coleta")
API = "https://servicespub.prod.api.aws.grupokabum.com.br/catalog/v2/products"


def _link(pid) -> str:
    return f"https://www.kabum.com.br/produto/{pid}"


def _id_da_url(url: str) -> str | None:
    m = re.search(r"/produto/(\d+)", url or "")
    return m.group(1) if m else None


class KabumLoja(Loja):
    nome = "KaBuM!"
    categorias_padrao = ["games"]
    base_padrao = "https://www.kabum.com.br"

    def _oferta(self, item: dict) -> Oferta | None:
        a = item.get("attributes") or {}
        pid = item.get("id")
        if not pid or not a.get("title"):
            return None
        oferta = a.get("offer") or {}
        # preço que se paga de fato: PIX da oferta relâmpago > PIX normal > cartão
        preco = (oferta.get("price_with_discount") or a.get("price_with_discount")
                 or oferta.get("price") or a.get("price") or 0)
        # preço "de": o antigo informado pela loja; numa oferta relâmpago, o preço normal.
        # (a diferença entre cartão e PIX não conta como desconto)
        ref = float(a.get("old_price") or 0) or (float(a.get("price_with_discount") or 0) if oferta else 0)
        estoque = int(oferta.get("quantity_available") or a.get("stock") or 0)
        o = Oferta(loja=self.nome, titulo=a["title"], url=_link(pid), preco=float(preco),
                   preco_lista=ref if ref > float(preco) * 1.001 else None,
                   condicao="usado" if a.get("is_openbox") else "novo",
                   vendedor=(a.get("marketplace") or {}).get("seller_name") if a.get("is_marketplace") else None,
                   extra={"kabum_disponivel": bool(a.get("available")) and estoque > 0,
                          "kabum_estoque": estoque, "api": "kabum"})
        return o

    def buscar(self, termo):
        status, dados = self.f.get(f"{API}?query={quote_plus(termo)}&page_number=1&page_size=40", json=True)
        if status != 200 or not isinstance(dados, dict):
            log.info("KaBuM!: API de busca respondeu HTTP %s", status)
            return []
        saida = []
        for item in dados.get("data") or []:
            o = self._oferta(item)
            if o and relevante(o.titulo, termo):
                saida.append(o)
            if len(saida) >= self.max_resultados:
                break
        return saida

    def validar(self, o):
        if o.extra.get("api") != "kabum":
            # link fixo: busca o produto pelo número da URL
            pid = _id_da_url(o.url)
            status, dados = self.f.get(f"{API}/{pid}", json=True) if pid else (0, None)
            item = (dados or {}).get("data") if isinstance(dados, dict) else None
            novo = self._oferta(item) if isinstance(item, dict) else None
            if not novo:
                o.disponivel, o.motivo_estoque = False, f"KaBuM! API sem o produto (HTTP {status})"
                return o
            novo.extra.update({k: v for k, v in o.extra.items() if k not in novo.extra})
            o = novo
        disp = o.extra.get("kabum_disponivel")
        o.disponivel = bool(disp)
        o.motivo_estoque = (f"KaBuM! API: {o.extra.get('kabum_estoque')} em estoque" if disp
                            else "KaBuM! API: indisponível")
        return o
