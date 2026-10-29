# Guia: Radar HQ no Telegram, sem PC ligado

```
GitHub Actions (6x por dia) ──► varre as lojas ──► bot do Telegram ──► seu celular
     grátis, na nuvem                                 grátis, oficial       com notificação
```

## Parte 1: Criar o bot (5 minutos)

1. No Telegram, procure **@BotFather** (tem selo azul de verificado) e toque em **Iniciar**.
2. Mande `/newbot`.
3. Nome do bot: `Radar HQ` (é o que aparece nas conversas).
4. Usuário do bot: precisa terminar em `bot` e ser único. Exemplo: `radarhq_bruno_bot`.
5. O BotFather responde com o **token**, algo como `7123456789:AAH...`. Guarde: ele é a senha do bot.
6. Toque no link do seu bot (`t.me/radarhq_bruno_bot`) e aperte **Iniciar**. Sem isso o bot não consegue falar com você.

## Parte 2: Descobrir o seu chat ID

Depois de apertar **Iniciar** no seu bot, abra no navegador, trocando `SEU_TOKEN`:

```
https://api.telegram.org/botSEU_TOKEN/getUpdates
```

Procure o trecho `"chat":{"id":123456789`. Esse número é o seu **chat ID**.

Se aparecer `"result":[]`, mande um "oi" para o bot e recarregue a página.

(Alternativa pelo terminal: `python pegar_chat_id.py SEU_TOKEN`)

## Parte 3: GitHub (onde o radar roda)

1. Crie uma conta em **github.com** e um repositório chamado `radar-hq`.
   * **Público**: minutos ilimitados de Actions. O token fica protegido em Secrets; o que fica visível é o código, o catálogo e os links já alertados.
   * **Privado**: 2.000 minutos grátis por mês. Seis execuções por dia só cabem se cada uma durar menos de 11 minutos.
2. Envie os arquivos do projeto. Pelo terminal, dentro da pasta extraída:
   ```bash
   git init
   git add .
   git commit -m "radar hq"
   git branch -M main
   git remote add origin https://github.com/SEU_USUARIO/radar-hq.git
   git push -u origin main
   ```
   Pelo site também dá (**Add file > Upload files**), mas confira se a pasta `.github` subiu, porque alguns sistemas escondem pastas que começam com ponto.
3. **Settings > Secrets and variables > Actions > New repository secret**. Crie dois:

   | Nome | Valor |
   |---|---|
   | `TELEGRAM_BOT_TOKEN` | o token do BotFather |
   | `TELEGRAM_CHAT_ID` | o número da Parte 2 |

## Parte 4: Ligar

1. Aba **Actions**. Se pedir, clique em **I understand my workflows, go ahead and enable them**.
2. **Radar HQ > Run workflow**, modo **teste**. Chega no Telegram um alerta de exemplo, com o botão **🛒 Comprar agora**.
3. Rode de novo no modo **seco** e abra o log do passo "Rodar o radar". Ele mostra tudo que achou e por que aceitou ou descartou cada item, sem mandar nada.
4. Pronto. A partir daí ele roda sozinho a cada 2 horas, das 05h30 às 23h30. Às 23h30 também chega o resumão do dia com as melhores ofertas.

Dica: no Telegram, abra a conversa com o bot, toque no nome dele e ative um **som de notificação próprio**. Assim você reconhece um alerta de oferta sem nem olhar o celular.

## Grupo com várias pessoas (Bruno, Thiago, Ivan)

Cada pessoa tem a própria lista em `config/pessoas/<nome>.yaml`. Os alertas vão para um grupo do Telegram e mostram para quem são:

```
🚨 NOVA OFERTA ENCONTRADA!
👥 Para: Bruno e Ivan
```

Cada um tem seus raros, promoções, bloqueios ("já tenho") e radares ligados. Um box que o Bruno já tem pode ser sugestão para o Ivan, e vice-versa.

**Montar o grupo:**

1. No Telegram, crie um grupo com o Thiago e o Ivan e adicione o bot **Radar HQ** como membro.
2. Mande qualquer mensagem no grupo.
3. Abra `https://api.telegram.org/botSEU_TOKEN/getUpdates` e procure o `"chat":{"id":-...` do grupo. O ID do grupo é **negativo**.
4. No GitHub, edite o Secret `TELEGRAM_CHAT_ID` e troque pelo ID do grupo, com o sinal de menos.

**Adicionar o que alguém quer:** edite o arquivo da pessoa no GitHub (clique no arquivo, depois no lápis) e salve. Cada arquivo tem exemplos comentados. Ao salvar, o bot manda no grupo a lista atualizada de todos.

**Ver as listas a qualquer momento:** Actions > Radar HQ > Run workflow, modo **lista**.

## Se algo der errado

* **Unauthorized (401)**: token errado. Copie de novo do BotFather, sem espaços.
* **chat not found (400)**: chat ID errado, ou você não apertou **Iniciar** no bot.
* **bot was blocked by the user (403)**: você bloqueou o bot sem querer. Abra a conversa e desbloqueie.
* **Execução atrasou**: normal. O GitHub pode atrasar agendamentos em horários de pico.
* **Workflow parou de rodar sozinho**: em repositório público, o GitHub pausa agendamentos depois de 60 dias sem atividade. O radar faz um commit por janela, o que deve evitar isso; se acontecer, chega um e-mail e é só reativar na aba Actions.

## Limites honestos deste modo

* **Lojas grandes bloqueiam mais quem vem da nuvem.** Amazon, Mercado Livre e Magalu tendem a recusar acessos de servidores do GitHub com mais frequência do que de uma casa. Essas janelas são puladas, nunca contornadas. As lojas oficiais e as menores costumam funcionar bem.
* **Escutar grupos de ofertas do WhatsApp não existe neste modo.**
