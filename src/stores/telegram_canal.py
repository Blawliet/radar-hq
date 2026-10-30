"""Canal PÚBLICO de ofertas do Telegram como fonte do radar (só leitura).

O Telegram mostra as últimas mensagens de canais públicos em t.me/s/<canal>, sem
login. Cada post com preço vira uma oferta, e passa pelas mesmas regras das lojas
(listas, coleção, raros, mínimo de desconto). O link vai como veio no post, para
o dono do canal continuar recebendo a comissão de afiliado.

Em lojas.yaml:

    - nome: "Telegram @mangasluofertas"
      adaptador: telegram
      canal: mangasluofertas
      horas: 6            # só posts das últimas N horas
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse

from bs4 import BeautifulSoup

from models import Oferta
from stock import _num
from stores.base import Loja

log = logging.getLogger("coleta")

LOJAS_POR_DOMINIO = {
    "amazon": "Amazon", "amzn": "Amazon", "shopee": "Shopee", "shope.ee": "Shopee",
    "mercadolivre": "Mercado Livre", "mercadolibre": "Mercado Livre", "meli.la": "Mercado Livre",
    "magalu": "Magalu", "magazineluiza": "Magalu", "magazinevoce": "Magalu", "kabum": "KaBuM!",
    "panini": "Loja Panini", "aliexpress": "AliExpress", "americanas": "Americanas",
}
NOMES_LOJA = ["Amazon", "Shopee", "Mercado Livre", "Magalu", "Magazine Luiza", "KaBuM!", "Panini",
              "Americanas", "AliExpress", "Submarino", "Estante Virtual", "Leitura"]
RX_PRECO = re.compile(r"R\$\s*(\d{1,3}(?:\.\d{3})*,\d{2}|\d+,\d{2}|\d+)\s*(?:\((\d{1,2})\s*%\))?", re.I)


def _loja_do_link(url: str) -> str | None:
    host = urlparse(url).netloc.lower()
    for chave, nome in LOJAS_POR_DOMINIO.items():
        if chave in host:
            return nome
    return None


def ler_posts(html: str) -> list[dict]:
    """Extrai os posts da página t.me/s/<canal>."""
    soup = BeautifulSoup(html, "lxml")
    posts = []
    for msg in soup.select("div.tgme_widget_message[data-post]"):
        corpo = msg.select_one(".tgme_widget_message_text")
        if not corpo:
            continue
        for br in corpo.find_all("br"):
            br.replace_with("\n")
        texto = corpo.get_text()
        links = [a["href"] for a in corpo.find_all("a", href=True)]
        quando = msg.select_one("time[datetime]")
        posts.append({
            "id": msg["data-post"],
            "texto": texto.strip(),
            "links": links,
            "quando": quando["datetime"] if quando else None,
        })
    return posts


def post_para_oferta(post: dict, canal: str) -> Oferta | None:
    linhas = [l.strip() for l in post["texto"].splitlines() if l.strip()]
    if not linhas:
        return None
    m = None
    for l in linhas:
        m = RX_PRECO.search(l)
        if m and not re.search(r"\b(off|acima|cupom|frete)\b", l, re.I):
            break
        m = None
    if not m:
        return None                                       # cupom ou aviso sem preço de produto
    preco = _num(m.group(1)) or 0
    if preco <= 0:
        return None
    pct = int(m.group(2)) if m.group(2) else None
    lista = round(preco / (1 - pct / 100), 2) if pct and 0 < pct < 95 else None
    titulo = linhas[0]
    loja = None
    for nome in NOMES_LOJA:
        if re.search(rf"\|\s*{re.escape(nome)}\s*$", titulo, re.I):
            loja = "Magalu" if nome == "Magazine Luiza" else nome
            titulo = re.sub(rf"\s*\|\s*{re.escape(nome)}\s*$", "", titulo, flags=re.I)
            break
    link = None
    for u in post["links"]:
        host = urlparse(u).netloc.lower()
        if not host or host.endswith("t.me") or host.endswith("telegram.me"):
            continue
        if _loja_do_link(u) or link is None:
            link = u
            if _loja_do_link(u):
                break
    if not link:
        return None
    loja = loja or _loja_do_link(link) or "loja"
    return Oferta(loja=f"{loja} (via @{canal})", titulo=titulo[:200], url=link, preco=preco,
                  preco_lista=lista, extra={"telegram": post["id"], "telegram_quando": post["quando"],
                                            "loja_original": loja})


class TelegramCanalLoja(Loja):
    nome = "Telegram"
    categorias_padrao = ["livros", "games"]

    def __init__(self, fetcher, cfg):
        super().__init__(fetcher, cfg)
        self.canal = str(cfg["canal"]).lstrip("@")
        self.horas = float(cfg.get("horas", 6))
        self.dominio = ""                    # nenhum link fixo é deste "site"
        self._lido = False

    def buscar(self, termo):
        # o canal não tem busca: lê os posts recentes uma vez por varredura
        if self._lido:
            return []
        self._lido = True
        r = self.f.get(f"https://t.me/s/{self.canal}")
        status, html = r.status, r.html
        if status != 200 or not html:
            log.info("Telegram @%s: página respondeu HTTP %s", self.canal, status)
            return []
        limite = datetime.now(timezone.utc) - timedelta(hours=self.horas)
        saida = []
        for p in ler_posts(html):
            try:
                quando = datetime.fromisoformat(p["quando"]) if p["quando"] else None
            except ValueError:
                quando = None
            if quando and quando < limite:
                continue
            o = post_para_oferta(p, self.canal)
            if o:
                saida.append(o)
        log.info("Telegram @%s: %d posts com preço nas últimas %.0f h", self.canal, len(saida), self.horas)
        return saida

    def validar(self, o):
        # o post é a fonte: a oferta foi publicada há pouco; o estoque não dá para conferir
        # sem abrir a loja (Amazon e Shopee bloqueiam acessos da nuvem)
        o.disponivel = True
        hora = ""
        try:
            q = datetime.fromisoformat(o.extra.get("telegram_quando") or "")
            hora = (q - timedelta(hours=3)).strftime(" às %Hh%M")          # horário de Brasília
        except ValueError:
            pass
        o.motivo_estoque = f"postado no canal @{self.canal}{hora}"
        return o
