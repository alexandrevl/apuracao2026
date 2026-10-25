---
name: apuracao-eleicoes-2026
description: Interpretar dados oficiais, projeções, termômetro, UFs, regiões e pesquisas da apuração presidencial de 2026 com o comando local apuracao, sem sobrecarregar o TSE.
---

# Apuração 2026

Use esta skill com o projeto [apuracao2026](https://github.com/alexandrevl/apuracao2026).
Ela é um arquivo de instruções, não um servidor MCP nem um acesso a notícias.

## Preparação

- O usuário precisa ter instalado o projeto e coletado dados. Confira os comandos e o contrato JSON em `AGENTS.md` no checkout.
- O executável é `apuracao`. Se ele não estiver no PATH, use `uv run apuracao` dentro do checkout.
- `APURACAO_DATA` seleciona outro diretório de dados. Não presuma caminhos da máquina do autor.
- Fontes são separadas: `tse` (padrão), `sim` e `t1`. Não interprete simulação como resultado real.

## Consultas

Enquanto existir `apuracao watch` coletando, leia apenas o histórico local:

```bash
apuracao snapshot --cached --json
apuracao history --json --since 19:30
apuracao events --json --limit 30
apuracao pesquisas --json
```

Para ensaio, acrescente `-s sim`. Os comandos acima não precisam acessar notícias nem criar um coletor paralelo.

Sem coletor rodando, uma chamada a `apuracao snapshot --json` consulta o TSE. Não coloque esse comando em laço; aguarde pelo menos 30 segundos entre consultas completas. Nunca descubra arquivos por tentativa de URL: erros 404 e excesso de requisições podem bloquear o IP.

## Interpretação

1. Confira `fonte`, `erro`, horário da totalização e porcentagem apurada antes de comentar resultados.
2. Separe os números oficiais (`candidatos`, `secoes`, `eleito`) das estimativas (`projecao`, `termometro`, votos restantes regionais). A projeção não é divulgação oficial do TSE.
3. A chance estimada é um palpite não calibrado do modelo; sua faixa não é intervalo de confiança. Indicação de vitória do modelo pode ser retirada.
4. O termômetro se refere ao candidato A na ordem oficial (nesta eleição, 13 LULA). Compare o necessário no restante, o projetado e os votos novos recentes. Necessidade negativa significa meta estimada alcançada; maior que 100% significa inviabilidade só naquele cenário. Só números oficiais podem fundamentar definição matemática.
5. `swing_pp` positivo favorece o candidato A em relação ao 1º turno entre os finalistas. Comparecimento parcial cobre somente as seções totalizadas.
6. As regiões são somas das UFs. Confira `regional.consistencia` antes de relacionar seus saldos à vantagem nacional. Volume restante, por si só, não favorece candidato algum.
7. Pesquisas só têm comparação definitiva quando `referencia == "final"`; antes disso, o ranking usa a estimativa do modelo. Verifique data, fonte e se os percentuais são de votos válidos. `pesquisas.lista` diz se a lista ativa é a do projeto ou a pessoal do usuário; entradas com `fonte_confirmada: false` só foram vistas em fonte secundária — avise isso ao citá-las.
8. Use o ritmo e a ETA como extrapolações da janela recente, não como horário prometido de encerramento. Reprocessamentos e UFs lentas podem alterar o ritmo.
9. Para notícias, faça pesquisa externa separada e cite as fontes. Não invente contexto a partir do placar.

## Resposta sugerida

Placar oficial e atualização → projeção com ressalva → termômetro → UFs/regiões que mais pesam → ritmo/ETA → eventos → pesquisas. Seja igualmente rigoroso para ambos os candidatos, independentemente da preferência política do usuário.
