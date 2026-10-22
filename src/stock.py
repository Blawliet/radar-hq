"""Validação de estoque a partir do HTML da página do produto.

Ordem de confiança:
  1. URL final: se a loja redirecionou para home, busca ou login, descarta.
  2. JSON-LD (schema.org Offer.availability): OutOfStock/SoldOut/Discontinued descarta.
  3. Avisos fortes na área de compra ("esgotado", "avise-me quando chegar", "anúncio pausado"...).
  4. Botão de compra presente e habilitado.

Página renderizada no navegador exige o botão. Página baixada por HTTP simples
(sem JavaScript) aceita JSON-LD InStock + nenhum aviso negativo, porque muitas
lojas só desenham o botão via JavaScript.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from urllib.parse import urlparse

from bs4 import BeautifulSoup

from texto import normalizar

DISPONIVEL = {"instock", "limitedavailability", "onlineonly", "instoreonly", "preorder", "presale"}
INDISPONIVEL = {"outofstock", "soldout", "discontinued", "backorder"}

AVISOS_FORTES = [
    "esgotado", "produto esgotado", "indisponivel", "produto indisponivel",
    "nao ha mais estoque disponivel", "sem estoque", "fora de estoque",
    "avise me quando chegar", "avise me quando estiver disponivel", "me avise quando chegar",
    "anuncio pausado", "publicacao pausada", "anuncio finalizado", "publicacao finalizada",
    "este produto nao esta disponivel", "produto nao disponivel", "temporariamente indisponivel",
    "currently unavailable", "out of stock", "sold out",
]
TEXTOS_BOTAO = [
    "comprar", "comprar agora", "adicionar ao carrinho", "adicionar a sacola",
    "adicionar na sacola", "colocar no carrinho", "add to cart", "buy now", "comprar ja",
]
URL_RUIM = re.compile(r"/(busca|search|s|catalogsearch|login|signin|account|carrinho|cart)(/|\?|$)|[?&](q|k|ft|s)=", re.I)
# áreas onde o aviso realmente se refere ao produto principal
AREA_COMPRA = re.compile(r"buy|compra|product-info|productinfo|availability|disponib|stock|estoque|add-to-cart|addtocart|price|preco|sku|ui-pdp", re.I)


@dataclass
class ResultadoEstoque:
    disponivel: bool
    motivo: str
    preco: float | None = None
    preco_lista: float | None = None
    titulo: str | None = None
    vendedor: str | None = None
    condicao: str | None = None


def _iter_jsonld(soup):
    for tag in soup.find_all("script", type="application/ld+json"):
        try:
            dados = json.loads(tag.string or tag.get_text() or "")
        except (json.JSONDecodeError, TypeError):
            continue
        pilha = dados if isinstance(dados, list) else [dados]
        while pilha:
            item = pilha.pop()
            if isinstance(item, list):
                pilha.extend(item)
            elif isinstance(item, dict):
                if "@graph" in item:
                    pilha.extend(item["@graph"] if isinstance(item["@graph"], list) else [item["@graph"]])
                yield item


def _tipo(item) -> set:
    t = item.get("@type", "")
    return {x.lower() for x in (t if isinstance(t, list) else [t]) if isinstance(x, str)}


def _num(v) -> float | None:
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).strip().replace("R$", "").replace("\xa0", "").strip()
    if re.search(r",\d{1,2}$", s):
        s = s.replace(".", "").replace(",", ".")
    try:
        return float(re.sub(r"[^\d.]", "", s))
    except ValueError:
        return None


def ler_jsonld(soup) -> dict:
    """Extrai {'availability','preco','titulo','condicao','vendedor'} do Product principal."""
    out: dict = {}
    for item in _iter_jsonld(soup):
        tipos = _tipo(item)
        if not tipos & {"product", "book", "productgroup", "individualproduct"}:
            continue
        out.setdefault("titulo", item.get("name"))
        ofertas = item.get("offers")
        ofertas = ofertas if isinstance(ofertas, list) else [ofertas] if ofertas else []
        for of in ofertas:
            if not isinstance(of, dict):
                continue
            if "offers" in of and isinstance(of["offers"], list):   # AggregateOffer
                ofertas.extend(of["offers"])
            disp = str(of.get("availability", "")).rsplit("/", 1)[-1].lower()
            if disp:
                # se qualquer oferta está disponível, o produto está disponível
                if disp in DISPONIVEL or "availability" not in out:
                    out["availability"] = disp
            preco = _num(of.get("price") or of.get("lowPrice"))
            if preco and ("preco" not in out or preco < out["preco"]):
                out["preco"] = preco
            cond = str(of.get("itemCondition", "")).lower()
            if "used" in cond or "refurb" in cond:
                out["condicao"] = "usado"
            elif "new" in cond:
                out.setdefault("condicao", "novo")
            vend = of.get("seller")
            if isinstance(vend, dict) and vend.get("name"):
                out.setdefault("vendedor", vend["name"])
        if out.get("availability"):
            break
    return out


def _botao_habilitado(soup) -> bool | None:
    """True = achou botão de compra ativo; False = achou só desabilitado; None = nenhum."""
    achou_desabilitado = False
    for el in soup.find_all(["button", "input", "a"]):
        texto = normalizar(el.get_text(" ") or el.get("value", "") or el.get("aria-label", "") or el.get("title", ""))
        ident = " ".join([el.get("id", ""), " ".join(el.get("class", [])), el.get("name", "")]).lower()
        eh_compra = any(texto == t or texto.startswith(t) for t in TEXTOS_BOTAO) or re.search(
            r"add-to-cart|addtocart|buy-now|buynow|btn-comprar|botao-comprar", ident)
        if not eh_compra:
            continue
        if el.name == "a" and not el.get("href") and "btn" not in ident and "button" not in ident:
            continue
        desabilitado = (
            el.has_attr("disabled")
            or el.get("aria-disabled") == "true"
            or re.search(r"\bdisabled\b|indisponivel|esgotado|unavailable|sold-out|soldout", ident)
        )
        if desabilitado:
            achou_desabilitado = True
        else:
            return True
    return False if achou_desabilitado else None


def _avisos_na_area_de_compra(soup) -> str | None:
    blocos = soup.find_all(attrs={"class": AREA_COMPRA}) + soup.find_all(attrs={"id": AREA_COMPRA})
    for b in blocos[:60]:
        t = normalizar(b.get_text(" "))
        if len(t) > 3000:       # bloco grande demais (provável página inteira), ignora
            continue
        for aviso in AVISOS_FORTES:
            if re.search(rf"\b{re.escape(aviso)}\b", t):
                return aviso
    # avisos que aparecem soltos mas são inequívocos
    corpo = normalizar(soup.get_text(" "))[:20000]
    for aviso in ("anuncio pausado", "publicacao pausada", "anuncio finalizado", "publicacao finalizada"):
        if aviso in corpo:
            return aviso
    return None


def url_eh_produto(url_original: str, url_final: str) -> bool:
    p = urlparse(url_final)
    if p.path in ("", "/"):
        return False
    if URL_RUIM.search(p.path + ("?" + p.query if p.query else "")):
        return False
    return True


def preco_lista_html(soup) -> float | None:
    """Preço 'de' riscado, em padrões comuns (Magento, Nuvemshop, VTEX, temas genéricos)."""
    el = soup.find(attrs={"data-price-type": "oldPrice"})
    if el and el.get("data-price-amount"):
        return _num(el["data-price-amount"])
    meta = soup.find("meta", property="product:original_price:amount")
    if meta:
        return _num(meta.get("content"))
    padrao = re.compile(r"old-price|price-old|list-price|listprice|preco-de|precode|from-price|compare-at|price--compare|was-price|de-por", re.I)
    for el in soup.find_all(attrs={"class": padrao})[:5]:
        v = _num(re.sub(r"[^\d,\.]", "", el.get_text(" ")))
        if v and v > 1:
            return v
    return None


def validar_html(url_original: str, url_final: str, status: int, html: str,
                 renderizado: bool, sinal_externo: bool = False) -> ResultadoEstoque:
    """sinal_externo=True quando uma API da loja já confirmou estoque; aí a página
    só precisa não ter avisos negativos."""
    if status != 200:
        return ResultadoEstoque(False, f"HTTP {status}")
    if not url_eh_produto(url_original, url_final):
        return ResultadoEstoque(False, f"redirecionou para página genérica ({url_final})")
    if re.search(r"captcha|validatecaptcha|account-verification|robot check", url_final + html[:5000], re.I):
        return ResultadoEstoque(False, "bloqueado por verificação anti-robô (não contornamos)")

    soup = BeautifulSoup(html, "lxml")
    ld = ler_jsonld(soup)
    titulo = ld.get("titulo") or (soup.find("meta", property="og:title") or {}).get("content") \
        or (soup.h1.get_text(strip=True) if soup.h1 else None)
    base = dict(preco=ld.get("preco"), titulo=titulo, vendedor=ld.get("vendedor"),
                condicao=ld.get("condicao"), preco_lista=preco_lista_html(soup))
    if base["preco"] is None:
        meta = soup.find("meta", property="product:price:amount") or soup.find(itemprop="price")
        if meta:
            base["preco"] = _num(meta.get("content") or meta.get_text())

    disp = ld.get("availability")
    if disp in INDISPONIVEL:
        return ResultadoEstoque(False, f"JSON-LD diz {disp}", **base)

    aviso = _avisos_na_area_de_compra(soup)
    if aviso:
        return ResultadoEstoque(False, f"aviso na página: '{aviso}'", **base)

    botao = _botao_habilitado(soup)
    if botao is False:
        return ResultadoEstoque(False, "botão de compra desabilitado", **base)
    if botao is True:
        return ResultadoEstoque(True, "botão de compra ativo" + (f" + JSON-LD {disp}" if disp else ""), **base)

    # nenhum botão encontrado no HTML
    if sinal_externo:
        return ResultadoEstoque(True, "API da loja confirma estoque e página sem avisos", **base)
    if renderizado:
        return ResultadoEstoque(False, "página renderizada sem botão de compra", **base)
    if disp in DISPONIVEL:
        return ResultadoEstoque(True, f"JSON-LD {disp}, sem avisos (botão via JS)", **base)
    return ResultadoEstoque(False, "sem sinal confiável de estoque", **base)
