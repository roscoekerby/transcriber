import os
import subprocess
import threading
import tkinter as tk
from tkinter import ttk, filedialog, messagebox


def find_mp4_files(root_folder):
    mp4_files = []
    for dirpath, _, filenames in os.walk(root_folder):
        for filename in filenames:
            if filename.lower().endswith(".mp4"):
                mp4_files.append(os.path.join(dirpath, filename))
    return mp4_files


def convert_mp4_to_mp3(mp4_path, log_callback, progress_callback, index, total):
    mp3_path = os.path.splitext(mp4_path)[0] + ".mp3"

    if os.path.exists(mp3_path):
        log_callback(f"[SKIP] Already exists: {os.path.basename(mp3_path)}")
        progress_callback(index, total)
        return True

    cmd = [
        "ffmpeg",
        "-i", mp4_path,
        "-vn",                  # no video
        "-acodec", "libmp3lame",
        "-ab", "192k",          # 192kbps bitrate
        "-ar", "44100",         # 44.1kHz sample rate
        "-y",                   # overwrite if somehow exists
        mp3_path
    ]

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True
        )
        if result.returncode == 0:
            log_callback(f"[OK]   {os.path.basename(mp4_path)} → {os.path.basename(mp3_path)}")
            progress_callback(index, total)
            return True
        else:
            log_callback(f"[FAIL] {os.path.basename(mp4_path)}")
            log_callback(f"       {result.stderr.strip().splitlines()[-1] if result.stderr else 'Unknown error'}")
            progress_callback(index, total)
            return False
    except FileNotFoundError:
        log_callback("[ERROR] ffmpeg not found. Please install FFmpeg and add it to PATH.")
        return False


class ConverterApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("MP4 → MP3 Converter")
        self.geometry("720x520")
        self.resizable(True, True)
        self.configure(bg="#1a1a2e")
        self._build_ui()

    def _build_ui(self):
        DARK_BG   = "#1a1a2e"
        PANEL_BG  = "#16213e"
        ACCENT    = "#e94560"
        TEXT      = "#eaeaea"
        MUTED     = "#8892b0"
        ENTRY_BG  = "#0f3460"

        # ── Title ──────────────────────────────────────────────
        title_frame = tk.Frame(self, bg=DARK_BG, pady=16)
        title_frame.pack(fill="x")
        tk.Label(
            title_frame, text="MP4  →  MP3  Converter",
            font=("Segoe UI", 18, "bold"), fg=TEXT, bg=DARK_BG
        ).pack()
        tk.Label(
            title_frame, text="Converts every MP4 in a folder (recursively). Originals are kept.",
            font=("Segoe UI", 9), fg=MUTED, bg=DARK_BG
        ).pack()

        # ── Folder picker ───────────────────────────────────────
        picker_frame = tk.Frame(self, bg=PANEL_BG, padx=16, pady=12)
        picker_frame.pack(fill="x", padx=16)

        tk.Label(picker_frame, text="Source Folder", font=("Segoe UI", 9, "bold"),
                 fg=MUTED, bg=PANEL_BG).pack(anchor="w")

        row = tk.Frame(picker_frame, bg=PANEL_BG)
        row.pack(fill="x", pady=(4, 0))

        self.folder_var = tk.StringVar()
        self.folder_entry = tk.Entry(
            row, textvariable=self.folder_var,
            font=("Segoe UI", 10), bg=ENTRY_BG, fg=TEXT,
            insertbackground=TEXT, relief="flat", bd=0
        )
        self.folder_entry.pack(side="left", fill="x", expand=True, ipady=6, padx=(0, 8))

        tk.Button(
            row, text="Browse", command=self._browse,
            font=("Segoe UI", 9, "bold"), bg=ACCENT, fg="white",
            relief="flat", bd=0, padx=14, pady=6, cursor="hand2",
            activebackground="#c73652", activeforeground="white"
        ).pack(side="right")

        # ── Progress ────────────────────────────────────────────
        prog_frame = tk.Frame(self, bg=DARK_BG, padx=16, pady=8)
        prog_frame.pack(fill="x")

        self.progress_label = tk.Label(
            prog_frame, text="", font=("Segoe UI", 9), fg=MUTED, bg=DARK_BG
        )
        self.progress_label.pack(anchor="w")

        self.progress_bar = ttk.Progressbar(prog_frame, mode="determinate", length=680)
        self.progress_bar.pack(fill="x", pady=(4, 0))

        style = ttk.Style(self)
        style.theme_use("default")
        style.configure("TProgressbar", troughcolor=PANEL_BG, background=ACCENT, thickness=10)

        # ── Log box ─────────────────────────────────────────────
        log_frame = tk.Frame(self, bg=DARK_BG, padx=16)
        log_frame.pack(fill="both", expand=True)

        tk.Label(log_frame, text="Log", font=("Segoe UI", 9, "bold"),
                 fg=MUTED, bg=DARK_BG).pack(anchor="w")

        self.log_box = tk.Text(
            log_frame, font=("Consolas", 9), bg=PANEL_BG, fg=TEXT,
            insertbackground=TEXT, relief="flat", bd=0, state="disabled"
        )
        self.log_box.pack(fill="both", expand=True, pady=(4, 0))

        scrollbar = tk.Scrollbar(self.log_box)
        scrollbar.pack(side="right", fill="y")
        self.log_box.config(yscrollcommand=scrollbar.set)
        scrollbar.config(command=self.log_box.yview)

        self.log_box.tag_configure("ok",   foreground="#64ffda")
        self.log_box.tag_configure("skip", foreground="#ffd700")
        self.log_box.tag_configure("fail", foreground=ACCENT)
        self.log_box.tag_configure("info", foreground=MUTED)

        # ── Convert button ──────────────────────────────────────
        btn_frame = tk.Frame(self, bg=DARK_BG, pady=12)
        btn_frame.pack()

        self.convert_btn = tk.Button(
            btn_frame, text="Convert All MP4s → MP3",
            command=self._start_conversion,
            font=("Segoe UI", 11, "bold"), bg=ACCENT, fg="white",
            relief="flat", bd=0, padx=32, pady=10, cursor="hand2",
            activebackground="#c73652", activeforeground="white"
        )
        self.convert_btn.pack()

    def _browse(self):
        folder = filedialog.askdirectory(title="Select folder containing MP4 files")
        if folder:
            self.folder_var.set(folder)

    def _log(self, message):
        self.log_box.config(state="normal")
        if message.startswith("[OK]"):
            tag = "ok"
        elif message.startswith("[SKIP]"):
            tag = "skip"
        elif message.startswith("[FAIL]") or message.startswith("[ERROR]"):
            tag = "fail"
        else:
            tag = "info"
        self.log_box.insert("end", message + "\n", tag)
        self.log_box.see("end")
        self.log_box.config(state="disabled")

    def _update_progress(self, done, total):
        pct = int((done / total) * 100) if total else 0
        self.progress_bar["value"] = pct
        self.progress_label.config(text=f"{done} / {total} files  ({pct}%)")
        self.update_idletasks()

    def _start_conversion(self):
        folder = self.folder_var.get().strip()
        if not folder or not os.path.isdir(folder):
            messagebox.showerror("Error", "Please select a valid folder first.")
            return

        self.convert_btn.config(state="disabled", text="Converting…")
        self.log_box.config(state="normal")
        self.log_box.delete("1.0", "end")
        self.log_box.config(state="disabled")
        self.progress_bar["value"] = 0
        self.progress_label.config(text="")

        thread = threading.Thread(target=self._run_conversion, args=(folder,), daemon=True)
        thread.start()

    def _run_conversion(self, folder):
        self._log(f"Scanning: {folder}")
        mp4_files = find_mp4_files(folder)
        total = len(mp4_files)

        if total == 0:
            self._log("No MP4 files found in this folder.")
            self.convert_btn.config(state="normal", text="Convert All MP4s → MP3")
            return

        self._log(f"Found {total} MP4 file(s). Starting conversion…\n")

        ok_count = 0
        for i, mp4_path in enumerate(mp4_files, start=1):
            success = convert_mp4_to_mp3(
                mp4_path,
                log_callback=self._log,
                progress_callback=self._update_progress,
                index=i,
                total=total
            )
            if success:
                ok_count += 1

        self._log(f"\nDone. {ok_count}/{total} converted successfully.")
        self.convert_btn.config(state="normal", text="Convert All MP4s → MP3")


if __name__ == "__main__":
    app = ConverterApp()
    app.mainloop()
