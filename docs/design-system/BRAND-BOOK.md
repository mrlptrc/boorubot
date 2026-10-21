O boorubot é o painel onde a artista sobe as artes dela, confere as tags que a IA sugeriu e deixa o robô publicar uma por dia no Danbooru, paheal e rule34.xxx. O visual é kawaii e acolhedor; a informação é clara e honesta. Use este sistema para qualquer tela, e-mail ou material do boorubot.

## Princípios

1. **Fofa, mas clara.** O visual é kawaii, a informação não. Status, datas e erros sempre em texto legível, nunca só em cor ou ícone.
2. **Calma no ritmo.** Um post por dia, sem pressa. Telas espaçadas, poucos alertas, animações curtas. Nada de vermelho piscando.
3. **Ela decide.** A IA sugere, a artista aprova. Sugestões aparecem tracejadas até ela confirmar; avisos explicam e nunca bloqueiam ("repostar é decisão dela").

## Por que estas escolhas

Quem usa o boorubot é uma artista que posta fan art e personagens próprios em comunidades de anime. É uma pessoa visual, que passa horas no painel e confia a ele a própria conta nos sites. O sistema precisa parecer feito para ela e, ao mesmo tempo, transmitir segurança.

- **Rosa e lilás em vez de azul corporativo.** O universo dela é anime e kawaii; um painel rosa parece um ateliê, não um sistema de TI. O rosa principal (`primary`) foi escurecido em `primary-strong` para que o botão com texto branco passe no contraste AA.
- **Lilás separa a IA do resto.** Tudo que a máquina sugere ou que é padrão dela fica em lilás; o que ela decide fica em rosa. Dá para ver de relance quem fez o quê.
- **Neutros ameixa em vez de cinza.** Cinza puro deixa o rosa com cara de doce barato. Neutros puxados para o ameixa (`ink`, `ink-soft`, `line`) mantêm a paleta quente e dão um texto bem escuro (14:1) para ler sem cansar.
- **Quicksand nos títulos.** Letras redondas e geométricas, amigáveis sem ser infantis. **Nunito no texto:** também arredondada, conversa com a Quicksand, mas tem mais peso e altura de x, então lê bem em 13–15px e em telas de celular.
- **Base de 4px e cantos grandes.** Espaços generosos acompanham o "calma no ritmo"; cantos de 12 a 24px e botões em pílula repetem a forma redonda da Mochi.
- **Sombras ameixa e quase nenhuma.** Cards ficam no plano, com borda; só o que flutua (menu, modal, toast) ganha sombra. A tela fica limpa e a hierarquia fica óbvia.
- **A Mochi como voz.** Um mascote transforma "a IA analisou a imagem" em "A Mochi já olhou", e erros em algo que não assusta. Ela aparece onde há emoção (vazio, sucesso, erro, espera) e some onde há trabalho.

## Voz e texto

- Português do Brasil, frases curtas, "você". A Mochi fala em primeira pessoa ("Achei 18 tags…").
- Diga a consequência, não o mecanismo: "vai para a fila de moderação (até 3 dias)", não "status: pending".
- Botões são verbos com objeto: "Aprovar e agendar", "Testar sem publicar", "Apagar arte". Nunca "OK", "Sim" ou "Enviar".
- Erros dizem o que houve e o que fazer: "pediu login de novo · entrar". Sem pedir desculpas, sem culpar.
- Sucesso no passado e com o dado: "Feito! Sua conta está conectada como Member."
- Status em minúsculas: rascunho, agendado, publicado, falhou.
- Datas curtas e humanas: "seg 19/10 às 18:00", "ontem 18:02".
- Sem emoji na interface. Brilho e fofura vêm da Mochi e da forma, não de 🌸.
- Nomes de tags como o site escreve, com sublinhado: `pink_hair`.

## Cor

- Fundo da página `paper`; cards, campos e modais `surface`; bordas `line`.
- Texto principal `ink`; descrições `ink-soft`; `ink-faint` só em placeholder e ícone inativo.
- Botão principal `primary-strong` com `on-primary`. `primary` fica para toggle, ícone ativo e gráficos: texto branco sobre ele não passa AA.
- Links e texto rosa em `primary-deep`.
- IA e tags padrão dela: `lilac-100` com texto `lilac-700` e ponto `lilac-500`.
- Status em pares texto/fundo: `ok`/`ok-bg`, `warn`/`warn-bg`, `danger`/`danger-bg`, `lilac-700`/`lilac-100`. Sempre com palavra; "falhou" usa X.
- `pink-300`, `pink-400`, `lilac-300` e `sparkle` são só para ilustração e gráfico, nunca para texto.
- Não existe tema escuro ainda. Se for criado, entra como tema novo neste arquivo, com os mesmos nomes.

