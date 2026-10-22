"""Amazon Brasil: busca na categoria Livros e valida na página /dp/ASIN.

A Amazon bloqueia robôs com frequência. Quando aparece CAPTCHA, o produto é
pulado nesta janela (o sistema nunca tenta resolver CAPTCHA).
"""
from __future__ import annotations

import logging
import re
from urllib.parse import quote_plus

from bs4 import BeautifulSoup

from models import Oferta
from stock import _num
from stores.base import Loja
from stores.plataformas import relevante
from texto import normalizar

log = logging.getLogger("coleta")
BASE = "https://www.amazon.com.br"


def _bloqueado(html: str, url: str) -> bool:
    return bool(re.search(r"validateCaptcha|captcha|Robot Check|api-services-support@amazon", url + html[:8000], re.I))


class AmazonLoja(Loja):
    nome = "Amazon Brasil"

    def buscar(self, termo):
        url = f"{BASE}/s?k={quote_plus(termo)}&i=stripbooks"
        r = self.f.get_render(url, 'div[data-component-type="s-search-result"]')
        if r.status != 200 or _bloqueado(r.html, r.url_final):
            log.warning("Amazon: busca bloqueada ou falhou (HTTP %s).", r.status)
            return []
        soup = BeautifulSoup(r.html, "lxml")
        saida = []
        for div in soup.select('div[data-component-type="s-search-result"]'):
            asin = div.get("data-asin")
            h2 = div.find("h2")
            if not asin or not h2:
                continue
            titulo = h2.get_text(" ", strip=True)
            if not relevante(titulo, termo):
                continue
            preco = div.select_one(".a-price .a-offscreen")
            saida.append(Oferta(
                loja=self.nome, titulo=titulo, url=f"{BASE}/dp/{asin}",
                preco=_num(preco.get_text()) if preco else 0, extra={"asin": asin},
            ))
            if len(saida) >= self.max_resultados:
                break
        return saida

    def validar(self, o):
        r = self.f.get_render(o.url, "#productTitle")
        if r.status != 200 or _bloqueado(r.html, r.url_final):
            o.disponivel, o.motivo_estoque = False, f"Amazon bloqueou ou falhou (HTTP {r.status})"
            return o
        s = BeautifulSoup(r.html, "lxml")

        titulo = s.select_one("#productTitle")
        if titulo:
            o.titulo = titulo.get_text(" ", strip=True)

        disp = normalizar(s.select_one("#availability").get_text(" ")) if s.select_one("#availability") else ""
        if re.search(r"indisponivel|nao disponivel|nao temos previsao|esgotado", disp):
            o.disponivel, o.motivo_estoque = False, f"Amazon: '{disp[:60]}'"
            return o

        novo = s.select_one("#add-to-cart-button, #buy-now-button")
        usado = s.select_one("#add-to-cart-button-ubb, #buy-now-button-ubb")
        botao = novo or usado
        if not botao or botao.has_attr("disabled"):
            o.disponivel, o.motivo_estoque = False, "Amazon: sem botão de compra ativo"
            return o
        o.condicao = "novo" if novo else "usado"

        preco = s.select_one(".priceToPay .a-offscreen, #corePrice_feature_div .a-offscreen, "
                             "#price, #kindle-price, #usedBuySection .a-color-price")
        if preco:
            o.preco = _num(preco.get_text()) or o.preco
        lista = s.select_one(".basisPrice .a-offscreen, span.a-price.a-text-price .a-offscreen, #listPrice")
        if lista:
            o.preco_lista = _num(lista.get_text())

        vend = s.select_one("#sellerProfileTriggerId, #merchantInfoFeature_feature_div "
                            ".offer-display-feature-text-message, #merchant-info a")
        o.vendedor = vend.get_text(" ", strip=True) if vend else "Amazon.com.br"

        o.disponivel = bool(o.preco)
        o.motivo_estoque = f"Amazon: botão {'novo' if novo else 'usado'} ativo" if o.preco else "Amazon: sem preço"
        return o
