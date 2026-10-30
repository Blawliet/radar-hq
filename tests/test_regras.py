import os
import sys
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ / "src"))

from mensagem import campos, montar  # noqa: E402
from whatsapp import MetaCloudClient  # noqa: E402
from models import Oferta  # noqa: E402
from rules import Catalogo, avaliar  # noqa: E402
from stock import validar_html  # noqa: E402
from texto import extrair_volume, normalizar  # noqa: E402

os.environ["RADAR_COLECAO_FIXTURES"] = str(RAIZ / "tests" / "fixtures")   # coleção de exemplo
CAT = Catalogo(RAIZ / "config" / "catalogo.yaml", RAIZ / "config" / "pessoas" / "bruno.yaml")


def of(titulo, preco=50.0, lista=None, cond="novo"):
    return Oferta(loja="Teste", titulo=titulo, url="https://loja.com/p/x", preco=preco,
                  preco_lista=lista, condicao=cond)


@pytest.mark.parametrize("titulo,vol", [
    ("A Saga do Flash Vol. 8", 8),
    ("A SAGA DO FLASH - VOLUME 14", 14),
    ("One Piece Nº 97", 97),
    ("One Piece - 103", 103),
    ("Absolute Batman 2", 2),
    ("Spawn Origens Vol.10 (Capa Dura)", 10),
    ("Blueberry: Edição Definitiva (Vol. 3 de 4)", 3),
    ("Battle Royale Omnibus #4", 4),
    ("One Piece 3 em 1 - Vol. 33", 33),
    ("ONE PIECE - 103 (Panini)", 103),
    ("Frieren 5 - Panini Mangá Novo Lacrado", 5),
    ("Tartarugas Ninja 4 Pipoca e Nanquim", 4),
])
def test_volume(titulo, vol):
    assert extrair_volume(normalizar(titulo)) == vol


# ---------------- Regra A
@pytest.mark.parametrize("titulo", [
    "A Saga do Flash Vol. 8", "A Saga do Flash Vol. 14", "One Piece Vol. 97", "One Piece Vol. 104",
    "Blueberry: Edição Definitiva Vol. 4", "Supergirl por Peter David Vol. 5",
    "A Saga do Lanterna Verde Vol. 3", "A Saga do Lanterna Verde Vol. 7", "Absolute Batman Vol. 2",
    "Quarteto Fantástico por Jonathan Hickman Omnibus Vol. 2",
])
def test_raros_alertam_mesmo_caro(titulo):
    d = avaliar(of(titulo, preco=500, lista=None), CAT, ref_capa=100)
    assert d.alertar and d.regra == "A", (titulo, d.motivo)


@pytest.mark.parametrize("titulo", [
    "A Saga do Flash Vol. 9", "One Piece Vol. 99", "Absolute Batman Vol. 1",
    "One Piece 3 em 1 Vol. 97",
])
def test_raros_fora_da_lista(titulo):
    assert not avaliar(of(titulo, preco=10, lista=100), CAT).alertar


# ---------------- Regra C
@pytest.mark.parametrize("titulo", [
    "Battle Royale Omnibus Vol. 2", "Battle Royale Omnibus Vol. 5", "Demon Slayer Vol. 12",
    "Kimetsu no Yaiba Box", "Zero Hora - Grandes Eventos DC", "Invencível Vol. 1",
    "Invencível Vol. 2", "Invencível Vol. 7", "Box Invencível Panini HQ", "Spawn Vol. 3", "Spawn Origens Vol. 11", "Spawn Origens Vol. 4",
    "Box Gantz Caixa 1 (Vols. 1 ao 18)",
])
def test_bloqueios(titulo):
    d = avaliar(of(titulo, preco=10, lista=100), CAT, ref_capa=100)
    assert not d.alertar and "bloqueado" in d.motivo, (titulo, d.motivo)


# ---------------- Regra B
def test_promo_com_desconto_suficiente():
    d = avaliar(of("Battle Royale Omnibus Vol. 3", preco=74.9, lista=None), CAT, ref_capa=100)
    assert d.alertar and d.regra == "B" and d.desconto_pct == pytest.approx(25.1)


def test_promo_desconto_insuficiente():
    assert not avaliar(of("Battle Royale Omnibus Vol. 3", preco=160), CAT, ref_capa=100).alertar


def test_promo_sem_capa_nao_alerta():
    d = avaliar(of("Battle Royale Omnibus Vol. 3", preco=50), CAT, ref_capa=None)
    assert not d.alertar and "capa" in d.motivo


def test_preco_de_inflado_e_ignorado():
    # loja diz "de R$ 200 por R$ 90", mas a capa real é 100: só 10% off
    d = avaliar(of("Battle Royale Omnibus Vol. 4", preco=90, lista=200), CAT, ref_capa=100)
    assert not d.alertar and d.preco_capa == 100


def test_box_gantz_parte_2_nao_e_bloqueado():
    d = avaliar(of("Box Gantz Parte 2 - Vols. 19 ao 37", preco=300), CAT, ref_capa=500)
    assert d.alertar and d.entrada_id == "box-gantz-parte-2"


def test_radar_omnibus():
    d = avaliar(of("Homem-Aranha por Todd McFarlane Omnibus", preco=300), CAT, ref_capa=450)
    assert d.alertar and d.regra == "RADAR" and d.formato == "Omnibus"


def test_battle_royale_vol_6_nao_cai_no_radar():
    assert not avaliar(of("Battle Royale Omnibus Vol. 6", preco=10), CAT, ref_capa=100).alertar


@pytest.mark.parametrize("titulo,alerta", [
    ("Frieren Vol. 1", False), ("Frieren Vol. 2", True), ("Sakamoto Days Vol. 1", False),
    ("Sakamoto Days Vol. 5", True), ("Hunter x Hunter Vol. 37", False), ("Hunter x Hunter Vol. 38", True),
    ("Dandadan Vol. 1", False), ("Tartarugas Ninja Vol. 1 (Pipoca & Nanquim)", True),
])
def test_series_em_andamento(titulo, alerta):
    assert avaliar(of(titulo, preco=70), CAT, ref_capa=100).alertar is alerta, titulo


@pytest.mark.parametrize("titulo", [
    "Box Chainsaw Man - Volumes 1 ao 11 (Panini)",
    "Kit Mangá Chainsaw Man 1 a 5",
    "Coleção Completa Vagabond Mangá - 37 volumes",
])
def test_radar_boxes_sugere(titulo):
    d = avaliar(of(titulo, preco=300), CAT, ref_capa=450)
    assert d.alertar and d.entrada_id == "radar-boxes" and d.formato == "Box", (titulo, d.motivo)


@pytest.mark.parametrize("titulo", [
    "Box Demon Slayer Kimetsu no Yaiba Mangá 1 ao 23",   # bloqueado: já tem
    "Box Hunter x Hunter Mangá Vol 1 ao 10",             # série do catálogo, volumes que já tem
    "Box Gantz Caixa 1 Panini (Vols. 1 ao 18)",          # bloqueado
    "Box Harry Potter 7 livros",                         # não é quadrinho
    "Kit Poster Mangá Naruto",                           # não é livro
])
def test_radar_boxes_ignora(titulo):
    assert not avaliar(of(titulo, preco=10), CAT, ref_capa=450).alertar, titulo


def test_fora_do_catalogo():
    assert not avaliar(of("Livro de receitas", preco=1), CAT, ref_capa=100).alertar


# ---------------- Estoque
HTML_OK = """<html><head><script type="application/ld+json">
{"@type":"Product","name":"A Saga do Flash Vol. 8","offers":{"@type":"Offer","price":"89.90",
"availability":"https://schema.org/InStock"}}</script></head>
<body><h1>A Saga do Flash Vol. 8</h1><div class="product-info"><button class="btn-comprar">Comprar</button></div>
<div class="vitrine"><span>Produto esgotado</span></div></body></html>"""

HTML_ESGOTADO = """<html><body><h1>One Piece 97</h1>
<div class="product-buy-box"><p>Produto esgotado</p><button disabled>Comprar</button></div></body></html>"""

HTML_LD_FORA = """<html><head><script type="application/ld+json">
{"@type":"Product","offers":{"price":10,"availability":"http://schema.org/OutOfStock"}}</script></head>
<body><button>Comprar</button></body></html>"""

HTML_ML_PAUSADO = "<html><body><h1>Spawn 10</h1><p>Anúncio pausado</p><button>Comprar agora</button></body></html>"


def test_estoque_ok_ignora_esgotado_da_vitrine():
    r = validar_html("https://l.com/p/1", "https://l.com/p/1", 200, HTML_OK, True)
    assert r.disponivel and r.preco == 89.9


@pytest.mark.parametrize("html", [HTML_ESGOTADO, HTML_LD_FORA, HTML_ML_PAUSADO])
def test_estoque_rejeita(html):
    assert not validar_html("https://l.com/p/1", "https://l.com/p/1", 200, html, True).disponivel


