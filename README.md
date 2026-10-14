# post-automation (boorubot)

Publica artes da fila em Danbooru, Gelbooru, rule34.paheal, rule34.us e rule34.xxx.
**Dry-run é o padrão**: nada é enviado sem `--send` + confirmação digitada.

| Site | Como publica | Login |
|---|---|---|
| Danbooru | API oficial (`/uploads.json` → `/posts.json`) | API key no `.env` |
| Gelbooru, paheal, rule34.us | Formulário web via Playwright | `boorubot login <site>` uma vez |
| rule34.xxx | Formulário web via Playwright | idem, mas com captcha no login e Cloudflare (só passa com navegador visível) |

## Painel (uso do dia a dia)

```bash
uv run boorubot panel        # abre http://127.0.0.1:8765 no navegador
```

- **Fila**: arraste a arte → **✨ Sugerir com IA** (tags + rating no padrão Danbooru, WD Tagger local)
  → revise → marque os sites → **Aprovar e agendar** (1 post por dia no horário configurado).
- **Sites**: **Entrar** abre o navegador neste PC para login/captcha (feche a janela ao terminar);
  **Testar** confere se a sessão ainda vale.
- **Histórico**: cada envio por site, com link do post ou o erro.
- **Modo teste** (ligado por padrão, em Sites): preenche tudo e tira print, mas **não envia**.

O painel roda neste PC (o robô precisa de navegador real). Dados em `data/` (banco + imagens, fora do git).
A sugestão de tags baixa ~380 MB na primeira vez (modelo `SmilingWolf/wd-vit-tagger-v3`).

## Setup

```bash
uv sync
uv run playwright install chromium
cp .env.example .env   # preencha
```

## Fila

Cada post é uma pasta em `queue/` com **uma** imagem + `post.json` (veja `queue/exemplo-001`):

```json
{
  "title": "...",
  "tags": ["1girl", "long_hair"],
  "rating": "general | sensitive | questionable | explicit",
  "source": "https://x.com/...",
  "commentary": {"title": "...", "description": "..."},
  "original_character": false,
  "sites": ["danbooru", "gelbooru"],
  "site_tags": {"danbooru": ["commission"]}
}
```

- `original_character: true` → Danbooru ganha a tag `original`; **paheal pula o post** (regra 18 do site).
- `site_tags` → tags extras só para aquele site.

## Comandos

```bash
uv run boorubot check                        # reconhecimento sem credenciais
uv run boorubot check rule34xxx --headed     # rule34.xxx só passa do Cloudflare com navegador visível
uv run boorubot login gelbooru               # abre o navegador; faça login (e captcha) e feche a janela
uv run boorubot discover gelbooru            # logado: salva os campos reais do form de upload em runs/
uv run boorubot post queue/exemplo-001                   # dry-run em todos os sites do post.json
uv run boorubot post queue/exemplo-001 --site danbooru --send   # envio real
uv run pytest
```

Prints, campos de formulário e logs de cada execução ficam em `runs/` (fora do git).
Sessões logadas ficam em `.profiles/` (fora do git, contém cookies).

## Ordem de validação

1. Danbooru no **testbooru** (`DANBOORU_URL=https://testbooru.donmai.us`) com `--send`.
2. `login` + `discover` em cada site de formulário e conferir se as heurísticas de
   `src/boorubot/sites/forms.py` (tags/source/title/rating) bateram com os campos reais.
3. Dry-run com a conta real → conferir os prints em `runs/` → primeiro `--send`.
