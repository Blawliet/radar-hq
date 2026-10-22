"""Leitor mínimo de .env (evita dependência extra)."""
import os
from pathlib import Path


def carregar_env(caminho: str | None = None) -> None:
    p = Path(caminho) if caminho else Path(__file__).resolve().parent.parent / ".env"
    if not p.exists():
        return
    for linha in p.read_text(encoding="utf-8").splitlines():
        linha = linha.strip()
        if not linha or linha.startswith("#") or "=" not in linha:
            continue
        k, v = linha.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip())
