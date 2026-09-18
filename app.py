"""
메이플 스킬바 미러 — 스킬 칸 + 캐릭터 따라가기
게임 화면 위에 스킬 아이콘 크기의 네모(칸)를 올려두면, 그 칸 안에 보이는 것을
항상 위에 뜨는 미러 창에 모아서 확대해 보여준다.
캐릭터는 이름표 그림을 표식으로 저장해 두고 매 프레임 찾아서 그 위쪽을 잘라 보여준다.
게임 프로세스에는 손대지 않고 모니터에 보이는 것만 캡처한다.

칸 편집: 테두리 드래그 = 이동 / 테두리 위에서 휠 = 크기 / 오른쪽 클릭 = 삭제
칸 안쪽은 투명하고(윈도우에서는 클릭도 게임으로 통과) 테두리만 잡힌다.
미러 창은 왼쪽 버튼 드래그로 옮긴다.
"""
import ctypes
import json
import sys
import time
import traceback
import tkinter as tk
from tkinter import messagebox, ttk
from pathlib import Path

import cv2
import mss
import numpy as np
from PIL import Image, ImageDraw, ImageTk

IS_WIN = sys.platform == "win32"
IS_MAC = sys.platform == "darwin"

if IS_WIN:
    import ctypes.wintypes
    # 윈도우 배율(125%, 150%)이 걸려 있어도 tkinter 좌표와 캡처 픽셀이 어긋나지 않게
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        pass

if IS_MAC:
    # 맥 .app 안은 쓰기가 막힐 수 있어서 사용자 폴더에 저장한다
    BASE_DIR = Path.home() / "Library" / "Application Support" / "MapleSkillMirror"
    BASE_DIR.mkdir(parents=True, exist_ok=True)
else:
    # exe로 묶였을 때도 설정 파일은 exe 옆에 둔다
    BASE_DIR = Path(sys.executable).parent if getattr(sys, "frozen", False) else Path(__file__).parent
CONFIG_PATH = BASE_DIR / "config.json"
LOG_PATH = BASE_DIR / "log.txt"
TAG_PATH = BASE_DIR / "nametag.png"   # 캐릭터 이름표 표식 그림

UI_FONT = ("Apple SD Gothic Neo", 12) if IS_MAC else ("Malgun Gothic", 11)


def log(msg):
    """오류를 log.txt에 남긴다. 다른 PC에서 문제가 나면 이 파일을 받아 본다."""
    try:
        with LOG_PATH.open("a", encoding="utf-8") as f:
            f.write(time.strftime("[%Y-%m-%d %H:%M:%S] ") + msg + "\n")
    except Exception:
        pass


def report_error(title, exc):
    text = "".join(traceback.format_exception(exc))
    log(f"{title}\n{text}")
    try:
        messagebox.showerror("메이플 스킬바 미러 - 오류", f"{title}\n\n{text}\n\n{LOG_PATH} 파일을 보내주세요.")
    except Exception:
        pass


DEFAULTS = {
    "boxes": [],           # [{"left", "top", "size", "dx", "dy"}, ...] 스킬 칸. dx,dy는 메이플 창 기준
    "box_size": 32,        # 모든 스킬 칸의 크기(픽셀). 메이플 기본 아이콘이 32
    "per_row": 0,          # 미러에 한 줄에 몇 칸씩. 0 = 한 줄로
    "gap": 2,              # 미러에서 칸 사이 간격(픽셀, 확대 전)
    "scale": 1.5,
    "alpha": 1.0,
    "fps": 15,
    "topmost": True,
    "mirror_pos": {"x": 100, "y": 100},
    "char_pos": {"x": 100, "y": 200},
    "panel_pos": {"x": 100, "y": 400},
    "char": {
        "enabled": False,
        "tag": None,       # 이름표 칸 {"left","top","w","h","dx","dy"} — 표식을 저장할 때 쓰는 편집용 칸
        "tag_w": 137,      # 이름표 칸 기본 크기. 2026-09-18 실제 화면(기본 UI 배율)에서 잼
        "tag_h": 30,
        "w": 160,          # 캐릭터 칸(이름표 위쪽) 크기
        "h": 200,
        "scale": 1.0,
        "round": True,     # 캐릭터 창을 동그랗게 (윈도우만. 맥 tk는 색 뚫기가 없어 네모)
        "threshold": 0.65, # 이 점수 이상이면 찾은 것으로 본다 (0~1)
        "hold": 2.0,       # 못 찾을 때 마지막 위치를 유지하는 시간(초)
    },
    "idle": {
        "enabled": True,   # 캐릭터가 멈추면(스킬을 안 쓰면) 미러 창 테두리를 빨갛게 깜빡인다
        "seconds": 3.0,    # 이 시간 동안 캐릭터 주변에 변화가 없으면 멈춘 것으로 본다
        "sensitivity": 6,  # 프레임 간 평균 밝기 차이(0~255). 이보다 크면 "움직임"으로 본다
        "watch": None,     # 감시 칸 {"left","top","w","h","dx","dy"}. 사냥 중에만 바뀌는 숫자(경험치 등) 위에 둔다.
                           # 있으면 캐릭터 칸 대신 이 칸의 변화로 멈춤을 판단한다 (지나가는 몹에 안 속는다)
        "watch_w": 220,
        "watch_h": 20,
    },
}

BORDER = 3                # 칸 테두리 두께. 캡처 영역 바깥에 그려진다
ICON_GAP = 3              # 메이플 스킬 아이콘 사이 간격(픽셀). 2026-09-18 실제 화면에서 잼
TRANSPARENT = "#ff00fe"   # 윈도우: 이 색은 창에서 뚫려 보이고 클릭도 통과된다
COLOR_EDIT = "#00ff88"    # 스킬 칸 테두리
COLOR_TAG = "#00ccff"     # 이름표 칸 테두리
COLOR_WATCH = "#ff9900"   # 감시 칸 테두리


# ---------------------------------------------------------------- 설정
def load_config():
    cfg = json.loads(json.dumps(DEFAULTS))
    if CONFIG_PATH.exists():
        try:
            data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
            char = data.pop("char", {}) or {}
            idle = data.pop("idle", {}) or {}
            cfg.update({k: v for k, v in data.items() if k in DEFAULTS})
            cfg["char"].update({k: v for k, v in char.items() if k in DEFAULTS["char"]})
            cfg["idle"].update({k: v for k, v in idle.items() if k in DEFAULTS["idle"]})
        except Exception as ex:
            log(f"config load error: {ex!r}")
    return cfg


def save_config(cfg):
    CONFIG_PATH.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")


# ---------------------------------------------------------------- 화면·창
def primary_monitor():
    with mss.MSS() as sct:
        for m in sct.monitors[1:]:
            if m.get("is_primary"):
                return m
        return sct.monitors[1]


def clamp_to_screen(x, y):
    """창 위치가 어느 모니터에도 안 들어가면 주 모니터 안으로 끌어온다.
    (모니터 수·해상도가 다른 PC에서 저장된 위치를 그대로 쓰면 화면 밖에 뜰 수 있다)"""
    with mss.MSS() as sct:
        mons = sct.monitors[1:]
    px, py = x + 40, y + 10   # 창 왼쪽 위에서 조금 안쪽 점. 모서리에 딱 붙은 창(x=-2 등)도 화면 안으로 본다
    for m in mons:
        if m["left"] <= px < m["left"] + m["width"] and m["top"] <= py < m["top"] + m["height"]:
            return x, y
    m = primary_monitor()
    return m["left"] + 80, m["top"] + 80


