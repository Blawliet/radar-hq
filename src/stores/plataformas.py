"""Coletores por plataforma de e-commerce.

Lojas menores rodam em plataformas prontas. Cada uma tem um jeito mais
confiável de ler estoque:
  VTEX         API pública de catálogo (AvailableQuantity)
  Shopify      /search/suggest.json + /products/<handle>.js (variant.available)
  WooCommerce  Store API /wp-json/wc/store/v1/products (is_in_stock)
  Nuvemshop    página do produto (atributo data-variants com "available")
  Outras       busca HTML + validação genérica da página (stock.py)
"""
from __future__ import annotations

import json
import logging
import re
from urllib.parse import quote, quote_plus, urljoin, urlparse, urlunparse

from bs4 import BeautifulSoup

from models import Oferta
from stock import validar_html
from stores.base import Fetcher, Loja
from texto import normalizar

log = logging.getLogger("coleta")

PALAVRAS_VAZIAS = {"de", "do", "da", "dos", "das", "e", "a", "o", "por", "vol", "volume",
                   "panini", "pipoca", "nanquim", "edicao", "kit", "box"}


def relevante(titulo: str, termo: str, minimo: float = 0.6) -> bool:
    tt = set(normalizar(titulo).split())
    tokens = [x for x in normalizar(termo).split() if x not in PALAVRAS_VAZIAS and not x.isdigit()]
    if not tokens:
        return True
    return sum(1 for x in tokens if x in tt) / len(tokens) >= minimo


def limpar_url(url: str) -> str:
    """Remove rastreadores e fragmentos, mantendo a URL do produto."""
    p = urlparse(url)
    return urlunparse((p.scheme, p.netloc, p.path, "", "", ""))


# ----------------------------------------------------------------- detecção
MARCADORES = [
    ("vtex", re.compile(r"vtex(commerce|assets|img|\.com)|/api/catalog_system|vtex-", re.I)),
    ("shopify", re.compile(r"cdn\.shopify\.com|Shopify\.theme|myshopify\.com", re.I)),
    ("nuvemshop", re.compile(r"nuvemshop|tiendanube|d26lpennugtm8s\.cloudfront", re.I)),
    ("woocommerce", re.compile(r"woocommerce|wc-block|/wp-content/plugins/woocommerce", re.I)),
    ("magento", re.compile(r"Magento_|mage/cookies|/static/version\d+/frontend", re.I)),
    ("tray", re.compile(r"tray\.com\.br|traycdn|images\.tcdn\.com\.br", re.I)),
    ("lojaintegrada", re.compile(r"lojaintegrada|cdn\.awsli\.com\.br", re.I)),
]


def detectar_plataforma(f: Fetcher, base: str) -> str:
    r = f.get(base)
    if r.status != 200:
        return f"erro-http-{r.status}"
    for nome, rx in MARCADORES:
        if rx.search(r.html):
            return nome
    return "html"


BUSCA_PADRAO = {
    "woocommerce": "/?s={q}&post_type=product",
    "nuvemshop": "/search/?q={q}",
    "magento": "/catalogsearch/result/?q={q}",
    "tray": "/loja/busca.php?palavra_busca={q}",
    "lojaintegrada": "/buscar?q={q}",
    "html": "/busca?q={q}",
}
PRODUTO_PADRAO = {
    "woocommerce": r"/produto/",
    "nuvemshop": r"/produtos/",
    "lojaintegrada": r"/[^/]+-\d+$|/produto/",
}


