#!/usr/bin/env python3
"""
descargador.py — Descarga videos de YouTube, Facebook, Instagram y X (Twitter),
de forma individual o por lotes (CSV/TXT), en la mejor calidad disponible y con
compresión inteligente opcional para reducir el peso sin pérdida visible.

Requisitos:
  - Python 3.9 o superior
  - yt-dlp      ->  pip install -U yt-dlp
  - ffmpeg      ->  debe estar instalado y disponible en el PATH
"""

import argparse
import csv
import json
import re
import shutil
import subprocess
import sys
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

try:
    import yt_dlp
except ImportError:
    sys.exit("Falta yt-dlp. Instálalo con:  pip install -U yt-dlp")

URL_RE = re.compile(r"https?://[^\s,;\"'<>|]+", re.IGNORECASE)
_print_lock = threading.Lock()


def log(msg):
    with _print_lock:
        print(msg, flush=True)


# ----------------------------------------------------------------------------
# Lectura de enlaces
# ----------------------------------------------------------------------------
def leer_enlaces_de_archivo(ruta):
    """Extrae todos los enlaces de un CSV o TXT, sin importar la columna,
    el separador (coma, punto y coma, tabulación) ni si hay encabezados."""
    texto = Path(ruta).read_text(encoding="utf-8-sig", errors="ignore")
    return [u.rstrip(").]") for u in URL_RE.findall(texto)]


def sin_duplicados(lista):
    return list(dict.fromkeys(lista))


# ----------------------------------------------------------------------------
# Análisis y compresión con ffmpeg
# ----------------------------------------------------------------------------
def analizar(ruta):
    cmd = ["ffprobe", "-v", "error", "-print_format", "json",
           "-show_streams", "-show_format", str(ruta)]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        return None
    datos = json.loads(r.stdout)
    streams = datos.get("streams", [])
    v = next((s for s in streams if s.get("codec_type") == "video"), None)
    a = next((s for s in streams if s.get("codec_type") == "audio"), None)
    if not v:
        return None
    try:
        num, den = (v.get("avg_frame_rate") or "30/1").split("/")
        fps = float(num) / float(den) if float(den) else 30.0
    except ValueError:
        fps = 30.0
    fps = fps or 30.0
    duracion = float(datos.get("format", {}).get("duration") or 0)
    peso = Path(ruta).stat().st_size
    bitrate = peso * 8 / duracion if duracion else 0
    w, h = int(v.get("width") or 0), int(v.get("height") or 0)
    # bits por píxel por fotograma: indica qué tan "inflado" está el archivo
    bpp = bitrate / (w * h * fps) if (w and h) else 0
    return {"vcodec": v.get("codec_name"), "acodec": a.get("codec_name") if a else None,
            "ancho": w, "alto": h, "fps": fps, "bpp": bpp}


def comprimir(ruta, cfg):
    """Recodifica el video solo si vale la pena. Devuelve (ruta_final, nota)."""
    info = analizar(ruta)
    if not info:
        return ruta, "no se pudo analizar; se conserva tal cual"

    if cfg.comprimir == "auto":
        if info["vcodec"] in ("av1", "hevc"):
            return ruta, f"ya está en {info['vcodec'].upper()} (códec eficiente); sin recomprimir"
        if info["bpp"] < cfg.umbral_bpp:
            return ruta, f"ya está optimizado (bpp {info['bpp']:.3f}); sin recomprimir"

    codec = "h264" if cfg.comprimir == "h264" else "h265"
    crf = cfg.crf if cfg.crf is not None else (20 if codec == "h264" else 24)

    if codec == "h265":
        vargs = ["-c:v", "libx265", "-crf", str(crf), "-preset", cfg.preset,
                 "-tag:v", "hvc1", "-x265-params", "log-level=error"]
    else:
        vargs = ["-c:v", "libx264", "-crf", str(crf), "-preset", cfg.preset]

    aargs = ["-c:a", "copy"] if info["acodec"] == "aac" else ["-c:a", "aac", "-b:a", "192k"]

    tmp = ruta.with_name(ruta.stem + ".tmp_optimizando.mp4")
    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", str(ruta),
           "-map", "0:v:0", "-map", "0:a:0?", *vargs, "-pix_fmt", "yuv420p",
           *aargs, "-movflags", "+faststart", str(tmp)]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0 or not tmp.exists():
        tmp.unlink(missing_ok=True)
        return ruta, "falló la compresión; se conserva el original"

    nuevo, original = tmp.stat().st_size, ruta.stat().st_size
    if nuevo >= original * (1 - cfg.ahorro_min):
        tmp.unlink()
        return ruta, "la compresión no reducía lo suficiente; se conserva el original"

    if cfg.conservar_original:
        destino = ruta.with_name(ruta.stem + " [optimizado].mp4")
    else:
        destino = ruta.with_suffix(".mp4")
        ruta.unlink()
    tmp.replace(destino)
    ahorro = 100 * (1 - nuevo / original)
    return destino, f"comprimido a {codec.upper()} (CRF {crf}): {ahorro:.0f}% más liviano"


