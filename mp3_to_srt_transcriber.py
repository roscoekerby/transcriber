#!/usr/bin/env python3
"""
MP3 → SRT Transcriber
faster-whisper + pyannote speaker diarization
Produces speaker-labeled SRT files for YouTube captions.
"""

import os, re, json, threading, subprocess, sys
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from pathlib import Path
import numpy as np

HOST_NAME   = "Roscoe Kerby"
CONFIG_PATH = Path(__file__).parent / "transcriber_config.json"


# ── Config ────────────────────────────────────────────────────────────────────

def load_config() -> dict:
    if CONFIG_PATH.exists():
        try:
            return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"hf_token": "", "model_size": "medium", "roscoe_ref": ""}


def save_config(cfg: dict) -> None:
    CONFIG_PATH.write_text(json.dumps(cfg, indent=2), encoding="utf-8")


# ── Helpers ───────────────────────────────────────────────────────────────────

def extract_guest(mp3: Path) -> str:
    """'01 Benjamin Yeezus/ep.mp3'  →  'Benjamin Yeezus'"""
    raw = mp3.parent.name
    cleaned = re.sub(r"^\d+[\s.\-_]+", "", raw).strip()
    return cleaned or raw


def fmt_srt(t: float) -> str:
    t = max(0.0, t)
    h, rem = divmod(int(t), 3600)
    m, s   = divmod(rem, 60)
    ms     = min(999, int(round((t % 1) * 1000)))
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def find_mp3s(root: str):
    for dp, _, files in os.walk(root):
        for f in files:
            if f.lower().endswith(".mp3"):
                yield Path(dp) / f


def load_audio_via_ffmpeg(path: str, sr: int = 16000) -> tuple:
    """Load audio as float32 mono numpy array via ffmpeg."""
    cmd = ["ffmpeg", "-y", "-i", path, "-ar", str(sr), "-ac", "1",
           "-f", "f32le", "-"]
    r = subprocess.run(cmd, capture_output=True)
    if r.returncode != 0:
        raise RuntimeError(r.stderr.decode(errors="ignore")[-400:])
    return np.frombuffer(r.stdout, dtype=np.float32).copy(), sr


# ── Embedding helpers ─────────────────────────────────────────────────────────

def cosine(a: np.ndarray, b: np.ndarray) -> float:
    a, b = a.flatten(), b.flatten()
    return float(np.dot(a, b) / (np.linalg.norm(a) * np.linalg.norm(b) + 1e-9))


def _embed_chunk(inference, chunk: np.ndarray, sr: int) -> np.ndarray | None:
    import torch
    if len(chunk) < sr:          # skip clips shorter than 1 second
        return None
    wf = torch.tensor(chunk, dtype=torch.float32).unsqueeze(0)  # (1, N)
    try:
        result = inference({"waveform": wf, "sample_rate": sr})
        return np.array(result).flatten()
    except Exception:
        return None


def compute_speaker_embeddings(inference, audio: np.ndarray, sr: int,
                               diarization, max_per: int = 6) -> dict:
    """Average embeddings for each diarized speaker over their longest turns."""
    buckets: dict[str, list] = {}
    for turn, _, spk in diarization.itertracks(yield_label=True):
        dur = turn.end - turn.start
        if dur < 2.0:
            continue
        buckets.setdefault(spk, []).append((dur, turn.start, turn.end))

    result = {}
    for spk, segs in buckets.items():
        segs.sort(reverse=True)
        embs = []
        for _, s, e in segs[:max_per]:
            chunk = audio[int(s * sr): int(e * sr)]
            emb = _embed_chunk(inference, chunk, sr)
            if emb is not None:
                embs.append(emb)
        if embs:
            result[spk] = np.mean(embs, axis=0)
    return result


def who_is_roscoe(spk_embs: dict, ref_emb: np.ndarray) -> str:
    return max(spk_embs, key=lambda s: cosine(spk_embs[s], ref_emb))


# ── Transcript + diarization merge ────────────────────────────────────────────

