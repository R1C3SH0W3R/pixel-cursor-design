"""Build one vendor-neutral Markdown guide from canonical workflow references."""
from pathlib import Path
import re


SECTIONS = (
    ("SKILL.md", "guide-workflow"),
    ("references/agent-adapters.md", "guide-adapters"),
    ("references/reference-to-pack.md", "guide-process"),
    ("references/design-and-motion.md", "guide-design"),
    ("references/windows-export.md", "guide-format"),
    ("references/tooling.md", "guide-tools"),
)


def make_guide(root):
    anchors = {name: anchor for name, anchor in SECTIONS}
    anchors.update({"START_HERE.md": "portable-guide", "使用说明.md": "portable-guide"})
    sections = []
    titles = []
    for name, anchor in SECTIONS:
        path = root / name
        content = path.read_text(encoding="utf-8")
        if name == "SKILL.md":
            content = re.sub(r"\A---\r?\n.*?\r?\n---\r?\n", "", content, count=1, flags=re.S)
        title = next(line.lstrip("# ") for line in content.splitlines() if line.startswith("# "))
        titles.append((title, anchor))

        def local_link(match):
            label, target = match.group(1), match.group(2)
            if "://" in target or target.startswith("#"):
                return match.group(0)
            relative = target.split("#", 1)[0]
            resolved = (path.parent / relative).resolve()
            try:
                key = resolved.relative_to(root.resolve()).as_posix()
            except ValueError:
                return label + "（外部参考）"
            if key in anchors:
                return f"[{label}](#{anchors[key]})"
            return f"{label}（完整包中的可选参考：{key}）"

        content = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", local_link, content)
        # Keep heading hierarchy without rewriting Markdown inside fenced examples.
        lines, in_fence = [], False
        for line in content.strip().splitlines():
            if line.lstrip().startswith("```"):
                in_fence = not in_fence
            if not in_fence and re.match(r"^#{1,5} ", line):
                line = "#" + line
            lines.append(line)
        sections.append(f'<a id="{anchor}"></a>\n\n' + "\n".join(lines))
    intro = [
        '<a id="portable-guide"></a>', "", "# 单文件通用指南：参考图生成像素动态光标", "",
        "将本文件作为任务说明提供给能够看图的 AI Agent，同时提供参考图。无需技能安装、特定工具名或专用调用前缀。",
        "执行者先检查本宿主的实际能力，再按下文完成制作；已有授权可用路径直接继续。宿主指令和权限始终优先。",
        "", "本文件由技能的正式说明自动汇编，包含设计、动画、格式、检查和工具数据约定。完整技能包另附 Python 工具，单独提供本文件时不能假定这些脚本存在。",
        "没有附带脚本但有可执行图像/二进制工具时，可按格式规范实现等效制作；缺少绘图或执行/文件交付能力时，需先说明具体缺口，不可宣称已生成。",
        "默认交付 Windows 光标；执行 Agent 的系统可不同。不宣称所有 Agent 或所有产品版本都已经实测。", "",
        "使用者可以说：请按照这份指南，把参考图制作成整套像素风动态光标，其余由你设计，给我预览和成品包。", "",
        "## 文档导航", "",
    ]
    intro.extend(f"- [{title}](#{anchor})" for title, anchor in titles)
    return "\n".join(intro) + "\n\n" + "\n\n---\n\n".join(sections) + "\n"


def main():
    root = Path(__file__).resolve().parent.parent
    output = root / "PORTABLE_GUIDE.md"
    output.write_text(make_guide(root), encoding="utf-8")
    print(f"Wrote {output.name} ({output.stat().st_size} bytes)")


if __name__ == "__main__":
    main()
