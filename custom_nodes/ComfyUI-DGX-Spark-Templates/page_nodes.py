"""Printable A4 pages.

Markdown to Pages lays out text as A4 pages at 300 dpi in DejaVu Sans, black on
white with wide margins, and saves them as PNG files plus one PDF. It reads the
part of Markdown that printed sheets need: # and ## headings, paragraphs,
bullet and numbered lists, pipe tables, **bold**, --- as a dashed cut line and
<!-- page --> as a page break. Tables get a bold header row on light grey, thin
rules and wrapped cells, and repeat the header on every page they continue on.

DejaVu Sans covers Latin, Greek, Cyrillic, Hebrew and Arabic script. Pillow
shapes and orders right-to-left text through raqm, so a paragraph or cell that
starts in Hebrew, Arabic or Persian is set right-aligned. Chinese, Japanese,
Korean, Thai and Ethiopic have no glyphs in the font.

Picture Page puts a list of pictures and their words on A4 pages: a picture
dictionary, a writing worksheet, a matching exercise, flashcards for double-sided
printing, bingo boards with a caller sheet, memory cards, captioned pictures or
comic panels. Each picture is cropped to its subject and an even, light
background is turned white, so pictures stand on the page as in a printed
picture dictionary. The words come from the list, never from the pictures.

The PDF is written here rather than by Pillow, which stores every page as JPEG:
text pages are zlib-compressed and stay sharp in print, and only pages with
pictures are stored as JPEG, at quality 92.
"""

import io
import math
import os
import random
import re
import unicodedata
import zlib

import numpy as np
from PIL import Image, ImageDraw, ImageFont, features

import folder_paths

DPI = 300
A4 = (2480, 3508)
MARGIN = 236  # 20 mm
FONTS = {False: "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
         True: "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"}
BODY_POINTS = {"normal": 11, "large": 16}
HEADER_FILL = (232, 232, 232)
RULE = (110, 110, 110)

_BOLD = re.compile(r"\*\*(.+?)\*\*")
_EMPHASIS = re.compile(r"(?<![\w*])[*_](?=\S)(.+?)(?<=\S)[*_](?![\w*])")
_HEADING = re.compile(r"(#{1,6})\s+(.*?)\s*#*$")
_ITEM = re.compile(r"([-*+]|\d{1,3}[.)])\s+(.*)")
_CUT = re.compile(r"(?:-\s*){3,}|(?:_\s*){3,}|(?:\*\s*){3,}")
_PAGE = re.compile(r"<!--\s*page\s*-->", re.I)
_TABLE_RULE = re.compile(r"\|?\s*:?-{2,}:?\s*(?:\|\s*:?-{2,}:?\s*)*\|?")
_RTL = {"direction": "rtl"} if features.check("raqm") else {}
_fonts = {}


def points(size):
    return round(size * DPI / 72)


def font(size, bold=False):
    if (size, bold) not in _fonts:
        _fonts[(size, bold)] = ImageFont.truetype(FONTS[bold], size)
    return _fonts[(size, bold)]


def is_rtl(text):
    """True when the first letter with a strong direction is right-to-left."""
    for ch in text:
        kind = unicodedata.bidirectional(ch)
        if kind in ("R", "AL"):
            return True
        if kind == "L":
            return False
    return False


def plain(text):
    """Inline Markdown to plain text: emphasis, code marks and escapes removed."""
    text = _EMPHASIS.sub(r"\1", _BOLD.sub(r"\1", text))
    return text.replace("`", "").replace("\\|", "|").replace("\\*", "*")


def runs(text):
    """Inline Markdown to [(text, bold)]."""
    out, last = [], 0
    for match in _BOLD.finditer(text):
        if match.start() > last:
            out.append((plain(text[last:match.start()]), False))
        out.append((plain(match.group(1)), True))
        last = match.end()
    if last < len(text):
        out.append((plain(text[last:]), False))
    return out


def words(text, bold=False):
    """[(word, bold, space_before)] from inline Markdown."""
    out, space = [], False
    for chunk, chunk_bold in runs(text):
        for piece in re.split(r"(\s+)", chunk):
            if not piece:
                continue
            if piece.isspace():
                space = True
                continue
            out.append((piece, bold or chunk_bold, space and bool(out)))
            space = False
    return out


def wrap(items, width, size):
    """Greedy line breaking of words() output into lines of (word, bold, x)."""
    space = font(size).getlength(" ")
    lines, line, x = [], [], 0.0
    for word, bold, gap in items:
        face = font(size, bold)
        w = face.getlength(word)
        start = x + space if gap and line else x
        if line and start + w > width:
            lines.append(line)
            line, start = [], 0.0
        while w > width - start and len(word) > 1:  # wider than the column: cut it
            cut = len(word) - 1
            while cut > 1 and face.getlength(word[:cut]) > width - start:
                cut -= 1
            line.append((word[:cut], bold, start))
            lines.append(line)
            line, start = [], 0.0
            word = word[cut:]
            w = face.getlength(word)
        line.append((word, bold, start))
        x = start + w
    if line:
        lines.append(line)
    return lines