# ----------------------------------------------------------------------------
# Descarga con yt-dlp
# ----------------------------------------------------------------------------
def opciones_ytdlp(cfg, archivos, progreso=None):
    if cfg.altura_max:
        h = cfg.altura_max
        formato = f"bv*[height<={h}]+ba/b[height<={h}]/bv*+ba/b"
    else:
        formato = "bv*+ba/b"  # mejor video + mejor audio = calidad original

    plantilla = ("%(extractor_key)s/"
                 "%(upload_date>%Y-%m-%d|sin-fecha)s - "
                 "%(uploader,channel,uploader_id|desconocido).50B - "
                 "%(title,description|video).80B [%(id)s].%(ext)s")

    opts = {
        "format": formato,
        "merge_output_format": "mp4/mkv",
        "outtmpl": str(cfg.salida / plantilla),
        "windowsfilenames": True,
        "noplaylist": not cfg.listas,
        "retries": 10,
        "fragment_retries": 10,
        "concurrent_fragment_downloads": 4,
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        "post_hooks": [archivos.append],
    }
    if progreso:
        opts["progress_hooks"] = [progreso]
    if cfg.compatible:
        # Prioriza H.264 + AAC: ideal para Premiere, WhatsApp, PowerPoint, etc.
        opts["format_sort"] = ["res", "fps", "vcodec:h264", "acodec:aac"]
        opts["merge_output_format"] = "mp4"
    if not cfg.sin_historial:
        opts["download_archive"] = str(cfg.salida / ".historial_descargas.txt")
    if cfg.cookies_navegador:
        opts["cookiesfrombrowser"] = (cfg.cookies_navegador,)
    if cfg.cookies:
        opts["cookiefile"] = cfg.cookies
    return opts


def procesar(url, cfg, progreso=None):
    """progreso: función opcional que recibe los avances (la usa la interfaz gráfica)."""
    archivos = []
    resultados = []
    try:
        with yt_dlp.YoutubeDL(opciones_ytdlp(cfg, archivos, progreso)) as ydl:
            ydl.download([url])
    except Exception as e:  # noqa: BLE001
        msg = re.sub(r"\x1b\[[0-9;]*m", "", str(e)).replace("ERROR: ", "")
        return [{"enlace": url, "estado": "ERROR", "archivo": "", "peso_MB": "", "nota": msg[:300]}]

    if not archivos:
        return [{"enlace": url, "estado": "OMITIDO", "archivo": "", "peso_MB": "",
                 "nota": "ya se había descargado antes (ver .historial_descargas.txt)"}]

    for f in archivos:
        ruta = Path(f)
        nota = "calidad original"
        if cfg.comprimir != "no" and ruta.exists():
            if progreso:
                progreso({"status": "comprimiendo"})
            ruta, nota = comprimir(ruta, cfg)
        peso = ruta.stat().st_size / 1_048_576 if ruta.exists() else 0
        resultados.append({"enlace": url, "estado": "OK", "archivo": str(ruta),
                           "peso_MB": f"{peso:.1f}", "nota": nota})
    return resultados


# ----------------------------------------------------------------------------
# Interfaz
# ----------------------------------------------------------------------------
EJEMPLOS = """
ejemplos:
  python descargador.py https://www.youtube.com/watch?v=XXXX
  python descargador.py URL1 URL2 URL3
  python descargador.py --csv enlaces.csv
  python descargador.py --csv enlaces.csv --comprimir h264 --hilos 3
  python descargador.py --csv enlaces.csv --cookies-navegador chrome
  python descargador.py            (modo interactivo: pega enlaces o la ruta de un CSV)
"""


def modo_interactivo():
    print("\n=== Descargador de videos (YouTube · Facebook · Instagram · X) ===")
    print("Pega un enlace o arrastra un archivo .csv/.txt y presiona Enter.")
    print("Deja la línea vacía y presiona Enter para comenzar.\n")
    enlaces = []
    while True:
        linea = input("> ").strip().strip('"').strip("'")
        if not linea:
            break
        if Path(linea).is_file():
            nuevos = leer_enlaces_de_archivo(linea)
            print(f"  {len(nuevos)} enlaces leídos del archivo.")
            enlaces += nuevos
        else:
            encontrados = URL_RE.findall(linea)
            if encontrados:
                enlaces += encontrados
            else:
                print("  No parece un enlace ni un archivo válido.")
    return enlaces


