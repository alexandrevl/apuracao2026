# Apuração 2026 — guia para agentes

Sistema pessoal de terminal que acompanha a totalização do **2º turno presidencial de 25/10/2026** direto dos arquivos públicos do TSE, grava o histórico e expõe JSON estável para interpretação.

Comando: `apuracao` (via `uv tool install -e .`; dentro do checkout também funciona `uv run apuracao`). Licença MIT. Repositório: https://github.com/alexandrevl/apuracao2026.

## Regras para agentes

- **Enquanto o usuário roda `apuracao watch`, use `--cached`** (lê o SQLite, zero requisições ao TSE). `--cached`, `pesquisas`, `grafico`, `history` e `events` nunca criam cliente HTTP nem baixam a base; sem base do 1º turno, mostram só os números oficiais, sem estimativas.
- Sem `watch` rodando, `apuracao snapshot --json` faz **uma** consulta completa (~30 arquivos, ~3 s). Não chame em laço; intervalo mínimo de 30 s entre chamadas.
- Nunca monte URLs do TSE à mão nem tente variações: 404 repetido e mais de 100 req/s por IP bloqueiam o IP por 10 min.
- Projeção é **estimativa própria** (campo `projecao.aviso`). Resultado oficial: `candidatos`, `secoes`, `matematicamente_definido`, `eleito`.
- Notícias e contexto externo: busque na web; o sistema só cuida dos dados do TSE.
- Fontes: `tse` (ao vivo, padrão), `sim` (simulação), `t1` (1º turno real). Cada fonte tem banco próprio; não misture. `watch -s sim` recusa iniciar sobre histórico simulado existente; `--reset-sim` apaga explicitamente só esse histórico.

## Comandos

| Comando | Uso |
|---|---|
| `apuracao snapshot --json [--cached] [--uf sp]` | Estado completo agora (Brasil ou uma UF) |
| `apuracao ufs --json [--cached]` | Lista das 28 abrangências (27 UFs + `zz` exterior) |
| `apuracao history --json [--uf br\|sp] [--since 19:30]` | Uma linha por versão publicada pelo TSE |
| `apuracao events --json [--since 19:30] [--limit 20]` | Viradas, marcos, UFs finalizadas, definição |
| `apuracao baseline --json` | 1º turno final por UF (base da projeção) |
| `apuracao pesquisas --json [--uf sp]` | Pesquisas × apuração (sempre lê o histórico local) |
| `apuracao grafico` | Gráficos da noite no terminal (timeline, termômetro, chance, vantagem, swing, régua das pesquisas); lê o histórico local |
| `apuracao watch [--plain] [--interval 5]` | Ao vivo (humano). `--plain` com stdout redirecionado gera log de 1 linha por mudança |
| `apuracao watch -s sim --speed 60` | Ensaio com noite simulada |

`--since` aceita `HH:MM` (hora de totalização do TSE, no dia da última totalização gravada) ou ISO completo.

## Contrato do `snapshot --json` (Brasil)