def wrap_plain(text, width, face):
    """Line breaking measured on whole lines, for right-to-left text that raqm shapes as a unit."""
    lines, line = [], ""
    for word in text.split():
        candidate = f"{line} {word}" if line else word
        if line and face.getlength(candidate) > width:
            lines.append(line)
            candidate = word
        line = candidate
    if line:
        lines.append(line)
    return lines


def cells(line):
    """A pipe table row to stripped cells; `\\|` stays inside its cell."""
    line = line.strip()
    if line.startswith("|"):
        line = line[1:]
    if line.endswith("|") and not line.endswith("\\|"):
        line = line[:-1]
    return [cell.strip() for cell in re.split(r"(?<!\\)\|", line)]


def _starts_block(line):
    return bool(_HEADING.fullmatch(line) or _CUT.fullmatch(line) or _PAGE.fullmatch(line) or line.startswith("|"))


def parse(markdown):
    """Markdown to blocks: (kind, payload) with kind in h1, h2, h3, p, list, table, cut, page."""
    blocks, paragraph = [], []
    lines = markdown.replace("\r\n", "\n").split("\n")

    def flush():
        if paragraph:
            blocks.append(("p", " ".join(paragraph)))
            paragraph.clear()

    i = 0
    while i < len(lines):
        line = lines[i].strip()
        following = lines[i + 1].strip() if i + 1 < len(lines) else ""
        if not line:
            flush()
        elif _PAGE.fullmatch(line):
            flush()
            blocks.append(("page", None))
        elif line.startswith("<!--"):
            flush()
            while "-->" not in lines[i] and i + 1 < len(lines):
                i += 1
        elif _CUT.fullmatch(line):
            flush()
            blocks.append(("cut", None))
        elif _HEADING.fullmatch(line):
            flush()
            level, text = _HEADING.fullmatch(line).groups()
            blocks.append((f"h{min(len(level), 3)}", text))
        elif "|" in line and (_TABLE_RULE.fullmatch(following) or line.startswith("|") and following.startswith("|")):
            flush()
            header = cells(line)
            i += 1 if not _TABLE_RULE.fullmatch(following) else 2
            rows = []
            while i < len(lines) and "|" in lines[i] and lines[i].strip():
                rows.append(cells(lines[i]))
                i += 1
            blocks.append(("table", (header, rows)))
            continue
        elif _ITEM.fullmatch(line):
            flush()
            items = []
            while i < len(lines) and lines[i].strip() and not _starts_block(lines[i].strip()):
                match = _ITEM.fullmatch(lines[i].strip())
                if match:
                    marker = "•" if match.group(1) in "-*+" else match.group(1)
                    items.append([marker, match.group(2)])
                elif items:
                    items[-1][1] += " " + lines[i].strip()
                i += 1
            blocks.append(("list", items))
            continue
        else:
            paragraph.append(line)
        i += 1
    flush()
    return blocks


