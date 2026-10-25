"""固定高度的三条情报卡；用字形渲染，新闻文字不交给图像模型。"""

from pathlib import Path


def render_brief_card(brief: dict, output: str, font_path: str = "", detail_hint: str = "") -> str:
    from PIL import Image, ImageDraw, ImageFont

    candidates = [font_path, "/AstrBot/data/plugin_data/astrbot_plugin_anisignal/NotoSansCJKsc-Regular.otf",
                  "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc", "C:/Windows/Fonts/msyh.ttc"]
    font_file = next((p for p in candidates if p and Path(p).is_file()), None)
    if not font_file:
        raise FileNotFoundError("Chinese font is required for brief cards")
    font = lambda size: ImageFont.truetype(font_file, size)
    items = brief.get("items", [])[:3]
    width, height = 960, 180 + len(items) * 166 + 72
    image = Image.new("RGB", (width, height), "#f6f4ed")
    draw = ImageDraw.Draw(image)
    ink, green, muted = "#203c35", "#387861", "#63766f"
    draw.rounded_rectangle((20, 20, width - 20, height - 20), radius=24, fill="#fffefa")
    draw.rounded_rectangle((48, 47, 55, 118), radius=3, fill=green)
    draw.text((76, 42), "AniSignal 情报笺", font=font(38), fill=ink)
    date = brief.get("date", "").split(" ", 1)[0]
    draw.text((78, 101), f"{brief.get('title', '今日速报')}  /  {date} · 北京时间", font=font(23), fill=muted)
    colors = {"wuwa": "#bc922f", "blhx": "#368fba", "hsr": "#8671b8", "arknights": "#c7803c"}
    labels = {"wuwa": "鸣潮", "blhx": "碧蓝航线", "hsr": "星穹铁道", "arknights": "明日方舟"}

    def wrapped(text, max_width, face, lines=2):
        output_lines, current = [], ""
        for char in text.replace("\n", " "):
            # 中文句末标点可悬挂一格，避免最后一行只剩一个句号。
            if char in "，。！？；：、）》】”’!?.,;:)" and current:
                current += char
                continue
            if draw.textlength(current + char, font=face) > max_width:
                output_lines.append(current)
                current = char
            else:
                current += char
        if current:
            output_lines.append(current)
        if len(output_lines) > lines:
            output_lines = output_lines[:lines]
            output_lines[-1] = output_lines[-1][:-1] + "…"
        return output_lines

    for index, item in enumerate(items, 1):
        y = 163 + (index - 1) * 166
        color = colors.get(item.get("community"), green)
        draw.line((52, y - 8, 908, y - 8), fill="#e7ebe3", width=2)
        draw.text((52, y + 14), f"0{index}", font=font(34), fill=color)
        draw.text((127, y + 7), labels.get(item.get("community"), "情报"), font=font(20), fill=color)
        if item.get("published_label"):
            draw.text((907, y + 7), item["published_label"], font=font(18), fill=muted, anchor="ra")
        for n, line in enumerate(wrapped(item.get("title", "新动态"), 760, font(30), 1)):
            draw.text((126, y + 42 + n * 36), line, font=font(30), fill=ink)
        for n, line in enumerate(wrapped(item.get("summary") or "", 760, font(25))):
            draw.text((127, y + 89 + n * 32), line, font=font(25), fill=muted)
    if detail_hint:
        draw.text((54, height - 64), detail_hint, font=font(23), fill=green)
    draw.text((807, height - 62), "AniSignal", font=font(19), fill=muted)
    Path(output).parent.mkdir(parents=True, exist_ok=True)
    image.save(output, "PNG", optimize=True)
    return output
