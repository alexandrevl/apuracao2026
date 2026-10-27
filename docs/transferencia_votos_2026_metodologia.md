# Metodologia y reproducción — flujos de votos presidenciales de Brasil, 2026

> Documento para revisión y comparación independiente por otro modelo de IA.
> La explicación detallada está en español; al final se incluyen resúmenes equivalentes en inglés y alemán.
> “Transferencia” y “flujo” en este documento describen una asignación estadística entre grupos agregados, salvo cuando se dice explícitamente “resultado oficial”. No hay datos que permitan rastrear el voto de una persona.

## 1. Qué se construyó

El artefacto es un informe HTML autónomo con gráficos SVG construidos en el navegador, tablas, datos embebidos y selectores de idioma (español, inglés y alemán):

- [Informe HTML](./transferencia_votos_2026.html)
- [Generador Python](../tools/generate_vote_transfer_report.py)

El generador consulta los JSON oficiales del Tribunal Superior Electoral de Brasil (TSE), valida que las 29 abrangencias (Brasil, 27 UFs y exterior) de cada turno estén finalizadas y crea el HTML con los resultados y las estimaciones calculadas.

El informe contiene:

1. Un Sankey de los votos de las candidaturas eliminadas en la primera vuelta, repartidos entre los dos finalistas mediante una regresión ecológica.
2. Un Sankey de todos los votos válidos: las 12 candidaturas de la primera vuelta más un flujo separado para el incremento neto de votos válidos, con los resultados oficiales de segunda vuelta como destinos.
3. Una comparación de la estimación del modelo con los porcentajes oficiales de Lula en 27 estados y el exterior.
4. Una explicación explícita de que el modelo no identifica decisiones individuales.
5. Un ejemplo aritmético que muestra que los totales también son compatibles con un escenario sin cambio directo entre los votantes iniciales de los dos finalistas.

No utiliza una encuesta individual, una muestra de electores enlazada entre turnos, datos de urna identificables ni resultados de sondeos como flujo observado.

## 2. Fuentes y datos de entrada

La fuente es el conjunto público de resultados por archivos JSON del TSE, servido bajo `https://resultados.tse.jus.br/oficial/ele2026`.

