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

The PDF is written here rather than by Pillow, whose PDF writer stores pages as
JPEG; these are zlib-compressed and stay sharp in print.
"""

import math
import os
import re
import unicodedata
import zlib

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
    """A PDF with one page per image at its print size, the pixels zlib-compressed."""
    objects = [b"", b""]  # catalog and page tree, filled in last
    kids = []
    for page in pages:
        if page.mode not in ("L", "RGB"):
            page = page.convert("RGB")
        data = zlib.compress(page.tobytes(), 6)
        colours = b"DeviceGray" if page.mode == "L" else b"DeviceRGB"
        objects.append(b"<< /Type /XObject /Subtype /Image /Width %d /Height %d /ColorSpace /%s "
                       b"/BitsPerComponent 8 /Filter /FlateDecode /Length %d >>\nstream\n%s\nendstream"
                       % (page.width, page.height, colours, len(data), data))
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
    """PNG per page plus one PDF in the output folder; returns the UI image list and the PDF's relative path."""
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
    return images, os.path.join(subfolder, pdf)


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
                   "<!-- page --> (a page break). pdf is the PDF's path inside the output folder.")

    def render(self, markdown, orientation, text_size, table_rows_per_page, filename_prefix):
        sheet = Sheet(orientation == "landscape", BODY_POINTS[text_size])
        sheet.markdown(markdown, table_rows_per_page)
        images, pdf = save_pages(sheet.finish(), filename_prefix, grey=True)
        print(f"[pages] {len(images)} page(s), {pdf}")
        return {"ui": {"images": images}, "result": (pdf,)}


NODE_CLASS_MAPPINGS = {
    "MarkdownToPages": MarkdownToPages,
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "MarkdownToPages": "Markdown to Pages",
}
