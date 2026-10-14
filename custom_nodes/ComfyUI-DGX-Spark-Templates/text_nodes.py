"""Text nodes: Table Lookup and Split Columns.

Table Lookup maps a key, such as the value of a dropdown, to the cells of a
matching row in a small table written as `key | a | b | ...`, one row per line.
Cells that read as numbers come out as numbers, so one dropdown can set a text,
a file name and a strength at once.

Split Columns reads text written one item per line with cells separated by `|`,
the format a language model is easy to ask for, and returns one list per
column. Wired into a node, a list makes ComfyUI run that node once per item. It
checks the format first and names the first line that does not fit, so a
malformed draft stops the run with a message instead of being rendered.
"""

import re

from .memory_nodes import ANY

_INT = re.compile(r"-?\d+")
_FLOAT = re.compile(r"-?\d+\.\d+")
_NUMBERING = re.compile(r"^\s*\d+\s*[.)]\s*")
_RULE = re.compile(r"^:?-{3,}:?$")
MAX_COLUMNS = 5


def cells(line):
    """`a | b | c` to a list of stripped cells; outer pipes of a Markdown table row are ignored."""
    line = line.strip()
    if line.startswith("|"):
        line = line[1:]
    if line.endswith("|"):
        line = line[:-1]
    return [cell.strip() for cell in line.split("|")]


def typed(cell):
    if _INT.fullmatch(cell):
        return int(cell)
    if _FLOAT.fullmatch(cell):
        return float(cell)
    return cell


class TableLookup:
    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "table": ("STRING", {"multiline": True, "default": "",
                                     "tooltip": "One row per line: key | value | value ... A row whose key is * "
                                                "answers every key no other row matches. Lines starting with # "
                                                "are skipped."}),
                # socket-only input after the widgets: workflows store widget values by position
                "key": ("STRING", {"forceInput": True}),
            },
        }

    RETURN_TYPES = (ANY, ANY, ANY, ANY, "STRING")
    RETURN_NAMES = ("column_2", "column_3", "column_4", "column_5", "row")
    FUNCTION = "lookup"
    CATEGORY = "text"
    DESCRIPTION = ("Finds the row of a table whose first cell matches key, ignoring case, and returns its other "
                   "cells. Cells that read as whole or decimal numbers come out as numbers.")

    def lookup(self, table, key):
        rows, fallback = {}, None
        for line in table.splitlines():
            if not line.strip() or line.lstrip().startswith("#"):
                continue
            row = cells(line)
            if row[0] == "*":
                fallback = row
            else:
                rows.setdefault(row[0].casefold(), row)
        row = rows.get(key.strip().casefold(), fallback)
        if row is None:
            raise ValueError(f"Table Lookup has no row for {key!r}. Keys: {', '.join(r[0] for r in rows.values())}.")
        values = [typed(cell) for cell in row[1:MAX_COLUMNS]]
        values += [""] * (MAX_COLUMNS - 1 - len(values))
        return (*values, " | ".join(row))


class SplitColumns:
    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "min_columns": ("INT", {"default": 1, "min": 1, "max": MAX_COLUMNS,
                                        "tooltip": "Every row needs at least this many cells."}),
                "title_line": ("BOOLEAN", {"default": True,
                                           "tooltip": "A first line starting with # is the title, not a row."}),
                "strip_numbers": ("BOOLEAN", {"default": True,
                                              "tooltip": "Drop numbering such as '1.' or '2)' from the first cell."}),
                "text": ("STRING", {"forceInput": True}),
            },
            "optional": {
                "expected_rows": ("INT", {"forceInput": True,
                                          "tooltip": "When connected, the number of rows has to match."}),
            },
        }

    RETURN_TYPES = ("STRING", "STRING", "STRING", "STRING", "STRING", "STRING", "STRING", "INT")
    RETURN_NAMES = ("title", "rows", "column_1", "column_2", "column_3", "column_4", "column_5", "count")
    OUTPUT_IS_LIST = (False, True, True, True, True, True, True, False)
    FUNCTION = "split"
    CATEGORY = "text"
    DESCRIPTION = ("Splits text written one item per line, cells separated by |, into one list per column. Blank "
                   "lines, Markdown table rules and later # lines are skipped.")

    def split(self, min_columns, title_line, strip_numbers, text, expected_rows=None):
        title, rows, columns = "", [], [[] for _ in range(MAX_COLUMNS)]
        lines = text.strip().splitlines()
        if title_line and lines and lines[0].lstrip().startswith("#"):
            title = lines.pop(0).lstrip("# ").strip()
        for number, line in enumerate(lines, 1):
            if not line.strip() or line.lstrip().startswith(("#", "<!--")):
                continue
            row = cells(line)
            if all(_RULE.fullmatch(cell) for cell in row if cell):
                continue
            if strip_numbers:
                row[0] = _NUMBERING.sub("", row[0])
            if len([cell for cell in row if cell]) < min_columns:
                raise ValueError(f"Line {number} does not have {min_columns} parts separated by |: {line.strip()!r}. "
                                 "Correct the text and run again.")
            rows.append(" | ".join(row))
            for k in range(MAX_COLUMNS):
                columns[k].append(row[k] if k < len(row) else "")
        if not rows:
            raise ValueError("No rows found. Write one item per line, with its parts separated by |.")
        if expected_rows is not None and expected_rows != len(rows):
            raise ValueError(f"Expected {expected_rows} rows and found {len(rows)}. Correct the text and run again.")
        return (title, rows, *columns, len(rows))


NODE_CLASS_MAPPINGS = {
    "TableLookup": TableLookup,
    "SplitColumns": SplitColumns,
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "TableLookup": "Table Lookup",
    "SplitColumns": "Split Columns",
}
