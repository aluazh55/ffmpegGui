#!/usr/bin/env python3
"""Audio Studio - small GUI around ffmpeg (mix / convert / trim / speed) and yt-dlp (download by link).

Requirements:
  * ffmpeg in PATH (or pick ffmpeg.exe in the top bar)
  * pip install -U yt-dlp      (only for the Download tab)
Run:  python audio_studio.py
"""
import importlib.util
import os
import queue
import shutil
import subprocess
import sys
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

BITRATES = ["128k", "192k", "256k", "320k"]
MEDIA = [
    ("Audio / Video", "*.mp3 *.wav *.m4a *.aac *.ogg *.flac *.webm *.mp4 *.mkv *.mov"),
    ("All files", "*.*"),
]
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)  # hides console popups on Windows
DEFAULT_DIR = r"D:\Downloads2\audio" if os.path.isdir(r"D:\Downloads2\audio") else os.path.expanduser("~")


def with_suffix(path, suffix, ext=None):
    base, old = os.path.splitext(path)
    return f"{base}{suffix}{ext or old}"


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Audio Studio")
        self.geometry("840x680")
        self.minsize(720, 580)
        self.q = queue.Queue()
        self.busy = False
        self.proc = None
        self.done_cb = None
        self.last_progress = False

        # --- top bar: ffmpeg path
        top = ttk.Frame(self, padding=(10, 8))
        top.pack(fill="x")
        ttk.Label(top, text="ffmpeg:").pack(side="left")
        self.ffmpeg = tk.StringVar(value="ffmpeg")
        ttk.Entry(top, textvariable=self.ffmpeg).pack(side="left", fill="x", expand=True, padx=6)
        ttk.Button(top, text="Browse…", command=self.pick_ffmpeg).pack(side="left")

        # --- tabs
        self.nb = ttk.Notebook(self)
        self.nb.pack(fill="x", padx=10)
        self.build_download()
        self.build_convert()
        self.build_mix()
        self.build_trim()
        self.build_speed()

        # --- progress + log
        bar = ttk.Frame(self, padding=(10, 8))
        bar.pack(fill="x")
        self.progress = ttk.Progressbar(bar, mode="indeterminate")
        self.progress.pack(side="left", fill="x", expand=True)
        self.stop_btn = ttk.Button(bar, text="Stop", command=self.stop, state="disabled")
        self.stop_btn.pack(side="left", padx=(8, 0))

        self.log = tk.Text(self, height=14, wrap="word", state="disabled",
                           bg="#161616", fg="#dcdcdc", font=("Consolas", 9))
        self.log.pack(fill="both", expand=True, padx=10, pady=(0, 10))

        self.after(100, self.poll)
        self.check_ffmpeg()

    # ------------------------------------------------------------------ helpers
    def ff(self):
        return self.ffmpeg.get().strip() or "ffmpeg"

    def check_ffmpeg(self):
        p = self.ff()
        if not (shutil.which(p) or os.path.isfile(p)):
            self.write("⚠ ffmpeg not found. Add it to PATH or pick ffmpeg.exe in the top bar.\n")

    def pick_ffmpeg(self):
        p = filedialog.askopenfilename(title="Select ffmpeg executable",
                                       filetypes=[("Executable", "*.exe"), ("All files", "*.*")])
        if p:
            self.ffmpeg.set(os.path.normpath(p))

    def tab(self, title):
        f = ttk.Frame(self.nb, padding=12)
        f.columnconfigure(1, weight=1)
        self.nb.add(f, text=title)
        return f

    def row(self, parent, r, label, var, kind=None, on_pick=None, save_ext=None):
        ttk.Label(parent, text=label).grid(row=r, column=0, sticky="w", pady=4)
        ttk.Entry(parent, textvariable=var).grid(row=r, column=1, sticky="ew", padx=6)
        if kind:
            ttk.Button(parent, text="Browse…",
                       command=lambda: self.browse(var, kind, on_pick, save_ext)).grid(row=r, column=2)

    def browse(self, var, kind, on_pick, save_ext):
        if kind == "open":
            p = filedialog.askopenfilename(filetypes=MEDIA)
        elif kind == "save":
            ext = save_ext() if callable(save_ext) else (save_ext or ".mp3")
            p = filedialog.asksaveasfilename(defaultextension=ext)
        else:
            p = filedialog.askdirectory()
        if p:
            var.set(os.path.normpath(p))
            if on_pick:
                on_pick(p)

    def ready(self, inputs, out):
        for p in inputs:
            if not p or not os.path.isfile(p):
                messagebox.showwarning("Missing file", f"File not found:\n{p or '(empty)'}")
                return False
        if not out:
            messagebox.showwarning("Missing output", "Choose an output file.")
            return False
        if any(os.path.abspath(out).lower() == os.path.abspath(p).lower() for p in inputs):
            messagebox.showwarning("Same file", "Output must be different from the input.")
            return False
        if os.path.exists(out) and not messagebox.askyesno("Overwrite?", f"{out}\nalready exists. Overwrite?"):
            return False
        os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
        return True

    @staticmethod
    def num(var, name):
        try:
            return float(var.get().replace(",", "."))
        except ValueError:
            messagebox.showwarning("Invalid number", f"{name} must be a number.")
            return None

    # ------------------------------------------------------------------ Download
    def build_download(self):
        f = self.tab("Download")
        self.dl_url = tk.StringVar()
        self.dl_dir = tk.StringVar(value=DEFAULT_DIR)
        self.dl_mode = tk.StringVar(value="Audio (mp3)")
        self.dl_playlist = tk.BooleanVar(value=False)
        self.row(f, 0, "Link:", self.dl_url)
        self.row(f, 1, "Save to:", self.dl_dir, "dir")
        ttk.Label(f, text="Format:").grid(row=2, column=0, sticky="w", pady=4)
        ttk.Combobox(f, textvariable=self.dl_mode, state="readonly", width=26,
                     values=["Audio (mp3)", "Video (mp4)", "Best (original, e.g. webm)"]
                     ).grid(row=2, column=1, sticky="w", padx=6)
        ttk.Checkbutton(f, text="Allow playlists", variable=self.dl_playlist
                        ).grid(row=3, column=1, sticky="w", padx=6)
        ttk.Button(f, text="Download", command=self.do_download).grid(row=4, column=1, sticky="w", padx=6, pady=8)

    def do_download(self):
        url = self.dl_url.get().strip()
        if not url:
            return messagebox.showwarning("Missing link", "Paste a link first.")
        if importlib.util.find_spec("yt_dlp") is None:
            return messagebox.showerror("yt-dlp missing", "Install it in a terminal:\n\npip install -U yt-dlp")
        out = self.dl_dir.get().strip() or DEFAULT_DIR
        os.makedirs(out, exist_ok=True)
        cmd = [sys.executable, "-m", "yt_dlp", "--newline", "-o", os.path.join(out, "%(title)s.%(ext)s")]
        if not self.dl_playlist.get():
            cmd.append("--no-playlist")
        ff = self.ff()
        if os.path.isfile(ff):
            cmd += ["--ffmpeg-location", ff]
        mode = self.dl_mode.get()
        if mode.startswith("Audio"):
            cmd += ["-x", "--audio-format", "mp3", "--audio-quality", "192K"]
        elif mode.startswith("Video"):
            cmd += ["-f", "bv*+ba/b", "--merge-output-format", "mp4"]
        cmd.append(url)
        self.run(cmd)

    # ------------------------------------------------------------------ Convert
    def build_convert(self):
        f = self.tab("Convert")
        self.cv_in, self.cv_out = tk.StringVar(), tk.StringVar()
        self.cv_fmt, self.cv_br = tk.StringVar(value="mp3"), tk.StringVar(value="192k")
        self.row(f, 0, "Input:", self.cv_in, "open",
                 lambda p: self.cv_out.set(with_suffix(p, "", "." + self.cv_fmt.get())))
        self.row(f, 1, "Output:", self.cv_out, "save", lambda: "." + self.cv_fmt.get())
        opts = ttk.Frame(f)
        opts.grid(row=2, column=1, sticky="w", padx=6, pady=4)
        ttk.Label(opts, text="Format:").pack(side="left")
        fmt = ttk.Combobox(opts, textvariable=self.cv_fmt, state="readonly", width=6,
                           values=["mp3", "wav", "m4a", "ogg", "mp4"])
        fmt.pack(side="left", padx=(4, 14))
        fmt.bind("<<ComboboxSelected>>", lambda e: self.cv_out.get() and self.cv_out.set(
            with_suffix(self.cv_out.get(), "", "." + self.cv_fmt.get())))
        ttk.Label(opts, text="Bitrate:").pack(side="left")
        ttk.Combobox(opts, textvariable=self.cv_br, values=BITRATES, state="readonly", width=6
                     ).pack(side="left", padx=4)
        ttk.Button(f, text="Convert", command=self.do_convert).grid(row=3, column=1, sticky="w", padx=6, pady=8)

    def do_convert(self):
        src, out = self.cv_in.get().strip(), self.cv_out.get().strip()
        if not self.ready([src], out):
            return
        if self.cv_fmt.get() == "mp4":
            cmd = [self.ff(), "-y", "-i", src, "-c:v", "libx264", "-c:a", "aac", "-b:a", self.cv_br.get(), out]
        else:
            cmd = [self.ff(), "-y", "-i", src, "-vn", "-b:a", self.cv_br.get(), out]
        self.run(cmd)

    # ------------------------------------------------------------------ Mix
    def build_mix(self):
        f = self.tab("Mix")
        self.mx_a, self.mx_b, self.mx_out = tk.StringVar(), tk.StringVar(), tk.StringVar()
        self.mx_va, self.mx_vb = tk.StringVar(value="1.0"), tk.StringVar(value="0.5")
        self.mx_dur, self.mx_br = tk.StringVar(value="first"), tk.StringVar(value="192k")

        def pick_a(p):
            if not self.mx_out.get():
                self.mx_out.set(with_suffix(p, "_mixed", ".mp3"))

        self.row(f, 0, "Track 1:", self.mx_a, "open", pick_a)
        ttk.Label(f, text="Volume 1:").grid(row=1, column=0, sticky="w")
        ttk.Spinbox(f, textvariable=self.mx_va, from_=0, to=4, increment=0.05, width=8
                    ).grid(row=1, column=1, sticky="w", padx=6)
        self.row(f, 2, "Track 2:", self.mx_b, "open")
        ttk.Label(f, text="Volume 2:").grid(row=3, column=0, sticky="w")
        ttk.Spinbox(f, textvariable=self.mx_vb, from_=0, to=4, increment=0.05, width=8
                    ).grid(row=3, column=1, sticky="w", padx=6)
        opts = ttk.Frame(f)
        opts.grid(row=4, column=1, sticky="w", padx=6, pady=4)
        ttk.Label(opts, text="Length follows:").pack(side="left")
        ttk.Combobox(opts, textvariable=self.mx_dur, values=["first", "longest", "shortest"],
                     state="readonly", width=9).pack(side="left", padx=(4, 14))
        ttk.Label(opts, text="Bitrate:").pack(side="left")
        ttk.Combobox(opts, textvariable=self.mx_br, values=BITRATES, state="readonly", width=6
                     ).pack(side="left", padx=4)
        self.row(f, 5, "Output:", self.mx_out, "save")
        btns = ttk.Frame(f)
        btns.grid(row=6, column=1, sticky="w", padx=6, pady=8)
        ttk.Button(btns, text="Mix", command=self.do_mix).pack(side="left")
        ttk.Button(btns, text="Use result as Track 1 (chain next mix)",
                   command=self.chain_mix).pack(side="left", padx=8)

    def do_mix(self):
        a, b, out = self.mx_a.get().strip(), self.mx_b.get().strip(), self.mx_out.get().strip()
        va, vb = self.num(self.mx_va, "Volume 1"), self.num(self.mx_vb, "Volume 2")
        if va is None or vb is None or not self.ready([a, b], out):
            return
        flt = (f"[0:a]volume={va:g}[a1];[1:a]volume={vb:g}[a2];"
               f"[a1][a2]amix=inputs=2:duration={self.mx_dur.get()}")
        self.run([self.ff(), "-y", "-i", a, "-i", b, "-filter_complex", flt, "-b:a", self.mx_br.get(), out])

    def chain_mix(self):
        out = self.mx_out.get().strip()
        if not os.path.isfile(out):
            return messagebox.showinfo("Nothing yet", "Run a mix first.")
        self.mx_a.set(out)
        self.mx_b.set("")
        self.mx_out.set(with_suffix(out, "_2"))

    # ------------------------------------------------------------------ Trim
    def build_trim(self):
        f = self.tab("Trim")
        self.tr_in, self.tr_out = tk.StringVar(), tk.StringVar()
        self.tr_s, self.tr_e = tk.StringVar(value="00:00:00"), tk.StringVar(value="00:10:00")
        self.tr_copy = tk.BooleanVar(value=True)
        self.row(f, 0, "Input:", self.tr_in, "open", lambda p: self.tr_out.set(with_suffix(p, "_cut")))
        self.row(f, 1, "Output:", self.tr_out, "save")
        t = ttk.Frame(f)
        t.grid(row=2, column=1, sticky="w", padx=6, pady=4)
        ttk.Label(t, text="Start:").pack(side="left")
        ttk.Entry(t, textvariable=self.tr_s, width=10).pack(side="left", padx=(4, 14))
        ttk.Label(t, text="End (empty = to the end):").pack(side="left")
        ttk.Entry(t, textvariable=self.tr_e, width=10).pack(side="left", padx=4)
        ttk.Checkbutton(f, text="Copy without re-encoding (instant, cut lands on nearest frame)",
                        variable=self.tr_copy).grid(row=3, column=1, sticky="w", padx=6)
        ttk.Button(f, text="Trim", command=self.do_trim).grid(row=4, column=1, sticky="w", padx=6, pady=8)

    def do_trim(self):
        src, out = self.tr_in.get().strip(), self.tr_out.get().strip()
        if not self.ready([src], out):
            return
        cmd = [self.ff(), "-y", "-ss", self.tr_s.get().strip() or "0"]
        if self.tr_e.get().strip():
            cmd += ["-to", self.tr_e.get().strip()]
        cmd += ["-i", src]
        cmd += ["-c", "copy"] if self.tr_copy.get() else ["-b:a", "192k"]
        self.run(cmd + [out])

    # ------------------------------------------------------------------ Speed
    def build_speed(self):
        f = self.tab("Speed")
        self.sp_in, self.sp_out = tk.StringVar(), tk.StringVar()
        self.sp_val, self.sp_br = tk.DoubleVar(value=0.9), tk.StringVar(value="192k")
        self.row(f, 0, "Input:", self.sp_in, "open", lambda p: self.sp_out.set(with_suffix(p, "_speed")))
        self.row(f, 1, "Output:", self.sp_out, "save")
        ttk.Label(f, text="Tempo:").grid(row=2, column=0, sticky="w")
        tk.Scale(f, variable=self.sp_val, from_=0.5, to=2.0, resolution=0.05, orient="horizontal",
                 length=320).grid(row=2, column=1, sticky="w", padx=6)
        ttk.Label(f, text="0.9 = slower, 1.0 = original, 1.25 = faster (pitch is kept)"
                  ).grid(row=3, column=1, sticky="w", padx=6)
        ttk.Button(f, text="Apply", command=self.do_speed).grid(row=4, column=1, sticky="w", padx=6, pady=8)

    def do_speed(self):
        src, out = self.sp_in.get().strip(), self.sp_out.get().strip()
        if not self.ready([src], out):
            return
        self.run([self.ff(), "-y", "-i", src, "-filter:a", f"atempo={self.sp_val.get():.2f}",
                  "-b:a", self.sp_br.get(), out])

    # ------------------------------------------------------------------ process runner
    def run(self, cmd, done=None):
        if self.busy:
            return messagebox.showinfo("Busy", "A job is already running.")
        self.busy = True
        self.done_cb = done
        self.write("\n$ " + " ".join(f'"{c}"' if " " in c else c for c in cmd) + "\n")
        self.progress.start(12)
        self.stop_btn.config(state="normal")
        threading.Thread(target=self.worker, args=(cmd,), daemon=True).start()

    def worker(self, cmd):
        env = dict(os.environ, PYTHONUTF8="1", PYTHONIOENCODING="utf-8")
        code = -1
        try:
            self.proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                         stdin=subprocess.DEVNULL, text=True, encoding="utf-8",
                                         errors="replace", creationflags=NO_WINDOW, env=env)
            for line in self.proc.stdout:
                self.q.put(("log", line))
            code = self.proc.wait()
        except FileNotFoundError:
            self.q.put(("log", f"Command not found: {cmd[0]}\n"))
        except Exception as e:  # noqa: BLE001
            self.q.put(("log", f"Error: {e}\n"))
        self.q.put(("done", code))

    def stop(self):
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
            self.write("■ Stopped by user\n")

    def poll(self):
        try:
            while True:
                kind, val = self.q.get_nowait()
                if kind == "log":
                    is_prog = val.lstrip().startswith(("size=", "frame=", "[download]"))
                    self.write(val, progress=is_prog)
                else:
                    self.progress.stop()
                    self.stop_btn.config(state="disabled")
                    self.busy, self.proc = False, None
                    self.write("✔ Done\n" if val == 0 else f"✖ Failed (exit code {val})\n")
                    if val == 0 and self.done_cb:
                        self.done_cb()
        except queue.Empty:
            pass
        self.after(100, self.poll)

    def write(self, text, progress=False):
        self.log.config(state="normal")
        if self.last_progress:  # overwrite previous progress line instead of spamming the log
            self.log.delete("end-1c -1l linestart", "end-1c")
        self.log.insert("end", text)
        self.last_progress = progress
        self.log.see("end")
        self.log.config(state="disabled")


if __name__ == "__main__":
    App().mainloop()
