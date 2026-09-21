# Control Internet — Especificación técnica

Sistema de monitoreo 24/7 de conectividad, velocidad y ancho de banda de internet,
usado para generar reportes con evidencia objetiva ante el proveedor de internet.

Este documento es la **fuente de verdad** del proyecto. Cualquier agente o
desarrollador que programe sobre este repo debe seguir esta spec al pie de la
letra. Cambios de diseño se documentan acá antes de implementarse.

## Objetivo del sistema

Detectar y registrar caídas de internet con precisión de segundos, para poder
generar reportes verificables (fecha, hora exacta, duración) que sirvan como
evidencia ante el ISP.

## Stack

- Servidor: Linux, ya corre Docker con otros stacks (siracom_app, sicop-mdsj,
  sistema-asistencias). Este proyecto sigue el mismo patrón: un stack propio
  con `docker-compose.yml`, containers con `restart: unless-stopped`.
- Agente: Python 3.11+
- Dashboard/API: FastAPI (Python) — consistencia con el resto del stack
- Base de datos: SQLite, persistida en volumen (`./data/monitor.db`)
- Puerto dashboard: **8085** (verificado libre contra `docker ps` del servidor)

## Componentes

### 1. Agente (`/agente`)
Contenedor con `network_mode: host` (OBLIGATORIO — es la única forma de medir
la red real del servidor y no la red interna de Docker).

Seis loops independientes, corriendo en paralelo (asyncio), para que
uno lento (speedtest) nunca bloquee la detección de caídas:

| Chequeo | Intervalo | Archivo |
|---|---|---|
| Conectividad (ping) | cada 5 segundos | `checks/connectivity.py` |
| Velocidad (speedtest-cli) | cada 30–60 min | `checks/speed.py` |
| Test liviano (light probe) | cada 60 segundos | `checks/light_probe.py` |
| Ancho de banda (vnstat) | cada 5 minutos | `checks/bandwidth.py` |
| Resolución DNS | cada 5 segundos | `checks/dns.py` |
| Re-detección ISP hop | cada 6 horas | `utils.py` (isp_hop_refresh_loop) |

**Conectividad — regla de diseño clave:**
- Pinguear 4 destinos en paralelo: `8.8.8.8`, `1.1.1.1`, el gateway local
  (detectado automáticamente, no hardcodeado), y opcionalmente `isp_hop`
  (primer salto fuera del gateway, detectado vía traceroute al iniciar).
- Implementar como **máquina de estados** UP/DOWN por destino. NO guardar cada
  ping individual (sería ~17k filas/día). Solo escribir en la base cuando un
  destino cambia de estado, con timestamp exacto.
- Un destino se considera DOWN tras **2 pings fallidos consecutivos**
  (evita falsos positivos por un paquete perdido aislado).
- **Clasificación de origen con 3 niveles:**
  - `red_local`: el gateway está DOWN → problema en la red local.
  - `isp_primer_salto`: el isp_hop está DOWN pero el gateway está UP →
    problema en el primer tramo de la red del ISP.
  - `isp_general`: destinos externos (8.8.8.8/1.1.1.1) DOWN pero isp_hop y
    gateway están UP → problema más adentro de la red del ISP o en internet
    en general.
  - Para el destino gateway, `origen` siempre es `"red_local"` por definición.
  - Para el destino isp_hop, `origen` es `"red_local"` si gateway está DOWN,
    `"isp_primer_salto"` en otro caso.
  - Si `isp_hop` no fue detectado (entorno sin hop identificable), los
    destinos externos usan la clasificación de 2 niveles original
    (`isp` si gateway UP, `red_local` si gateway DOWN).

**Detección de ISP hop (isp_hop):**
- Al iniciar, ejecutar `traceroute -I -n -m 15 -w 3 8.8.8.8` y tomar el
  primer hop que NO sea el gateway local. Se aceptan IPs CGNAT (10.x, 100.64.x)
  porque en redes con CGNAT extenso no hay hop con IP pública del ISP.
- Si traceroute no logra identificar un hop válido: loguearlo como WARNING
  y omitir ese destino sin romper el resto del sistema.