class Sheet:
    """A4 pages filled from the top, a new one started when the next block does not fit."""

    def __init__(self, landscape=False, body_points=11):
        self.size = (A4[1], A4[0]) if landscape else A4
        self.body = points(body_points)
        self.line = round(self.body * 1.45)
        self.left, self.right = MARGIN, self.size[0] - MARGIN
        self.bottom = self.size[1] - MARGIN
        self.pages = []
        self.new_page()

    def new_page(self):
        self.page = Image.new("RGB", self.size, "white")
        self.draw = ImageDraw.Draw(self.page)
        self.pages.append(self.page)
        self.y = MARGIN

    def fit(self, height):
        """Start a new page unless height fits on this one; an empty page always takes it."""
        if self.y + height > self.bottom and self.y > MARGIN:
            self.new_page()

    def space(self, height):
        if self.y > MARGIN:
            self.y += height

    def text(self, text, size, bold=False, indent=0, marker=None, keep_with_next=0):
        """A paragraph from inline Markdown, wrapped to the column; marker hangs in the indent."""
        line_height = round(size * 1.45)
        rtl = is_rtl(plain(text))
        left, right = (self.left, self.right - indent) if rtl else (self.left + indent, self.right)
        face = font(size, bold)
        lines = wrap_plain(plain(text), right - left, face) if rtl else wrap(words(text, bold), right - left, size)
        for k, line in enumerate(lines or [[]]):
            self.fit(line_height + (keep_with_next if k == 0 else 0))
            if k == 0 and marker:
                gap = round(size * 0.2)
                x = self.right - gap - face.getlength(marker) if rtl else self.left + gap
                self.draw.text((x, self.y), marker, font=face, fill="black")
            self.draw_line(line, left, right, self.y, size, bold)
            self.y += line_height

    def draw_line(self, line, left, right, y, size, bold):
        """A wrapped line: right-to-left text as one right-aligned string, otherwise word by word."""
        if isinstance(line, str):
            face = font(size, bold)
            self.draw.text((right - face.getlength(line), y), line, font=face, fill="black", **_RTL)
            return
        for word, word_bold, x in line:
            self.draw.text((left + x, y), word, font=font(size, word_bold), fill="black")

    def heading(self, text, level):
        size = round(self.body * {1: 1.8, 2: 1.35, 3: 1.0}[level])
        self.space(round(self.line * (0.9 if level == 1 else 0.6)))
        self.text(text, size, bold=True, keep_with_next=self.line * 2)
        self.y += round(self.line * 0.25)

    def items(self, items):
        indent = round(self.body * 1.6)
        for marker, text in items:
            self.text(text, self.body, indent=indent, marker=marker)
            self.y += round(self.line * 0.15)

    def cut(self):
        self.fit(self.line)
        y = self.y + self.line // 2
        dash, gap, x = points(4), points(3), self.left
        while x < self.right:
            self.draw.line([(x, y), (min(x + dash, self.right), y)], fill="black", width=3)
            x += dash + gap
        self.y += round(self.line * 1.25)

    def column_widths(self, header, rows, pad):
        """Natural widths when they fit; otherwise each column keeps its longest word and shares the rest."""
        available = self.right - self.left
        natural, least = [], []
        for c, head in enumerate(header):
            texts = [(head, True)] + [(row[c], False) for row in rows]
            # two pixels of slack, so rounding never leaves a word a fraction too wide for its column
            natural.append(math.ceil(max(font(self.body, b).getlength(plain(t)) for t, b in texts)) + 2 * pad + 2)
            longest = max((font(self.body, b).getlength(w) for t, b in texts for w in plain(t).split()), default=0)
            least.append(min(math.ceil(longest) + 2 * pad + 2, natural[-1]))
        if sum(natural) <= available:
            widths = [n * available / sum(natural) for n in natural]
        elif sum(least) >= available:
            widths = [n * available / sum(least) for n in least]
        else:
            spare, want = available - sum(least), [n - m for n, m in zip(natural, least)]
            widths = [m + spare * w / sum(want) for m, w in zip(least, want)]
        widths = [max(int(w), m) if sum(least) < available else int(w) for w, m in zip(widths, least)]
        widths[widths.index(max(widths))] += available - sum(widths)
        return widths

    def table(self, header, rows, rows_per_page=0):
        columns = max([len(header)] + [len(row) for row in rows])
        header = header + [""] * (columns - len(header))
        rows = [row + [""] * (columns - len(row)) for row in rows]
        pad = round(self.body * 0.45)
        line_height = round(self.body * 1.35)
        widths = self.column_widths(header, rows, pad)

        def lay(row, bold):
            laid = []
            for text, width in zip(row, widths):
                if is_rtl(plain(text)):
                    laid.append(wrap_plain(plain(text), width - 2 * pad, font(self.body, bold)))
                else:
                    laid.append(wrap(words(text, bold), width - 2 * pad, self.body))
            return laid, max(1, *(len(cell) for cell in laid)) * line_height + 2 * pad

        def draw(laid, height, bold):
            x = self.left
            for cell, width in zip(laid, widths):
                if bold:
                    self.draw.rectangle([x, self.y, x + width, self.y + height], fill=HEADER_FILL)
                for k, line in enumerate(cell):
                    self.draw_line(line, x + pad, x + width - pad, self.y + pad + k * line_height, self.body, bold)
                self.draw.rectangle([x, self.y, x + width, self.y + height], outline=RULE, width=2)
                x += width
            self.y += height

        head, head_height = lay(header, True)
        first = lay(rows[0], False)[1] if rows else 0
        self.fit(head_height + first)
        draw(head, head_height, True)
        on_page = 0
        for row in rows:
            laid, height = lay(row, False)
            if (rows_per_page and on_page >= rows_per_page) or self.y + height > self.bottom:
                self.new_page()
                draw(head, head_height, True)
                on_page = 0
            draw(laid, height, False)
            on_page += 1

    def markdown(self, text, rows_per_page=0):
        for kind, payload in parse(text):
            if kind in ("h1", "h2", "h3"):
                self.heading(payload, int(kind[1]))
            elif kind == "p":
                self.text(payload, self.body)
                self.y += round(self.line * 0.45)
            elif kind == "list":
                self.items(payload)
                self.y += round(self.line * 0.3)
            elif kind == "table":
                self.space(round(self.line * 0.2))
                self.table(*payload, rows_per_page=rows_per_page)
                self.y += round(self.line * 0.6)
            elif kind == "cut":
                self.cut()
            elif kind == "page" and self.y > MARGIN:
                self.new_page()

    def finish(self):
        """The pages, numbered at the foot when there is more than one."""
        if len(self.pages) > 1:
            face = font(round(self.body * 0.8))
            for number, page in enumerate(self.pages, 1):
                label = f"{number} / {len(self.pages)}"
                ImageDraw.Draw(page).text(((self.size[0] - face.getlength(label)) / 2, self.size[1] - MARGIN * 0.6),
                                          label, font=face, fill=(90, 90, 90))
        return self.pages


