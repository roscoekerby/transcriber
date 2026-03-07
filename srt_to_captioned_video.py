#!/usr/bin/env python3
"""
Video Caption Burner
Adds styled captions to any video file.
- Live preview with drag-to-position
- Uses existing SRT, auto-detects, or generates via Whisper
- Word replacements, style presets, words-per-caption slider
- Exports via ffmpeg with ASS subtitles
Requires: ffmpeg on PATH, faster-whisper (optional), Pillow (pip install Pillow)
"""

import os, re, json, threading, subprocess, tempfile
from pathlib import Path
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

try:
    from PIL import Image, ImageDraw, ImageFont, ImageTk
    PIL_AVAILABLE = True
except ImportError:
    PIL_AVAILABLE = False

try:
    import cv2
    CV2_AVAILABLE = True
except ImportError:
    CV2_AVAILABLE = False

CONFIG_PATH = Path(__file__).parent / "caption_config.json"
PREVIEW_W   = 540
PREVIEW_H   = 304   # 16:9


# ── SRT / time helpers ─────────────────────────────────────────────────────────

def srt_time_to_sec(t: str) -> float:
    t = t.replace(",", ".")
    h, m, s = t.split(":")
    return int(h) * 3600 + int(m) * 60 + float(s)

def sec_to_srt_time(s: float) -> str:
    s = max(0.0, s)
    h = int(s // 3600); m = int((s % 3600) // 60); sec = s % 60
    return f"{h:02d}:{m:02d}:{int(sec):02d},{min(999, int(round((sec%1)*1000))):03d}"

def sec_to_ass_time(s: float) -> str:
    s = max(0.0, s)
    h = int(s // 3600); m = int((s % 3600) // 60); sec = s % 60
    return f"{h}:{m:02d}:{int(sec):02d}.{min(99, int(round((sec%1)*100))):02d}"

def parse_srt(text: str) -> list:
    result = []
    for block in re.split(r"\n\s*\n", text.strip()):
        lines = block.strip().splitlines()
        if len(lines) < 3:
            continue
        try:
            idx = int(lines[0].strip())
        except ValueError:
            continue
        m = re.match(r"(\d{2}:\d{2}:\d{2}[,\.]\d{3})\s*-->\s*(\d{2}:\d{2}:\d{2}[,\.]\d{3})", lines[1].strip())
        if not m:
            continue
        result.append({"index": idx, "start": srt_time_to_sec(m.group(1)),
                        "end": srt_time_to_sec(m.group(2)), "text": " ".join(lines[2:]).strip()})
    return result

def captions_to_srt(caps: list) -> str:
    out = []
    for c in caps:
        out += [str(c["index"]), f"{sec_to_srt_time(c['start'])} --> {sec_to_srt_time(c['end'])}", c["text"], ""]
    return "\n".join(out)

def rechunk_captions(caps: list, words_per: int) -> list:
    if words_per <= 0:
        return caps
    result = []; idx = 1
    for cap in caps:
        words = cap["text"].split()
        if len(words) <= words_per:
            result.append({**cap, "index": idx}); idx += 1
        else:
            total = len(words); dur = cap["end"] - cap["start"]
            for i in range(0, total, words_per):
                chunk = words[i:i+words_per]
                fs = i / total; fe = min(i+words_per, total) / total
                result.append({"index": idx, "start": cap["start"]+fs*dur,
                                "end": cap["start"]+fe*dur, "text": " ".join(chunk)})
                idx += 1
    return result

def apply_replacements(caps: list, replacements: dict) -> list:
    if not replacements:
        return caps
    result = []
    for cap in caps:
        text = cap["text"]
        for old, new in replacements.items():
            if old:
                text = re.sub(re.escape(old), new, text, flags=re.IGNORECASE)
        result.append({**cap, "text": text})
    return result


# ── ASS subtitle generation ────────────────────────────────────────────────────

def _ac(r, g, b, a=0) -> str:          # &HAABBGGRR
    return f"&H{a:02X}{b:02X}{g:02X}{r:02X}"

STYLES = {
    "Bold Impact": {
        "desc": "Large bold white, thick outline — TikTok / Reels",
        "Fontname": "Arial Black", "Fontsize": 28,
        "PrimaryColour": _ac(255,255,255), "SecondaryColour": _ac(255,220,0),
        "OutlineColour": _ac(0,0,0),       "BackColour": _ac(0,0,0,180),
        "Bold": 1, "Italic": 0, "Outline": 3, "Shadow": 0,
        "BorderStyle": 1, "Alignment": 2, "MarginV": 40,
        "PIL_Scale": 0.71,
    },
    "Classic": {
        "desc": "White text, black outline — YouTube standard",
        "Fontname": "Arial", "Fontsize": 22,
        "PrimaryColour": _ac(255,255,255), "SecondaryColour": _ac(255,255,0),
        "OutlineColour": _ac(0,0,0),       "BackColour": _ac(0,0,0,128),
        "Bold": 0, "Italic": 0, "Outline": 2, "Shadow": 1,
        "BorderStyle": 1, "Alignment": 2, "MarginV": 30,
        "PIL_Scale": 0.91,
    },
    "Yellow Highlight": {
        "desc": "Yellow bold — high contrast on dark backgrounds",
        "Fontname": "Arial", "Fontsize": 24,
        "PrimaryColour": _ac(255,220,0),   "SecondaryColour": _ac(255,255,255),
        "OutlineColour": _ac(0,0,0),       "BackColour": _ac(0,0,0,128),
        "Bold": 1, "Italic": 0, "Outline": 2, "Shadow": 1,
        "BorderStyle": 1, "Alignment": 2, "MarginV": 30,
        "PIL_Scale": 0.91,
    },
    "Subtitle Box": {
        "desc": "White on dark box — documentary / podcast style",
        "Fontname": "Arial", "Fontsize": 20,
        "PrimaryColour": _ac(255,255,255), "SecondaryColour": _ac(200,200,200),
        "OutlineColour": _ac(0,0,0),       "BackColour": _ac(0,0,0,100),
        "Bold": 0, "Italic": 0, "Outline": 0, "Shadow": 0,
        "BorderStyle": 3, "Alignment": 2, "MarginV": 20,
        "PIL_Scale": 0.91,
    },
    "Minimal": {
        "desc": "Small clean white — non-distracting",
        "Fontname": "Arial", "Fontsize": 16,
        "PrimaryColour": _ac(255,255,255), "SecondaryColour": _ac(200,200,200),
        "OutlineColour": _ac(30,30,30),    "BackColour": _ac(0,0,0,180),
        "Bold": 0, "Italic": 0, "Outline": 1, "Shadow": 0,
        "BorderStyle": 1, "Alignment": 2, "MarginV": 20,
        "PIL_Scale": 0.91,
    },
    "Top Center": {
        "desc": "Classic white at top — for bottom-heavy visuals",
        "Fontname": "Arial", "Fontsize": 22,
        "PrimaryColour": _ac(255,255,255), "SecondaryColour": _ac(255,255,0),
        "OutlineColour": _ac(0,0,0),       "BackColour": _ac(0,0,0,128),
        "Bold": 0, "Italic": 0, "Outline": 2, "Shadow": 1,
        "BorderStyle": 1, "Alignment": 8, "MarginV": 20,
        "PIL_Scale": 0.91,
    },
}

def build_ass(caps: list, style_name: str, res_x=1920, res_y=1080, pos_override=None, fontsize=None) -> str:
    s = STYLES[style_name]
    effective_fontsize = fontsize if fontsize is not None else s["Fontsize"]
    hdr = (
        f"[Script Info]\nScriptType: v4.00+\nPlayResX: {res_x}\nPlayResY: {res_y}\n"
        f"ScaledBorderAndShadow: yes\n\n[V4+ Styles]\n"
        f"Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, "
        f"BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, "
        f"BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\n"
        f"Style: Default,{s['Fontname']},{effective_fontsize},"
        f"{s['PrimaryColour']},{s['SecondaryColour']},{s['OutlineColour']},{s['BackColour']},"
        f"{s['Bold']},{s['Italic']},0,0,100,100,0,0,"
        f"{s['BorderStyle']},{s['Outline']},{s['Shadow']},"
        f"{s['Alignment']},10,10,{s['MarginV']},1\n\n"
        f"[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
    )
    events = []
    for cap in caps:
        text = cap["text"].replace("\n", "\\N")
        # Always apply fontsize as inline override — more reliable than Style header alone
        tags = f"\\fs{effective_fontsize}"
        if pos_override:
            vx = int(pos_override[0] * res_x)
            vy = int(pos_override[1] * res_y)
            tags += f"\\an5\\pos({vx},{vy})"
        text = f"{{{tags}}}{text}"
        events.append(
            f"Dialogue: 0,{sec_to_ass_time(cap['start'])},{sec_to_ass_time(cap['end'])},"
            f"Default,,0,0,0,,{text}"
        )
    return hdr + "\n".join(events)


# ── Preview helpers ────────────────────────────────────────────────────────────

def parse_ass_color(ass_c: str):
    """&HAABBGGRR → (R, G, B, A)  A: 0=opaque"""
    h = ass_c.lstrip("&H").lstrip("&h")
    if len(h) >= 8:
        return (int(h[6:8],16), int(h[4:6],16), int(h[2:4],16), int(h[0:2],16))
    return (255, 255, 255, 0)

_FONT_DIR = Path("C:/Windows/Fonts")
_FONT_MAP  = {
    "Arial Black": ["ariblk.ttf"],
    "Arial":       ["arialbd.ttf", "arial.ttf"],
    "Impact":      ["impact.ttf"],
}

def _load_pil_font(fontname: str, bold: int, size: int):
    from PIL import ImageFont
    for fname in _FONT_MAP.get(fontname, []):
        p = _FONT_DIR / fname
        if p.exists():
            try: return ImageFont.truetype(str(p), size)
            except Exception: pass
    for fallback in ["arial.ttf", "calibri.ttf", "segoeui.ttf"]:
        p = _FONT_DIR / fallback
        if p.exists():
            try: return ImageFont.truetype(str(p), size)
            except Exception: pass
    return ImageFont.load_default()

def default_pos(style: dict) -> tuple:
    """Return default caption center as (x_pct, y_pct) from style alignment/margins."""
    align = style["Alignment"]; mv = style["MarginV"]; ml = 10
    col = (align - 1) % 3    # 0=left 1=center 2=right
    row = (align - 1) // 3   # 0=bottom 1=mid 2=top
    x = [ml/1920, 0.5, 1.0-ml/1920][col]
    y = [1.0-mv/1080, 0.5, mv/1080][row]
    return (x, y)

def render_preview(frame_img, caption_text: str, style: dict,
                   pos_x: float, pos_y: float, w: int, h: int,
                   fontsize: int = None, play_res_x: int = 1920, play_res_y: int = 1080):
    """
    Returns (PIL Image, text_bbox=(x0,y0,x1,y1)).
    fontsize is in ASS script units. Scale is computed the same way ASS renderers
    do it: min(canvas_w/PlayResX, canvas_h/PlayResY), so the preview matches the
    burned output regardless of video orientation or aspect ratio.
    """
    from PIL import Image, ImageDraw

    img = Image.new("RGB", (w, h), (13, 13, 26))
    if frame_img:
        frame = frame_img.copy()
        frame.thumbnail((w, h), Image.LANCZOS)
        xo = (w - frame.width) // 2; yo = (h - frame.height) // 2
        img.paste(frame, (xo, yo))

    def color(key, force_alpha=None):
        r, g, b, a = parse_ass_color(style[key])
        alpha = (255 - a) if force_alpha is None else force_alpha
        return (r, g, b, alpha)

    primary_c  = color("PrimaryColour",  255)[:3]
    outline_c  = color("OutlineColour",  255)[:3]
    back_rgba  = color("BackColour")

    # Match ASS renderer: use min scale across both axes so font size is correct
    # for any aspect ratio (landscape, portrait, square).
    ass_fontsize = fontsize if fontsize is not None else style["Fontsize"]
    scale = min(w / play_res_x, h / play_res_y)
    scale_factor = style.get("PIL_Scale", 1.0)
    font_size = max(6, int(ass_fontsize * scale_factor * scale))
    font = _load_pil_font(style["Fontname"], style["Bold"], font_size)

    cx = int(pos_x * w); cy = int(pos_y * h)
    tmp_draw = ImageDraw.Draw(img)
    bb = tmp_draw.textbbox((0, 0), caption_text, font=font)
    tw = bb[2] - bb[0]; th = bb[3] - bb[1]
    tx = cx - tw // 2; ty = cy - th // 2

    if style["BorderStyle"] == 3:
        pad = max(2, font_size // 5)
        overlay = Image.new("RGBA", img.size, (0,0,0,0))
        od = ImageDraw.Draw(overlay)
        box_a = 255 - back_rgba[3]
        od.rectangle([tx-pad, ty-pad, tx+tw+pad, ty+th+pad], fill=(*back_rgba[:3], box_a))
        img = Image.alpha_composite(img.convert("RGBA"), overlay).convert("RGB")

    draw = ImageDraw.Draw(img)
    ow = max(0, round(style["Outline"] * scale))
    if ow:
        for dx in range(-ow, ow+1):
            for dy in range(-ow, ow+1):
                if dx or dy:
                    draw.text((tx+dx, ty+dy), caption_text, font=font, fill=outline_c)
    if style["Shadow"]:
        so = max(1, round(style["Shadow"] * scale))
        draw.text((tx+so, ty+so), caption_text, font=font, fill=(0,0,0))
    draw.text((tx, ty), caption_text, font=font, fill=primary_c)

    return img, (tx, ty, tx+tw, ty+th)


# ── ffmpeg helpers ─────────────────────────────────────────────────────────────

def extract_frame(video_path: str, timestamp: float, output_path: str) -> bool:
    r = subprocess.run(
        ["ffmpeg", "-y", "-ss", str(max(0.0, timestamp)), "-i", video_path,
         "-vframes", "1", "-f", "image2", output_path],
        capture_output=True,
    )
    return r.returncode == 0 and os.path.exists(output_path)

def get_video_resolution(video_path: str):
    r = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=width,height", "-of", "csv=s=x:p=0", video_path],
        capture_output=True, text=True,
    )
    w, h = 1920, 1080
    if r.returncode == 0 and "x" in r.stdout:
        try:
            w_str, h_str = r.stdout.strip().split("x")
            w, h = int(w_str), int(h_str)
        except Exception: pass

    try:
        rot_r = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "v:0",
             "-show_entries", "stream_tags=rotate",
             "-of", "default=noprint_wrappers=1:nokey=1", video_path],
            capture_output=True, text=True,
        )
        angle_str = rot_r.stdout.strip()
        if angle_str:
            angle = int(float(angle_str))
            if abs(angle) in (90, 270):
                w, h = h, w
    except Exception: pass

    return w, h