def merge_segments(whisper_segs, diarization) -> list:
    """Assign a diarization speaker to each Whisper segment by max overlap."""
    out = []
    for seg in whisper_segs:
        overlaps: dict[str, float] = {}
        for turn, _, spk in diarization.itertracks(yield_label=True):
            o = max(0.0, min(seg.end, turn.end) - max(seg.start, turn.start))
            if o > 0:
                overlaps[spk] = overlaps.get(spk, 0.0) + o
        best = max(overlaps, key=overlaps.get) if overlaps else "UNKNOWN"
        out.append((seg.start, seg.end, best, seg.text.strip()))
    return out


def build_srt(segs: list, spk_map: dict) -> str:
    lines = []
    for i, (s, e, spk, txt) in enumerate(segs, 1):
        name = spk_map.get(spk, spk)
        lines += [str(i), f"{fmt_srt(s)} --> {fmt_srt(e)}", f"[{name}]: {txt}", ""]
    return "\n".join(lines)


# ── Speaker assignment dialog ─────────────────────────────────────────────────

class AssignDialog(tk.Toplevel):
    """Shows the first words of each detected speaker so the user can click
    which one is Roscoe. All other speakers get the guest name."""

    def __init__(self, parent, folder: str, guest: str, previews: list[tuple]):
        super().__init__(parent)
        self.title(f"Who is Roscoe?  —  {folder}")
        self.configure(bg="#1a1a2e")
        self.grab_set()
        self.resizable(False, False)
        self.result = None          # {spk_id: display_name}
        self._guest    = guest
        self._previews = previews   # [(spk_id, preview_text), ...]
        self._build()
        self.wait_window()

    def _build(self):
        BG, PANEL, ACCENT, TEXT, MUT = "#1a1a2e", "#16213e", "#e94560", "#eaeaea", "#8892b0"

        tk.Label(self, text="Identify speakers",
                 font=("Segoe UI", 13, "bold"), fg=TEXT, bg=BG).pack(pady=(16, 2))
        tk.Label(self, text="Click the card that belongs to  Roscoe Kerby",
                 font=("Segoe UI", 9), fg=MUT, bg=BG).pack(pady=(0, 10))

        for spk_id, preview in self._previews:
            card = tk.Frame(self, bg=PANEL, padx=14, pady=10, cursor="hand2",
                            highlightbackground=ACCENT, highlightthickness=1)
            card.pack(fill="x", padx=22, pady=5)

            tk.Label(card, text=spk_id, font=("Segoe UI", 9, "bold"),
                     fg=ACCENT, bg=PANEL).pack(anchor="w")
            snippet = (preview[:160] + "…") if len(preview) > 160 else preview
            tk.Label(card, text=f'"{snippet}"', font=("Segoe UI", 9),
                     fg=TEXT, bg=PANEL, wraplength=460,
                     justify="left").pack(anchor="w")

            def pick(sid=spk_id):
                self.result = {}
                for pid, _ in self._previews:
                    self.result[pid] = HOST_NAME if pid == sid else self._guest
                self.destroy()

            for w in [card] + card.winfo_children():
                w.bind("<Button-1>", lambda e, fn=pick: fn())

        tk.Button(self, text="Skip this episode", command=self.destroy,
                  font=("Segoe UI", 9), bg="#16213e", fg=MUT,
                  relief="flat", bd=0, pady=6).pack(pady=(8, 16))


# ── Main App ──────────────────────────────────────────────────────────────────