def test_redirect_para_home_rejeita():
    assert not validar_html("https://l.com/p/1", "https://l.com/", 200, HTML_OK, True).disponivel


def test_redirect_para_busca_rejeita():
    assert not validar_html("https://l.com/p/1", "https://l.com/busca?q=flash", 200, HTML_OK, True).disponivel


def test_http_404_rejeita():
    assert not validar_html("https://l.com/p/1", "https://l.com/p/1", 404, HTML_OK, True).disponivel


# ---------------- Mensagem
def test_formato_mensagem():
    o = of("Battle Royale Omnibus Vol. 3", preco=74.9)
    o.loja, o.vendedor, o.url = "Mercado Livre", "SEBO DO ZÉ", "https://produto.mercadolivre.com.br/MLB-1"
    d = avaliar(o, CAT, ref_capa=119.9)
    txt = montar(o, d)
    assert txt.startswith("🚨 *NOVA OFERTA ENCONTRADA!*\n\n📖 *Título:* Battle Royale Omnibus Vol. 3")
    assert "📦 *Formato:* Omnibus" in txt
    assert "🏪 *Loja:* Mercado Livre (vendedor: SEBO DO ZÉ)" in txt
    assert "⚡ *Status:* Novo Lacrado" in txt
    assert "🏷️ *Preço de Capa:* R$ 119,90" in txt
    assert "💰 *Você Paga:* *R$ 74,90*" in txt
    assert "📉 *Desconto:* *38% OFF* (Economia de R$ 45,00)" in txt
    assert txt.endswith("🔗 *Link direto de compra:*\nhttps://produto.mercadolivre.com.br/MLB-1")
    assert "—" not in txt


# ---------------- Template da Meta
def test_template_meta_payload():
    o = of("Spawn Origens\nVol.   12", preco=150)
    o.url = "https://loja.com/p/spawn12"
    d = avaliar(o, CAT, ref_capa=120)
    valores = campos(o, d)
    assert len(valores) == 8
    assert all("\n" not in v and "    " not in v for v in valores)
    p = MetaCloudClient("123", "tok").payload_template("5511999998888", valores)
    assert p["template"]["name"] == "alerta_oferta"
    assert p["template"]["language"]["code"] == "pt_BR"
    params = p["template"]["components"][0]["parameters"]
    assert [x["text"] for x in params][-1] == "https://loja.com/p/spawn12"
    assert params[3]["text"] == "Novo Lacrado"



# ---------------- Telegram
def test_telegram_html_e_botao():
    from whatsapp import TelegramClient
    o = of("Box Gantz Parte 2 <19 ao 37> & extras", preco=300)
    o.url = "https://loja.com/p/gantz2"
    d = avaliar(o, CAT, ref_capa=500)
    texto = montar(o, d)
    p = TelegramClient("tok", "123").payload(texto, campos(o, d)[-1])
    assert p["parse_mode"] == "HTML" and p["chat_id"] == "123"
    assert "<b>NOVA OFERTA ENCONTRADA!</b>" in p["text"]
    assert "<b>Você Paga:</b> <b>R$ 300,00</b>" in p["text"]
    assert "&lt;19 ao 37&gt; &amp; extras" in p["text"]
    assert "*" not in p["text"]
    assert p["reply_markup"]["inline_keyboard"][0][0]["url"] == "https://loja.com/p/gantz2"


def test_telegram_varios_destinos(monkeypatch):
    from whatsapp import TelegramClient
    enviados = []
    cli = TelegramClient("tok", "111, 222,-100333")
    monkeypatch.setattr(cli, "_post", lambda p: enviados.append(p["chat_id"]) or p["chat_id"] != "222")
    assert cli.enviar_alerta("", ["x"] * 7 + ["https://l.com/p"], "*oi*")
    assert enviados == ["111", "222", "-100333"]


# ---------------- Vários perfis
def _grupo_teste(tmp_path):
    import shutil
    from rules import Grupo
    (tmp_path / "pessoas").mkdir()
    shutil.copy(RAIZ / "config" / "catalogo.yaml", tmp_path / "catalogo.yaml")
    shutil.copy(RAIZ / "config" / "pessoas" / "bruno.yaml", tmp_path / "pessoas" / "bruno.yaml")
    (tmp_path / "pessoas" / "ivan.yaml").write_text("""
nome: Ivan
radares: [radar-boxes]
bloqueios:
  - id: op-tem
    termos: [one piece]
    volumes: "1-100"
promocoes:
  - id: one-piece
    nome: One Piece
    termos: [one piece]
    volumes: "101+"
    buscas: [one piece]
raros: []
""", encoding="utf-8")
    return Grupo(tmp_path)


def test_perfis_independentes(tmp_path):
    g = _grupo_teste(tmp_path)
    assert [p.nome for p in g.perfis] == ["Bruno", "Ivan"]
    bruno, ivan = g.perfis
    # One Piece 103: raro para o Bruno, promo para o Ivan
    d_b = avaliar(of("One Piece Vol. 103", preco=90), bruno, ref_capa=40)
    d_i = avaliar(of("One Piece Vol. 103", preco=90), ivan, ref_capa=40)
    assert d_b.alertar and d_b.regra == "A" and not d_i.alertar
    d_i2 = avaliar(of("One Piece Vol. 103", preco=25), ivan, ref_capa=40)
    assert d_i2.alertar and d_i2.regra == "B"
    # One Piece 97: Ivan bloqueou; Bruno quer
    assert not avaliar(of("One Piece Vol. 97", preco=10), ivan, ref_capa=40).alertar
    # Box Demon Slayer: bloqueado para o Bruno, sugestão para o Ivan
    box = of("Box Demon Slayer Mangá 1 ao 23", preco=300)
    assert not avaliar(box, bruno, ref_capa=500).alertar
    assert avaliar(box, ivan, ref_capa=500).alertar
    # Omnibus: radar ligado só para o Bruno
    omni = of("X-Men Omnibus Vol. 1", preco=300)
    assert avaliar(omni, bruno, ref_capa=500).alertar and not avaliar(omni, ivan, ref_capa=500).alertar
    assert ("x", "one piece") in [("x", b) for _, b, _ in g.todas_buscas()]


def test_mensagem_com_interessados():
    o = of("One Piece Vol. 103", preco=90)
    d = avaliar(o, CAT, ref_capa=40)
    txt = montar(o, d, ["Bruno", "Ivan", "Thiago"])
    assert txt.startswith("🚨 *NOVA OFERTA ENCONTRADA!*\n👥 *Para:* Bruno, Ivan e Thiago\n\n📖")
    assert "👥" not in montar(o, d)


def test_resumo_perfil():
    from rules import resumo_perfil, volumes_legivel
    assert volumes_legivel("9+") == "a partir do vol. 9"
    assert volumes_legivel("10,12+") == "vol. 10 e do 12 em diante"
    r = resumo_perfil(CAT, {"radar-omnibus": "Omnibus", "radar-boxes": "Boxes e coleções"})
    assert "Radar de Bruno" in r and "Hunter x Hunter: a partir do vol. 38" in r and "Omnibus, Boxes" in r


# ---------------- Histórico de preços
def test_historico_de_precos(tmp_path):
    from datetime import datetime, timedelta
    from db import Banco
    from mensagem import linha_historico
    b = Banco(tmp_path / "r.db")
    ontem = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
    assert b.menor_preco_anterior("one piece:103", "https://a/1") is None       # nunca visto
    b.con.execute("INSERT INTO precos VALUES ('one piece:103','https://a/1',?,52.9,'Amazon Brasil')", (ontem,))
    b.con.execute("INSERT INTO precos VALUES ('one piece:103','https://b/2',?,49.9,'Loja Panini')", (ontem,))
    b.registrar_preco("one piece:103", "https://c/3", 44.9, "Rika Comic Shop")
    ant = b.menor_preco_anterior("one piece:103", "https://c/3")
    assert ant[0] == 49.9 and ant[1] == "Loja Panini"
    assert linha_historico(44.9, ant).startswith("🏆 *MENOR PREÇO JÁ VISTO PELO RADAR!* (antes: R$ 49,90, Loja Panini,")
    assert linha_historico(49.9, ant).startswith("🏆 *Igual ao menor")
    assert linha_historico(60.0, ant).startswith("📊 *Menor já visto:* R$ 49,90 (Loja Panini,")
    assert linha_historico(10.0, None) is None
    # mesmo link, mesmo dia: guarda o menor
    b.registrar_preco("one piece:103", "https://c/3", 47.0, "Rika Comic Shop")
    assert b.con.execute("SELECT preco FROM precos WHERE url='https://c/3'").fetchone()[0] == 44.9