def get_video_duration(video_path: str) -> float:
    r = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", video_path],
        capture_output=True, text=True,
    )
    try: return float(r.stdout.strip())
    except Exception: return 0.0

def burn_captions(video_path: str, ass_path: str, output_path: str, progress_cb=None) -> bool:
    duration = get_video_duration(video_path)
    ass_escaped = ass_path.replace("\\", "/").replace(":", "\\:")
    proc = subprocess.Popen(
        ["ffmpeg", "-y", "-i", video_path, "-vf", f"ass='{ass_escaped}'",
         "-c:a", "copy", "-c:v", "libx264", "-preset", "fast", "-crf", "18", output_path],
        stderr=subprocess.PIPE, universal_newlines=True,
    )
    time_re = re.compile(r"time=(\d{2}):(\d{2}):(\d{2})\.(\d{2})")
    for line in proc.stderr:
        if progress_cb and duration > 0:
            m = time_re.search(line)
            if m:
                t = int(m.group(1))*3600 + int(m.group(2))*60 + int(m.group(3)) + int(m.group(4))/100
                progress_cb(t, duration)
    proc.wait()
    return proc.returncode == 0


# ── Whisper transcription ──────────────────────────────────────────────────────

def generate_srt_from_video(video_path: str, model_size: str, log_cb, progress_cb):
    log_cb("Extracting audio from video…", "info")
    try:
        from faster_whisper import WhisperModel
        import torch
    except ImportError:
        log_cb("[ERROR] faster-whisper not installed. Run install_transcriber_dependencies.bat", "fail")
        return None
    tmp = os.path.join(tempfile.gettempdir(), "caption_audio_tmp.wav")
    try:
        r = subprocess.run(
            ["ffmpeg", "-y", "-i", video_path, "-ar", "16000", "-ac", "1", "-f", "wav", tmp],
            capture_output=True,
        )
        if r.returncode != 0:
            log_cb(f"[ERROR] Audio extraction failed:\n{r.stderr.decode(errors='ignore')[-300:]}", "fail")
            return None
        device = "cuda" if torch.cuda.is_available() else "cpu"
        ct = "float16" if device == "cuda" else "int8"
        log_cb(f"Loading Whisper ({model_size}) on {device.upper()}…", "info")
        model = WhisperModel(model_size, device=device, compute_type=ct)
        log_cb("Transcribing…", "info")
        segs, _ = model.transcribe(tmp, language="en", beam_size=5, vad_filter=True)
        caps = [{"index": i, "start": s.start, "end": s.end, "text": s.text.strip()}
                for i, s in enumerate(segs, 1)]
        log_cb(f"[OK] Transcribed {len(caps)} segments.", "ok")
        return caps
    finally:
        try: os.unlink(tmp)
        except Exception: pass


