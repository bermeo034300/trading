# Validación de la ventaja — septiembre/octubre 2026

**Veredicto: no se encontró ventaja.** Los tres setups (absorción, imbalance apilado y divergencia de CVD)
no predicen el precio de BTCUSDT ni de ETHUSDT (futuros perpetuos de Binance), en ninguno de los tres
marcos probados. La regla actual pierde dinero en todos. QuantFlow queda como herramienta visual de
order flow, no como sistema de decisión.

## Método

- Datos: 60 días completos (2026-09-02 → 2026-10-31 UTC), 131 millones de ticks reales con lado agresor.
- `python -m quantflow research --days 60`: registra todas las señales evaluadas, aceptadas o no, y simula
  cada una con los ticks siguientes hasta el stop, el objetivo o el límite de tiempo, con comisiones
  (0,05 % taker, 0,02 % maker) y slippage.
- Separación temporal: el primer 60 % del periodo para ajustar y el último 40 % para probar.
- Solo cuentan las señales que no se solapan con otra abierta del mismo setup y lado.
- En 15 min y 1 h se escalaron el tamaño de nivel del footprint, el perfil de volumen y el tiempo máximo
  en operación.

## Resultados

| | 5 min | 15 min | 1 hora |
|---|---|---|---|
| Señales sin solape | 7 789 | 2 023 | 337 |
| Coste por operación (todas las señales) | 1,04R | 0,56R | 0,27R |
| **Ventaja antes de comisiones** | **−0,05R** | **−0,05R** | **−0,13R** |
| Resultado neto, todas las señales | −1,09R | −0,61R | −0,40R |
| Regla actual, periodo de ajuste | −0,27R | −0,15R | −0,31R |
| Regla actual, periodo de prueba | −0,25R | −0,22R | −0,18R |
| AUC del modelo re-ajustado en prueba (0,5 = azar) | 0,535 | 0,525 | 0,540 |
| AUC del modelo actual en prueba | 0,497 | 0,509 | 0,426 |

- **La ventaja antes de comisiones es cero o negativa en todos los marcos.** Subir de marco solo reduce
  el peso de las comisiones.
- **La probabilidad del modelo no se cumple:** en 5 min, cuando predice 79 % acierta el 28 %.
- **Ningún setup es consistente entre periodos ni marcos.** Los pocos resultados positivos aparecen en un
  solo periodo o con muestras pequeñas (por ejemplo, 4 señales de imbalance apilado en 1 h), así que son
  compatibles con el azar.

Informes completos: [5 min](2026-10_5min.md) · [15 min](2026-10_15min.md) · [1 hora](2026-10_1hora.md).

## Si se retoma

Antes de diseñar reglas nuevas, medir si alguna variable de order flow (delta, desequilibrio del libro, CVD)
predice el retorno a 5, 15 o 60 minutos. Si ninguna lo hace, buscar otra familia de estrategias.
