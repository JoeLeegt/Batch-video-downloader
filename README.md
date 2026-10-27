# Descargador de videos — YouTube · Facebook · Instagram · X

Herramienta para descargar videos de forma individual o por lotes (CSV), en la mejor
calidad disponible y con optimización inteligente del peso. Incluye interfaz gráfica
y versión de terminal. Funciona sobre [yt-dlp](https://github.com/yt-dlp/yt-dlp) y
[ffmpeg](https://ffmpeg.org/).

## Instalación (una sola vez)

1. **Python 3.9+** — https://www.python.org/downloads/ (en Windows, marca "Add Python to PATH").
2. **Descarga este repositorio** (botón verde *Code → Download ZIP*) o clónalo:
   `git clone https://github.com/JoeLeegt/descargador-videos.git`
3. **Dependencias de Python**: `pip install -U -r requirements.txt`
4. **ffmpeg**:
   - Windows: `winget install ffmpeg`
   - macOS: `brew install ffmpeg`
   - Linux: `sudo apt install ffmpeg`

## Interfaz gráfica (recomendada)

- **Windows:** doble clic en `descargador_gui.pyw` (o en `abrir_interfaz.bat`).
- **macOS / Linux:** `python3 descargador_gui.pyw`
  (en Linux puede hacer falta `sudo apt install python3-tk`; en macOS, el Python de python.org ya trae lo necesario).

Pega los enlaces (uno por línea) o pulsa **Cargar CSV / TXT**, elige las opciones y pulsa **Descargar**.
La tabla muestra el avance de cada video; con doble clic en una fila terminada se abre el archivo.
Si Instagram, Facebook o X fallan, elige tu navegador en **Sesión del navegador**.

## Versión web (desde el navegador o GitHub Pages)

La interfaz web funciona en dos partes: **la página** (que puede vivir en GitHub Pages) y
**el motor** (`servidor.py`), que corre en tu computadora y hace las descargas. Ninguna
página web puede descargar de estas plataformas por sí sola, por eso el motor es necesario.

**Encender el motor**

- Windows: doble clic en `iniciar_web.bat`.
- macOS: doble clic en `iniciar_web.command` (la primera vez: clic derecho → Abrir).
- Cualquier sistema: `python servidor.py`

Se abrirá el navegador con la herramienta lista. La ventana del motor muestra un
**código de acceso**; la página lo pide una sola vez y lo recuerda. Deja esa ventana
abierta mientras uses la herramienta. Los videos se guardan en `Videos/Descargas`
(cámbialo con `--salida "D:/Mis videos"`).

**Publicar la página en GitHub Pages**

1. En tu repositorio, ve a **Settings → Pages**.
2. En *Build and deployment*, elige **Deploy from a branch**, rama **main** y carpeta **/docs**. Guarda.
3. En un par de minutos estará en `https://JoeLeegt.github.io/descargador-videos/`.

Al abrir esa dirección con el motor encendido, la página se conecta sola a
`http://localhost:8765`. Funciona en Chrome, Edge y Firefox. En Safari usa directamente
la dirección local que muestra el motor.

**Para todo un equipo (misma red)**

Enciende el motor en una computadora de la oficina con `iniciar_web_red.bat`
(o `python servidor.py --red`). La ventana mostrará una dirección como
`http://192.168.1.20:8765/?codigo=…`; compártela con el equipo. Cada persona puede
bajar a su computadora los videos con el botón de cada archivo. Si Windows pregunta por
el firewall, permite el acceso en redes privadas. (Desde GitHub Pages no se puede usar un
motor de otra computadora porque el navegador lo bloquea; en ese caso usa la dirección local.)

## Uso desde la terminal

| Qué quieres hacer | Comando |
|---|---|
| Un video | `python descargador.py "https://youtu.be/XXXX"` |
| Varios videos | `python descargador.py URL1 URL2 URL3` |
| Lote desde CSV | `python descargador.py --csv enlaces.csv` |
| Modo interactivo | `python descargador.py` (o doble clic en `descargar.bat` en Windows) |

En Windows también puedes **arrastrar un CSV sobre `descargar.bat`**.

El CSV puede tener cualquier estructura: el programa detecta los enlaces en cualquier columna,
con coma o punto y coma, con o sin encabezados, y elimina duplicados.

Los videos se guardan en `descargas/<Plataforma>/` con el formato
`fecha - autor - título [id].mp4`, y al final se genera un `reporte_*.csv` con el
resultado de cada enlace.

## Calidad y peso

- Siempre se descarga **el mejor video + el mejor audio disponibles** (la calidad original que entrega la plataforma).
- `--comprimir auto` (predeterminado): analiza cada archivo y lo recomprime a H.265 **solo si está inflado**. Si ya viene en un códec eficiente (AV1, H.265) o con bitrate bajo, no lo toca. Si la compresión no ahorra al menos 10 %, se conserva el original.
- `--comprimir h264`: compresión con máxima compatibilidad (Premiere, WhatsApp, PowerPoint).
- `--comprimir no`: archivo exactamente como viene de la plataforma.
- `--crf 22` → más calidad / más peso; `--crf 28` → menos peso. `--preset slow` reduce más el peso a cambio de tiempo.
- `--conservar-original`: guarda ambas versiones.
- `--compatible`: prioriza H.264/AAC en MP4 desde la descarga (útil para edición).
- `--altura-max 1080`: limita la resolución (por ejemplo, para evitar 4K).

## Si algo falla

- **Actualiza yt-dlp** primero: `pip install -U yt-dlp`. Las plataformas cambian seguido.
- **Instagram, Facebook y a veces X/YouTube piden sesión iniciada**: usa
  `--cookies-navegador chrome` (o firefox, edge, brave). Debes tener la sesión abierta en ese navegador;
  en Chrome/Edge en Windows conviene cerrar el navegador antes de ejecutar.
- Publicaciones privadas o borradas no se pueden descargar.
- Los enlaces ya descargados se omiten (historial en `descargas/.historial_descargas.txt`).
  Usa `--sin-historial` para forzar.

## Uso responsable

Descarga solo contenido que tengas derecho a usar (propio, con permiso, o bajo uso legítimo
como cita periodística) y acredita a los autores. Los términos de servicio de algunas
plataformas restringen la descarga.

## Licencia

Código bajo licencia MIT (ver `LICENSE`). yt-dlp y ffmpeg tienen sus propias licencias.
