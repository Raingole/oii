"""Convert conversational sentences into separate delivery actions, preserving text."""
import re


def split_reply_parts(messages, limit=4):
    result = []
    for raw in messages:
        text = str(raw).strip()
        if not text:
            continue
        # Commands, code and structured lists need their original formatting.
        if '```' in text or re.search(r'^\s*(?:[-*+]\s|\d+[.)、]\s|#{1,6}\s|\|)', text, re.M) or re.match(r'^(?:sudo|bash|python\d*|node|git|curl|npm|pip\d*|systemctl|docker|ssh|powershell)\s', text):
            result.append(text)
            continue
        protected = [match.span() for match in re.finditer(r'https?://[^\s<>]+|`[^`\n]+`|“[^”]*”|「[^」]*」|『[^』]*』', text)]
        start = 0
        for boundary in re.finditer(r'[。！？!?；;]+[”’」』]*|\.(?=\s+[A-Z])|\n+|[ \t]{2,}', text):
            if any(a <= boundary.start() < b for a, b in protected):
                continue
            # A sentence-ending question mark is kept in its own message.
            end = boundary.end() if not boundary.group().isspace() else boundary.start()
            part = text[start:end].strip()
            if part:
                result.append(part)
            start = boundary.end()
        if text[start:].strip():
            result.append(text[start:].strip())
    # Preserve the entire reply even if it contains more than four sentences.
    return result[:limit - 1] + ['\n'.join(result[limit - 1:])] if len(result) > limit else result
