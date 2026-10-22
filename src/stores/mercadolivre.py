"""Mercado Livre.

Desde 2025 a API pública de busca devolve 403 sem token. Com ML_ACCESS_TOKEN
(app gratuito em developers.mercadolivre.com.br) usamos a API, que informa
status do anúncio (active/paused/closed) e quantidade disponível.
Sem token, cai para busca HTML no navegador, que pode pedir verificação.
"""
from __future__ import annotations

import logging
import os
import re
from urllib.parse import quote

from bs4 import BeautifulSoup

from models import Oferta
from stock import validar_html
from stores.base import Loja
from stores.plataformas import limpar_url, relevante

log = logging.getLogger("coleta")
API = "https://api.mercadolibre.com"


class MercadoLivreLoja(Loja):
    nome = "Mercado Livre"

    def __init__(self, fetcher, cfg):
        super().__init__(fetcher, cfg)
        self.token = os.getenv("ML_ACCESS_TOKEN", "").strip()

    def _h(self):
        return {"Authorization": f"Bearer {self.token}"} if self.token else None

    # ---------------- busca
    def buscar(self, termo):
        if self.token:
            status, dados = self.f.get(
                f"{API}/sites/MLB/search?q={quote(termo)}&limit={self.max_resultados}",
                json=True, headers=self._h())
            if status == 200 and dados:
                return self._de_api(dados.get("results", []), termo)
            log.warning("ML API respondeu %s; usando busca HTML.", status)
        return self._de_html(termo)

    def _de_api(self, resultados, termo):
        saida = []
        for it in resultados:
            if not relevante(it.get("title", ""), termo):
                continue
            saida.append(Oferta(
                loja=self.nome, titulo=it["title"], url=limpar_url(it["permalink"]),
                preco=float(it.get("price") or 0),
                preco_lista=float(it["original_price"]) if it.get("original_price") else None,
                condicao="usado" if it.get("condition") == "used" else "novo",
                vendedor=(it.get("seller") or {}).get("nickname"),
                extra={"ml_id": it["id"]},
            ))
        return saida

    def _de_html(self, termo):
        slug = re.sub(r"\s+", "-", termo.strip().lower())
        r = self.f.get_render(f"https://lista.mercadolivre.com.br/{quote(slug)}", "li.ui-search-layout__item")
        if r.status != 200 or "account-verification" in r.url_final:
            log.warning("ML: busca HTML bloqueada (HTTP %s).", r.status)
            return []
        soup = BeautifulSoup(r.html, "lxml")
        vistos, saida = set(), []
        for a in soup.find_all("a", href=True):
            href = a["href"]
            if not re.search(r"mercadolivre\.com\.br/.*(MLB-?\d+|/p/MLB\d+)", href):
                continue
            texto = a.get_text(" ", strip=True)
            if not texto or not relevante(texto, termo):
                continue
            url = limpar_url(href)
            if url in vistos:
                continue
            vistos.add(url)
            saida.append(Oferta(loja=self.nome, titulo=texto, url=url, preco=0))
            if len(saida) >= self.max_resultados:
                break
        return saida

    # ---------------- validação
    def validar(self, o):
        ml_id = o.extra.get("ml_id")
        if ml_id and self.token:
            status, it = self.f.get(f"{API}/items/{ml_id}", json=True, headers=self._h())
            if status == 200 and it:
                if it.get("status") != "active":
                    o.disponivel, o.motivo_estoque = False, f"ML: anúncio {it.get('status')} {it.get('sub_status') or ''}".strip()
                    return o
                if int(it.get("available_quantity") or 0) <= 0:
                    o.disponivel, o.motivo_estoque = False, "ML: sem estoque"
                    return o
                o.preco = float(it.get("price") or o.preco)
                if it.get("original_price"):
                    o.preco_lista = float(it["original_price"])
                o.disponivel, o.motivo_estoque = True, "ML API: active com estoque"
                return o
        # sem API: valida a página
        r = self.f.get_render(o.url, "form.ui-pdp-actions, .ui-pdp-buybox")
        res = validar_html(o.url, r.url_final, r.status, r.html, r.via == "browser")
        o.titulo = res.titulo or o.titulo
        o.preco = res.preco or o.preco
        o.preco_lista = res.preco_lista or o.preco_lista
        o.condicao = res.condicao or o.condicao
        o.vendedor = res.vendedor or o.vendedor
        o.disponivel, o.motivo_estoque = res.disponivel and bool(o.preco), res.motivo
        return o