class Screen:
    """화면 캡처. 윈도우에서는 dxcam(DXGI 데스크톱 복제, 한 번에 1ms 미만)을 쓰고,
    맥이거나 dxcam이 안 되면 mss(GDI, 한 번에 15ms 이상)로 자동 대체한다.
    dxcam은 화면이 안 바뀌면 None을 주므로 그때는 직전 프레임을 다시 쓴다."""

    def __init__(self):
        self.sct = mss.MSS()
        self.outputs = []       # [(device_idx, output_idx, left, top, right, bottom)]
        self.cams = {}
        self.last = {}          # 영역 -> 마지막 프레임
        self.dx_ok = False
        if IS_WIN:
            try:
                import dxcam
                factory = getattr(dxcam, "__factory")   # 클래스 안에서 from-import하면 이름이 꼬여서 getattr로
                self.dxcam = dxcam
                for di, outs in enumerate(factory.outputs):
                    for oi, o in enumerate(outs):
                        if o.desc.AttachedToDesktop:
                            d = o.desc.DesktopCoordinates
                            self.outputs.append((di, oi, d.left, d.top, d.right, d.bottom))
                self.dx_ok = bool(self.outputs)
            except Exception as ex:
                log(f"dxcam 사용 불가, mss로 대체: {ex!r}")

    def _output_for(self, x, y, w, h):
        for out in self.outputs:
            _, _, l, t, r, b = out
            if l <= x and t <= y and x + w <= r and y + h <= b:
                return out
        return None     # 모니터 두 개에 걸친 영역 등은 mss로

    def _camera(self, out):
        key = out[:2]
        if key not in self.cams:
            self.cams[key] = self.dxcam.create(device_idx=out[0], output_idx=out[1], output_color="BGR")
        return self.cams[key]

    def grab_bgr(self, x, y, w, h):
        x, y, w, h = int(x), int(y), max(1, int(w)), max(1, int(h))
        if self.dx_ok:
            out = self._output_for(x, y, w, h)
            if out:
                try:
                    cam = self._camera(out)
                    region = (x - out[2], y - out[3], x - out[2] + w, y - out[3] + h)
                    key = (x, y, w, h)
                    f = cam.grab(region=region)
                    if f is None and key not in self.last:
                        for _ in range(10):            # 첫 프레임은 잠깐 기다려 준다
                            time.sleep(0.01)
                            f = cam.grab(region=region)
                            if f is not None:
                                break
                    if f is not None:
                        f = np.ascontiguousarray(f)
                        self.last[key] = f
                        return f
                    if key in self.last:
                        return self.last[key]
                except Exception as ex:
                    log(f"dxcam 오류, mss로 대체: {ex!r}")
                    self.dx_ok = False
        shot = self.sct.grab({"left": x, "top": y, "width": w, "height": h})
        arr = np.frombuffer(shot.bgra, dtype=np.uint8).reshape(shot.height, shot.width, 4)
        return np.ascontiguousarray(arr[:, :, :3])

    def grab(self, x, y, w, h):
        """PIL RGB 이미지"""
        return Image.fromarray(np.ascontiguousarray(self.grab_bgr(x, y, w, h)[:, :, ::-1]))

    def backend(self):
        return "dxcam" if self.dx_ok else "mss"

    def close(self):
        for cam in self.cams.values():
            try:
                cam.release()
            except Exception:
                pass
        self.sct.close()


class MapleWindowBase:
    """메이플 창의 게임 화면 사각형 (x, y, w, h)을 돌려준다. 칸 위치는 (x, y)를 기준으로
    저장해서 창을 옮겨도 따라간다. 0.5초에 한 번만 실제로 찾는다."""

    def __init__(self):
        self.rect = None        # (x, y, w, h) 또는 None(창 없음)
        self._last_check = 0.0

    @property
    def origin(self):
        return (self.rect[0], self.rect[1]) if self.rect else None

    def refresh(self):
        now = time.monotonic()
        if now - self._last_check < 0.5:
            return self.origin
        self._last_check = now
        try:
            self.rect = self._locate()
        except Exception as ex:
            log(f"maple window locate error: {ex!r}")
            self.rect = None
        return self.origin

    def _locate(self):
        return None


class MapleWindowWin(MapleWindowBase):
    """제목 MapleStory / 클래스 MapleStoryClass 창의 클라이언트 영역."""

    TITLE = "MapleStory"
    CLASS = "MapleStoryClass"

    def __init__(self):
        super().__init__()
        self.user32 = ctypes.windll.user32
        self.hwnd = None

    def _locate(self):
        u = self.user32
        if not self.hwnd or not u.IsWindow(self.hwnd):
            self.hwnd = u.FindWindowW(self.CLASS, None) or u.FindWindowW(None, self.TITLE) or None
        if not self.hwnd or not u.IsWindowVisible(self.hwnd) or u.IsIconic(self.hwnd):
            return None
        pt = ctypes.wintypes.POINT(0, 0)
        rc = ctypes.wintypes.RECT()
        if not u.ClientToScreen(self.hwnd, ctypes.byref(pt)) or not u.GetClientRect(self.hwnd, ctypes.byref(rc)):
            return None
        return (pt.x, pt.y, rc.right - rc.left, rc.bottom - rc.top)


class MapleWindowMac(MapleWindowBase):
    """맥: 화면에 떠 있는 창 목록에서 소유 앱 이름에 maple이 들어간 가장 큰 창.
    창 테두리 포함 좌표지만, 칸도 같은 기준으로 저장하므로 따라가기에는 문제없다."""

    def _locate(self):
        from Quartz import (CGWindowListCopyWindowInfo, kCGNullWindowID,
                            kCGWindowListExcludeDesktopElements, kCGWindowListOptionOnScreenOnly)
        infos = CGWindowListCopyWindowInfo(
            kCGWindowListOptionOnScreenOnly | kCGWindowListExcludeDesktopElements, kCGNullWindowID) or []
        best = None
        for w in infos:
            owner = str(w.get("kCGWindowOwnerName", "")).lower().replace(" ", "")
            if "maple" not in owner or w.get("kCGWindowLayer", 0) != 0:
                continue
            b = w.get("kCGWindowBounds") or {}
            if b.get("Width", 0) < 300 or b.get("Height", 0) < 200:
                continue
            if best is None or b["Width"] * b["Height"] > best[0]:
                best = (b["Width"] * b["Height"], int(b["X"]), int(b["Y"]), int(b["Width"]), int(b["Height"]))
        return best[1:] if best else None


def make_maple_window():
    return MapleWindowMac() if IS_MAC else MapleWindowWin()


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


def box_dims(box):
    """스킬 칸은 size 하나, 이름표 칸은 w/h 따로."""
    return int(box.get("w", box.get("size", 32))), int(box.get("h", box.get("size", 32)))


