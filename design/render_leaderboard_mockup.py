from pathlib import Path
import sys

from PIL import Image, ImageDraw


DESIGN_DIR = Path(__file__).resolve().parent


def round_avatar(source: Path, source_box: tuple[int, int, int, int], size: int) -> Image.Image:
    avatar = Image.open(source).convert("RGB").crop(source_box)
    avatar = avatar.resize((size, size), Image.Resampling.LANCZOS).convert("RGBA")
    scale = 4
    mask = Image.new("L", (size * scale, size * scale), 0)
    ImageDraw.Draw(mask).ellipse((0, 0, size * scale - 1, size * scale - 1), fill=255)
    avatar.putalpha(mask.resize((size, size), Image.Resampling.LANCZOS))
    return avatar


def main() -> None:
    version = sys.argv[1] if len(sys.argv) > 1 else "v1"
    if version not in {"v1", "v2"}:
        raise SystemExit("version must be v1 or v2")

    base_render = Path(
        "/private/tmp/english_reciter_mockup_preview/"
        f"leaderboard-podium-mockup-{version}-wrapper.svg.png"
    )
    output = DESIGN_DIR / f"leaderboard-podium-mockup-{version}.png"
    board = Image.open(base_render).convert("RGBA").crop((0, 400, 1480, 1080))
    ethan = round_avatar(
        DESIGN_DIR / "leaderboard-avatar-ethan.png", (20, 34, 170, 184), 102
    )
    dylan = round_avatar(
        DESIGN_DIR / "leaderboard-avatar-dylan.png", (17, 17, 143, 143), 88
    )
    wanggang = round_avatar(
        DESIGN_DIR / "leaderboard-avatar-wanggang.png", (17, 17, 143, 143), 88
    )

    placements = (
        (
            (ethan, (689, 197)),
            (dylan, (432, 253)),
            (wanggang, (960, 273)),
            (dylan.resize((50, 50), Image.Resampling.LANCZOS), (225, 586)),
            (wanggang.resize((50, 50), Image.Resampling.LANCZOS), (405, 586)),
            (ethan.resize((50, 50), Image.Resampling.LANCZOS), (1025, 586)),
            (dylan.resize((50, 50), Image.Resampling.LANCZOS), (1205, 586)),
        )
        if version == "v1"
        else (
            (ethan, (689, 291)),
            (dylan, (432, 338)),
            (wanggang, (960, 358)),
            (dylan.resize((44, 44), Image.Resampling.LANCZOS), (98, 598)),
            (wanggang.resize((44, 44), Image.Resampling.LANCZOS), (458, 598)),
            (ethan.resize((44, 44), Image.Resampling.LANCZOS), (1158, 598)),
            (wanggang.resize((52, 52), Image.Resampling.LANCZOS), (194, 620)),
            (ethan.resize((52, 52), Image.Resampling.LANCZOS), (1054, 620)),
            (dylan.resize((52, 52), Image.Resampling.LANCZOS), (1414, 620)),
        )
    )

    for avatar, position in placements:
        board.alpha_composite(avatar, position)

    board.convert("RGB").save(output, quality=95)


if __name__ == "__main__":
    main()