class TranscriberApp(tk.Tk):

    def __init__(self):
        super().__init__()
        self.title("MP3 → SRT Transcriber")
        self.geometry("780x700")
        self.resizable(True, True)
        self.configure(bg="#1a1a2e")
        self.cfg = load_config()
        self._build_ui()

    # ── UI ────────────────────────────────────────────────────────────────────

    def _build_ui(self):
        BG    = "#1a1a2e"
        PANEL = "#16213e"
        ACCENT= "#e94560"
        TEXT  = "#eaeaea"
        MUT   = "#8892b0"
        ENTRY = "#0f3460"

        # Title
        hdr = tk.Frame(self, bg=BG, pady=14)
        hdr.pack(fill="x")
        tk.Label(hdr, text="MP3  →  SRT  Transcriber",
                 font=("Segoe UI", 18, "bold"), fg=TEXT, bg=BG).pack()
        tk.Label(hdr,
                 text="faster-whisper + pyannote  •  Speaker-labeled SRT files for YouTube",
                 font=("Segoe UI", 9), fg=MUT, bg=BG).pack()

        # ── Settings panel ────────────────────────────────────────────────────
        settings = tk.Frame(self, bg=PANEL, pady=12)
        settings.pack(fill="x", padx=16, pady=(0, 8))
        tk.Label(settings, text="Settings", font=("Segoe UI", 9, "bold"),
                 fg=MUT, bg=PANEL).pack(anchor="w", padx=16, pady=(0, 6))

        def field_row(parent, label, var, browse_cmd=None, pw="", tip=""):
            row = tk.Frame(parent, bg=PANEL)
            row.pack(fill="x", padx=16, pady=2)
            tk.Label(row, text=label, font=("Segoe UI", 8, "bold"), fg=MUT,
                     bg=PANEL, width=20, anchor="w").pack(side="left")
            e = tk.Entry(row, textvariable=var, font=("Segoe UI", 9),
                         bg=ENTRY, fg=TEXT, insertbackground=TEXT,
                         relief="flat", bd=0, show=pw)
            e.pack(side="left", fill="x", expand=True, ipady=5, padx=(0, 6))
            if browse_cmd:
                tk.Button(row, text="Browse", command=browse_cmd,
                          font=("Segoe UI", 8, "bold"), bg=ACCENT, fg="white",
                          relief="flat", bd=0, padx=10, pady=4, cursor="hand2",
                          activebackground="#c73652").pack(side="right")
            if tip:
                tk.Label(parent, text="  " + tip, font=("Segoe UI", 7),
                         fg=MUT, bg=PANEL).pack(anchor="w", padx=16)

        self.folder_var = tk.StringVar()
        self.token_var  = tk.StringVar(value=self.cfg.get("hf_token", ""))
        self.ref_var    = tk.StringVar(value=self.cfg.get("roscoe_ref", ""))
        self.model_var  = tk.StringVar(value=self.cfg.get("model_size", "medium"))

        field_row(settings, "Episodes Folder", self.folder_var,
                  browse_cmd=lambda: self.folder_var.set(
                      filedialog.askdirectory(title="Select episodes root folder")))

        field_row(settings, "HuggingFace Token", self.token_var, pw="•",
                  tip="Required — get one at huggingface.co → Settings → Access Tokens"
                      "  |  Then accept terms at huggingface.co/pyannote/speaker-diarization-3.1")

        field_row(settings, "Roscoe Reference Audio", self.ref_var,
                  browse_cmd=lambda: self.ref_var.set(
                      filedialog.askopenfilename(
                          title="Pick any MP3/WAV clip of Roscoe speaking",
                          filetypes=[("Audio files", "*.mp3 *.wav *.m4a *.flac")])),
                  tip="Recommended — any clip where only Roscoe is speaking."
                      "  Enables fully automatic speaker ID across all episodes.")

        # Model size picker
        mf = tk.Frame(settings, bg=PANEL)
        mf.pack(fill="x", padx=16, pady=(8, 0))
        tk.Label(mf, text="Whisper Model", font=("Segoe UI", 8, "bold"),
                 fg=MUT, bg=PANEL, width=20, anchor="w").pack(side="left")
        for size in ["tiny", "base", "small", "medium", "large-v3"]:
            lbl = size if size != "medium" else "medium (recommended)"
            tk.Radiobutton(mf, text=lbl, variable=self.model_var, value=size,
                           font=("Segoe UI", 9), fg=TEXT, bg=PANEL,
                           selectcolor=ENTRY, activebackground=PANEL,
                           activeforeground=TEXT).pack(side="left", padx=5)

        # ── Progress ──────────────────────────────────────────────────────────
        pf = tk.Frame(self, bg=BG, padx=16, pady=6)
        pf.pack(fill="x")
        self.prog_lbl = tk.Label(pf, text="", font=("Segoe UI", 9), fg=MUT, bg=BG)
        self.prog_lbl.pack(anchor="w")
        self.prog_bar = ttk.Progressbar(pf, mode="determinate")
        self.prog_bar.pack(fill="x", pady=(4, 0))
        style = ttk.Style(self)
        style.theme_use("default")
        style.configure("TProgressbar", troughcolor=PANEL,
                        background=ACCENT, thickness=10)

        # ── Log ───────────────────────────────────────────────────────────────
        lf = tk.Frame(self, bg=BG, padx=16)
        lf.pack(fill="both", expand=True)
        tk.Label(lf, text="Log", font=("Segoe UI", 9, "bold"),
                 fg=MUT, bg=BG).pack(anchor="w")
        self.log_box = tk.Text(lf, font=("Consolas", 9), bg=PANEL, fg=TEXT,
                               insertbackground=TEXT, relief="flat", bd=0,
                               state="disabled")
        sb = tk.Scrollbar(self.log_box)
        sb.pack(side="right", fill="y")
        self.log_box.config(yscrollcommand=sb.set)
        sb.config(command=self.log_box.yview)
        self.log_box.pack(fill="both", expand=True, pady=(4, 0))
        self.log_box.tag_configure("ok",   foreground="#64ffda")
        self.log_box.tag_configure("warn", foreground="#ffd700")
        self.log_box.tag_configure("fail", foreground=ACCENT)
        self.log_box.tag_configure("info", foreground=MUT)

        # ── Convert button ────────────────────────────────────────────────────
        bf = tk.Frame(self, bg=BG, pady=10)
        bf.pack()
        self.btn = tk.Button(bf, text="Transcribe All MP3s → SRT",
                             command=self._start,
                             font=("Segoe UI", 11, "bold"), bg=ACCENT, fg="white",
                             relief="flat", bd=0, padx=32, pady=10, cursor="hand2",
                             activebackground="#c73652", activeforeground="white")
        self.btn.pack()

    # ── Logging helpers ───────────────────────────────────────────────────────

    def _log(self, msg: str, tag: str = ""):
        if not tag:
            if "[OK]" in msg:     tag = "ok"
            elif any(x in msg for x in ("[FAIL]", "[ERROR]")): tag = "fail"
            elif any(x in msg for x in ("[WARN]", "[SKIP]")):  tag = "warn"
            else:                  tag = "info"
        self.log_box.config(state="normal")
        self.log_box.insert("end", msg + "\n", tag)
        self.log_box.see("end")
        self.log_box.config(state="disabled")
        self.update_idletasks()

    def _prog(self, done: int, total: int):
        pct = int(done / total * 100) if total else 0
        self.prog_bar["value"] = pct
        self.prog_lbl.config(text=f"{done} / {total}  ({pct}%)")
        self.update_idletasks()

    # ── Start ─────────────────────────────────────────────────────────────────

    def _start(self):
        folder = self.folder_var.get().strip()
        token  = self.token_var.get().strip()
        ref    = self.ref_var.get().strip()
        size   = self.model_var.get()

        if not folder or not os.path.isdir(folder):
            messagebox.showerror("Error", "Select a valid episodes folder first.")
            return
        if not token:
            messagebox.showerror(
                "HuggingFace Token Required",
                "Enter your HuggingFace access token.\n\n"
                "1. Create one at huggingface.co → Settings → Access Tokens\n"
                "2. Accept the model terms at:\n"
                "   huggingface.co/pyannote/speaker-diarization-3.1\n"
                "   huggingface.co/pyannote/segmentation-3.0"
            )
            return

        self.cfg.update({"hf_token": token, "model_size": size, "roscoe_ref": ref})
        save_config(self.cfg)

        self.btn.config(state="disabled", text="Processing…")
        self.log_box.config(state="normal")
        self.log_box.delete("1.0", "end")
        self.log_box.config(state="disabled")
        self.prog_bar["value"] = 0

        threading.Thread(
            target=self._worker, args=(folder, token, ref, size), daemon=True
        ).start()

    # ── Worker thread ─────────────────────────────────────────────────────────

    def _worker(self, folder: str, token: str, roscoe_ref: str, model_size: str):
        try:
            # ── Dependency check ──────────────────────────────────────────────
            self._log("Checking dependencies…", "info")
            try:
                from faster_whisper import WhisperModel
            except ImportError:
                self._log(
                    "[ERROR] faster-whisper not installed.\n"
                    "Run install_dependencies.bat and restart the tool.", "fail"
                )
                return

            try:
                from pyannote.audio import Pipeline, Inference
                import torch
            except ImportError:
                self._log(
                    "[ERROR] pyannote.audio not installed.\n"
                    "Run install_dependencies.bat and restart the tool.", "fail"
                )
                return

            # ── Device ────────────────────────────────────────────────────────
            device       = "cuda" if torch.cuda.is_available() else "cpu"
            compute_type = "float16" if device == "cuda" else "int8"
            self._log(f"Device: {device.upper()}  |  Whisper model: {model_size}", "info")

            # ── Load Whisper ──────────────────────────────────────────────────
            self._log("Loading Whisper model (downloads once, then cached)…", "info")
            try:
                whisper = WhisperModel(model_size, device=device,
                                       compute_type=compute_type)
            except Exception as e:
                self._log(f"[ERROR] Whisper load failed: {e}", "fail"); return

            # ── Load Diarization pipeline ─────────────────────────────────────
            self._log(
                "Loading pyannote diarization pipeline\n"
                "(first run downloads ~2 GB — subsequent runs use cache)…", "info"
            )
            try:
                pipeline = Pipeline.from_pretrained(
                    "pyannote/speaker-diarization-3.1",
                    token=token
                )
                if device == "cuda":
                    pipeline.to(torch.device("cuda"))
            except Exception as e:
                self._log(
                    f"[ERROR] Could not load diarization pipeline:\n{e}\n\n"
                    "Make sure you have accepted the model terms at:\n"
                    "  https://huggingface.co/pyannote/speaker-diarization-3.1\n"
                    "  https://huggingface.co/pyannote/segmentation-3.0", "fail"
                )
                return

            # ── Compute Roscoe reference embedding using the pipeline itself ──
            # This guarantees the same embedding space (same model, same dims)
            # as the per-episode speaker_embeddings the pipeline returns.
            emb_inf    = None   # no longer used
            roscoe_emb = None

            if roscoe_ref and os.path.isfile(roscoe_ref):
                self._log("Computing Roscoe voice embedding via pipeline…", "info")
                try:
                    ref_audio, ref_sr = load_audio_via_ffmpeg(roscoe_ref)
                    ref_wf  = torch.tensor(ref_audio, dtype=torch.float32).unsqueeze(0)
                    ref_out = pipeline({"waveform": ref_wf, "sample_rate": ref_sr})
                    # Reference clip has one speaker — take the first (only) embedding
                    if ref_out.speaker_embeddings is not None and len(ref_out.speaker_embeddings) > 0:
                        roscoe_emb = np.array(ref_out.speaker_embeddings[0]).flatten()
                        self._log(
                            f"[OK] Roscoe voice embedding ready ({len(roscoe_emb)}-dim) — "
                            "automatic speaker ID enabled for all episodes.", "ok"
                        )
                    else:
                        self._log("[WARN] No embedding returned for reference clip — will use manual assignment.", "warn")
                except Exception as e:
                    self._log(
                        f"[WARN] Could not build Roscoe embedding: {e}\n"
                        "Falling back to manual assignment per episode.", "warn"
                    )
                    roscoe_emb = None
            else:
                self._log(
                    "No Roscoe reference audio provided — "
                    "will ask for speaker assignment on each episode.", "info"
                )

            # ── Find MP3s ─────────────────────────────────────────────────────
            mp3s  = list(find_mp3s(folder))
            total = len(mp3s)

            if total == 0:
                self._log("No MP3 files found in the selected folder.", "warn")
                return

            self._log(f"\nFound {total} MP3 file(s). Starting…\n", "info")

            ok_count = 0
            for idx, mp3 in enumerate(mp3s, 1):
                self._log(f"[{idx}/{total}]  {mp3.parent.name} / {mp3.name}", "info")
                self._prog(idx - 1, total)
                if self._process(mp3, whisper, pipeline, emb_inf, roscoe_emb):
                    ok_count += 1
                self._prog(idx, total)

            self._log(
                f"\n✓ Done.  {ok_count} / {total} SRT files created.", "ok"
            )

        except Exception as e:
            self._log(f"[ERROR] Unexpected error: {e}", "fail")
        finally:
            self.btn.config(state="normal", text="Transcribe All MP3s → SRT")

    # ── Per-file processing ───────────────────────────────────────────────────

    def _process(self, mp3: Path, whisper, pipeline, emb_inf, roscoe_emb) -> bool:
        import torch

        # 0. Skip if SRT already exists
        srt_path = mp3.with_suffix(".srt")
        if srt_path.exists():
            self._log(f"  [SKIP] SRT already exists — skipping.", "warn")
            return True

        # 1. Transcribe
        self._log("  → Transcribing…", "info")
        try:
            segs_gen, _ = whisper.transcribe(
                str(mp3), language="en", beam_size=5, vad_filter=True
            )
            whisper_segs = list(segs_gen)
        except Exception as e:
            self._log(f"  [FAIL] Whisper error: {e}", "fail")
            return False

        if not whisper_segs:
            self._log("  [WARN] No speech detected — skipping.", "warn")
            return False

        # 2. Load audio via ffmpeg (avoids torchcodec / AudioDecoder issues)
        try:
            audio, sr = load_audio_via_ffmpeg(str(mp3))
            audio_tensor = torch.tensor(audio, dtype=torch.float32).unsqueeze(0)
            audio_input  = {"waveform": audio_tensor, "sample_rate": sr}
        except Exception as e:
            self._log(f"  [FAIL] Audio load error: {e}", "fail")
            return False

        # 3. Diarize — pass pre-loaded tensor so pyannote skips its broken decoder
        self._log("  → Diarizing speakers…", "info")
        try:
            output       = pipeline(audio_input)
            diarization  = output.speaker_diarization   # Annotation with itertracks
            spk_embs_arr = output.speaker_embeddings    # ndarray (n_speakers, dim)
        except Exception as e:
            self._log(f"  [FAIL] Diarization error: {e}", "fail")
            return False

        unique_spks = sorted(diarization.labels())
        self._log(f"  → {len(unique_spks)} speaker(s): {', '.join(unique_spks)}", "info")

        # 4. Merge transcript segments with diarization
        segments = merge_segments(whisper_segs, diarization)
        guest    = extract_guest(mp3)

        # 5. Build speaker name map
        spk_map = None

        if len(unique_spks) == 1:
            spk_map = {unique_spks[0]: HOST_NAME}

        elif roscoe_emb is not None and spk_embs_arr is not None and len(spk_embs_arr) > 0:
            # Use embeddings already computed by the pipeline — no extra work needed
            try:
                spk_embs = {
                    label: spk_embs_arr[i]
                    for i, label in enumerate(unique_spks)
                }
                roscoe_spk = who_is_roscoe(spk_embs, roscoe_emb)
                spk_map = {
                    s: (HOST_NAME if s == roscoe_spk else guest)
                    for s in unique_spks
                }
                self._log(
                    f"  → Auto-ID: {roscoe_spk} = {HOST_NAME}, others = {guest}", "info"
                )
            except Exception as e:
                self._log(f"  [WARN] Auto-ID failed ({e}) — switching to manual.", "warn")

        # 5. Manual fallback — show assignment dialog on main thread
        if spk_map is None:
            previews_map: dict[str, str] = {}
            for _, _, spk, txt in segments:
                if spk not in previews_map:
                    previews_map[spk] = txt
                elif len(previews_map[spk].split()) < 25:
                    previews_map[spk] += " " + txt

            prev_list = [
                (s, previews_map.get(s, "(no speech detected)"))
                for s in unique_spks
            ]

            result_holder: list = [None]
            ev = threading.Event()

            def show_dialog():
                d = AssignDialog(self, mp3.parent.name, guest, prev_list)
                result_holder[0] = d.result
                ev.set()

            self.after(0, show_dialog)
            ev.wait()
            spk_map = result_holder[0]

        if spk_map is None:
            self._log(f"  [SKIP] Skipped (assignment cancelled).", "warn")
            return False

        # 6. Write SRT alongside the MP3
        srt_path.write_text(build_srt(segments, spk_map), encoding="utf-8")
        self._log(f"  [OK]   {srt_path.name}", "ok")
        return True


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    app = TranscriberApp()
    app.mainloop()
