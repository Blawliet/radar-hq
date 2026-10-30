"""Coleção da pessoa numa planilha Google: o radar busca só os volumes que faltam.

No arquivo da pessoa (config/pessoas/<nome>.yaml):

    colecao:
      planilha: <id da planilha>      # a parte entre /d/ e /edit no link
      aba: Coleção                    # opcional

A planilha precisa estar com "Qualquer pessoa com o link: Leitor". Colunas, por
nome do cabeçalho (a ordem não importa):

    Série | Editora | Volumes que tenho | Total de volumes | Alerta |
    Palavras que o título precisa ter | Palavras para ignorar

Cada linha vira:
  * um BLOQUEIO com os volumes que a pessoa já tem (nunca alerta deles);
  * uma entrada de PROMOÇÃO ou RARO com os volumes que faltam (e os próximos,
    se a série estiver em andamento).

A última leitura boa fica em data/colecao/<pessoa>.csv: se a planilha não
responder, o radar usa a cópia.
"""
from __future__ import annotations

import csv
import io
import logging
import os
import re
from pathlib import Path
from urllib.parse import quote

from texto import normalizar

log = logging.getLogger("colecao")
PASTA = Path(__file__).resolve().parents[1] / "data" / "colecao"
_lidas: dict[str, list[dict]] = {}


# ------------------------------------------------------------------ volumes
def expandir(texto: str) -> list[int]:
    """'1-5, 7, 9' -> [1, 2, 3, 4, 5, 7, 9]"""
    vols = set()
    for parte in re.split(r"[,;/]", str(texto or "")):
        parte = parte.strip().replace(" ", "")
        if not parte:
            continue
        m = re.fullmatch(r"(\d+)(?:[-–a~](\d+))?", parte)
        if not m:
            continue
        a = int(m.group(1))
        b = int(m.group(2) or a)
        if b < a:
            a, b = b, a
        if b - a > 500:
            continue
        vols.update(range(a, b + 1))
    return sorted(vols)


def compactar(vols: list[int]) -> str:
    """[1, 2, 3, 5, 8, 9] -> '1-3,5,8-9'"""
    partes, i = [], 0
    vols = sorted(set(vols))
    while i < len(vols):
        j = i
        while j + 1 < len(vols) and vols[j + 1] == vols[j] + 1:
            j += 1
        partes.append(str(vols[i]) if i == j else f"{vols[i]}-{vols[j]}")
        i = j + 1
    return ",".join(partes)


def faltam(tenho: list[int], total: int | None) -> tuple[list[int], int | None]:
    """(volumes que faltam até o fim conhecido, primeiro volume 'em diante' ou None)"""
    if total:
        return [v for v in range(1, total + 1) if v not in tenho], None
    topo = max(tenho) if tenho else 0
    return [v for v in range(1, topo + 1) if v not in tenho], topo + 1


# ------------------------------------------------------------------ planilha
def _coluna(cab: list[str], *chaves: str) -> int | None:
    for i, c in enumerate(cab):
        n = normalizar(c)
        if any(n.startswith(k) for k in chaves):
            return i
    return None


def ler_csv(texto: str) -> list[dict]:
    linhas = list(csv.reader(io.StringIO(texto)))
    if not linhas:
        return []
    cab = linhas[0]
    idx = {
        "serie": _coluna(cab, "serie"), "editora": _coluna(cab, "editora"),
        "tenho": _coluna(cab, "volumes que tenho", "tenho"), "total": _coluna(cab, "total"),
        "alerta": _coluna(cab, "alerta"), "termos": _coluna(cab, "palavras que"),
        "excluir": _coluna(cab, "palavras para ignorar", "ignorar", "excluir"),
    }
    if idx["serie"] is None or idx["tenho"] is None:
        log.warning("Planilha da coleção sem as colunas Série e Volumes que tenho.")
        return []
    saida = []
    for ln in linhas[1:]:
        def campo(k):
            i = idx[k]
            return ln[i].strip() if i is not None and i < len(ln) else ""
        if not campo("serie"):
            continue
        tot = re.sub(r"\D", "", campo("total"))
        saida.append({"serie": campo("serie"), "editora": campo("editora"), "tenho": campo("tenho"),
                      "total": int(tot) if tot else None, "alerta": normalizar(campo("alerta")) or "promocao",
                      "termos": campo("termos"), "excluir": campo("excluir")})
    return saida


