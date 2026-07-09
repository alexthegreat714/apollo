from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


APOLLO_ROOT = Path(__file__).resolve().parents[1]
FIXTURE_ROOT = APOLLO_ROOT / "evaluations" / "fixtures" / "ocr97_challenger"


def _font(size: int) -> ImageFont.ImageFont:
    for candidate in (
        Path(r"C:\Windows\Fonts\consola.ttf"),
        Path(r"C:\Windows\Fonts\arial.ttf"),
    ):
        if candidate.exists():
            return ImageFont.truetype(str(candidate), size=size)
    return ImageFont.load_default()


def _write_fixture(path: Path, *, lines: list[str], size: tuple[int, int], font_size: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    image = Image.new("RGB", size, color=(255, 255, 255))
    draw = ImageDraw.Draw(image)
    font = _font(font_size)
    y = 24
    for line in lines:
        draw.text((24, y), line, fill=(0, 0, 0), font=font)
        y += font_size + 10
    image.save(path)


def ensure_fixtures() -> list[Path]:
    files = [
        FIXTURE_ROOT / "invoice_summary.png",
        FIXTURE_ROOT / "tiny_equity_terms.png",
        FIXTURE_ROOT / "rotated_totals.png",
    ]
    if not files[0].exists():
        _write_fixture(
            files[0],
            size=(1200, 700),
            font_size=30,
            lines=[
                "Invoice INV-2048",
                "Subtotal $1,280.00",
                "Tax $102.40",
                "Total $1,382.40",
                "Account 998877",
                "Payment due 2026-05-01",
            ],
        )
    if not files[1].exists():
        _write_fixture(
            files[1],
            size=(1500, 500),
            font_size=20,
            lines=[
                "Pattern day trader threshold 25000",
                "Margin requirement and equity markets",
                "Bond yields and market structure",
                "Volatility, liquidity, execution",
            ],
        )
    if not files[2].exists():
        base = Image.new("RGB", (1200, 500), color=(255, 255, 255))
        draw = ImageDraw.Draw(base)
        font = _font(28)
        y = 60
        for line in ["Total Assets $184,250", "Total Liabilities $92,000", "Shareholder Equity $92,250"]:
            draw.text((40, y), line, fill=(0, 0, 0), font=font)
            y += 56
        rotated = base.rotate(2.5, expand=True, fillcolor=(255, 255, 255))
        files[2].parent.mkdir(parents=True, exist_ok=True)
        rotated.save(files[2])
    return files


if __name__ == "__main__":
    for item in ensure_fixtures():
        print(item)
