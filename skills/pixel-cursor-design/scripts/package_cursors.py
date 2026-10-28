"""Validate a finished cursor set and create user-ready previews, instructions and ZIP."""
from __future__ import annotations

import argparse
import json
import math
import os
from pathlib import Path
import re
import shutil
import sys
from zipfile import ZIP_DEFLATED, ZipFile

from PIL import Image, ImageDraw, ImageFont

import cursor_io


STATES = {
    "normal": ("正常选择", "Arrow"), "help": ("帮助选择", "Help"),
    "background": ("在后台工作", "AppStarting"), "busy": ("忙", "Wait"),
    "precise": ("精确选择", "Crosshair"), "text": ("文本选择", "IBeam"),
    "pen": ("笔", "NWPen"), "unavailable": ("不可用", "No"),
    "vresize": ("垂直调整大小", "SizeNS"), "hresize": ("水平调整大小", "SizeWE"),
    "diag1": ("对角线调整大小 1", "SizeNWSE"), "diag2": ("对角线调整大小 2", "SizeNESW"),
    "move": ("移动", "SizeAll"), "alternate": ("备用选择", "UpArrow"),
    "link": ("链接选择", "Hand"), "location": ("位置选择", "Pin"),
    "person": ("个人选择", "Person"),
}


def pick_size(frame):
    # Display native pixels and an integer zoom; never invent a 128px detail version.
    sizes = list(frame)
    return min(sizes, key=lambda size: (abs(max(size) - 64), max(size)))


def preview_font():
    for path, chinese in (
        ("C:/Windows/Fonts/msyh.ttc", True),
        ("/System/Library/Fonts/PingFang.ttc", True),
        ("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc", True),
        ("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", False),
    ):
        if Path(path).is_file():
            return ImageFont.truetype(path, 14), chinese
    return ImageFont.load_default(), False


def load_animation(path):
    if path.suffix == ".ani":
        frames, sequence, jiffies = cursor_io.parse_ani(path.read_bytes())
        return [frames[i] for i in sequence], jiffies
    return [cursor_io.parse_cur(path.read_bytes())], [60]