## Tipografia

- Títulos em Quicksand 700 (`display`, `title-1`, `title-2`, `title-3`); texto em Nunito (`body`, `body-strong`, `small`). Ambas no Google Fonts.
- Um `display` por tela: o nome da arte no editor, a saudação no Início.
- `eyebrow` em maiúsculas com espaçamento 0,08em, só para rotular grupos ("PARA COMEÇAR").
- Números de Métricas em `number-xl` com algarismos tabulares.
- Linhas de texto com até 65 caracteres; títulos com `text-wrap: balance`.

## Espaço, forma e camadas

- Tudo em múltiplos de 4px (`space-1` a `space-16`). Padding de card `space-6`, entre seções `space-10`, margem lateral no celular `space-4`.
- Desktop: sidebar de `sidebar-width` e conteúdo até `content-max`, grade de 12 colunas com gutter `space-6`. Abaixo de `breakpoint-mobile`: uma coluna e barra fixa embaixo.
- Raios: campos e opções `radius-input`, cards de lista e menus `radius-md`, seções e modais `radius-lg`, botões, tags e chips `radius-full`.
- Sombras: cards planos com borda; `shadow-md` em menus, `shadow-lg` em modais e toasts; `shadow-focus` em todo foco de teclado.
- Alvo de toque mínimo `touch-min` (44px).

## Movimento

- Curto e macio: 120 ms para hover e cor, 200 ms para abrir menu e modal, curva `cubic-bezier(.2, .8, .2, 1)`.
- Toast entra subindo 8px. A Mochi pode dar um pulinho de 4px ao comemorar, uma vez.
- Respeite `prefers-reduced-motion`: sem deslocamento, só troca de opacidade.

## Ícones

- Ícones de traço, 20px na navegação e 18px em botões, traço 1,75px, pontas e cantos arredondados, cor `currentColor`. Estilo compatível com Lucide; use o Lucide quando faltar algum.
- Ícone nunca anda sozinho em ação importante: sempre com rótulo ou `aria-label`.
- Pontos coloridos de 7px marcam origem de tag e status, sempre com legenda.

## Mochi

- Seis arquivos em Assets › Mochi: a marca e cinco expressões (feliz, comemorando, pensando, dormindo, ops).
- Feliz no logo e em "A Mochi já olhou"; pensando enquanto a IA roda; comemorando ao publicar ou conectar; dormindo em fila vazia e dia de descanso; ops em erro.
- Use em estados vazios, na fala da IA e em avisos importantes. Nunca em botões, campos ou tabelas, e no máximo uma por tela.
- Não recolora, não distorça, não coloque texto sobre ela. Sempre sobre fundo claro.

## Acessibilidade

- Todo texto passa 4,5:1 no fundo onde é usado (a nota de cada token diz qual). `ink-faint` (3,7:1) só para placeholder.
- Cor nunca é a única pista: tags têm legenda, status têm palavra, "falhou" tem X.
- Foco visível com `shadow-focus` em todo controle; ordem de tab segue a leitura.
- Toggle e rating usam `role="switch"` e `role="radio"`; o X da tag tem `aria-label` "Remover <tag>".

## Onde está

- Figma: boorubot · Design System — https://www.figma.com/design/CamE3nE836K1UhfPewZNE4 (páginas Fundamentos, Componentes, Telas v2; variáveis Primitivos/Cores/Espaço/Raio, 12 estilos de texto, 4 de sombra).
- Fundamentos inclui a Turma (ursinho = Métricas, coelhinha = Calendário, cachorrinho = Sites, pintinho = Guia, Mochi deitada = vazio, patinha = Artes).
- Telas v2 no Figma: Editor, Início, Nova arte, Artes, Sites, Sites com painel do Danbooru, Ajustes, Calendário, Métricas, Guia, Modais e avisos. Faltam Celular Início e Celular Editor.
