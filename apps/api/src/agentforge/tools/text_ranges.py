"""Complete-line, UTF-8-byte-bounded read pages for untrusted source text."""

MAX_READ_BYTES = 10_000
MAX_READ_LINES = 200


def read_lines(text: str, *, line_start: int = 1, max_lines: int = MAX_READ_LINES) -> dict:
    if isinstance(line_start, bool) or not isinstance(line_start, int) or line_start < 1:
        raise ValueError("invalid_read_line_start")
    if isinstance(max_lines, bool) or not isinstance(max_lines, int) or not 1 <= max_lines <= MAX_READ_LINES:
        raise ValueError("invalid_read_max_lines")
    lines = text.splitlines(keepends=True)
    total = len(lines)
    if line_start > total + 1:
        raise ValueError("read_line_start_out_of_range")
    selected: list[str] = []
    byte_size = 0
    cursor = line_start - 1
    for line in lines[cursor:cursor + max_lines]:
        size = len(line.encode("utf-8"))
        if size > MAX_READ_BYTES:
            if not selected:
                # No partial line cursor exists: fail explicitly so retries do
                # not loop on a page that can never advance past this line.
                raise ValueError("read_line_exceeds_10000_byte_limit")
            break
        if byte_size + size > MAX_READ_BYTES:
            break
        selected.append(line)
        byte_size += size
        cursor += 1
    has_more = cursor < total
    return {"content": "".join(selected), "line_start": line_start,
            "total_lines": total, "next_line": cursor + 1 if has_more else None,
            "has_more": has_more, "truncated": has_more}
