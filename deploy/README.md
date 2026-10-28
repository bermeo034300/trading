# Despliegue 24/7 en un VPS

Objetivo: correr QuantFlow sin depender de tu Mac, con una URL HTTPS estable para TradingView.

## Requisitos mínimos

- Ubuntu 22.04 o 24.04.
- 1 vCPU, 1 GB de RAM, 10 GB de disco.
- Puertos 22, 80 y 443 abiertos.
- Binance accesible desde la región del VPS (no usar regiones de EE. UU.).
- Un dominio o subdominio. Si no tenés dominio, se puede usar `https://<ip-con-guiones>.sslip.io`.

## Opción A: Oracle Cloud Always Free

Es gratis, pero el registro y la capacidad ARM son la parte más lenta.

1. Crear cuenta en https://www.oracle.com/cloud/free/ y verificar con tarjeta (no cobra si te quedás en Always Free).
2. Elegir **home region** con cuidado: no se puede cambiar después. Para Binance, elegí **São Paulo** o una región de Europa (Frankfurt, Madrid, Ámsterdam). **No EE. UU.**
3. Ir a **Compute → Instances → Create instance**.
4. Imagen: **Ubuntu 24.04**.
5. Shape: **Ampere → VM.Standard.A1.Flex**, 1 OCPU y 6 GB (Always Free permite hasta 4 OCPU / 24 GB).
6. Asignar **IPv4 pública** y agregar tu clave SSH.
7. Crear la instancia y reservar la IP pública para que no cambie al reiniciar.
8. En la **VCN → Security List**, permitir ingress TCP 22, 80 y 443 desde `0.0.0.0/0`.
9. En la instancia, abrir los puertos en el firewall de Ubuntu:

```bash
sudo iptables -I INPUT 6 -m state --state NEW -p tcp --dport 80 -j ACCEPT
sudo iptables -I INPUT 6 -m state --state NEW -p tcp --dport 443 -j ACCEPT
sudo apt update && sudo DEBIAN_FRONTEND=noninteractive apt install -y iptables-persistent
sudo netfilter-persistent save
```

10. Conectar: `ssh ubuntu@IP_PUBLICA`.

Si aparece **"Out of capacity"** para Ampere, probá otro availability domain o esperá; el micro AMD siempre suele estar disponible, pero es más lento.

> **Ojo con Always Free:** OCI puede reclamar instancias ociosas (CPU, red y memoria bajas durante 7 días).
> Este bot consume poco CPU, así que para 24/7 estable conviene un VPS pago; Oracle sirve bien para probar.

## Opción B: cualquier VPS (Hostinger VPS, Hetzner, DigitalOcean)

Crear una instancia Ubuntu 24.04 con 1 vCPU / 1 GB, IP pública y clave SSH. Abrir 22/80/443:

```bash
sudo ufw allow 22,80,443/tcp
sudo ufw --force enable
```

## Instalación

```bash
sudo apt update
sudo apt install -y python3 python3-venv python3-pip git sqlite3
sudo useradd --system --no-create-home --home-dir /opt/quantflow --shell /usr/sbin/nologin quantflow
sudo mkdir -p /opt/quantflow
sudo git clone https://github.com/bermeo034300/trading.git /opt/quantflow
sudo chown -R quantflow:quantflow /opt/quantflow
sudo -u quantflow python3 -m venv /opt/quantflow/.venv
sudo -u quantflow /opt/quantflow/.venv/bin/pip install -e /opt/quantflow
sudo -u quantflow mkdir -p /opt/quantflow/data /opt/quantflow/reports /opt/quantflow/backups
```

## Configuración

```bash
sudo -u quantflow cp /opt/quantflow/config.yaml /opt/quantflow/config.local.yaml
sudo -u quantflow chmod 600 /opt/quantflow/config.local.yaml
sudo -u quantflow nano /opt/quantflow/config.local.yaml
```

Cambiar:

