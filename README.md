# TTKIA SDK

SDK oficial de Python para **TTKIA** – Telefónica Tech Knowledge Intelligence Assistant.

Permite consultar la base de conocimiento de TTKIA, mantener conversaciones, adjuntar ficheros y URLs, obtener feedback y gestionar sesiones desde cualquier script o terminal.

---

## Requisitos

- Python ≥ 3.9
- Una **API Key** válida (la generas tú mismo desde tu perfil en TTKIA)

## Instalación

El SDK se instala directamente desde el repositorio de GitHub:

```bash
# 1. Clona el repositorio
git clone https://github.com/xerar/TTKIA_SDK_v2.git
cd TTKIA_SDK_v2

# 2. Crea un entorno virtual
python -m venv .venv
source .venv/bin/activate   # Linux/macOS
# En Windows: .venv\Scripts\activate

# 3. Instala el SDK en modo editable
pip install -e .
```

Para incluir también las dependencias de los ejemplos:

```bash
pip install -e ".[examples]"
```

---

## Generar tu API Key

Cada usuario genera y gestiona sus propias API Keys desde la interfaz web de TTKIA.

### Paso a paso

1. Inicia sesión en TTKIA desde tu navegador.
2. Accede a tu **perfil** (icono de usuario o menú lateral).
3. En la sección **API Keys**, pulsa **Generate Key**.
4. Rellena los campos:
   - **Name**: un nombre descriptivo (ej. `mi-script-python`, `cli-portatil`).
   - **Description** *(opcional)*: para qué vas a usar esta key.
   - **Scopes**: permisos que tendrá la key. Por defecto se seleccionan todos:
     - `query` – enviar consultas
     - `conversations` – gestionar conversaciones
     - `feedback` – enviar valoraciones sobre respuestas
   - **Expiration**: por defecto 90 días.
5. Pulsa **Generate Key**.
6. **Copia la API Key** (`ttkia_sk_...`) que se muestra en pantalla.

> ⚠️ **La key solo se muestra una vez.** Si la pierdes, tendrás que revocarla y crear una nueva.

### Gestión de tus keys

Desde la misma sección de tu perfil puedes:

- **Desactivar/activar** temporalmente una key (sin revocarla).
- **Revocar** permanentemente una key que ya no necesites.
- Ver el **uso** (número de peticiones) y la **última fecha de uso** de cada key.

Puedes tener un máximo de **5 API Keys activas** simultáneamente.

Un administrador también puede revocar keys desde el panel de administración si es necesario.

---

## Configuración rápida

Hay dos formas de configurar la conexión: por **CLI** o por **variables de entorno**.

### Opción A – CLI (recomendada)

```bash
ttkia config --url https://ttkia.tu-empresa.com --api-key ttkia_sk_a1b2c3d4...
```

Esto guarda la configuración en `~/.ttkia/config.json` con permisos restringidos (solo tu usuario). A partir de aquí todos los comandos usarán estos datos automáticamente.

> 💡 **Truco**: cuando generas una API Key en TTKIA, la interfaz te muestra directamente el comando `ttkia config` listo para copiar y pegar.

Para verificar que funciona:

```bash
ttkia health
# 🟢 TTKIA: ok
```

### Opción B – Variables de entorno

```bash
export TTKIA_URL="https://ttkia.tu-empresa.com"
export TTKIA_API_KEY="ttkia_sk_a1b2c3d4..."
```

Las variables de entorno tienen prioridad sobre el fichero de configuración.

---

## Uso por línea de comandos (CLI)

Al instalar el SDK se registra el comando `ttkia` en tu terminal.

### Consulta rápida

```bash
ttkia ask "¿Cómo configuro una VPN site-to-site en Fortinet?"
```

### Modo conversación interactivo

```bash
ttkia chat
```

### Otros comandos útiles

```bash
ttkia health          # Estado del servidor
ttkia envs            # Listar entornos disponibles
ttkia history         # Ver historial de conversaciones
ttkia config --help   # Opciones de configuración
```

---

## Uso como librería Python

### Consulta básica

```python
from ttkia_sdk import TTKIAClient

client = TTKIAClient(
    base_url="https://ttkia.tu-empresa.com",
    api_key="ttkia_sk_..."
)

response = client.query("¿Qué es OSPF?")
print(response.text)
print(f"Confianza: {response.confidence:.0%}")
print(f"Fuentes: {len(response.sources)}")

client.close()
```