def test_chave_produto_igual_entre_lojas():
    import main
    from rules import Grupo
    g = Grupo(RAIZ / "config")
    a = main.chave_produto(of("One Piece Vol. 103"), g)
    b = main.chave_produto(of("ONE PIECE - 103 (Panini)"), g)
    assert a == b == "one piece:103"
    x = main.chave_produto(of("X-Men Omnibus Vol. 1 Capa Dura Panini"), g)
    y = main.chave_produto(of("X-Men Omnibus Vol. 1"), g)
    assert x == y


def test_mensagem_com_historico():
    o = of("One Piece Vol. 103", preco=44.9)
    d = avaliar(o, CAT, ref_capa=49.9)
    txt = montar(o, d, ["Bruno"], "🏆 *MENOR PREÇO JÁ VISTO PELO RADAR!* (antes: R$ 49,90, Loja Panini, 21/10/2026)")
    assert "📉 *Desconto:*" in txt and "\n🏆 *MENOR PREÇO" in txt
    assert txt.index("🏆") > txt.index("📉") and txt.index("🏆") < txt.index("🔗")


# ---------------- Ajustes da primeira varredura real
@pytest.mark.parametrize("titulo", [
    "The Flash by Geoff Johns Book One", "Green Lantern by Geoff Johns Omnibus Vol. 2",
    "By Peter David - Supergirl (1998-04-16) [Paperback]", "One Piece Vol. 97 (English Edition)",
])
def test_titulo_estrangeiro_nao_alerta(titulo):
    d = avaliar(of(titulo, preco=10), CAT, ref_capa=100)
    assert not d.alertar and "estrangeira" in d.motivo, (titulo, d.motivo)


def test_isbn():
    from stock import isbn_estrangeiro
    assert isbn_estrangeiro("1974722899") and isbn_estrangeiro("408883190X")
    assert isbn_estrangeiro("978-1-4012-5873-5")
    assert not isbn_estrangeiro("6525944201") and not isbn_estrangeiro("852505612X")
    assert not isbn_estrangeiro("978-65-259-4420-1") and not isbn_estrangeiro("9788525056122")


def test_pagina_em_ingles_rejeitada():
    html = """<html><head><script type="application/ld+json">{"@type":"Book","name":"One Piece 97",
    "isbn":"9781974722891","offers":{"price":"72.30","availability":"https://schema.org/InStock"}}</script>
    </head><body><button>Comprar</button></body></html>"""
    r = validar_html("https://l.com/p/1", "https://l.com/p/1", 200, html, True)
    assert not r.disponivel and "estrangeira" in r.motivo
    html2 = "<html><body><h1>Blueberry 4</h1><ul><li>Idioma: Inglês</li></ul><button>Comprar</button></body></html>"
    assert not validar_html("https://l.com/p/1", "https://l.com/p/1", 200, html2, True).disponivel
    html3 = "<html><body><h1>Blueberry 4</h1><ul><li>Idioma: Português</li></ul><button>Comprar</button></body></html>"
    assert validar_html("https://l.com/p/1", "https://l.com/p/1", 200, html3, True).disponivel


def test_preco_de_nao_concatena_numeros():
    from stock import preco_no_texto
    assert preco_no_texto("De R$ 3.548,02 5 parcelas de 709,60") == 3548.02
    assert preco_no_texto("R$ 89,90") == 89.9
    html = """<html><body><h1>Blueberry 4</h1><span class="old-price">R$ 35,48 · 025 vendidos</span>
    <script type="application/ld+json">{"@type":"Product","offers":{"price":"266.10","availability":"InStock"}}</script>
    <button>Comprar</button></body></html>"""
    r = validar_html("https://l.com/p/1", "https://l.com/p/1", 200, html, True)
    assert r.preco_lista == 35.48


def test_capa_absurda_e_descartada():
    d = avaliar(of("Blueberry: Edição Definitiva (Vol. 4 de 4)", preco=266.1, lista=3548025.0), CAT)
    assert d.alertar and d.regra == "A" and d.preco_capa is None      # raro: alerta, mas sem inventar desconto
    d2 = avaliar(of("Battle Royale Omnibus Vol. 4", preco=20, lista=500), CAT)
    assert not d2.alertar                                               # promo: não alerta com capa suspeita


def test_magento_e_vtex():
    mag = """<html><body><h1>A Saga do Flash 8</h1><div class="stock available"><span>Em estoque</span></div>
    <button id="product-addtocart-button" class="action primary tocart" disabled>Adicionar ao carrinho</button></body></html>"""
    assert validar_html("https://l.com/p/1", "https://l.com/p/1", 200, mag, False).disponivel
    mag_fora = mag.replace("stock available", "stock unavailable")
    assert not validar_html("https://l.com/p/1", "https://l.com/p/1", 200, mag_fora, False).disponivel
    vtex = "<html><body><h1>Mangá</h1><button disabled class='vtex-add-to-cart-button'>Adicionar ao carrinho</button></body></html>"
    assert validar_html("https://l.com/p/1", "https://l.com/p/1", 200, vtex, False, sinal_externo=True).disponivel
    assert not validar_html("https://l.com/p/1", "https://l.com/p/1", 200, vtex, False).disponivel


def test_palavra_captcha_solta_nao_bloqueia():
    html = "<html><head><script>var captchaConfig = {};</script></head><body><h1>X</h1><button>Comprar</button></body></html>"
    assert validar_html("https://l.com/p/1", "https://l.com/p/1", 200, html, True).disponivel


# ---------------- Coletores Magento e WooCommerce (sem rede)
class _FetcherFalso:
    def __init__(self, respostas):
        self.respostas, self.pedidos = respostas, []
    def get(self, url, json=False, headers=None):
        from stores.base import Resposta
        self.pedidos.append(url)
        for chave, valor in self.respostas.items():
            if chave in url:
                return valor if json else Resposta(200, url, valor)
        return (404, None) if json else Resposta(404, url, "")
    def get_render(self, url, seletor=None):
        return self.get(url)


def test_magento_graphql():
    from stores.plataformas import MagentoLoja
    api = {"data": {"products": {"items": [
        {"name": "Thorgal Vol. 3", "url_key": "thorgal-vol-3", "url_suffix": ".html", "stock_status": "IN_STOCK",
         "price_range": {"minimum_price": {"regular_price": {"value": 99.9}, "final_price": {"value": 69.9}}}},
        {"name": "Thorgal Vol. 2", "url_key": "thorgal-vol-2", "url_suffix": ".html", "stock_status": "OUT_OF_STOCK",
         "price_range": {"minimum_price": {"regular_price": {"value": 99.9}, "final_price": {"value": 99.9}}}},
    ]}}}
    pagina = "<html><body><h1>Thorgal Vol. 3</h1><div class='stock available'>Em estoque</div></body></html>"
    f = _FetcherFalso({"/graphql": (200, api), "thorgal-vol-3.html": pagina})
    loja = MagentoLoja(f, {"nome": "Pipoca & Nanquim", "base": "https://pipocaenanquim.com.br/", "oficial": True})
    achados = loja.buscar("thorgal")
    assert [o.url for o in achados] == ["https://pipocaenanquim.com.br/thorgal-vol-3.html"]
    o = loja.validar(achados[0])
    assert o.disponivel and o.preco == 69.9 and o.preco_lista == 99.9


def test_magento_sem_graphql_cai_para_html():
    from stores.plataformas import MagentoLoja
    busca = '<a href="https://www.comix.com.br/gantz-e-01.html">Gantz E 01</a><a href="/conta">Conta</a>'
    f = _FetcherFalso({"/catalogsearch/result": busca})
    loja = MagentoLoja(f, {"nome": "Comix", "base": "https://www.comix.com.br"})
    assert [o.url for o in loja.buscar("gantz")] == ["https://www.comix.com.br/gantz-e-01.html"]
    assert loja.graphql_ok is False


def test_woocommerce():
    from stores.plataformas import WooLoja
    api = [{"name": "O Fantasma: a fase DC Comics completa", "permalink": "https://lojamonstra.com.br/produto/o-fantasma/",
            "is_in_stock": True, "is_purchasable": True,
            "prices": {"price": "15990", "regular_price": "19990", "currency_minor_unit": 2}},
           {"name": "O Fantasma vol 2", "permalink": "https://lojamonstra.com.br/produto/f2/",
            "is_in_stock": False, "is_purchasable": True, "prices": {"price": "100", "currency_minor_unit": 2}}]
    f = _FetcherFalso({"/wp-json/wc/store": (200, api)})
    loja = WooLoja(f, {"nome": "Monstra", "base": "https://lojamonstra.com.br"})
    achados = loja.buscar("o fantasma")
    assert len(achados) == 1 and achados[0].preco == 159.9 and achados[0].preco_lista == 199.9


# ---------------- Ajustes da varredura seca (22/10 14h)
def test_esgotado_em_produtos_relacionados_nao_conta():
    html = """<html><body><div class="summary entry-summary"><h1>O Fantasma</h1>
    <p class="stock in-stock">3 em estoque</p><button class="single_add_to_cart_button">Comprar</button></div>
    <section class="related products"><ul><li class="product"><p class="stock out-of-stock">Esgotado</p></li></ul></section>
    </body></html>"""
    assert validar_html("https://l.com/produto/x/", "https://l.com/produto/x/", 200, html, False, sinal_externo=True).disponivel


