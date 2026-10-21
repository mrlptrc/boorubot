# post-automation (boorubot)

Publica as artes da artista em Danbooru, rule34.paheal e rule34.xxx.
Ela sobe a arte, revisa as tags e aprova; o robô posta **1 por dia**, no horário dela, em todos os sites marcados.

| Site | Como publica | Login |
|---|---|---|
| Danbooru | API oficial (`/uploads.json` → `/posts.json`) | chave de API, cadastrada no painel |
| rule34.paheal | formulário web via Playwright, **sem janela** no envio; tags próprias (só série, personagem e artista) | Sites → Entrar (uma vez) |
| rule34.xxx | **envio assistido**: a Cloudflare barra o navegador do robô, então o painel prepara arte, tags e rating e ela envia pelo navegador dela | no navegador dela |

Saíram do projeto: **Gelbooru** (copia sozinho os posts do Danbooru, pela conta "danbooru") e **rule34.us**
(o formulário de envio do site não manda o arquivo). A migração do banco tira os dois das artes e do histórico.

Regras centrais do código:
- **Teste e produção nunca se misturam**: "Testar este post" grava só em `test_runs` e não mexe em fila,
  status nem ritmo; `publications` guarda só envios reais; com o modo teste ligado a produção é bloqueada.
- **Captcha é sempre resolvido por uma pessoa.** Nada de serviço de captcha, proxy ou truque anti-bot.
- **Fila + limitador de ritmo**: post aprovado tem posição, não data; no máximo 1 por dia, 12 h entre posts,
  e atraso (PC desligado/dormindo) não acumula.

## Instalação no PC dela (Windows)

1. Copiar a pasta do boorubot para o PC dela (ex.: `C:\boorubot`). Se vier do pacote de entrega, a pasta
   `data\` já traz a fila, o histórico e a chave do Danbooru.
2. Na pasta, no PowerShell:
   ```powershell
   powershell -ExecutionPolicy Bypass -File scripts\instalar-windows.ps1
   ```
   Instala o uv (se faltar), as dependências e o navegador do robô, deixa o painel abrindo escondido com o
   Windows e abre o painel em http://127.0.0.1:8765.
3. **Desligar o painel do computador antigo** antes de ligar o dela: dois painéis com a mesma conta postam em dobro.
4. Ajustar a energia do Windows para o PC **não dormir na tomada** (o guia do painel explica, seção
   "O PC precisa estar acordado"). PC dormindo = post atrasado até ele acordar.
5. No painel: **Sites → Entrar** no paheal (o login fica salvo no computador onde o painel roda, não vai junto),
   escolher o **horário do post** e a **pasta do backup** (OneDrive/Google Drive) em Ajustes.
6. Instalar um acesso remoto (Chrome Remote Desktop, AnyDesk ou Tailscale) para a supervisão.

O que fica no PC (tudo fora do git):
- `data/`: banco (fila, histórico, métricas, configurações) e imagens. É o que vai para o backup.
- `.profiles/`: sessões logadas dos sites (cookies). Não vai para o backup; se perder, é só entrar de novo.
- `runs/`: `panel.log` (com rotação) e prints/diagnóstico de cada execução.

## Painel (uso do dia a dia)

- **Início (painel do dia)**: o que sai hoje, folga da fila (quantos dias seguidos têm post), próximo post,
  favoritos na semana, um alerta por vez (sessão caída > backup falhando > modo teste), próximos 7 dias e
  "Para revisar" (upload + rascunhos + falhas). Arraste a arte → **✨ Sugerir com IA** (tags + rating no padrão
  Danbooru, WD Tagger local) → revise → marque os sites → **Aprovar e agendar**. Reordenar: início/fim da fila.
- **🧪 Testar este post**: simula em cada site (Danbooru envia ao testbooru só se houver conta de teste).
- **Calendário**: semana × site com a arte prevista em cada dia, "pulado (OC)" no paheal, "esperando login"
  quando a sessão do site caiu, e o que está aprovado mas ainda sem dia.
- **Métricas**: favoritos ganhos, score médio nas primeiras 24 h, posts publicados, favoritos por semana,
  artes mais amadas e tabela por site (7 dias · 30 dias · tudo). Botão "Atualizar métricas".
- **Sites**: contas, início automático, backup, **ritmo por site**, horário do post, tags e sites padrão, e em
  "Avançado" o modo do navegador por site (invisível / janela escondida / visível).
- **Histórico**: só envios reais, com score e favoritos do Danbooru.
- **Guia**: passo a passo para a cliente. **Ctrl+K** busca artes, páginas e ações.

### Ritmo por site (Sites → Ritmo por site)

A fila principal define o "dia 0" de cada arte (1 por dia, intervalo mínimo de 12 h, atraso não acumula).
Cada site percorre a mesma fila no seu ritmo:

| Ritmo | Quando | Efeito |
|---|---|---|
| 1 por dia (padrão) | mesmo dia (padrão) | sai junto com o dia 0, exatamente como antes |
| 1 por dia | N dias depois | sai N dias depois do dia 0 da arte (ex.: paheal 1 dia depois do Danbooru) |
| dia sim, dia não | qualquer | no máximo 1 envio a cada 2 dias nesse site; a fila dele anda mais devagar |
| desligado | — | o site não recebe nada |

Sites fora do padrão ganham linhas em `site_schedule` (agendado → publicado/falhou/pulado), enviadas pelo
agendador de cada site: 1 por dia por site, 1 envio por vez no total, e nunca reenvia para um site que já recebeu.
Envio atrasado de um site não conta como "post do dia" da fila principal.

A sugestão de tags baixa ~380 MB na primeira vez (modelo `SmilingWolf/wd-vit-tagger-v3`).

## Comandos

```bash
uv run boorubot panel [--no-browser] [--port 8765]   # se já estiver rodando, só abre o navegador
uv run boorubot startup install|remove|status        # início automático com o Windows (.vbs na pasta Inicializar)
uv run boorubot check [site...] [--headed]           # reconhecimento sem credenciais
uv run boorubot login paheal                         # abre o navegador; login (e captcha) e feche a janela
uv run boorubot discover paheal                      # logado: salva os campos reais do form de upload em runs/
uv run boorubot post queue/exemplo-001               # CLI do técnico: dry-run com o .env (testbooru)
uv run pytest
```

O `.env` (veja `.env.example`) é só para a CLI do técnico. O painel usa apenas as contas cadastradas nele.

## Validação antes de ligar a produção

1. Danbooru no **testbooru** (conta de teste em Sites → Danbooru → "Conta de teste") e "Testar este post".
2. `login` + `discover` em cada site de formulário; conferir as heurísticas de `src/boorubot/sites/forms.py`
   (tags/source/title/rating) contra os campos reais.
3. "Testar este post" com a conta real dela → conferir os prints em `runs/` → primeiro envio real.
4. Escolher o horário do post diário num horário em que o PC dela costuma estar ligado.

Histórico das pendências de cada site (incluindo por que Gelbooru e rule34.us saíram):
[docs/PENDENCIAS-SITES.md](docs/PENDENCIAS-SITES.md).

## Próxima fase (se ela pedir)

Servidor na nuvem (VPS) para não depender do PC ligado e acordado: painel com senha na internet e
navegador remoto para logins/captchas. O guia do painel já explica essa opção para ela.
