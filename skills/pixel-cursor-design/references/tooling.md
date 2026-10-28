# 内部制作、检查与自动交付工具

本页供执行技能的助手使用，不是使用者需要操作的说明。使用者可以只上传参考图；助手负责产生素材、最终帧、热点与下述清单并运行所有命令，不让用户填写 JSON 或运行 Python。

本页描述附带 Python 工具的实现路径，其他 Agent 可以采用 [能力适配中的等效路线](agent-adapters.md#无-python-的等效路线)。不要求终端：能在代码解释器导入模块也可调用相同函数。未得到脚本文件时，不假定代码存在；使用完整技能包，或依据格式规范在现有运行时实现。

`scripts/cursor_io.py` 使用 Python 3.10+ 和 Pillow 9.1+，不读取密钥、不联网、不安装系统光标。用已确认的 Python 解释器运行 `scripts/probe_runtime.py` 可检查这条本地路线；探测器不判断宿主的看图/绘图工具，也不需要联网。源图设计与像素处理先完成，再交给编码器。已有可靠导出器可继续使用。

## 输入约定

使用透明 RGBA PNG，每一帧已处于最终画布上；宽高和 manifest 的尺寸一致。像素 alpha 仅 0/255；工具不会擅自缩图、改色、阈值化或吸附热点。透明像素的 RGB 会归零。

manifest 的所有相对图片路径相对于 manifest 文件所在目录，状态名作为输出文件名。一个状态所有尺寸具有相同帧数，热点为该尺寸的一组固定整数坐标。默认 jiffies 为 10，可在状态级覆盖。单帧只输出 CUR，多帧输出 CUR 和 ANI；多帧内容必须有差异。

```json
{
  "version": 1,
  "jiffies": 10,
  "states": [
    {
      "name": "text",
      "sizes": {
        "32": {"hotspot": [7, 16], "frames": ["frames/text-32-00.png", "frames/text-32-01.png"]},
        "64": {"hotspot": [14, 32], "frames": ["frames/text-64-00.png", "frames/text-64-01.png"]}
      }
    }
  ]
}
```

这里的坐标仅是格式示例，实际需按图像的功能指示点确定。工具支持 1..256 的方形尺寸；库函数 `encode_cur` 也接受矩形图。不要默认导出所有可能尺寸。

## 命令

下列 `SKILL_DIR`、`PROJECT` 是由助手替换的路径标记，不是预设环境变量。`python` 指已确认可用的解释器；不同宿主可能使用 `python3`、完整解释器路径或直接执行 Python 代码，不能假定某命令名必然存在。

```text
python "SKILL_DIR/scripts/cursor_io.py" build "PROJECT/manifest.json" --out "PROJECT/cursors-new"
python "SKILL_DIR/scripts/cursor_io.py" validate "PROJECT/cursors-new" --margin 1 --native --report "PROJECT/validation.json"
```

默认不覆盖已存在的同名输出。备份后，已授权的重复构建可加 `--overwrite`，不用因此重新询问用户。工具不会清除旧目录中的其他文件；从动态改为静态时，优先导出到新目录，避免遗留旧 ANI 造成误选。

`validate` 可接受目录或多个文件，目录只扫描直接子文件。`--native` 仅 Windows 可用，会真实加载文件、读回热点与尺寸，但不应用到系统；其他平台省略它并如实说明未做原生检查。`--margin 1` 要求全部帧距边缘至少一像素；这是适用于本类完整角色图的检查参数，不是 CUR 格式的规定。

报告包含 SHA256、尺寸、热点、帧/步数、时长、不同帧数量、错误和原生加载计数。工具独立解析 DIB 与 RIFF，核对 alpha/mask、所有帧热点不动、边距、静态文件与 ANI 首个播放帧一致。`validate` 成功返回 0，任何失败返回 1。

解析器面向本技能的二值透明、32 位 DIB 子集，不支持 PNG payload 或其他合法编码；遇到“超出支持范围”不应宣称文件必然损坏。输入文件过大或来源不明时先查看体积与结构，不无限制重复尝试。

## 需要另做的检查

- 功能语义：脚本不知道笔尖、指尖、文本杠在哪，不能自动证明热点正确。
- 共享色板与轮廓风格：将解码图与目标图/基准对照；没有强制固定色数。
- 特殊布局：如角色与杠底对齐，需要在渲染层保存角色和功能层分别检查。
- 真实播放：原生加载不是系统动画演示，不能据此称已安装或已测试所有应用。
- 美术质量：实际大小、手臂接触、完整人物、首尾衔接和主题辨识仍需看图。

Python 中也可使用 `encode_cur([(image, (x, y)), ...])`、`encode_ani([cur_frame, ...], jiffies)`、`parse_cur(data)`、`parse_ani(data)`。后两者返回解码帧与时序，可接入已有针对性检查，避免复制此工具或把项目资产放入技能目录。

## 解析和无损提取既有动画

`parse_cur(data)` 返回 `{(width, height): ((hotspot_x, hotspot_y), PIL_RGBA_image)}`。`parse_ani(data)` 返回 `(frames, sequence, rates)`：frames 是 CUR 解码字典列表，sequence 是每个播放步骤引用的帧索引，rates 是每个播放步骤的时长，单位 1/60 秒。重复帧用于停留，不能去重后改变动作速度。

例如，助手可按 `for step, index in enumerate(sequence)` 遍历，用 `frames[index][(64,64)][1].save(...)` 保存逐步 PNG，并从对应 tuple 读取热点。这些坐标是输入成品的数据，不能照搬给新主题。重建等时长 manifest 时保留重复步骤；变时长可转为公约数步长的重复帧，或沿用支持 rate/seq 的现有导出器，避免未经验证的超大帧数。

## 自动成品包

`scripts/package_cursors.py` 同样只依赖 Python/Pillow，与 `cursor_io.py` 同目录。它接收已经导出的光标文件，负责最终检查、预览和交付；不会从参考图自动绘画，因此助手必须先完成 [参考图流程](reference-to-pack.md)。

```text
python "SKILL_DIR/scripts/package_cursors.py" "PROJECT/files" --out "PROJECT/delivery" --slug "my-pixel-cursors" --title "个性化像素光标"
```

默认强制完整 17 个状态的 CUR 和具有可见变化的 ANI，检查边距至少一像素。Windows 上自动做原生加载，其他平台明确标记未测。然后输出：

- `my-pixel-cursors.zip`：直接给使用者的成品。
- 同名目录里的 `files/`：34 个光标文件。
- `preview/overview-light.png`、`overview-dark.png`：深浅背景、1 倍大小和整数倍放大总览。
- `preview/<state>.gif`、`hotspots.png`：逐状态动画与热点图。
- `使用说明.md`、`cursor-map.json`、`validation.json`：应用方法、17 状态对照和真实检查记录。

包内路径相对化，只复制指定成品，不夹带整个工作区、原用户图片、密钥、生成脚本和本机路径。使用已有文件夹/包名时会停止以避免覆盖；修订可换输出目录或版本名。

只有用户明确要求局部修改时添加 `--states text pen` 之类的范围；明确只要静态时添加 `--static`。`--no-native` 会跳过并如实标记原生检查，不得用它掩盖原生加载失败。可选 `--margin` 适配有意贴边的设计，不应拿它掩盖不完整构图。

打包器生成的是技术检查和预览，不会替助手判断人物身份、手臂自然度、语义热点和动作流畅性；助手仍需打开产物查看并修复实际问题。
