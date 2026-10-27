#!/usr/bin/env python3
"""
descargador_gui.pyw — Interfaz gráfica del descargador de videos
(YouTube · Facebook · Instagram · X).

En Windows basta con hacer doble clic en este archivo.
En macOS / Linux:  python3 descargador_gui.pyw
Requiere descargador.py en la misma carpeta, yt-dlp y ffmpeg.
"""

import os
import queue
import re
import shutil
import subprocess
import sys
import threading
import types
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

import tkinter as tk
from tkinter import filedialog, messagebox, ttk

sys.path.insert(0, str(Path(__file__).resolve().parent))
try:
    import descargador as core
except SystemExit as e:  # falta yt-dlp
    root = tk.Tk(); root.withdraw()
    messagebox.showerror("Falta un componente", str(e) + "\n\nAbre una terminal y ejecuta:\npip install -U yt-dlp")
    sys.exit(1)

# ---------------------------------------------------------------- opciones
CALIDADES = {"Original (máxima disponible)": None, "4K (2160p)": 2160, "1440p": 1440,
             "1080p (Full HD)": 1080, "720p (HD)": 720}
COMPRESION = {"Automática (recomendada)": "auto", "H.265 · máximo ahorro": "h265",
              "H.264 · máxima compatibilidad": "h264", "Sin compresión": "no"}
VELOCIDAD = {"Rápida": "veryfast", "Equilibrada": "medium", "Más liviana (lenta)": "slow"}
NAVEGADORES = ["Ninguno", "chrome", "firefox", "edge", "brave", "opera", "safari"]
PLATAFORMAS = {"youtube.com": "YouTube", "youtu.be": "YouTube", "facebook.com": "Facebook",
               "fb.watch": "Facebook", "instagram.com": "Instagram", "x.com": "X",
               "twitter.com": "X"}


def plataforma(url):
    host = urlparse(url).netloc.lower()
    for dominio, nombre in PLATAFORMAS.items():
        if host == dominio or host.endswith("." + dominio):
            return nombre
    return "Otra"


def abrir_en_sistema(ruta):
    ruta = str(ruta)
    if sys.platform.startswith("win"):
        os.startfile(ruta)  # noqa: S606
    elif sys.platform == "darwin":
        subprocess.Popen(["open", ruta])
    else:
        subprocess.Popen(["xdg-open", ruta])