### Context Manager (recomendado)

```python
with TTKIAClient(base_url="...", api_key="ttkia_sk_...") as client:
    response = client.query("¿Qué es OSPF?")
    print(response.text)
```

### Continuidad de conversación

```python
with TTKIAClient(base_url="...", api_key="ttkia_sk_...") as client:
    r1 = client.query("¿Qué es BGP?")
    r2 = client.query("¿En qué se diferencia de OSPF?", conversation_id=r1.conversation_id)
    r3 = client.query("¿Cuál es mejor para mi DC?", conversation_id=r1.conversation_id)
```

### Opciones de consulta

```python
response = client.query(
    "Explica la arquitectura SDWAN",
    style="detailed",          # Estilo de respuesta
    prompt="expert",           # Plantilla de prompt
    web_search=True,           # Búsqueda web
    teacher_mode=True,         # Chain of Thought (ver aviso)
    sources=["sdwan.pdf"],     # Filtrar por documentos
    title="Investigación SDWAN",  # Título para nueva conversación
)
```

> ⚠️ **`teacher_mode` no surte efecto con API Key.** El backend lo fuerza a
> `False` en la ruta de API: el modo extendido enruta al modelo pesado y un
> bucle programático lo dispararía en cada llamada. Es una política de coste
> deliberada. El parámetro sigue en la firma —la ruta también la usa el
> planificador de tareas, que sí lo respeta— pero desde el SDK
> `response.thinking_process` volverá vacío. El CLI avisa si usas `--cot`.

---

## Adjuntos: ficheros, imágenes y URLs

El SDK permite adjuntar tres tipos de contenido a una conversación. El backend los procesa de forma diferente:

- **Documentos**: se embeben en Qdrant como vectores de la conversación.
- **Imágenes**: se guardan físicamente y se procesan con visión multimodal (AWS Bedrock).
- **URLs**: el contenido se descarga, se embebe en Qdrant como referencia web.

### Documentos (PDF, DOCX, TXT, CSV, PCAP, YAML, logs…)

El embedding ocurre en background. `upload_attachment` espera por defecto a que termine antes de devolver, así que la query siguiente ya encuentra el contenido indexado.

```python
with TTKIAClient() as client:
    cid = client.create_conversation("Config Review")

    # Sube y espera al embedding (status='completed')
    attachment = client.upload_attachment("/path/to/router_config.txt", cid)

    response = client.query(
        "Review this config and check if OSPF is correctly configured",
        conversation_id=cid,
        attached_files=[attachment],
        style="detailed",
    )
    print(response.text)

    # El adjunto persiste en la conversación: las queries siguientes
    # pueden referenciarlo sin volver a subirlo
    r2 = client.query("What changes would you recommend?", conversation_id=cid)
```

Si no quieres bloquear esperando al embedding (p.ej. ficheros muy grandes que vas a usar más tarde):

```python
attachment = client.upload_attachment(
    "/path/to/big.pdf", cid,
    wait_for_embedding=False,   # devuelve inmediatamente con status='processing'
)
```

### Imágenes (PNG, JPG, JPEG, GIF, WEBP, BMP)

Las imágenes se procesan con visión multimodal. El usuario necesita tener cuota multimodal disponible.

```python
with TTKIAClient() as client:
    cid = client.create_conversation("Network Diagram")
    attachment = client.upload_attachment("/path/to/topology.png", cid)

    response = client.query(
        f"Analyze the network diagram in {attachment['name']} "
        "and identify the topology type and any potential issues",
        conversation_id=cid,
        attached_files=[attachment],
    )
    print(response.text)
```

### URLs

El SDK indexa la URL vía `index_url` y la pasa en `attached_urls` (mismo flujo que el chat web).

El dominio debe estar en la lista permitida del backend (cisco.com, fortinet.com, paloaltonetworks.com, microsoft.com, telefonica.com, kubernetes.io, docker.com, etc.). Si no, `index_url` devuelve lista vacía.

```python
with TTKIAClient() as client:
    cid = client.create_conversation("CVE Research")

    # 1. Indexar la URL → metadata con path 'web://...'
    url_attachments = client.index_url(
        "https://sec.cloudapps.cisco.com/security/center/publicationListing.x",
        cid,
    )

    if not url_attachments:
        print("URL bloqueada (dominio no permitido)")
    else:
        # 2. Query con la URL en attached_urls
        response = client.query(
            "Summarize the main security advisory and list affected Cisco products",
            conversation_id=cid,
            attached_urls=url_attachments,
        )
        print(response.text)
```

