#!/usr/bin/env python3
"""
servidor.py — Motor web del descargador de videos.

Inicia un pequeño servidor en tu computadora que hace las descargas.
La interfaz se puede usar de dos maneras:
  1. Abriendo http://localhost:8765 (el propio servidor la sirve).
  2. Desde la página publicada en GitHub Pages, que se conecta a este motor.

Uso:
  python servidor.py                  # solo esta computadora
  python servidor.py --red            # también otras computadoras de la misma red
  python servidor.py --salida "D:/Videos" --puerto 8765

No requiere librerías extra además de yt-dlp (y ffmpeg instalado).
"""

import argparse
import csv
import io
import json
import mimetypes
import os
import secrets
import shutil
import socket
import string
import subprocess
import sys
import threading
import time
import types
import uuid
import webbrowser
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE))
import descargador as core  # noqa: E402

VERSION = "1.0"
INTERFAZ = BASE / "docs" / "index.html"
CONFIG_DIR = Path.home() / ".descargador_videos"
MAX_ENLACES = 500

CALIDADES = {"original": None, "2160": 2160, "1440": 1440, "1080": 1080, "720": 720}
COMPRESION = {"auto", "h265", "h264", "no"}
PRESETS = {"veryfast", "medium", "slow"}
NAVEGADORES = {"", "chrome", "firefox", "edge", "brave", "opera", "safari"}


# ---------------------------------------------------------------- utilidades
def codigo_de_acceso():
    """Código que la página debe enviar para usar el motor. Se guarda para no cambiar en cada inicio."""
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    archivo = CONFIG_DIR / "codigo.txt"
    if archivo.exists():
        c = archivo.read_text(encoding="utf-8").strip()
        if c:
            return c
    alfabeto = "".join(ch for ch in string.ascii_uppercase + string.digits if ch not in "O0I1L")
    c = "".join(secrets.choice(alfabeto) for _ in range(8))
    archivo.write_text(c, encoding="utf-8")
    return c


def ip_local():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except OSError:
        return "127.0.0.1"


def abrir_en_sistema(ruta):
    if sys.platform.startswith("win"):
        os.startfile(str(ruta))  # noqa: S606
    elif sys.platform == "darwin":
        subprocess.Popen(["open", str(ruta)])
    else:
        subprocess.Popen(["xdg-open", str(ruta)])


