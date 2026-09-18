"""
마법사에 넣을 예시 그림을 지금 화면에서 만든다. (개발용. 배포본에는 만들어진 assets/*.png만 들어간다)
현재 config.json의 스킬 칸·이름표 칸·감시 칸 자리를 찍고 그 위에 테두리를 그린다.
"""
import json
from pathlib import Path

import mss
from PIL import Image, ImageDraw

import app

HERE = Path(__file__).parent
OUT = HERE / "assets"
OUT.mkdir(exist_ok=True)

cfg = json.loads((HERE / "MapleSkillMirror" / "config.json").read_text(encoding="utf-8"))
maple = app.make_maple_window(); maple.refresh()
origin = maple.origin
sct = mss.MSS()


def grab(x, y, w, h):
    return app.Image.frombytes("RGB", (w, h), sct.grab({"left": x, "top": y, "width": w, "height": h}).bgra, "raw", "BGRX")


def picture(rects, color, margin=24, scale=2, top=None):
    """rects: 화면 좌표 (x, y, w, h) 목록. 주변을 찍고 테두리를 그린 뒤 확대."""
    x0 = min(r[0] for r in rects) - margin; y0 = min(r[1] for r in rects) - (top if top is not None else margin)
    x1 = max(r[0] + r[2] for r in rects) + margin; y1 = max(r[1] + r[3] for r in rects) + margin
    img = grab(x0, y0, x1 - x0, y1 - y0)
    d = ImageDraw.Draw(img)
    for (x, y, w, h) in rects:
        d.rectangle((x - x0 - 1, y - y0 - 1, x - x0 + w, y - y0 + h), outline=color, width=2)
    img = img.resize((img.width * scale, img.height * scale), Image.NEAREST)
    if img.width > 420:
        img = img.resize((420, int(img.height * 420 / img.width)), Image.LANCZOS)
    return img


# 1) 스킬 칸
# 칸이 가장 많은 한 줄만 그림에 담는다 (다른 곳에 따로 둔 칸까지 넣으면 그림이 너무 커진다)
rows = {}
for b in cfg["boxes"]:
    x, y = app.box_screen_pos(b, origin)
    rows.setdefault(round(y / 20), []).append((x, y, b["size"], b["size"]))
rects = max(rows.values(), key=len)
picture(rects, app.COLOR_EDIT, margin=12, scale=2).save(OUT / "wizard_skills.png")

# 2) 이름표: 지금 캐릭터 위치를 찾아서 그 자리에 파란 테두리
tr = app.CharTracker(cfg)
tr.template is not None or exit("nametag.png 없음")
frame = app.Frame(app.Screen(), *maple.rect)
found = tr.find(frame, margin=0.02)
if not found:
    exit("이름표를 못 찾음 — 캐릭터가 서 있을 때 다시")
cx, ty, _ = found
tw, th = tr.tag_size
picture([(cx - tw // 2, ty, tw, th)], app.COLOR_TAG, margin=40, top=110, scale=2).save(OUT / "wizard_tag.png")

# 3) 감시 칸
w = cfg["idle"]["watch"]
if w:
    picture([(*app.box_screen_pos(w, origin), w["w"], w["h"])], app.COLOR_WATCH, margin=30, scale=2).save(OUT / "wizard_watch.png")
print("done:", sorted(p.name for p in OUT.iterdir()))