### Varios adjuntos en una query

```python
with TTKIAClient() as client:
    cid = client.create_conversation("Incident Analysis")

    files = [
        client.upload_attachment("/logs/fw01_syslog.txt", cid),
        client.upload_attachment("/captures/traffic.pcap", cid),
        client.upload_attachment("/configs/fw01_config.txt", cid),
    ]
    urls = client.index_url("https://docs.fortinet.com/document/...", cid)

    response = client.query(
        "Correlate the syslog, pcap, firewall config and the vendor doc "
        "to identify the root cause of the connectivity drop at 14:32 UTC",
        conversation_id=cid,
        attached_files=files,
        attached_urls=urls,
        style="detailed",
    )
    print(response.text)
```

> **Nota**: Los formatos permitidos, tamaño máximo (100 MB) y límite de ficheros
> (5 por conversación) están definidos en el servidor.

---

### API asíncrona

Todos los métodos tienen su variante `async` con prefijo `a`:

```python
import asyncio

async def main():
    async with TTKIAClient(base_url="...", api_key="ttkia_sk_...") as client:
        response = await client.aquery("¿Qué es BGP?")
        envs = await client.aget_environments()
        health = await client.ahealth()

asyncio.run(main())
```

### Consultas concurrentes (batch)

```python
import asyncio

async def batch():
    async with TTKIAClient(base_url="...", api_key="ttkia_sk_...") as client:
        sem = asyncio.Semaphore(2)  # Máximo 2 consultas simultáneas

        async def ask(q):
            async with sem:
                return await client.aquery(q)

        results = await asyncio.gather(
            ask("¿Qué es OSPF?"),
            ask("¿Qué es BGP?"),
            ask("¿Qué es MPLS?"),
        )
        for r in results:
            print(f"{r.query}: {r.confidence:.0%}")

asyncio.run(batch())
```

### Feedback

```python
response = client.query("¿Cómo reseteo un FortiGate?")
result = client.send_feedback(
    conversation_id=response.conversation_id,
    message_id=response.message_id,
    score=1,   # 1 = positivo, -1 = negativo
)
print(result.message)
```

### Exportar conversación

```python
client.export_conversation(response.conversation_id, "sesion.zip")
```

---

## Objeto de respuesta (`QueryResponse`)

```python
response = client.query("¿Qué es OSPF?")

# Contenido
response.text                  # La respuesta generada
response.confidence            # Confianza: 0.0 – 1.0
response.is_error              # True si hubo error
response.error                 # Mensaje de error (si aplica)

# Identificadores (para seguimiento y feedback)
response.conversation_id       # ID de conversación
response.message_id            # ID del mensaje

# Fuentes
response.sources               # Todas las fuentes (docs + web)
response.docs                  # Solo fuentes documentales
response.webs                  # Solo fuentes web

# Tokens consumidos
response.token_usage.input_tokens
response.token_usage.output_tokens
response.token_usage.total

# Tiempos de ejecución
response.timing.total          # Segundos totales (propiedad, no método)
response.timing.get("retrieve")   # Segundos de una fase concreta, o None
response.timing.summary()      # {"retrieve": 0.5, "textual": 2.1, ..., "total": 2.9}

# Chain of Thought
# ⚠️ Vacío cuando se usa API Key: ver la nota de teacher_mode más abajo.
response.thinking_process      # Lista de pasos del razonamiento
```

---

## Métodos disponibles

| Método | Async | Descripción |
|--------|-------|-------------|
| `query()` | `aquery()` | Enviar consulta |
| `health()` | `ahealth()` | Estado del servicio |
| `get_environments()` | `aget_environments()` | Listar entornos de conocimiento |
| `get_prompts()` | `aget_prompts()` | Listar plantillas de prompt |
| `get_styles()` | `aget_styles()` | Listar estilos de respuesta |
| `list_conversations()` | `alist_conversations()` | Listar conversaciones |
| `get_conversation()` | `aget_conversation()` | Obtener conversación con mensajes |
| `create_conversation()` | `acreate_conversation()` | Crear nueva conversación |
| `delete_conversation()` | `adelete_conversation()` | Eliminar conversación |
| `send_feedback()` | `asend_feedback()` | Enviar feedback sobre una respuesta |
| `export_conversation()` | — | Exportar conversación como ZIP |
| `upload_attachment()` | `aupload_attachment()` | Subir fichero (espera al embedding) |
| `index_url()` | `aindex_url()` | Indexar una URL en la conversación |