```jsonc
{
  "fonte": "tse", "eleicao": "6258", "capturado_em": "2026-10-25T19:42:15", "erro": null,   // capturado_em em horário de Brasília, o mesmo relógio de totalizado_em
  "abrangencia": "br",
  "andamento": "p",                       // n não iniciada · p parcial · f finalizada
  "tse": { "idg": "…", "gerado_em": "…", "totalizado_em": "…", "matematicamente_definido": "e|s|n|null" },
  "secoes": { "total": 499248, "totalizadas": 250917, "pct": 50.26 },
  "eleitorado": { "total": …, "nao_totalizado": …, "comparecimento": …, "abstencao": …, "pct_comparecimento": … },
  "votos": { "validos": …, "brancos": …, "nulos": …, "total": … },
  "candidatos": [ { "numero": "22", "nome": "FLAVIO BOLSONARO", "partido": "PL", "votos": …, "pct": 51.5, "eleito": false } ],
  "lider": { "numero": "22", "nome": "…", "vantagem_votos": …, "vantagem_pp": … },
  "projecao": {
    "aviso": "Estimativa própria…",
    "candidatos": [ { "numero": "13", "nome": "LULA", "pct": 48.4, "min": 48.3, "max": 48.5, "votos": … } ],
    "swing_nacional_pp": -0.38,           // candidato A (1º na ordem oficial = 13 LULA) vs 1º turno
    "validos_projetados": …, "meta_votos": …, "falta_para_meta": { "13": …, "22": … },
    "meta_metodo_2022": …
  },
  "matematicamente_definido": { "definido": false, "vencedor": null, "vantagem": …, "eleitorado_restante": … },
  "ritmo": { "secoes_por_min": 3220.6, "janela_min": 5.2, "secoes_restantes": …, "eta_min": 47.2, "eta": "…" },
  "ufs": [ { "uf": "sp", "andamento": "p", "pct_secoes": 55.8, "votos": {"13": …, "22": …}, "pct": {…},
             "lider": "22", "share_1t_candidato_a": 42.38, "swing_pp": -0.63,
             "pct_comparecimento": 76.4, "pct_comparecimento_1t": 77.46 } ],
  "regional": {
    "regioes": [ { "regiao": "Sudeste", "ufs": ["es", "mg", "rj", "sp"],
      "dados_completos": true, "ufs_sem_resultado": [], "ufs_finalizadas": 0,
      "andamento": "p", "secoes": {"total": …, "totalizadas": …, "pct": …},
      "eleitorado": {"total": …, "nao_totalizado": …, "comparecimento": …, "abstencao": …, "pct_comparecimento": …},
      "votos_validos": …, "votos": {"13": …, "22": …}, "pct": {"13": …, "22": …}, "lider": "22",
      "saldo_candidato_a_votos": …, "share_1t_candidato_a": …, "swing_pp": …,
      "pct_comparecimento_1t": …, "validos_restantes_estimados": …, "ufs_restante_base_1t": [] } ],
    "consistencia": {"status": "sincronizado|defasado|incompleto|sem_dados", "aviso": "…",
      "diferenca_validos_ufs_menos_br": …, "diferenca_secoes_ufs_menos_br": …,
      "diferenca_votos_ufs_menos_br": {"13": …, "22": …},
      "ufs_sem_resultado": [], "ufs_divergentes_acompanhamento": []}, "aviso": "…"
  },
  "eventos_recentes": [ { "captured_at": "…", "totalizado_em": "…", "type": "virada", "abr": "br", "message": "…", "payload": {} } ],
  "pesquisas": { "erro": null, "nota": "…", "comparacoes": [
    { "instituto": "Datafolha", "abr": "br", "campo_fim": "2026-10-22", "registro_tse": "BR-…", "margem_pp": 2.0,
      "validos": {"13": 50.0, "22": 50.0}, "validos_calculado": false, "fonte": "https://…",
      "projecao_a": 48.4, "oficial_a": 47.9, "pct_secoes": 62.1,
      "referencia": "projecao",          // "final" quando a abrangência tem and=f; "sem_dados"
      "diferenca_pp": 1.6,               // pesquisa − referência, % válidos de 13 LULA
      "dentro_margem": true } ] },
  "termometro": {                        // sempre do ponto de vista de 13 LULA
    "necessario_pct_restante": 52.2,     // >100: inviável no cenário estimado, não definição matemática oficial
    "projecao_pct_restante": 48.3,       // quanto a projeção dá a ele no restante
    "gap_pp": 3.9,                       // necessário − projeção (positivo = falta ritmo)
    "recente_pct": 48.8, "recente_janela_min": 5.0,   // % dele nos votos que entraram na janela recente
    "validos_restantes": …, "chance_pct": 6.1, "rotulo": "difícil", "definido": false, "aviso": "…" }  // chance_pct null + rotulo "dados insuficientes" com <5% das seções (sem definição oficial)
}
```

Com `--uf sp`: mesmo bloco oficial da UF + `comparacao_1t`. Sem dados ainda: `"disponivel": false`. Código de saída 2 quando não há resultado.

Percentuais `pct` são % dos votos válidos com 9 casas (campo `pvapn` do TSE). `swing_pp` e `share_1t_candidato_a` referem-se ao **candidato A = 13 LULA** (fração entre os dois finalistas; demais candidatos do 1º turno redistribuídos proporcionalmente). Swing positivo = Lula melhor que no 1º turno naquela UF.

## Regiões

Aba `5`: Nordeste, Sudeste, Centro-Oeste, Norte, Sul e Exterior. `o` alterna entre ordem regional, votos restantes estimados, eleitorado, apurado e saldo absoluto, sem alterar a ordenação da aba UFs. Dados no bloco `regional` do snapshot nacional.