def write_pdf(path, pages, dpi=DPI):
    """A PDF with one page per image at its print size: greyscale pages zlib-compressed, colour pages as JPEG."""
    objects = [b"", b""]  # catalog and page tree, filled in last
    kids = []
    for page in pages:
        if page.mode not in ("L", "RGB"):
            page = page.convert("RGB")
        if page.mode == "L":
            data, colours, codec = zlib.compress(page.tobytes(), 6), b"DeviceGray", b"FlateDecode"
        else:
            buffer = io.BytesIO()
            page.save(buffer, "JPEG", quality=92, subsampling=0)
            data, colours, codec = buffer.getvalue(), b"DeviceRGB", b"DCTDecode"
        objects.append(b"<< /Type /XObject /Subtype /Image /Width %d /Height %d /ColorSpace /%s "
                       b"/BitsPerComponent 8 /Filter /%s /Length %d >>\nstream\n%s\nendstream"
                       % (page.width, page.height, colours, codec, len(data), data))
        image = len(objects)
        width, height = page.width * 72 / dpi, page.height * 72 / dpi
        content = b"q %.2f 0 0 %.2f 0 0 cm /Im0 Do Q" % (width, height)
        objects.append(b"<< /Length %d >>\nstream\n%s\nendstream" % (len(content), content))
        objects.append(b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 %.2f %.2f] "
                       b"/Resources << /XObject << /Im0 %d 0 R >> >> /Contents %d 0 R >>"
                       % (width, height, image, image + 1))
        kids.append(len(objects))
    objects[0] = b"<< /Type /Catalog /Pages 2 0 R >>"
    objects[1] = b"<< /Type /Pages /Kids [%s] /Count %d >>" % (b" ".join(b"%d 0 R" % k for k in kids), len(kids))
    out = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = []
    for number, body in enumerate(objects, 1):
        offsets.append(len(out))
        out += b"%d 0 obj\n%s\nendobj\n" % (number, body)
    start = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1)
    out += b"".join(b"%010d 00000 n \n" % offset for offset in offsets)
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objects) + 1, start)
    with open(path, "wb") as handle:
        handle.write(out)


def save_pages(pages, filename_prefix, grey=False):
    """PNG per page plus one PDF in the output folder; returns the UI image list and the PDF's UI entry."""
    folder, name, counter, subfolder, _ = folder_paths.get_save_image_path(
        filename_prefix, folder_paths.get_output_directory(), pages[0].width, pages[0].height)
    if grey:
        pages = [page.convert("L") for page in pages]
    images = []
    for number, page in enumerate(pages, 1):
        file = f"{name}_{counter:05}_{number:02}.png"
        page.save(os.path.join(folder, file), compress_level=6)
        images.append({"filename": file, "subfolder": subfolder, "type": "output"})
    pdf = f"{name}_{counter:05}_.pdf"
    write_pdf(os.path.join(folder, pdf), pages)
    return images, {"filename": pdf, "subfolder": subfolder, "type": "output"}


class MarkdownToPages:
    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "markdown": ("STRING", {"multiline": True, "default": "# Title\n\nText, **bold**, a list:\n\n- one\n"
                                                                     "- two\n\n| A | B |\n| --- | --- |\n| 1 | 2 |"}),
                "orientation": (["portrait", "landscape"],),
                "text_size": (list(BODY_POINTS), {"tooltip": "large sets the body text at 16 pt instead of 11."}),
                "table_rows_per_page": ("INT", {"default": 0, "min": 0, "max": 200,
                                                "tooltip": "0 fits as many rows as a page holds."}),
                "filename_prefix": ("STRING", {"default": "pages/page"}),
            },
        }

    RETURN_TYPES = ("STRING",)
    RETURN_NAMES = ("pdf",)
    OUTPUT_NODE = True
    FUNCTION = "render"
    CATEGORY = "image/text"
    DESCRIPTION = ("Lays out Markdown as A4 pages at 300 dpi and saves them as PNG files plus one PDF. Reads # and "
                   "## headings, paragraphs, lists, pipe tables, **bold**, --- (a dashed cut line) and "
                   "<!-- page --> (a page break). pdf is the PDF's path inside the output folder; the "
                   "browser downloads the PDF when the node finishes.")

    def render(self, markdown, orientation, text_size, table_rows_per_page, filename_prefix):
        sheet = Sheet(orientation == "landscape", BODY_POINTS[text_size])
        sheet.markdown(markdown, table_rows_per_page)
        images, pdf = save_pages(sheet.finish(), filename_prefix, grey=True)
        path = os.path.join(pdf["subfolder"], pdf["filename"])
        print(f"[pages] {len(images)} page(s), {path}")
        return {"ui": {"images": images, "pdf": [pdf]}, "result": (path,)}


