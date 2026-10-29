"""Mercado Livre pela API oficial de CATÁLOGO.

Desde 2026 a busca de anúncios (/sites/MLB/search) devolve 403 mesmo com token,
e a busca pelo site bloqueia acessos vindos da nuvem. O que segue liberado é o
catálogo:

  1. POST /oauth/token (grant_type=client_credentials)  -> acesso temporário
  2. GET  /products/search?site_id=MLB&q=...              -> produtos do catálogo
  3. GET  /products/{id}/items                            -> ofertas ativas dos vendedores

Precisa de um app grátis no portal de desenvolvedores do Mercado Livre:
ML_CLIENT_ID e ML_CLIENT_SECRET (Secrets do GitHub). Sem eles, a loja é pulada.

Limite: só aparecem produtos de catálogo (livros/mangás novos, com ISBN). Anúncios
avulsos de usados e lotes de sebo costumam ficar de fora.
"""
from __future__ import annotations

import logging
import os
import time
from urllib.parse import quote

import httpx

from models import Oferta
from stores.base import Loja
from stores.plataformas import relevante

log = logging.getLogger("coleta")
API = "https://api.mercadolibre.com"


def link_anuncio(item_id: str) -> str:
    """MLB1234567 -> https://produto.mercadolivre.com.br/MLB-1234567"""
    item_id = str(item_id or "")
    if item_id.upper().startswith("MLB") and "-" not in item_id:
        return f"https://produto.mercadolivre.com.br/MLB-{item_id[3:]}"
    return f"https://produto.mercadolivre.com.br/{item_id}"


class MercadoLivreLoja(Loja):
    nome = "Mercado Livre"
    categorias_padrao = ["livros", "games"]
    base_padrao = "https://www.mercadolivre.com.br"

    def __init__(self, fetcher, cfg):
        super().__init__(fetcher, cfg)
        self.client_id = os.getenv("ML_CLIENT_ID", "").strip()
        self.client_secret = os.getenv("ML_CLIENT_SECRET", "").strip()
        self.token: str | None = None
        self.token_expira = 0.0
        self.desligado = False           # sem credenciais ou acesso recusado: não insiste
        self.produtos_por_busca = int(cfg.get("produtos_por_busca", 6))
        # o domínio dos links de anúncio, para os links fixos (urls) baterem com esta loja
        self.dominio = "mercadolivre.com.br"

    # ---------------- acesso
    def _obter_token(self) -> str | None:
        if self.desligado:
            return None
        if self.token and time.time() < self.token_expira - 60:
            return self.token
        if not (self.client_id and self.client_secret):
            log.info("Mercado Livre: ML_CLIENT_ID/ML_CLIENT_SECRET não configurados; loja pulada.")
            self.desligado = True
            return None
        try:
            r = self.f.http.post(f"{API}/oauth/token", data={
                "grant_type": "client_credentials",
                "client_id": self.client_id,
                "client_secret": self.client_secret,
            }, headers={"Accept": "application/json"})
        except httpx.HTTPError as e:
            log.warning("Mercado Livre: falha ao pedir acesso (%s).", e)
            self.desligado = True
            return None
        if r.status_code != 200:
            log.warning("Mercado Livre: acesso recusado (HTTP %s): %s", r.status_code, r.text[:200])
            self.desligado = True
            return None
        dados = r.json()
        self.token = dados.get("access_token")
        self.token_expira = time.time() + int(dados.get("expires_in") or 21600)
        return self.token

    def _get(self, caminho: str):
        token = self._obter_token()
        if not token:
            return 0, None
        status, dados = self.f.get(f"{API}{caminho}", json=True,
                                   headers={"Authorization": f"Bearer {token}"})
        if status in (401, 403):
            log.warning("Mercado Livre: %s em %s; desligando a loja nesta varredura.", status, caminho)
            self.desligado = True
        return status, dados

    # ---------------- busca
    def buscar(self, termo):
        if self.desligado:
            return []
        status, dados = self._get(f"/products/search?status=active&site_id=MLB&q={quote(termo)}"
                                  f"&limit={self.produtos_por_busca}")
        if status != 200 or not isinstance(dados, dict):
            return []
        saida = []
        for p in dados.get("results") or []:
            nome = p.get("name") or ""
            pid = p.get("id")
            if not pid or not relevante(nome, termo):
                continue
            oferta = self._melhor_oferta(pid, nome)
            if oferta:
                saida.append(oferta)
            if self.desligado:
                break
        return saida

    def _melhor_oferta(self, produto_id: str, nome: str) -> Oferta | None:
        status, dados = self._get(f"/products/{produto_id}/items?limit=20")
        if status != 200 or not isinstance(dados, dict):
            return None
        ofertas = [i for i in (dados.get("results") or [])
                   if i.get("item_id") and float(i.get("price") or 0) > 0
                   and (i.get("currency_id") in (None, "BRL"))]
        if not ofertas:
            return None
        melhor = min(ofertas, key=lambda i: float(i["price"]))
        preco = float(melhor["price"])
        lista = melhor.get("original_price")
        return Oferta(
            loja=self.nome, titulo=nome, url=link_anuncio(melhor["item_id"]), preco=preco,
            preco_lista=float(lista) if lista and float(lista) > preco else None,
            condicao="usado" if melhor.get("condition") == "used" else "novo",
            vendedor=None,
            extra={"ml_produto": produto_id, "ml_item": melhor["item_id"], "ml_ofertas": len(ofertas)},
        )

    # ---------------- validação
    def validar(self, o):
        # /products/{id}/items só lista ofertas ATIVAS (com estoque), e a página do
        # anúncio é bloqueada para acessos da nuvem: a API é a fonte do estoque aqui.
        if o.extra.get("ml_item"):
            o.disponivel = True
            o.motivo_estoque = f"ML API: oferta ativa no catálogo ({o.extra.get('ml_ofertas')} vendedores)"
        else:
            o.disponivel, o.motivo_estoque = False, "ML: link sem dados da API"
        return o