- **Re-detección periódica:** cada 6 horas (configurable con
  `ISP_HOP_REFRESH_HOURS`), re-ejecutar traceroute. Si la IP cambió:
  actualizar metadata, cerrar evento abierto del hop viejo con
  `cierre_tipo='cambio_destino'`, y reiniciar la máquina de estados
  para la nueva IP.

**Latencia bajo carga (bufferbloat):**
- La función `_ping()` de connectivity.py retorna tanto el resultado
  UP/DOWN (exit code) como la latencia parseada de `time=X.XX ms` del
  stdout de `ping -c 1`. Cero tráfico adicional.
- Un objeto compartido `SpeedtestWindow` (en `checks/shared.py`) coordina
  entre `speed_loop` y `connectivity_loop`:
  1. `speed.py` llama `window.start()` antes de ejecutar speedtest.
  2. `connectivity.py`, en cada ciclo, si `window.is_active`, pasa la
     latencia del ping a 8.8.8.8 (la misma que ya usó para la máquina
     de estados) a `window.record_latency(ms)`.
  3. `speed.py` llama `window.finish()` al terminar → obtiene promedio
     de latencia bajo carga → guarda en `mediciones_velocidad.latencia_bajo_carga_ms`.
- El `SpeedtestWindow` usa `asyncio.Lock` como defensa en profundidad.

**Estado inicial de la máquina de estados:**
- Al iniciar el agente, todos los destinos arrancan en estado `UP` con
  `consecutive_failures = 0`. No se abre ningún evento de caída hasta que
  se cumpla el umbral de 2 fallos consecutivos normalmente.

**Chequeo DNS:**
- Resolver un dominio conocido (`google.com`) contra un servidor DNS
  específico (`8.8.8.8`) cada 5 segundos, midiendo tiempo de respuesta.
- Máquina de estados UP/DOWN con umbral de 2 fallos consecutivos
  (mismo patrón que conectividad).
- Si DNS falla mientras el ping al mismo destino sigue UP → evento
  distinto en tabla `eventos_dns`, separado de caídas de conectividad.
- Evidencia extra en el reporte: "el enlace estuvo arriba pero DNS
  falló X veces por Y minutos".

**Ancho de banda — lógica de deltas:**
- `bandwidth.py` guarda **deltas** (consumo por ventana de 5 min), no
  acumulados. vnstat devuelve contadores acumulados, así que el agente
  resta la lectura anterior de la actual.
- Primer ciclo: no inserta nada, solo guarda la lectura base.
- Si el acumulado actual < anterior (reset de vnstat): delta = 0.

**Info de interfaz de red:**
- El servidor va conectado por cable Ethernet (fibra → MikroTik → Ethernet).
- La columna `senal_wifi` en `eventos_caida` existe como nullable para
  extensibilidad futura, pero la lógica de captura de señal WiFi NO se
  implementa — siempre queda NULL.

**Test liviano y degradación de velocidad (Fase 0):**
- Loop cada 60s (`LIGHT_PROBE_INTERVAL_SECONDS`) en `checks/light_probe.py`.
- **Descomposición de Tiempos HTTP (PycURL)**: Mide con precisión de microsegundos: `dns_ms`, `tcp_connect_ms`, `tls_ms`, `ttfb_ms`, `transfer_ms`, `mbps_throughput` y `mbps_aproximado`.
- **Streams Paralelos (3 Conexiones TCP)**: Ejecuta `LIGHT_PROBE_STREAMS = 3` conexiones concurrentes.
  - Throughput agregado: `bytes_totales / transfer_wall_ms` (duración de transferencia del cuerpo en tiempo de pared).
  - Tiempos de fase: `max()` entre los 3 streams para `dns_ms`, `tcp_connect_ms`, `tls_ms` y `ttfb_ms` (peor caso para detección de degradaciones).
