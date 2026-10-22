# Radar HQ

Monitor de ofertas de mangás e quadrinhos com alertas no celular, 100% gratuito.

Dois jeitos de rodar:

* **Telegram sem PC (recomendado):** GitHub Actions + bot do Telegram. Nada fica ligado na sua casa, sem chip extra, sem custo. Passo a passo em `GUIA_SEM_PC.md`.
* **WhatsApp self-hosted:** Evolution API ou Baileys num PC/servidor ligado 24h, com um número remetente separado. Descrito abaixo.

> A API oficial do WhatsApp (Meta) com número de teste não funciona para destinatários no Brasil: a Meta bloqueia mensagens de números estrangeiros para o país (erro 130497).

```
                ┌────────────── docker compose ──────────────┐
  6 janelas/dia │  radar (Python)                            │
  05:30 09:00   │   ├─ coletores por loja (paralelo)         │
  12:00 15:00 ──┤   ├─ validação de estoque na página        │
  19:00 23:30   │   ├─ regras A / B / C + Radar Omnibus      │
                │   ├─ SQLite (anti-spam + preço de capa)    │
                │   └─ cliente HTTP ──► Evolution API ───────┼──► seu WhatsApp
                │                       (Postgres + Redis)   │
                │   webhook opcional ◄── grupos de ofertas   │
                └────────────────────────────────────────────┘
```

## Estrutura

```
radar-hq/
├─ docker-compose.yml        Evolution API + Postgres + Redis + monitor
├─ Dockerfile                imagem do monitor (com Chromium para Playwright)
├─ .env.example              todas as configurações
├─ conectar_whatsapp.py      cria a instância e gera o QR Code
├─ verificar_lojas.py        confere URL e plataforma de cada loja
├─ config/
│  ├─ catalogo.yaml          Regras A, B, C e Radar Omnibus (edite aqui)
│  └─ lojas.yaml             lojas monitoradas
├─ src/
│  ├─ main.py                orquestrador + agendador
│  ├─ rules.py               motor de regras
│  ├─ stock.py               validação de estoque no HTML
│  ├─ mensagem.py            formato obrigatório da mensagem
│  ├─ whatsapp.py            clientes Evolution e Baileys
│  ├─ webhook_server.py      escuta grupos de ofertas
│  ├─ db.py                  SQLite
│  └─ stores/                coletores (VTEX, Shopify, Woo, Nuvemshop, HTML, Amazon, ML)
├─ whatsapp-baileys/         alternativa leve sem Docker (Node.js)
└─ tests/                    55 testes das regras, estoque e mensagem
```

---

## 1. Antes de começar: duas decisões

**Onde rodar.** As janelas de 05h30 e 23h30 exigem uma máquina ligada direto.
* Seu PC, se ele fica ligado 24h.
* Uma VM gratuita da **Oracle Cloud (Always Free)**: a instância ARM Ampere cabe tudo com folga. Pede cartão só para verificação de identidade.

**Número remetente.** Evolution API e Baileys não são oficiais: o WhatsApp pode banir o número que dispara mensagens, e o risco sobe se ele também ler grupos. Use um **chip secundário** como remetente e receba os alertas no seu número pessoal. Volume baixo (poucas mensagens por dia, sempre para o mesmo contato) é o cenário de menor risco.

---

## 2. Opção principal: Evolution API com Docker

```bash
# 1. Configure
cp .env.example .env
#   edite: CANAL=evolution, EVOLUTION_API_KEY, POSTGRES_PASSWORD, WHATSAPP_DESTINO (seu número pessoal)

# 2. Suba os serviços
docker compose up -d evolution postgres redis

# 3. Conecte o chip remetente
pip install -r requirements.txt
python conectar_whatsapp.py
#   abra data/qrcode.png e escaneie: WhatsApp > Dispositivos conectados > Conectar
#   ao conectar, chega um "✅ Radar HQ conectado!" no seu número pessoal

# 4. Confira as lojas (algumas URLs estão marcadas como CONFIRA)
python verificar_lojas.py

# 5. Teste a varredura sem enviar nada
python src/main.py --seco

# 6. Suba o monitor agendado
docker compose up -d --build radar
docker compose logs -f radar
```

Endpoints da Evolution usados (v2):

| Ação | Método e rota |
|---|---|
| Criar instância | `POST /instance/create` |
| QR Code | `GET /instance/connect/{instancia}` |
| Estado | `GET /instance/connectionState/{instancia}` |
| Enviar texto | `POST /message/sendText/{instancia}` com `{"number","text"}` |
| Webhook | `POST /webhook/set/{instancia}` evento `MESSAGES_UPSERT` |

Todas com header `apikey: <EVOLUTION_API_KEY>`. Negrito (`*texto*`), quebras de linha e emojis funcionam direto no campo `text`.

**Escutar grupos de ofertas:** entre com o chip remetente nos grupos, coloque `WEBHOOK_ENABLED=true` e ajuste `WEBHOOK_PALAVRAS` no `.env`. O monitor registra o webhook sozinho, aplica os bloqueios da Regra C, abre o link e só encaminha se o estoque for confirmado (`WEBHOOK_SO_VALIDADOS=true`).

---

## 3. Alternativa leve: Baileys sem Docker

```bash
cd whatsapp-baileys
npm install
BAILEYS_TOKEN=um-token-qualquer node index.js     # QR aparece no terminal
```

No `.env`: `CANAL=baileys`, `BAILEYS_URL=http://localhost:3333`, `BAILEYS_TOKEN=...`.
O resto do sistema não muda. A sessão fica salva em `whatsapp-baileys/auth/`.
Rode o monitor com `python src/main.py --agendar` (ou como serviço do sistema).