Percentuais regionais são razões das somas de votos/seções/comparecimento, nunca médias de percentuais estaduais. O saldo `saldo_candidato_a_votos` é Lula − Flávio; positivo favorece Lula, negativo favorece Flávio. O swing compara a fração regional entre os finalistas com a fração regional ponderada pelos votos do 1º turno. Durante a parcial também sofre viés da ordem das UFs.

`validos_restantes_estimados` reutiliza o cálculo da projeção nacional. Onde não há arquivo de resultado da UF, usa a base do 1º turno, identificado em `ufs_restante_base_1t`. Maior volume restante não significa que um candidato será beneficiado.

Não confunda agregados regionais com um resultado regional oficial do TSE. `consistencia` compara as somas estaduais com BR: diferenças positivas significam soma das UFs maior que BR. Com cobertura incompleta, percentuais/saldo regionais ficam `null` e os totais disponíveis não representam toda a região. Não reescale as UFs para forçar coincidência com BR. O placar nacional segue sempre o arquivo BR.

## Pesquisas

Lista do projeto: `src/apuracao/pesquisas.json` (rodada mais recente de cada instituto nacional, conferida à mão, com link da fonte). Se existir `pesquisas.json` no diretório de dados, ela **substitui** a do projeto; o JSON informa qual está ativa em `pesquisas.lista` (`"projeto"` ou `"pessoal"`). O arquivo ativo é relido automaticamente. Campos obrigatórios: `instituto`, `abr` (`br` ou UF), `validos` (`{"13": x, "22": y}` somando ~100). Opcionais: `contratante`, `divulgacao`, `campo_inicio`, `campo_fim`, `registro_tse`, `amostra`, `margem_pp`, `validos_calculado`, `totais`, `fonte`, `fonte_confirmada` (padrão `true`; `false` = números só vistos em fonte secundária, marcado com † no painel), `obs`. Ordenação: por abrangência, menor `|diferenca_pp|` primeiro.

Ao interpretar: o placar parcial (`oficial_a`) é enviesado pela ordem de chegada das UFs; use `diferenca_pp` (contra a projeção) como indicação e só trate como acerto/erro definitivo quando `referencia == "final"`. Pesquisas estaduais viram definitivas assim que a UF finaliza, bem antes do Brasil.

## Como a projeção funciona

Por UF: peso do dado atual `w = min(1, apurado/20%)`; fração do restante = `w·atual + (1−w)·(1º turno + swing nacional)`; votos restantes = eleitorado não totalizado × comparecimento × taxa de válidos (atual misturada com 1º turno pelo mesmo `w`). Soma Brasil. Faixa `min/max` = cenário de ±2 p.p. no prior e ±0,5 p.p. no observado; **não é intervalo de confiança**. Antes de qualquer voto, a projeção é só o 1º turno redistribuído.

"Matematicamente definido" usa só números oficiais: vantagem do líder > eleitorado ainda não totalizado.

**Termômetro e chance.** `necessario`/`projecao` usam votos válidos restantes estimados (projeção − oficial); se as UFs estiverem defasadas em relação ao arquivo BR, ficam `null` (nunca viram resultado). `definido` só com `andamento == "f"` ou definição matemática oficial. `chance_pct` = P(% final do Lula > 50) numa normal centrada na projeção, σ = maior entre a faixa (lida como ~90%) e 2,5 p.p. × fração do eleitorado não totalizado. Palpite do modelo, não probabilidade calibrada — sempre apresentar com esse aviso. Rótulos: <1 muito improvável · <10 difícil · <30 possível, mas atrás · <70 aberto · <90 favorável · <99 muito favorável · senão praticamente garantido.

**Limites do termômetro.** Necessidade bruta negativa indica meta estimada já alcançada, não votos negativos nem vitória oficial; o JSON preserva esse diagnóstico enquanto não houver definição. Acima de 100%, a inviabilidade vale apenas no cenário estimado. Após definição matemática ou `andamento=f`, `necessario_pct_restante`, `projecao_pct_restante` e `gap_pp` ficam `null`. Se o share projetado do restante sair de 0–100% por incompatibilidade entre arquivos, esses campos também ficam `null` (não são artificialmente corrigidos para um percentual válido).

O gráfico mostra a necessidade no intervalo visual 0–100%, sem alterar a conta bruta. Usa os snapshots oficiais históricos para interromper a série na definição/finalização, e não une a curva por trechos de estimativa incompatível. A série histórica da chance usa o eleitorado oficial restante e só recebe 0/100 definitivos quando os números oficiais permitem.

## Tipos de evento