# ── Word replacement table ─────────────────────────────────────────────────────

class ReplacementTable(tk.Frame):
    def __init__(self, parent, C: dict):
        super().__init__(parent, bg=C["BG"])
        self.C = C; self._rows = []
        hdr = tk.Frame(self, bg=C["BG"]); hdr.pack(fill="x")
        tk.Label(hdr, text="Word Replacements", font=("Segoe UI", 9, "bold"),
                 fg=C["MUT"], bg=C["BG"]).pack(side="left")
        tk.Button(hdr, text="+ Add", command=self._add_row, font=("Segoe UI", 8),
                  bg=C["ACCENT"], fg="white", relief="flat", bd=0, padx=8, pady=2,
                  cursor="hand2", activebackground="#c73652").pack(side="right")
        self.tf = tk.Frame(self, bg=C["BG"]); self.tf.pack(fill="x", pady=(4,0))
        h = tk.Frame(self.tf, bg=C["PANEL"]); h.pack(fill="x", pady=(0,2))
        tk.Label(h, text="Original word / phrase", font=("Segoe UI", 8), fg=C["MUT"],
                 bg=C["PANEL"], width=22, anchor="w").pack(side="left", padx=(10,0), pady=3)
        tk.Label(h, text="Replace with", font=("Segoe UI", 8), fg=C["MUT"],
                 bg=C["PANEL"], anchor="w").pack(side="left", padx=10, pady=3)

    def _add_row(self, old="", new=""):
        C = self.C; row = tk.Frame(self.tf, bg=C["BG"]); row.pack(fill="x", pady=1)
        ov = tk.StringVar(value=old); nv = tk.StringVar(value=new)
        for var, w in [(ov, 22), (nv, 22)]:
            if var is nv:
                tk.Label(row, text="→", fg=C["MUT"], bg=C["BG"],
                         font=("Segoe UI", 10)).pack(side="left", padx=2)
            tk.Entry(row, textvariable=var, font=("Segoe UI", 9), bg=C["ENTRY"],
                     fg=C["TEXT"], insertbackground=C["TEXT"], relief="flat", bd=0,
                     width=w).pack(side="left", ipady=4, padx=(0,4) if var is ov else (4,4),
                                   fill="x" if var is nv else None,
                                   expand=True if var is nv else False)
        def remove():
            self._rows[:] = [(o,n,f) for o,n,f in self._rows if f is not row]
            row.destroy()
        tk.Button(row, text="✕", command=remove, font=("Segoe UI", 8),
                  bg=C["BG"], fg=C["MUT"], relief="flat", bd=0, padx=4,
                  cursor="hand2").pack(side="right")
        self._rows.append((ov, nv, row))

    def get_replacements(self) -> dict:
        return {o.get().strip(): n.get().strip() for o,n,_ in self._rows if o.get().strip()}

    def set_replacements(self, d: dict):
        for _,_,row in self._rows: row.destroy()
        self._rows.clear()
        for old, new in d.items(): self._add_row(old, new)


# ── Config ─────────────────────────────────────────────────────────────────────

def load_config() -> dict:
    if CONFIG_PATH.exists():
        try: return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        except Exception: pass
    return {"words_per_caption": 6, "style": "Bold Impact", "replacements": {},
            "last_video": "", "last_output_dir": "", "whisper_model": "medium",
            "pos_x": None, "pos_y": None, "font_size": 28}

def save_config(cfg: dict):
    CONFIG_PATH.write_text(json.dumps(cfg, indent=2), encoding="utf-8")


# ── Main App ───────────────────────────────────────────────────────────────────