@pytest.mark.parametrize("titulo,alerta", [
    ("Tartarugas Ninja Vol. 3", True),
    ("Power Rangers e Tartarugas Ninja Vol. 2", False),
    ("Batman e Tartarugas Ninja Vol. 2", False),
    ("Tartarugas Ninja: Coleção Clássica Vol. 2", False),
])
def test_tmnt_sem_crossovers(titulo, alerta):
    assert avaliar(of(titulo, preco=70), CAT, ref_capa=120).alertar is alerta, titulo


def test_magento_url_pela_api():
    from stores.plataformas import MagentoLoja
    api = {"data": {"products": {"items": [
        {"name": "A Saga do Flash Vol. 8", "url_key": "saga-flash-8", "url_suffix": "",
         "url_rewrites": [{"url": "quadrinhos/dc/saga-flash-8"}, {"url": "a-saga-do-flash-8.html"}],
         "stock_status": "IN_STOCK",
         "price_range": {"minimum_price": {"regular_price": {"value": 54.9}, "final_price": {"value": 54.9}}}}]}}}
    f = _FetcherFalso({"/graphql": (200, api)})
    loja = MagentoLoja(f, {"nome": "Loja Panini", "base": "https://panini.com.br"})
    assert loja.buscar("saga do flash")[0].url == "https://panini.com.br/a-saga-do-flash-8.html"



# ---------------- Spawn em promoção, Dandadan só pacote, melhor loja
@pytest.mark.parametrize("titulo", ["Spawn Origens Vol. 10", "Spawn Origens Vol. 15", "Spawn Vol. 9", "Spawn Vol. 30"])
def test_spawn_agora_e_promocao(titulo):
    assert not avaliar(of(titulo, preco=45), CAT, ref_capa=54.9).alertar          # 18% off: não
    d = avaliar(of(titulo, preco=39.9), CAT, ref_capa=54.9)                          # 27% off: sim
    assert d.alertar and d.regra == "B"


def test_dandadan_so_pacote():
    assert not avaliar(of("Dandadan Vol. 5", preco=10), CAT, ref_capa=45).alertar
    d = avaliar(of("Kit Dandadan Vols. 1 ao 10 (usado)", preco=290), CAT, ref_capa=10 * 42.0)
    assert d.alertar and d.pacote == 10 and d.entrada_id == "dandadan"
    assert not avaliar(of("Kit Dandadan Vols. 1 ao 10", preco=380), CAT, ref_capa=420).alertar   # só 10% off
    assert not avaliar(of("Kit Dandadan", preco=10), CAT, ref_capa=420).alertar                 # sem nº de volumes


def test_preco_tipico_volume(tmp_path):
    from db import Banco
    b = Banco(tmp_path / "r.db")
    for v, p in [(1, 40.0), (2, 42.0), (3, 45.0), (4, 30.0)]:
        b.registrar_preco(f"dandadan:{v}", f"https://l/{v}", p, "Loja")
    b.registrar_preco("dandadan pacote:unico", "https://l/kit", 300.0, "Loja")
    assert b.preco_tipico_volume("dandadan") == 41.0
    b.salvar_capa("capa:dandadan:5", 44.9, "Loja Panini")
    assert b.preco_tipico_volume("dandadan") == 44.9


def test_mensagem_pacote_e_outras_lojas():
    o = of("Kit Dandadan Vols. 1 ao 10 (usado)", preco=290, cond="usado")
    d = avaliar(o, CAT, ref_capa=420)
    d.preco_volume = 42.0
    txt = montar(o, d, ["Bruno"], None, [("Comic Boom", 310.0), ("Estante Virtual", 320.0)])
    assert "🏷️ *Preço de Capa:* R$ 420,00 (estimado: 10 vols x R$ 42,00)" in txt
    assert "⚡ *Status:* Seminovo" in txt
    assert "🛍️ *Também em:* Comic Boom R$ 310,00 · Estante Virtual R$ 320,00" in txt



# ---------------- Thiago e Ivan
def _perfil(nome):
    return Catalogo(RAIZ / "config" / "catalogo.yaml", RAIZ / "config" / "pessoas" / f"{nome}.yaml")


@pytest.mark.parametrize("titulo,alerta", [
    ("Lanterna Verde: O Dia Mais Claro Vol. 02 (Grandes Eventos DC)", True),
    ("Lanterna Verde: O Dia Mais Claro Vol. 01", False),
    ("Superman: Mundos em Guerra Vol. 2", True),
    ("Parcerias: Perseguição Até o Fim dos Tempos! (O Melhor da DC)", True),
    ("Lanterna Verde: O Outro Lanterna Verde da Terra", True),
    ("Batman: Padrões Sombrios Vol. 2", True),
    ("Batman por Neal Adams - Edição Absoluta Vol. 2", True),
    ("Sociedade da Justiça da América por Geoff Johns Vol. 3 (Omnibus)", True),
    ("Superman: As Quatro Estações (DC de Bolso)", True),
    ("Superman: O Mundo", True), ("Supergirl: O Mundo", True), ("Coringa: O Mundo", True),
    ("Superman: O Homem que salvou o mundo", False),
    ("Tartarugas Ninja: Coleção Clássica Vol. 5", True), ("Tartarugas Ninja: Coleção Clássica Vol. 4", False),
    ("Tartarugas Ninja: O Último Ronin", True), ("Tartarugas Ninja: O Último Ronin - Os Anos Perdidos", True),
    ("Tartarugas Ninja: Preto, Branco e Verde", True),
    ("O Eternauta: Segunda Parte", True), ("O Eternauta", False),
    ("Dylan Dog Omnibus Vol. 4", True), ("Dylan Dog Omnibus Vol. 3", False),
    ("O Fantasma: A Fase Marvel Completa", True), ("O Fantasma: A Fase DC Comics Completa", True),
    ("Batman: Ano 100", True),
])
def test_lista_thiago(titulo, alerta):
    d = avaliar(of(titulo, preco=70), _perfil("thiago"), ref_capa=100)
    assert d.alertar is alerta, (titulo, d.motivo)


@pytest.mark.parametrize("titulo,alerta", [
    ("Console PlayStation 5 Sony Edição Digital Marvel's Wolverine Pacote de Edição Limitada, 825 GB, Amarelo", True),
    ("PlayStation 5 Edição Digital - Pacote Marvel's Wolverine Limited Edition Bundle", True),
    ("Console PS5 Slim Edição Digital Marvel's Wolverine + Controle DualSense Extra", False),
    ("Marvel's Wolverine - Edição Standard - PlayStation 5", False),
    ("Controle DualSense Marvel's Wolverine Edição Limitada", False),
    ("Tampas do Console PS5 Wolverine", False),
    ("Wolverine Omnibus Vol. 1", False),
    ("Console PS5 Slim Edição Limitada Marvel's Wolverine + Controle DualSense Extra", True),
    ("Kit Console PlayStation 5 Pacote Wolverine Edição Limitada com 2 Controles", True),
    ("Kit 2 Controles DualSense Wolverine para PS5 Slim", False),
    ("DualSense Wolverine Battle Yellow para console PS5", False),
])
def test_ivan_ps5_wolverine(titulo, alerta):
    d = avaliar(of(titulo, preco=4556.07, lista=5099.0), _perfil("ivan"))
    assert d.alertar is alerta, (titulo, d.motivo)
    if alerta:
        assert d.so_menor_preco and d.formato == "Console"


def test_console_nao_cai_no_radar_de_boxes():
    assert not avaliar(of("Kit Console PS5 + Jogo Marvel's Wolverine", preco=3000), CAT, ref_capa=5000).alertar


def test_buscas_por_categoria():
    from rules import Grupo
    g = Grupo(RAIZ / "config")
    cats = {b: c for _, b, c in g.todas_buscas()}
    assert cats["console ps5 wolverine"] == "games" and cats["a saga do flash"] == "livros"
    assert any("kabum.com.br/produto/1065184" in u for u, _ in g.urls_fixas())


