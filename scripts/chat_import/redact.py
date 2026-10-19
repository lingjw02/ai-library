"""Redact recognizable credentials from import copies, never original logs."""
import re

PATTERNS = {
    'encoded_credential': r'\beyJ[A-Za-z0-9+/=_-]{60,}',
    'api_key': r'\b(?:sk-(?:proj-)?[A-Za-z0-9_-]{24,}|gh[pousr]_[A-Za-z0-9]{30,}|AIza[0-9A-Za-z_-]{30,}|xkeysib-[A-Za-z0-9_-]{30,}|xsmtpsib-[A-Za-z0-9_-]{30,})',
    'jwt': r'\beyJ[A-Za-z0-9_-]{15,}\.[A-Za-z0-9_-]{15,}\.[A-Za-z0-9_-]{15,}',
    'private_key': r'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----.*?-----END (?:RSA |EC |OPENSSH )?PRIVATE KEY-----',
    'labelled_credential': r'(?i)(?<=\b)(?:api[_ -]?key|smtp[_ -]?key|password|access[_ -]?token|secret[_ -]?key)\s*[:=]\s*[\x22\x27]?[A-Za-z0-9_/+.=-]{24,}',
}

def redact(text):
    counts = {}
    for kind, pattern in PATTERNS.items():
        text, count = re.subn(pattern, '[REDACTED:' + kind + ']', text, flags=re.S)
        if count:
            counts[kind] = count
    return text, counts