# ---------------------------------------------------------------- 캐릭터 따라가기
class CharTracker:
    """저장한 이름표 그림을 메이플 창 안에서 찾아 캐릭터 위치를 추정한다.
    못 찾으면 잠깐 마지막 위치를 쓰고, 그래도 못 찾으면 창 가운데를 쓴다."""

    SEARCH_MARGIN = 0.2     # 평소엔 창 가운데 60%만 뒤진다 (카메라가 캐릭터를 따라오므로)
    DOWNSCALE = 2           # 반으로 줄여서 찾는다. 4배 빠르고 이름표 정도 크기면 충분히 맞는다
    DETECT_EVERY = 0.15     # 인식은 이 간격(초)으로만. 그 사이 프레임은 마지막 위치를 쓴다
    WIDE_EVERY = 0.5        # 놓친 상태가 이어지면 이 간격으로 창 전체를 뒤진다

    def __init__(self, cfg):
        self.cfg = cfg
        self.template = None
        self.tag_size = (0, 0)
        self.last_pos = None    # 이름표 (가운데 x, 위 y) 화면 좌표
        self.last_time = 0.0
        self.last_score = 0.0
        self.last_detect = 0.0
        self.last_wide = 0.0
        self.state = "표식 없음"
        self.load()

    def load(self):
        self.template = None
        if TAG_PATH.exists():
            # cv2.imread는 한글 경로를 못 읽어서 바이트로 읽어 디코드한다
            # 색까지 비교한다: 내 이름표의 파란 테두리가 몬스터 이름표(검은 바탕)와 구별되는 단서
            img = cv2.imdecode(np.fromfile(str(TAG_PATH), dtype=np.uint8), cv2.IMREAD_COLOR)
            if img is not None and img.shape[0] >= 6 and img.shape[1] >= 6:
                self.tag_size = (img.shape[1], img.shape[0])
                self.template = cv2.resize(img, (max(3, img.shape[1] // self.DOWNSCALE),
                                                 max(3, img.shape[0] // self.DOWNSCALE)),
                                           interpolation=cv2.INTER_AREA)
                self.state = "표식 저장됨"
                return
        self.state = "표식 없음"

    def save_template(self, screen, box, origin):
        x, y = box_screen_pos(box, origin)
        w, h = box_dims(box)
        screen.grab(x, y, w, h).save(TAG_PATH)
        self.load()

    def find(self, frame, margin=None):
        """frame(메이플 창 한 장) 안에서 이름표를 찾아 (가운데 x, 위 y, 점수)를 화면 좌표로 돌려준다."""
        if self.template is None or frame is None:
            return None
        img, fx, fy, factor = frame.img, frame.x, frame.y, frame.factor
        H, W = img.shape[:2]
        margin = self.SEARCH_MARGIN if margin is None else margin
        mx, my = int(W * margin), int(H * margin)
        sub = img[my:H - my, mx:W - mx]
        ds = self.DOWNSCALE * factor
        small = cv2.resize(sub, (max(1, sub.shape[1] // ds), max(1, sub.shape[0] // ds)), interpolation=cv2.INTER_AREA)
        if small.shape[0] < self.template.shape[0] or small.shape[1] < self.template.shape[1]:
            return None
        res = cv2.matchTemplate(small, self.template, cv2.TM_CCOEFF_NORMED)
        _, score, _, loc = cv2.minMaxLoc(res)
        if score < float(self.cfg["char"]["threshold"]):
            return None
        tx = fx + (mx + loc[0] * ds) // factor
        ty = fy + (my + loc[1] * ds) // factor
        return (tx + self.tag_size[0] // 2, ty, score)

    def region(self, frame, rect):
        """이번 프레임에 잘라낼 캐릭터 사각형 (left, top, w, h) 화면 좌표. 창이 없으면 None."""
        cw, ch = int(self.cfg["char"]["w"]), int(self.cfg["char"]["h"])
        now = time.monotonic()
        found = None
        if frame is not None and now - self.last_detect >= self.DETECT_EVERY:
            self.last_detect = now
            found = self.find(frame)
            lost_long = now - self.last_time >= float(self.cfg["char"]["hold"])
            if not found and lost_long and now - self.last_wide >= self.WIDE_EVERY:
                self.last_wide = now
                found = self.find(frame, margin=0.02)   # 오래 놓쳤으면 창 전체를 한 번 뒤진다
        if found:
            self.last_pos = (found[0], found[1])
            self.last_time = now
            self.last_score = found[2]
            self.state = f"찾음 ({found[2]:.2f})"
        elif self.last_pos and now - self.last_time < float(self.cfg["char"]["hold"]):
            self.state = "잠깐 놓침 → 마지막 자리"
        else:
            self.last_pos = None
            self.state = "못 찾음 → 창 가운데" if self.template is not None else "표식 없음 → 창 가운데"
        if self.last_pos:
            cx, top = self.last_pos
            bottom = top + self.tag_size[1] + 4   # 이름표까지 포함
            return (cx - cw // 2, bottom - ch, cw, ch)
        if rect:
            x, y, w, h = rect
            return (x + w // 2 - cw // 2, y + h // 2 - ch // 2, cw, ch)
        return None


# ---------------------------------------------------------------- 멈춤 감지
class IdleDetector:
    """캐릭터 칸 그림을 직전 프레임과 비교한다. 스킬을 쓰면 이펙트로 변화가 크고,
    가만히 서 있으면 숨쉬기 모션 정도라 변화가 작다. 변화가 작은 상태가 이어지면 '멈춤'."""

    def __init__(self, cfg):
        self.cfg = cfg
        self.prev = None
        self.last_active = time.monotonic()
        self.diff = 0.0

    def update(self, img):
        """img: 캐릭터 칸 PIL 이미지(배율 적용 전). 멈춘 시간(초)을 돌려준다."""
        # 비교용 축소: 가로세로 비율을 유지하되 긴 쪽을 60픽셀로
        r = 60 / max(img.width, img.height)
        size = (max(4, int(img.width * r)), max(4, int(img.height * r)))
        small = np.asarray(img.convert("L").resize(size, Image.BILINEAR), dtype=np.int16)
        now = time.monotonic()
        if self.prev is not None and self.prev.shape == small.shape:
            self.diff = float(np.abs(small - self.prev).mean())
            if self.diff > float(self.cfg["idle"]["sensitivity"]):
                self.last_active = now
        else:
            self.last_active = now
        self.prev = small
        return now - self.last_active

    def is_idle(self, idle_for):
        return bool(self.cfg["idle"]["enabled"]) and idle_for >= float(self.cfg["idle"]["seconds"])


# ---------------------------------------------------------------- 미러 합성
class Frame:
    """한 프레임에 화면을 한 번만 캡처한 것. 캡처는 한 번 부를 때마다 화면 한 프레임(약 15ms)을
    기다리므로, 칸마다 따로 찍지 않고 큰 영역 한 장에서 잘라 쓴다."""

    def __init__(self, screen, x, y, w, h):
        self.screen = screen
        self.x, self.y, self.w, self.h = int(x), int(y), max(1, int(w)), max(1, int(h))
        self.img = screen.grab_bgr(self.x, self.y, self.w, self.h)
        self.factor = max(1, round(self.img.shape[1] / self.w))   # 레티나면 2

    def contains(self, x, y, w, h):
        return self.x <= x and self.y <= y and x + w <= self.x + self.w and y + h <= self.y + self.h

    def crop(self, x, y, w, h):
        """화면 좌표 사각형을 PIL 이미지로. 프레임 밖이면 그 부분만 따로 캡처한다."""
        x, y, w, h = int(x), int(y), max(1, int(w)), max(1, int(h))
        if not self.contains(x, y, w, h):
            return self.screen.grab(x, y, w, h)
        f = self.factor
        sub = self.img[(y - self.y) * f:(y - self.y + h) * f, (x - self.x) * f:(x - self.x + w) * f]
        return Image.fromarray(np.ascontiguousarray(sub[:, :, ::-1]))


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


def union_rect(rects):
    x0 = min(r[0] for r in rects); y0 = min(r[1] for r in rects)
    x1 = max(r[0] + r[2] for r in rects); y1 = max(r[1] + r[3] for r in rects)
    return (x0, y0, x1 - x0, y1 - y0)


def scaled(img, s):
    return img.resize((max(1, int(img.width * s)), max(1, int(img.height * s))), Image.NEAREST)


def compose_skills(frame, screen, cfg, origin):
    """스킬 칸을 잘라 한 장으로 붙인다. 칸이 없으면 None. 배율 1.0 = 화면에 보이는 크기."""
    boxes = ordered_boxes(cfg["boxes"])
    if not boxes:
        return None
    cells = []
    for b in boxes:
        x, y = box_screen_pos(b, origin)
        cells.append(frame.crop(x, y, b["size"], b["size"]) if frame else screen.grab(x, y, b["size"], b["size"]))
    cw = max(c.width for c in cells)
    ch = max(c.height for c in cells)
    factor = max(1, round(cw / max(1, int(boxes[0]["size"]))))
    gap = int(cfg["gap"]) * factor
    per_row = int(cfg["per_row"]) or len(cells)
    n_rows = -(-len(cells) // per_row)
    out = Image.new("RGB", (per_row * cw + (per_row - 1) * gap, n_rows * ch + (n_rows - 1) * gap), "#111111")
    for i, cell in enumerate(cells):
        rr, cc = divmod(i, per_row)
        out.paste(cell, (cc * (cw + gap), rr * (ch + gap)))
    return scaled(out, float(cfg["scale"]) / factor)


def compose_all(screen, cfg, maple, tracker):
    """한 번 캡처한 프레임에서 (스킬 줄 그림, 캐릭터 그림)을 만든다. 없는 쪽은 None."""
    origin = maple.origin
    # 무엇을 한 장에 담을지: 메이플 창이 있으면 창 전체(캐릭터 인식에 필요), 없으면 스킬 칸들의 바깥 사각형
    if maple.rect:
        frame = Frame(screen, *maple.rect)
    elif cfg["boxes"]:
        rects = [(*box_screen_pos(b, origin), b["size"], b["size"]) for b in cfg["boxes"]]
        frame = Frame(screen, *union_rect(rects))
    else:
        frame = None
    skills = compose_skills(frame, screen, cfg, origin)
    watch = cfg["idle"]["watch"]
    char_img = raw = idle_img = None
    if cfg["char"]["enabled"] or (cfg["idle"]["enabled"] and not watch):
        reg = tracker.region(frame if maple.rect else None, maple.rect)
        if reg:
            raw = frame.crop(*reg) if frame else screen.grab(*reg)
            f = max(1, round(raw.width / max(1, reg[2])))
            char_img = scaled(raw, float(cfg["char"]["scale"]) / f)
    if cfg["idle"]["enabled"]:
        if watch:
            wx, wy = box_screen_pos(watch, origin)
            ww, wh = box_dims(watch)
            idle_img = frame.crop(wx, wy, ww, wh) if frame else screen.grab(wx, wy, ww, wh)
        else:
            idle_img = raw
    return skills, char_img, idle_img


# ---------------------------------------------------------------- 화면 위 칸
class OverlayBox:
    """게임 화면 위에 올려두는 네모 하나. 안쪽은 투명, 테두리만 잡힌다."""

    def __init__(self, root, box, color, get_origin, on_change, on_delete=None, on_wheel=None):
        self.box = box
        self.get_origin = get_origin
        self.on_change = on_change
        self.on_delete = on_delete
        self.on_wheel_cb = on_wheel
        self.win = tk.Toplevel(root)
        self.win.overrideredirect(True)
        self.win.attributes("-topmost", True)
        if IS_MAC:
            # 맥 tk는 색 키 투명이 없고 -transparent + systemTransparent 배경을 쓴다.
            # 클릭 통과는 안 되지만 편집 끝을 누르면 칸이 사라지므로 플레이에는 영향 없다.
            self.win.attributes("-transparent", True)
            inner_bg = "systemTransparent"
        else:
            self.win.attributes("-transparentcolor", TRANSPARENT)
            inner_bg = TRANSPARENT
        self.win.configure(bg=color, cursor="fleur")
        self.inner = tk.Frame(self.win, bg=inner_bg)
        self.inner.place(x=BORDER, y=BORDER)
        self.apply_geometry()
        self.win.bind("<ButtonPress-1>", self.drag_start)
        self.win.bind("<B1-Motion>", self.drag_move)
        self.win.bind("<ButtonRelease-1>", lambda e: self.on_change())
        self.win.bind("<MouseWheel>", self.on_wheel)
        if on_delete:
            self.win.bind("<ButtonPress-3>", lambda e: on_delete(self))
            self.win.bind("<ButtonPress-2>", lambda e: on_delete(self))  # 맥은 오른쪽 클릭이 Button-2
        self._drag = None

    def apply_geometry(self):
        w, h = box_dims(self.box)
        x, y = box_screen_pos(self.box, self.get_origin())
        self.win.geometry(f"{w + 2 * BORDER}x{h + 2 * BORDER}+{int(x) - BORDER}+{int(y) - BORDER}")
        self.inner.configure(width=w, height=h)

    def drag_start(self, e):
        self._drag = (e.x_root - self.win.winfo_x(), e.y_root - self.win.winfo_y())

    def drag_move(self, e):
        dx, dy = self._drag
        box_set_screen_pos(self.box, e.x_root - dx + BORDER, e.y_root - dy + BORDER, self.get_origin())
        self.apply_geometry()

    def on_wheel(self, e):
        if self.on_wheel_cb:
            self.on_wheel_cb(1 if e.delta > 0 else -1)

    def destroy(self):
        self.win.destroy()


# ---------------------------------------------------------------- 미러 창
class ImageWindow:
    """그림 한 장을 보여주는 항상-위 창. 스킬 창과 캐릭터 창이 각각 이것 하나씩. 드래그로 옮긴다."""

    def __init__(self, root, cfg, pos_key, empty_text, on_move, is_round=None):
        self.cfg = cfg
        self.pos_key = pos_key
        self.empty_text = empty_text
        self.on_move = on_move
        self.is_round = is_round or (lambda: False)   # True면 타원으로 자르고 바깥을 뚫는다
        self._round_now = False
        self._photo = None
        self._drag = None
        self.win = tk.Toplevel(root)
        self.win.overrideredirect(True)
        self.win.configure(bg="#111")
        x, y = clamp_to_screen(cfg[pos_key]["x"], cfg[pos_key]["y"])
        self.win.geometry(f"+{x}+{y}")
        self.label = tk.Label(self.win, bg="#111", fg="#888", bd=0, cursor="fleur",
                              text=empty_text, font=UI_FONT, padx=10, pady=10)
        self.label.pack(padx=BORDER, pady=BORDER)   # 창 배경색이 테두리로 보인다
        self.border = "#111"
        if IS_WIN:
            self.win.attributes("-transparentcolor", TRANSPARENT)
        self.label.bind("<ButtonPress-1>", self.drag_start)
        self.label.bind("<B1-Motion>", self.drag_move)
        self.label.bind("<ButtonRelease-1>", lambda e: self.on_move())
        self.apply_settings()

    def apply_settings(self):
        self.win.attributes("-topmost", bool(self.cfg["topmost"]))
        self.win.attributes("-alpha", float(self.cfg["alpha"]))

    def show(self, img):
        rnd = bool(self.is_round()) and IS_WIN and img is not None
        if rnd != self._round_now:
            # 동그란 모양일 때는 창·라벨 배경을 뚫리는 색으로, 네모일 때는 어두운 색으로
            self._round_now = rnd
            self.label.configure(bg=TRANSPARENT if rnd else "#111")
            self.win.configure(bg=TRANSPARENT if rnd else self.border)
        if img is None:
            self._photo = None
            self.label.configure(image="", text=self.empty_text, padx=10, pady=10)
            return
        if rnd:
            img = self._ellipse(img)
        self._photo = ImageTk.PhotoImage(img)
        self.label.configure(image=self._photo, text="", padx=0, pady=0)

    def _ellipse(self, img):
        """타원 안쪽만 남기고 바깥은 뚫리는 색. 알림 중이면 타원 테두리를 그 색으로 그린다."""
        w, h = img.size
        pad = BORDER
        out = Image.new("RGB", (w + 2 * pad, h + 2 * pad), TRANSPARENT)
        mask = Image.new("L", (w, h), 0)
        ImageDraw.Draw(mask).ellipse((0, 0, w - 1, h - 1), fill=255)
        out.paste(img, (pad, pad), mask)
        if self.border != "#111":
            ImageDraw.Draw(out).ellipse((1, 1, w + 2 * pad - 2, h + 2 * pad - 2), outline=self.border, width=pad)
        return out

    def drag_start(self, e):
        self._drag = (e.x_root - self.win.winfo_x(), e.y_root - self.win.winfo_y())

    def drag_move(self, e):
        dx, dy = self._drag
        self.win.geometry(f"+{e.x_root - dx}+{e.y_root - dy}")

    def position(self):
        return {"x": self.win.winfo_x(), "y": self.win.winfo_y()}

    def set_border(self, color):
        if color != self.border:
            self.border = color
            if not self._round_now:
                self.win.configure(bg=color)

    def move_to(self, x, y):
        self.win.geometry(f"+{x}+{y}")

    def set_visible(self, on):
        if on:
            self.win.deiconify()
        else:
            self.win.withdraw()

    def close(self):
        self.win.destroy()


class MirrorLoop:
    """한 프레임에 한 번 캡처해서 스킬 창과 캐릭터 창에 나눠 준다."""

    def __init__(self, root, cfg, maple, tracker, screen, on_move):
        self.root = root
        self.cfg = cfg
        self.maple = maple
        self.tracker = tracker
        self.screen = screen
        self._job = None
        self.idle = IdleDetector(cfg)
        self.idle_for = 0.0
        self.skills = ImageWindow(root, cfg, "mirror_pos", "  칸을 추가하세요  ", on_move)
        self.char = ImageWindow(root, cfg, "char_pos", "  캐릭터  ", on_move,
                                is_round=lambda: cfg["char"]["round"])
        self.char.set_visible(bool(cfg["char"]["enabled"]))
        self.tick()

    def apply_settings(self):
        self.skills.apply_settings()
        self.char.apply_settings()
        self.char.set_visible(bool(self.cfg["char"]["enabled"]))

    def tick(self):
        try:
            self.maple.refresh()
            skills_img, char_img, idle_img = compose_all(self.screen, self.cfg, self.maple, self.tracker)
            self.skills.show(skills_img)
            if self.cfg["char"]["enabled"]:
                self.char.show(char_img)
            if idle_img is not None:
                self.idle_for = self.idle.update(idle_img)
            # 멈췄으면 두 창 테두리를 빨갛게 깜빡인다 (0.3초 간격)
            alert = self.idle.is_idle(self.idle_for)
            color = ("#ff2020" if int(time.monotonic() / 0.3) % 2 == 0 else "#111") if alert else "#111"
            self.skills.set_border(color)
            self.char.set_border(color)
        except Exception as ex:
            log(f"capture error: {ex!r}")
        self._job = self.root.after(int(1000 / max(1, int(self.cfg["fps"]))), self.tick)

    def positions(self):
        return self.skills.position(), self.char.position()

    def close(self):
        if self._job:
            self.root.after_cancel(self._job)
        self.skills.close()
        self.char.close()


# ---------------------------------------------------------------- 처음 설정 안내
class Wizard:
    """처음 설정을 한 단계씩 안내한다. 각 단계에서 그 단계의 네모만 화면에 보여준다."""

    def __init__(self, panel):
        self.p = panel
        self.step = 0
        self.win = tk.Toplevel(panel.root)
        self.win.title("처음 설정")
        self.win.resizable(False, False)
        self.win.attributes("-topmost", True)
        self.win.protocol("WM_DELETE_WINDOW", self.finish)
        px, py = panel.win.winfo_x(), panel.win.winfo_y()
        self.win.geometry(f"+{px + 30}+{py + 30}")

        frm = ttk.Frame(self.win, padding=14)
        frm.pack(fill="both", expand=True)
        self.title_lbl = ttk.Label(frm, text="", font=(UI_FONT[0], 13, "bold"))
        self.title_lbl.pack(anchor="w")
        self.text_lbl = ttk.Label(frm, text="", justify="left", wraplength=400)
        self.text_lbl.pack(anchor="w", pady=(8, 10))
        self.body = ttk.Frame(frm)
        self.body.pack(fill="x")
        self.status_lbl = ttk.Label(frm, text="", foreground="#666")
        self.status_lbl.pack(anchor="w", pady=(8, 0))
        nav = ttk.Frame(frm)
        nav.pack(fill="x", pady=(14, 0))
        self.prev_btn = ttk.Button(nav, text="이전", command=self.prev)
        self.prev_btn.pack(side="left")
        self.next_btn = ttk.Button(nav, text="다음", command=self.next)
        self.next_btn.pack(side="right")
        self.skip_btn = ttk.Button(nav, text="건너뛰기", command=self.skip)
        self.skip_btn.pack(side="right", padx=(0, 6))
        self.show_step(0)
        self.tick()

    # ---- 단계 ----
    def show_step(self, i):
        self.step = i
        for w in self.body.winfo_children():
            w.destroy()
        p = self.p
        self.prev_btn.configure(state="normal" if i > 0 else "disabled")
        self.skip_btn.pack_forget()
        self.next_btn.configure(text="다음")
        if i == 0:
            p.set_edit(True, kinds={"skills"})
            self.title_lbl.configure(text="1 / 3  스킬 칸 놓기")
            self.text_lbl.configure(text=(
                "초록 네모를 보고 싶은 스킬 아이콘 위에 하나씩 올려주세요.\n"
                "• [칸 추가]를 누르면 마지막 칸 오른쪽에 하나 더 생겨요\n"
                "• 네모의 테두리를 잡고 끌어서 옮기기\n"
                "• 잘못 만든 건 테두리에서 오른쪽 클릭 → 삭제\n"
                "• 아이콘보다 크거나 작으면 아래 '칸 크기' 숫자로 맞추기 (모든 칸이 같이 바뀜)"))
            row = ttk.Frame(self.body)
            row.pack(fill="x")
            ttk.Button(row, text="칸 추가", command=p.add_box).pack(side="left", padx=(0, 6))
            ttk.Button(row, text="모두 삭제", command=p.clear_boxes).pack(side="left", padx=(0, 14))
            ttk.Label(row, text="칸 크기").pack(side="left")
            ttk.Spinbox(row, from_=12, to=200, width=5, textvariable=p.size_var, command=p.on_size_change).pack(side="left", padx=(4, 0))
            if not p.cfg["boxes"]:
                p.add_box()
        elif i == 1:
            p.place_tag_box(edit_kinds={"tag"})
            self.title_lbl.configure(text="2 / 3  내 캐릭터 이름표")
            self.text_lbl.configure(text=(
                "파란 네모를 내 캐릭터 발밑 이름표에 딱 맞춰 주세요.\n"
                "• 크기가 안 맞으면 아래 숫자로 조절\n"
                "• 캐릭터가 가만히 서 있을 때 [이름표 저장]을 누르세요 (움직이면 다른 게 찍혀요)\n"
                "• 저장되면 캐릭터가 따로 작은 창에 나와요. 필요 없으면 [건너뛰기]"))
            row = ttk.Frame(self.body)
            row.pack(fill="x")
            ttk.Label(row, text="이름표 크기").pack(side="left")
            ttk.Spinbox(row, from_=20, to=400, width=5, textvariable=p.tag_w_var, command=p.on_tag_size).pack(side="left", padx=(4, 2))
            ttk.Label(row, text="×").pack(side="left")
            ttk.Spinbox(row, from_=8, to=200, width=5, textvariable=p.tag_h_var, command=p.on_tag_size).pack(side="left", padx=(2, 14))
            ttk.Button(row, text="이름표 저장", command=p.save_tag).pack(side="left")
            self.skip_btn.pack(side="right", padx=(0, 6))
        elif i == 2:
            p.place_watch_box(edit_kinds={"watch"})
            self.title_lbl.configure(text="3 / 3  멈춤 감시")
            self.text_lbl.configure(text=(
                "주황 네모를 화면 맨 아래 경험치 숫자 위에 올려주세요.\n"
                "그 숫자가 몇 초 동안 안 바뀌면(사냥이 멈추면) 미러 창 테두리가 빨갛게 깜빡여요.\n"
                "• 사냥 중에만 바뀌는 숫자면 뭐든 됩니다 (경험치, 콤보 수)\n"
                "• 필요 없으면 [건너뛰기]"))
            row = ttk.Frame(self.body)
            row.pack(fill="x")
            ttk.Label(row, text="감시 칸 크기").pack(side="left")
            ttk.Spinbox(row, from_=20, to=600, width=5, textvariable=p.watch_w_var, command=p.on_watch_size).pack(side="left", padx=(4, 2))
            ttk.Label(row, text="×").pack(side="left")
            ttk.Spinbox(row, from_=8, to=200, width=5, textvariable=p.watch_h_var, command=p.on_watch_size).pack(side="left", padx=(2, 0))
            self.skip_btn.pack(side="right", padx=(0, 6))
        else:
            p.set_edit(False)
            self.title_lbl.configure(text="설정 끝!")
            self.text_lbl.configure(text=(
                "• 스킬 창과 캐릭터 창은 마우스로 끌어서 어디든 옮길 수 있어요\n"
                "• 다른 모니터로 보내려면 설정창의 [고급 설정] → [미러를 다른 모니터로]\n"
                "• 네모 위치만 고치려면 설정창의 [칸 편집], 전부 다시 하려면 [처음부터 설정]\n"
                "• 메이플 창을 옮겨도 칸이 따라갑니다 (창 크기를 바꾸면 다시 놓아야 해요)"))
            self.next_btn.configure(text="완료")

    def next(self):
        if self.step >= 3:
            self.finish()
        else:
            self.show_step(self.step + 1)

    def prev(self):
        if self.step > 0:
            self.show_step(self.step - 1)

    def skip(self):
        p = self.p
        if self.step == 1 and p.tracker.template is None:
            p.cfg["char"]["tag"] = None          # 저장 안 한 이름표 칸은 버린다
            p.cfg["char"]["enabled"] = False
            p.char_on_var.set(False)
            p.save_boxes()
        elif self.step == 2:
            p.clear_watch_box()                  # 자리를 안 맞춘 감시 칸은 버린다
        self.show_step(self.step + 1)

    def tick(self):
        if not self.win.winfo_exists():
            return
        p = self.p
        if self.step == 0:
            n = len(p.cfg["boxes"])
            self.status_lbl.configure(text=f"칸 {n}개" if n else "칸이 없어요. [칸 추가]를 눌러 주세요.")
        elif self.step == 1:
            self.status_lbl.configure(text=f"이름표: {p.tracker.state}")
        elif self.step == 2 and p.mirror:
            self.status_lbl.configure(text=f"감시 칸 변화 {p.mirror.idle.diff:.0f}  (사냥 중엔 숫자가 계속 바뀌어야 정상)")
        else:
            self.status_lbl.configure(text="")
        self.win.after(500, self.tick)

    def finish(self):
        self.p.set_edit(False)
        self.p.wizard = None
        self.win.destroy()


# ---------------------------------------------------------------- 조절 패널
class ControlPanel:
    def __init__(self, root, cfg):
        self.root = root
        self.cfg = cfg
        self.mirror = None
        self.wizard = None
        self.skill_boxes = []   # 편집 중일 때만 화면에 떠 있는 OverlayBox들
        self.tag_box = None
        self.watch_box = None
        self._editing = False
        self.maple = make_maple_window()
        self.maple.refresh()
        self.migrate_boxes()
        self.tracker = CharTracker(cfg)
        self.screen = Screen()
        ch, idle = cfg["char"], cfg["idle"]

        # tk 변수들 (기본 화면과 고급 설정, 마법사가 같이 쓴다)
        self.size_var = tk.IntVar(value=cfg["box_size"])
        self.per_row_var = tk.IntVar(value=cfg["per_row"])
        self.scale_var = tk.DoubleVar(value=cfg["scale"])
        self.alpha_var = tk.DoubleVar(value=cfg["alpha"])
        self.fps_var = tk.IntVar(value=cfg["fps"])
        self.topmost_var = tk.BooleanVar(value=cfg["topmost"])
        self.char_on_var = tk.BooleanVar(value=ch["enabled"])
        self.char_round_var = tk.BooleanVar(value=ch["round"])
        self.char_scale_var = tk.DoubleVar(value=ch["scale"])
        self.tag_w_var = tk.IntVar(value=ch["tag_w"])
        self.tag_h_var = tk.IntVar(value=ch["tag_h"])
        self.char_w_var = tk.IntVar(value=ch["w"])
        self.char_h_var = tk.IntVar(value=ch["h"])
        self.idle_on_var = tk.BooleanVar(value=idle["enabled"])
        self.idle_sec_var = tk.DoubleVar(value=idle["seconds"])
        self.idle_sens_var = tk.IntVar(value=idle["sensitivity"])
        self.watch_w_var = tk.IntVar(value=idle["watch_w"])
        self.watch_h_var = tk.IntVar(value=idle["watch_h"])

        self.win = tk.Toplevel(root)
        self.win.title("메이플 스킬바 미러")
        self.win.resizable(False, False)
        x, y = clamp_to_screen(cfg["panel_pos"]["x"], cfg["panel_pos"]["y"])
        self.win.geometry(f"+{x}+{y}")
        self.win.protocol("WM_DELETE_WINDOW", self.quit)
        self.win.attributes("-topmost", bool(cfg["topmost"]))

        frm = ttk.Frame(self.win, padding=12)
        frm.pack(fill="both", expand=True)

        # ---- 기본 화면: 버튼 4개 + 상태 + 슬라이더 2개 + 체크 3개 ----
        top = ttk.Frame(frm)
        top.pack(fill="x")
        ttk.Button(top, text="처음부터 설정", command=self.open_wizard).pack(side="left", padx=(0, 6))
        self.edit_btn = ttk.Button(top, text="칸 편집", command=self.toggle_edit)
        self.edit_btn.pack(side="left", padx=(0, 6))
        self.toggle_btn = ttk.Button(top, text="미러 끄기", command=self.toggle_mirror)
        self.toggle_btn.pack(side="left")
        ttk.Button(top, text="종료", command=self.quit).pack(side="right")

        self.status = ttk.Label(frm, text="", foreground="#666")
        self.status.pack(anchor="w", pady=(10, 0))
        self.char_status = ttk.Label(frm, text="", foreground="#666")
        self.char_status.pack(anchor="w")
        self.hint = ttk.Label(frm, text="", foreground="#888")
        self.hint.pack(anchor="w", pady=(0, 6))

        self._slider(frm, "스킬 배율", self.scale_var, 0.5, 4.0, 0.1, lambda v: f"{v:.1f}배", padx=0)
        self._slider(frm, "캐릭터 배율", self.char_scale_var, 0.5, 3.0, 0.1, lambda v: f"{v:.1f}배", padx=0)

        checks = ttk.Frame(frm)
        checks.pack(fill="x", pady=(4, 0))
        ttk.Checkbutton(checks, text="캐릭터 표시", variable=self.char_on_var, command=self.on_change).pack(side="left", padx=(0, 10))
        ttk.Checkbutton(checks, text="동그랗게", variable=self.char_round_var, command=self.on_change).pack(side="left", padx=(0, 10))
        ttk.Checkbutton(checks, text="멈추면 빨간 테두리", variable=self.idle_on_var, command=self.on_change).pack(side="left")

        # ---- 고급 설정 (접힘) ----
        self.adv_btn = ttk.Button(frm, text="고급 설정 ▸", command=self.toggle_advanced)
        self.adv_btn.pack(anchor="w", pady=(10, 0))
        self.adv = ttk.Frame(frm)
        self._advanced_open = False
        self._build_advanced(self.adv)

        self.start_mirror()
        self.update_status()
        self.tick_follow()
        if cfg["boxes"]:
            self.set_edit(False)
        else:
            self.open_wizard()   # 처음이면 안내부터

    def _build_advanced(self, adv):
        g = ttk.LabelFrame(adv, text="스킬 칸", padding=8)
        g.pack(fill="x", pady=(6, 0))
        r = ttk.Frame(g); r.pack(fill="x")
        ttk.Button(r, text="칸 추가", command=self.add_box).pack(side="left", padx=(0, 6))
        ttk.Button(r, text="모두 삭제", command=self.clear_boxes).pack(side="left", padx=(0, 14))
        ttk.Label(r, text="칸 크기").pack(side="left")
        ttk.Spinbox(r, from_=12, to=200, width=5, textvariable=self.size_var, command=self.on_size_change).pack(side="left", padx=(4, 14))
        ttk.Label(r, text="한 줄에").pack(side="left")
        ttk.Spinbox(r, from_=0, to=30, width=4, textvariable=self.per_row_var, command=self.on_change).pack(side="left", padx=4)
        ttk.Label(r, text="칸 (0 = 한 줄)").pack(side="left")

        g = ttk.LabelFrame(adv, text="캐릭터 (이름표 인식)", padding=8)
        g.pack(fill="x", pady=(6, 0))
        r = ttk.Frame(g); r.pack(fill="x")
        ttk.Button(r, text="이름표 칸 놓기", command=self.place_tag_box).pack(side="left", padx=(0, 6))
        ttk.Button(r, text="이름표 저장", command=self.save_tag).pack(side="left", padx=(0, 14))
        ttk.Label(r, text="이름표 크기").pack(side="left")
        ttk.Spinbox(r, from_=20, to=400, width=5, textvariable=self.tag_w_var, command=self.on_tag_size).pack(side="left", padx=(4, 2))
        ttk.Label(r, text="×").pack(side="left")
        ttk.Spinbox(r, from_=8, to=200, width=5, textvariable=self.tag_h_var, command=self.on_tag_size).pack(side="left", padx=(2, 0))
        r = ttk.Frame(g); r.pack(fill="x", pady=(6, 0))
        ttk.Label(r, text="캐릭터 칸 크기").pack(side="left")
        ttk.Spinbox(r, from_=40, to=800, increment=10, width=5, textvariable=self.char_w_var, command=self.on_change).pack(side="left", padx=(4, 2))
        ttk.Label(r, text="×").pack(side="left")
        ttk.Spinbox(r, from_=40, to=800, increment=10, width=5, textvariable=self.char_h_var, command=self.on_change).pack(side="left", padx=(2, 0))

        g = ttk.LabelFrame(adv, text="멈춤 감시", padding=8)
        g.pack(fill="x", pady=(6, 0))
        r = ttk.Frame(g); r.pack(fill="x")
        ttk.Spinbox(r, from_=1, to=30, increment=0.5, width=5, textvariable=self.idle_sec_var, command=self.on_change).pack(side="left", padx=(0, 2))
        ttk.Label(r, text="초 동안 변화 없으면 알림").pack(side="left", padx=(0, 14))
        ttk.Label(r, text="민감도").pack(side="left")
        ttk.Spinbox(r, from_=1, to=40, width=4, textvariable=self.idle_sens_var, command=self.on_change).pack(side="left", padx=(4, 0))
        r = ttk.Frame(g); r.pack(fill="x", pady=(6, 0))
        ttk.Button(r, text="감시 칸 놓기", command=self.place_watch_box).pack(side="left", padx=(0, 6))
        ttk.Button(r, text="감시 칸 지우기", command=self.clear_watch_box).pack(side="left", padx=(0, 14))
        ttk.Label(r, text="감시 칸 크기").pack(side="left")
        ttk.Spinbox(r, from_=20, to=600, width=5, textvariable=self.watch_w_var, command=self.on_watch_size).pack(side="left", padx=(4, 2))
        ttk.Label(r, text="×").pack(side="left")
        ttk.Spinbox(r, from_=8, to=200, width=5, textvariable=self.watch_h_var, command=self.on_watch_size).pack(side="left", padx=(2, 0))
        ttk.Label(g, foreground="#888", justify="left", wraplength=420,
                  text="감시 칸이 없으면 캐릭터 칸의 변화로 판단해요 (지나가는 몹에 속을 수 있음).").pack(anchor="w", pady=(4, 0))

        g = ttk.LabelFrame(adv, text="창", padding=8)
        g.pack(fill="x", pady=(6, 0))
        self._slider(g, "투명도", self.alpha_var, 0.2, 1.0, 0.05, lambda v: f"{int(v * 100)}%", padx=0)
        self._slider(g, "갱신 속도", self.fps_var, 1, 60, 1, lambda v: f"초당 {int(v)}회", padx=0)
        r = ttk.Frame(g); r.pack(fill="x")
        ttk.Checkbutton(r, text="항상 위에 두기", variable=self.topmost_var, command=self.on_change).pack(side="left", padx=(0, 14))
        ttk.Button(r, text="미러를 다른 모니터로", command=self.move_mirror_monitor).pack(side="left")

    def toggle_advanced(self):
        self._advanced_open = not self._advanced_open
        if self._advanced_open:
            self.adv.pack(fill="x")
            self.adv_btn.configure(text="고급 설정 ▾")
        else:
            self.adv.pack_forget()
            self.adv_btn.configure(text="고급 설정 ▸")

    def open_wizard(self):
        if self.wizard and self.wizard.win.winfo_exists():
            self.wizard.win.lift()
            return
        self.wizard = Wizard(self)

    def _slider(self, parent, title, var, lo, hi, step, fmt, padx=10):
        row = ttk.Frame(parent)
        row.pack(fill="x", padx=padx, pady=4)
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

    # ---- 편집 모드 ----
    def set_edit(self, on, kinds=None):
        """kinds: 보여줄 네모 종류 {"skills","tag","watch"}. None이면 전부."""
        kinds = kinds or {"skills", "tag", "watch"}
        self._editing = on
        for sb in self.skill_boxes:
            sb.destroy()
        self.skill_boxes = []
        if self.tag_box:
            self.tag_box.destroy()
            self.tag_box = None
        if self.watch_box:
            self.watch_box.destroy()
            self.watch_box = None
        if on:
            if "skills" in kinds:
                for b in self.cfg["boxes"]:
                    self.skill_boxes.append(self._make_skill_box(b))
            if "tag" in kinds and self.cfg["char"]["tag"]:
                self.tag_box = self._make_tag_box()
            if "watch" in kinds and self.cfg["idle"]["watch"]:
                self.watch_box = self._make_watch_box()
        self.edit_btn.configure(text="편집 끝" if on else "칸 편집")
        self.hint.configure(text=(
            "편집 중: 초록 = 스킬, 파란 = 이름표, 주황 = 감시 · 테두리를 끌어 옮기기 · 스킬 칸은 오른쪽 클릭으로 삭제"
            if on else "네모 위치를 바꾸려면 [칸 편집]"))

    def toggle_edit(self):
        self.set_edit(not self._editing)

    def _make_skill_box(self, b):
        return OverlayBox(self.root, b, COLOR_EDIT, self.maple.refresh, self.save_boxes,
                          on_delete=self.delete_box,
                          on_wheel=lambda d: self.set_all_size(int(self.cfg["box_size"]) + d))

    def _make_tag_box(self):
        return OverlayBox(self.root, self.cfg["char"]["tag"], COLOR_TAG, self.maple.refresh, self.save_boxes)

    def _make_watch_box(self):
        return OverlayBox(self.root, self.cfg["idle"]["watch"], COLOR_WATCH, self.maple.refresh, self.save_boxes)

    # ---- 감시 칸 ----
    def place_watch_box(self, edit_kinds=None):
        idle = self.cfg["idle"]
        if not idle["watch"]:
            origin = self.maple.refresh()
            if self.maple.rect:
                x, y, w, h = self.maple.rect
                left, top = x + w // 2 - idle["watch_w"] // 2, y + h - 60   # 화면 아래 경험치 줄 근처
            else:
                m = primary_monitor()
                left, top = m["left"] + m["width"] // 2, m["top"] + m["height"] // 2
            box = {"left": left, "top": top, "w": idle["watch_w"], "h": idle["watch_h"]}
            box_set_screen_pos(box, left, top, origin)
            idle["watch"] = box
        if edit_kinds or not self._editing:
            self.set_edit(True, kinds=edit_kinds)
        elif not self.watch_box:
            self.watch_box = self._make_watch_box()
        self.save_boxes()

    def clear_watch_box(self):
        self.cfg["idle"]["watch"] = None
        if self.watch_box:
            self.watch_box.destroy()
            self.watch_box = None
        self.save_boxes()

    def on_watch_size(self):
        idle = self.cfg["idle"]
        try:
            idle["watch_w"] = max(20, int(self.watch_w_var.get()))
            idle["watch_h"] = max(8, int(self.watch_h_var.get()))
        except tk.TclError:
            return
        if idle["watch"]:
            idle["watch"]["w"], idle["watch"]["h"] = idle["watch_w"], idle["watch_h"]
        if self.watch_box:
            self.watch_box.apply_geometry()
        self.save_boxes()

    # ---- 스킬 칸 ----
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
            self.set_edit(True, kinds={"skills"} if self.wizard else None)
        else:
            self.skill_boxes.append(self._make_skill_box(box))
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
        self.set_edit(True, kinds={"skills"} if self.wizard else None)
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

    # ---- 이름표 ----
    def place_tag_box(self, edit_kinds=None):
        ch = self.cfg["char"]
        if not ch["tag"]:
            origin = self.maple.refresh()
            if self.maple.rect:
                x, y, w, h = self.maple.rect
                left, top = x + w // 2 - ch["tag_w"] // 2, y + h // 2 + 40
            else:
                m = primary_monitor()
                left, top = m["left"] + m["width"] // 2, m["top"] + m["height"] // 2
            tag = {"left": left, "top": top, "w": ch["tag_w"], "h": ch["tag_h"]}
            box_set_screen_pos(tag, left, top, origin)
            ch["tag"] = tag
        if edit_kinds or not self._editing:
            self.set_edit(True, kinds=edit_kinds)
        elif not self.tag_box:
            self.tag_box = self._make_tag_box()
        self.save_boxes()

    def on_tag_size(self):
        ch = self.cfg["char"]
        try:
            ch["tag_w"] = max(20, int(self.tag_w_var.get()))
            ch["tag_h"] = max(8, int(self.tag_h_var.get()))
        except tk.TclError:
            return
        if ch["tag"]:
            ch["tag"]["w"], ch["tag"]["h"] = ch["tag_w"], ch["tag_h"]
        if self.tag_box:
            self.tag_box.apply_geometry()
        self.save_boxes()

    def save_tag(self):
        ch = self.cfg["char"]
        if not ch["tag"]:
            messagebox.showinfo("이름표", "먼저 [이름표 칸 놓기]로 파란 칸을 이름표에 맞춰 주세요.")
            return
        # 테두리 창이 캡처에 들어가지 않게 잠깐 숨긴다
        if self.tag_box:
            self.tag_box.win.withdraw()
            self.root.update()
            time.sleep(0.05)
        try:
            self.tracker.save_template(self.screen, ch["tag"], self.maple.refresh())
            ch["enabled"] = True
            self.char_on_var.set(True)
        except Exception as ex:
            report_error("이름표 저장 실패", ex)
        finally:
            if self.tag_box:
                self.tag_box.win.deiconify()
        self.save_boxes()

    # ---- 저장·상태 ----
    def save_boxes(self):
        self.save()
        self.update_status()

    def migrate_boxes(self):
        """예전 저장(절대 좌표만)에 창 기준 좌표를 붙인다. 창을 못 찾으면 다음 기회에."""
        origin = self.maple.origin
        if not origin:
            return
        extra = [b for b in (self.cfg["char"]["tag"], self.cfg["idle"]["watch"]) if b]
        for b in self.cfg["boxes"] + extra:
            if "dx" not in b:
                box_set_screen_pos(b, b["left"], b["top"], origin)

    def update_status(self):
        n = len(self.cfg["boxes"])
        o = self.maple.origin
        win = f"메이플 창 찾음 ({o[0]}, {o[1]}) → 칸이 창을 따라감" if o else "메이플 창 없음 → 마지막 자리 그대로"
        win += f"  ·  캡처 {self.screen.backend()}"
        head = f"스킬 칸 {n}개" if n else "스킬 칸 없음"
        self.status.configure(text=f"{head}  ·  {win}")
        idle_txt = ""
        if self.mirror:
            d = self.mirror.idle.diff
            src = "감시 칸" if self.cfg["idle"]["watch"] else "캐릭터 칸"
            idle_txt = f"  ·  {src} 변화 {d:.0f}  ·  " + (f"멈춤 {self.mirror.idle_for:.0f}초" if self.mirror.idle_for >= 1 else "활동 중")
        self.char_status.configure(text=f"이름표: {self.tracker.state}{idle_txt}")

    def tick_follow(self):
        """0.5초마다 메이플 창 위치를 확인해서 편집 중인 테두리와 상태 글을 맞춘다."""
        before = self.maple.origin
        after = self.maple.refresh()
        if after != before:
            if after:
                self.migrate_boxes()
            for sb in self.skill_boxes + [b for b in (self.tag_box, self.watch_box) if b]:
                sb.apply_geometry()
        self.update_status()
        self.root.after(500, self.tick_follow)

    def on_change(self):
        ch = self.cfg["char"]
        self.cfg["scale"] = round(self.scale_var.get(), 2)
        self.cfg["alpha"] = round(self.alpha_var.get(), 2)
        self.cfg["fps"] = int(self.fps_var.get())
        self.cfg["topmost"] = bool(self.topmost_var.get())
        ch["enabled"] = bool(self.char_on_var.get())
        ch["round"] = bool(self.char_round_var.get())
        ch["scale"] = round(self.char_scale_var.get(), 2)
        idle = self.cfg["idle"]
        idle["enabled"] = bool(self.idle_on_var.get())
        try:
            idle["seconds"] = max(0.5, float(self.idle_sec_var.get()))
            idle["sensitivity"] = max(1, int(self.idle_sens_var.get()))
        except (tk.TclError, ValueError):
            pass
        for key, var, lo in (("per_row", self.per_row_var, 0),):
            try:
                self.cfg[key] = max(lo, int(var.get()))
            except tk.TclError:
                pass
        for key, var in (("w", self.char_w_var), ("h", self.char_h_var)):
            try:
                ch[key] = max(40, int(var.get()))
            except tk.TclError:
                pass
        self.win.attributes("-topmost", self.cfg["topmost"])
        if self.mirror:
            self.mirror.apply_settings()
        self.save()

    def save(self):
        if self.mirror:
            self.cfg["mirror_pos"], self.cfg["char_pos"] = self.mirror.positions()
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
            self.mirror = MirrorLoop(self.root, self.cfg, self.maple, self.tracker, self.screen, on_move=self.save)
        self.toggle_btn.configure(text="미러 끄기")

    def move_mirror_monitor(self):
        """미러 창을 다음 모니터의 왼쪽 위로 옮긴다. 눌러서 원하는 모니터가 나오면 거기서 끌어 조정."""
        if not self.mirror:
            self.start_mirror()
        with mss.MSS() as sct:
            mons = sct.monitors[1:]
        pos = self.mirror.skills.position()
        cur = 0
        for i, m in enumerate(mons):
            if m["left"] <= pos["x"] < m["left"] + m["width"] and m["top"] <= pos["y"] < m["top"] + m["height"]:
                cur = i
                break
        nxt = mons[(cur + 1) % len(mons)]
        self.mirror.skills.move_to(nxt["left"] + 40, nxt["top"] + 40)
        self.mirror.char.move_to(nxt["left"] + 40, nxt["top"] + 160)   # 캐릭터 창은 그 아래에
        self.save()

    def stop_mirror(self):
        if self.mirror:
            self.save()
            self.mirror.close()
            self.mirror = None
        self.toggle_btn.configure(text="미러 켜기")

    def quit(self):
        self.save()
        if self.wizard and self.wizard.win.winfo_exists():
            self.wizard.win.destroy()
        self.screen.close()
        self.root.destroy()


def main():
    root = tk.Tk()
    root.withdraw()
    # 버튼 누를 때 나는 오류도 조용히 사라지지 않고 log.txt에 남게
    root.report_callback_exception = lambda et, ev, tb: log("callback\n" + "".join(traceback.format_exception(et, ev, tb)))
    try:
        ControlPanel(root, load_config())
    except Exception as ex:
        report_error("시작 중 오류가 났어요", ex)
        root.destroy()
        return
    root.mainloop()


if __name__ == "__main__":
    main()
