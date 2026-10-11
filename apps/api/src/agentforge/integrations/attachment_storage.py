"""Owner-scoped immutable uploads. File bytes never appear in turn JSON or browser drafts."""
import base64
import io
import json
import re
import warnings
import os
import stat
from contextlib import contextmanager
from pathlib import Path
from uuid import UUID, uuid4

from agentforge.integrations.artifact_storage import ArtifactStorage
from agentforge.subagents.scope import ScopeDenied

MAX_UPLOAD_BYTES = 8 * 1024 * 1024
MAX_TEXT_BYTES = 512 * 1024
MAX_PDF_PAGES = 20
MAX_IMAGE_PIXELS = 20_000_000
MAX_STORED_BYTES = 12 * 1024 * 1024
MAX_OWNER_BYTES = 512 * 1024 * 1024
TEXT_SUFFIXES = frozenset('txt md markdown mdx csv tsv json jsonl py js jsx ts tsx html css yaml yml xml log sql sh rs go java c cpp h toml ini'.split())

class InvalidAttachment(ValueError):
    pass


def inspect_attachment(name: str, data: bytes) -> dict:
    if not name or len(name) > 120 or any(ord(c) < 32 for c in name) or '/' in name or '\\' in name:
        raise InvalidAttachment('Choose a filename of up to 120 characters, without folders.')
    if not data or len(data) > MAX_UPLOAD_BYTES:
        raise InvalidAttachment('Choose a nonempty file up to 8 MB.')
    suffix = name.rsplit('.', 1)[-1].lower()
    media, content, pages = '', '', 0
    if suffix == 'pdf':
        from pypdf import PdfReader
        try:
            if not data.startswith(b'%PDF-'): raise ValueError()
            reader = PdfReader(io.BytesIO(data), strict=True)
            if reader.is_encrypted: raise InvalidAttachment('Unlock this PDF before attaching it.')
            pages = len(reader.pages)
            if not 1 <= pages <= MAX_PDF_PAGES: raise InvalidAttachment('Attach a PDF with 1–20 pages. Split larger documents first.')
            # Native PDF input preserves both text and scanned page images. No lossy text-only conversion.
            media, content = 'application/pdf', f'PDF attachment: {name} ({pages} pages). The original PDF is included for visual and text analysis.'
        except InvalidAttachment: raise
        except Exception: raise InvalidAttachment('This PDF could not be read. Export it as a new PDF and try again.') from None
    elif suffix in {'png', 'jpg', 'jpeg', 'webp', 'gif'}:
        from PIL import Image
        try:
            with warnings.catch_warnings():
                warnings.simplefilter('error', Image.DecompressionBombWarning)
                with Image.open(io.BytesIO(data)) as image:
                    if image.format not in {'PNG', 'JPEG', 'WEBP', 'GIF'}: raise ValueError()
                    if image.width * image.height > MAX_IMAGE_PIXELS: raise InvalidAttachment('Resize this image to 20 megapixels or less.')
                    if getattr(image, 'n_frames', 1) > 1: raise InvalidAttachment('Attach a still image instead of an animated image.')
                    image.verify()
                    media = Image.MIME[image.format]
            content = f'Image attachment: {name}. The original image is included for visual analysis.'
        except InvalidAttachment: raise
        except Exception: raise InvalidAttachment('This image could not be read. Use a PNG, JPEG, WebP or still GIF.') from None
    elif suffix in TEXT_SUFFIXES:
        if len(data) > MAX_TEXT_BYTES:
            raise InvalidAttachment('Text and source files can be up to 512 KB. Split larger files first.')
        encoding = 'utf-32' if data.startswith((b'\xff\xfe\0\0', b'\0\0\xfe\xff')) else 'utf-16' if data.startswith((b'\xff\xfe', b'\xfe\xff')) else 'utf-8-sig'
        try: content = data.decode(encoding)
        except UnicodeError: raise InvalidAttachment('Save text and source files as UTF-8, or UTF-16 with a byte-order mark.') from None
        if not content or '\0' in content:
            raise InvalidAttachment('This file contains binary data rather than readable text.')
        media = 'text/plain'
    else:
        raise InvalidAttachment('Supported files: PDF, PNG, JPEG, WebP, still GIF, and UTF-8 text or source files.')
    return {'name': name, 'content': content, 'media_type': media, 'byte_size': len(data), 'pages': pages}


