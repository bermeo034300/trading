# Correr QuantFlow en Windows

## Requisitos

- Windows 10 u 11.
- Python 3.11 o superior (recomendado 3.12).
- Conexión a internet (Binance).

## Pasos

1. Instalar Python desde https://www.python.org/downloads/windows/.
   **Importante:** marcar la casilla **"Add python.exe to PATH"** al instalar.
2. Descargar el repo: en GitHub, **Code → Download ZIP**, y extraerlo.
3. Hacer doble clic en **`iniciar.bat`**.
4. La primera vez crea `.venv` e instala las dependencias (2–5 minutos).
5. Se abre el panel en http://127.0.0.1:8000. El calentamiento tarda cerca de un minuto.
6. Para detenerlo: cerrar la ventana o `Ctrl+C`.

El lanzador crea `config.local.yaml` la primera vez. Ahí podés cambiar la passphrase del webhook
sin tocar `config.yaml`.

## Si algo falla

| Problema | Solución |
|---|---|
| "No encontré Python 3.11+" | Reinstalar Python marcando "Add python.exe to PATH". |
| No instalan las dependencias | Revisar internet, antivirus o firewall. |
| "Puerto 8000 ocupado" | Cerrar la otra app o cambiar `server.port` en `config.local.yaml`. |
| No llegan datos de Binance | Tu país o red puede estar bloqueado; probar otra red. |
| Windows pregunta por el firewall | Permitir el acceso a Python en redes privadas. |

## TradingView (opcional)

El panel local funciona sin TradingView. Para recibir alertas reales hace falta:

1. Cambiar `server.webhook_passphrase` en `config.local.yaml` por una clave larga.
2. Exponer el puerto con un túnel (por ejemplo `cloudflared`) o correrlo en un VPS.
3. En TradingView, usar `https://<tu-url>/webhook/tradingview` como Webhook URL.

TradingView exige plan pago para webhooks. Sin túnel o VPS, el webhook no recibe alertas desde internet.

## Notas

- Es **paper trading**: no envía órdenes reales.
- `config.local.yaml`, `.venv/`, `data/` y `reports/` quedan solo en tu PC; no se suben a git.
- Para actualizar: borrar la carpeta y descargar el ZIP nuevo, o usar `git pull` si sabés Git.
