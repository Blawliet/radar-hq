"""Normalização de títulos e extração do número do volume."""
from __future__ import annotations

import re
import unicodedata

_ESPACOS = re.compile(r"\s+")


def normalizar(s: str) -> str:
    s = s.replace("º", "").replace("°", "").replace("ª", "")
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = s.lower()
    s = re.sub(r"[^\w#+]+", " ", s)   # pontuação vira espaço (mantém # e +)
    s = s.replace("_", " ")
    return _ESPACOS.sub(" ", s).strip()


# "vol 8", "volume 14", "v 3", "n 97", "no 97", "numero 2", "edicao 5", "tomo 2", "#10"
_VOL_ROTULADO = re.compile(
    r"(?:\bvol(?:ume)?s?|\bv|\bn|\bno|\bnum(?:ero)?|\bedicao|\bed|\btomo|\blivro|#)\s*0*(\d{1,3})\b"
)
# número solto no fim do título: "absolute batman 2"
_VOL_FINAL = re.compile(r"(?:^|\s)0*(\d{1,3})$")
# ruídos que parecem número de volume mas não são
_RUIDO = re.compile(
    r"\b\d+\s*em\s*1\b|\b\d+\s*in\s*1\b|\b\d+\s*paginas?\b|\b\d+\s*x\s*\d+\b"
    r"|\bde\s+\d+\b|\b\d+\s*cm\b|\b\d+\s*a\s*\d+\b|\b\d+\s*ao\s*\d+\b|\b\d+\s*ate\s*\d+\b"
)


# palavras que as lojas penduram no fim do título: "One Piece 103 Panini Novo Lacrado"
_SUFIXO = re.compile(
    r"(\s+(panini|jbc|newpop|pipoca|e|nanquim|mythos|devir|conrad|darkside|novo|lacrado|capa|dura|"
    r"brochura|manga|mangas|hq|lancamento|original|br|editora|comics))+$")


def extrair_volume(titulo_norm: str) -> int | None:
    t = _SUFIXO.sub("", _RUIDO.sub(" ", titulo_norm).strip())
    m = _VOL_ROTULADO.search(t)
    if m:
        return int(m.group(1))
    m = _VOL_FINAL.search(t.strip())
    if m:
        return int(m.group(1))
    return None


def contem(titulo_norm: str, termo: str) -> bool:
    """Busca por palavra/frase inteira, sem acento."""
    termo = normalizar(termo)
    return re.search(rf"(?<!\w){re.escape(termo)}(?!\w)", titulo_norm) is not None


_FAIXA = re.compile(r"(\d{1,3})\s*(?:a|ao|ate|até|-|–|/)\s*(\d{1,3})(?!\d)")
_QTD = re.compile(r"(\d{1,3})\s*(?:volumes|vols?\.?|livros|edi[cç][oõ]es|mang[aá]s|exemplares|unidades|hqs|revistas)\b")
_LISTA = re.compile(r"\bvol(?:ume)?s?\.?\s*((?:\d{1,3}\s*(?:,|e|\+|&)\s*)+\d{1,3})\b")


def contar_volumes(titulo: str) -> int | None:
    """Quantos volumes tem um pacote/kit, pelo título. None se não der para saber."""
    t = (titulo or "").lower()
    candidatos = []
    for a, b in _FAIXA.findall(t):
        a, b = int(a), int(b)
        if 1 <= a < b <= 150 and b - a < 100:
            candidatos.append(b - a + 1)
    for n in _QTD.findall(t):
        if 2 <= int(n) <= 150:
            candidatos.append(int(n))
    for grupo in _LISTA.findall(t):
        nums = re.findall(r"\d{1,3}", grupo)
        if len(nums) >= 2:
            candidatos.append(len(nums))
    return max(candidatos) if candidatos else None