# ------------------------------------------------------------------ picture pages

LAYOUTS = ("dictionary", "write", "matching", "flashcards", "bingo", "memory", "captions", "comic")
GRID_INK = (60, 60, 60)
SECOND = (80, 80, 80)


def auto_grid(count):
    """(columns, rows) for count pictures on a portrait page, the way picture dictionaries set them."""
    for limit, grid in ((1, (1, 1)), (2, (2, 1)), (4, (2, 2)), (6, (2, 3)), (9, (3, 3)), (12, (3, 4)),
                        (15, (3, 5)), (16, (4, 4)), (20, (4, 5)), (24, (4, 6)), (25, (5, 5))):
        if count <= limit:
            return grid
    return (5, 6)


def cutout(image):
    """The subject of a picture, cropped to it; an even, light background is turned white."""
    rgb = np.asarray(image.convert("RGB")).astype(np.float32)
    border = np.concatenate([rgb[0], rgb[-1], rgb[:, 0], rgb[:, -1]])
    background = np.median(border, axis=0)
    even = np.percentile(np.abs(border - background).max(axis=1), 90) < 24
    if not even:
        return image.convert("RGB")
    difference = np.abs(rgb - background).max(axis=2)
    if background.min() > 190:
        alpha = np.clip((difference - 10.0) / 30.0, 0.0, 1.0)[..., None]
        rgb = rgb * alpha + 255.0 * (1.0 - alpha)
    picture = Image.fromarray(rgb.clip(0, 255).astype(np.uint8))
    ys, xs = np.nonzero(difference > 28)
    if not len(ys):
        return picture
    pad = int(0.03 * max(picture.size))
    box = (max(0, xs.min() - pad), max(0, ys.min() - pad), min(picture.width, xs.max() + pad + 1),
           min(picture.height, ys.max() + pad + 1))
    return picture.crop(box)


def fit(picture, width, height):
    scale = min(width / picture.width, height / picture.height)
    size = (max(1, int(picture.width * scale)), max(1, int(picture.height * scale)))
    return picture.resize(size, Image.LANCZOS)


class _Cell:
    """Drawing helpers shared by the picture layouts."""

    def __init__(self, page, size):
        self.page = page
        self.draw = ImageDraw.Draw(page)
        self.size = size  # label size in pixels

    def picture(self, picture, box, align="bottom"):
        x0, y0, x1, y1 = box
        if x1 - x0 < 4 or y1 - y0 < 4:
            return
        fitted = fit(picture, x1 - x0, y1 - y0)
        x = x0 + (x1 - x0 - fitted.width) // 2
        y = y1 - fitted.height if align == "bottom" else y0 + (y1 - y0 - fitted.height) // 2
        self.page.paste(fitted, (x, y))

    def label_font(self, text, width, size, bold=True):
        while size > 18 and font(size, bold).getlength(text) > width:
            size -= 2
        return font(size, bold)

    def text(self, text, centre_x, top, width, size, bold=True, fill="black", max_lines=2):
        """Centred text, shrunk to fit width and wrapped if it still does not; returns the height used."""
        if not text:
            return 0
        face = self.label_font(text, width, size, bold)
        lines = [text] if face.getlength(text) <= width else wrap_plain(text, width, face)[:max_lines]
        height = round(face.size * 1.25)
        for k, line in enumerate(lines):
            extra = _RTL if is_rtl(line) else {}
            self.draw.text((centre_x, top + k * height), line, font=face, fill=fill, anchor="ma", **extra)
        return height * len(lines)

    def number(self, number, x, y):
        face = font(round(self.size * 0.7), True)
        self.draw.text((x, y), str(number), font=face, fill=GRID_INK)


def _new_page():
    page = Image.new("RGB", A4, "white")
    return page


def _title(page, title, size):
    """Draws the page title; returns the y where the content starts."""
    if not title:
        return MARGIN
    face = font(round(size * 2.2), True)
    while face.size > 40 and face.getlength(title) > A4[0] - 2 * MARGIN:
        face = font(face.size - 4, True)
    ImageDraw.Draw(page).text((A4[0] / 2, MARGIN), title, font=face, fill="black", anchor="ma",
                              **(_RTL if is_rtl(title) else {}))
    return MARGIN + round(face.size * 1.6)


def _dashed_grid(draw, left, top, width, height, columns, rows):
    dash, gap = points(3), points(2)
    for c in range(columns + 1):
        x = left + round(c * width / columns)
        for y in range(top, top + height, dash + gap):
            draw.line([(x, y), (x, min(y + dash, top + height))], fill=GRID_INK, width=2)
    for r in range(rows + 1):
        y = top + round(r * height / rows)
        for x in range(left, left + width, dash + gap):
            draw.line([(x, y), (min(x + dash, left + width), y)], fill=GRID_INK, width=2)


def _cells(left, top, width, height, columns, rows):
    for r in range(rows):
        for c in range(columns):
            yield (left + round(c * width / columns), top + round(r * height / rows),
                   left + round((c + 1) * width / columns), top + round((r + 1) * height / rows))