- **Semántica de `mbps_throughput` vs `mbps_aproximado` y Decisión de Diseño sobre Baseline**:
  - `mbps_throughput`: Mide la tasa de transferencia pura del payload excluyendo la fase de conexión HTTP (`transfer_ms`). Es el valor principal usado para evaluar degradaciones.
  - `mbps_aproximado`: Mide la tasa global sobre el tiempo de pared total acumulado (`total_ms`), manteniendo compatibilidad histórica.
  - **Limitación Consciente y Trade-off de Fase 0 (M-Lab NDT7 Fallback)**: En la sonda primaria (Cloudflare via PycURL multi-stream), `mbps_throughput` excluye el overhead de setup. En el fallback secundario de M-Lab (vía NDT7 WebSocket), `mbps_throughput` se asigna igual al throughput NDT7 (`mbps_aproximado`). Dado que M-Lab solo se activa excepcionalmente tras un fallo total de la sonda HTTP de Cloudflare, la abrumadora mayoría (>99%) de muestras sanas en estado normal derivan de Cloudflare. Por ende, la mediana del baseline no se ve contaminada en operación habitual. Mantener un baseline único en `COALESCE(mbps_throughput, mbps_aproximado)` es una decisión consciente para evitar la complejidad de segmentar baselines por proveedor en esta iteración.
- **Ventana de Cooldown Post-Speedtest (60s)**: Durante los 60 segundos posteriores a cada test oficial de Ookla, la muestra se toma e inserta con `muestra_valida = 0` para diagnóstico pero se excluye del baseline y no evalúa degradaciones (`LIGHT_PROBE_COOLDOWN_SECONDS`).
- **Regla de Baselines con Mediana e Hysteresis**:
  - Baseline de probe liviano usa `statistics.median` sobre las últimas 20 muestras sanas (`muestra_valida = 1`).
  - Se requiere un umbral de **2 fallos consecutivos (2 minutos)** para abrir un evento de degradación en la fuente liviana.
- **Optimización Futura (SaaS)**: Para escalar a múltiples clientes con consumo controlado, las descargas paralelas podrán usar HTTP Range requests dividiendo el archivo entre los 3 streams.



### 2. Base de datos — esquema mínimo

```sql
CREATE TABLE eventos_caida (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    destino TEXT NOT NULL,          -- '8.8.8.8', '1.1.1.1', 'gateway', 'isp_hop'
    origen TEXT NOT NULL,           -- 'red_local' | 'isp_primer_salto' | 'isp_general'
    inicio DATETIME NOT NULL,
    fin DATETIME,                   -- NULL mientras sigue caído
    duracion_segundos INTEGER,
    cierre_tipo TEXT,               -- 'recuperacion' | 'cambio_destino' | NULL (abierto)
    senal_wifi INTEGER              -- RSSI en dBm, NULL si es cable (siempre NULL en este deploy)
);

CREATE TABLE eventos_dns (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    dominio TEXT NOT NULL,           -- 'google.com'
    servidor_dns TEXT NOT NULL,      -- '8.8.8.8'
    inicio DATETIME NOT NULL,
    fin DATETIME,                    -- NULL mientras sigue fallando
    duracion_segundos INTEGER
);

CREATE TABLE mediciones_velocidad (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp DATETIME NOT NULL,
    descarga_mbps REAL,
    subida_mbps REAL,
    ping_ms REAL,
    latencia_bajo_carga_ms REAL     -- promedio de ping durante el speedtest (bufferbloat)
);

CREATE TABLE mediciones_probe_liviano (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp DATETIME NOT NULL,
    mbps_aproximado REAL NOT NULL,
    tiempo_respuesta_ms REAL NOT NULL
);

CREATE TABLE eventos_degradacion (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    severidad TEXT NOT NULL,         -- 'degradada' | 'critica'
    fuente TEXT NOT NULL,            -- 'oficial' | 'liviano'
    baseline_mbps REAL NOT NULL,
    velocidad_mbps REAL NOT NULL,
    inicio DATETIME NOT NULL,
    fin DATETIME,                    -- NULL mientras siga degradado
    duracion_segundos INTEGER
);

CREATE TABLE uso_ancho_banda (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp DATETIME NOT NULL,
    bytes_in INTEGER,              -- delta desde la última medición
    bytes_out INTEGER              -- delta desde la última medición
);

CREATE TABLE metadata (
    clave TEXT PRIMARY KEY,
    valor TEXT NOT NULL
);
```

### 3. Dashboard (`/dashboard`)
FastAPI, contenedor en red bridge normal, expuesto solo en `8085`. Lee de la
misma SQLite (montada como volumen compartido).

