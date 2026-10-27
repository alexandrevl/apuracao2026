# Cambios: escenarios interactivos de transferencia de votos

## Resumen

El informe HTML ahora permite explorar escenarios alternativos de asignación de votos entre Lula y Flávio para la segunda vuelta. Se puede ajustar el porcentaje de cada candidatura de primera vuelta que se asigna a Lula; el porcentaje restante se asigna a Flávio. Los gráficos y las sumas del escenario se actualizan en el navegador.

## Cambios incluidos

- Se añadieron controles deslizantes y campos numéricos de 0 % a 100 % para las 12 candidaturas de primera vuelta y para el aumento neto de votos válidos.
- Se añadió un botón para restaurar las proporciones iniciales calibradas del modelo.
- El Sankey de todos los votos válidos muestra los flujos del escenario seleccionado y actualiza los totales de destino.
- El resumen muestra los votos del escenario para cada finalista y la diferencia frente a su resultado oficial de segunda vuelta.
- La interfaz y las explicaciones están disponibles en español, inglés y alemán.
- Se actualizaron el generador del informe y esta documentación metodológica.

## Archivos

- `tools/generate_vote_transfer_report.py`: plantilla HTML, textos localizados y lógica interactiva.
- `docs/transferencia_votos_2026.html`: informe HTML generado y listo para abrir en un navegador.
- `docs/transferencia_votos_2026_metodologia.md`: metodología, fuentes, supuestos, límites y reproducción.

## Alcance e interpretación

Los valores iniciales reproducen el modelo calibrado que acompaña al informe. Los controles son parámetros para explorar escenarios, no una encuesta, una predicción ni una reconstrucción de decisiones individuales.

El control del aumento neto solo distribuye aritméticamente la diferencia entre los totales oficiales de votos válidos de ambas vueltas. No representa un grupo identificable de nuevos votantes. El primer gráfico —solo candidaturas eliminadas— y la tabla descriptiva siguen mostrando la estimación del modelo como referencia; los controles modifican el Sankey completo de escala común.

## Validación realizada

- Sintaxis del JavaScript embebido: correcta.
- Ruff sobre el generador: sin problemas.
- Pruebas del repositorio: **76 pasaron**.
- Comprobación interactiva en navegador: 13 controles disponibles; los escenarios extremos de 0 % y 100 % conservan exactamente el total de votos válidos; el botón de restauración devuelve los resultados oficiales y el cambio de idioma mantiene los controles.

## English

The HTML report now has 13 adjustable scenario controls: one for each of the 12 first-round candidates and one for the net increase in valid votes. Each sets the share assigned to Lula; the remainder goes to Flávio. The full-scale Sankey and scenario totals update immediately, including each finalist's difference from the official runoff result. A reset button restores the calibrated model values.

These are user-defined scenarios, not observed voter movements, a poll, or a prediction. The net-increase control allocates only an arithmetic difference between official totals; it is not an identifiable group of voters. The generator, generated HTML, and methodology were updated. Validation passed: JavaScript syntax, Ruff, all 76 repository tests, and browser checks for the 0%/100% extremes, reset, and language switching.

## Deutsch

Der HTML-Bericht enthält jetzt 13 einstellbare Szenario-Regler: je einen für die 12 Kandidaten des ersten Wahlgangs und einen für den Nettozuwachs gültiger Stimmen. Jeder Regler legt den Lula zugeordneten Anteil fest; der Rest entfällt auf Flávio. Das vollständige Sankey-Diagramm und die Szenariosummen werden sofort aktualisiert, einschließlich der Abweichung vom amtlichen Stichwahlergebnis. Eine Schaltfläche stellt die kalibrierten Modellwerte wieder her.

Dies sind benutzerdefinierte Szenarien, keine beobachteten Wählerwechsel, Umfrage oder Prognose. Der Regler für den Nettozuwachs verteilt nur eine rechnerische Differenz zwischen amtlichen Summen und keine identifizierbare Wählergruppe. Generator, erzeugtes HTML und Methodik wurden aktualisiert. Die Prüfungen waren erfolgreich: JavaScript-Syntax, Ruff, alle 76 Repository-Tests sowie Browserprüfungen der Extremwerte 0 %/100 %, der Zurücksetzen-Funktion und des Sprachwechsels.
