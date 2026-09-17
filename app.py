"""
메이플 스킬바 미러 — 스킬 칸 방식
게임 화면 위에 스킬 아이콘 크기의 네모(칸)를 올려두면, 그 칸 안에 보이는 것을
항상 위에 뜨는 미러 창에 모아서 확대해 보여준다.
게임 프로세스에는 손대지 않고 모니터에 보이는 것만 캡처한다.

칸 편집: 테두리 드래그 = 이동 / 테두리 위에서 휠 = 크기 / 오른쪽 클릭 = 삭제
칸 안쪽은 투명하고 클릭이 게임으로 통과된다.
미러 창은 왼쪽 버튼 드래그로 옮긴다.
"""
import ctypes
import ctypes.wintypes
import json
import sys
import time
import tkinter as tk
from tkinter import ttk
from pathlib import Path

import mss
from PIL import Image, ImageTk

# 윈도우 배율(125%, 150%)이 걸려 있어도 tkinter 좌표와 캡처 픽셀이 어긋나지 않게
try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except Exception:
    pass

# exe로 묶였을 때도 설정 파일은 exe 옆에 둔다
BASE_DIR = Path(sys.executable).parent if getattr(sys, "frozen", False) else Path(__file__).parent
CONFIG_PATH = BASE_DIR / "config.json"

DEFAULTS = {
    "boxes": [],           # [{"left", "top", "size"}, ...] 화면 좌표. 칸 안쪽(캡처 영역)만 저장
    "box_size": 32,        # 모든 칸의 크기(픽셀). 스킬 아이콘에 딱 맞게 조절
    "per_row": 0,          # 미러에 한 줄에 몇 칸씩. 0 = 한 줄로
    "gap": 2,              # 미러에서 칸 사이 간격(픽셀, 확대 전)
    "scale": 1.5,
    "alpha": 1.0,
    "fps": 15,
    "topmost": True,
    "mirror_pos": {"x": 100, "y": 1200},
    "panel_pos": {"x": 100, "y": 1450},
}

BORDER = 3                # 칸 테두리 두께. 캡처 영역 바깥에 그려진다
ICON_GAP = 3              # 메이플 스킬 아이콘 사이 간격(픽셀). 2026-09-18 실제 화면에서 잼
TRANSPARENT = "#ff00fe"   # 이 색은 창에서 뚫려 보이고 클릭도 통과된다
COLOR_EDIT = "#00ff88"


class MapleWindow:
    """제목 MapleStory / 클래스 MapleStoryClass 창을 찾아 게임 화면(클라이언트 영역)의
    왼쪽 위 화면 좌표를 돌려준다. 칸 위치는 이 점을 기준으로 저장해서 창을 옮겨도 따라간다."""

    TITLE = "MapleStory"
    CLASS = "MapleStoryClass"

    def __init__(self):
        self.user32 = ctypes.windll.user32
        self.hwnd = None
        self.origin = None      # (x, y) 또는 None(창 없음)
        self._last_check = 0.0

    def _find(self):
        hwnd = self.user32.FindWindowW(self.CLASS, None) or self.user32.FindWindowW(None, self.TITLE)
        return hwnd or None

    def refresh(self):
        """0.5초에 한 번만 실제로 찾는다. 창이 사라지면 origin이 None이 된다."""
        now = time.monotonic()
        if now - self._last_check < 0.5:
            return self.origin
        self._last_check = now
        if not self.hwnd or not self.user32.IsWindow(self.hwnd):
            self.hwnd = self._find()
        if not self.hwnd or not self.user32.IsWindowVisible(self.hwnd) or self.user32.IsIconic(self.hwnd):
            self.origin = None
            return None
        pt = ctypes.wintypes.POINT(0, 0)
        if not self.user32.ClientToScreen(self.hwnd, ctypes.byref(pt)):
            self.origin = None
            return None
        self.origin = (pt.x, pt.y)
        return self.origin


def box_screen_pos(box, origin):
    """칸의 화면 좌표. 메이플 창이 있으면 창 기준(dx, dy)으로, 없으면 마지막 절대 좌표."""
    if origin and "dx" in box:
        return origin[0] + box["dx"], origin[1] + box["dy"]
    return box["left"], box["top"]


def box_set_screen_pos(box, left, top, origin):
    """칸을 화면 좌표로 옮기고, 메이플 창이 있으면 창 기준 좌표도 같이 갱신."""
    box["left"], box["top"] = int(left), int(top)
    if origin:
        box["dx"], box["dy"] = int(left) - origin[0], int(top) - origin[1]


