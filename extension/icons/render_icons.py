from pathlib import Path

from PIL import Image, ImageDraw


HERE = Path(__file__).resolve().parent
SIZES = (16, 32, 48, 128)
SCALE = 8


def points(values, scale):
    return [(round(x * scale), round(y * scale)) for x, y in values]


def render(size: int) -> None:
    scale = size / 128 * SCALE
    canvas_size = size * SCALE
    image = Image.new("RGBA", (canvas_size, canvas_size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)

    # Rounded blue tile with a subtle vertical gradient.
    mask = Image.new("L", image.size, 0)
    mask_draw = ImageDraw.Draw(mask)
    mask_draw.rounded_rectangle(
        (4 * scale, 4 * scale, 124 * scale, 124 * scale),
        radius=28 * scale,
        fill=255,
    )
    gradient = Image.new("RGBA", image.size)
    gradient_pixels = gradient.load()
    top = (57, 121, 246, 255)
    bottom = (23, 73, 198, 255)
    for y in range(canvas_size):
        ratio = y / max(1, canvas_size - 1)
        color = tuple(round(top[index] * (1 - ratio) + bottom[index] * ratio) for index in range(4))
        for x in range(canvas_size):
            gradient_pixels[x, y] = color
    image.alpha_composite(Image.composite(gradient, Image.new("RGBA", image.size), mask))
    draw = ImageDraw.Draw(image)

    # Play symbol.
    draw.polygon(points(((42, 27), (42, 89), (93, 58)), scale), fill="white")

    # Green download badge and arrow.
    draw.ellipse(
        (68 * scale, 68 * scale, 122 * scale, 122 * scale),
        fill="#10B981",
        outline="white",
        width=max(1, round(6 * scale)),
    )
    arrow_width = max(1, round(7 * scale))
    draw.line(points(((95, 78), (95, 102)), scale), fill="white", width=arrow_width)
    draw.line(points(((84, 91), (95, 102), (106, 91)), scale), fill="white", width=arrow_width, joint="curve")

    image = image.resize((size, size), Image.Resampling.LANCZOS)
    image.save(HERE / f"icon{size}.png", optimize=True)


for icon_size in SIZES:
    render(icon_size)