Conexión a SQLite vía URI `file:/app/data/monitor.db?mode=ro` con `uri=True`
(read-only a nivel de conexión, no de volumen Docker — necesario para WAL).
Patrón: conexión nueva por request (lecturas rápidas).

Endpoints mínimos:
- `GET /` — estado actual (UP/DOWN por destino) + dashboard visual
- `GET /api/status` — estado JSON
- `GET /caidas?desde=&hasta=` — historial de eventos de caída
- `GET /dns?desde=&hasta=` — historial de eventos DNS
- `GET /velocidad?desde=&hasta=` — historial de mediciones
- `GET /reporte/pdf?desde=&hasta=` — reporte exportable para el ISP

**Regla de reporte:** Los eventos de caída con `cierre_tipo='cambio_destino'`
deben marcarse aparte o excluirse de los cálculos de disponibilidad y tiempo
sin servicio, para no contaminar las métricas con cierres por rotación de hop.

### 4. docker-compose.yml — estructura esperada

```yaml
services:
  internet_agent:
    build: ./agente
    container_name: internet_monitor_agent
    network_mode: host
    restart: unless-stopped
    environment:
      - SPEED_INTERVAL_MINUTES=1
      - ISP_HOP_REFRESH_HOURS=6
    volumes:
      - ./data:/app/data
      - ./data/vnstat:/var/lib/vnstat
    healthcheck:
      test: ["CMD", "python", "-c", "import sqlite3; sqlite3.connect('/app/data/monitor.db').execute('SELECT 1 FROM eventos_caida LIMIT 0')"]
      interval: 5s
      timeout: 3s
      retries: 10
      start_period: 10s

  internet_dashboard:
    build: ./dashboard
    container_name: internet_monitor_dashboard
    restart: unless-stopped
    ports:
      - "8085:80"
    volumes:
      - ./data:/app/data
    depends_on:
      internet_agent:
        condition: service_healthy
```

## Checklist de revisión (para el supervisor)

Antes de aprobar cualquier PR/commit, verificar:

- [ ] El loop de ping corre cada 5s y no se bloquea por el loop de speedtest
- [ ] Se usa máquina de estados, no se guarda cada ping individual
- [ ] Umbral de 2 fallos consecutivos antes de marcar DOWN
- [ ] Se distingue `origen` con 3 niveles: `red_local`, `isp_primer_salto`, `isp_general`
- [ ] Para gateway, `origen` siempre es `"red_local"` por definición
- [ ] Estado inicial: todos los destinos en UP, consecutive_failures = 0
- [ ] bandwidth.py guarda deltas, no acumulados
- [ ] `network_mode: host` está presente en el servicio del agente
- [ ] Ambos servicios tienen `restart: unless-stopped`
- [ ] El puerto expuesto es 8085 y no colisiona con otros stacks del servidor
- [ ] La base SQLite persiste en `./data`, no dentro del contenedor
- [ ] vnstat persiste en `./data/vnstat`, no dentro del contenedor
- [ ] Dashboard conecta con URI `?mode=ro` + `uri=True`, volumen sin `:ro`
- [ ] No hay credenciales, tokens ni IPs internas hardcodeadas en el código
- [ ] El código sigue el esquema de base de datos definido arriba (o el
      cambio de esquema está documentado acá primero)
- [ ] `traceroute` está instalado en el Dockerfile del agente
- [ ] `isp_hop` se detecta al arranque y se re-detecta cada 6 horas
- [ ] Cierre de evento por cambio de hop usa `cierre_tipo='cambio_destino'`
- [ ] El check DNS corre en paralelo con máquina de estados propia
- [ ] El bufferbloat reutiliza la latencia del ping existente (sin ping extra)
- [ ] `SpeedtestWindow` protegido con `asyncio.Lock`
- [ ] `senal_wifi` es nullable y siempre NULL en este deploy (cable)
- [ ] Reporte PDF excluye/marca aparte cierres por `cambio_destino`
- [ ] Pruebas de verificación manual/scripts ad-hoc deben usar tablas separadas o etiquetar datos con `origen='test'` para prevenir el borrado accidental de datos reales de producción mediante filtros genéricos.

