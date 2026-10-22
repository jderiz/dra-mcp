"""Shared helpers for filling AcroForm PDFs and stamping a signature image."""

import os
from io import BytesIO
from pathlib import Path

from PIL import Image, ImageOps
from pypdf import PdfReader, PdfWriter, Transformation
from pypdf.generic import NameObject

SIGNATURE_PADDING = 3


def resolve_signature(sign: bool | None) -> Path | None:
    """Return the signature image to stamp, or None.

    `sign=None` signs only if SIGNATURE_IMAGE points to an existing file,
    `sign=False` never signs, `sign=True` requires a configured image.
    """
    if sign is False:
        return None
    value = os.getenv("SIGNATURE_IMAGE")
    path = Path(value).expanduser() if value else None
    if path is None or not path.is_file():
        if sign:
            raise ValueError("sign=True requires SIGNATURE_IMAGE in .env to point to a PNG/JPG file.")
        return None
    return path


def _signature_page(image_path: Path):
    image = Image.open(image_path)
    if image.mode in ("RGBA", "LA", "P"):
        image = image.convert("RGBA")
        background = Image.new("RGB", image.size, "white")
        background.paste(image, mask=image.split()[-1])
        image = background
    else:
        image = image.convert("RGB")
    content = ImageOps.invert(image.convert("L")).getbbox()
    if content:
        image = image.crop(content)
    buffer = BytesIO()
    image.save(buffer, format="PDF", resolution=72)
    return PdfReader(buffer).pages[0]


def stamp_signature(writer: PdfWriter, field_name: str, image_path: Path) -> None:
    """Draw the image into the widget rectangle of `field_name`.

    The image becomes page content; the signature field itself stays untouched,
    so an electronic signature can still be applied afterwards.
    """
    stamp = _signature_page(image_path)
    for page in writer.pages:
        for annotation in page.get("/Annots") or []:
            annotation = annotation.get_object()
            if annotation.get("/T") != field_name:
                continue
            left, bottom, right, top = (float(value) for value in annotation["/Rect"])
            box_width = right - left - 2 * SIGNATURE_PADDING
            box_height = top - bottom - 2 * SIGNATURE_PADDING
            width, height = float(stamp.mediabox.width), float(stamp.mediabox.height)
            scale = min(box_width / width, box_height / height)
            x = left + SIGNATURE_PADDING + (box_width - width * scale) / 2
            y = bottom + SIGNATURE_PADDING + (box_height - height * scale) / 2
            page.merge_transformed_page(stamp, Transformation().scale(scale).translate(x, y))
            return
    raise ValueError(f"Signature field {field_name!r} not found in the PDF.")


def fill_pdf_form(
    source: str | Path,
    destination: str | Path,
    form_values: dict[str, str | NameObject],
    signature: tuple[str, Path] | None = None,
) -> Path:
    """Write a copy of `source` with `form_values` filled in; never overwrites an existing file.

    Text fields take a str, check boxes and radio groups take the NameObject of the
    export state (or `/Off`). If `destination` exists, `-1`, `-2`, ... is appended.
    """
    source = Path(source).expanduser().resolve()
    destination = Path(destination).expanduser().resolve()
    if source == destination:
        raise ValueError("The output PDF must not overwrite the template.")

    writer = PdfWriter(clone_from=source)
    for page in writer.pages:
        writer.update_page_form_field_values(page, form_values, auto_regenerate=False)
        # pypdf sets /AS on the radio widgets; viewers also read the group value /V.
        for annotation in page.get("/Annots") or []:
            parent = annotation.get_object().get("/Parent")
            if parent is None:
                continue
            parent = parent.get_object()
            value = form_values.get(parent.get("/T"))
            if parent.get("/FT") == "/Btn" and isinstance(value, NameObject):
                parent[NameObject("/V")] = value

    if signature is not None:
        stamp_signature(writer, *signature)

    destination.parent.mkdir(parents=True, exist_ok=True)
    requested_destination = destination
    suffix = 0
    while True:
        try:
            with destination.open("xb") as output_file:
                writer.write(output_file)
            break
        except FileExistsError:
            suffix += 1
            destination = requested_destination.with_name(
                f"{requested_destination.stem}-{suffix}{requested_destination.suffix}"
            )
    return destination
