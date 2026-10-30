"""Sonda de lojas: roda no GitHub Actions (mesmo IP do radar) e grava o que cada
site responde em data/sonda/, para ajustar coletores sem adivinhar.

Uso: python tools/sondar.py [alvo1,alvo2,...]   (sem argumento = todos)
"""
from __future__ import annotations

import json
import re
import sys
import time
from pathlib import Path

import httpx

PASTA = Path(__file__).resolve().parents[1] / "data" / "sonda"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/130.0.0.0 Safari/537.36")
CAB = {"User-Agent": UA, "Accept-Language": "pt-BR,pt;q=0.9,en;q=0.8",
       "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"}

DICAS = {
    "vtex": r"vtex|vteximg", "magento": r"mage/|Magento|catalogsearch", "shopify": r"cdn\.shopify",
    "nuvemshop": r"nuvemshop|tiendanube|d26lpennugtm8s", "woocommerce": r"woocommerce|wp-content",
    "tray": r"tray\.com|traycdn", "loja_integrada": r"lojaintegrada", "wake": r"fbits|wake\.tech",
    "next": r"__NEXT_DATA__", "aspnet": r"__VIEWSTATE|\.aspx", "cloudflare": r"cf-chl|challenge-platform|Just a moment",
    "linx": r"linx|chaordic", "yampi": r"yampi", "vnda": r"vnda",
}

ALVOS: dict[str, list[tuple[str, str]]] = {
    "leitura": [
        ("home", "https://leitura.com.br/"),
        ("home-www", "https://www.leitura.com.br/"),
        ("busca-q", "https://www.leitura.com.br/busca?q=frieren"),
        ("busca-search", "https://www.leitura.com.br/search?q=frieren"),
        ("busca-pesquisa", "https://www.leitura.com.br/pesquisa?t=frieren"),
        ("vtex-api", "https://www.leitura.com.br/api/catalog_system/pub/products/search?ft=frieren&_from=0&_to=9"),
        ("vtex-ft", "https://www.leitura.com.br/frieren"),
        ("magento", "https://www.leitura.com.br/catalogsearch/result/?q=frieren"),
    ],
    "mundos": [
        ("home", "https://mundosinfinitos.com.br/"),
        ("home-www", "https://www.mundosinfinitos.com.br/"),
        ("busca", "https://mundosinfinitos.com.br/geek/solucoes/busca.aspx?t=frieren"),
        ("busca-www", "https://www.mundosinfinitos.com.br/geek/solucoes/busca.aspx?t=frieren"),
    ],
    "mirane": [
        ("produto", "https://miranecomics.com.br/produtos/sakamoto-days-16/"),
    ],
    "amazon": [
        ("busca", "https://www.amazon.com.br/s?k=frieren&i=stripbooks"),
        ("dp-bloqueado", "https://www.amazon.com.br/dp/652591311X"),
        ("dp-ok", "https://www.amazon.com.br/dp/6525935903"),
        ("aod", "https://www.amazon.com.br/gp/aod/ajax/ref=auto_load_aod?asin=652591311X&pc=dp"),
    ],
    "magalu": [
        ("busca", "https://www.magazineluiza.com.br/busca/frieren/"),
    ],
    "kabum": [
        ("api", "https://servicespub.prod.api.aws.grupokabum.com.br/catalog/v2/products?query=wolverine&page_number=1&page_size=20"),
        ("produto", "https://www.kabum.com.br/produto/1065184"),
    ],
}


def resumo(nome: str, url: str, r: httpx.Response | None, erro: str = "", via: str = "http") -> str:
    if r is None:
        return f"## {nome} [{via}]\nurl: {url}\nERRO: {erro}\n"
    corpo = r.text if hasattr(r, "text") else ""
    dicas = [k for k, rx in DICAS.items() if re.search(rx, corpo[:400000], re.I)]
    tit = re.search(r"<title[^>]*>(.*?)</title>", corpo, re.I | re.S)
    cab = {k: v for k, v in r.headers.items() if k.lower() in (
        "server", "content-type", "x-powered-by", "cf-ray", "x-vtex-cache-status", "set-cookie", "location", "via")}
    texto = re.sub(r"<script.*?</script>|<style.*?</style>", " ", corpo, flags=re.S | re.I)
    texto = re.sub(r"<[^>]+>", " ", texto)
    texto = re.sub(r"\s+", " ", texto)
    extras = []
    for rx in (r'data-variants="[^"]{0,1500}', r'"availability"[^,}]{0,80}', r'"price"\s*:\s*"?[\d.,]+',
               r'application/ld\+json[^<]{0,1200}'):
        for m in list(re.finditer(rx, corpo))[:3]:
            extras.append(m.group(0)[:1500])
    return (f"## {nome} [{via}]\nurl: {url}\nfinal: {r.url}\nstatus: {r.status_code}\n"
            f"cabecalhos: {json.dumps(cab, ensure_ascii=False)[:800]}\ntamanho: {len(corpo)}\n"
            f"titulo: {(tit.group(1).strip()[:200] if tit else '')}\nplataforma (dicas): {dicas}\n"
            f"trechos: {json.dumps(extras, ensure_ascii=False)[:5000]}\n"
            f"texto: {texto[:3000]}\n")


def com_navegador(urls):
    saida = {}
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return saida
    with sync_playwright() as p:
        b = p.chromium.launch()
        ctx = b.new_context(user_agent=UA, locale="pt-BR")
        pg = ctx.new_page()
        for nome, url in urls:
            try:
                resp = pg.goto(url, timeout=30000, wait_until="domcontentloaded")
                pg.wait_for_timeout(4000)
                html = pg.content()

                class R:  # imita httpx.Response
                    pass
                r = R(); r.text = html; r.url = pg.url; r.status_code = resp.status if resp else 0
                r.headers = dict(resp.headers) if resp else {}
                saida[nome] = resumo(nome, url, r, via="navegador")
            except Exception as e:
                saida[nome] = resumo(nome, url, None, str(e)[:300], via="navegador")
        b.close()
    return saida


def main():
    pedidos = set(sys.argv[1].split(",")) if len(sys.argv) > 1 and sys.argv[1] else set(ALVOS)
    PASTA.mkdir(parents=True, exist_ok=True)
    with httpx.Client(headers=CAB, follow_redirects=True, timeout=20) as c:
        for alvo, urls in ALVOS.items():
            if alvo not in pedidos:
                continue
            partes = [f"# sonda {alvo} em {time.strftime('%d/%m %H:%M')}\n"]
            for nome, url in urls:
                try:
                    partes.append(resumo(nome, url, c.get(url)))
                except Exception as e:
                    partes.append(resumo(nome, url, None, f"{type(e).__name__}: {e}"[:300]))
                time.sleep(1)
            nav = com_navegador(urls[:3])
            partes += list(nav.values())
            (PASTA / f"{alvo}.txt").write_text("\n".join(partes), encoding="utf-8")
            print(alvo, "ok")


if __name__ == "__main__":
    main()