| Dato | ID de elección | Archivo nacional |
|---|---:|---|
| Presidente, primera vuelta | `6257` | [`br-c0001-e006257-u.json`](https://resultados.tse.jus.br/oficial/ele2026/6257/dados/br/br-c0001-e006257-u.json) |
| Presidente, segunda vuelta | `6258` | [`br-c0001-e006258-u.json`](https://resultados.tse.jus.br/oficial/ele2026/6258/dados/br/br-c0001-e006258-u.json) |

El generador también descarga el archivo equivalente de Brasil (`br`), cada una de las 27 UFs y el exterior (`zz`) para ambos turnos: 29 abrangencias por turno, **58 archivos en total**. Para el modelo geográfico se excluye la fila nacional `br`, por lo que se usan 28 observaciones (27 UFs y `zz`). Los códigos y la construcción validada de rutas se importan de [sources.py](../src/apuracao/sources.py); los objetos oficiales se interpretan con [parse.py](../src/apuracao/parse.py).

La versión del HTML descrita aquí tiene `generatedAt = 2026-10-26T14:20:58.907814+00:00` (UTC). Una regeneración posterior marcará un nuevo momento y podría incorporar cambios en las fuentes.

Para cada descarga, el programa comprueba que:

- el ID de elección coincide con el turno esperado;
- la abrangencia devuelta coincide con la solicitada;
- el resultado está marcado como final (`and == "f"`).

Si falla una descarga o una comprobación, la generación se detiene en vez de producir un informe parcial con aspecto de resultado completo. Las solicitudes están separadas por 0,25 segundos.

### Totales nacionales de la versión documentada

El HTML generado contiene los siguientes totales TSE:

| Indicador | Primera vuelta | Segunda vuelta | Diferencia |
|---|---:|---:|---:|
| Votos válidos | 119.300.788 | 124.082.273 | **+4.781.485 (+4,01 %)** |
| Votos totales | 125.275.835 | 128.236.074 | +2.960.239 |
| Votos blancos | 2.300.798 | 1.430.496 | −870.302 |
| Votos nulos | 3.674.249 | 2.723.305 | −950.944 |

La diferencia entre votos válidos es un **cambio neto de totales**, no un recuento de personas nuevas que votaron. No identifica movimientos brutos de participación, abstenciones ni el posible paso de votos blancos o nulos a válidos.

### Resultados oficiales de las candidaturas finalistas

| Candidatura | 1.ª vuelta | % válido 1.ª vuelta | 2.ª vuelta | % válido 2.ª vuelta |
|---|---:|---:|---:|---:|
| Flávio Bolsonaro | 56.104.503 | 47,027772356 % | 59.826.041 | 48,214817116 % |
| Lula | 53.879.538 | 45,162767911 % | 64.256.232 | 51,785182884 % |

Los otros diez candidatos sumaron **9.316.747 votos válidos** en la primera vuelta.

## 3. Por qué hay dos gráficos Sankey

### Gráfico 1: solo candidaturas eliminadas

El primer gráfico responde a la pregunta acotada “¿cómo podrían haberse repartido los votos de los candidatos que no llegaron a la segunda vuelta?”. Por eso omite a Lula y Flávio como fuentes: sus votos no son votos de candidatos eliminados.

Los coeficientes provienen del modelo estatal sin la calibración nacional del segundo gráfico. Para cada candidato eliminado, el valor estimado se calcula como:

```text
votos nacionales de primera vuelta del candidato
× coeficiente ecológico estimado para votar por Lula
```

El resto se asigna a Flávio para que las dos partes sumen el total original de esa candidatura. Los destinos de este gráfico son **aportes estimados únicamente de las candidaturas eliminadas**. No deben confundirse con los totales oficiales de los finalistas ni con una reconstrucción completa de la segunda vuelta.

Los flujos enteros que aparecen en ese primer gráfico en la versión documentada son:

| Candidatura eliminada | 1.ª vuelta | Modelo → Lula | Modelo → Flávio |
|---|---:|---:|---:|
| Escritor Augusto Cury | 3.448.569 | 1.763.284 | 1.685.285 |
| Renan Santos | 2.675.887 | 1.350.714 | 1.325.173 |
| Ronaldo Caiado | 2.605.148 | 1.286.258 | 1.318.890 |
| Zema | 326.488 | 168.251 | 158.237 |
| Samara | 122.911 | 63.560 | 59.351 |
| Hertz Dias | 43.103 | 22.315 | 20.788 |
| Clariana Barão | 40.043 | 20.727 | 19.316 |
| Edmilson Costa | 22.693 | 11.750 | 10.943 |
| Veterinário Wilson Grassi | 16.881 | 8.741 | 8.140 |
| Rui Costa Pimenta | 15.024 | 7.779 | 7.245 |

### Gráfico 2: todos los votos válidos, en una escala común

Este gráfico tiene un propósito distinto: compara el volumen de votos válidos entre turnos y deja visibles las dos candidaturas finalistas en la primera vuelta. Incluye:

- una fuente para cada una de las 12 candidaturas de primera vuelta;
- dos destinos que son los votos oficiales de Lula y Flávio en la segunda vuelta;
- una fuente adicional, “aumento neto de votos válidos”, igual a 124.082.273 − 119.300.788 = **4.781.485**.

El espesor de todas las corrientes se expresa en votos usando la misma constante de escala SVG. La suma de las corrientes hacia cada finalista se reconcilia exactamente con su resultado TSE de segunda vuelta.

Para el gráfico completo se ajusta el modelo estatal mediante un desplazamiento logístico común de sus coeficientes. Además, el aumento neto se distribuye entre los dos destinos según sus porcentajes oficiales de segunda vuelta. Así se conservan simultáneamente:

```text
Σ fuentes de primera vuelta = votos válidos oficiales de primera vuelta
Σ flujos a Lula            = votos oficiales de Lula en segunda vuelta
Σ flujos a Flávio          = votos oficiales de Flávio en segunda vuelta
```

**Es una conciliación de un modelo con los totales, no una observación de trayectorias individuales.** El flujo desde “aumento neto” se reparte según los porcentajes de segunda vuelta para cerrar las cuentas; no identifica a esos votantes ni a los que dejaron de votar.

## 4. Modelo ecológico estatal

### Unidades y variables

Se usan 28 observaciones geográficas: los 27 estados y el exterior. Para cada unidad geográfica `r`:

- `x[r,c]` = votos de primera vuelta del candidato `c` divididos por los votos válidos de primera vuelta de esa unidad;
- `y[r]` = votos de Lula en segunda vuelta divididos por los votos válidos de segunda vuelta de esa unidad;
- `w[r]` = votos válidos de segunda vuelta de esa unidad divididos por los votos válidos totales de las 28 unidades.

Hay 12 variables `x`, una por cada candidatura en la primera vuelta. La suma de estas proporciones es 1 en cada geografía.

### Ajuste de coeficientes

Se estima un coeficiente `b[c]` por candidatura. La participación predicha de Lula en una unidad geográfica es:

```text
ŷ[r] = Σc x[r,c] × b[c]
```

El ajuste minimiza una suma de cuadrados ponderada con una regularización leve hacia el porcentaje nacional oficial de Lula en la segunda vuelta (`q`):

```text
L(b) = Σr w[r] × (ŷ[r] − y[r])²
       + 0,01 × Σc (b[c] − q)²
sujeto a 0 ≤ b[c] ≤ 1
```

En el código, el descenso de gradiente empieza con todos los coeficientes en `q`, usa paso `0,25`, limita los coeficientes al intervalo `[0, 1]`, y para al alcanzar un cambio máximo menor de `1e-11` o después de 30.000 iteraciones. La lista de candidaturas y el orden de las variables se obtiene de los resultados oficiales.

El ajuste es **ecológico**: relaciona composición de voto por estado con el resultado de segunda vuelta del mismo estado. No sigue a los mismos votantes. Las proporciones de cada partido o candidato tienden a estar correlacionadas geográficamente, y solo hay 28 unidades frente a 12 coeficientes; por eso el resultado depende de supuestos, restricciones y regularización.

### Calidad descriptiva mostrada

El generador calcula el error y `R²` ponderados entre el porcentaje estatal real de Lula y la predicción estatal **antes de la calibración nacional**:

- RMSE ponderado: **3,1836 puntos porcentuales** (redondeado a 3,18 pp en el informe);
- `R²` ponderado: **0,9332** (aprox. 93,3 %).

Estas estadísticas describen ajuste entre 28 agregados geográficos. No son validación individual, un intervalo de confianza ni evidencia causal. Un `R²` alto no elimina la falacia ecológica.

### Calibración adicional del Sankey completo

Los coeficientes del ajuste estatal se transforman a logit:

```text
z[c] = log(b[c] / (1 − b[c]))
```

Se busca un único desplazamiento `d` tal que:

```text
Σc (votos1[c] / votos_validos1) × sigmoid(z[c] + d)
  = (votos2_Lula − aumento_neto × porcentaje2_Lula) / votos_validos1
```

La búsqueda usa bisección durante 100 iteraciones en `[-40, 40]`. El lado derecho es el porcentaje de Lula que queda atribuido a las fuentes de primera vuelta después de apartar el flujo del aumento neto. Los coeficientes transformados se usan para repartir tanto los votos de los candidatos eliminados como los votos iniciales de ambos finalistas en el Sankey completo.

El aumento neto se asigna así:

```text
neto_Lula  = round(aumento_neto × porcentaje_oficial2_Lula)
neto_Flávio = aumento_neto − neto_Lula
```

El redondeo de los flujos de primera vuelta se corrige en el código para que ambos destinos coincidan exactamente con los votos oficiales de segunda vuelta. Este ajuste asegura coherencia aritmética; no añade información de identidad ni confirma transferencias.

## 5. Qué dice el cálculo sobre posibles cambios Lula ↔ Flávio

### Estimación del modelo

En el Sankey completo calibrado, el modelo produce estos dos flujos cruzados entre los grupos de votos iniciales de los finalistas:

| Flujo estimado por el modelo | Votos | Proporción del grupo inicial de origen |
|---|---:|---:|
| Flávio 1.ª vuelta → Lula 2.ª vuelta | **8.309.781** | aprox. 14,81 % de los 56.104.503 votos iniciales de Flávio |
| Lula 1.ª vuelta → Flávio 2.ª vuelta | **5.112.189** | aprox. 9,49 % de los 53.879.538 votos iniciales de Lula |

Estos valores responden “¿qué reparte este modelo, sujeto a estos datos y supuestos?”, no “¿qué hicieron esas personas?”. Los resultados agregados **permiten que algunos votantes de Flávio hayan cambiado a Lula**, pero no demuestran que ocurriera ni identifican cuántos.

La tabla completa de flujos de primera vuelta usada en el Sankey calibrado (los destinos incluyen cualquier flujo hacia un finalista; el último renglón no es una candidatura):

| Fuente | Votos válidos / tamaño del flujo | Asignado a Lula | Asignado a Flávio |
|---|---:|---:|---:|
| Flávio Bolsonaro, votos de 1.ª vuelta | 56.104.503 | 8.309.781 | 47.794.722 |
| Lula, votos de 1.ª vuelta | 53.879.538 | 48.767.349 | 5.112.189 |
| Escritor Augusto Cury | 3.448.569 | 1.763.144 | 1.685.425 |
| Renan Santos | 2.675.887 | 1.350.606 | 1.325.281 |
| Ronaldo Caiado | 2.605.148 | 1.286.153 | 1.318.995 |
| Zema | 326.488 | 168.238 | 158.250 |
| Samara | 122.911 | 63.555 | 59.356 |
| Hertz Dias | 43.103 | 22.313 | 20.790 |
| Clariana Barão | 40.043 | 20.725 | 19.318 |
| Edmilson Costa | 22.693 | 11.749 | 10.944 |
| Veterinário Wilson Grassi | 16.881 | 8.740 | 8.141 |
| Rui Costa Pimenta | 15.024 | 7.778 | 7.246 |
| Aumento neto de votos válidos | 4.781.485 | 2.476.101 | 2.305.384 |
| **Total de segunda vuelta** | **124.082.273** | **64.256.232** | **59.826.041** |

Las asignaciones son números enteros redondeados y reconciliados por el generador; la tabla no debe interpretarse como una lista de movimientos reales de electores.

### Los totales no obligan a que haya habido cambio entre los dos finalistas

El aumento total de Lula entre vueltas fue:

```text
64.256.232 − 53.879.538 = 10.376.694
```

El aumento total de Flávio fue:

```text
59.826.041 − 56.104.503 = 3.721.538
```

Un escenario puramente contable que conserva todos los votos iniciales de Lula con Lula y todos los iniciales de Flávio con Flávio también cabe en los totales:

```text
votos de los diez candidatos eliminados   9.316.747
+ aumento neto de votos válidos            4.781.485
                                          ----------
pool agregado disponible                  14.098.232

ganancia de Lula                          10.376.694
+ ganancia de Flávio                       3.721.538
                                          ----------
ganancia total                            14.098.232
```

Por tanto, **no es matemáticamente necesario que votos de Flávio se hayan transferido a Lula para explicar el resultado**. Una combinación de votos de candidaturas eliminadas y el cambio neto de votos válidos puede cubrir las ganancias de ambos finalistas sin intercambios directos entre sus grupos iniciales.

Este escenario prueba posibilidad aritmética, no describe lo que realmente sucedió. El aumento neto no es necesariamente un grupo identificable de electores; el número de votos válidos no revela quién participó en ambos turnos, quién se abstuvo, quién cambió entre blanco/nulo y válido o cómo votó una persona.

### Conclusión responsable

- **Posibilidad:** sí, es posible que parte de los votantes de Flávio en primera vuelta eligiera a Lula en segunda vuelta.
- **Lo que estima este modelo:** 8.309.781 en ese sentido, y 5.112.189 en el sentido contrario, en la versión documentada.
- **Lo que prueban los totales:** ninguno de esos flujos individuales. Los resultados son compatibles también con una contabilidad sin cambio directo entre finalistas.
- **Qué haría falta para afirmar transferencias individuales:** por ejemplo, una encuesta/panel con metodología y muestra adecuadas, o datos individuales legalmente disponibles y enlazables. Los archivos públicos agregados del TSE no bastan.

## 6. Construcción del HTML y controles visuales

- El HTML contiene todos los datos en un objeto JavaScript serializado por el generador; no necesita un servidor de aplicación ni una biblioteca externa de gráficos.
- Los gráficos se construyen con elementos SVG (`path`, `rect`, `text`) y muestran detalles adicionales en las etiquetas emergentes `<title>`.
- El primer Sankey se escala con los flujos de candidatos eliminados. El segundo usa la misma escala por voto en todos los enlaces e incluye todas las candidaturas iniciales y el incremento neto.
- Antes del segundo Sankey hay controles interactivos para ajustar de 0 % a 100 % la proporción asignada a Lula para cada una de las 12 candidaturas iniciales y para el aumento neto; la parte restante se asigna a Flávio. Los flujos, destinos, porcentajes y diferencias respecto a los resultados oficiales se actualizan en el navegador. “Restaurar estimación del modelo” recupera los porcentajes calibrados iniciales.
- Los controles son parámetros de análisis de escenarios, no una nueva estimación ni datos observados. El control del aumento neto solo divide una diferencia aritmética de totales; no representa un grupo identificable de votantes. El primer gráfico de candidaturas eliminadas y la tabla descriptiva permanecen como referencia del modelo.
- Los valores de resultado y la interfaz se vuelven a renderizar al cambiar entre español, inglés y alemán.
- El archivo muestra enlaces a los dos JSON nacionales del TSE y un aviso de IA.
- No hay logotipo ni menciones de una marca en el informe.
- El porcentaje de aumento del segundo gráfico se presenta con dos decimales para que el `+4,01 %` no se redondee visualmente a `+4 %`.

## 7. Reproducción y validación

Desde la raíz del proyecto en Windows, con el entorno `.venv` ya configurado:

```powershell
.\.venv\Scripts\python.exe .\tools\generate_vote_transfer_report.py
.\.venv\Scripts\ruff.exe check .\tools\generate_vote_transfer_report.py
.\.venv\Scripts\python.exe -X utf8 -m pytest -q
```

El primer comando vuelve a consultar el TSE y sobrescribe el informe, por lo que el contenido puede diferir si se actualizan los archivos fuente. Para comparar exactamente la versión documentada, use los totales y la marca de generación insertados en el HTML o guarde una copia antes de regenerar.

También se verificaron la sintaxis del JavaScript embebido (`node --check`), la presencia de las 12 candidaturas de primera vuelta y estas identidades:

```text
Σ votos iniciales de las 12 candidaturas = 119.300.788
Σ flujos de primera vuelta a Lula + neto_Lula = 64.256.232
Σ flujos de primera vuelta a Flávio + neto_Flávio = 59.826.041
neto_Lula + neto_Flávio = 4.781.485
```

La batería del repositorio pasó: **76 pruebas**. En Windows se usó `-X utf8` porque los fixtures del proyecto contienen texto UTF-8 y el modo de texto predeterminado de esa máquina no lo decodifica correctamente.

## 8. Archivos clave

- [Informe HTML final](./transferencia_votos_2026.html)
- [Generador de datos, modelo, calibración y HTML](../tools/generate_vote_transfer_report.py)
- [Rutas TSE, ID de elecciones y lista de abrangencias](../src/apuracao/sources.py)
- [Parser de resultados EA20](../src/apuracao/parse.py)

## English summary

The report uses final TSE JSON results for Brazil's 2026 presidential elections 6257 (round one) and 6258 (runoff), covering Brazil nationally, 27 states, and abroad: 58 files total. Its ecological model uses 28 subnational units, excluding the national aggregate. It contains two different Sankey charts. The eliminated-candidate chart uses a bounded, ridge-regularized ecological regression fitted to those 28 geographic aggregates. The all-valid-votes chart includes all 12 first-round candidates and a separate net-valid-vote increase stream. A common logistic offset calibrates first-round candidate flows to the official runoff totals; the net increase is split using the official runoff shares. Rounded links are reconciled exactly to both finalists' official totals.

Official valid votes increased from 119,300,788 to 124,082,273 (+4,781,485, +4.01%). The calibrated model estimates 8,309,781 Flávio-first-round votes flowing to Lula and 5,112,189 Lula-first-round votes flowing to Flávio. The HTML also provides sliders and percentage inputs for each first-round candidate and the net increase; changing an allocation updates the full Sankey and scenario totals immediately, and a reset restores model defaults. These are scenario controls, not observed voter movements. Aggregate totals do not prove individual transfers. A no-cross-switch accounting scenario is also possible: the 9,316,747 eliminated-candidate votes plus the 4,781,485 net valid-vote increase equal the finalists' combined gains of 14,098,232. This is arithmetic, not evidence of voter behavior.

## Deutsche Zusammenfassung

Der Bericht verwendet endgültige öffentliche TSE-JSON-Ergebnisse für die brasilianische Präsidentschaftswahl 2026, Wahlkennungen 6257 (erster Wahlgang) und 6258 (Stichwahl), für Brasilien gesamt, 27 Bundesstaaten und das Ausland: insgesamt 58 Dateien. Das ökologische Modell verwendet 28 Untereinheiten und lässt das nationale Aggregat aus. Der Bericht enthält zwei unterschiedliche Sankey-Diagramme. Das Diagramm der ausgeschiedenen Kandidaten beruht auf einer beschränkten, regularisierten ökologischen Regression mit 28 geografischen Aggregaten. Das Diagramm aller gültigen Stimmen enthält alle 12 Kandidaten des ersten Wahlgangs und einen separaten Strom für den Nettozuwachs gültiger Stimmen. Eine gemeinsame logistische Verschiebung kalibriert die Flüsse auf die amtlichen Stichwahlergebnisse; der Nettozuwachs wird anhand der amtlichen Stimmenanteile aufgeteilt. Gerundete Flüsse stimmen exakt mit den amtlichen Ergebnissen beider Finalisten überein.

Die gültigen Stimmen stiegen von 119.300.788 auf 124.082.273 (+4.781.485 bzw. +4,01 %). Das kalibrierte Modell schätzt 8.309.781 Stimmen von Flávio-Wählern aus dem ersten Wahlgang zu Lula und 5.112.189 Stimmen von Lula-Wählern zu Flávio. Das HTML bietet außerdem Schieberegler und Prozentfelder für jeden Kandidaten des ersten Wahlgangs sowie den Nettozuwachs; Änderungen aktualisieren Sankey-Diagramm und Szenariosummen sofort, und die Zurücksetzen-Schaltfläche stellt die Modellwerte wieder her. Dies sind Szenariowerte, keine beobachteten individuellen Wechsel. Aggregierte Ergebnisse beweisen keine individuellen Übertritte. Auch eine rechnerische Bilanz ohne direkten Wechsel zwischen den Wählergruppen ist möglich: 9.316.747 Stimmen ausgeschiedener Kandidaten plus 4.781.485 Nettozuwachs ergeben zusammen 14.098.232, genau den Gesamtzuwachs beider Finalisten. Das ist eine arithmetische Möglichkeit, kein Beleg für das tatsächliche Wahlverhalten.