# ----------------------------------------------------------------- VTEX
class VtexLoja(Loja):
    def buscar(self, termo):
        base = self.cfg["base"].rstrip("/")
        url = (f"{base}/api/catalog_system/pub/products/search?ft={quote(termo)}"
               f"&_from=0&_to={self.max_resultados - 1}")
        status, dados = self.f.get(url, json=True)
        saida = []
        for p in dados or []:
            melhor = None
            for item in p.get("items", []):
                for s in item.get("sellers", []):
                    co = s.get("commertialOffer", {})
                    if co.get("AvailableQuantity", 0) > 0 and co.get("Price", 0) > 0:
                        if not melhor or co["Price"] < melhor[0]["Price"]:
                            melhor = (co, s.get("sellerName"))
            if not melhor or not relevante(p.get("productName", ""), termo):
                continue
            co, vendedor = melhor
            saida.append(Oferta(
                loja=self.nome, titulo=p["productName"], url=limpar_url(p["link"]),
                preco=float(co["Price"]), preco_lista=float(co.get("ListPrice") or 0) or None,
                vendedor=vendedor, extra={"qtd_api": co.get("AvailableQuantity")},
            ))
        return saida

    def validar(self, o):
        r = self.f.get(o.url)
        res = validar_html(o.url, r.url_final, r.status, r.html, False, sinal_externo=True)
        o.disponivel, o.motivo_estoque = res.disponivel, f"VTEX qtd {o.extra.get('qtd_api')} | {res.motivo}"
        return o


# ----------------------------------------------------------------- Shopify
class ShopifyLoja(Loja):
    def buscar(self, termo):
        base = self.cfg["base"].rstrip("/")
        url = (f"{base}/search/suggest.json?q={quote_plus(termo)}"
               f"&resources[type]=product&resources[limit]={min(self.max_resultados, 10)}")
        status, dados = self.f.get(url, json=True)
        prods = ((dados or {}).get("resources", {}).get("results", {}).get("products", []))
        saida = []
        for p in prods:
            if not relevante(p.get("title", ""), termo):
                continue
            saida.append(Oferta(
                loja=self.nome, titulo=p["title"], url=f"{base}/products/{p['handle']}",
                preco=float(p.get("price") or 0), extra={"handle": p["handle"]},
            ))
        return saida

    def validar(self, o):
        base = self.cfg["base"].rstrip("/")
        status, dados = self.f.get(f"{base}/products/{o.extra['handle']}.js", json=True)
        variantes = [v for v in (dados or {}).get("variants", []) if v.get("available")]
        if not variantes:
            o.disponivel, o.motivo_estoque = False, "Shopify: nenhuma variante disponível"
            return o
        v = min(variantes, key=lambda x: x["price"])
        o.preco = v["price"] / 100
        o.preco_lista = (v.get("compare_at_price") or 0) / 100 or None
        r = self.f.get(o.url)
        res = validar_html(o.url, r.url_final, r.status, r.html, False, sinal_externo=True)
        o.disponivel, o.motivo_estoque = res.disponivel, f"Shopify variant.available | {res.motivo}"
        return o