def make_previews(preview, animations):
    font, chinese = preview_font()
    columns, cell_w, cell_h = 4, 230, 288
    rows = math.ceil(len(animations) / columns)
    canvases = {"overview-light": ("#f4f6fc", "#152038"),
                "overview-dark": ("#202631", "#ecf1fa"),
                "hotspots": ("#e9eff8", "#152038")}
    for name, (background, ink) in canvases.items():
        sheet = Image.new("RGBA", (columns * cell_w, rows * cell_h), background)
        draw = ImageDraw.Draw(sheet)
        for index, (state, (frames, _)) in enumerate(animations.items()):
            x, y = index % columns * cell_w, index // columns * cell_h
            size = pick_size(frames[0])
            hotspot, im = frames[0][size]
            zoom = max(1, min(3, 156 // max(size)))
            scaled = im.resize((im.width * zoom, im.height * zoom), Image.Resampling.NEAREST)
            px, py = x + (cell_w - scaled.width) // 2, y + 10
            sheet.alpha_composite(scaled, (px, py))
            if name == "hotspots":
                hx, hy = px + (hotspot[0] + .5) * zoom, py + (hotspot[1] + .5) * zoom
                draw.line((hx - 5, hy, hx + 5, hy), fill="#e53d65", width=1)
                draw.line((hx, hy - 5, hx, hy + 5), fill="#e53d65", width=1)
            actual = im if max(size) <= 96 else None
            if actual is not None:
                sheet.alpha_composite(actual, (x + 12, y + 168))
            draw.text((x + 92, y + 178), f"{size[0]}px  1:1" if actual else f"{size[0]}px source", font=font, fill=ink)
            if name == "hotspots":
                draw.text((x + 92, y + 201), f"hotspot {hotspot}", font=font, fill=ink)
            label = f"{STATES[state][0]} / {state}" if chinese else state
            draw.text((x + 10, y + 244), label, font=font, fill=ink)
        sheet.convert("RGB").save(preview / f"{name}.png")

    for state, (frames, rates) in animations.items():
        size = pick_size(frames[0])
        zoom = max(1, min(3, 192 // max(size)))
        gif_frames = []
        for frame in frames:
            im = frame[size][1]
            bg = Image.new("RGBA", im.size, "#e8eff9")
            bg.alpha_composite(im)
            gif_frames.append(bg.resize((im.width * zoom, im.height * zoom), Image.Resampling.NEAREST).convert("RGB"))
        # GIF stores centiseconds. Round cumulative boundaries to avoid drift.
        elapsed, previous, durations = 0, 0, []
        for rate in rates:
            elapsed += rate
            boundary = round(elapsed * 100 / 60)
            durations.append(max(10, (boundary - previous) * 10))
            previous = boundary
        gif_frames[0].save(preview / f"{state}.gif", save_all=True, append_images=gif_frames[1:],
                           duration=durations, loop=0, disposal=2)


def package(files, output, slug, title, states, static=False, native=None, margin=1):
    cursor_io.require(re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", slug), "Package slug must use lowercase letters, digits or hyphens")
    cursor_io.require(states and len(states) == len(set(states)) and all(s in STATES for s in states), "Invalid or duplicate states")
    output = output.resolve()
    destination, archive = output / slug, output / f"{slug}.zip"
    cursor_io.require(not destination.exists() and not archive.exists(), "Use a new output directory/slug; previous packages are preserved")
    selected = []
    for state in states:
        for extension in ((".cur",) if static else (".cur", ".ani")):
            path = files / f"{state}{extension}"
            cursor_io.require(path.is_file(), f"Missing required cursor: {path.name}")
            selected.append(path)
    native = os.name == "nt" if native is None else native
    report = cursor_io.validate(selected, native=native, margin=margin)
    cursor_io.require(report["status"] == "passed", json.dumps(report["errors"], ensure_ascii=False))
    if not static:
        for record in report["records"]:
            if record["file"].lower().endswith(".ani"):
                cursor_io.require(record["frames"] > 1 and record["distinct_frames"] > 1,
                                  f"No visible animation: {Path(record['file']).name}")
    # Whitelist final assets; never recursively zip the project or its credentials.
    (destination / "files").mkdir(parents=True)
    preview = destination / "preview"
    preview.mkdir()
    for path in selected:
        shutil.copy2(path, destination / "files" / path.name)
    for record in report["records"]:
        record["file"] = "files/" + Path(record["file"]).name
    report["required_states"] = states
    report["full_17_state_pack"] = set(states) == set(STATES)
    report["generation_tested_by_packager"] = False
    report["visual_and_semantic_review"] = "Must be performed by the assistant; not certified by this script"
    (destination / "validation.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    animations = {state: load_animation(destination / "files" / f"{state}{'.cur' if static else '.ani'}") for state in states}
    make_previews(preview, animations)
    mapping = [{"id": state, "label": STATES[state][0], "windows_role": STATES[state][1],
                "recommended_file": f"files/{state}{'.cur' if static else '.ani'}", "static_file": f"files/{state}.cur"}
               for state in states]
    (destination / "cursor-map.json").write_text(json.dumps(mapping, ensure_ascii=False, indent=2), encoding="utf-8")
    clean_title = " ".join(title.split())
    instructions = [f"# {clean_title}", "", f"包含 {len(states)} 种状态；用于 Windows 10/11。", "",
                    "## 使用方法", "", "1. 解压整个压缩包，将文件夹保存到固定位置。",
                    "2. 打开鼠标属性 → 指针，选中一个状态，点击“浏览”。",
                    "3. 按下表选择 files 文件夹中的文件并应用。ANI 是动态，CUR 是静态。",
                    "4. 设置好后可将方案另存为方便识别的名称。", "",
                    "如果仍显示旧样式，先选系统默认方案并应用，再重新选择新文件。不要在应用后移动或删除光标文件夹。",
                    "", "本包不会自动修改系统设置。其他系统需要另外适配。", "",
                    "## 状态对照", "", "| Windows 状态 | 文件 |", "|---|---|"]
    instructions += [f"| {STATES[s][0]} | files/{s}{'.cur' if static else '.ani'} |" for s in states]
    instructions += ["", "## 预览与检查", "", "preview 中包含深浅底总览、实际大小、每个状态的动画预览和热点图。",
                     "热点图的红色标记仅用于检查，不会出现在实际光标里。GIF 预览时长按其格式精度近似，不改变 ANI 的原始时长。",
                     "已完成文件结构、帧尺寸、热点一致性和边界检查。",
                     "Windows 原生加载与热点读回已检查；未自动应用系统，不能据此推断已测试所有应用的动画播放。" if native
                     else "本次未进行 Windows 原生加载；需在 Windows 上检查。",
                     "" ]
    (destination / "使用说明.md").write_text("\n".join(instructions), encoding="utf-8")
    with ZipFile(archive, "w", ZIP_DEFLATED) as packed:
        for path in sorted(destination.rglob("*")):
            if path.is_file():
                packed.write(path, (Path(slug) / path.relative_to(destination)).as_posix())
    return {"status": "passed", "archive": str(archive), "folder": str(destination),
            "states": len(states), "cursor_files": len(selected), "windows_loads": report["windows_loads"],
            "frame_size_checks": report["frame_size_checks"], "preview": str(preview / "overview-light.png")}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("files", type=Path, help="Directory containing exported CUR/ANI")
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--slug", default="pixel-cursors")
    parser.add_argument("--title", default="个性化像素光标")
    parser.add_argument("--states", nargs="+", choices=list(STATES), help="Only for an explicitly requested subset")
    parser.add_argument("--static", action="store_true", help="Only for an explicitly requested static pack")
    parser.add_argument("--no-native", action="store_true", help="Skip Windows loading and label it untested")
    parser.add_argument("--margin", type=int, default=1)
    args = parser.parse_args()
    try:
        result = package(args.files.resolve(), args.out, args.slug, args.title,
                         args.states or list(STATES), args.static, False if args.no_native else None, args.margin)
        print(json.dumps(result, ensure_ascii=True, indent=2))
        return 0
    except (ValueError, OSError) as error:
        print(f"Error: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
