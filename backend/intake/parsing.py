"""Deterministic bounded text/HTML extraction; never executes source content."""
from html.parser import HTMLParser
import re


EXTRACTOR_VERSION = 'stdlib-text-html/1.0'
FILE_EXTENSIONS = ['.txt', '.md', '.html', '.htm']


class ParseError(ValueError):
    def __init__(self, message, code='PARSE_FAILED'):
        super().__init__(message)
        self.code = code


class _HTMLText(HTMLParser):
    SKIP = {'script', 'style', 'noscript', 'template', 'svg', 'head'}
    BREAK = {'p', 'div', 'br', 'li', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'section', 'article', 'tr', 'hr'}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.stack = []
        self.text = []
        self.title = []
        self.elements = 0

    def handle_starttag(self, tag, attrs):
        self.elements += 1
        if self.elements > 50000 or len(self.stack) > 128:
            raise ParseError('HTML complexity exceeds the parser limit', 'RESOURCE_LIMIT')
        if len(attrs) > 100 or sum(len(k) + len(v or '') for k, v in attrs) > 16384:
            raise ParseError('HTML attributes exceed the parser limit', 'RESOURCE_LIMIT')
        if tag not in ('br', 'hr', 'img', 'meta', 'input', 'link', 'wbr', 'source', 'area', 'base', 'col', 'embed', 'param', 'track'):
            self.stack.append(tag)
        if tag in self.BREAK:
            self.text.append('\n')

    def handle_endtag(self, tag):
        if tag in self.stack:
            index = len(self.stack) - 1 - self.stack[::-1].index(tag)
            self.stack = self.stack[:index]
        if tag in self.BREAK:
            self.text.append('\n')

    def handle_data(self, data):
        if 'title' in self.stack:
            self.title.append(data)
        elif not self.SKIP.intersection(self.stack):
            self.text.append(data)


def extract(raw: bytes, mime_type: str, charset='utf-8', maximum=2 * 1024 * 1024):
    if len(raw) > maximum:
        raise ParseError('Source exceeds the byte limit', 'RESOURCE_LIMIT')
    try:
        text = raw.decode('utf-8-sig' if charset.lower() == 'utf-8' else charset, errors='strict')
    except (UnicodeError, LookupError) as exc:
        raise ParseError('Source text encoding is unsupported or invalid') from exc
    if '\x00' in text:
        raise ParseError('Binary content is not a supported text file', 'UNSUPPORTED_FORMAT')
    if mime_type in ('text/html', 'application/xhtml+xml'):
        parser = _HTMLText()
        parser.feed(text)
        parser.close()
        content = '\n'.join(line.strip() for line in ''.join(parser.text).splitlines() if line.strip())
        title = re.sub(r'\s+', ' ', ''.join(parser.title)).strip()[:500]
    else:
        # A disguised binary or HTML file cannot evade the format matrix.
        if mime_type != 'text/markdown' and re.search(r'<\s*(?:!doctype\s+html|html|script|head|body)\b', text[:4096], re.I):
            raise ParseError('Detected HTML does not match the declared text format', 'FORMAT_MISMATCH')
        content, title = text, ''
    if not content.strip():
        raise ParseError('No readable text could be extracted', 'EMPTY_EXTRACTION')
    return {'content': content, 'document_title': title, 'extractor_version': EXTRACTOR_VERSION,
            'extraction_status': 'TEXT_EXTRACTED', 'published_at': None,
            'coverage_limits': ['Text only; scripts, styles and embedded media are not executed or captured.',
                                'Publication time is unknown unless explicitly available from a supported parser.'],
            'locators': {'kind': 'unicode_character_offsets', 'start': 0, 'end': len(content)}}