# ----------------------------------------------------------------- HTML genérico
class HtmlLoja(Loja):
    """Busca HTML + validação da página. Serve para Nuvemshop, Tray, Magento,
    Loja Integrada, Magalu, Estante Virtual e lojas próprias."""

    def __init__(self, fetcher, cfg, plataforma="html"):
        super().__init__(fetcher, cfg)
        self.plataforma = plataforma
        self.busca = cfg.get("busca") or BUSCA_PADRAO.get(plataforma, BUSCA_PADRAO["html"])
        padrao = cfg.get("padrao_produto") or PRODUTO_PADRAO.get(plataforma)
        self.rx_produto = re.compile(padrao, re.I) if padrao else None
        self.render = bool(cfg.get("playwright", False))

    def _pegar(self, url, seletor=None):
        return self.f.get_render(url, seletor) if self.render else self.f.get(url)

    def buscar(self, termo):
        base = self.cfg["base"].rstrip("/")
        q = quote(termo) if "{q_path}" in self.busca else quote_plus(termo)
        slug = re.sub(r"[^a-z0-9]+", "-", normalizar(termo)).strip("-")
        url = base + self.busca.replace("{q_slug}", slug).replace("{q_path}", q).replace("{q}", q)
        r = self._pegar(url)
        if r.status != 200:
            log.info("%s: busca devolveu HTTP %s", self.nome, r.status)
            return []
        soup = BeautifulSoup(r.html, "lxml")
        host = urlparse(base).netloc.replace("www.", "")
        vistos, saida = set(), []
        for a in soup.find_all("a", href=True):
            href = urljoin(r.url_final, a["href"])
            p = urlparse(href)
            if host not in p.netloc or p.path in ("", "/"):
                continue
            if self.rx_produto and not self.rx_produto.search(p.path):
                continue
            if re.search(r"/(busca|search|categoria|category|collections?/?$|conta|account|carrinho|cart|login|blog)", p.path, re.I):
                continue
            img = a.find("img")
            texto = " ".join(filter(None, [a.get_text(" ", strip=True), a.get("title"),
                                           img.get("alt") if img else None]))
            if not texto or not relevante(texto, termo):
                continue
            limpa = limpar_url(href)
            if limpa in vistos:
                continue
            vistos.add(limpa)
            saida.append(Oferta(loja=self.nome, titulo=texto[:200], url=limpa, preco=0))
            if len(saida) >= self.max_resultados:
                break
        return saida

    def validar(self, o):
        r = self._pegar(o.url, self.cfg.get("seletor_espera"))
        res = validar_html(o.url, r.url_final, r.status, r.html, r.via == "browser")
        if res.titulo:
            o.titulo = res.titulo
        if res.preco:
            o.preco = res.preco
        o.preco_lista = res.preco_lista or o.preco_lista
        o.vendedor = res.vendedor or o.vendedor
        o.condicao = res.condicao or o.condicao
        o.disponivel, o.motivo_estoque = res.disponivel, res.motivo

        if self.plataforma == "nuvemshop" and r.status == 200:
            self._refinar_nuvemshop(o, r.html)
        if not o.preco:
            o.disponivel, o.motivo_estoque = False, "não consegui ler o preço"
        return o

    @staticmethod
    def _refinar_nuvemshop(o: Oferta, html: str):
        m = re.search(r'data-variants="([^"]+)"', html)
        if not m:
            return
        try:
            variantes = json.loads(m.group(1).replace("&quot;", '"'))
        except json.JSONDecodeError:
            return
        disp = [v for v in variantes if v.get("available")]
        if not disp:
            o.disponivel, o.motivo_estoque = False, "Nuvemshop: variantes indisponíveis"
            return
        v = min(disp, key=lambda x: x.get("price_number") or 1e9)
        o.preco = float(v.get("price_number") or o.preco)
        if v.get("compare_at_price_number"):
            o.preco_lista = float(v["compare_at_price_number"])
        # só promove para disponível se a página não trouxe aviso negativo explícito
        sinais_fracos = ("sem sinal confiável", "renderizada sem botão")
        if not o.disponivel and not any(s in o.motivo_estoque for s in sinais_fracos):
            return
        o.disponivel = True
        o.motivo_estoque = "Nuvemshop variant.available | " + o.motivo_estoque


# ----------------------------------------------------------------- Magento
GRAPHQL_MAGENTO = (
    '{products(search:"%s",pageSize:%d){items{name url_key %s stock_status '
    'price_range{minimum_price{regular_price{value} final_price{value}}}}}}'
)


