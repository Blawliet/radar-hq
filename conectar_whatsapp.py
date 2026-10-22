"""Cria a instância na Evolution API e salva o QR Code em data/qrcode.png.

Uso (com o docker compose rodando):
    python conectar_whatsapp.py
Depois abra data/qrcode.png e escaneie pelo WhatsApp do chip remetente:
    WhatsApp > Dispositivos conectados > Conectar um dispositivo
"""
import base64
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "src"))
from dotenv_mini import carregar_env  # noqa: E402
from whatsapp import EvolutionClient  # noqa: E402

carregar_env()
cli = EvolutionClient(
    os.getenv("EVOLUTION_URL", "http://localhost:8080"),
    os.environ["EVOLUTION_API_KEY"],
    os.getenv("EVOLUTION_INSTANCE", "radar"),
)

try:
    cli.criar_instancia()
    print("Instância criada.")
except Exception as e:  # já existe
    print(f"Instância já existe ou não pôde ser criada ({e}). Seguindo para o QR.")

if cli.estado() == "open":
    print("WhatsApp já está conectado.")
    sys.exit(0)

qr = cli.qrcode()
b64 = qr.get("base64", "")
if not b64:
    sys.exit(f"A API não devolveu QR Code. Resposta: {qr}")

destino = Path("data/qrcode.png")
destino.parent.mkdir(exist_ok=True)
destino.write_bytes(base64.b64decode(b64.split(",", 1)[-1]))
print(f"QR salvo em {destino.resolve()}. Escaneie em até 40 segundos.")

for _ in range(30):
    time.sleep(3)
    if cli.estado() == "open":
        print("Conectado! Mandando mensagem de teste...")
        ok = cli.enviar_texto(os.environ["WHATSAPP_DESTINO"], "✅ *Radar HQ conectado!*")
        print("Teste enviado." if ok else "Falhou o envio do teste, veja os logs.")
        sys.exit(0)
print("Tempo esgotado. Rode de novo para gerar outro QR.")
