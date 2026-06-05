# Traductor de PDFs

Prototipo local para leer un PDF con texto extraible, escoger un rango de paginas y comparar el texto original con una columna preparada para traduccion.

## Uso local

1. Instala dependencias:

```powershell
python -m pip install -r requirements.txt
```

2. Deja tus PDFs en la carpeta del proyecto.

3. Para traduccion real con OpenAI, crea un archivo `.env`:

```text
OPENAI_API_KEY=tu_clave
OPENAI_MODEL=gpt-5-mini
```

4. Inicia la app:

```powershell
python server.py
```

5. Abre:

```text
http://localhost:8765
```

## Estado actual

- Lista PDFs disponibles en la carpeta del proyecto.
- Extrae texto solo del rango de paginas elegido.
- Muestra original y traduccion en ventanas paralelas.
- Traduce el rango extraido con OpenAI cuando existe `OPENAI_API_KEY`.
- OCR queda pendiente para PDFs escaneados.