```yaml
server:
  host: 127.0.0.1
  port: 8000
  webhook_passphrase: "UNA-PASSPHRASE-LARGA-Y-PRIVADA"
```

Las rutas `data/` y `reports/` se resuelven contra `/opt/quantflow`, no contra el paquete. La passphrase
no se commitea: `config.local.yaml` está en `.gitignore`.

## Servicio systemd

```bash
sudo cp /opt/quantflow/deploy/quantflow.service /etc/systemd/system/quantflow.service
sudo systemctl daemon-reload
sudo systemctl enable --now quantflow
sudo systemctl status quantflow --no-pager
```

Logs: `journalctl -u quantflow -f`.

## HTTPS con Caddy

Instalar Caddy desde el repositorio oficial (trae `basic_auth`):

```bash
sudo apt install -y debian-keyring debian-archive-keyring apt-transport-https curl
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' | sudo gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' | sudo tee /etc/apt/sources.list.d/caddy-stable.list
sudo apt update && sudo apt install -y caddy
```

Generar el hash de la clave del panel:

```bash
caddy hash-password --plaintext 'TU-CLAVE-DEL-PANEL'
```

Editar `/etc/caddy/Caddyfile` (o copiar `deploy/Caddyfile`), reemplazar el dominio y el hash:

```caddyfile
quantflow.example.com {
    encode zstd gzip

    @public path /webhook/*
    handle @public {
        reverse_proxy 127.0.0.1:8000
    }

    handle {
        basic_auth {
            admin $2a$14$REEMPLAZAR_POR_EL_HASH
        }
        reverse_proxy 127.0.0.1:8000
    }
}
```

```bash
sudo systemctl reload caddy
```

Caddy pide el certificado Let's Encrypt automáticamente. Si no tenés dominio, usá
`203-0-113-10.sslip.io` (la IP con guiones) como hostname.

## Verificación

```bash
curl -s http://127.0.0.1:8000/api/state | python3 -m json.tool | head
curl -I https://TU_HOST/
curl -s -X POST https://TU_HOST/webhook/tradingview \
  -H 'Content-Type: application/json' \
  -d '{"passphrase":"TU-PASSPHRASE","symbol":"BTCUSDT","side":"long"}'
```

- El panel debe pedir usuario/clave.
- El webhook debe devolver `200` con la passphrase correcta y `401` con una incorrecta.

## TradingView

1. En el gráfico (`BINANCE:BTCUSDT.P`), agregar `pine/quantflow_orderflow.pine`.
2. En la configuración del indicador, poner la misma passphrase del config.
3. Crear alerta: condición **QuantFlow → Any alert() function call**.
4. Webhook URL: `https://TU_HOST/webhook/tradingview`.
5. TradingView requiere plan pago para webhooks.

## Backup y logs

```bash
# Copia diaria de SQLite a las 03:00 UTC
echo '0 3 * * * quantflow /opt/quantflow/deploy/backup.sh' | sudo tee /etc/cron.d/quantflow-backup
```

El script guarda 14 días en `/opt/quantflow/backups`. Los logs van a journald:

```bash
journalctl -u quantflow --since "1 hour ago"
```

## Actualizaciones

```bash
sudo -u quantflow git -C /opt/quantflow pull --ff-only
sudo -u quantflow /opt/quantflow/.venv/bin/pip install -e /opt/quantflow
sudo systemctl restart quantflow
```

## Notas de producción

- No exponer el puerto 8000 a internet: Caddy habla con `127.0.0.1:8000`.
- El panel queda detrás de basic auth; el webhook queda público pero protegido por passphrase.
- Si el VPS está en EE. UU., Binance puede bloquear las conexiones. Probar `curl https://fapi.binance.com/fapi/v1/ping`.
- SQLite alcanza para un solo proceso. No correr dos instancias sobre la misma base.
- El funding de perpetuos no está modelado; es una limitación conocida del motor.