---

## 4. Meta WhatsApp Cloud API: por que ficou de fora

O modelo de 1.000 conversas grátis por mês **não existe mais**. Desde 1º de julho de 2025 a Meta cobra por mensagem. Mensagens de texto livre só são permitidas e gratuitas dentro da janela de 24h aberta quando **você** manda mensagem para o número do bot. Fora dela, só templates aprovados, e templates de utilidade fora da janela são pagos.

Na prática: para ser grátis, você teria que mandar mensagem para o bot todo dia. Funciona como plano C, mas não é "gratuito e permanente" de verdade.

## 5. pywhatkit / Selenium: contingência não implementada

Abre o WhatsApp Web num navegador e "digita" a mensagem. Exige o PC ligado com tela e sessão ativa, quebra a cada mudança do WhatsApp Web e é tão não oficial quanto o Baileys, só que mais frágil. Se a Evolution e o Baileys caírem ao mesmo tempo, o problema é o WhatsApp, e o pywhatkit cai junto. Por isso não entrou.

---

## Como a validação de estoque funciona

Um anúncio só vira alerta se passar por tudo isto:

1. **URL direta**: a página precisa responder HTTP 200 e não pode ter redirecionado para home, busca, login ou carrinho.
2. **Fonte mais confiável da loja**: API de catálogo (VTEX `AvailableQuantity`, Shopify `variant.available`, Woo `is_in_stock`, ML `status=active` + `available_quantity`) quando existe.
3. **JSON-LD** da página: `OutOfStock`, `SoldOut` ou `Discontinued` descarta.
4. **Avisos na área de compra**: "esgotado", "indisponível", "não há mais estoque disponível", "avise-me quando chegar", "anúncio pausado" e similares descartam. Avisos em vitrines de outros produtos são ignorados.
5. **Botão de compra habilitado** ("Comprar", "Adicionar ao carrinho"...). Botão com `disabled` descarta.
6. **CAPTCHA ou verificação anti-robô**: o item é pulado naquela janela. O sistema nunca tenta burlar.

Tudo que é checado vai para a tabela `historico` em `data/radar.db`, com o motivo de aceite ou descarte.

## Preço de capa e o corte de 25%

Desconto "real" exige saber o preço de capa. O sistema usa, nesta ordem:

1. **Preço da loja oficial da editora** (Loja Panini e Pipoca & Nanquim, marcadas com `oficial: true`). Elas rodam primeiro em cada varredura e alimentam a tabela `capas`.
2. **Preço "de" da própria loja**, só se não estiver inflado (até 5% acima da referência oficial). "De R$ 200 por R$ 90" num livro de capa R$ 100 conta como 10%, não 55%.
3. Sem nenhuma referência confiável, a Regra B **não alerta** (rigor acima de volume).

A Regra A alerta em qualquer valor. Se o preço estiver acima da capa, a linha de desconto mostra quanto está acima.

## Anti-spam

O mesmo link só é reenviado se o preço cair mais 3% ou depois de `REALERTAR_DIAS` (padrão 7).

## Situação de cada loja

| Loja | Como coleta | Observação |
|---|---|---|
| Loja Panini | auto-detecção | URL marcada CONFIRA |
| Pipoca & Nanquim | auto-detecção | URL marcada CONFIRA |
| Amazon Brasil | navegador real | bloqueia robôs com frequência; janelas bloqueadas são puladas |
| Mercado Livre | API com token, ou HTML | sem token, a busca costuma cair em verificação |
| Magalu | HTML no navegador | pode ter antirrobô |
| Ri Happy | auto-detecção | |
| Estante Virtual | HTML no navegador | anúncios majoritariamente usados (Status: Seminovo) |
| Rika, Comix, Monstra, Comic Boom | auto-detecção | URLs marcadas CONFIRA |
| Shopee | **desativada** | busca exige login e antirrobô agressivo; use o webhook de grupos |

Para o Mercado Livre: crie um app grátis em developers.mercadolivre.com.br, gere o access token e coloque em `ML_ACCESS_TOKEN`. O token expira em 6 horas; para uso contínuo, implemente o refresh token (posso fazer isso num próximo passo).

## Editando o catálogo

Tudo fica em `config/catalogo.yaml`. Formatos aceitos em `volumes`: `"8,14"`, `"1-9,11"`, `"12+"`, `todos`, `box`.

As séries em andamento (Dandadan, Tartarugas Ninja IDW, Hunter x Hunter, Frieren, Sakamoto Days) estão **desativadas**: troque `"99+"` pelo próximo volume que você ainda não tem e mude `ativo: true`.

Depois de editar, rode `python -m pytest -q` para garantir que nada quebrou.

## Comandos

```bash
python src/main.py --seco      # varre e imprime, sem enviar
python src/main.py --agora     # varre e envia
python src/main.py --teste     # manda uma mensagem de exemplo
python src/main.py --agendar   # modo contínuo (padrão no Docker)
python -m pytest -q            # testes
```

## Problemas comuns

* **QR Code não aparece**: atualize a tag da imagem `evoapicloud/evolution-api` no compose para a versão mais recente e rode `docker compose pull evolution && docker compose up -d evolution`.
* **Mensagens não chegam**: `docker compose logs evolution` e confira se `WHATSAPP_DESTINO` tem DDI 55 + DDD + número.
* **Loja sem resultados**: veja a tabela `historico` ou rode com `LOG_LEVEL=DEBUG`. Lojas mudam o layout; ajuste `busca` e `padrao_produto` em `lojas.yaml`.
