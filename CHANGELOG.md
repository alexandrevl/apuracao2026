# Changelog

Mudanças relevantes para quem usa o projeto. Datas no fuso de Brasília.

## [0.1.0] — 2026-10-25

Primeira versão pública, para acompanhar o 2º turno presidencial de 25/10/2026.

### Acompanhamento
- Coleta dos arquivos públicos do TSE (acompanhamento EA14 e resultado EA20) para Brasil, 27 UFs e exterior, com ETag/304, cadência limitada e espera em caso de bloqueio.
- Histórico em SQLite e JSONs originais por fonte (`tse`, `sim`, `t1`); reabrir o `watch` continua do estado atual.
- Painel em tela cheia com cinco abas (painel, gráficos, pesquisas, UFs, regiões), temas selecionáveis e salvos, e modo simples (`--plain`) para terminal ou log.

### Análises
- Projeção própria por UF com referência do 1º turno, swing e faixa de cenários; ritmo e ETA da totalização.
- Termômetro do Lula (precisa × projeção × votos recentes) e chance estimada, sempre identificada como palpite não calibrado.
- Agregados regionais calculados por somas, com aviso quando as UFs não batem com o arquivo nacional.
- Eventos com histórico rolável: viradas, marcos, UFs finalizadas, indicação e retirada pelo modelo, definição matemática e apuração encerrada.

### Pesquisas
- Lista das pesquisas nacionais conferidas (rodada mais recente de cada instituto, até 24/10), com fonte, registro no TSE e margem; uma lista pessoal no diretório de dados a substitui.

### Para agentes e desenvolvimento
- Consultas únicas com JSON (`snapshot`, `ufs`, `history`, `events`, `pesquisas`, `grafico`), `--cached` sem acesso à rede, `AGENTS.md` e skill portátil.
- Simulador de noite de apuração no formato do TSE; testes, lint (ruff), hook de pre-push e GitHub Actions em Linux e macOS.

[0.1.0]: https://github.com/alexandrevl/apuracao2026/releases/tag/v0.1.0