def main():
    p = argparse.ArgumentParser(
        description="Descarga videos de YouTube, Facebook, Instagram y X en alta calidad.",
        formatter_class=argparse.RawDescriptionHelpFormatter, epilog=EJEMPLOS)
    p.add_argument("enlaces", nargs="*", help="uno o más enlaces de publicaciones")
    p.add_argument("-c", "--csv", action="append", default=[], metavar="ARCHIVO",
                   help="archivo .csv o .txt con enlaces (se puede repetir)")
    p.add_argument("-o", "--salida", default="descargas", help="carpeta de destino (por defecto: descargas)")
    p.add_argument("--comprimir", choices=["auto", "h265", "h264", "no"], default="auto",
                   help="auto: comprime solo si el archivo está inflado (por defecto)")
    p.add_argument("--crf", type=int, help="calidad de compresión; menor = más calidad (H.265: 24, H.264: 20)")
    p.add_argument("--preset", default="medium",
                   help="velocidad de compresión: veryfast, fast, medium, slow (más lento = más liviano)")
    p.add_argument("--altura-max", type=int, metavar="PX", help="limitar resolución, p. ej. 1080")
    p.add_argument("--compatible", action="store_true",
                   help="priorizar H.264/AAC en MP4 para máxima compatibilidad con editores")
    p.add_argument("--conservar-original", action="store_true",
                   help="guardar el original además de la versión optimizada")
    p.add_argument("--cookies-navegador", metavar="NAVEGADOR",
                   help="usar la sesión del navegador (chrome, firefox, edge, brave, safari)")
    p.add_argument("--cookies", metavar="ARCHIVO", help="archivo cookies.txt (formato Netscape)")
    p.add_argument("--hilos", type=int, default=2, help="descargas simultáneas (por defecto: 2)")
    p.add_argument("--listas", action="store_true", help="descargar listas de reproducción completas")
    p.add_argument("--sin-historial", action="store_true", help="volver a descargar aunque ya exista")
    p.add_argument("--umbral-bpp", type=float, default=0.05, help=argparse.SUPPRESS)
    p.add_argument("--ahorro-min", type=float, default=0.10, help=argparse.SUPPRESS)
    cfg = p.parse_args()

    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        sys.exit("No se encontró ffmpeg. Es necesario para unir video+audio en máxima calidad.\n"
                 "Windows: winget install ffmpeg   |   macOS: brew install ffmpeg   |   "
                 "Linux: sudo apt install ffmpeg")

    enlaces = list(cfg.enlaces)
    for archivo in cfg.csv:
        if not Path(archivo).is_file():
            sys.exit(f"No existe el archivo: {archivo}")
        enlaces += leer_enlaces_de_archivo(archivo)
    if not enlaces and not cfg.csv:
        enlaces = modo_interactivo()
    enlaces = sin_duplicados(enlaces)
    if not enlaces:
        sys.exit("No se encontraron enlaces para descargar.")

    cfg.salida = Path(cfg.salida).expanduser().resolve()
    cfg.salida.mkdir(parents=True, exist_ok=True)

    total = len(enlaces)
    log(f"\n{total} enlace(s) en cola · destino: {cfg.salida} · compresión: {cfg.comprimir}\n")

    todos = []
    with ThreadPoolExecutor(max_workers=max(1, cfg.hilos)) as pool:
        futuros = {pool.submit(procesar, u, cfg): u for u in enlaces}
        for i, fut in enumerate(as_completed(futuros), 1):
            for r in fut.result():
                todos.append(r)
                icono = {"OK": "✔", "OMITIDO": "↷", "ERROR": "✖"}[r["estado"]]
                detalle = (f"{Path(r['archivo']).name} ({r['peso_MB']} MB) — {r['nota']}"
                           if r["estado"] == "OK" else r["nota"])
                log(f"[{i}/{total}] {icono} {r['enlace']}\n        {detalle}")

    reporte = cfg.salida / f"reporte_{datetime.now():%Y%m%d_%H%M%S}.csv"
    with open(reporte, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=["enlace", "estado", "archivo", "peso_MB", "nota"])
        w.writeheader()
        w.writerows(todos)

    ok = sum(r["estado"] == "OK" for r in todos)
    err = sum(r["estado"] == "ERROR" for r in todos)
    omit = sum(r["estado"] == "OMITIDO" for r in todos)
    log(f"\nListo: {ok} descargado(s), {omit} omitido(s), {err} con error.")
    log(f"Reporte: {reporte}")
    if err:
        log("Consejo: si fallan Instagram/Facebook/X, prueba con --cookies-navegador chrome "
            "y actualiza yt-dlp con:  pip install -U yt-dlp")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit("\nCancelado por el usuario.")