---

## Gestión de errores

```python
from ttkia_sdk import (
    TTKIAClient, TTKIAError, AuthenticationError,
    RateLimitError, InsufficientScopeError,
)
import time

try:
    response = client.query("...")
except AuthenticationError:
    print("API Key inválida, expirada o revocada – genera una nueva desde tu perfil en TTKIA")
except InsufficientScopeError:
    print("La API Key no tiene el scope necesario para esta operación")
except RateLimitError as e:
    if e.retryable:
        print(f"Límite de peticiones alcanzado. Reintenta en {e.retry_after}s")
        time.sleep(e.retry_after)
    else:
        print(f"Presupuesto agotado: {e.message}")   # esperar no lo levanta
except TTKIAError as e:
    print(f"Error [{e.status_code}]: {e.message}")
```

### Los dos `429`

`RateLimitError` cubre dos situaciones que **no se reintentan igual**. Mira
siempre `retryable` antes de dormir:

| `retryable` | Causa | Qué hacer |
|---|---|---|
| `True` | Límite de frecuencia (30 peticiones/minuto por usuario) | Esperar `retry_after` segundos y reintentar |
| `False` | Presupuesto agotado de la API Key o del usuario | Parar. La ventana de gasto se mide en **días** |

Reintentar cada minuto contra un presupuesto agotado no levanta el tope y
cuesta una consulta de gasto en el servidor por intento. Si necesitas más
presupuesto, pídeselo a un administrador de coste.

---

## Resolución de problemas

| Problema | Causa probable | Solución |
|----------|---------------|----------|
| `❌ No TTKIA URL configured` | Falta configurar la URL | `ttkia config --url https://...` |
| `❌ No authentication configured` | Falta la API Key | `ttkia config --api-key ttkia_sk_...` |
| `❌ Authentication failed` | Key expirada o revocada | Genera una nueva desde tu perfil en TTKIA |
| `⏳ Rate limited` | Demasiadas peticiones | Espera el tiempo indicado y reintenta |
| `💸 Presupuesto agotado` | Tope de gasto de la key o del usuario | Esperar no sirve: pide ampliación a un administrador de coste |
| Timeout en respuestas | Consulta compleja o red lenta | `ttkia config --timeout 180` |
| Error de SSL | Certificado autofirmado | `ttkia config --no-ssl` (solo entornos internos) |
| `Maximum 5 active API Keys` | Has alcanzado el límite | Revoca alguna key antigua desde tu perfil |

---

## Ejemplos incluidos

El repositorio incluye ejemplos de uso en la carpeta `examples/`.

Estos ejemplos **no tienen credenciales hardcodeadas** y leen la configuración desde
variables de entorno (opcionalmente desde un fichero `.env`).

### Requisitos para ejecutar los ejemplos

Instala el SDK junto con las dependencias de ejemplos:

```bash
pip install -e ".[examples]"
```

Esto instalará python-dotenv, usado únicamente por los ejemplos.

### Configuración mediante .env

Crea un fichero `.env` en la carpeta `examples/`:

```
TTKIA_URL=https://ttkia.tu-empresa.com
TTKIA_API_KEY=ttkia_sk_...
```

También puedes usar variables de entorno directamente:

```bash
export TTKIA_URL=https://ttkia.tu-empresa.com
export TTKIA_API_KEY=ttkia_sk_...
```

### Ejecutar un ejemplo

```bash
# Ejemplo básico (por defecto)
python examples/examples.py

# Seleccionar un ejemplo concreto
TTKIA_EXAMPLE=conv python examples/examples.py
TTKIA_EXAMPLE=batch python examples/examples.py
TTKIA_EXAMPLE=attach python examples/examples.py
TTKIA_EXAMPLE=attach_url python examples/examples.py
```

Ejemplos disponibles: `simple`, `conv`, `cot`, `web`, `errors`, `batch`,
`incident`, `feedback`, `explore`, `attach`, `attach_image`, `attach_url`.

> ℹ️ El SDK no depende de python-dotenv.
> Solo los ejemplos y herramientas de desarrollo utilizan esta librería.

---

## Desarrollo

```bash
git clone https://github.com/xerar/TTKIA_SDK_v2.git
cd TTKIA_SDK_v2
pip install -e ".[dev]"
pytest tests/ -v
```

## Licencia

MIT