class MagentoLoja(HtmlLoja):
    """Magento 2 (Loja Panini, Pipoca & Nanquim, Comix, Comic Boom).
    Usa a API GraphQL pública da loja: traz estoque (IN_STOCK/OUT_OF_STOCK),
    preço cheio e preço com desconto. Se a API estiver fechada, cai para a busca HTML."""

    def __init__(self, fetcher, cfg, plataforma="magento"):
        super().__init__(fetcher, cfg, "magento")
        self.sufixo = cfg.get("sufixo_url")          # ".html", "" ou None (= perguntar à API)
        self.graphql_ok = True

    def _graphql(self, termo):
        base = self.cfg["base"].rstrip("/")
        termo_q = termo.replace('"', " ")
        for campo_sufixo in ("url_suffix url_rewrites{url}", "url_suffix", ""):
            q = GRAPHQL_MAGENTO % (termo_q, self.max_resultados, campo_sufixo)
            status, dados = self.f.get(f"{base}/graphql?query={quote(q)}", json=True)
            if status == 200 and dados and not dados.get("errors"):
                return dados["data"]["products"]["items"]
        return None

    def buscar(self, termo):
        if not self.graphql_ok:
            return super().buscar(termo)
        itens = self._graphql(termo)
        if itens is None:
            log.info("%s: GraphQL indisponível, usando busca HTML.", self.nome)
            self.graphql_ok = False
            return super().buscar(termo)
        base = self.cfg["base"].rstrip("/")
        saida = []
        for it in itens:
            if it.get("stock_status") != "IN_STOCK" or not relevante(it.get("name", ""), termo):
                continue
            pr = (it.get("price_range") or {}).get("minimum_price") or {}
            preco = float((pr.get("final_price") or {}).get("value") or 0)
            cheio = float((pr.get("regular_price") or {}).get("value") or 0)
            reescritas = [r.get("url") for r in (it.get("url_rewrites") or []) if r.get("url")]
            direta = next((u for u in reescritas if "/" not in u.strip("/")), None)
            if direta:
                url = f"{base}/{direta.lstrip('/')}"
            else:
                sufixo = self.sufixo if self.sufixo is not None else (it.get("url_suffix") or "")
                url = f"{base}/{it['url_key']}{sufixo}"
            saida.append(Oferta(
                loja=self.nome, titulo=it["name"], url=url,
                preco=preco, preco_lista=cheio if cheio > preco else None,
                extra={"api": "magento", "preco_cheio": cheio},
            ))
        return saida

    def validar(self, o):
        if o.extra.get("api") != "magento":
            return super().validar(o)
        r = self._pegar(o.url, self.cfg.get("seletor_espera"))
        if r.status == 404:
            # a loja usa outro sufixo de URL: tenta com/sem ".html" e memoriza o que funcionar
            alt = o.url[:-5] if o.url.endswith(".html") else o.url + ".html"
            r2 = self._pegar(alt, self.cfg.get("seletor_espera"))
            if r2.status == 200:
                self.sufixo = ".html" if alt.endswith(".html") else ""
                o.url, r = alt, r2
        res = validar_html(o.url, r.url_final, r.status, r.html, r.via == "browser", sinal_externo=True)
        o.titulo = res.titulo or o.titulo
        o.disponivel = res.disponivel
        o.motivo_estoque = f"Magento IN_STOCK | {res.motivo}"
        if self.oficial and o.extra.get("preco_cheio"):
            o.preco_lista = o.extra["preco_cheio"]      # loja da editora: preço cheio = preço de capa
        return o


# ----------------------------------------------------------------- WooCommerce
class WooLoja(HtmlLoja):
    """WooCommerce (Loja Monstra). Store API pública com is_in_stock; se fechada, busca HTML."""

    def __init__(self, fetcher, cfg, plataforma="woocommerce"):
        super().__init__(fetcher, cfg, "woocommerce")
        self.api_ok = True

    def buscar(self, termo):
        if not self.api_ok:
            return super().buscar(termo)
        base = self.cfg["base"].rstrip("/")
        status, dados = self.f.get(
            f"{base}/wp-json/wc/store/v1/products?search={quote_plus(termo)}&per_page={self.max_resultados}",
            json=True)
        if status != 200 or not isinstance(dados, list):
            log.info("%s: Store API indisponível (HTTP %s), usando busca HTML.", self.nome, status)
            self.api_ok = False
            return super().buscar(termo)
        saida = []
        for p in dados:
            if not (p.get("is_in_stock") and p.get("is_purchasable")):
                continue
            nome = BeautifulSoup(p.get("name", ""), "lxml").get_text()
            if not relevante(nome, termo):
                continue
            pr = p.get("prices", {})
            div = 10 ** int(pr.get("currency_minor_unit", 2))
            preco = int(pr.get("price") or 0) / div
            lista = int(pr.get("regular_price") or 0) / div
            saida.append(Oferta(
                loja=self.nome, titulo=nome, url=limpar_url(p["permalink"]), preco=preco,
                preco_lista=lista if lista > preco else None, extra={"api": "woo"},
            ))
        return saida

    def validar(self, o):
        if o.extra.get("api") != "woo":
            return super().validar(o)
        r = self._pegar(o.url)
        res = validar_html(o.url, r.url_final, r.status, r.html, r.via == "browser", sinal_externo=True)
        o.titulo = res.titulo or o.titulo
        o.disponivel, o.motivo_estoque = res.disponivel, f"Woo is_in_stock | {res.motivo}"
        return o