def baixar(planilha: str, aba: str, pessoa: str) -> list[dict]:
    chave = f"{planilha}|{aba}"
    if chave in _lidas:
        return _lidas[chave]
    import sys
    slug = re.sub(r'[^a-z0-9]+', '-', normalizar(pessoa))
    if "pytest" in sys.modules:
        # testes usam uma cópia fixa (tests/fixtures), nunca a planilha real
        fix = Path(os.getenv("RADAR_COLECAO_FIXTURES", "/nao-existe")) / f"colecao-{slug}.csv"
        return ler_csv(fix.read_text(encoding="utf-8")) if fix.exists() else []
    copia = PASTA / f"{slug}.csv"
    texto = None
    if True:
        try:
            import httpx
            url = (f"https://docs.google.com/spreadsheets/d/{planilha}/gviz/tq?tqx=out:csv"
                   f"&sheet={quote(aba)}&headers=1")
            r = httpx.get(url, timeout=20, follow_redirects=True)
            if r.status_code == 200 and "text/csv" in r.headers.get("content-type", ""):
                texto = r.text
                PASTA.mkdir(parents=True, exist_ok=True)
                copia.write_text(texto, encoding="utf-8")
            else:
                log.warning("Planilha da coleção de %s não abriu (HTTP %s). Ela está com "
                            "'Qualquer pessoa com o link: Leitor'?", pessoa, r.status_code)
        except Exception as e:
            log.warning("Planilha da coleção de %s não respondeu (%s).", pessoa, e)
    if texto is None and copia.exists():
        log.info("Coleção de %s: usando a última cópia salva.", pessoa)
        texto = copia.read_text(encoding="utf-8")
    _lidas[chave] = ler_csv(texto) if texto else []
    return _lidas[chave]


# ------------------------------------------------------------------ entradas
def _lista(texto: str) -> list[str]:
    return [normalizar(x) for x in re.split(r"[,;]", texto or "") if normalizar(x)]


def entradas(linhas: list[dict], janela: int | None = None) -> dict[str, list[dict]]:
    """Linhas da planilha -> {'bloqueios': [...], 'raros': [...], 'promocoes': [...]} no formato do YAML.

    Para a varredura não dobrar de tamanho, cada série faz UMA busca a cada duas
    janelas (metade das séries em cada rodada de 2 h), alternando entre a busca
    geral e as buscas por volume. As regras continuam valendo em todas as rodadas:
    um volume que falta achado por outra busca também gera alerta.
    """
    import time
    if janela is None:
        janela = int(time.time() // 7200)
    out = {"bloqueios": [], "raros": [], "promocoes": []}
    for i, ln in enumerate(linhas):
        nome = ln["serie"]
        slug = re.sub(r"[^a-z0-9]+", "-", normalizar(nome)).strip("-")
        termos = _lista(ln["termos"]) or [normalizar(re.sub(r"\(.*?\)", "", nome))]
        excluir = _lista(ln["excluir"])
        tenho = expandir(ln["tenho"])
        base = {"nome": nome, "termos": termos, "excluir": excluir}
        if ln["editora"]:
            base["editora"] = ln["editora"]
        if tenho:
            out["bloqueios"].append({**base, "id": f"colecao-{slug}-tenho", "volumes": compactar(tenho)})
        alerta = ln["alerta"]
        if alerta.startswith("nao"):
            continue
        buracos, seguinte = faltam(tenho, ln["total"])
        if not buracos and not seguinte:
            continue
        spec = compactar(buracos)
        if seguinte:
            # série em andamento: os próximos volumes, com um teto (último que tenho + 25).
            # Evita pegar a numeração de outra edição, como Spawn nº 167 da Abril.
            prox = f"{seguinte}-{seguinte + 24}"
            legivel = f"{spec},{seguinte}+" if spec else f"{seguinte}+"
            spec = f"{spec},{prox}" if spec else prox
        else:
            legivel = spec
        termo_busca = " ".join(termos)
        alvo = (buracos + ([seguinte] if seguinte else []))[:3]
        todas = [termo_busca] + [f"{termo_busca} {v}" for v in alvo]
        if alerta.startswith("raro"):
            buscas = todas[:3]          # raro procura em toda rodada, sem revezar
        else:
            buscas = [todas[(janela // 2) % len(todas)]] if (i + janela) % 2 == 0 else []
        destino = "raros" if alerta.startswith("raro") else "promocoes"
        out[destino].append({**base, "id": f"colecao-{slug}", "volumes": spec, "volumes_txt": legivel,
                             "buscas": buscas,
                             "origem": "planilha"})
    return out


def carregar(cfg: dict, pessoa: str) -> dict[str, list[dict]]:
    if not cfg or not cfg.get("planilha"):
        return {"bloqueios": [], "raros": [], "promocoes": []}
    linhas = baixar(str(cfg["planilha"]), cfg.get("aba") or "Coleção", pessoa)
    return entradas(linhas)