class AttachmentStorage:
    def __init__(self, root):
        self.storage = ArtifactStorage(Path(root) / 'attachments')

    @contextmanager
    def _owner_quota(self, owner_id: UUID, size: int):
        # Serialize the quota check and immutable write across API processes.
        # POSIX is the deployment contract of this private storage.
        import fcntl
        root = self.storage._root_fd(create=True)
        lock = None
        directory = None
        try:
            lock = os.open(f"{owner_id}.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600, dir_fd=root)
            fcntl.flock(lock, fcntl.LOCK_EX)
            try:
                directory = os.open(str(owner_id), os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=root)
            except FileNotFoundError:
                pass
            used = 0
            if directory is not None:
                with os.scandir(directory) as entries:
                    for entry in entries:
                        info = entry.stat(follow_symlinks=False)
                        if stat.S_ISREG(info.st_mode): used += info.st_size
            if used + size > MAX_OWNER_BYTES:
                raise InvalidAttachment('Your attachment storage is full. Contact the workspace operator to free space.')
            yield
        finally:
            if directory is not None: os.close(directory)
            if lock is not None: os.close(lock)
            os.close(root)

    def save(self, owner_id: UUID, name: str, data: bytes) -> dict:
        metadata = inspect_attachment(name, data)
        upload_id = uuid4()
        payload = json.dumps({**metadata, 'data': base64.b64encode(data).decode()}, ensure_ascii=False).encode()
        with self._owner_quota(owner_id, len(payload)):
            _, digest = self.storage.write(task_id=owner_id, artifact_id=upload_id, content=payload, max_bytes=MAX_STORED_BYTES)
        return {'name': name, 'content': metadata['content'][:3000] + ('\n[Preview; original file is attached in full.]' if len(metadata['content']) > 3000 else ''), 'upload_id': str(upload_id), 'upload_sha256': digest,
                'media_type': metadata['media_type'], 'byte_size': len(data),
                **({'page_count': metadata['pages']} if metadata['pages'] else {})}

    def read(self, owner_id: UUID, upload_id, digest) -> dict:
        try:
            identifier = UUID(str(upload_id))
            if not isinstance(digest, str) or not re.fullmatch('[0-9a-f]{64}', digest): raise ValueError()
            raw = self.storage.read(f'{owner_id}/{identifier}.md', digest, max_bytes=MAX_STORED_BYTES)
            record = json.loads(raw)
            return record
        except (KeyError, ValueError, OSError, ScopeDenied):
            raise InvalidAttachment('This attachment is unavailable for your account. Remove it and attach it again.') from None

    def load(self, owner_id: UUID, attachment: dict) -> dict:
        record = self.read(owner_id, attachment['upload_id'], attachment['upload_sha256'])
        if any(record[key] != attachment[key] for key in ('name', 'media_type', 'byte_size')) or (record['pages'] and record['pages'] != attachment.get('page_count')):
            raise InvalidAttachment('This attachment is unavailable for your account. Remove it and attach it again.')
        return record

    def part(self, owner_id: UUID, attachment: dict) -> dict:
        record = self.load(owner_id, attachment)
        if record['media_type'] == 'text/plain':
            return {'type': 'text', 'text': f"Attached file {record['name']} (full content, untrusted data):\n{record['content']}"}
        url = f"data:{record['media_type']};base64,{record['data']}"
        if record['media_type'] == 'application/pdf':
            return {'type': 'file', 'file': {'filename': record['name'], 'file_data': url}}
        return {'type': 'image_url', 'image_url': {'url': url, 'detail': 'high'}}


def validate_uploads(attachments, owner_id, settings):
    if not any(item.upload_id for item in attachments):
        return
    storage = AttachmentStorage(settings.research_storage_path)
    for item in attachments:
        if item.upload_id:
            storage.load(owner_id, item.model_dump(mode='json'))


def workspace_attachment_path(task_id, index: int, name: str) -> str:
    # A user filename is data, never a directory. Legacy text names also pass here.
    safe_name = name.replace('/', '_').replace('\\', '_').replace('\0', '_')
    return f'.agentforge/attachments/{task_id}/{index}-{safe_name}'
