# Radar HQ

Monitor de ofertas de mangás, HQs e games que avisa no **Telegram**. Roda sozinho no GitHub Actions, sem PC ligado e sem custo.

Cada pessoa do grupo tem a própria lista. O radar varre as lojas a cada 2 horas, confere se o produto tem estoque de verdade e só avisa quem está procurando aquele item.

```
GitHub Actions (a cada 2h) ──► varre as lojas ──► confere estoque ──► regras de cada pessoa ──► grupo no Telegram
        grátis, na nuvem          12 lojas          na página           Bruno, Thiago, Ivan        "BASTA LER"
```

---

## Como chega no grupo

```
🚨 NOVA OFERTA ENCONTRADA!
👥 Para: Bruno e Thiago

📖 Título: Spawn: Origens Vol. 14
📦 Formato: Encadernado
🏪 Loja: Amazon Brasil
⚡ Status: Novo Lacrado

🏷️ Preço de Capa: R$ 59,90
💰 Você Paga: R$ 39,50
📉 Desconto: 34% OFF (Economia de R$ 20,40)
🏆 MENOR PREÇO JÁ VISTO PELO RADAR! (antes: R$ 41,00, Comix, 22/10/2026)
🛍️ Também em: Comix R$ 41,00 · Comic Boom R$ 44,90

🔗 Link direto de compra:
https://www.amazon.com.br/dp/...
                                        [ 🛒 Comprar agora ]
```

* **Para:** quem tem esse item na lista.
* **Menor preço já visto:** o radar guarda o histórico de preço de cada produto em todas as lojas.
* **Também em:** o mesmo produto em várias lojas vira um alerta só, com a mais barata em destaque.

---

## Horários

| Quando | O que faz |
|---|---|
| 05h30, 07h30, 09h30, 11h30, 13h30, 15h30, 17h30, 19h30, 21h30 | Varredura e alertas |
| 23h30 | Varredura, **resumão do dia** (maiores descontos, recordes de preço, raros e difíceis de achar, total por pessoa) e **boletim das lojas** (quais rendem ofertas, quais bloqueiam e quais não trazem nada) |
| Quando alguém edita uma lista | O bot manda no grupo as listas atualizadas |

O GitHub pode atrasar alguns minutos em horário de pico.

---

## Estrutura do projeto

```
radar-hq/
├─ .github/workflows/radar.yml   agendamento e modos de execução (GitHub Actions)
├─ config/
│  ├─ catalogo.yaml              configurações gerais e radares permanentes (omnibus, boxes)
│  ├─ lojas.yaml                 lojas monitoradas e como cada uma é lida
│  └─ pessoas/
│     ├─ bruno.yaml              lista do Bruno
│     ├─ thiago.yaml             lista do Thiago
│     └─ ivan.yaml               lista do Ivan
├─ data/                         memória do radar (salva automaticamente a cada execução)
│  ├─ radar.db                   alertas enviados, histórico de preços, preços de capa
│  └─ plataformas.json           plataforma detectada de cada loja
├─ src/
│  ├─ main.py                    orquestrador: varredura, resumo, relatório, lista
│  ├─ rules.py                   regras de cada pessoa (raros, promoções, bloqueios, radares)
│  ├─ stock.py                   validação de estoque e de edição brasileira
│  ├─ mensagem.py                formato dos alertas e do resumão
│  ├─ db.py                      banco SQLite
│  ├─ texto.py                   leitura de título, volume e tamanho de pacote
│  ├─ whatsapp.py                envio (Telegram; WhatsApp como alternativa)
│  └─ stores/                    coletores por loja
│     ├─ amazon.py               Amazon (livros e games)
│     ├─ mercadolivre.py         Mercado Livre
│     ├─ plataformas.py          Magento, WooCommerce, VTEX, Shopify e HTML genérico
│     └─ base.py                 acesso às páginas (com navegador quando precisa)
├─ tests/test_regras.py          testes automáticos (rodam antes de cada varredura)
├─ GUIA_SEM_PC.md                passo a passo de instalação no Telegram + GitHub
└─ pegar_chat_id.py              ajuda a descobrir o ID do chat no Telegram
```

Arquivos do modo WhatsApp self-hosted (opcional, ver no fim): `docker-compose.yml`, `Dockerfile`, `conectar_whatsapp.py`, `whatsapp-baileys/`, `src/webhook_server.py`.

---

## As listas de cada pessoa

Cada arquivo em `config/pessoas/` tem quatro partes:

| Parte | Quando alerta |
|---|---|
| `raros` | Em **qualquer preço**, desde que tenha estoque |
| `promocoes` | Com **25% OFF ou mais**, ou **sem desconto se for difícil de achar** (estoque em até 2 lojas na varredura) |
| `bloqueios` | **Nunca**: o que a pessoa já tem |
| `radares` | Liga os radares gerais: `radar-omnibus` e `radar-boxes` (qualquer omnibus ou box/coleção com 25% OFF) |

### Exemplo de item

```yaml
  - id: dylan-dog-omnibus          # identificador único, sem espaços
    nome: "Dylan Dog Omnibus"      # como aparece nas listas do grupo
    termos: [dylan dog, omnibus]   # TODAS essas palavras precisam estar no título
    volumes: "4,5"                 # quais volumes interessam
    formato: Omnibus
    buscas: [dylan dog omnibus]    # o que o radar digita na busca das lojas
```

### Campos disponíveis