def test_ivan_so_menor_preco_no_envio(tmp_path, monkeypatch):
    import main, stores
    from db import Banco

    class F:
        def fechar(self): pass

    class Kabum:
        nome, oficial, f, dominio = "KaBuM!", False, F(), "kabum.com.br"
        categorias, categoria_atual = ["games"], "games"
        preco = 4556.07
        def buscar(self, termo): return []
        def validar(self, o):
            o.titulo = "Console PlayStation 5 Edição Digital Marvel's Wolverine Pacote de Edição Limitada"
            o.preco, o.disponivel, o.motivo_estoque = Kabum.preco, True, "ok"
            return o

    (tmp_path / "data").mkdir()
    monkeypatch.setattr(main, "RAIZ", tmp_path)
    import shutil; shutil.copytree(RAIZ / "config", tmp_path / "config")
    monkeypatch.setattr(stores, "montar_lojas", lambda p: [Kabum()])
    enviados = []

    class Cli:
        def enviar_alerta(self, d, c, texto): enviados.append(texto); return True
    import whatsapp
    monkeypatch.setattr(whatsapp, "cliente_do_ambiente", lambda: Cli())
    monkeypatch.setattr(main.time, "sleep", lambda s: None)

    main.varredura(True)                       # 1ª vez: marca a referência
    assert len(enviados) == 1 and "👥 *Para:* Ivan" in enviados[0]
    b = Banco(tmp_path / "data" / "radar.db")
    b.con.execute("UPDATE precos SET dia='2026-01-01'"); b.con.execute("DELETE FROM alertas"); b.con.commit()
    Kabum.preco = 4600.0
    main.varredura(True)                       # mais caro que o menor: não alerta
    assert len(enviados) == 1
    Kabum.preco = 4299.0
    main.varredura(True)                       # novo menor preço: alerta
    assert len(enviados) == 2 and "MENOR PREÇO" in enviados[1]



def test_relatorio_disponibilidade(tmp_path, monkeypatch, capsys):
    import main, shutil, stores

    class F:
        def fechar(self): pass

    def loja(nome, itens):
        class L:
            oficial, f, dominio = False, F(), ""
            categorias, categoria_atual = ["livros"], "livros"
            def buscar(self, termo):
                return [Oferta(nome, t, f"https://{nome}/{i}", 0) for i, t in enumerate(itens) if
                        any(w in t.lower() for w in termo.lower().split()[:1])]
            def validar(self, o):
                o.preco, o.disponivel, o.motivo_estoque = 50.0 + len(nome), True, "ok"
                return o
        l = L(); l.nome = nome; return l

    lojas = [loja(n, ["Superman: O Mundo", "Dylan Dog Omnibus Vol. 4"]) for n in ("A", "BB", "CCC")]
    lojas.append(loja("DDDD", ["Tartarugas Ninja: Coleção Clássica Vol. 6"]))
    shutil.copytree(RAIZ / "config", tmp_path / "config"); (tmp_path / "data").mkdir()
    monkeypatch.setattr(main, "RAIZ", tmp_path)
    monkeypatch.setattr(stores, "montar_lojas", lambda p: lojas)
    main.relatorio(enviar=False)
    out = capsys.readouterr().out
    thiago = out[out.index("Rastreamento de Thiago"):]
    assert "🟢" in thiago and "• Superman: O Mundo: 3 lojas, a partir de R$ 51,00 (A)" in thiago
    assert "• Dylan Dog Omnibus vol. 4: 3 lojas" in thiago
    assert "🟡" in thiago and "Coleção Clássica vol. 6: 1 loja," in thiago
    assert "🔴" in thiago and "• Dylan Dog Omnibus vol. 5" in thiago and "• Coringa: O Mundo" in thiago



# ---------------- Resumão do dia
def test_resumo_do_dia(tmp_path):
    import sqlite3
    from datetime import datetime
    from db import Banco
    from mensagem import resumo_do_dia
    # banco antigo, sem as colunas novas: precisa migrar sem perder nada
    con = sqlite3.connect(tmp_path / "r.db")
    con.execute("CREATE TABLE alertas (url TEXT PRIMARY KEY, preco REAL, enviado_em TEXT, regra TEXT)")
    con.execute("INSERT INTO alertas VALUES ('https://velho', 10, '2026-10-20T10:00:00', 'B')"); con.commit(); con.close()
    b = Banco(tmp_path / "r.db")
    b.registrar_alerta("https://a/1", 39.9, "B", "Invencível Vol. 5", "Comix", 33.0, "Bruno")
    b.registrar_alerta("https://a/2", 4299.0, "A", "PS5 Wolverine", "KaBuM!", 15.7, "Ivan", recorde=True)
    b.registrar_alerta("https://a/3", 29.9, "A", "Spawn Origens 10", "Estante Virtual", None, "Bruno e Thiago")
    b.registrar_alerta("https://a/1", 35.0, "B", "Invencível Vol. 5", "Comix", 41.5, "Bruno")
    hoje = datetime.now().strftime("%Y-%m-%d")
    al = b.alertas_do_dia(hoje)
    assert len(al) == 3
    txt = resumo_do_dia("26/10", al, 812, 9)
    assert txt.startswith("📰 *Resumão do dia 26/10*")
    assert "3 ofertas enviadas hoje · 812 anúncios conferidos em 9 lojas" in txt
    assert txt.index("Invencível") < txt.index("PS5")                      # maior desconto primeiro
    assert "*R$ 35,00* *42% OFF*" in txt                                    # último preço do dia
    assert "⭐ *Raros e difíceis de achar*\n• Spawn Origens 10" in txt
    assert "👥 *Por pessoa:* Bruno 2 · Ivan 1 · Thiago 1" in txt
    assert "Dia tranquilo" in resumo_do_dia("27/10", [], 500, 8)


def test_telegram_corta_mensagem_grande():
    from whatsapp import TelegramClient
    p = TelegramClient("t", "1").payload("linha\n" * 2000)
    assert len(p["text"]) < 4096 and p["text"].endswith("(...)")


# ---------------- KaBuM! e PS5 edição limitada
def test_kabum_sinais_da_loja():
    import yaml
    from stores.plataformas import HtmlLoja
    # sinais próprios de uma loja HTML (era a KaBuM!, que agora usa a API)
    cfg = {"nome": "KaBuM!", "base": "https://www.kabum.com.br", "estoque_ok_regex": r'restam\s+\d+\s+unid',
           "preco_regex": r'r\$\s*(\d{1,3}(?:\.\d{3})*,\d{2})[^$]{0,40}?vista no pix'}
    pagina = """<html><head><script type="application/ld+json">{"@type":"Product","name":"Console PlayStation 5 Wolverine","offers":{"price":"4742.07","availability":"https://schema.org/InStock"}}</script></head><body>
    <h1>Console PlayStation 5 Sony Edição Digital Marvel's Wolverine Pacote de Edição Limitada</h1>
    <div id="blocoValores"><b>R$ 5.099,00</b> <h4>R$ 4.556,07</h4><span>À vista no PIX com 7% de desconto</span>
    <span>Restam 7 Unid.</span><button class="sc-buy disabled" disabled>COMPRAR</button></div></body></html>"""
    f = _FetcherFalso({"/produto/1065184": pagina})
    loja = HtmlLoja(f, {**cfg, "playwright": False}, "html")
    o = loja.validar(Oferta("KaBuM!", "x", "https://www.kabum.com.br/produto/1065184/ps5", 0))
    assert o.disponivel and o.preco == 4556.07 and "restam 7 unid" in o.motivo_estoque
    esgotado = pagina.replace("InStock", "OutOfStock")
    f2 = _FetcherFalso({"/produto/1065184": esgotado})
    o2 = HtmlLoja(f2, {**cfg, "playwright": False}, "html").validar(
        Oferta("KaBuM!", "x", "https://www.kabum.com.br/produto/1065184/ps5", 0))
    assert not o2.disponivel


@pytest.mark.parametrize("titulo,alerta", [
    ("PlayStation®5 Edição Digital – Marvel's Wolverine Pacote de Edição Limitada Amarelo", True),
    ("PlayStation 5 Digital Edition – Marvel’s Wolverine Battle Yellow Limited Edition Bundle", True),
    ("Console Sony PlayStation 5, Com Leitor de Discos + Jogo Marvel's Wolverine, PS5", False),
    ("Marvel's Wolverine - Edição Limitada - PlayStation 5", True),
])
def test_ps5_so_edicao_limitada(titulo, alerta):
    d = avaliar(of(titulo, preco=4556.07, lista=5099.0), _perfil("ivan"))
    assert d.alertar is alerta, (titulo, d.motivo)



def test_woo_confia_na_api_de_estoque():
    from stores.plataformas import WooLoja
    pagina = """<html><body><div class="product-summary"><h1>Frieren Vol. 13</h1>
    <span class="price">R$ 39,90</span><div class="widget stock-filter">Esgotado</div>
    <button class="single_add_to_cart_button">Comprar</button></div></body></html>"""
    f = _FetcherFalso({"/produto/frieren": pagina})
    loja = WooLoja(f, {"nome": "Monstra", "base": "https://lojamonstra.com.br"})
    o = loja.validar(Oferta("Monstra", "Frieren 13", "https://lojamonstra.com.br/produto/frieren-13/", 39.9,
                            extra={"api": "woo"}))
    assert o.disponivel and "Woo API" in o.motivo_estoque
    fora = pagina.replace("<body>", """<body><script type="application/ld+json">{"@type":"Product",
    "offers":{"price":"39.90","availability":"https://schema.org/OutOfStock"}}</script>""")
    f2 = _FetcherFalso({"/produto/frieren": fora})
    o2 = WooLoja(f2, {"nome": "Monstra", "base": "https://lojamonstra.com.br"}).validar(
        Oferta("Monstra", "Frieren 13", "https://lojamonstra.com.br/produto/frieren-13/", 39.9, extra={"api": "woo"}))
    assert not o2.disponivel


