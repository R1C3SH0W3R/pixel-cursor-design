"""Build and independently inspect DIB CUR/ANI; Pillow required, no system changes."""
from __future__ import annotations

import argparse
import ctypes
import hashlib
import json
import os
from pathlib import Path
import re
import struct
import sys

from PIL import Image


def require(condition, message):
    if not condition:
        raise ValueError(message)


def checked_pixels(im):
    im = im.convert("RGBA")
    require(1 <= im.width <= 256 and 1 <= im.height <= 256, "CUR dimensions must be 1..256")
    raw = bytearray(im.tobytes())
    for offset in range(0, len(raw), 4):
        require(raw[offset + 3] in (0, 255), "Prepare binary alpha before exporting pixel art")
        if raw[offset + 3] == 0:
            raw[offset:offset + 3] = b"\0\0\0"
    return Image.frombytes("RGBA", im.size, bytes(raw))


def encode_dib(im):
    im = checked_pixels(im)
    w, h = im.size
    stride = ((w + 31) // 32) * 4
    mask = bytearray(stride * h)
    alpha = im.getchannel("A")
    for y in range(h):
        for x in range(w):
            if alpha.getpixel((x, y)) == 0:
                mask[(h - y - 1) * stride + x // 8] |= 0x80 >> (x % 8)
    xor = im.tobytes("raw", "BGRA", 0, -1)
    header = struct.pack("<IiiHHIIiiII", 40, w, h * 2, 1, 32, 0, len(xor), 0, 0, 0, 0)
    return header + xor + mask


def encode_cur(entries):
    """entries: iterable of (RGBA image, (hotspot_x, hotspot_y))."""
    entries = list(entries)
    require(0 < len(entries) <= 65535, "Invalid CUR entry count")
    directory, payloads = bytearray(), bytearray()
    offset = 6 + 16 * len(entries)
    sizes = set()
    for im, hotspot in entries:
        w, h = im.size
        require((w, h) not in sizes, "Duplicate image dimensions")
        sizes.add((w, h))
        require(len(hotspot) == 2 and all(type(v) is int for v in hotspot), "Integer hotspot required")
        hx, hy = hotspot
        require(0 <= hx < w and 0 <= hy < h, "Hotspot outside canvas")
        payload = encode_dib(im)
        directory += struct.pack("<BBBBHHII", w % 256, h % 256, 0, 0, hx, hy, len(payload), offset)
        payloads += payload
        offset += len(payload)
    return struct.pack("<HHH", 0, 2, len(entries)) + directory + payloads


def chunk(tag, data):
    return tag + struct.pack("<I", len(data)) + data + (b"\0" if len(data) & 1 else b"")


def encode_ani(frames, jiffies=10):
    """Uniform sequential animation; frames are complete CUR bytes."""
    require(bool(frames), "At least one frame is required")
    require(type(jiffies) is int and 1 <= jiffies <= 0xFFFFFFFF, "Positive integer jiffies required")
    first = parse_cur(frames[0])
    for frame in frames[1:]:
        current = parse_cur(frame)
        require(set(current) == set(first), "Frame dimensions differ")
        require(all(current[s][0] == first[s][0] for s in first), "Hotspot moves between frames")
    anih = struct.pack("<9I", 36, len(frames), len(frames), 0, 0, 32, 1, jiffies, 1)
    body = chunk(b"anih", anih) + chunk(b"LIST", b"fram" + b"".join(chunk(b"icon", f) for f in frames))
    return b"RIFF" + struct.pack("<I", len(body) + 4) + b"ACON" + body


def parse_cur(data):
    require(len(data) >= 6, "Truncated CUR")
    reserved, kind, count = struct.unpack_from("<HHH", data)
    require(reserved == 0 and kind == 2 and count > 0, "Expected CUR directory")
    require(6 + 16 * count <= len(data), "Truncated CUR entries")
    result, spans = {}, []
    for index in range(count):
        w, h, colors, pad, hx, hy, length, offset = struct.unpack_from("<BBBBHHII", data, 6 + 16 * index)
        w, h = w or 256, h or 256
        require(pad == 0 and 0 <= hx < w and 0 <= hy < h, "Invalid CUR entry/hotspot")
        require((w, h) not in result, "Duplicate CUR dimensions")
        require(offset >= 6 + 16 * count and offset + length <= len(data), "CUR payload outside file")
        require(all(offset + length <= a or offset >= b for a, b in spans), "Overlapping CUR payloads")
        spans.append((offset, offset + length))
        payload = data[offset:offset + length]
        require(not payload.startswith(b"\x89PNG"), "PNG payload is outside this DIB validator's supported subset")
        require(len(payload) >= 40, "Truncated DIB header")
        head, dw, dh, planes, bpp, compression = struct.unpack_from("<IiiHHI", payload)
        require((head, dw, dh, planes, bpp, compression) == (40, w, h * 2, 1, 32, 0), "Expected 32-bit uncompressed bottom-up DIB")
        stride = ((w + 31) // 32) * 4
        xor_end = 40 + w * h * 4
        require(len(payload) == xor_end + stride * h, "Incorrect DIB XOR/AND length")
        im = Image.frombytes("RGBA", (w, h), payload[40:xor_end], "raw", "BGRA", 0, -1)
        mask = payload[xor_end:]
        for y in range(h):
            for x in range(w):
                r, g, b, a = im.getpixel((x, y))
                require(a in (0, 255), "Non-binary alpha outside pixel-art subset")
                transparent = bool(mask[(h - y - 1) * stride + x // 8] & (0x80 >> (x % 8)))
                require(transparent == (a == 0), "AND mask differs from alpha")
                require(a or (r, g, b) == (0, 0, 0), "Transparent pixel contains RGB residue")
        result[(w, h)] = ((hx, hy), im)
    return result


def iter_chunks(data, start=0, end=None):
    end = len(data) if end is None else end
    pos = start
    while pos < end:
        require(pos + 8 <= end, "Truncated RIFF chunk header")
        tag, length = struct.unpack_from("<4sI", data, pos)
        stop = pos + 8 + length
        require(stop + (length & 1) <= end, "RIFF chunk exceeds boundary")
        yield tag, data[pos + 8:stop]
        pos = stop + (length & 1)
    require(pos == end, "RIFF alignment error")


def parse_ani(data):
    require(len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"ACON", "Expected RIFF/ACON")
    require(struct.unpack_from("<I", data, 4)[0] == len(data) - 8, "Incorrect RIFF length")
    header, frames, rate, sequence = None, [], None, None
    frame_list_seen = False
    for tag, payload in iter_chunks(data, 12):
        if tag == b"anih":
            require(header is None and len(payload) == 36, "Duplicate or invalid anih")
            header = struct.unpack("<9I", payload)
        elif tag in (b"rate", b"seq "):
            require(len(payload) % 4 == 0, "Invalid DWORD array")
            values = list(struct.unpack(f"<{len(payload) // 4}I", payload))
            if tag == b"rate":
                require(rate is None, "Duplicate rate")
                rate = values
            else:
                require(sequence is None, "Duplicate sequence")
                sequence = values
        elif tag == b"LIST" and payload[:4] == b"fram":
            require(not frame_list_seen, "Duplicate frame list")
            frame_list_seen = True
            frames = [parse_cur(p) for t, p in iter_chunks(payload, 4) if t == b"icon"]
    require(header is not None, "Missing anih")
    cb, count, steps, cx, cy, bits, planes, jif, flags = header
    require(cb == 36 and count == len(frames) and count > 0 and steps > 0, "Incorrect ANI frame/step counts")
    require(flags & 1 and not (flags & ~3), "Unsupported ANI flags")
    if flags & 2:
        require(sequence is not None and len(sequence) == steps, "Sequence flag/count mismatch")
    else:
        require(sequence is None and steps == count, "Sequential ANI step count mismatch")
        sequence = list(range(count))
    require(all(i < count for i in sequence), "Sequence index outside frames")
    if rate is None:
        require(jif > 0, "No valid default frame duration")
        rate = [jif] * steps
    require(len(rate) == steps and all(v > 0 for v in rate), "Invalid frame durations")
    return frames, sequence, rate


def native_check(path, size, expected_hotspot):
    """Read-only Windows load/GetIconInfo/GetObject; does not set system cursors."""
    from ctypes import wintypes as wt

    class IconInfo(ctypes.Structure):
        _fields_ = [("fIcon", wt.BOOL), ("xHotspot", wt.DWORD), ("yHotspot", wt.DWORD),
                    ("hbmMask", wt.HBITMAP), ("hbmColor", wt.HBITMAP)]

    class Bitmap(ctypes.Structure):
        _fields_ = [("bmType", wt.LONG), ("bmWidth", wt.LONG), ("bmHeight", wt.LONG),
                    ("bmWidthBytes", wt.LONG), ("bmPlanes", wt.WORD),
                    ("bmBitsPixel", wt.WORD), ("bmBits", ctypes.c_void_p)]

    user = ctypes.WinDLL("user32", use_last_error=True)
    gdi = ctypes.WinDLL("gdi32", use_last_error=True)
    user.LoadImageW.argtypes = [wt.HINSTANCE, wt.LPCWSTR, wt.UINT, ctypes.c_int, ctypes.c_int, wt.UINT]
    user.LoadImageW.restype = wt.HANDLE
    user.GetIconInfo.argtypes = [wt.HANDLE, ctypes.POINTER(IconInfo)]
    user.GetIconInfo.restype = wt.BOOL
    user.DestroyCursor.argtypes = [wt.HANDLE]
    user.DestroyCursor.restype = wt.BOOL
    gdi.DeleteObject.argtypes = [wt.HANDLE]
    gdi.DeleteObject.restype = wt.BOOL
    gdi.GetObjectW.argtypes = [wt.HANDLE, ctypes.c_int, ctypes.c_void_p]
    gdi.GetObjectW.restype = ctypes.c_int
    handle = user.LoadImageW(None, str(path.resolve()), 2, *size, 0x10)
    require(bool(handle), f"Windows load failed: {path.name}, {size}, error={ctypes.get_last_error()}")
    info = IconInfo()
    try:
        require(user.GetIconInfo(handle, ctypes.byref(info)), "GetIconInfo failed")
        require(not info.fIcon, "Loaded object is not a cursor")
        require((info.xHotspot, info.yHotspot) == expected_hotspot, "Windows hotspot differs")
        bitmap = Bitmap()
        require(info.hbmColor and gdi.GetObjectW(info.hbmColor, ctypes.sizeof(bitmap), ctypes.byref(bitmap)), "GetObjectW failed")
        require((bitmap.bmWidth, bitmap.bmHeight) == size, "Windows selected/resized a different bitmap size")
    finally:
        for obj in (info.hbmMask, info.hbmColor):
            if obj:
                gdi.DeleteObject(obj)
        user.DestroyCursor(handle)


def inspect_file(path, native=False, margin=0):
    data = path.read_bytes()
    if path.suffix.lower() == ".cur":
        frames, sequence, rate = [parse_cur(data)], [0], []
    else:
        frames, sequence, rate = parse_ani(data)
    first = frames[sequence[0]]
    distinct = set()
    for index, frame in enumerate(frames):
        require(set(frame) == set(first), f"Frame sizes differ: {path.name}/{index}")
        pixels = hashlib.sha256()
        for size, (hotspot, im) in sorted(frame.items()):
            require(hotspot == first[size][0], f"Moving hotspot: {path.name}/{index}/{size}")
            box = im.getchannel("A").getbbox()
            require(box is not None, f"Empty frame: {path.name}/{index}/{size}")
            require(box[0] >= margin and box[1] >= margin and box[2] <= size[0] - margin and box[3] <= size[1] - margin,
                    f"Insufficient margin: {path.name}/{index}/{size}/{box}")
            pixels.update(im.tobytes())
        distinct.add(pixels.hexdigest())
    loads = 0
    if native:
        for size, (hotspot, _) in first.items():
            native_check(path, size, hotspot)
            loads += 1
    record = {
        "file": str(path.resolve()), "sha256": hashlib.sha256(data).hexdigest(),
        "frames": len(frames), "steps": len(sequence), "distinct_frames": len(distinct),
        "sizes": {f"{w}x{h}": {"hotspot": list(hotspot)} for (w, h), (hotspot, _) in first.items()},
        "jiffies": rate, "duration_ms": round(sum(rate) * 1000 / 60, 3),
        "frame_size_checks": len(frames) * len(first), "windows_loads": loads,
        "warnings": ["Multiple frames have no visible difference"] if len(frames) > 1 and len(distinct) == 1 else [],
    }
    return record, first


def validate(paths, native=False, margin=0):
    require(margin >= 0, "Margin must be nonnegative")
    require(not native or os.name == "nt", "--native requires Windows")
    files = []
    for path in paths:
        if path.is_dir():
            files.extend(p for p in path.iterdir() if p.suffix.lower() in (".cur", ".ani"))
        else:
            require(path.suffix.lower() in (".cur", ".ani"), "Expected CUR/ANI path")
            files.append(path)
    files = sorted(set(p.resolve() for p in files))
    require(bool(files), "No cursor files found")
    records, errors, decoded = [], [], {}
    for path in files:
        try:
            record, first = inspect_file(path, native, margin)
            records.append(record)
            decoded[path] = first
        except (ValueError, OSError, struct.error) as error:
            errors.append({"file": str(path), "error": str(error)})
    for path, cur in decoded.items():
        if path.suffix.lower() != ".cur":
            continue
        partner = next((p for p in decoded if p.parent == path.parent and p.stem == path.stem and p.suffix.lower() == ".ani"), None)
        if partner:
            ani = decoded[partner]
            same = set(cur) == set(ani) and all(cur[s][0] == ani[s][0] and cur[s][1].tobytes() == ani[s][1].tobytes() for s in cur)
            if not same:
                errors.append({"file": str(path), "error": "Static CUR differs from first played ANI frame"})
    return {
        "status": "failed" if errors else "passed", "files_checked": len(files),
        "frame_size_checks": sum(r["frame_size_checks"] for r in records),
        "windows_loads": sum(r["windows_loads"] for r in records),
        "native_status": "checked" if native else "not_requested",
        "scope": "Binary DIB/RIFF, fixed hotspots, bounds, first-frame match; optional native loading. Visual pose, semantic hotspot, palette consistency and actual playback require separate checks. No system settings changed.",
        "records": records, "errors": errors,
    }


def build(manifest, output, overwrite=False):
    spec = json.loads(manifest.read_text(encoding="utf-8-sig"))
    require(spec.get("version") == 1, "Manifest version must be 1")
    states = spec.get("states")
    require(isinstance(states, list) and states, "Manifest states must be a nonempty array")
    pending, names = [], set()
    for state in states:
        name = state["name"]
        require(re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", name), "Use lowercase alphanumeric/hyphen state names")
        require(name not in names, "Duplicate state name")
        names.add(name)
        sizes = state["sizes"]
        require(isinstance(sizes, dict) and sizes, "Each state requires sizes")
        normalized = []
        counts = set()
        for key, value in sizes.items():
            require(re.fullmatch(r"[1-9][0-9]*", key), "Size keys must be canonical integer strings")
            size = int(key)
            require(1 <= size <= 256, "Size must be 1..256")
            paths = value["frames"]
            require(isinstance(paths, list) and paths, "Each size requires a nonempty frame list")
            counts.add(len(paths))
            normalized.append((size, value))
        require(len(counts) == 1, "Every size must have the same frame count")
        encoded = []
        for index in range(next(iter(counts))):
            entries = []
            for size, value in sorted(normalized):
                source = manifest.parent / value["frames"][index]
                with Image.open(source) as opened:
                    require(opened.size == (size, size), f"Prepare exact-size PNG first: {source}")
                    entries.append((opened.convert("RGBA"), value["hotspot"]))
            encoded.append(encode_cur(entries))
        pending.append((output / f"{name}.cur", encoded[0]))
        if len(encoded) > 1:
            require(len(set(encoded)) > 1, f"All animation frames are identical: {name}")
            pending.append((output / f"{name}.ani", encode_ani(encoded, state.get("jiffies", spec.get("jiffies", 10)))))
    require(overwrite or not any(path.exists() for path, _ in pending), "Output exists; use a new directory or --overwrite after preserving prior files")
    output.mkdir(parents=True, exist_ok=True)
    for path, data in pending:
        path.write_bytes(data)
    return {"status": "written", "files": [str(path.resolve()) for path, _ in pending]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    exporter = commands.add_parser("build", help="Build prepared PNG frames using a JSON manifest")
    exporter.add_argument("manifest", type=Path)
    exporter.add_argument("--out", type=Path, required=True)
    exporter.add_argument("--overwrite", action="store_true")
    checker = commands.add_parser("validate", help="Inspect exported DIB CUR/ANI without installing")
    checker.add_argument("paths", type=Path, nargs="+")
    checker.add_argument("--native", action="store_true")
    checker.add_argument("--margin", type=int, default=0)
    checker.add_argument("--report", type=Path)
    args = parser.parse_args()
    try:
        if args.command == "build":
            result = build(args.manifest.resolve(), args.out.resolve(), args.overwrite)
        else:
            result = validate(args.paths, args.native, args.margin)
            if args.report:
                args.report.parent.mkdir(parents=True, exist_ok=True)
                args.report.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(result, ensure_ascii=True, indent=2))
        return 1 if result["status"] == "failed" else 0
    except (ValueError, KeyError, TypeError, OSError, struct.error) as error:
        print(f"Error: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