def load_config():
    cfg = dict(DEFAULTS)
    if CONFIG_PATH.exists():
        try:
            data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
            cfg.update({k: v for k, v in data.items() if k in DEFAULTS})
        except Exception:
            pass
    return cfg


def save_config(cfg):
    CONFIG_PATH.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")


def primary_monitor():
    with mss.MSS() as sct:
        for m in sct.monitors[1:]:
            if m.get("is_primary"):
                return m
        return sct.monitors[1]


def grab(sct, left, top, w, h):
    shot = sct.grab({"left": int(left), "top": int(top), "width": int(w), "height": int(h)})
    return Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX")


def ordered_boxes(boxes):
    """화면에서 위→아래, 왼→오른 순서. 세로가 칸 절반 이내면 같은 줄로 본다."""
    if not boxes:
        return []
    bs = sorted(boxes, key=lambda b: (b["top"], b["left"]))
    rows, cur, row_top = [], [], bs[0]["top"]
    for b in bs:
        if abs(b["top"] - row_top) > b["size"] / 2:
            rows.append(sorted(cur, key=lambda x: x["left"]))
            cur, row_top = [], b["top"]
        cur.append(b)
    rows.append(sorted(cur, key=lambda x: x["left"]))
    return [b for row in rows for b in row]


def compose(sct, cfg, origin):
    """모든 칸을 캡처해 한 장으로 붙인다. 칸이 없으면 None."""
    boxes = ordered_boxes(cfg["boxes"])
    if not boxes:
        return None
    cells = []
    for b in boxes:
        x, y = box_screen_pos(b, origin)
        cells.append(grab(sct, x, y, b["size"], b["size"]))
    cw = max(c.width for c in cells)
    ch = max(c.height for c in cells)
    gap = int(cfg["gap"])
    per_row = int(cfg["per_row"]) or len(cells)
    n_rows = -(-len(cells) // per_row)
    out = Image.new("RGB", (per_row * cw + (per_row - 1) * gap, n_rows * ch + (n_rows - 1) * gap), "#111111")
    for i, cell in enumerate(cells):
        rr, cc = divmod(i, per_row)
        out.paste(cell, (cc * (cw + gap), rr * (ch + gap)))
    return out


class SkillBox:
    """게임 화면 위에 올려두는 네모 하나. 안쪽은 투명·클릭 통과, 테두리만 잡힌다."""

    def __init__(self, root, box, on_change, on_delete, on_resize, get_origin):
        self.box = box
        self.get_origin = get_origin
        self.on_change = on_change
        self.on_delete = on_delete
        self.on_resize = on_resize  # 휠로 크기를 바꾸면 모든 칸에 같이 적용된다
        self.win = tk.Toplevel(root)
        self.win.overrideredirect(True)
        self.win.attributes("-topmost", True)
        self.win.attributes("-transparentcolor", TRANSPARENT)
        self.win.configure(bg=COLOR_EDIT, cursor="fleur")
        self.inner = tk.Frame(self.win, bg=TRANSPARENT)
        self.inner.place(x=BORDER, y=BORDER)
        self.apply_geometry()
        self.win.bind("<ButtonPress-1>", self.drag_start)
        self.win.bind("<B1-Motion>", self.drag_move)
        self.win.bind("<ButtonRelease-1>", lambda e: self.commit())
        self.win.bind("<MouseWheel>", self.on_wheel)
        self.win.bind("<ButtonPress-3>", lambda e: self.on_delete(self))
        self._drag = None

    def apply_geometry(self):
        s = int(self.box["size"])
        x, y = box_screen_pos(self.box, self.get_origin())
        self.win.geometry(f"{s + 2 * BORDER}x{s + 2 * BORDER}+{int(x) - BORDER}+{int(y) - BORDER}")
        self.inner.configure(width=s, height=s)

    def drag_start(self, e):
        self._drag = (e.x_root - self.win.winfo_x(), e.y_root - self.win.winfo_y())

    def drag_move(self, e):
        dx, dy = self._drag
        box_set_screen_pos(self.box, e.x_root - dx + BORDER, e.y_root - dy + BORDER, self.get_origin())
        self.apply_geometry()

    def on_wheel(self, e):
        step = 1 if e.delta > 0 else -1
        self.on_resize(max(12, min(200, int(self.box["size"]) + step)))

    def commit(self):
        self.on_change()

    def destroy(self):
        self.win.destroy()


class MirrorWindow:
    def __init__(self, root, cfg, on_move, get_origin):
        self.root = root
        self.cfg = cfg
        self.on_move = on_move
        self.get_origin = get_origin
        self.sct = mss.MSS()
        self._photo = None
        self._drag = None
        self._job = None

        self.win = tk.Toplevel(root)
        self.win.overrideredirect(True)
        self.win.configure(bg="#111")
        pos = cfg["mirror_pos"]
        self.win.geometry(f"+{pos['x']}+{pos['y']}")
        self.label = tk.Label(self.win, bg="#111", fg="#888", bd=0, cursor="fleur",
                              text="  칸을 추가하세요  ", font=("Malgun Gothic", 11), padx=10, pady=10)
        self.label.pack()
        self.label.bind("<ButtonPress-1>", self.drag_start)
        self.label.bind("<B1-Motion>", self.drag_move)
        self.label.bind("<ButtonRelease-1>", lambda e: self.on_move())
        self.apply_settings()
        self.tick()

    def apply_settings(self):
        self.win.attributes("-topmost", bool(self.cfg["topmost"]))
        self.win.attributes("-alpha", float(self.cfg["alpha"]))

    def drag_start(self, e):
        self._drag = (e.x_root - self.win.winfo_x(), e.y_root - self.win.winfo_y())

    def drag_move(self, e):
        dx, dy = self._drag
        self.win.geometry(f"+{e.x_root - dx}+{e.y_root - dy}")

    def position(self):
        return {"x": self.win.winfo_x(), "y": self.win.winfo_y()}

    def tick(self):
        try:
            img = compose(self.sct, self.cfg, self.get_origin())
            if img is None:
                self._photo = None
                self.label.configure(image="", text="  칸을 추가하세요  ")
            else:
                s = float(self.cfg["scale"])
                img = img.resize((max(1, int(img.width * s)), max(1, int(img.height * s))), Image.NEAREST)
                self._photo = ImageTk.PhotoImage(img)
                self.label.configure(image=self._photo, text="", padx=0, pady=0)
        except Exception as ex:
            print("capture error:", ex, file=sys.stderr)
        self._job = self.root.after(int(1000 / max(1, int(self.cfg["fps"]))), self.tick)

    def close(self):
        if self._job:
            self.root.after_cancel(self._job)
        self.sct.close()
        self.win.destroy()


class ControlPanel:
    def __init__(self, root, cfg):
        self.root = root
        self.cfg = cfg
        self.mirror = None
        self.skill_boxes = []   # 편집 중일 때만 화면에 떠 있는 SkillBox들
        self._editing = False
        self.maple = MapleWindow()
        self.maple.refresh()
        self.migrate_boxes()

        self.win = tk.Toplevel(root)
        self.win.title("메이플 스킬바 미러")
        self.win.resizable(False, False)
        pos = cfg["panel_pos"]
        self.win.geometry(f"+{pos['x']}+{pos['y']}")
        self.win.protocol("WM_DELETE_WINDOW", self.quit)
        self.win.attributes("-topmost", bool(cfg["topmost"]))

        pad = {"padx": 10, "pady": 4}
        frm = ttk.Frame(self.win, padding=10)
        frm.pack(fill="both", expand=True)

        # ---- 칸 편집 ----
        box_frame = ttk.LabelFrame(frm, text="스킬 칸", padding=8)
        box_frame.pack(fill="x", **pad)
        row1 = ttk.Frame(box_frame)
        row1.pack(fill="x")
        ttk.Button(row1, text="칸 추가", command=self.add_box).pack(side="left", padx=(0, 6))
        self.edit_btn = ttk.Button(row1, text="칸 편집", command=self.toggle_edit)
        self.edit_btn.pack(side="left", padx=(0, 6))
        ttk.Button(row1, text="모두 삭제", command=self.clear_boxes).pack(side="right")

        row2 = ttk.Frame(box_frame)
        row2.pack(fill="x", pady=(8, 0))
        self.size_var = tk.IntVar(value=cfg["box_size"])
        ttk.Label(row2, text="칸 크기").pack(side="left")
        ttk.Spinbox(row2, from_=12, to=200, increment=1, width=5, textvariable=self.size_var,
                    command=self.on_size_change).pack(side="left", padx=(4, 14))
        self.per_row_var = tk.IntVar(value=cfg["per_row"])
        ttk.Label(row2, text="한 줄에").pack(side="left")
        ttk.Spinbox(row2, from_=0, to=30, width=4, textvariable=self.per_row_var,
                    command=self.on_change).pack(side="left", padx=4)
        ttk.Label(row2, text="칸 (0 = 한 줄)").pack(side="left")

        self.status = ttk.Label(box_frame, text="", foreground="#666")
        self.status.pack(anchor="w", pady=(8, 0))
        self.hint = ttk.Label(box_frame, foreground="#888", justify="left",
                              text="편집 중: 테두리를 끌어 스킬 위에 놓기 · 휠 또는 [칸 크기] = 전체 크기 · 오른쪽 클릭 = 삭제")
        self.hint.pack(anchor="w", pady=(2, 0))

        # ---- 미러 ----
        mir = ttk.Frame(frm)
        mir.pack(fill="x", **pad)
        self.toggle_btn = ttk.Button(mir, text="미러 끄기", command=self.toggle_mirror)
        self.toggle_btn.pack(side="left", padx=(0, 6))
        ttk.Button(mir, text="미러를 다른 모니터로", command=self.move_mirror_monitor).pack(side="left")
        ttk.Button(mir, text="종료", command=self.quit).pack(side="right")

        self.scale_var = tk.DoubleVar(value=cfg["scale"])
        self.alpha_var = tk.DoubleVar(value=cfg["alpha"])
        self.fps_var = tk.IntVar(value=cfg["fps"])
        self.topmost_var = tk.BooleanVar(value=cfg["topmost"])
        self._slider(frm, "확대 배율", self.scale_var, 0.5, 4.0, 0.1, lambda v: f"{v:.1f}배")
        self._slider(frm, "투명도", self.alpha_var, 0.2, 1.0, 0.05, lambda v: f"{int(v * 100)}%")
        self._slider(frm, "갱신 속도", self.fps_var, 1, 60, 1, lambda v: f"초당 {int(v)}회")
        ttk.Checkbutton(frm, text="항상 위에 두기", variable=self.topmost_var,
                        command=self.on_change).pack(anchor="w", **pad)
        ttk.Label(frm, text="미러 창은 마우스로 끌어서 옮길 수 있어요.", foreground="#888").pack(anchor="w", **pad)

        self.start_mirror()
        self.update_status()
        self.tick_follow()
        if not cfg["boxes"]:
            self.set_edit(True)  # 처음이면 바로 편집 모드
        else:
            self.set_edit(False)

    def _slider(self, parent, title, var, lo, hi, step, fmt):
        row = ttk.Frame(parent)
        row.pack(fill="x", padx=10, pady=4)
        ttk.Label(row, text=title, width=9).pack(side="left")
        value_lbl = ttk.Label(row, text=fmt(var.get()), width=9)
        value_lbl.pack(side="right")

        def on_slide(v):
            val = round(float(v) / step) * step
            var.set(val)
            value_lbl.configure(text=fmt(val))
            self.on_change()

        ttk.Scale(row, from_=lo, to=hi, variable=var, command=on_slide, length=200).pack(
            side="left", fill="x", expand=True, padx=6)

    # ---- 칸 ----
    def set_edit(self, on):
        self._editing = on
        for sb in self.skill_boxes:
            sb.destroy()
        self.skill_boxes = []
        if on:
            for b in self.cfg["boxes"]:
                self.skill_boxes.append(SkillBox(self.root, b, self.save_boxes, self.delete_box, self.set_all_size, self.maple.refresh))
        self.edit_btn.configure(text="편집 끝" if on else "칸 편집")
        self.hint.configure(text=(
            "편집 중: 테두리를 끌어 스킬 위에 놓기 · 휠 또는 [칸 크기] = 전체 크기 · 오른쪽 클릭 = 삭제"
            if on else "잠김: 칸 위치를 바꾸려면 [칸 편집]"))

    def toggle_edit(self):
        self.set_edit(not self._editing)

    def add_box(self):
        size = int(self.cfg["box_size"])
        if self.cfg["boxes"]:
            lx, ly = box_screen_pos(self.cfg["boxes"][-1], self.maple.refresh())
            box = {"left": lx + size + ICON_GAP, "top": ly, "size": size}
        elif self.maple.refresh():
            ox, oy = self.maple.origin   # 첫 칸은 메이플 창 안쪽에 만들어서 바로 눈에 띄게
            box = {"left": ox + 200, "top": oy + 200, "size": size}
        else:
            m = primary_monitor()
            box = {"left": m["left"] + m["width"] // 2, "top": m["top"] + m["height"] // 2, "size": size}
        box_set_screen_pos(box, box["left"], box["top"], self.maple.refresh())
        self.cfg["boxes"].append(box)
        if not self._editing:
            self.set_edit(True)
        else:
            self.skill_boxes.append(SkillBox(self.root, box, self.save_boxes, self.delete_box, self.set_all_size, self.maple.refresh))
        self.save_boxes()

    def delete_box(self, sb):
        if sb.box in self.cfg["boxes"]:
            self.cfg["boxes"].remove(sb.box)
        if sb in self.skill_boxes:
            self.skill_boxes.remove(sb)
        sb.destroy()
        self.save_boxes()

    def clear_boxes(self):
        self.cfg["boxes"] = []
        self.set_edit(True)
        self.save_boxes()

    def set_all_size(self, size):
        """모든 칸을 같은 크기로. 스킬 아이콘은 다 같은 크기라서 하나만 맞추면 된다."""
        size = max(12, min(200, int(size)))
        self.cfg["box_size"] = size
        self.size_var.set(size)
        for b in self.cfg["boxes"]:
            b["size"] = size
        for sb in self.skill_boxes:
            sb.apply_geometry()
        self.save_boxes()

    def on_size_change(self):
        try:
            self.set_all_size(int(self.size_var.get()))
        except tk.TclError:
            pass

    def save_boxes(self):
        self.save()
        self.update_status()

    def migrate_boxes(self):
        """예전 저장(절대 좌표만)에 창 기준 좌표를 붙인다. 창을 못 찾으면 다음 기회에."""
        origin = self.maple.origin
        if not origin:
            return
        for b in self.cfg["boxes"]:
            if "dx" not in b:
                box_set_screen_pos(b, b["left"], b["top"], origin)

    def update_status(self):
        n = len(self.cfg["boxes"])
        o = self.maple.origin
        win = f"메이플 창 찾음 ({o[0]}, {o[1]}) → 칸이 창을 따라감" if o else "메이플 창 없음 → 마지막 자리 그대로"
        head = f"칸 {n}개" if n else "칸이 없어요. [칸 추가]를 눌러 스킬 위에 올리세요."
        self.status.configure(text=f"{head}  ·  {win}")

    def tick_follow(self):
        """0.5초마다 메이플 창 위치를 확인해서 편집 중인 테두리와 상태 글을 맞춘다."""
        before = self.maple.origin
        after = self.maple.refresh()
        if after != before:
            if after:
                self.migrate_boxes()
            for sb in self.skill_boxes:
                sb.apply_geometry()
            self.update_status()
        self.root.after(500, self.tick_follow)

    # ---- 설정 ----
    def on_change(self):
        self.cfg["scale"] = round(self.scale_var.get(), 2)
        self.cfg["alpha"] = round(self.alpha_var.get(), 2)
        self.cfg["fps"] = int(self.fps_var.get())
        self.cfg["topmost"] = bool(self.topmost_var.get())
        try:
            self.cfg["per_row"] = max(0, int(self.per_row_var.get()))
        except tk.TclError:
            pass
        self.win.attributes("-topmost", self.cfg["topmost"])
        if self.mirror:
            self.mirror.apply_settings()
        self.save()

    def save(self):
        if self.mirror:
            self.cfg["mirror_pos"] = self.mirror.position()
        self.cfg["panel_pos"] = {"x": self.win.winfo_x(), "y": self.win.winfo_y()}
        save_config(self.cfg)

    # ---- 미러 ----
    def toggle_mirror(self):
        if self.mirror:
            self.stop_mirror()
        else:
            self.start_mirror()

    def start_mirror(self):
        if not self.mirror:
            self.mirror = MirrorWindow(self.root, self.cfg, on_move=self.save, get_origin=self.maple.refresh)
        self.toggle_btn.configure(text="미러 끄기")

    def move_mirror_monitor(self):
        """미러 창을 다음 모니터의 왼쪽 위로 옮긴다. 눌러서 원하는 모니터가 나오면 거기서 끌어 조정."""
        if not self.mirror:
            self.start_mirror()
        with mss.MSS() as sct:
            mons = sct.monitors[1:]
        x, y = self.mirror.win.winfo_x(), self.mirror.win.winfo_y()
        cur = 0
        for i, m in enumerate(mons):
            if m["left"] <= x < m["left"] + m["width"] and m["top"] <= y < m["top"] + m["height"]:
                cur = i
                break
        nxt = mons[(cur + 1) % len(mons)]
        self.mirror.win.geometry(f"+{nxt['left'] + 40}+{nxt['top'] + 40}")
        self.save()

    def stop_mirror(self):
        if self.mirror:
            self.save()
            self.mirror.close()
            self.mirror = None
        self.toggle_btn.configure(text="미러 켜기")

    def quit(self):
        self.save()
        self.root.destroy()


def main():
    root = tk.Tk()
    root.withdraw()
    ControlPanel(root, load_config())
    root.mainloop()


if __name__ == "__main__":
    main()