# ---------------------------------------------------------------- ventana
class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Descargador de videos · YouTube · Facebook · Instagram · X")
        self.geometry("1040x720")
        self.minsize(860, 600)
        self.cola = queue.Queue()
        self.trabajando = False
        self.detener = threading.Event()
        self.filas = {}      # url -> id de fila en la tabla
        self.archivos = {}   # id de fila -> ruta del archivo final
        self._estilos()
        self._construir()
        self.after(100, self._revisar_cola)
        self.after(300, self._verificar_ffmpeg)

    # ---------- apariencia
    def _estilos(self):
        st = ttk.Style(self)
        if "clam" in st.theme_names():
            st.theme_use("clam")
        base = ("Segoe UI", 10) if sys.platform.startswith("win") else ("Helvetica", 11)
        self.option_add("*Font", base)
        st.configure("Titulo.TLabel", font=(base[0], 15, "bold"))
        st.configure("Sub.TLabel", foreground="#666")
        st.configure("Accion.TButton", font=(base[0], base[1] + 1, "bold"), padding=(18, 8))
        st.configure("Treeview", rowheight=26)
        st.configure("Treeview.Heading", font=(base[0], base[1], "bold"))

    # ---------- construcción
    def _construir(self):
        raiz = ttk.Frame(self, padding=14)
        raiz.pack(fill="both", expand=True)
        raiz.columnconfigure(0, weight=1)
        raiz.rowconfigure(3, weight=1)

        ttk.Label(raiz, text="Descargador de videos", style="Titulo.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Label(raiz, text="Pega uno o varios enlaces (uno por línea) o carga un archivo CSV.",
                  style="Sub.TLabel").grid(row=1, column=0, sticky="w", pady=(0, 8))

        # --- zona superior: enlaces + opciones
        arriba = ttk.Frame(raiz)
        arriba.grid(row=2, column=0, sticky="nsew")
        arriba.columnconfigure(0, weight=1)

        marco_enl = ttk.LabelFrame(arriba, text=" Enlaces ", padding=8)
        marco_enl.grid(row=0, column=0, sticky="nsew", padx=(0, 10))
        marco_enl.columnconfigure(0, weight=1)
        self.txt = tk.Text(marco_enl, height=7, wrap="none", relief="solid", borderwidth=1,
                           undo=True, font=("Consolas", 10) if sys.platform.startswith("win") else None)
        self.txt.grid(row=0, column=0, sticky="nsew")
        sb = ttk.Scrollbar(marco_enl, command=self.txt.yview)
        sb.grid(row=0, column=1, sticky="ns")
        self.txt.configure(yscrollcommand=sb.set)
        self.txt.bind("<<Modified>>", self._contar)

        bot = ttk.Frame(marco_enl)
        bot.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(6, 0))
        ttk.Button(bot, text="📋 Pegar", command=self._pegar).pack(side="left")
        ttk.Button(bot, text="📄 Cargar CSV / TXT…", command=self._cargar_csv).pack(side="left", padx=6)
        ttk.Button(bot, text="Limpiar", command=lambda: self.txt.delete("1.0", "end")).pack(side="left")
        self.lbl_cuenta = ttk.Label(bot, text="0 enlaces", style="Sub.TLabel")
        self.lbl_cuenta.pack(side="right")

        # opciones
        op = ttk.LabelFrame(arriba, text=" Opciones ", padding=8)
        op.grid(row=0, column=1, sticky="nsew")

        self.v_salida = tk.StringVar(value=str(Path.home() / "Videos" / "Descargas"))
        self.v_calidad = tk.StringVar(value=next(iter(CALIDADES)))
        self.v_compr = tk.StringVar(value=next(iter(COMPRESION)))
        self.v_vel = tk.StringVar(value="Equilibrada")
        self.v_nav = tk.StringVar(value="Ninguno")
        self.v_hilos = tk.IntVar(value=2)
        self.v_original = tk.BooleanVar()
        self.v_compat = tk.BooleanVar()
        self.v_listas = tk.BooleanVar()
        self.v_repetir = tk.BooleanVar()

        def fila(r, texto, widget):
            ttk.Label(op, text=texto).grid(row=r, column=0, sticky="w", pady=3, padx=(0, 8))
            widget.grid(row=r, column=1, sticky="ew", pady=3)

        carpeta = ttk.Frame(op)
        ttk.Entry(carpeta, textvariable=self.v_salida, width=26).pack(side="left", fill="x", expand=True)
        ttk.Button(carpeta, text="…", width=3, command=self._elegir_carpeta).pack(side="left", padx=(4, 0))
        fila(0, "Guardar en", carpeta)
        fila(1, "Calidad", ttk.Combobox(op, textvariable=self.v_calidad, values=list(CALIDADES), state="readonly"))
        fila(2, "Optimizar peso", ttk.Combobox(op, textvariable=self.v_compr, values=list(COMPRESION), state="readonly"))
        fila(3, "Velocidad", ttk.Combobox(op, textvariable=self.v_vel, values=list(VELOCIDAD), state="readonly"))
        fila(4, "Sesión del navegador", ttk.Combobox(op, textvariable=self.v_nav, values=NAVEGADORES, state="readonly"))
        fila(5, "Descargas simultáneas", ttk.Spinbox(op, from_=1, to=6, textvariable=self.v_hilos, width=5))

        checks = ttk.Frame(op)
        checks.grid(row=6, column=0, columnspan=2, sticky="w", pady=(6, 0))
        ttk.Checkbutton(checks, text="Guardar también el original", variable=self.v_original).pack(anchor="w")
        ttk.Checkbutton(checks, text="Formato compatible con editores (H.264/MP4)", variable=self.v_compat).pack(anchor="w")
        ttk.Checkbutton(checks, text="Descargar listas de reproducción completas", variable=self.v_listas).pack(anchor="w")
        ttk.Checkbutton(checks, text="Volver a descargar aunque ya exista", variable=self.v_repetir).pack(anchor="w")

        # --- tabla de resultados
        marco_tab = ttk.LabelFrame(raiz, text=" Progreso ", padding=8)
        marco_tab.grid(row=3, column=0, sticky="nsew", pady=(10, 0))
        marco_tab.columnconfigure(0, weight=1)
        marco_tab.rowconfigure(0, weight=1)
        cols = ("plat", "enlace", "estado", "peso", "nota")
        self.tabla = ttk.Treeview(marco_tab, columns=cols, show="headings", selectmode="browse")
        for c, t, w, s in [("plat", "Plataforma", 90, False), ("enlace", "Enlace", 300, True),
                           ("estado", "Estado", 200, False), ("peso", "Peso", 80, False),
                           ("nota", "Detalle", 320, True)]:
            self.tabla.heading(c, text=t)
            self.tabla.column(c, width=w, stretch=s, anchor="center" if c in ("plat", "peso") else "w")
        self.tabla.grid(row=0, column=0, sticky="nsew")
        sb2 = ttk.Scrollbar(marco_tab, command=self.tabla.yview)
        sb2.grid(row=0, column=1, sticky="ns")
        self.tabla.configure(yscrollcommand=sb2.set)
        self.tabla.tag_configure("ok", foreground="#1b7f3b")
        self.tabla.tag_configure("error", foreground="#b3261e")
        self.tabla.tag_configure("omitido", foreground="#7a6a00")
        self.tabla.bind("<Double-1>", self._abrir_fila)
        ttk.Label(marco_tab, text="Doble clic en una fila terminada para abrir el video.",
                  style="Sub.TLabel").grid(row=1, column=0, sticky="w", pady=(4, 0))

        # --- barra inferior
        abajo = ttk.Frame(raiz)
        abajo.grid(row=4, column=0, sticky="ew", pady=(10, 0))
        abajo.columnconfigure(1, weight=1)
        self.btn_ini = ttk.Button(abajo, text="⬇  Descargar", style="Accion.TButton", command=self._iniciar)
        self.btn_ini.grid(row=0, column=0)
        self.barra = ttk.Progressbar(abajo, mode="determinate")
        self.barra.grid(row=0, column=1, sticky="ew", padx=10)
        self.lbl_estado = ttk.Label(abajo, text="Listo.", width=28)
        self.lbl_estado.grid(row=0, column=2)
        self.btn_det = ttk.Button(abajo, text="Detener", command=self._detener, state="disabled")
        self.btn_det.grid(row=0, column=3, padx=(10, 6))
        ttk.Button(abajo, text="📂 Abrir carpeta", command=self._abrir_carpeta).grid(row=0, column=4)

    # ---------- acciones de la zona de enlaces
    def _enlaces(self):
        return core.sin_duplicados(core.URL_RE.findall(self.txt.get("1.0", "end")))

    def _contar(self, _=None):
        self.txt.edit_modified(False)
        n = len(self._enlaces())
        self.lbl_cuenta.config(text=f"{n} enlace{'s' if n != 1 else ''}")

    def _pegar(self):
        try:
            texto = self.clipboard_get()
        except tk.TclError:
            return
        actual = self.txt.get("1.0", "end").strip()
        self.txt.insert("end", ("\n" if actual else "") + texto.strip() + "\n")
        self._contar()

    def _cargar_csv(self):
        rutas = filedialog.askopenfilenames(title="Selecciona uno o más archivos con enlaces",
                                            filetypes=[("CSV o texto", "*.csv *.txt"), ("Todos", "*.*")])
        total = 0
        for r in rutas:
            nuevos = core.leer_enlaces_de_archivo(r)
            total += len(nuevos)
            if nuevos:
                actual = self.txt.get("1.0", "end").strip()
                self.txt.insert("end", ("\n" if actual else "") + "\n".join(nuevos) + "\n")
        if rutas:
            self._contar()
            if total == 0:
                messagebox.showwarning("Sin enlaces", "No se encontraron enlaces en el archivo seleccionado.")

    def _elegir_carpeta(self):
        d = filedialog.askdirectory(title="Carpeta de destino", initialdir=self.v_salida.get())
        if d:
            self.v_salida.set(d)

    def _abrir_carpeta(self):
        p = Path(self.v_salida.get()).expanduser()
        p.mkdir(parents=True, exist_ok=True)
        abrir_en_sistema(p)

    def _abrir_fila(self, _):
        sel = self.tabla.selection()
        if sel and sel[0] in self.archivos and Path(self.archivos[sel[0]]).exists():
            abrir_en_sistema(self.archivos[sel[0]])

    def _verificar_ffmpeg(self):
        if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
            messagebox.showwarning(
                "Falta ffmpeg",
                "No se encontró ffmpeg, que es necesario para descargar en máxima calidad y comprimir.\n\n"
                "Windows: winget install ffmpeg\nmacOS: brew install ffmpeg\nLinux: sudo apt install ffmpeg\n\n"
                "Después de instalarlo, vuelve a abrir este programa.")

    # ---------- descarga
    def _config(self):
        return types.SimpleNamespace(
            salida=Path(self.v_salida.get()).expanduser().resolve(),
            comprimir=COMPRESION[self.v_compr.get()], crf=None,
            preset=VELOCIDAD[self.v_vel.get()], altura_max=CALIDADES[self.v_calidad.get()],
            compatible=self.v_compat.get(), conservar_original=self.v_original.get(),
            cookies_navegador=None if self.v_nav.get() == "Ninguno" else self.v_nav.get(),
            cookies=None, hilos=max(1, min(6, int(self.v_hilos.get() or 2))),
            listas=self.v_listas.get(), sin_historial=self.v_repetir.get(),
            umbral_bpp=0.05, ahorro_min=0.10)

    def _iniciar(self):
        if self.trabajando:
            return
        enlaces = self._enlaces()
        if not enlaces:
            messagebox.showinfo("Sin enlaces", "Pega al menos un enlace o carga un archivo CSV.")
            return
        if not shutil.which("ffmpeg"):
            self._verificar_ffmpeg()
            return
        cfg = self._config()
        try:
            cfg.salida.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            messagebox.showerror("Carpeta no válida", str(e))
            return

        self.tabla.delete(*self.tabla.get_children())
        self.filas.clear()
        self.archivos.clear()
        for u in enlaces:
            self.filas[u] = self.tabla.insert("", "end", values=(plataforma(u), u, "En cola", "", ""))
        self.total, self.hechos, self.resultados = len(enlaces), 0, []
        self.barra.configure(maximum=self.total, value=0)
        self.trabajando = True
        self.detener.clear()
        self.btn_ini.configure(state="disabled")
        self.btn_det.configure(state="normal")
        self.lbl_estado.config(text=f"0 de {self.total} completados")
        threading.Thread(target=self._trabajo, args=(enlaces, cfg), daemon=True).start()

    def _detener(self):
        self.detener.set()
        self.btn_det.configure(state="disabled")
        self.lbl_estado.config(text="Deteniendo… (terminando las descargas en curso)")

    def _hook(self, url):
        ultimo = {"txt": None}

        def hook(d):
            st = d.get("status")
            if st == "downloading":
                total = d.get("total_bytes") or d.get("total_bytes_estimate")
                tipo = "audio" if (d.get("info_dict") or {}).get("vcodec") == "none" else "video"
                pct = f" {100 * d.get('downloaded_bytes', 0) / total:.0f}%" if total else "…"
                txt = f"Descargando {tipo}{pct}"
            elif st == "finished":
                txt = "Uniendo audio y video…"
            elif st == "comprimiendo":
                txt = "Optimizando peso…"
            else:
                return
            if txt != ultimo["txt"]:
                ultimo["txt"] = txt
                self.cola.put(("estado", url, txt))
        return hook

    def _trabajo(self, enlaces, cfg):
        def tarea(u):
            if self.detener.is_set():
                return [{"enlace": u, "estado": "OMITIDO", "archivo": "", "peso_MB": "", "nota": "cancelado"}]
            self.cola.put(("estado", u, "Iniciando…"))
            return core.procesar(u, cfg, self._hook(u))

        with ThreadPoolExecutor(max_workers=cfg.hilos) as pool:
            futuros = {pool.submit(tarea, u): u for u in enlaces}
            for fut in as_completed(futuros):
                u = futuros[fut]
                try:
                    res = fut.result()
                except Exception as e:  # noqa: BLE001
                    res = [{"enlace": u, "estado": "ERROR", "archivo": "", "peso_MB": "", "nota": str(e)[:300]}]
                self.cola.put(("resultado", u, res))
        self.cola.put(("fin", cfg, None))

    # ---------- comunicación con la interfaz (hilo principal)
    def _revisar_cola(self):
        try:
            while True:
                tipo, a, b = self.cola.get_nowait()
                if tipo == "estado" and a in self.filas:
                    self.tabla.set(self.filas[a], "estado", b)
                elif tipo == "resultado":
                    self._mostrar_resultado(a, b)
                elif tipo == "fin":
                    self._terminar(a)
        except queue.Empty:
            pass
        self.after(100, self._revisar_cola)

    def _mostrar_resultado(self, url, res):
        self.resultados += res
        fila = self.filas.get(url)
        etiquetas = {"OK": ("✔ Completado", "ok"), "ERROR": ("✖ Error", "error"),
                     "OMITIDO": ("↷ Omitido", "omitido")}
        for i, r in enumerate(res):
            texto, tag = etiquetas[r["estado"]]
            peso = f"{r['peso_MB']} MB" if r["peso_MB"] else ""
            nota = r["nota"]
            if r["estado"] == "OK" and len(res) > 1:
                nota = f"{Path(r['archivo']).name} — {nota}"
            vals = (plataforma(url), url, texto, peso, nota)
            if i == 0:
                self.tabla.item(fila, values=vals, tags=(tag,))
                destino = fila
            else:  # listas de reproducción: una fila por video
                destino = self.tabla.insert("", self.tabla.index(fila) + i, values=vals, tags=(tag,))
            if r["archivo"]:
                self.archivos[destino] = r["archivo"]
        self.hechos += 1
        self.barra.configure(value=self.hechos)
        self.lbl_estado.config(text=f"{self.hechos} de {self.total} completados")

    def _terminar(self, cfg):
        self.trabajando = False
        self.btn_ini.configure(state="normal")
        self.btn_det.configure(state="disabled")
        import csv
        reporte = cfg.salida / f"reporte_{datetime.now():%Y%m%d_%H%M%S}.csv"
        try:
            with open(reporte, "w", newline="", encoding="utf-8-sig") as f:
                w = csv.DictWriter(f, fieldnames=["enlace", "estado", "archivo", "peso_MB", "nota"])
                w.writeheader()
                w.writerows(self.resultados)
        except OSError:
            reporte = None
        ok = sum(r["estado"] == "OK" for r in self.resultados)
        err = sum(r["estado"] == "ERROR" for r in self.resultados)
        omit = sum(r["estado"] == "OMITIDO" for r in self.resultados)
        self.lbl_estado.config(text=f"Terminado: {ok} ✔  {omit} ↷  {err} ✖")
        msg = f"Descargados: {ok}\nOmitidos: {omit}\nCon error: {err}"
        if reporte:
            msg += f"\n\nReporte guardado en:\n{reporte.name}"
        if err:
            plats = {plataforma(r["enlace"]) for r in self.resultados if r["estado"] == "ERROR"}
            if plats & {"Instagram", "Facebook", "X"} and self.v_nav.get() == "Ninguno":
                msg += ("\n\nConsejo: Instagram, Facebook y X suelen pedir sesión iniciada. "
                        "Elige tu navegador en «Sesión del navegador» y vuelve a intentar.")
            msg += "\n\nSi el error persiste, actualiza yt-dlp:  pip install -U yt-dlp"
            messagebox.showwarning("Descarga terminada con errores", msg)
        else:
            messagebox.showinfo("Descarga terminada", msg)


if __name__ == "__main__":
    App().mainloop()
