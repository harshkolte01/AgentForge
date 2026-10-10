"""Incremental extraction of one top-level string field from a streamed JSON object.

Agents answer with a single JSON object. The person-facing text lives in one string field
(`response` for chat replies, `summary` for research/general answers). This reader feeds on the
raw token stream and yields that field's decoded text as it arrives, so the reply can be shown
while it is still being written. It never yields any other field, and it yields nothing for a
field whose value is not a string (for example `"response": null`).
"""

_ESCAPES = {'"': '"', "\\": "\\", "/": "/", "b": "\b", "f": "\f", "n": "\n", "r": "\r", "t": "\t"}


class JsonStringFieldStream:
    def __init__(self, field: str) -> None:
        self.field = field
        self._depth = 0
        self._in_string = False
        self._escape = False
        self._unicode: str | None = None  # hex digits collected after \u
        self._high_surrogate = ""
        self._expect_key = False
        self._reading_key = False
        self._key = ""
        self._last_key = ""
        self._await_value = False  # saw ':' for the target key, waiting for the value to start
        self._streaming = False  # inside the target string value
        self.done = False

    def feed(self, chunk: str) -> str:
        """Return the newly decoded text of the target field contained in `chunk`."""
        out: list[str] = []
        for char in chunk:
            if self.done:
                break
            if self._streaming:
                self._stream_char(char, out)
            elif self._in_string:
                self._string_char(char)
            else:
                self._structure_char(char)
        return "".join(out)

    # Decoding inside the target value -------------------------------------------------
    def _stream_char(self, char: str, out: list[str]) -> None:
        if self._unicode is not None:
            self._unicode += char
            if len(self._unicode) == 4:
                try:
                    code = int(self._unicode, 16)
                except ValueError:
                    code = 0xFFFD
                self._unicode = None
                if 0xD800 <= code < 0xDC00:
                    self._high_surrogate = chr(code)
                    return
                if 0xDC00 <= code < 0xE000 and self._high_surrogate:
                    out.append(chr(0x10000 + ((ord(self._high_surrogate) - 0xD800) << 10) + (code - 0xDC00)))
                else:
                    out.append(chr(code))
                self._high_surrogate = ""
            return
        if self._escape:
            self._escape = False
            if char == "u":
                self._unicode = ""
            else:
                out.append(_ESCAPES.get(char, char))
            return
        if char == "\\":
            self._escape = True
        elif char == '"':
            self._streaming = False
            self.done = True
        else:
            out.append(char)

    # Tracking key names outside the target value --------------------------------------
    def _string_char(self, char: str) -> None:
        if self._escape:
            self._escape = False
            if self._reading_key:
                self._key += char
            return
        if char == "\\":
            self._escape = True
        elif char == '"':
            self._in_string = False
            if self._reading_key:
                self._reading_key = False
                self._last_key = self._key
        elif self._reading_key:
            self._key += char

    def _structure_char(self, char: str) -> None:
        if char == '"':
            if self._await_value:
                self._await_value = False
                self._streaming = True
                return
            self._in_string = True
            if self._depth == 1 and self._expect_key:
                self._reading_key, self._key = True, ""
            self._expect_key = False
        elif char in "{[":
            self._depth += 1
            self._await_value = False
            self._expect_key = char == "{" and self._depth == 1
        elif char in "}]":
            self._depth -= 1
            self._await_value = False
        elif char == ",":
            self._expect_key = self._depth == 1
            self._await_value = False
        elif char == ":":
            self._await_value = self._depth == 1 and self._last_key == self.field
        elif char.isspace():
            return
        else:
            # A non-string value (null, number, true...) for the target field: nothing to stream.
            self._await_value = False
