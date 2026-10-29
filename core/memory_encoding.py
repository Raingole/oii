"""Grounded three-channel encoding; no provider calls or third-party imports."""
import re


EMOTIONS = {
    '不舒服': '不适', '不开心': '不快', '不高兴': '不快',
    '开心': '开心', '高兴': '高兴', '快乐': '快乐', '难过': '难过',
    '伤心': '伤心', '失落': '失落', '生气': '生气', '愤怒': '愤怒',
    '烦躁': '烦躁', '焦虑': '焦虑', '紧张': '紧张', '害怕': '害怕',
    '担心': '担心', '安心': '安心', '放心': '放心', '轻松': '轻松',
    '舒服': '舒适', '温暖': '温暖', '委屈': '委屈', '孤独': '孤独',
    '想念': '想念', '感动': '感动', '难受': '难受', '松了一口气': '放松',
    '松口气': '放松', '心里一沉': '失落',
    '无语': '无语', '崩溃': '崩溃', '不爽': '不快', '烦死': '烦躁',
    '心累': '疲惫', '寂寞': '孤独', '兴奋': '兴奋', '期待': '期待',
    '幸福': '幸福', '满足': '满足', '遗憾': '遗憾', '后悔': '后悔',
    '感激': '感激', '惊讶': '惊讶', '震惊': '震惊', '尴尬': '尴尬',
    '压抑': '压抑', '痛苦': '痛苦', '恐惧': '恐惧', '绝望': '绝望',
    '失望': '失望',
}
_EMOTION = re.compile('|'.join(map(re.escape, EMOTIONS)))


def explicit_affect(text):
    """Only retain literal self-reports; questions and hypotheticals are not states."""
    for clause in re.split(r'[。！？!?；;\n]', text):
        for match in _EMOTION.finditer(clause):
            before, after = clause[:match.start()], clause[match.end():]
            if re.search(r'如果|假如|假设|比如|例如|他说|她说|你说|不要|别|会不会|是不是|请回复|请输出|请写|示例|例子|这句话|写成', before):
                continue
            if re.match(r'吗|么|不[？?]?|是什么|是什么意思|这个词|的人|很重要', after.strip()):
                continue
            if re.search(r'不|没|没有|并非', before[-3:]):
                continue
            personal = re.search(r'我(?:现在|今天|真的|有点|很|太|觉得|感觉|特别|非常|挺|已经|还是|又|好|其实|终于|总算|有些|这么|那么|得|也|了|比较|的)*$', before)
            sensory = re.search(r'(?:看着|听着|这样|聊着|和你聊天|跟你聊天|心里)(?:很|挺|比较|更|就|真|很是)*$', before)
            direct = re.fullmatch(r'\s*(?:今天|现在|刚才|这会儿|这次|好|真|的|太|很|有点|终于|总算|能)*', before)
            if personal or sensory or direct:
                start = personal.start() if personal else sensory.start() if sensory else 0
                return EMOTIONS[match.group()], clause[start:match.end()].strip()[:160]
    return '', ''


def clean_observation(value):
    if not isinstance(value, dict):
        return {}
    result = {}
    for key, limit in [('fact', 1200), ('gist', 240), ('affect', 120), ('affect_evidence', 160)]:
        if isinstance(value.get(key), str):
            result[key] = value[key].strip()[:limit]
    for key in ('entities', 'themes'):
        raw = value.get(key, [])
        if isinstance(raw, list):
            result[key] = list(dict.fromkeys(item.strip()[:40] for item in raw[:8] if isinstance(item, str) and item.strip()))
    return result


def encode_turn(user_text, assistant_text='', observation=None):
    user_text, assistant_text = str(user_text).strip(), str(assistant_text).strip()
    value = clean_observation(observation)
    fallback_fact = f'用户：{user_text}' + (f'\n助手：{assistant_text}' if assistant_text else '')
    gist = value.get('gist') or '这次谈到：' + re.sub(r'\s+', ' ', user_text)[:100]
    if not value.get('gist') and re.search(r'(少.{0,4}(符号|标点)|句子.{0,4}短)', user_text):
        gist = '用户偏好少符号、简短的表达'
    label, quote = explicit_affect(user_text)
    # Model affect must carry an exact quote and a recognizable emotional
    # self-report. Otherwise use the conservative literal extraction only.
    evidence = value.get('affect_evidence', '')
    if evidence and evidence in user_text:
        evidence_label, _ = explicit_affect(evidence)
        if evidence_label and label == evidence_label:
            quote = evidence
    affect = f'用户感到{label}（原话：{quote}）' if label else ''
    return {
        'fact': value.get('fact') or fallback_fact, 'gist': gist, 'affect': affect,
        'entities': [item for item in value.get('entities', []) if item in user_text],
        'themes': value.get('themes', []),
    }