def _labelled_grid(items, title, columns, rows, size, numbers, mode):
    """dictionary, write and captions: a picture per cell with its word, a line, or a sentence below it."""
    pages, per_page = [], columns * rows
    for start in range(0, len(items), per_page):
        page = _new_page()
        cell = _Cell(page, size)
        top = _title(page, title, size)
        chunk = items[start:start + per_page]
        width, height = A4[0] - 2 * MARGIN, A4[1] - MARGIN - top
        for index, ((picture, label, second), box) in enumerate(zip(chunk, _cells(MARGIN, top, width, height,
                                                                               columns, rows)), start + 1):
            x0, y0, x1, y1 = box
            pad = round((x1 - x0) * 0.06)
            text_width = x1 - x0 - 2 * pad
            if mode == "captions":
                caption_height = round(size * 1.25) * 3 + pad
            else:
                caption_height = round(size * 1.25) * (2 if second else 1) + pad
            cell.picture(picture, (x0 + pad, y0 + pad, x1 - pad, y1 - caption_height - pad))
            y = y1 - caption_height
            if mode == "write":
                line_y = y + round(size * 1.05)
                cell.draw.line([(x0 + pad, line_y), (x1 - pad, line_y)], fill="black", width=3)
                if second:
                    cell.text(second, (x0 + x1) / 2, line_y + round(size * 0.2), text_width, round(size * 0.75),
                              bold=False, fill=SECOND, max_lines=1)
            elif mode == "captions":
                cell.text(label, (x0 + x1) / 2, y, text_width, round(size * 0.85), bold=False, max_lines=3)
            else:
                used = cell.text(label, (x0 + x1) / 2, y, text_width, size)
                if second:
                    cell.text(second, (x0 + x1) / 2, y + used, text_width, round(size * 0.75), bold=False,
                              fill=SECOND, max_lines=1)
            if numbers:
                cell.number(index, x0 + pad // 2, y0 + pad // 2)
        pages.append(page)
    return pages


def _matching(items, title, columns, rows, size, rng):
    """Numbered pictures above, the same words shuffled in a bank below, each with a box for its number."""
    pages, per_page = [], columns * rows
    for start in range(0, len(items), per_page):
        chunk = items[start:start + per_page]
        page = _new_page()
        cell = _Cell(page, size)
        top = _title(page, title, size)
        bank_rows = math.ceil(len(chunk) / 4)
        bank_height = bank_rows * round(size * 2.1) + MARGIN // 2
        width, height = A4[0] - 2 * MARGIN, A4[1] - MARGIN - top - bank_height
        for index, ((picture, _, _), box) in enumerate(zip(chunk, _cells(MARGIN, top, width, height,
                                                                         columns, rows)), 1):
            x0, y0, x1, y1 = box
            pad = round((x1 - x0) * 0.06)
            cell.picture(picture, (x0 + pad, y0 + pad + size, x1 - pad, y1 - pad), align="centre")
            cell.number(index, x0 + pad // 2, y0 + pad // 2)
        words = [label for _, label, _ in chunk]
        rng.shuffle(words)
        bank_top = A4[1] - MARGIN - bank_height + MARGIN // 2
        cell.draw.line([(MARGIN, bank_top - MARGIN // 4), (A4[0] - MARGIN, bank_top - MARGIN // 4)], fill=GRID_INK,
                       width=2)
        for k, word in enumerate(words):
            column, row = k % 4, k // 4
            x0 = MARGIN + round(column * width / 4)
            y = bank_top + row * round(size * 2.1)
            box_size = round(size * 1.2)
            cell.draw.rectangle([x0, y, x0 + box_size, y + box_size], outline="black", width=3)
            face = cell.label_font(word, width / 4 - box_size - size, size, bold=False)
            cell.draw.text((x0 + box_size + round(size * 0.4), y + box_size / 2), word, font=face, fill="black",
                           anchor="lm", **(_RTL if is_rtl(word) else {}))
        pages.append(page)
    return pages


def _cards(cards, size, columns=3, rows=4, mirror_backs=None):
    """Cards on cut-line grids. cards: list of draw functions (cell, box). With mirror_backs, every front
    page is followed by a back page whose columns are mirrored for long-edge double-sided printing."""
    pages, per_page = [], columns * rows
    width, height = A4[0] - 2 * MARGIN, A4[1] - 2 * MARGIN
    for start in range(0, len(cards), per_page):
        chunk = cards[start:start + per_page]
        sides = [chunk] if mirror_backs is None else [chunk, mirror_backs[start:start + per_page]]
        for side, side_cards in enumerate(sides):
            page = _new_page()
            cell = _Cell(page, size)
            boxes = list(_cells(MARGIN, MARGIN, width, height, columns, rows))
            for k, draw_card in enumerate(side_cards):
                column, row = k % columns, k // columns
                if side == 1:
                    column = columns - 1 - column
                draw_card(cell, boxes[row * columns + column])
            _dashed_grid(cell.draw, MARGIN, MARGIN, width, height, columns, rows)
            pages.append(page)
    return pages


def _picture_card(picture):
    def draw(cell, box):
        x0, y0, x1, y1 = box
        pad = round((x1 - x0) * 0.1)
        cell.picture(picture, (x0 + pad, y0 + pad, x1 - pad, y1 - pad), align="centre")
    return draw


def _word_card(label, second, size):
    def draw(cell, box):
        x0, y0, x1, y1 = box
        pad = round((x1 - x0) * 0.08)
        height = round(size * 1.3) * (2 if second else 1)
        y = (y0 + y1) / 2 - height / 2
        used = cell.text(label, (x0 + x1) / 2, y, x1 - x0 - 2 * pad, round(size * 1.3))
        if second:
            cell.text(second, (x0 + x1) / 2, y + used, x1 - x0 - 2 * pad, size, bold=False, fill=SECOND, max_lines=1)
    return draw


def _bingo(items, title, boards, grid, size, rng):
    """boards different bingo boards of grid x grid pictures, then a caller sheet with every word."""
    cells = grid * grid
    free = len(items) == cells - 1 and grid % 2 == 1
    if len(items) < cells and not free:
        raise ValueError(f"A {grid}x{grid} bingo board needs at least {cells} pictures; the list has {len(items)}. "
                         "Add words or pick a smaller board.")
    seen, layouts = set(), []
    for _ in range(boards * 50):
        if len(layouts) == boards:
            break
        chosen = rng.sample(range(len(items)), cells - (1 if free else 0))
        if tuple(chosen) not in seen:
            seen.add(tuple(chosen))
            layouts.append(chosen)
    pages = []
    side = min(A4[0] - 2 * MARGIN, A4[1] - 3 * MARGIN)
    for number, chosen in enumerate(layouts, 1):
        page = _new_page()
        cell = _Cell(page, size)
        top = _title(page, title or "BINGO", size)
        left = (A4[0] - side) // 2
        cell.number(number, A4[0] - MARGIN - size, MARGIN)
        order = list(chosen)
        if free:
            order.insert(cells // 2, None)
        for item, box in zip(order, _cells(left, top, side, side, grid, grid)):
            x0, y0, x1, y1 = box
            cell.draw.rectangle(box, outline="black", width=4)
            if item is None:
                cell.text("★", (x0 + x1) / 2, (y0 + y1) / 2 - size, x1 - x0, round(size * 2))
                continue
            pad = round((x1 - x0) * 0.1)
            cell.picture(items[item][0], (x0 + pad, y0 + pad, x1 - pad, y1 - pad), align="centre")
        pages.append(page)
    columns, rows = auto_grid(len(items))
    return pages + _labelled_grid(items, f"{title or 'Bingo'}: caller sheet", columns, rows, round(size * 0.8),
                                  False, "dictionary")


def _comic(items, title, size, numbers):
    """Panels two to a row, each with its caption in a box below."""
    pages, per_page = [], 6
    for start in range(0, len(items), per_page):
        page = _new_page()
        cell = _Cell(page, size)
        top = _title(page, title, size)
        width, height = A4[0] - 2 * MARGIN, A4[1] - MARGIN - top
        for index, ((picture, label, _), box) in enumerate(zip(items[start:start + per_page],
                                                              _cells(MARGIN, top, width, height, 2, 3)), start + 1):
            x0, y0, x1, y1 = box
            pad = round(size * 0.5)
            caption_height = round(size * 1.15) * 3 + pad
            frame = (x0 + pad, y0 + pad, x1 - pad, y1 - pad - caption_height)
            fitted = fit(picture, frame[2] - frame[0], frame[3] - frame[1])
            px = frame[0] + (frame[2] - frame[0] - fitted.width) // 2
            page.paste(fitted, (px, frame[1]))
            cell.draw.rectangle([px, frame[1], px + fitted.width, frame[1] + fitted.height], outline="black", width=5)
            cell.text(label, (x0 + x1) / 2, frame[1] + fitted.height + pad // 2, x1 - x0 - 2 * pad,
                      round(size * 0.85), bold=False, max_lines=3)
            if numbers:
                cell.number(index, px + pad // 2, frame[1] + pad // 2)
        pages.append(page)
    return pages


def picture_pages(pictures, labels, seconds, layout, title="", columns=None, numbers=False, bingo_boards=4,
                  bingo_grid=4, text_size="normal", seed=0):
    """A4 pages for one of LAYOUTS. pictures: PIL images; labels and seconds: strings, seconds may be empty."""
    items = [(cutout(p), label.strip(), (second or "").strip()) for p, label, second in zip(pictures, labels, seconds)]
    size = round(points(BODY_POINTS[text_size]) * 1.5)
    rng = random.Random(seed)
    count = len(items)
    if layout in ("dictionary", "write", "captions"):
        grid = auto_grid(count) if not columns else (columns, min(6, math.ceil(count / columns)))
        if layout == "captions" and not columns:
            grid = (2, min(3, math.ceil(count / 2)))
        return _labelled_grid(items, title, *grid, round(size * (0.9 if layout == "captions" else 1)), numbers,
                              layout), grid
    if layout == "matching":
        grid = auto_grid(min(count, 20)) if not columns else (columns, min(5, math.ceil(min(count, 20) / columns)))
        return _matching(items, title, *grid, round(size * 0.9), rng), grid
    if layout == "flashcards":
        fronts = [_picture_card(p) for p, _, _ in items]
        backs = [_word_card(label, second, size) for _, label, second in items]
        return _cards(fronts, size, mirror_backs=backs), (3, 4)
    if layout == "memory":
        cards = [_picture_card(p) for p, _, _ in items] + [_word_card(l, s, round(size * 0.8)) for _, l, s in items]
        rng.shuffle(cards)
        return _cards(cards, size, columns=4, rows=5), (4, 5)
    if layout == "bingo":
        return _bingo(items, title, bingo_boards, bingo_grid, size, rng), (bingo_grid, bingo_grid)
    if layout == "comic":
        return _comic(items, title, size, numbers), (2, 3)
    raise ValueError(f"Unknown layout {layout!r}")


class PicturePage:
    INPUT_IS_LIST = True

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "layout": (list(LAYOUTS), {"tooltip": "dictionary: picture and word. write: picture and a line to "
                                                      "write on. matching: numbered pictures and the words "
                                                      "shuffled. flashcards: pictures with the words on the back, "
                                                      "for long-edge double-sided printing. bingo: different "
                                                      "boards and a caller sheet. memory: a picture card and a "
                                                      "word card for each word. captions: picture and sentence. "
                                                      "comic: panels with captions."}),
                "title": ("STRING", {"default": ""}),
                "columns": (["auto", "2", "3", "4", "5", "6"], {"default": "auto"}),
                "numbers": ("BOOLEAN", {"default": False}),
                "bingo_boards": ("INT", {"default": 4, "min": 1, "max": 40}),
                "bingo_grid": (["4x4", "3x3", "5x5"], {"default": "4x4"}),
                "text_size": (list(BODY_POINTS), {"default": "normal"}),
                "seed": ("INT", {"default": 0, "min": 0, "max": 0xFFFFFFFF,
                                 "tooltip": "Sets the shuffle of matching, memory and bingo."}),
                "filename_prefix": ("STRING", {"default": "pages/pictures"}),
                # socket-only inputs after the widgets: workflows store widget values by position
                "images": ("IMAGE",),
                "labels": ("STRING", {"forceInput": True}),
            },
            "optional": {
                "second_labels": ("STRING", {"forceInput": True,
                                             "tooltip": "A second line under each word, such as a translation."}),
            },
        }

    RETURN_TYPES = ("STRING",)
    RETURN_NAMES = ("pdf",)
    OUTPUT_NODE = True
    FUNCTION = "render"
    CATEGORY = "image/text"
    DESCRIPTION = ("Puts a list of pictures and their words on A4 pages and saves them as PNG files plus one PDF: "
                   "a picture dictionary, a worksheet, a matching exercise, flashcards, bingo, memory cards, "
                   "captions or a comic. The words come from the list, never from the pictures.")

    def render(self, layout, title, columns, numbers, bingo_boards, bingo_grid, text_size, seed, filename_prefix,
               images, labels, second_labels=None):
        pictures = []
        for batch in images:
            for frame in batch:
                pictures.append(Image.fromarray(np.clip(frame.cpu().numpy() * 255.0, 0, 255).astype(np.uint8)))
        if len(pictures) != len(labels):
            raise ValueError(f"Got {len(pictures)} pictures for {len(labels)} words; each word needs one picture.")
        seconds = list(second_labels) if second_labels else [""] * len(labels)
        if len(seconds) != len(labels):
            raise ValueError(f"Got {len(seconds)} second labels for {len(labels)} words.")
        chosen = None if columns[0] == "auto" else int(columns[0])
        grid = int(bingo_grid[0][0])
        pages, used = picture_pages(pictures, labels, seconds, layout[0], title[0], chosen, numbers[0],
                                    bingo_boards[0], grid, text_size[0], seed[0])
        images_out, pdf = save_pages(pages, filename_prefix[0])
        path = os.path.join(pdf["subfolder"], pdf["filename"])
        print(f"[pages] {layout[0]}: {len(labels)} words, grid {used[0]}x{used[1]}, {len(pages)} page(s), {path}; "
              f"words: {', '.join(labels)}")
        return {"ui": {"images": images_out, "pdf": [pdf]}, "result": (path,)}


NODE_CLASS_MAPPINGS = {
    "MarkdownToPages": MarkdownToPages,
    "PicturePage": PicturePage,
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "MarkdownToPages": "Markdown to Pages",
    "PicturePage": "Picture Page",
}
