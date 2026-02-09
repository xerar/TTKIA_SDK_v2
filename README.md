# TTKIA SDK v2.1

SDK oficial de Python para **TTKIA** – Telefónica Tech Knowledge Intelligence Assistant.

Permite consultar la base de conocimiento de TTKIA, mantener conversaciones, obtener feedback y gestionar sesiones desde cualquier script o terminal.

---

## Requisitos

- Python ≥ 3.9
- Una **API Key** válida (la generas tú mismo desde tu perfil en TTKIA)

## Instalación

```bash
pip install ttkia-sdk
```

> Si trabajas en un entorno sin acceso a PyPI, pide el paquete `.whl` a tu administrador e instálalo con `pip install ttkia_sdk-2.1.0-py3-none-any.whl`.

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

Con opciones adicionales:

```bash
ttkia ask "Explica SDWAN" --style detailed --cot --sources
```

| Flag | Descripción |
|------|-------------|
| `--style` | Estilo de respuesta: `concise` (por defecto), `detailed`, etc. |
| `--prompt` | Plantilla de prompt: `default`, `expert`, etc. |
| `--web` | Habilita búsqueda web complementaria |
| `--cot` | Habilita Chain of Thought (razonamiento paso a paso) |
| `--sources` | Muestra los documentos fuente usados en la respuesta |
| `--json` | Salida en formato JSON |
| `-c ID` | Continúa una conversación existente |

### Chat interactivo

```bash
ttkia chat
```

Dentro del chat dispones de estos comandos:

| Comando | Acción |
|---------|--------|
| `/quit` | Salir del chat |
| `/new` | Iniciar nueva conversación |
| `/export` | Exportar la conversación como ZIP |
| `/sources` | Activar/desactivar visualización de fuentes |
| `/web` | Activar/desactivar búsqueda web |
| `/id` | Mostrar el ID de la conversación actual |
| `/help` | Ver todos los comandos disponibles |

### Otros comandos útiles

```bash
ttkia health              # Verificar estado del servicio
ttkia envs                # Listar entornos de conocimiento disponibles
ttkia prompts             # Listar plantillas de prompt
ttkia styles              # Listar estilos de respuesta
ttkia history             # Ver conversaciones recientes
ttkia export <ID>         # Exportar una conversación como ZIP
```

### Referencia completa de `ttkia config`

```bash
ttkia config --url URL           # URL del servidor TTKIA
ttkia config --api-key KEY       # API Key (ttkia_sk_...)
ttkia config --token TOKEN       # App Token legacy (Bearer JWT)
ttkia config --timeout 180       # Timeout en segundos (por defecto: 120)
ttkia config --no-ssl            # Desactivar verificación SSL
ttkia config --ssl               # Reactivar verificación SSL
ttkia config                     # Mostrar configuración actual
```

---

## Uso como librería Python

### Consulta básica

```python
from ttkia_sdk import TTKIAClient

client = TTKIAClient(
    base_url="https://ttkia.tu-empresa.com",
    api_key="ttkia_sk_a1b2c3d4...",
)

response = client.query("¿Cómo configuro una VPN site-to-site en Fortinet?")
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
    teacher_mode=True,         # Chain of Thought
    sources=["sdwan.pdf"],     # Filtrar por documentos
    title="Investigación SDWAN",  # Título para nueva conversación
)
```

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
client.feedback(
    conversation_id=response.conversation_id,
    message_id=response.message_id,
    positive=True,
    comment="Respuesta precisa",
)
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
response.timing.total_seconds
response.timing.get_step("retrieve")
response.timing.summary()      # {"retrieve": 0.5, "textual": 2.1, ...}

# Chain of Thought (cuando teacher_mode=True)
response.thinking_process      # Lista de pasos del razonamiento
```

---

## Gestión de errores

```python
from ttkia_sdk import TTKIAError, AuthenticationError, RateLimitError
import time

try:
    response = client.query("...")
except AuthenticationError:
    print("API Key inválida, expirada o revocada – genera una nueva desde tu perfil en TTKIA")
except RateLimitError as e:
    print(f"Límite de peticiones alcanzado. Reintenta en {e.retry_after}s")
    time.sleep(e.retry_after)
except TTKIAError as e:
    print(f"Error [{e.status_code}]: {e.message}")
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
| `feedback()` | `afeedback()` | Enviar feedback sobre una respuesta |
| `export_conversation()` | `aexport_conversation()` | Exportar conversación como ZIP |

---

## Resolución de problemas

| Problema | Causa probable | Solución |
|----------|---------------|----------|
| `❌ No TTKIA URL configured` | Falta configurar la URL | `ttkia config --url https://...` |
| `❌ No authentication configured` | Falta la API Key | `ttkia config --api-key ttkia_sk_...` |
| `❌ Authentication failed` | Key expirada o revocada | Genera una nueva desde tu perfil en TTKIA |
| `⏳ Rate limited` | Demasiadas peticiones | Espera el tiempo indicado y reintenta |
| Timeout en respuestas | Consulta compleja o red lenta | `ttkia config --timeout 180` |
| Error de SSL | Certificado autofirmado | `ttkia config --no-ssl` (solo entornos internos) |
| `Maximum 5 active API Keys` | Has alcanzado el límite | Revoca alguna key antigua desde tu perfil |

---

## Desarrollo

```bash
git clone https://github.com/xerar/TTKIA_SDK.git
cd TTKIA_SDK
pip install -e ".[dev]"
pytest tests/ -v
```

## Licencia

MIT