# ---------------------------------------------------------------- trabajos
class Trabajo:
    def __init__(self, enlaces, cfg):
        self.id = uuid.uuid4().hex[:12]
        self.cfg = cfg
        self.creado = datetime.now()
        self.detener = threading.Event()
        self.lock = threading.Lock()
        self.terminado = False
        self.items = [{"enlace": u, "estado": "cola", "progreso": 0, "texto": "En cola",
                       "archivos": [], "nota": ""} for u in enlaces]

    def hook(self, i):
        def f(d):
            st = d.get("status")
            with self.lock:
                it = self.items[i]
                if st == "downloading":
                    total = d.get("total_bytes") or d.get("total_bytes_estimate")
                    tipo = "audio" if (d.get("info_dict") or {}).get("vcodec") == "none" else "video"
                    it["texto"] = f"Descargando {tipo}"
                    if total:
                        it["progreso"] = round(100 * d.get("downloaded_bytes", 0) / total)
                elif st == "finished":
                    it["texto"], it["progreso"] = "Uniendo audio y video", 100
                elif st == "comprimiendo":
                    it["texto"], it["progreso"] = "Optimizando peso", 100
        return f

    def ejecutar(self):
        def tarea(i):
            it = self.items[i]
            if self.detener.is_set():
                with self.lock:
                    it.update(estado="omitido", texto="Cancelado", nota="se detuvo antes de empezar")
                return
            with self.lock:
                it.update(estado="activo", texto="Conectando")
            try:
                res = core.procesar(it["enlace"], self.cfg, self.hook(i))
            except Exception as e:  # noqa: BLE001
                res = [{"estado": "ERROR", "archivo": "", "peso_MB": "", "nota": str(e)[:300]}]
            with self.lock:
                estados = {r["estado"] for r in res}
                if "OK" in estados:
                    it["estado"], it["texto"] = "ok", "Listo"
                elif "ERROR" in estados:
                    it["estado"], it["texto"] = "error", "Error"
                else:
                    it["estado"], it["texto"] = "omitido", "Omitido"
                it["nota"] = res[0]["nota"] if len(res) == 1 else f"{len(res)} videos"
                for r in res:
                    if r.get("archivo"):
                        it["archivos"].append({"ruta": r["archivo"], "nombre": Path(r["archivo"]).name,
                                               "peso_MB": r["peso_MB"], "nota": r["nota"]})

        with ThreadPoolExecutor(max_workers=self.cfg.hilos) as pool:
            list(pool.map(tarea, range(len(self.items))))
        self.terminado = True

    def publico(self):
        with self.lock:
            items = []
            for i, it in enumerate(self.items):
                items.append({**{k: v for k, v in it.items() if k != "archivos"},
                              "archivos": [{"n": j, "nombre": a["nombre"], "peso_MB": a["peso_MB"],
                                            "nota": a["nota"]} for j, a in enumerate(it["archivos"])]})
            return {"id": self.id, "terminado": self.terminado,
                    "deteniendo": self.detener.is_set(), "items": items}

    def reporte_csv(self):
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(["enlace", "estado", "archivo", "peso_MB", "nota"])
        with self.lock:
            for it in self.items:
                if it["archivos"]:
                    for a in it["archivos"]:
                        w.writerow([it["enlace"], it["estado"], a["nombre"], a["peso_MB"], a["nota"]])
                else:
                    w.writerow([it["enlace"], it["estado"], "", "", it["nota"]])
        return "\ufeff" + buf.getvalue()


TRABAJOS = {}