# ---------------- Lojas novas
def test_lojas_novas_configuradas(tmp_path, monkeypatch):
    import stores
    from stores.plataformas import HtmlLoja, ShopifyLoja, WooLoja
    monkeypatch.setattr(stores, "detectar_plataforma", lambda f, base: "vtex")   # sem rede no teste
    monkeypatch.setattr(stores, "CACHE", tmp_path / "plataformas.json")
    lojas = {l.nome: l for l in stores.montar_lojas(RAIZ / "config" / "lojas.yaml")}
    assert isinstance(lojas["Capitão Onigiri"], WooLoja) and isinstance(lojas["Amora Bookstore"], WooLoja)
    assert isinstance(lojas["Taverna do Rei"], ShopifyLoja)
    assert "Mundos Infinitos" not in lojas                     # desativada (busca lenta demais)
    assert type(lojas["Leitura"]) is HtmlLoja and lojas["Leitura"].busca == "/catalogo?q={q}"
    from stores.kabum import KabumLoja
    assert isinstance(lojas["KaBuM!"], KabumLoja)
    assert lojas["Capitão Onigiri"].busca == "/?s={q}&post_type=product"
    from stores.plataformas import VtexLoja
    assert isinstance(lojas["Livrarias Curitiba"], VtexLoja) and "Leitura" in lojas
    assert lojas["Mirane Comics"].plataforma == "nuvemshop" and lojas["Mirane Comics"].busca == "/search/?q={q}"


def test_mundos_infinitos_busca():
    from stores.plataformas import HtmlLoja
    busca = """<div class="vitrine">
      <a href="/geek/produto/Comics-Marvel-O-Incrivel-Hulk-Por-Peter-David-Vol-3-Omnibus-162040.aspx?oid=1366508">
        Comics Marvel O Incrível Hulk Por Peter David Vol 3 Omnibus</a>
      <a href="/geek/vitrines/omnibus.aspx">Omnibus</a>
      <a href="/geek/solucoes/busca.aspx?t=omnibus">Busca</a></div>"""
    f = _FetcherFalso({"/geek/solucoes/busca.aspx": busca})
    cfg = {"nome": "Mundos Infinitos", "base": "https://mundosinfinitos.com.br",
           "busca": "/geek/solucoes/busca.aspx?t={q}", "padrao_produto": "/geek/produto/"}
    achados = HtmlLoja(f, cfg, "html").buscar("hulk omnibus")
    assert [o.url for o in achados] == [
        "https://mundosinfinitos.com.br/geek/produto/Comics-Marvel-O-Incrivel-Hulk-Por-Peter-David-Vol-3-Omnibus-162040.aspx"]
    assert "busca.aspx?t=hulk+omnibus" in f.pedidos[0]



def test_nuvemshop_estoque_pelas_variantes():
    from stores.plataformas import HtmlLoja
    pagina = """<html><body><h1>Absolute Batman 07</h1>
    <div class="js-product-container" data-variants="[{&quot;available&quot;:true,&quot;price_number&quot;:39.9,
    &quot;compare_at_price_number&quot;:49.9}]"><span class="js-price-display">R$ 39,90</span>
    <input type="submit" class="js-addtocart" value="Comprar"></div></body></html>"""
    f = _FetcherFalso({"/produtos/absolute-batman-07": pagina})
    loja = HtmlLoja(f, {"nome": "Mirane Comics", "base": "https://miranecomics.com.br"}, "nuvemshop")
    o = loja.validar(Oferta("Mirane Comics", "x", "https://miranecomics.com.br/produtos/absolute-batman-07-63iu6/", 0))
    assert o.disponivel and o.preco == 39.9 and o.preco_lista == 49.9
    esgotado = pagina.replace("&quot;available&quot;:true", "&quot;available&quot;:false")
    f2 = _FetcherFalso({"/produtos/absolute-batman-07": esgotado})
    o2 = HtmlLoja(f2, {"nome": "Mirane Comics", "base": "https://miranecomics.com.br"}, "nuvemshop").validar(
        Oferta("Mirane Comics", "x", "https://miranecomics.com.br/produtos/absolute-batman-07-63iu6/", 0))
    assert not o2.disponivel



# ---------------- Difícil de achar x fácil de achar
def _varredura_fake(tmp_path, monkeypatch, ofertas_por_loja):
    """ofertas_por_loja: {loja: [(titulo, preco, preco_lista)]}. Devolve os textos enviados."""
    import main, shutil, stores, whatsapp

    class F:
        def fechar(self): pass

    def loja(nome, itens):
        class L:
            oficial, f, dominio = False, F(), ""
            categorias, categoria_atual = ["livros"], "livros"
            def buscar(self, termo):
                return [Oferta(nome, t, f"https://{nome}/{i}", p, preco_lista=pl, extra={})
                        for i, (t, p, pl) in enumerate(itens)]
            def validar(self, o):
                o.disponivel, o.motivo_estoque = True, "ok"
                return o
        l = L(); l.nome = nome; return l

    if not (tmp_path / "config").exists():
        shutil.copytree(RAIZ / "config", tmp_path / "config"); (tmp_path / "data").mkdir()
    monkeypatch.setattr(main, "RAIZ", tmp_path)
    monkeypatch.setattr(stores, "montar_lojas", lambda p: [loja(n, i) for n, i in ofertas_por_loja.items()])
    enviados = []

    class Cli:
        def enviar_alerta(self, d, c, texto): enviados.append(texto); return True
    monkeypatch.setattr(whatsapp, "cliente_do_ambiente", lambda: Cli())
    monkeypatch.setattr(main.time, "sleep", lambda s: None)
    main.varredura(True)
    return enviados


def test_dificil_de_achar_avisa_sem_desconto(tmp_path, monkeypatch):
    env = _varredura_fake(tmp_path, monkeypatch, {
        "Mirane": [("Coringa: O Mundo", 69.9, 69.9)],
        "Amora": [("Coringa: O Mundo", 72.0, 72.0)],
    })
    assert len(env) == 1
    assert "Difícil de achar (só em 2 lojas)" in env[0] and "*R$ 69,90*" in env[0]
    assert "🛍️ *Também em:* Amora R$ 72,00" in env[0]


def test_facil_de_achar_sem_desconto_nao_avisa(tmp_path, monkeypatch):
    env = _varredura_fake(tmp_path, monkeypatch, {
        "A": [("Coringa: O Mundo", 69.9, 69.9)], "B": [("Coringa: O Mundo", 70.0, 70.0)],
        "C": [("Coringa: O Mundo", 71.0, 71.0)],
    })
    assert env == []


def test_facil_de_achar_com_desconto_manda_a_melhor(tmp_path, monkeypatch):
    env = _varredura_fake(tmp_path, monkeypatch, {
        "A": [("Coringa: O Mundo", 69.9, 69.9)], "B": [("Coringa: O Mundo", 49.9, 69.9)],
        "C": [("Coringa: O Mundo", 52.0, 69.9)],
    })
    assert len(env) == 1 and "🏪 *Loja:* B" in env[0] and "29% OFF" in env[0]
    assert "Difícil" not in env[0]



def test_boletim_das_lojas(tmp_path):
    from datetime import datetime
    from db import Banco
    from mensagem import boletim_lojas
    b = Banco(tmp_path / "r.db")
    hoje = datetime.now().strftime("%Y-%m-%d")
    b.diagnostico("Comix", 60, 300, 0, 50, 40, 240)
    b.diagnostico("Amora", 60, 0, 0, 0, 0, 90)
    b.diagnostico("Amazon", 60, 200, 0, 100, 30, 480)
    b.diagnostico("Rika", 60, 30, 0, 10, 1, 60)
    for i in range(60):
        b.historico("Amazon", "x", f"u{i}", 10, 0, "Amazon bloqueou ou falhou (HTTP 200)")
    b.registrar_alerta("https://c/1", 30, "B", "Frieren 5", "Comix", 30.0, "Bruno")
    txt = boletim_lojas("27/10", b.saude_lojas(hoje), ["Comix", "Amora", "Amazon", "Rika", "Leitura"])
    assert "✅ *Rendendo ofertas*\n• Comix: 1 oferta enviada, 40 itens com estoque, 4 min por varredura" in txt
    assert "🟢 *Funcionando, sem oferta hoje*\n• Rika: 1 item com estoque" in txt
    assert "⚠️" in txt and "• Amazon: 60 de 100 páginas bloqueadas" in txt
    assert "• Amora: 60 buscas sem nenhum resultado" in txt and "• Leitura: não rodou hoje" in txt



