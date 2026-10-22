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

CAT = Catalogo(RAIZ / "config" / "catalogo.yaml")


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
])
def test_volume(titulo, vol):
    assert extrair_volume(normalizar(titulo)) == vol


# ---------------- Regra A
@pytest.mark.parametrize("titulo", [
    "A Saga do Flash Vol. 8", "A Saga do Flash Vol. 14", "One Piece Vol. 97", "One Piece Vol. 104",
    "Blueberry: Edição Definitiva Vol. 4", "Supergirl por Peter David Vol. 5",
    "Spawn Origens Vol. 10", "Spawn Origens Vol. 15", "Spawn Vol. 9", "Spawn Vol. 30",
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
    "Invencível Vol. 2", "Spawn Vol. 3", "Spawn Origens Vol. 11", "Spawn Origens Vol. 4",
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
    assert not avaliar(of("Invencível Vol. 4", preco=80), CAT, ref_capa=100).alertar


def test_promo_sem_capa_nao_alerta():
    d = avaliar(of("Invencível Vol. 4", preco=50), CAT, ref_capa=None)
    assert not d.alertar and "capa" in d.motivo


def test_preco_de_inflado_e_ignorado():
    # loja diz "de R$ 200 por R$ 90", mas a capa real é 100: só 10% off
    d = avaliar(of("Invencível Vol. 5", preco=90, lista=200), CAT, ref_capa=100)
    assert not d.alertar and d.preco_capa == 100


def test_box_gantz_parte_2_nao_e_bloqueado():
    d = avaliar(of("Box Gantz Parte 2 - Vols. 19 ao 37", preco=300), CAT, ref_capa=500)
    assert d.alertar and d.entrada_id == "box-gantz-parte-2"


def test_radar_omnibus():
    d = avaliar(of("Homem-Aranha por Todd McFarlane Omnibus", preco=300), CAT, ref_capa=450)
    assert d.alertar and d.regra == "OMNIBUS" and d.formato == "Omnibus"


def test_battle_royale_vol_6_nao_cai_no_radar():
    assert not avaliar(of("Battle Royale Omnibus Vol. 6", preco=10), CAT, ref_capa=100).alertar


def test_series_inativas_nao_alertam():
    assert not avaliar(of("Frieren Vol. 14", preco=10), CAT, ref_capa=100).alertar


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
    assert params[3]["text"] == "Raro em Estoque"



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