class CaptionApp(tk.Tk):
    C = {"BG": "#1a1a2e", "PANEL": "#16213e", "ACCENT": "#e94560",
         "TEXT": "#eaeaea", "MUT": "#8892b0", "ENTRY": "#0f3460"}

    def __init__(self):
        super().__init__()
        self.title("Video Caption Burner")
        self.geometry("1060x780")
        self.resizable(True, True)
        self.configure(bg=self.C["BG"])
        self.cfg = load_config()
        self.fontsize_var = tk.StringVar(value=str(self.cfg.get("font_size", 28)))
        # Preview state
        self._frame_img       = None
        self._photo_img       = None
        self._cap_bbox        = None
        self._pos_custom      = self.cfg.get("pos_x") is not None
        self._pos_x           = self.cfg.get("pos_x") or 0.5
        self._pos_y           = self.cfg.get("pos_y") or 0.85
        # Video player state
        self._video_cap       = None   # cv2.VideoCapture
        self._is_playing      = False
        self._current_time    = 0.0
        self._video_fps       = 30.0
        self._video_duration  = 0.0
        self._play_job        = None
        self._raw_captions    = []             # parsed from SRT, before processing
        self._preview_captions = []            # after rechunk + replacements
        self._video_res       = (1920, 1080)   # (width, height) of loaded video
        self._canvas_w        = PREVIEW_W      # explicit canvas size — avoids winfo race
        self._canvas_h        = PREVIEW_H
        self._display_rotate  = 0             # rotation angle from video metadata (0/90/180/270)
        self._build_ui()
        self.after(200, self._update_preview)   # initial render after layout settles

    def _get_fontsize(self) -> int:
        try:
            v = int(self.fontsize_var.get())
            return max(8, min(200, v))
        except (ValueError, tk.TclError):
            return 28

    # ── Build UI ───────────────────────────────────────────────────────────────

    def _build_ui(self):
        C = self.C

        # ── Scrollable root ────────────────────────────────────────────────────
        _vscroll = tk.Scrollbar(self, orient="vertical")
        _vscroll.pack(side="right", fill="y")
        _scroll_cv = tk.Canvas(self, bg=C["BG"], highlightthickness=0,
                               yscrollcommand=_vscroll.set)
        _scroll_cv.pack(side="left", fill="both", expand=True)
        _vscroll.config(command=_scroll_cv.yview)

        main = tk.Frame(_scroll_cv, bg=C["BG"])
        _win_id = _scroll_cv.create_window((0, 0), window=main, anchor="nw")

        def _on_frame_configure(e):
            _scroll_cv.configure(scrollregion=_scroll_cv.bbox("all"))
        def _on_canvas_configure(e):
            _scroll_cv.itemconfig(_win_id, width=e.width)
        main.bind("<Configure>", _on_frame_configure)
        _scroll_cv.bind("<Configure>", _on_canvas_configure)

        def _mousewheel(e):
            # Don't steal scroll from Text / Listbox widgets
            if isinstance(e.widget, (tk.Text, tk.Listbox, tk.Scale)):
                return
            _scroll_cv.yview_scroll(int(-1 * (e.delta / 120)), "units")
        self.bind_all("<MouseWheel>", _mousewheel)

        # ── Title ──────────────────────────────────────────────────────────────
        hdr = tk.Frame(main, bg=C["BG"], pady=6); hdr.pack(fill="x")
        tk.Label(hdr, text="Video Caption Burner",
                 font=("Segoe UI", 14, "bold"), fg=C["TEXT"], bg=C["BG"]).pack()
        tk.Label(hdr, text="Click or drag on the preview to position captions  •  ASS subtitles via ffmpeg",
                 font=("Segoe UI", 9), fg=C["MUT"], bg=C["BG"]).pack()

        # ── Two-column body ────────────────────────────────────────────────────
        body = tk.Frame(main, bg=C["BG"]); body.pack(fill="x", padx=14, pady=(0,4))
        left = tk.Frame(body, bg=C["BG"], width=456)
        left.pack(side="left", fill="y", padx=(0,10))
        left.pack_propagate(False)
        self._build_left(left)
        right = tk.Frame(body, bg=C["BG"])
        right.pack(side="left", fill="x", expand=True)
        self._build_right(right)

        # ── Progress ───────────────────────────────────────────────────────────
        pf = tk.Frame(main, bg=C["BG"], padx=14, pady=2); pf.pack(fill="x")
        self.prog_lbl = tk.Label(pf, text="", font=("Segoe UI", 9), fg=C["MUT"], bg=C["BG"])
        self.prog_lbl.pack(anchor="w")
        self.prog_bar = ttk.Progressbar(pf, mode="determinate")
        self.prog_bar.pack(fill="x", pady=(2,0))
        sty = ttk.Style(self); sty.theme_use("default")
        sty.configure("TProgressbar", troughcolor=C["PANEL"], background=C["ACCENT"], thickness=8)

        # ── Log ────────────────────────────────────────────────────────────────
        lf = tk.Frame(main, bg=C["BG"], padx=14); lf.pack(fill="x")
        tk.Label(lf, text="Log", font=("Segoe UI", 9, "bold"), fg=C["MUT"], bg=C["BG"]).pack(anchor="w")
        self.log_box = tk.Text(lf, font=("Consolas", 9), bg=C["PANEL"], fg=C["TEXT"],
                               insertbackground=C["TEXT"], relief="flat", bd=0,
                               state="disabled", height=4)
        sb = tk.Scrollbar(self.log_box); sb.pack(side="right", fill="y")
        self.log_box.config(yscrollcommand=sb.set); sb.config(command=self.log_box.yview)
        self.log_box.pack(fill="x", pady=(2,0))
        for tag, col in [("ok","#64ffda"),("warn","#ffd700"),("fail",C["ACCENT"]),("info",C["MUT"])]:
            self.log_box.tag_configure(tag, foreground=col)

        # ── Export button ──────────────────────────────────────────────────────
        bf = tk.Frame(main, bg=C["BG"], pady=6); bf.pack()
        self.btn = tk.Button(bf, text="Burn Captions  →  Export Video", command=self._start,
                             font=("Segoe UI", 11, "bold"), bg=C["ACCENT"], fg="white",
                             relief="flat", bd=0, padx=28, pady=9, cursor="hand2",
                             activebackground="#c73652", activeforeground="white")
        self.btn.pack()

    def _build_left(self, parent):
        C = self.C

        def panel(title, fn):
            f = tk.Frame(parent, bg=C["PANEL"], pady=5); f.pack(fill="x", pady=(0,4))
            tk.Label(f, text=title, font=("Segoe UI", 9, "bold"),
                     fg=C["MUT"], bg=C["PANEL"]).pack(anchor="w", padx=14, pady=(0,3))
            fn(f)

        def field(p, label, var, browse_cmd=None, tip=""):
            row = tk.Frame(p, bg=C["PANEL"]); row.pack(fill="x", padx=14, pady=1)
            tk.Label(row, text=label, font=("Segoe UI", 8, "bold"), fg=C["MUT"],
                     bg=C["PANEL"], width=16, anchor="w").pack(side="left")
            tk.Entry(row, textvariable=var, font=("Segoe UI", 9), bg=C["ENTRY"],
                     fg=C["TEXT"], insertbackground=C["TEXT"], relief="flat", bd=0,
                     ).pack(side="left", fill="x", expand=True, ipady=4, padx=(0,4))
            if browse_cmd:
                tk.Button(row, text="Browse", command=browse_cmd,
                          font=("Segoe UI", 8, "bold"), bg=C["ACCENT"], fg="white",
                          relief="flat", bd=0, padx=8, pady=3, cursor="hand2",
                          activebackground="#c73652").pack(side="right")
            if tip:
                tk.Label(p, text="  "+tip, font=("Segoe UI", 7),
                         fg=C["MUT"], bg=C["PANEL"]).pack(anchor="w", padx=14)

        # Files
        self.video_var   = tk.StringVar(value=self.cfg.get("last_video", ""))
        self.srt_var     = tk.StringVar()
        self.out_dir_var = tk.StringVar(value=self.cfg.get("last_output_dir", ""))

        def build_files(p):
            field(p, "Video File", self.video_var, browse_cmd=self._browse_video,
                  tip="MP4, MOV, MKV, AVI — any ffmpeg-supported format")
            srt_row = tk.Frame(p, bg=C["PANEL"]); srt_row.pack(fill="x", padx=14, pady=1)
            tk.Label(srt_row, text="SRT File", font=("Segoe UI", 8, "bold"), fg=C["MUT"],
                     bg=C["PANEL"], width=16, anchor="w").pack(side="left")
            tk.Entry(srt_row, textvariable=self.srt_var, font=("Segoe UI", 9), bg=C["ENTRY"],
                     fg=C["TEXT"], insertbackground=C["TEXT"], relief="flat", bd=0,
                     ).pack(side="left", fill="x", expand=True, ipady=4, padx=(0,4))
            tk.Button(srt_row, text="Browse", command=self._browse_srt,
                      font=("Segoe UI", 8, "bold"), bg=C["ACCENT"], fg="white",
                      relief="flat", bd=0, padx=8, pady=3, cursor="hand2",
                      activebackground="#c73652").pack(side="right")
            tk.Label(p, text="  Leave blank: auto-detect same-name SRT or generate via Whisper",
                     font=("Segoe UI", 7), fg=C["MUT"], bg=C["PANEL"]).pack(anchor="w", padx=14)
            field(p, "Output Folder", self.out_dir_var,
                  browse_cmd=lambda: self.out_dir_var.set(
                      filedialog.askdirectory(title="Select output folder")),
                  tip="Leave blank to save alongside the video")

        panel("Input / Output", build_files)

        # Options
        self.style_var = tk.StringVar(value=self.cfg.get("style", "Bold Impact"))
        self.words_var = tk.IntVar(value=self.cfg.get("words_per_caption", 6))
        self.model_var = tk.StringVar(value=self.cfg.get("whisper_model", "medium"))

        def build_options(p):
            sf = tk.Frame(p, bg=C["PANEL"]); sf.pack(fill="x", padx=14, pady=1)
            tk.Label(sf, text="Caption Style", font=("Segoe UI", 8, "bold"),
                     fg=C["MUT"], bg=C["PANEL"], width=16, anchor="w").pack(side="left")
            cb = ttk.Combobox(sf, textvariable=self.style_var, values=list(STYLES.keys()),
                              state="readonly", font=("Segoe UI", 9), width=18)
            cb.pack(side="left")
            self._desc_lbl = tk.Label(sf, text="", font=("Segoe UI", 7), fg=C["MUT"],
                                      bg=C["PANEL"], wraplength=150, justify="left")
            self._desc_lbl.pack(side="left", padx=6)
            cb.bind("<<ComboboxSelected>>", self._on_style_change)
            self._on_style_change()

            # Font size
            ff = tk.Frame(p, bg=C["PANEL"]); ff.pack(fill="x", padx=14, pady=(4,1))
            tk.Label(ff, text="Font Size", font=("Segoe UI", 8, "bold"),
                     fg=C["MUT"], bg=C["PANEL"], width=16, anchor="w").pack(side="left")
            fsb = tk.Spinbox(ff, from_=8, to=200, textvariable=self.fontsize_var, width=5,
                             font=("Segoe UI", 9), bg=C["ENTRY"], fg=C["TEXT"],
                             insertbackground=C["TEXT"], buttonbackground=C["PANEL"],
                             relief="flat", bd=0,
                             command=self._update_preview)
            fsb.pack(side="left", ipady=3)
            fsb.bind("<Return>",   lambda e: self._update_preview())
            fsb.bind("<FocusOut>", lambda e: self._update_preview())
            tk.Label(ff, text="px  (at 1080p — preview matches export exactly)",
                     font=("Segoe UI", 7), fg=C["MUT"], bg=C["PANEL"]).pack(side="left", padx=6)

            wf = tk.Frame(p, bg=C["PANEL"]); wf.pack(fill="x", padx=14, pady=(6,1))
            tk.Label(wf, text="Words / Caption", font=("Segoe UI", 8, "bold"),
                     fg=C["MUT"], bg=C["PANEL"], width=16, anchor="w").pack(side="left")
            self._wlbl = tk.Label(wf, text=str(self.words_var.get()),
                                  font=("Segoe UI", 9, "bold"), fg=C["TEXT"], bg=C["PANEL"], width=3)
            self._wlbl.pack(side="left")
            def on_words(v):
                self._wlbl.config(text=str(int(float(v))))
                self._rebuild_preview_captions()
            tk.Scale(wf, from_=1, to=15, orient="horizontal", variable=self.words_var,
                     command=on_words, bg=C["PANEL"], fg=C["TEXT"], troughcolor=C["ENTRY"],
                     highlightthickness=0, relief="flat", length=175, showvalue=False,
                     ).pack(side="left", padx=6)
            tk.Label(wf, text="(1=word  6=natural  15=long)", font=("Segoe UI", 7),
                     fg=C["MUT"], bg=C["PANEL"]).pack(side="left")

            mf = tk.Frame(p, bg=C["PANEL"]); mf.pack(fill="x", padx=14, pady=(6,2))
            tk.Label(mf, text="Whisper Model", font=("Segoe UI", 8, "bold"),
                     fg=C["MUT"], bg=C["PANEL"], width=16, anchor="w").pack(side="left")
            tk.Label(mf, text="(if no SRT)", font=("Segoe UI", 7),
                     fg=C["MUT"], bg=C["PANEL"]).pack(side="left", padx=(0,6))
            for sz in ["tiny","base","small","medium","large-v3"]:
                tk.Radiobutton(mf, text=sz if sz != "medium" else "medium★",
                               variable=self.model_var, value=sz, font=("Segoe UI", 8),
                               fg=C["TEXT"], bg=C["PANEL"], selectcolor=C["ENTRY"],
                               activebackground=C["PANEL"], activeforeground=C["TEXT"],
                               ).pack(side="left", padx=3)

        panel("Caption Options", build_options)

        # Replacements
        rp = tk.Frame(parent, bg=C["BG"], pady=2); rp.pack(fill="x")
        self.repl_table = ReplacementTable(rp, C)
        self.repl_table.pack(fill="x")
        self.repl_table.set_replacements(self.cfg.get("replacements", {}))

    def _build_right(self, parent):
        C = self.C

        nb = ttk.Notebook(parent)
        nb.pack(fill="both", expand=True)

        # ── Tab 1: Preview ─────────────────────────────────────────────────────
        prev_tab = tk.Frame(nb, bg=C["BG"])
        nb.add(prev_tab, text="  Preview  ")

        # Canvas
        self._canvas = tk.Canvas(prev_tab, bg="#0d0d1a", highlightthickness=1,
                                 highlightbackground=C["PANEL"],
                                 width=PREVIEW_W, height=PREVIEW_H, cursor="fleur")
        self._canvas.pack(anchor="center", pady=(4,0))
        self._canvas.bind("<Button-1>",        self._on_click)
        self._canvas.bind("<B1-Motion>",       self._on_drag)
        self._canvas.bind("<ButtonRelease-1>", lambda e: None)
        self._canvas.create_text(PREVIEW_W//2, PREVIEW_H//2,
                                 text="Select a video to start previewing\nDrag on the frame to reposition captions",
                                 fill=C["MUT"], font=("Segoe UI", 10), justify="center",
                                 tags="placeholder")

        if not PIL_AVAILABLE:
            tk.Label(prev_tab, text="Install Pillow for styled preview:  pip install Pillow",
                     font=("Segoe UI", 8), fg="#ffd700", bg=C["BG"]).pack(pady=(2,0))

        # Video info bar
        vif = tk.Frame(prev_tab, bg=C["BG"]); vif.pack(fill="x", pady=(2,0))
        self._video_info_lbl = tk.Label(vif, text="No video loaded",
                                        font=("Segoe UI", 7), fg=C["MUT"], bg=C["BG"])
        self._video_info_lbl.pack(side="left", padx=2)

        # Current caption display
        cl = tk.Frame(prev_tab, bg=C["PANEL"], pady=3); cl.pack(fill="x", pady=(2,0))
        tk.Label(cl, text="Caption:", font=("Segoe UI", 8), fg=C["MUT"],
                 bg=C["PANEL"]).pack(side="left", padx=(8,4))
        self._cap_lbl = tk.Label(cl, text="—", font=("Segoe UI", 9, "bold"),
                                 fg=C["TEXT"], bg=C["PANEL"], anchor="w")
        self._cap_lbl.pack(side="left", fill="x", expand=True, padx=(0,8))

        # Timeline scrubber
        tf = tk.Frame(prev_tab, bg=C["BG"], pady=3); tf.pack(fill="x")
        self._time_lbl = tk.Label(tf, text="0:00:00", font=("Consolas", 8),
                                  fg=C["MUT"], bg=C["BG"], width=7)
        self._time_lbl.pack(side="left", padx=(0,4))
        self._timeline_var = tk.DoubleVar(value=0.0)
        self._timeline_slider = tk.Scale(
            tf, variable=self._timeline_var, from_=0, to=100,
            orient="horizontal", resolution=0.033,
            bg=C["BG"], fg=C["TEXT"], troughcolor=C["ENTRY"],
            highlightthickness=0, relief="flat", showvalue=False,
            command=self._on_timeline_scrub,
        )
        self._timeline_slider.pack(side="left", fill="x", expand=True)
        self._timeline_slider.bind("<ButtonRelease-1>", self._on_timeline_release)
        self._dur_lbl = tk.Label(tf, text="0:00:00", font=("Consolas", 8),
                                 fg=C["MUT"], bg=C["BG"], width=7)
        self._dur_lbl.pack(side="left", padx=(4,0))

        # Playback controls
        pf = tk.Frame(prev_tab, bg=C["BG"], pady=3); pf.pack(fill="x")
        self._play_btn = tk.Button(pf, text="▶  Play", command=self._play_pause,
                                   font=("Segoe UI", 9, "bold"), bg=C["ACCENT"], fg="white",
                                   relief="flat", bd=0, padx=14, pady=4, cursor="hand2",
                                   activebackground="#c73652", activeforeground="white")
        self._play_btn.pack(side="left")
        if not CV2_AVAILABLE:
            tk.Label(pf, text="  pip install opencv-python  for smooth playback",
                     font=("Segoe UI", 7), fg=C["MUT"], bg=C["BG"]).pack(side="left", padx=6)

        # Position label + reset
        pr = tk.Frame(prev_tab, bg=C["BG"], pady=2); pr.pack(fill="x")
        self._pos_lbl = tk.Label(pr, text="Position: style default",
                                 font=("Segoe UI", 8), fg=C["MUT"], bg=C["BG"])
        self._pos_lbl.pack(side="left")
        tk.Button(pr, text="Reset position", command=self._reset_pos,
                  font=("Segoe UI", 8), bg=C["PANEL"], fg=C["MUT"],
                  relief="flat", bd=0, padx=8, pady=2, cursor="hand2").pack(side="right")

        # ── Tab 2: Script / SRT Editor ─────────────────────────────────────────
        srt_tab = tk.Frame(nb, bg=C["BG"])
        nb.add(srt_tab, text="  Script / SRT Editor  ")

        tb = tk.Frame(srt_tab, bg=C["BG"], pady=5); tb.pack(fill="x", padx=6)
        tk.Label(tb, text="Edit the SRT below — your edits are used on export",
                 font=("Segoe UI", 8), fg=C["MUT"], bg=C["BG"]).pack(side="left")
        tk.Button(tb, text="Reload from file", command=self._reload_srt_from_file,
                  font=("Segoe UI", 8), bg=C["PANEL"], fg=C["MUT"],
                  relief="flat", bd=0, padx=8, pady=2, cursor="hand2").pack(side="right", padx=(4,0))
        tk.Button(tb, text="Refresh Preview", command=self._refresh_preview_from_editor,
                  font=("Segoe UI", 8), bg=C["PANEL"], fg=C["MUT"],
                  relief="flat", bd=0, padx=8, pady=2, cursor="hand2").pack(side="right", padx=(4,0))
        tk.Button(tb, text="Save to file", command=self._save_srt_to_file,
                  font=("Segoe UI", 8, "bold"), bg=C["ACCENT"], fg="white",
                  relief="flat", bd=0, padx=8, pady=2, cursor="hand2").pack(side="right", padx=(4,0))

        ef = tk.Frame(srt_tab, bg=C["BG"]); ef.pack(fill="x", padx=6, pady=(0,6))
        self._srt_editor = tk.Text(ef, font=("Consolas", 9), bg=C["PANEL"], fg=C["TEXT"],
                                   insertbackground=C["TEXT"], relief="flat", bd=0,
                                   wrap="none", undo=True, height=18)
        esb_y = tk.Scrollbar(ef, command=self._srt_editor.yview)
        esb_y.pack(side="right", fill="y")
        esb_x = tk.Scrollbar(ef, orient="horizontal", command=self._srt_editor.xview)
        esb_x.pack(side="bottom", fill="x")
        self._srt_editor.config(yscrollcommand=esb_y.set, xscrollcommand=esb_x.set)
        self._srt_editor.pack(side="left", fill="x", expand=True)

    # ── Preview logic ──────────────────────────────────────────────────────────

    def _on_style_change(self, event=None):
        name = self.style_var.get()
        if name in STYLES:
            self._desc_lbl.config(text=STYLES[name]["desc"])
        if not self._pos_custom:
            self._pos_x, self._pos_y = default_pos(STYLES[name])
        self._update_preview()

    def _on_click(self, event):
        self._move_caption(event.x, event.y)

    def _on_drag(self, event):
        self._move_caption(event.x, event.y)

    def _move_caption(self, cx: int, cy: int):
        w = self._canvas_w
        h = self._canvas_h
        self._pos_x = max(0.0, min(1.0, cx / w))
        self._pos_y = max(0.0, min(1.0, cy / h))
        self._pos_custom = True
        self._pos_lbl.config(text=f"Position: {self._pos_x:.2f}, {self._pos_y:.2f}  (custom — will apply to export)")
        self._update_preview()

    def _reset_pos(self):
        name = self.style_var.get()
        self._pos_x, self._pos_y = default_pos(STYLES.get(name, STYLES["Bold Impact"]))
        self._pos_custom = False
        self._pos_lbl.config(text="Position: style default")
        self._update_preview()

    # ── Video player ───────────────────────────────────────────────────────────

    @staticmethod
    def _fmt_time(s: float) -> str:
        s = max(0.0, s)
        h = int(s // 3600); m = int((s % 3600) // 60); sec = int(s % 60)
        return f"{h}:{m:02d}:{sec:02d}"

    def _cv2_to_pil(self, frame) -> "Image":
        """Convert a cv2 BGR frame to a PIL Image, rotating to match display orientation."""
        frame_rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        img = Image.fromarray(frame_rgb)
        angle = getattr(self, "_display_rotate", 0)
        if angle:
            # PIL rotate is CCW; video rotate tag means "rotate CCW by this angle to display"
            img = img.rotate(angle, expand=True)
        return img

    def _get_caption_at(self, t: float) -> str:
        for cap in self._preview_captions:
            if cap["start"] <= t <= cap["end"]:
                return cap["text"]
        return ""

    def _resize_preview_canvas(self, vw: int, vh: int):
        """Resize the preview canvas to match the video's aspect ratio."""
        MAX_DIM = 460
        aspect = vw / vh if vh else 16 / 9
        if aspect >= 1:          # landscape / square
            pw = MAX_DIM
            ph = max(1, int(MAX_DIM / aspect))
        else:                    # portrait
            ph = MAX_DIM
            pw = max(1, int(MAX_DIM * aspect))
        self._canvas_w = pw      # store explicitly so _update_preview never races winfo
        self._canvas_h = ph
        self._canvas.config(width=pw, height=ph)

    def _load_video_for_preview(self, path: str):
        """Open the video for preview. Uses cv2 if available, else ffprobe for duration."""
        # --- Step 1: get display dimensions from ffprobe (already accounts for rotation) ---
        disp_w, disp_h = get_video_resolution(path)

        # --- Step 2: detect rotation metadata so we can rotate cv2 frames later ---
        self._display_rotate = 0
        try:
            rot_r = subprocess.run(
                ["ffprobe", "-v", "error", "-select_streams", "v:0",
                 "-show_entries", "stream_tags=rotate",
                 "-of", "default=noprint_wrappers=1:nokey=1", path],
                capture_output=True, text=True,
            )
            angle_str = rot_r.stdout.strip()
            if angle_str:
                self._display_rotate = int(float(angle_str))
        except Exception:
            pass

        self._video_res = (disp_w, disp_h)

        # --- Step 3: open for frame reading ---
        if CV2_AVAILABLE:
            if self._video_cap:
                self._video_cap.release()
            self._video_cap = cv2.VideoCapture(path)
            if self._video_cap.isOpened():
                self._video_fps = self._video_cap.get(cv2.CAP_PROP_FPS) or 30.0
                frames = self._video_cap.get(cv2.CAP_PROP_FRAME_COUNT)
                self._video_duration = frames / self._video_fps if self._video_fps else 0.0
            else:
                self._video_cap = None
                self._video_duration = get_video_duration(path)
        else:
            self._video_duration = get_video_duration(path)

        # Update info label
        rot_note = f"  (rotated {self._display_rotate}°)" if self._display_rotate else ""
        fps_str  = f"{self._video_fps:.2f}".rstrip("0").rstrip(".")
        info = f"{disp_w}×{disp_h}  {fps_str} fps{rot_note}"
        if hasattr(self, "_video_info_lbl"):
            self._video_info_lbl.config(text=info)

        self._resize_preview_canvas(*self._video_res)

        if self._video_duration > 0:
            self._timeline_slider.config(to=self._video_duration)
            self._dur_lbl.config(text=self._fmt_time(self._video_duration))
        self._seek_to(0.0)

    def _seek_to(self, t: float):
        """Seek to time t: grab frame and update caption display."""
        self._current_time = max(0.0, min(t, self._video_duration or t))
        self._timeline_var.set(self._current_time)
        self._time_lbl.config(text=self._fmt_time(self._current_time))

        if CV2_AVAILABLE and self._video_cap and self._video_cap.isOpened():
            self._video_cap.set(cv2.CAP_PROP_POS_MSEC, self._current_time * 1000)
            ret, frame = self._video_cap.read()
            if ret and PIL_AVAILABLE:
                self._frame_img = self._cv2_to_pil(frame)
            else:
                self._frame_img = None

        cap_text = self._get_caption_at(self._current_time)
        if hasattr(self, "_cap_lbl"):
            self._cap_lbl.config(text=cap_text or "—")
        self._update_preview()

    def _on_timeline_scrub(self, val):
        """Called continuously as the slider moves."""
        t = float(val)
        self._current_time = t
        self._time_lbl.config(text=self._fmt_time(t))
        cap_text = self._get_caption_at(t)
        if hasattr(self, "_cap_lbl"):
            self._cap_lbl.config(text=cap_text or "—")
        if CV2_AVAILABLE and self._video_cap and self._video_cap.isOpened():
            # cv2 seeking is fast — update frame live
            self._video_cap.set(cv2.CAP_PROP_POS_MSEC, t * 1000)
            ret, frame = self._video_cap.read()
            if ret and PIL_AVAILABLE:
                self._frame_img = self._cv2_to_pil(frame)
            self._update_preview()
        else:
            # Without cv2, just update caption text (frame extracted on release)
            self._update_preview()

    def _on_timeline_release(self, event):
        """On slider mouse-up, extract frame via ffmpeg if cv2 not available."""
        if CV2_AVAILABLE:
            return
        t = self._timeline_var.get()
        video = self.video_var.get().strip()
        if not video or not os.path.isfile(video) or not PIL_AVAILABLE:
            return
        tmp = os.path.join(tempfile.gettempdir(), "caption_preview_frame.jpg")
        def _do():
            if extract_frame(video, t, tmp):
                try:
                    img = Image.open(tmp).copy()
                    self._frame_img = img
                    self.after(0, self._update_preview)
                except Exception:
                    pass
        threading.Thread(target=_do, daemon=True).start()

    def _play_pause(self):
        if self._is_playing:
            self._is_playing = False
            if self._play_job:
                self.after_cancel(self._play_job)
                self._play_job = None
            self._play_btn.config(text="▶  Play")
            return

        video = self.video_var.get().strip()
        if not video or not os.path.isfile(video):
            messagebox.showwarning("No video", "Select a video file first.")
            return
        if not CV2_AVAILABLE:
            messagebox.showwarning(
                "opencv-python required",
                "Smooth playback needs opencv-python.\n\nInstall with:\n  pip install opencv-python\n\nYou can still scrub the timeline manually.")
            return
        if not self._video_cap or not self._video_cap.isOpened():
            self._load_video_for_preview(video)
        # Reopen at current time
        if self._video_cap and self._video_cap.isOpened():
            self._video_cap.set(cv2.CAP_PROP_POS_MSEC, self._current_time * 1000)
        self._is_playing = True
        self._play_btn.config(text="⏸  Pause")
        self._playback_tick()

    def _playback_tick(self):
        if not self._is_playing or not CV2_AVAILABLE:
            return
        if not self._video_cap or not self._video_cap.isOpened():
            return
        ret, frame = self._video_cap.read()
        if ret:
            if PIL_AVAILABLE:
                self._frame_img = self._cv2_to_pil(frame)
            pos_ms = self._video_cap.get(cv2.CAP_PROP_POS_MSEC)
            self._current_time = pos_ms / 1000.0
            self._timeline_var.set(self._current_time)
            self._time_lbl.config(text=self._fmt_time(self._current_time))
            cap_text = self._get_caption_at(self._current_time)
            self._cap_lbl.config(text=cap_text or "—")
            self._update_preview()
            interval = max(1, int(1000 / self._video_fps))
            self._play_job = self.after(interval, self._playback_tick)
        else:
            # End of video — stop
            self._is_playing = False
            self._play_btn.config(text="▶  Play")
            self._play_job = None

    def _rebuild_preview_captions(self):
        """Re-apply rechunking + word replacements to raw captions, then refresh."""
        if not self._raw_captions:
            return
        caps = apply_replacements(self._raw_captions, self.repl_table.get_replacements())
        caps = rechunk_captions(caps, self.words_var.get())
        self._preview_captions = caps
        self._update_preview()

    def _update_preview(self):
        if not hasattr(self, "_canvas"):
            return
        w = self._canvas_w
        h = self._canvas_h
        cap_text = self._get_caption_at(self._current_time)
        text = cap_text if cap_text else ("(no caption at this time)" if self._preview_captions else "Load a video & SRT to preview")
        style = STYLES.get(self.style_var.get(), STYLES["Bold Impact"])
        vres = getattr(self, "_video_res", (1920, 1080))

        if PIL_AVAILABLE:
            try:
                from PIL import ImageTk
                fs = self._get_fontsize() if hasattr(self, "fontsize_var") else 28
                # Compute and display diagnostic font size info
                _scale = min(w / vres[0], h / vres[1])
                _preview_fs = max(6, int(fs * _scale))
                if hasattr(self, "_video_info_lbl"):
                    _cur = self._video_info_lbl.cget("text")
                    # Strip any previous font annotation then append
                    _base = _cur.split(" | font:")[0]
                    self._video_info_lbl.config(
                        text=f"{_base} | font: {_preview_fs}px preview  /  {fs}px video"
                    )
                img, bbox = render_preview(self._frame_img, text,
                                           style, self._pos_x, self._pos_y, w, h,
                                           fontsize=fs,
                                           play_res_x=vres[0], play_res_y=vres[1])
                self._cap_bbox   = bbox
                self._photo_img  = ImageTk.PhotoImage(img)
                self._canvas.delete("all")
                self._canvas.create_image(0, 0, anchor="nw", image=self._photo_img)
                # Dashed selection border around caption
                x0,y0,x1,y1 = bbox
                self._canvas.create_rectangle(x0-4, y0-4, x1+4, y1+4,
                                              outline="#e94560", dash=(4,3), width=1)
                self._canvas.create_text(w//2, h-10,
                                         text="click or drag anywhere to reposition",
                                         fill="#8892b0", font=("Segoe UI", 7))
            except Exception as e:
                self._canvas.delete("all")
                self._canvas.create_text(w//2, h//2, text=f"Preview error:\n{e}",
                                         fill="#e94560", font=("Segoe UI", 9), justify="center")
        else:
            # Fallback: no PIL
            self._canvas.delete("all")
            self._canvas.create_rectangle(0, 0, w, h, fill="#0d0d1a")
            if self._frame_img is None:
                self._canvas.create_text(w//2, h//2-20,
                                         text="Install Pillow for styled preview",
                                         fill="#8892b0", font=("Segoe UI", 9))
            cx = int(self._pos_x * w); cy = int(self._pos_y * h)
            r, g, b, _ = parse_ass_color(style["PrimaryColour"])
            color = f"#{r:02x}{g:02x}{b:02x}"
            self._canvas.create_text(cx, cy, text=text,
                                     fill=color, anchor="center",
                                     font=("Arial", 14, "bold" if style["Bold"] else "normal"))
            self._canvas.create_text(w//2, h-10,
                                     text="click or drag to reposition",
                                     fill="#8892b0", font=("Segoe UI", 7))

    # ── SRT editor helpers ─────────────────────────────────────────────────────

    def _load_srt_into_editor(self, path: str):
        try:
            text = Path(path).read_text(encoding="utf-8", errors="ignore")
            self._srt_editor.delete("1.0", "end")
            self._srt_editor.insert("1.0", text.rstrip("\n"))
            self._raw_captions = parse_srt(text)
            self._log(f"Loaded SRT into editor: {Path(path).name}", "info")
            self._rebuild_preview_captions()
        except Exception as e:
            self._log(f"[WARN] Could not load SRT into editor: {e}", "warn")

    def _refresh_preview_from_editor(self):
        text = self._srt_editor.get("1.0", "end").strip()
        if text:
            try:
                self._raw_captions = parse_srt(text)
                self._rebuild_preview_captions()
                self._log(f"Preview updated — {len(self._preview_captions)} captions.", "info")
            except Exception as e:
                self._log(f"[WARN] Could not parse editor SRT: {e}", "warn")

    def _reload_srt_from_file(self):
        path = self.srt_var.get().strip()
        if not path or not os.path.isfile(path):
            messagebox.showwarning("No SRT file", "No SRT file is currently selected.")
            return
        self._load_srt_into_editor(path)

    def _save_srt_to_file(self):
        path = self.srt_var.get().strip()
        if not path:
            path = filedialog.asksaveasfilename(
                title="Save SRT as",
                defaultextension=".srt",
                filetypes=[("SRT files", "*.srt"), ("All files", "*.*")])
            if not path:
                return
            self.srt_var.set(path)
        try:
            content = self._srt_editor.get("1.0", "end").rstrip("\n") + "\n"
            Path(path).write_text(content, encoding="utf-8")
            self._log(f"[OK] Saved SRT: {Path(path).name}", "ok")
        except Exception as e:
            self._log(f"[ERROR] Could not save SRT: {e}", "fail")

    # ── File browsers ──────────────────────────────────────────────────────────

    def _browse_video(self):
        path = filedialog.askopenfilename(
            title="Select video file",
            filetypes=[("Video files","*.mp4 *.mov *.mkv *.avi *.webm *.m4v"),("All files","*.*")])
        if not path: return
        self.video_var.set(path)
        # Load video into preview player (fast — just opens file + gets metadata)
        self._load_video_for_preview(path)
        srt = Path(path).with_suffix(".srt")
        if srt.exists() and not self.srt_var.get():
            self.srt_var.set(str(srt))
            self._log(f"Auto-detected SRT: {srt.name}", "ok")
            self._load_srt_into_editor(str(srt))

    def _browse_srt(self):
        path = filedialog.askopenfilename(title="Select SRT file",
            filetypes=[("SRT files","*.srt"),("All files","*.*")])
        if path:
            self.srt_var.set(path)
            self._load_srt_into_editor(path)

    # ── Logging (thread-safe) ──────────────────────────────────────────────────

    def _log(self, msg: str, tag: str = "info"):
        def _do():
            self.log_box.config(state="normal")
            self.log_box.insert("end", msg+"\n", tag)
            self.log_box.see("end")
            self.log_box.config(state="disabled")
        self.after(0, _do)

    def _prog(self, done: float, total: float):
        def _do():
            pct = int(done/total*100) if total else 0
            self.prog_bar["value"] = pct
            self.prog_lbl.config(text=f"{pct}%  ({done:.0f}s / {total:.0f}s)" if total else "")
        self.after(0, _do)

    # ── Start / Worker ─────────────────────────────────────────────────────────

    def _start(self):
        video = self.video_var.get().strip()
        if not video or not os.path.isfile(video):
            messagebox.showerror("Error", "Select a valid video file first.")
            return
        pos_override = (self._pos_x, self._pos_y) if self._pos_custom else None
        self.cfg.update({
            "last_video": video, "last_output_dir": self.out_dir_var.get().strip(),
            "style": self.style_var.get(), "words_per_caption": self.words_var.get(),
            "replacements": self.repl_table.get_replacements(),
            "whisper_model": self.model_var.get(),
            "pos_x": self._pos_x if self._pos_custom else None,
            "pos_y": self._pos_y if self._pos_custom else None,
            "font_size": self._get_fontsize(),
        })
        save_config(self.cfg)
        self.btn.config(state="disabled", text="Processing…")
        self.log_box.config(state="normal"); self.log_box.delete("1.0","end")
        self.log_box.config(state="disabled"); self.prog_bar["value"] = 0
        editor_srt = self._srt_editor.get("1.0", "end").strip()
        threading.Thread(target=self._worker,
                         args=(video, self.srt_var.get().strip(),
                               self.cfg["last_output_dir"], self.cfg["style"],
                               self.cfg["words_per_caption"], self.cfg["replacements"],
                               self.cfg["whisper_model"], pos_override,
                               self.cfg["font_size"], editor_srt),
                         daemon=True).start()

    def _worker(self, video, srt_path, out_dir, style, words_per,
                replacements, model, pos_override, fontsize=28, editor_srt=""):
        video_p = Path(video)
        try:
            # 1. Load / generate captions
            captions = None
            if editor_srt:
                self._log("Using SRT from editor…", "info")
                try:
                    captions = parse_srt(editor_srt)
                    self._log(f"[OK] {len(captions)} caption blocks from editor.", "ok")
                except Exception as e:
                    self._log(f"[ERROR] SRT parse failed: {e}", "fail"); return
            elif srt_path and os.path.isfile(srt_path):
                self._log(f"Loading SRT: {Path(srt_path).name}", "info")
                try:
                    captions = parse_srt(Path(srt_path).read_text(encoding="utf-8", errors="ignore"))
                    self._log(f"[OK] {len(captions)} caption blocks.", "ok")
                except Exception as e:
                    self._log(f"[ERROR] SRT parse failed: {e}", "fail"); return

            if captions is None:
                auto = video_p.with_suffix(".srt")
                if auto.exists():
                    self._log(f"Auto-detected: {auto.name}", "info")
                    try:
                        captions = parse_srt(auto.read_text(encoding="utf-8", errors="ignore"))
                        self._log(f"[OK] {len(captions)} caption blocks.", "ok")
                    except Exception as e:
                        self._log(f"[WARN] Could not parse: {e}", "warn")

            if captions is None:
                self._log("No SRT found — generating via Whisper…", "info")
                captions = generate_srt_from_video(video, model, self._log, self._prog)
                if captions is None: return
                gen = video_p.with_suffix(".srt")
                try:
                    srt_text = captions_to_srt(captions)
                    gen.write_text(srt_text, encoding="utf-8")
                    self._log(f"[OK] Saved SRT: {gen.name}", "ok")
                    def _load_generated(t=srt_text):
                        self._srt_editor.delete("1.0", "end")
                        self._srt_editor.insert("1.0", t.rstrip("\n"))
                        self._raw_captions = parse_srt(t)
                        self._rebuild_preview_captions()
                    self.after(0, _load_generated)
                except Exception as e:
                    self._log(f"[WARN] SRT save: {e}", "warn")

            if not captions:
                self._log("[ERROR] No captions available.", "fail"); return

            # 2. Process
            if replacements:
                captions = apply_replacements(captions, replacements)
                self._log(f"Applied {len(replacements)} replacement(s).", "info")
            captions = rechunk_captions(captions, words_per)
            self._log(f"Re-chunked to {len(captions)} blocks ({words_per} words max).", "info")

            res_x, res_y = get_video_resolution(video)
            self._log(f"Video: {res_x}×{res_y}", "info")
            if pos_override:
                self._log(f"Custom position: {pos_override[0]:.2f}, {pos_override[1]:.2f}", "info")

            self._log(f"Font size: {fontsize}px", "info")
            # 3. Build ASS + burn
            ass_content = build_ass(captions, style, res_x, res_y, pos_override, fontsize=fontsize)
            ass_tmp = os.path.join(tempfile.gettempdir(), "caption_burn_tmp.ass")
            with open(ass_tmp, "w", encoding="utf-8") as f:
                f.write(ass_content)
            try:
                suffix   = f"_captioned_{style.lower().replace(' ','_')}"
                out_name = video_p.stem + suffix + video_p.suffix
                out_path = str(
                    (Path(out_dir) if out_dir and os.path.isdir(out_dir) else video_p.parent)
                    / out_name
                )
                self._log(f"Burning [{style}] → {out_path}", "info")
                ok = burn_captions(video, ass_tmp, out_path, progress_cb=self._prog)
                if ok:
                    self._log(f"[OK] Saved: {Path(out_path).name}", "ok")
                    self.after(0, lambda: (self.prog_bar.config(value=100),
                                           self.prog_lbl.config(text="Complete!")))
                else:
                    self._log("[FAIL] ffmpeg error — check ffmpeg is on PATH.", "fail")
            finally:
                try: os.unlink(ass_tmp)
                except Exception: pass

        except Exception as e:
            import traceback
            self._log(f"[ERROR] {e}", "fail")
            self._log(traceback.format_exc(), "fail")
        finally:
            self.after(0, lambda: self.btn.config(state="normal",
                                                   text="Burn Captions  →  Export Video"))


# ── Entry point ────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    app = CaptionApp()
    app.mainloop()
