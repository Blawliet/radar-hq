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
    "amzlink": "Amazon",
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
    """Extrai os posts da página t.me/s/<canal>: cada linha do texto com os links dela."""
    soup = BeautifulSoup(html, "lxml")
    posts = []
    for msg in soup.select("div.tgme_widget_message[data-post]"):
        corpo = msg.select_one(".tgme_widget_message_text")
        if not corpo:
            continue
        links = []
        for a in corpo.find_all("a", href=True):
            links.append(a["href"])
            a.replace_with(f" [[L{len(links) - 1}]] ")
        for br in corpo.find_all("br"):
            br.replace_with("\n")
        linhas = []
        for bruta in corpo.get_text().splitlines():
            idx = [int(i) for i in re.findall(r"\[\[L(\d+)\]\]", bruta)]
            texto = re.sub(r"\s+", " ", re.sub(r"\[\[L\d+\]\]", " ", bruta)).strip()
            if texto or idx:
                linhas.append({"texto": texto, "links": [links[i] for i in idx]})
        quando = msg.select_one("time[datetime]")
        posts.append({"id": msg["data-post"], "linhas": linhas, "links": links,
                      "quando": quando["datetime"] if quando else None})
    return posts


RX_DE_POR = re.compile(r"de\s*R\$\s*([\d.,]+)\s*(?:por|→|->|➡️?|>)\s*R\$\s*([\d.,]+)", re.I)
RX_CUPOM = re.compile(r"R\$\s*[\d.,]+\s*off|\d+\s*%\s*off\s+(no|na|em|acima)|acima de|\bcupom|cashback|\bparcel", re.I)
RX_SEPARADOR = re.compile(r"\s*(?:》|»|➡️?|➜|→|->|\|\s*de\s*R\$|\bde\s*R\$|R\$).*$", re.I)


def _limpar_titulo(t: str) -> tuple[str, str | None]:
    """Tira enfeites e o '| Loja' do fim. Devolve (título, loja citada)."""
    loja = None
    for nome in NOMES_LOJA:
        if re.search(rf"\|\s*{re.escape(nome)}\s*$", t, re.I):
            loja = "Magalu" if nome == "Magazine Luiza" else nome
            t = re.sub(rf"\s*\|\s*{re.escape(nome)}\s*$", "", t, flags=re.I)
            break
    t = re.sub(r"^[^\w(\[]+", "", t)                       # emojis e setas do começo
    t = re.sub(r"\[(promo|oferta|pr[ée]-?venda|apenas[^\]]*|destaque[^\]]*)\]\s*", "", t, flags=re.I)
    return t.strip(" -–|:"), loja


def _parece_titulo(t: str) -> bool:
    if len(t) < 4 or "R$" in t or RX_CUPOM.search(t):
        return False
    letras = [c for c in t if c.isalpha()]
    # cabeçalho todo em maiúsculas com emoji ("DESTAQUE DA MEGA OFERTA") não é título
    return bool(letras) and not (sum(c.isupper() for c in letras) / len(letras) > 0.8 and len(letras) > 12)


def _melhor_link(links: list[str]) -> str | None:
    bons = [u for u in links if urlparse(u).netloc and not urlparse(u).netloc.lower().endswith(("t.me", "telegram.me"))]
    for u in bons:
        if _loja_do_link(u) or "amzlink" in u or "bit.ly" in u:
            return u
    return bons[0] if bons else None


def post_para_ofertas(post: dict, canal: str) -> list[Oferta]:
    """Um post pode ter um ou vários produtos. Cada linha com preço de produto vira
    uma oferta; o título é o texto antes do preço ou a linha de cima."""
    linhas = post["linhas"]
    saida = []
    for i, ln in enumerate(linhas):
        t = ln["texto"]
        if "R$" not in t or RX_CUPOM.search(t):
            continue
        m2 = RX_DE_POR.search(t)
        if m2:
            lista, preco = _num(m2.group(1)), _num(m2.group(2))
            mp = re.search(r"\((\d{1,2})\s*%", t)
            pct = int(mp.group(1)) if mp else None
        else:
            m = RX_PRECO.search(t)
            if not m:
                continue
            preco = _num(m.group(1))
            pct = int(m.group(2)) if m.group(2) else None
            lista = round(preco / (1 - pct / 100), 2) if preco and pct and 0 < pct < 95 else None
        if not preco or preco <= 0:
            continue
        sem_inicio = re.sub(r"^[^\w(\[]+", "", t)                # setas/emojis do começo
        titulo = re.sub(r"\s+(por|de|a partir de|só)\s*$", "", RX_SEPARADOR.sub("", sem_inicio).strip(), flags=re.I)
        if not _parece_titulo(titulo):
            # linha só com preço: o título é a linha de cima (que não seja outro item com link)
            titulo = next((linhas[j]["texto"] for j in range(i - 1, max(-1, i - 4), -1)
                           if _parece_titulo(linhas[j]["texto"]) and not linhas[j]["links"]), "")
        titulo, loja = _limpar_titulo(titulo)
        if len(titulo) < 3:
            continue
        # link: o da própria linha, senão o das linhas seguintes até o próximo preço
        cand = list(ln["links"])
        for j in range(i + 1, min(len(linhas), i + 4)):
            if "R$" in linhas[j]["texto"]:
                break
            cand += linhas[j]["links"]
        link = _melhor_link(cand)
        if not link:
            continue
        loja = loja or _loja_do_link(link) or ("Amazon" if "amzlink" in link else "loja")
        saida.append(Oferta(loja=f"{loja} (via @{canal})", titulo=titulo[:200], url=link, preco=preco,
                            preco_lista=lista if lista and lista > preco else None,
                            extra={"telegram": post["id"], "telegram_quando": post["quando"],
                                   "loja_original": loja}))
    return saida


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
            saida.extend(post_para_ofertas(p, self.canal))
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