# ---------------- Travas contra loja lenta ou fora do ar
def test_loja_que_nao_responde_e_abandonada(tmp_path, monkeypatch):
    import httpx, main
    from db import Banco
    from rules import Grupo
    from stores.base import Fetcher
    from stores.plataformas import HtmlLoja
    chamadas = []

    def trava(self, url, headers=None):
        chamadas.append(url)
        raise httpx.ReadTimeout("sem resposta")
    monkeypatch.setattr(httpx.Client, "get", trava)
    f = Fetcher(delay=0)
    loja = HtmlLoja(f, {"nome": "Lenta", "base": "https://lenta.com.br"}, "html")
    achados = main.varrer_loja(loja, Grupo(RAIZ / "config"), Banco(tmp_path / "r.db"))
    assert achados == [] and len(chamadas) == main.MAX_FALHAS_SEGUIDAS     # não insiste nas 60 buscas
    linha = Banco(tmp_path / "r.db").con.execute("SELECT parada FROM diagnostico").fetchone()
    assert linha[0] == "loja não responde"


def test_loja_respeita_tempo_maximo(tmp_path, monkeypatch):
    import main
    from db import Banco
    from rules import Grupo

    class F:
        falhas_seguidas = 0
        def fechar(self): pass

    class Lerda:
        nome, oficial, f, dominio, cfg = "Lerda", False, F(), "", {"tempo_max_min": 0}
        categorias, categoria_atual = ["livros"], "livros"
        def buscar(self, termo): raise AssertionError("não devia buscar depois do prazo")
    main.varrer_loja(Lerda(), Grupo(RAIZ / "config"), Banco(tmp_path / "r.db"))
    assert Banco(tmp_path / "r.db").con.execute("SELECT parada FROM diagnostico").fetchone()[0] == "tempo esgotado"



def test_vtex_confia_na_api():
    from stores.plataformas import VtexLoja
    pagina = """<html><body><h1>Box Ajin 1-17</h1><div class="product-info"><span>Esgotado</span></div>
    <button class="buy-button">Comprar</button></body></html>"""
    f = _FetcherFalso({"/box-ajin": pagina})
    loja = VtexLoja(f, {"nome": "Livrarias Curitiba", "base": "https://www.livrariascuritiba.com.br"})
    o = loja.validar(Oferta("Livrarias Curitiba", "Box Ajin 1-17", "https://www.livrariascuritiba.com.br/box-ajin/p",
                            442.4, extra={"qtd_api": 10}))
    assert o.disponivel and "VTEX API" in o.motivo_estoque


def test_titulo_do_produto_e_nao_do_carrinho():
    html = """<html><body><h1 class="widget-title">No carrinho</h1>
    <h1 class="product_title entry-title">Zetman Completo Lote</h1><button>Comprar</button></body></html>"""
    assert validar_html("https://l.com/produto/x/", "https://l.com/produto/x/", 200, html, True).titulo == "Zetman Completo Lote"



def test_escassez_considera_ultimos_dias(tmp_path, monkeypatch):
    from db import Banco
    (tmp_path / "data").mkdir()
    import shutil; shutil.copytree(RAIZ / "config", tmp_path / "config")
    b = Banco(tmp_path / "data" / "radar.db")
    # o item apareceu em outras 2 lojas ontem: não é difícil de achar
    from datetime import datetime, timedelta
    ontem = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
    for loja in ("Comix", "Estante"):
        b.con.execute("INSERT INTO precos VALUES ('coringa o mundo:unico', ?, ?, 70, ?)", (f"https://{loja}/x", ontem, loja))
    b.con.commit()
    env = _varredura_fake(tmp_path, monkeypatch, {"Mirane": [("Coringa: O Mundo", 69.9, 69.9)]})
    assert env == []



# ---------------- Mercado Livre (API de catálogo)
def test_mercado_livre_catalogo(monkeypatch):
    from stores.mercadolivre import MercadoLivreLoja, link_anuncio
    monkeypatch.setenv("ML_CLIENT_ID", "123"); monkeypatch.setenv("ML_CLIENT_SECRET", "abc")

    class Resp:
        status_code = 200
        text = ""
        def json(self): return {"access_token": "TOK", "expires_in": 21600}

    class Http:
        def __init__(self): self.posts = []
        def post(self, url, data=None, headers=None):
            self.posts.append(data); return Resp()

    respostas = {
        "/products/search": (200, {"results": [
            {"id": "MLB111", "name": "Frieren E A Jornada Para O Além Vol. 6"},
            {"id": "MLB222", "name": "Caneca Frieren"}]}),
        "/products/MLB111/items": (200, {"results": [
            {"item_id": "MLB900", "price": 41.9, "original_price": 52.9, "condition": "new", "currency_id": "BRL"},
            {"item_id": "MLB901", "price": 37.5, "condition": "new", "currency_id": "BRL"}]}),
    }
    f = _FetcherFalso(respostas)
    f.http = Http()
    loja = MercadoLivreLoja(f, {"nome": "Mercado Livre"})
    achados = loja.buscar("frieren vol 6")
    assert len(achados) == 1
    o = loja.validar(achados[0])
    assert o.disponivel and o.preco == 37.5 and o.url == "https://produto.mercadolivre.com.br/MLB-901"
    assert f.http.posts[0]["grant_type"] == "client_credentials"
    assert link_anuncio("MLB12345") == "https://produto.mercadolivre.com.br/MLB-12345"


def test_mercado_livre_sem_credenciais_e_pulado(monkeypatch):
    from stores.mercadolivre import MercadoLivreLoja
    monkeypatch.delenv("ML_CLIENT_ID", raising=False); monkeypatch.delenv("ML_CLIENT_SECRET", raising=False)
    f = _FetcherFalso({})
    loja = MercadoLivreLoja(f, {"nome": "Mercado Livre"})
    assert loja.buscar("frieren") == [] and loja.buscar("dandadan") == [] and f.pedidos == []


# ---------------- WooCommerce: busca curta e loja que ignora a busca
def _woo_prod(i, nome, preco=14990, lista=14990):
    return {"id": i, "name": nome, "permalink": f"https://loja.com.br/produto/{i}/",
            "is_in_stock": True, "is_purchasable": True,
            "prices": {"price": str(preco), "regular_price": str(lista), "currency_minor_unit": 2}}


def test_woo_tenta_busca_mais_curta():
    from stores.plataformas import WooLoja

    class F(_FetcherFalso):
        def get(self, url, json=False, headers=None):
            self.pedidos.append(url)
            if "search=nossos+melhores+amigos&" in url:
                return 200, [_woo_prod(1, "Nossos Melhores Amigos – A Verdadeira História de Friends", 9095, 12990)]
            return 200, []
    f = F({})
    loja = WooLoja(f, {"nome": "Comix Zone", "base": "https://comixzone.com.br"})
    achados = loja.buscar("nossos melhores amigos friends")
    assert len(achados) == 1 and achados[0].preco == 90.95 and achados[0].preco_lista == 129.9
    assert len(f.pedidos) == 2


def test_woo_loja_que_ignora_a_busca_vai_para_html():
    from stores.plataformas import WooLoja
    vitrine = [_woo_prod(i, f"Mirai Nikki {i}") for i in range(1, 6)]
    f = _FetcherFalso({"wc/store": (200, vitrine)})
    loja = WooLoja(f, {"nome": "Amora Bookstore", "base": "https://amorabookstore.com.br"})
    for termo in ["frieren", "sakamoto days", "spawn origens"]:
        loja.buscar(termo)
    assert loja.api_ok is False and "ignora" in loja.aviso


# ---------------- KaBuM! pela API do catálogo
def test_kabum_api():
    from stores.kabum import KabumLoja
    item = {"type": "product", "id": 1065184, "attributes": {
        "title": "Console PlayStation 5 Edição Digital Marvel's Wolverine Edição Limitada",
        "price": 5099.0, "old_price": 0.0, "price_with_discount": 4742.07, "offer": None,
        "available": True, "stock": 3, "is_openbox": False, "is_marketplace": False}}
    jogo = {"type": "product", "id": 1, "attributes": {
        "title": "Jogo Marvel's Wolverine, PS5", "price": 399.0, "old_price": 0.0, "price_with_discount": 371.07,
        "offer": {"quantity_available": 493, "price": 339.0, "price_with_discount": 315.27},
        "available": True, "stock": 10}}
    f = _FetcherFalso({"products?query=": (200, {"data": [item, jogo]}),
                       "products/1065184": (200, {"data": dict(item, attributes=dict(item["attributes"], available=False, stock=0))})})
    loja = KabumLoja(f, {"nome": "KaBuM!"})
    achados = loja.buscar("wolverine")
    ps5 = loja.validar(achados[0])
    assert ps5.disponivel and ps5.preco == 4742.07 and ps5.preco_lista is None
    assert ps5.url == "https://www.kabum.com.br/produto/1065184"
    j = loja.validar(achados[1])
    assert j.preco == 315.27 and j.preco_lista == 371.07
    # link fixo: confere pelo número do produto
    from models import Oferta
    fixa = Oferta("KaBuM!", "PS5", "https://www.kabum.com.br/produto/1065184/console-ps5", 0, extra={"fixa": True})
    r = loja.validar(fixa)
    assert r.disponivel is False and r.extra.get("fixa")


