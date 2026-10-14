# post-automation (boorubot)

Publica as artes da artista em Danbooru, Gelbooru, rule34.paheal, rule34.us e rule34.xxx.
Ela sobe a arte, revisa as tags e aprova; o robô posta **1 por dia**, no horário dela, em todos os sites marcados.

| Site | Como publica | Login |
|---|---|---|
| Danbooru | API oficial (`/uploads.json` → `/posts.json`) | chave de API, cadastrada no painel |
| Gelbooru, paheal, rule34.us | formulário web via Playwright, **sem janela** no envio | Sites → Entrar (uma vez) |
| rule34.xxx | formulário web via Playwright, **janela fora da tela** (Cloudflare barra headless) | idem; o login tem captcha |

Regras centrais do código:
- **Teste e produção nunca se misturam**: "Testar este post" grava só em `test_runs` e não mexe em fila,
  status nem ritmo; `publications` guarda só envios reais; com o modo teste ligado a produção é bloqueada.
- **Captcha é sempre resolvido por uma pessoa.** Nada de serviço de captcha, proxy ou truque anti-bot.
- **Fila + limitador de ritmo**: post aprovado tem posição, não data; no máximo 1 por dia, 12 h entre posts,
  e atraso (PC desligado/dormindo) não acumula.

## Instalação no PC dela (Windows)

1. Instalar o [uv](https://docs.astral.sh/uv/) e clonar este repositório (ex.: `C:\boorubot`).
2. Na pasta do projeto:
   ```powershell
   uv sync
   uv run playwright install chromium
   uv run boorubot startup install   # painel abre escondido quando o Windows liga
   uv run boorubot panel             # abre agora, em http://127.0.0.1:8765
   ```
3. Ajustar a energia do Windows para o PC **não dormir na tomada** (o guia do painel explica, seção
   "O PC precisa estar acordado"). PC dormindo = post atrasado até ele acordar.
4. No painel, seguir o **Guia** (menu Guia) com ela: conectar o Danbooru (chave de API) e entrar em cada site,
   fazer um "Testar este post", e só então desligar o modo teste.
5. Em Sites → Backup diário, apontar para uma pasta do OneDrive dela.
6. Instalar um acesso remoto (Chrome Remote Desktop, AnyDesk ou Tailscale) para a supervisão.

O que fica no PC (tudo fora do git):
- `data/`: banco (fila, histórico, métricas, configurações) e imagens. É o que vai para o backup.
- `.profiles/`: sessões logadas dos sites (cookies). Não vai para o backup; se perder, é só entrar de novo.
- `runs/`: `panel.log` (com rotação) e prints/diagnóstico de cada execução.

## Painel (uso do dia a dia)

- **Fila**: arraste a arte → **✨ Sugerir com IA** (tags + rating no padrão Danbooru, WD Tagger local)
  → revise → marque os sites → **Aprovar e agendar**. Reordenar: início/fim da fila.
- **🧪 Testar este post**: simula em cada site (Danbooru envia ao testbooru só se houver conta de teste).
- **Sites**: contas, início automático, backup, horário do post, tags e sites padrão, e em "Avançado"
  o modo do navegador por site (invisível / janela escondida / visível).
- **Histórico**: só envios reais, com score e favoritos do Danbooru (atualizados 1x por dia ou no botão).
- **Guia**: passo a passo para a cliente.

A sugestão de tags baixa ~380 MB na primeira vez (modelo `SmilingWolf/wd-vit-tagger-v3`).

## Comandos

```bash
uv run boorubot panel [--no-browser] [--port 8765]   # se já estiver rodando, só abre o navegador
uv run boorubot startup install|remove|status        # início automático com o Windows (.vbs na pasta Inicializar)
uv run boorubot check [site...] [--headed]           # reconhecimento sem credenciais
uv run boorubot login gelbooru                       # abre o navegador; login (e captcha) e feche a janela
uv run boorubot discover gelbooru                    # logado: salva os campos reais do form de upload em runs/
uv run boorubot post queue/exemplo-001               # CLI do técnico: dry-run com o .env (testbooru)
uv run pytest
```

O `.env` (veja `.env.example`) é só para a CLI do técnico. O painel usa apenas as contas cadastradas nele.

## Validação antes de ligar a produção

1. Danbooru no **testbooru** (conta de teste em Sites → Danbooru → "Conta de teste") e "Testar este post".
2. `login` + `discover` em cada site de formulário; conferir as heurísticas de `src/boorubot/sites/forms.py`
   (tags/source/title/rating) contra os campos reais. A URL de upload do rule34.us ainda não foi confirmada.
3. "Testar este post" com a conta real dela → conferir os prints em `runs/` → primeiro envio real.
4. Escolher o horário do post diário num horário em que o PC dela costuma estar ligado.

## Próxima fase (se ela pedir)

Servidor na nuvem (VPS) para não depender do PC ligado e acordado: painel com senha na internet e
navegador remoto para logins/captchas. O guia do painel já explica essa opção para ela.
