from pathlib import Path

from PIL import Image, ImageDraw

SIZE = 128
OUT = Path(__file__).resolve().parent.parent / "icon.png"

def main():
    img = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([4, 4, SIZE - 4, SIZE - 4], radius=28,
                        fill=(21, 25, 37, 255))
    accent = (139, 92, 246, 255)
    white = (247, 246, 255, 255)
    d.rounded_rectangle([18, 22, SIZE - 18, SIZE - 22], radius=10,
                        outline=white, width=4)
    d.rounded_rectangle([26, 30, SIZE - 26, SIZE - 30], radius=6,
                        fill=(36, 41, 60, 255))
    d.polygon([(30, 96), (56, 62), (76, 96)], fill=accent)
    d.polygon([(64, 96), (88, 70), (102, 96)], fill=(96, 165, 250, 255))
    d.ellipse([78, 38, 94, 54], fill=(250, 214, 120, 255))
    d.polygon([(30, 108), (42, 102), (42, 114)], fill=white)
    d.polygon([(52, 102), (64, 108), (52, 114)], fill=white)
    img.save(OUT, "PNG", optimize=True)
    size = OUT.stat().st_size
    print(f"icon: {OUT} {size} bytes, 128x128 RGBA")
    assert size <= 8192, f"иконка тяжёлая: {size}"

if __name__ == "__main__":
    main()
