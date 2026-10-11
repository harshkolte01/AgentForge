"""Safe display/download names; storage keys remain server-generated UUIDs."""
import re

FORMATS = {
    "text": ("general-output.txt", "text/plain; charset=utf-8"),
    "markdown": ("general-report.md", "text/markdown; charset=utf-8"),
    "csv": ("general-table.csv", "text/csv; charset=utf-8"),
}


def valid_artifact_filename(value: object, format: str) -> bool:
    if not isinstance(value, str) or not 1 <= len(value) <= 120:
        return False
    extension = FORMATS[format][0].rsplit(".", 1)[1]
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9 _.-]*\." + extension, value):
        return False
    stem = value.rsplit(".", 1)[0]
    reserved = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}
    return ".." not in value and stem == stem.rstrip(" .") and stem.split(".")[0].upper() not in reserved


def artifact_filename(format: str, filename: object = None, title: object = None) -> str:
    # Legacy callers sometimes put the requested filename in title.
    for value in (filename, title):
        if valid_artifact_filename(value, format):
            return value
    return FORMATS[format][0]
