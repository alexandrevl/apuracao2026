# Changelog

Mudanças relevantes para quem usa o projeto. Datas no fuso de Brasília.

## [Não lançado]

### Adicionado
- `apuracao watch -s replay --speed 1 [--offset MIN] [--reset-sim]`: refaz a noite gravada no ritmo original (`--speed 1`) ou acelerada, pelo painel ou `--plain`, em `data/replay`. Um relógio virtual começa no primeiro resultado do BR com totalização e solta cada rodada gravada no `captured_at` original; o que veio antes (zerado) entra de uma vez. Em seguida, consulte com `-s replay --cached`.
- `apuracao replay [--from tse] [--to replay] [--reset]`: o mesmo reprocessamento de uma vez, sem rede. Reaplica cada versão publicada nos horários originais, descarta as que retrocedem a totalização (como o `watch`) e recalcula projeção e eventos. O EA14 não é gravado, então o acompanhamento é reconstruído dos resultados; o aviso `sem_atualizacao` não é reproduzido.

### Alterado
- `K` do encolhimento do swing: 0,05 → 0,005. Na noite de 25/10 refeita com as cidades reconstruídas por seção (de 5% a 99% apurado), o erro médio da projeção do Lula contra o oficial final caiu de 0,56 para 0,49 p.p. com as 80 cidades (0,44 → 0,36 com 600 e 0,40 → 0,29 com 1000); o viés continua negativo (−0,49 com 80). Metade do peso do dado agora vem com 0,5% apurado (antes, 5%). A mesma noite escolheu e avaliou o `K`, e o ganho está em 5%–12% apurado; entre 12% e 25% a curva fica um pouco pior e mais volátil.

### Corrigido
- Horários do painel e dos registros (`checado`, `capturado_em`, carimbo dos eventos) agora usam horário de Brasília, o mesmo relógio do TSE, em qualquer fuso da máquina. Registros gravados antes desta mudança guardam a hora local da máquina em `captured_at`.
- O aviso "Sem atualização do TSE" leva a hora em que disparou (antes mostrava a do último dado) e não se repete ao reabrir o `watch` para o mesmo dado parado.
- A chance estimada some (`chance_pct: null`, "dados insuficientes") com menos de 5% das seções apuradas, quando a amostra é pequena e enviesada; também não gera `projecao_definida` nesse ponto.
- A projeção superestimava o candidato que lidera nas primeiras seções de cada UF: aplicava ao país inteiro o swing bruto das UFs mais adiantadas e confiava 100% no dado da UF a partir de 20% apurado. Agora o swing observado é encolhido pelo % apurado e não é emprestado de outras UFs (no histórico de 25/10, o erro ao prever os votos que entraram depois caiu de 4,2 para 2,3 p.p. e o viés pró-Lula, de +3,2 para −0,1 p.p.). Novo campo `projecao.swing_regioes_pp`.
- O candidato A (termômetro, swing, cores, pesquisas) era escolhido pelo `seq` do arquivo do TSE, que é a classificação por votos e não a ordem da urna. Na virada das 19:44:44 de 25/10 o 22 passou a seq 1 e todos esses campos se inverteram para o Flávio. Agora A é sempre o de menor número (13 LULA).
- Projeção passa a separar as 80 maiores cidades (~35% do eleitorado, arquivos municipais oficiais do TSE) do resto de cada UF, cada unidade comparada com o próprio 1º turno. Swing do resto encolhe para 0 com `K = 0,05` (no histórico de 25/10, por UF: RMSE 2,3 p.p. e viés −0,1, contra 2,7/+0,6 do centro regional); cidades encolhem para o swing das cidades da própria UF (com região e país como reserva enquanto a UF tem pouco apurado). Faixa vira ~90% de erros de swing comuns, regionais e por unidade. Falha de um arquivo municipal (404, timeout) não atrasa BR/UF: a cidade fica pendente e é tentada de novo, mesmo sem mudança no acompanhamento; bloqueio do TSE continua parando a rodada. Novos campos `projecao.swing_cidades_pp` e `cidades_usadas`; nova tabela `city_snapshots`; 1º turno das cidades em `baseline/mu/`.

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