# ---------------------------------------------------------------- servidor HTTP
class Manejador(BaseHTTPRequestHandler):
    server_version = "DescargadorVideos/" + VERSION
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):  # silenciar el registro por petición
        pass

    # --- cabeceras comunes
    def _cors(self):
        origen = self.headers.get("Origin")
        if origen:
            self.send_header("Access-Control-Allow-Origin", origen)
            self.send_header("Vary", "Origin")
            self.send_header("Access-Control-Allow-Headers", "Content-Type, X-Codigo")
            self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
            # permite que una página https (GitHub Pages) hable con este motor local
            self.send_header("Access-Control-Allow-Private-Network", "true")

    def _json(self, datos, codigo=200):
        cuerpo = json.dumps(datos, ensure_ascii=False).encode("utf-8")
        self.send_response(codigo)
        self._cors()
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(cuerpo)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(cuerpo)

    def _error(self, codigo, mensaje):
        self._json({"error": mensaje}, codigo)

    def _autorizado(self, qs=None):
        c = self.headers.get("X-Codigo") or (qs or {}).get("codigo", [""])[0]
        return secrets.compare_digest(c.strip().upper(), self.server.codigo)

    def _es_local(self):
        return self.client_address[0] in ("127.0.0.1", "::1", "::ffff:127.0.0.1")

    def _leer_json(self):
        n = int(self.headers.get("Content-Length") or 0)
        if n > 1_000_000:
            raise ValueError("solicitud demasiado grande")
        return json.loads(self.rfile.read(n) or b"{}")

    # --- métodos
    def do_OPTIONS(self):  # noqa: N802
        self.send_response(204)
        self._cors()
        self.send_header("Access-Control-Max-Age", "600")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_GET(self):  # noqa: N802
        url = urlparse(self.path)
        qs = parse_qs(url.query)
        partes = [p for p in url.path.split("/") if p]

        if url.path in ("/", "/index.html"):
            return self._servir_interfaz()
        if partes == ["api", "estado"]:
            return self._json({"ok": True, "version": VERSION, "local": self._es_local(),
                               "ffmpeg": bool(shutil.which("ffmpeg")),
                               "yt_dlp": core.yt_dlp.version.__version__,
                               "autorizado": self._autorizado(qs)})
        if not self._autorizado(qs):
            return self._error(401, "Código de acceso incorrecto")

        if len(partes) == 3 and partes[:2] == ["api", "trabajos"]:
            t = TRABAJOS.get(partes[2])
            return self._json(t.publico()) if t else self._error(404, "Trabajo no encontrado")
        if len(partes) == 3 and partes[:2] == ["api", "reporte"]:
            t = TRABAJOS.get(partes[2])
            if not t:
                return self._error(404, "Trabajo no encontrado")
            cuerpo = t.reporte_csv().encode("utf-8")
            nombre = f"reporte_{t.creado:%Y%m%d_%H%M%S}.csv"
            return self._enviar_bytes(cuerpo, "text/csv; charset=utf-8", nombre)
        if len(partes) == 5 and partes[:2] == ["api", "archivo"]:
            return self._servir_archivo(partes[2], partes[3], partes[4])
        return self._error(404, "Ruta no encontrada")

    def do_POST(self):  # noqa: N802
        partes = [p for p in urlparse(self.path).path.split("/") if p]
        if not self._autorizado():
            return self._error(401, "Código de acceso incorrecto")
        try:
            datos = self._leer_json()
        except (ValueError, json.JSONDecodeError):
            return self._error(400, "Solicitud no válida")

        if partes == ["api", "trabajos"]:
            return self._crear_trabajo(datos)
        if len(partes) == 4 and partes[:2] == ["api", "trabajos"] and partes[3] == "detener":
            t = TRABAJOS.get(partes[2])
            if not t:
                return self._error(404, "Trabajo no encontrado")
            t.detener.set()
            return self._json({"ok": True})
        if partes == ["api", "abrir-carpeta"]:
            if not self._es_local():
                return self._error(403, "Solo disponible en la computadora donde corre el motor")
            abrir_en_sistema(self.server.salida)
            return self._json({"ok": True})
        return self._error(404, "Ruta no encontrada")

    # --- acciones
    def _crear_trabajo(self, datos):
        texto = "\n".join(datos.get("enlaces") or []) if isinstance(datos.get("enlaces"), list) \
            else str(datos.get("enlaces") or "")
        enlaces = core.sin_duplicados(core.URL_RE.findall(texto))[:MAX_ENLACES]
        if not enlaces:
            return self._error(400, "No se encontraron enlaces")
        o = datos.get("opciones") or {}
        calidad = str(o.get("calidad", "original"))
        comprimir = o.get("comprimir", "auto")
        preset = o.get("preset", "medium")
        nav = o.get("navegador", "") or ""
        cfg = types.SimpleNamespace(
            salida=self.server.salida,
            altura_max=CALIDADES.get(calidad),
            comprimir=comprimir if comprimir in COMPRESION else "auto",
            preset=preset if preset in PRESETS else "medium",
            crf=None,
            compatible=bool(o.get("compatible")),
            conservar_original=bool(o.get("conservar_original")),
            cookies_navegador=nav if nav in NAVEGADORES and nav else None,
            cookies=None,
            hilos=max(1, min(6, int(o.get("hilos") or 2))),
            listas=bool(o.get("listas")),
            sin_historial=bool(o.get("repetir")),
            umbral_bpp=0.05, ahorro_min=0.10)
        t = Trabajo(enlaces, cfg)
        TRABAJOS[t.id] = t
        threading.Thread(target=t.ejecutar, daemon=True).start()
        return self._json({"id": t.id, "total": len(enlaces)}, 201)

    def _servir_interfaz(self):
        try:
            cuerpo = INTERFAZ.read_bytes()
        except OSError:
            return self._error(500, "No se encontró docs/index.html junto a servidor.py")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(cuerpo)))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(cuerpo)

    def _enviar_bytes(self, cuerpo, tipo, nombre):
        self.send_response(200)
        self._cors()
        self.send_header("Content-Type", tipo)
        self.send_header("Content-Length", str(len(cuerpo)))
        self.send_header("Content-Disposition", _disposicion(nombre))
        self.end_headers()
        self.wfile.write(cuerpo)

    def _servir_archivo(self, tid, i, j):
        t = TRABAJOS.get(tid)
        try:
            a = t.items[int(i)]["archivos"][int(j)]
        except (AttributeError, ValueError, IndexError):
            return self._error(404, "Archivo no encontrado")
        ruta = Path(a["ruta"]).resolve()
        if self.server.salida not in ruta.parents or not ruta.is_file():
            return self._error(404, "Archivo no encontrado")
        tipo = mimetypes.guess_type(ruta.name)[0] or "application/octet-stream"
        self.send_response(200)
        self._cors()
        self.send_header("Content-Type", tipo)
        self.send_header("Content-Length", str(ruta.stat().st_size))
        self.send_header("Content-Disposition", _disposicion(ruta.name))
        self.end_headers()
        with open(ruta, "rb") as f:
            try:
                shutil.copyfileobj(f, self.wfile, 1024 * 1024)
            except (BrokenPipeError, ConnectionResetError):
                pass