# ---------------- coleção na planilha
def test_colecao_expande_e_calcula_o_que_falta():
    from colecao import expandir, compactar, faltam
    assert expandir("1-3, 5, 8-9") == [1, 2, 3, 5, 8, 9]
    assert compactar([1, 2, 3, 5, 8, 9]) == "1-3,5,8-9"
    assert faltam([1, 2, 3, 5, 8, 9], 10) == ([4, 6, 7, 10], None)
    assert faltam(expandir("1-9, 11"), None) == ([10], 12)
    assert faltam(expandir("1-10"), 10) == ([], None)


def test_colecao_vira_entradas_do_radar():
    from colecao import ler_csv, entradas
    from rules import Entrada, SpecVolume
    csv_txt = ('"Série","Editora","Volumes que tenho (ex.: 1-5, 7, 9)","Total de volumes (vazio = em andamento)",'
               '"Alerta","Faltam (calculado)","Palavras que o título precisa ter (opcional)","Palavras para ignorar (opcional)"\n'
               '"Spawn Origens","Panini","1-9, 11","","promoção","10, 12 em diante","spawn, origens",""\n'
               '"Berserk","Panini","1-40","41","raro","41","",""\n'
               '"Akira","JBC","1-6","6","promoção","completa","",""\n'
               '"Gibis","","1-3","","não buscar","","",""\n')
    e = entradas(ler_csv(csv_txt), janela=0)
    promo = {d["id"]: d for d in e["promocoes"]}
    raro = {d["id"]: d for d in e["raros"]}
    assert promo["colecao-spawn-origens"]["volumes"] == "10,12-36"
    assert promo["colecao-spawn-origens"]["termos"] == ["spawn", "origens"]
    assert raro["colecao-berserk"]["volumes"] == "41"
    assert raro["colecao-berserk"]["buscas"] == ["berserk", "berserk 41"]     # raro busca toda rodada
    assert "colecao-akira" not in promo and "colecao-gibis" not in promo
    bloq = {d["id"]: d for d in e["bloqueios"]}
    assert bloq["colecao-akira-tenho"]["volumes"] == "1-6" and "colecao-gibis-tenho" in bloq
    sv = SpecVolume.parse(promo["colecao-spawn-origens"]["volumes"])
    assert sv.aceita(10) and sv.aceita(12) and sv.aceita(20) and not sv.aceita(11) and not sv.aceita(5)
    assert not sv.aceita(167)                       # numeração de outra edição
    ent = Entrada.de_dict(promo["colecao-spawn-origens"])
    assert ent.casa("spawn origens vol 10")


def test_colecao_reveza_as_buscas():
    from colecao import ler_csv, entradas
    csv_txt = '"Série","Volumes que tenho","Alerta"\n' + "".join(f'"Serie {i}","1-3","promoção"\n' for i in range(6))
    linhas = ler_csv(csv_txt)
    por_janela = [sum(len(d["buscas"]) for d in entradas(linhas, janela=j)["promocoes"]) for j in range(4)]
    assert por_janela == [3, 3, 3, 3]                     # metade das séries, uma busca cada
    b0 = {d["id"]: d["buscas"] for d in entradas(linhas, janela=0)["promocoes"]}
    b2 = {d["id"]: d["buscas"] for d in entradas(linhas, janela=2)["promocoes"]}
    assert b0["colecao-serie-0"] != b2["colecao-serie-0"]   # alterna entre busca geral e por volume


def test_mercado_livre_link_fixo_de_catalogo(monkeypatch):
    from stores.mercadolivre import MercadoLivreLoja
    monkeypatch.setenv("ML_CLIENT_ID", "1"); monkeypatch.setenv("ML_CLIENT_SECRET", "x")

    class Resp:
        status_code = 200
        text = ""
        def json(self): return {"access_token": "T", "expires_in": 21600}

    class Http:
        def post(self, *a, **k): return Resp()
    f = _FetcherFalso({"/products/MLB54797458/items": (200, {"results": [
        {"item_id": "MLB777", "price": 289.9, "condition": "new", "currency_id": "BRL"}]})})
    f.http = Http()
    loja = MercadoLivreLoja(f, {"nome": "Mercado Livre"})
    o = loja.validar(Oferta("Mercado Livre", "Questão Omnibus",
                            "https://www.mercadolivre.com.br/questao-vol-1/p/MLB54797458", 0, extra={"fixa": True}))
    assert o.disponivel and o.preco == 289.9 and o.url.endswith("MLB-777") and o.extra.get("fixa")


@pytest.mark.parametrize("titulo,alerta", [
    ("Spawn nº 167", False),                         # Abril: fora do teto de volumes
    ("Spawn Vol. 10 - Panini", True),
    ("Spawn Vol. 10 - Editora Abril", False),        # outra editora
    ("Spawn Vol. 10 - Pandora Books", False),
])
def test_spawn_outra_edicao(titulo, alerta):
    from colecao import ler_csv, entradas
    from rules import Entrada
    csv_txt = ('"Série","Editora","Volumes que tenho","Alerta","Palavras que o título precisa ter","Palavras para ignorar"\n'
               '"Spawn (mensal)","Panini","1-9","promoção","spawn","origens"\n')
    d = entradas(ler_csv(csv_txt), janela=0)["promocoes"][0]
    e = Entrada.de_dict(d)
    n = normalizar(titulo)
    assert (e.casa(n) and e.volumes.aceita(extrair_volume(n))) == alerta


def test_colecao_palavras_alternativas():
    from colecao import ler_csv, entradas
    from rules import Entrada
    csv_txt = ('"Série","Volumes que tenho","Alerta","Palavras que o título precisa ter","Palavras para ignorar"\n'
               '"The Lost Canvas Especial","1-3","promoção","lost canvas, especial|esp","gaiden"\n')
    d = entradas(ler_csv(csv_txt), janela=0)["promocoes"][0]
    e = Entrada.de_dict(d)
    assert e.casa(normalizar("CDZ The Lost Canvas ESP. #14 Determinação"))
    assert e.casa(normalizar("Cavaleiros do Zodíaco - The Lost Canvas Especial 6"))
    assert not e.casa(normalizar("The Lost Canvas - Saga de Hades 6"))
    assert not e.casa(normalizar("The Lost Canvas Gaiden Especial 6"))
    assert d["buscas"] == ["lost canvas especial"]


# ---------------- canal público de ofertas do Telegram
PAGINA_TG = """<html><body>
<div class="tgme_widget_message" data-post="mangasluofertas/115598"><div class="tgme_widget_message_text">
Elden Ring: o caminho para Térvore 06 | Amazon<br/><br/>R$13,54 (69%)<br/><a href="https://link.amazon/abc">https://link.amazon/abc</a><br/>
Entre no grupo: <a href="https://t.me/cupons">https://t.me/cupons</a></div>
<time datetime="2099-10-30T14:42:00+00:00"></time></div>
<div class="tgme_widget_message" data-post="mangasluofertas/115599"><div class="tgme_widget_message_text">
Cupom da loja | Shopee<br/>R$10 OFF acima de R$59<br/><a href="https://s.shopee.com.br/x">link</a></div>
<time datetime="2099-10-30T14:47:00+00:00"></time></div>
<div class="tgme_widget_message" data-post="mangasluofertas/100"><div class="tgme_widget_message_text">
Frieren 02 | Amazon<br/>R$20,00 (50%)<br/><a href="https://link.amazon/velho">x</a></div>
<time datetime="2001-01-01T10:00:00+00:00"></time></div>
</body></html>"""


def test_telegram_canal_le_posts_com_preco():
    from stores.telegram_canal import TelegramCanalLoja
    f = _FetcherFalso({"t.me/s/mangasluofertas": PAGINA_TG})
    loja = TelegramCanalLoja(f, {"nome": "Telegram @mangasluofertas", "canal": "mangasluofertas", "horas": 6})
    achados = loja.buscar("qualquer")
    assert len(achados) == 1                                   # cupom e post antigo ficam de fora
    o = achados[0]
    assert o.titulo == "Elden Ring: o caminho para Térvore 06" and o.preco == 13.54
    assert o.url == "https://link.amazon/abc" and o.loja == "Amazon (via @mangasluofertas)"
    assert round(o.preco_lista, 2) == 43.68                     # 69% de desconto
    assert loja.buscar("outra") == []                          # lê o canal uma vez por varredura
    assert loja.validar(o).disponivel