`lideranca` (primeiros votos), `virada`, `virada_projecao`, `marco` (1/10/25/50/75/90/95/99/100%), `uf_finalizada`, `definido`, `tse_md` (TSE sinalizou `md`), `eleito`, `sem_atualizacao` (10 min sem mudança em andamento; carimbado com a hora em que disparou, traz `payload.ultimo_totalizado_em` e não se repete para o mesmo dado parado ao reabrir o `watch`).

`projecao_definida`: modelo indica um vencedor ao atingir chance estimada ≥99% (para qualquer candidato), com votos apurados e restante estimado disponível. É uma estimativa não calibrada, não definição matemática. `projecao_retirada`: retira essa indicação se a chance cair abaixo do limiar, mudar o candidato ou o restante estimado ficar indisponível, antes da definição oficial. Ao abrir o watch, o detector memoriza a situação sem repetir indicações antigas.

`apuracao_finalizada`: transição nacional para `and=f`, independente dos eventos `marco` de 100%, `definido` (vantagem oficial > eleitorado restante) e `eleito` (sinalização do TSE). Todos são gravados no SQLite; a área de eventos no painel tem rolagem pelo histórico completo. Acompanha o fim automaticamente só quando o usuário já está no fim.

## Dados locais

Diretório de dados, em ordem de prioridade: `APURACAO_DATA` (aceita `~`) → `data/` do checkout editável → `$XDG_DATA_HOME/apuracao` (se absoluto) → `~/.local/share/apuracao`. Os exemplos abaixo usam `data/`.

```
data/
  baseline/<abr>.json        1º turno final (imutável)
  templates/                 moldes reais do 2º turno para a simulação
  tse/apuracao.db            histórico ao vivo   · tse/raw/<abr>/<idg>.json  cada versão do TSE
  sim/ …                     simulação (apagada só com `watch -s sim --reset-sim`)
  t1/ …                      leituras do 1º turno
  preferences.json           tema escolhido no painel
  pesquisas.json             lista pessoal de pesquisas; se existir, substitui src/apuracao/pesquisas.json
```

Cada JSON bruto é escrito de forma atômica antes do commit no SQLite, então o banco nunca referencia um arquivo incompleto. Fechar o `watch` não recupera as versões publicadas enquanto ele estava fechado.

Tabelas: `snapshots` (uma linha por versão por abrangência), `candidate_votes`, `projections`, `events`, `baseline_t1`; view `v_votos` (snapshot + candidato achatado).

Consultas úteis (`sqlite3 data/tse/apuracao.db`):

```sql
-- Evolução nacional
select totalizado_em, pct_secoes, numero, nome, pct from v_votos where abr = 'br' order by totalizado_em;

-- Última situação por UF
select abr, pct_secoes, numero, pct from v_votos v
where totalizado_em = (select max(totalizado_em) from snapshots s where s.abr = v.abr) and abr != 'br';

-- Como a projeção evoluiu
select totalizado_em, pct_secoes_br, numero, round(pct,2), round(pct_min,2), round(pct_max,2), round(swing_pp,2)
from projections order by id;

-- Swing por UF vs 1º turno (Lula entre os finalistas)
with atual as (
  select abr, sum(case when numero='13' then votos end)*1.0/sum(votos) s from v_votos v
  where totalizado_em = (select max(totalizado_em) from snapshots x where x.abr = v.abr) and abr != 'br'
  group by abr),
t1 as (select abr, sum(case when numero='13' then votos end)*1.0/sum(case when numero in ('13','22') then votos end) s
       from baseline_t1 group by abr)
select a.abr, round(100*(a.s - t1.s), 2) swing_pp from atual a join t1 using (abr) order by swing_pp desc;
```

## Fonte oficial

- Base: `https://resultados.tse.jus.br/oficial/ele2026` · eleição 2º turno `6258` (1º turno `6257`) · cargo `0001`.
- Acompanhamento: `6258/dados/br/br-e006258-ab.json` (EA14). Resultado: `6258/dados/<abr>/<abr>-c0001-e006258-u.json` (EA20).
- Documentação: https://www.tse.jus.br/eleicoes/informacoes-tecnicas-sobre-a-divulgacao-de-resultados (aba Documentos).

## Desenvolvimento

`uv run pytest` · código em `src/apuracao/` (`sources` → `parse` → `store` → `analysis`/`events` → `tracker` → `render`/`tui`/`cli`; `sim` gera arquivos no formato do TSE).