| Campo | Para que serve |
|---|---|
| `termos` | Todas precisam aparecer no título (sem acento, tanto faz maiúscula) |
| `qualquer` | Pelo menos uma precisa aparecer |
| `excluir` | Nenhuma pode aparecer |
| `regex` / `excluir_regex` | Casos difíceis (ex.: `superman\s+o\s+mundo`) |
| `volumes` | `"8,14"` · `"1-9"` · `"12+"` (do 12 em diante) · `todos` · `box` |
| `modo: pacote` | Só kits e coleções com vários volumes; o preço de capa é estimado pelo nº de volumes |
| `categoria: games` | Busca só nas lojas de games (Amazon e KaBuM!) |
| `so_menor_preco: true` | Só avisa quando bater o menor preço já visto |
| `urls` | Páginas de produto conferidas em toda varredura |
| `prioridade` | `maxima` ou `alta` sobem o alerta na fila |

### Como editar pelo GitHub

1. Abra `config/pessoas/<nome>.yaml` e clique no **lápis**.
2. Copie um item parecido, mude o que precisar e salve com **Commit changes**.
3. O bot manda no grupo a lista atualizada. Se algo estiver errado no arquivo, os testes falham e nada é enviado.

---

## Modos de execução

Em **Actions > Radar HQ > Run workflow**:

| Modo | O que faz |
|---|---|
| `teste` | Manda um alerta de exemplo no grupo |
| `lista` | Manda o que cada pessoa está procurando |
| `relatorio` | Varre tudo sem alertar e mostra, por pessoa, o que está **fácil** (3+ lojas), com **pouca oferta** (1 ou 2) ou **sem estoque** |
| `resumo` | Varredura e resumão do dia |
| `seco` | Varre e registra tudo no log, sem mandar nada |
| `agora` | Varredura normal, com alertas |

---

## Como o radar decide

**1. Estoque de verdade.** Um anúncio só vira alerta se a página do produto:
* responder normalmente e não redirecionar para busca ou página inicial;
* não estar marcada como esgotada (dados estruturados da página ou avisos como "esgotado", "avise-me", "anúncio pausado");
* ter o botão de compra ativo, ou um sinal próprio da loja (API de estoque, "Restam N unid.").

Avisos de "esgotado" em produtos relacionados, no rodapé da página, são ignorados.

**2. Edição brasileira.** Livros com ISBN de fora do Brasil (só valem os que começam com 85 ou 65), ficha com idioma estrangeiro ou títulos como *Paperback* e *English Edition* são descartados.

**3. Preço de capa confiável.** A referência vem das lojas das editoras (Panini, Pipoca & Nanquim, Monstra). O preço "de" de outras lojas só vale se não estiver inflado, e descontos acima de 75% são tratados como erro de leitura.

**4. Sem repetição.** O mesmo link só é reenviado se o preço cair mais 3% ou depois de 7 dias.

---

## Lojas

| Loja | Como é lida | Situação |
|---|---|---|
| Loja Panini | API da loja (Magento) | ✅ loja oficial, referência de capa |
| Pipoca & Nanquim | API da loja (Magento) | ✅ loja oficial, referência de capa |
| Monstra | API da loja (WooCommerce) | ✅ loja oficial, referência de capa |
| Comix Book Shop | API da loja (Magento) | ✅ |
| Comic Boom | API da loja (Magento) | ✅ |
| Rika Comic Shop | API da loja (VTEX) | ✅ |
| Ri Happy | API da loja (VTEX) | ✅ |
| Estante Virtual | página, com navegador | ✅ muitos usados (aparecem como Seminovo) |
| Amazon Brasil | página, com navegador | ⚠️ funciona, mas às vezes bloqueia (a janela é pulada) |
| KaBuM! | página, com navegador | ⚠️ games; em observação |
| Mercado Livre | API oficial de catálogo | ⚙️ precisa dos Secrets `ML_CLIENT_ID` e `ML_CLIENT_SECRET`; só produtos de catálogo |
| Magalu | desativada | ❌ bloqueia acessos vindos da nuvem |
| Shopee | desativada | ❌ exige login |

O radar nunca tenta burlar CAPTCHA ou bloqueio: quando uma loja bloqueia, aquela janela é pulada.

---

## Configuração (Secrets do GitHub)

Em **Settings > Secrets and variables > Actions**:

| Nome | Valor |
|---|---|
| `TELEGRAM_BOT_TOKEN` | token do bot, gerado no @BotFather |
| `TELEGRAM_CHAT_ID` | ID do grupo (número negativo) |
| `ML_CLIENT_ID` | App ID do app no portal de desenvolvedores do Mercado Livre (opcional) |
| `ML_CLIENT_SECRET` | Secret Key do mesmo app (opcional) |

Passo a passo completo em `GUIA_SEM_PC.md`.

---

## Rodar no próprio computador (opcional)

```bash
pip install -r requirements.txt
python -m playwright install chromium
cp .env.example .env          # preencha TELEGRAM_BOT_TOKEN e TELEGRAM_CHAT_ID
python -m pytest -q           # testes
python src/main.py --seco     # varre e mostra na tela, sem enviar
```

### Alternativa: WhatsApp self-hosted

O projeto também envia por WhatsApp via **Evolution API** (Docker) ou **Baileys** (Node.js), usando um número remetente separado e um computador ou servidor ligado 24h. Configure `CANAL=evolution` ou `CANAL=baileys` no `.env` e siga os comentários em `docker-compose.yml` e `whatsapp-baileys/index.js`.

A API oficial do WhatsApp (Meta) com número de teste não funciona para quem está no Brasil: a Meta bloqueia mensagens de números estrangeiros para o país (erro 130497).
