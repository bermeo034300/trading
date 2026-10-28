# QuantFlow

Programa de análisis de **order flow institucional** con decisión **cuantitativa** de entradas y salidas,
integrado con **TradingView**. Opera en **paper trading** (simulado): no envía órdenes reales.

> No es asesoría financiera. Ningún sistema garantiza ganancias. Valida cualquier configuración con
> backtests y semanas de paper trading antes de pensar en dinero real.

## Qué hace

```
 Binance (ticks con agresor + libro L2) ──► Footprint por nivel ─┐
                                           Perfil de volumen     │
                                           VWAP ± σ, CVD         ├─► Setups ─► Modelo de probabilidad ─► Decisión ─► Paper broker
                                           Libro: desequilibrio  │            + calibración bayesiana     (EV neto)    (stop, TP, BE,
 TradingView (Pine + alerta webhook) ──────► Confirmación ───────┘                                                      salida por flujo)
                                                                                         │
                                                         Panel web (http://127.0.0.1:8000) + diario SQLite
```

**Order flow** (`quantflow/orderflow/`)
- **Footprint**: volumen bid × ask por nivel de precio en cada barra, delta, delta máx./mín. intrabarra, POC.
- **Imbalances diagonales** (ask(p) ≥ 3× bid(p−1)) e **imbalances apilados** (3+ niveles seguidos).
- **Perfil de volumen** móvil: POC, VAH y VAL (área de valor del 70 %).
- **VWAP** de sesión con bandas de desviación estándar; **CVD** (delta acumulado).
- **Libro L2** (top 20): desequilibrio bids/asks y detección de muros de liquidez.

**Setups** (`quantflow/strategy/setups.py`)
| Setup | Lógica | Tipo |
|---|---|---|
| `absorcion` | Agresión fuerte de un lado (delta) pero la vela cierra del otro lado de su rango: un participante pasivo grande absorbe. | Reversión al valor |
| `imbalance_apilado` | Varios niveles seguidos con imbalance: iniciativa agresiva. Precio del lado correcto del VWAP. | Continuación |
| `divergencia_cvd` | Nuevo máximo/mínimo de precio que el CVD no confirma: agotamiento. | Reversión al valor |

**Decisión cuantitativa** (`quantflow/strategy/decision.py`)
1. Cada setup produce factores en [−1, 1]: fuerza del order flow, delta, ubicación (VWAP / área de valor),
   régimen (exponente de **Hurst**: tendencia vs. reversión), libro L2 y confirmación de TradingView.
2. Modelo logístico → `p_modelo`.
3. **Calibración bayesiana por setup**: con cada operación cerrada se corrige el sesgo entre lo predicho y
   la tasa real de acierto, con una fuerza previa de 30 operaciones (pocas operaciones no distorsionan).
4. **Esperanza neta** en múltiplos de R, descontando comisiones y slippage:
   `EV = p · ganancia_R − (1 − p) · pérdida_R`. Las salidas al objetivo son órdenes límite
   (comisión maker); entradas, stops y salidas a mercado pagan taker. Las tasas se configuran en
   `risk.fee_pct_per_side` y `risk.maker_fee_pct_per_side`.
5. Se entra solo si probabilidad ≥ 55 %, EV ≥ +0.15R, R:R ≥ 1.5, el stop está entre los límites de ATR y los
   costes no superan el 35 % del riesgo. Si hay varias señales, gana la de mayor EV.

**Entradas y salidas** (`quantflow/execution/paper.py`)
- Entrada al cierre de la barra de la señal. Stop estructural (más allá del extremo o de la zona de
  imbalance, con colchón de ATR). Objetivo estructural (POC / VWAP / borde del área de valor) o múltiplo de R.
- Tamaño por riesgo fijo (0.5 % del capital por operación, tope de apalancamiento 5×).
- Break-even al llegar a +1R, salida si aparece order flow en contra, salida por tiempo.
- Límite de pérdida diaria (2 %), enfriamiento entre operaciones, una posición por símbolo.

## Instalación

Requiere Python 3.11 o superior.

**macOS / Linux**

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

**Windows (PowerShell)**

La forma más simple es doble clic en **`iniciar.bat`** (ver [`WINDOWS.md`](WINDOWS.md)). Crea el
entorno, instala dependencias y abre el panel.

Para desarrollo manual:

```powershell
py -3.12 -m venv .venv
.venv\Scripts\Activate.ps1
pip install -e ".[dev]"
```

Si solo querés ejecutar el programa, sin herramientas de desarrollo:

```bash
pip install -r requirements.txt
```

Si instalás el wheel (sin el repo), copiá el `config.yaml` de ejemplo al directorio desde donde vas a
ejecutar o pasá `--config /ruta/config.yaml`.

`pyproject.toml` declara las dependencias con rangos; `requirements.txt` fija las versiones
exactas del entorno probado. Al instalar el paquete queda disponible el comando `quantflow`
dentro del entorno virtual.

## Uso

**Backtest con ticks reales** (el mismo motor que en vivo):

```bash
python -m quantflow backtest --hours 24
```

```bash
python -m quantflow backtest --days 7 --save-calibration
```

`--hours` descarga por la API REST de Binance; `--days` descarga los archivos diarios de
data.binance.vision (más rápido para periodos largos; se guardan en `data/cache`). `--save-calibration`
guarda lo aprendido para que el modo en vivo empiece con probabilidades calibradas. El detalle de cada
operación queda en `reports/`.

**En vivo (paper trading + panel)**:

```bash
python -m quantflow live
```

