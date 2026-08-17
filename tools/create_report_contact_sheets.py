import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


folder = (
    Path(sys.argv[1])
    if len(sys.argv) > 1
    else Path(__file__).resolve().parents[1] / "deliverables" / "rendered_report_word_v3"
)
pages = sorted(folder.glob("page-*.png"))
label_font = ImageFont.truetype(r"C:\Windows\Fonts\msyhbd.ttc", 28)

for group in range((len(pages) + 8) // 9):
    subset = pages[group * 9 : (group + 1) * 9]
    with Image.open(subset[0]) as sample:
        width, height = sample.size
    sheet = Image.new("RGB", (width * 3, height * 3 + 60), "#D0D5DD")
    draw = ImageDraw.Draw(sheet)
    for index, path in enumerate(subset):
        with Image.open(path) as page:
            image = page.convert("RGB")
        x = (index % 3) * width
        y = (index // 3) * height + 60
        sheet.paste(image, (x, y))
        draw.rectangle((x, y - 45, x + 190, y - 5), fill="#17365D")
        draw.text((x + 12, y - 42), path.stem, font=label_font, fill="white")
    sheet.save(folder / f"contact-{group + 1}.png")
