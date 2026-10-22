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
    assert d.alertar and d.regra == "RADAR" and d.formato == "Omnibus"


def test_battle_royale_vol_6_nao_cai_no_radar():
    assert not avaliar(of("Battle Royale Omnibus Vol. 6", preco=10), CAT, ref_capa=100).alertar


@pytest.mark.parametrize("titulo,alerta", [
    ("Frieren Vol. 1", False), ("Frieren Vol. 2", True), ("Sakamoto Days Vol. 1", False),
    ("Sakamoto Days Vol. 5", True), ("Hunter x Hunter Vol. 37", False), ("Hunter x Hunter Vol. 38", True),
    ("Dandadan Vol. 1", True), ("Tartarugas Ninja Vol. 1 (Pipoca & Nanquim)", True),
])
def test_series_em_andamento(titulo, alerta):
    assert avaliar(of(titulo, preco=70), CAT, ref_capa=100).alertar is alerta, titulo


@pytest.mark.parametrize("titulo", [
    "Box Chainsaw Man - Volumes 1 ao 11 (Panini)",
    "Kit Mangá Jujutsu Kaisen 1 a 5",
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
    assert ("x", "one piece") in [("x", b) for _, b in g.todas_buscas()]


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