Abre http://127.0.0.1:8000. Al arrancar descarga 2 horas de ticks para calentar los indicadores
(tarda entre 30 s y unos minutos) y luego escucha el mercado en tiempo real. El panel muestra el
footprint, los niveles clave, las señales con sus razones y la probabilidad, la posición abierta y el
rendimiento. Todo queda registrado en `data/quantflow.db` (tablas `signals`, `trades` y `calibration`).

También podés usar `quantflow` directamente si instalaste el paquete (`quantflow backtest --days 7`,
`quantflow live`, etc.).

**Pruebas**:

```bash
pytest
```

## Desarrollo

```bash
ruff check .        # lint
mypy                # tipos (quantflow/)
pytest -q           # tests
pre-commit install  # hooks opcionales antes de cada commit
```

El CI de GitHub Actions corre lint, tipos y tests en Python 3.11 y 3.12 para cada push a `main`
y cada pull request.

## Producción (VPS)

Para correrlo 24/7 sin depender de tu computadora, con HTTPS y webhook estable, seguí
[`deploy/README.md`](deploy/README.md). Incluye Oracle Cloud Always Free paso a paso, systemd,
Caddy con basic auth, backup de SQLite y la configuración de TradingView.

## Integración con TradingView

TradingView no ofrece una API de datos de ticks ni del libro de órdenes, así que la integración funciona
en el sentido en que TradingView lo permite: **TradingView → QuantFlow** mediante alertas webhook.

1. En TradingView abre el Editor de Pine, pega `pine/quantflow_orderflow.pine` y añádelo al gráfico
   (por ejemplo `BINANCE:BTCUSDT.P`). Dibuja VWAP ± σ y las señales en el precio, y el CVD o el delta en
   un panel inferior.
2. En la configuración del indicador escribe la misma passphrase que pusiste en
   `config.yaml → server.webhook_passphrase`. El webhook queda **deshabilitado** (HTTP 503) mientras
   siga la passphrase de ejemplo `cambia-esto`.
3. TradingView solo envía webhooks a URLs públicas. Expón el puerto local con un túnel, por ejemplo:

   ```bash
   cloudflared tunnel --url http://127.0.0.1:8000
   ```

4. Crea la alerta: condición **QuantFlow → Cualquier llamada a la función alert()**, y en Notificaciones
   marca **Webhook URL** = `https://<tu-túnel>/webhook/tradingview`. Los webhooks requieren un plan de
   pago de TradingView.

Cada alerta queda como confirmación durante 15 minutos (`tradingview_ttl_seconds`) y suma o resta en el
modelo de probabilidad. Con `require_tradingview: true` el motor solo entra cuando TradingView confirma la
dirección. También puedes enviar alertas de tus propios indicadores o estrategias con este JSON:

```json
{"passphrase": "...", "symbol": "{{ticker}}", "side": "long", "setup": "mi_estrategia", "price": {{close}}}
```

## Configuración

Todo está en `config.yaml`: símbolos, mercado (`futures` o `spot`), duración de barra, tamaño de nivel del
footprint, umbrales de imbalance, setups activos, pesos del modelo, mínimos de probabilidad y EV, y
parámetros de riesgo.

`quantflow` busca `config.yaml` en el directorio actual y, si no existe, en la raíz del repo. También podés
indicar otro archivo con `--config /ruta/config.yaml`. Las rutas relativas del config (`storage.db_path`,
`storage.cache_dir`, `--out`) se resuelven contra el directorio donde vive el config, no contra el paquete
instalado.

## Limitaciones (importante)

- **Los pesos del modelo son supuestos razonables, no están optimizados.** La calibración bayesiana corrige
  las probabilidades con los resultados, pero hace falta una muestra grande (cientos de operaciones) para
  que sean fiables. Usa `backtest --days N --save-calibration` con varias semanas de datos.
- El backtest no tiene historial del libro L2 ni alertas de TradingView: esos factores valen 0 en él.
- El slippage es un supuesto fijo; en momentos de volatilidad real puede ser mayor.
- **No se modela el funding de los perpetuos** (cada 8 h). Si una posición cruza las 00:00, 08:00 o
  16:00 UTC, el costo o ingreso real puede diferir.
- Las comisiones por defecto son las estándar de Binance USDⓈ-M Tier 0 (0.02 % maker / 0.05 % taker).
  Tu nivel VIP o el descuento pagando con BNB pueden reducirlas; ajustá `risk.fee_pct_per_side` y
  `risk.maker_fee_pct_per_side` a tu cuenta.
- Binance no está disponible desde algunos países (por ejemplo EE. UU.); en ese caso hay que adaptar
  `quantflow/feeds/binance.py` a otro exchange.
- Para futuros CME (ES, NQ, GC) el order flow real requiere un feed de pago (Rithmic, CQG, IBKR). El motor
  es independiente de la fuente: basta con escribir un feed que produzca `Trade` y `BookSnapshot`.

## Estructura

```
quantflow/
  feeds/binance.py        WebSocket en vivo, REST y data.binance.vision
  orderflow/              footprint, perfil de volumen, VWAP, libro L2
  quant/                  ATR, Hurst, sigmoide, calibración bayesiana
  strategy/               setups y motor de decisión
  execution/paper.py      broker simulado y gestión de riesgo
  engine.py               orquesta todo (vivo y backtest)
  backtest.py             reproducción de ticks históricos
  server.py               API, webhook de TradingView y panel
  dashboard/index.html    panel web
pine/quantflow_orderflow.pine
tests/                    núcleo, server, storage, config, feed y backtest
```

## Licencia

MIT. Ver `LICENSE`.