def _disposicion(nombre):
    from urllib.parse import quote
    ascii_ = nombre.encode("ascii", "ignore").decode().replace('"', "") or "video"
    return f"attachment; filename=\"{ascii_}\"; filename*=UTF-8''{quote(nombre)}"


# ---------------------------------------------------------------- inicio
def main():
    p = argparse.ArgumentParser(description="Motor web del descargador de videos")
    p.add_argument("--puerto", type=int, default=8765)
    p.add_argument("--salida", default=str(Path.home() / "Videos" / "Descargas"),
                   help="carpeta donde se guardan los videos")
    p.add_argument("--red", action="store_true",
                   help="permitir que otras computadoras de la misma red usen este motor")
    p.add_argument("--no-abrir", action="store_true", help="no abrir el navegador al iniciar")
    a = p.parse_args()

    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        print("AVISO: no se encontró ffmpeg. Instálalo para descargar en máxima calidad:\n"
              "  Windows: winget install ffmpeg | macOS: brew install ffmpeg | Linux: sudo apt install ffmpeg\n")

    salida = Path(a.salida).expanduser().resolve()
    salida.mkdir(parents=True, exist_ok=True)
    host = "0.0.0.0" if a.red else "127.0.0.1"
    try:
        srv = ThreadingHTTPServer((host, a.puerto), Manejador)
    except OSError:
        sys.exit(f"El puerto {a.puerto} ya está en uso. ¿El motor ya está abierto en otra ventana?")
    srv.daemon_threads = True
    srv.salida = salida
    srv.codigo = codigo_de_acceso()

    local = f"http://localhost:{a.puerto}/?codigo={srv.codigo}"
    print("=" * 62)
    print("  Motor del descargador de videos — funcionando")
    print("=" * 62)
    print(f"  Abre en esta computadora:  {local}")
    if a.red:
        print(f"  Desde otras computadoras:  http://{ip_local()}:{a.puerto}/?codigo={srv.codigo}")
    print(f"  Código de acceso:          {srv.codigo}")
    print(f"  Los videos se guardan en:  {salida}")
    print("\n  Deja esta ventana abierta mientras uses la herramienta.")
    print("  Para apagar el motor, cierra la ventana o presiona Ctrl+C.")
    print("=" * 62)

    if not a.no_abrir:
        threading.Timer(1.0, lambda: webbrowser.open(local)).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\nMotor apagado.")


if __name__ == "__main__":
    main()
