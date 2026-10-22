"""
anthropic_backend — ชั้น 1 (ชั้นหลัก, คนละผู้ให้บริการกับ RunningHub): Anthropic ผ่าน anthropic SDK

หน้าที่: รับ messages/tools **รูปแบบ OpenAI** (แบบเดียวกับที่ services.py ประกอบให้ RunningHub)
→ แปลงเป็นรูปแบบ Anthropic Messages API → เรียก client.messages.create → แปลงคำตอบกลับเป็น
object หน้าตาเหมือน OpenAI (choices[0].message.content / .tool_calls[].function.name/.arguments / usage)
เพื่อให้ loop ใน services.answer_question + read_order_number ใช้โค้ดเดิมโดยไม่แยกเคส

รองรับ: system prompt · tool calling (tool_use ↔ tool_result) · vision (image_url data: URL → image base64)
ไม่แตะ DB · ไม่ import ตอนโหลด services (import ใน services._call_tier)
"""
import base64
import json
import re
from types import SimpleNamespace

from django.conf import settings

_DATA_URL_RE = re.compile(r'^data:(?P<mime>[\w/+.-]+);base64,(?P<data>.+)$', re.DOTALL)


def _client(timeout):
    import anthropic  # ImportError → ผู้เรียก except (ชั้นนี้ไม่พร้อม = ข้าม)
    return anthropic.Anthropic(api_key=settings.ANTHROPIC_API_KEY, timeout=timeout, max_retries=0)


def convert_tools(openai_tools):
    """OpenAI function tools → Anthropic tools (name/description/input_schema)"""
    out = []
    for t in openai_tools or []:
        fn = t.get('function', t)
        out.append({
            'name': fn['name'],
            'description': fn.get('description', ''),
            'input_schema': fn.get('parameters') or {'type': 'object', 'properties': {}},
        })
    return out


def _image_block(url):
    m = _DATA_URL_RE.match(url or '')
    if not m:
        return {'type': 'image', 'source': {'type': 'url', 'url': url}}
    return {'type': 'image', 'source': {'type': 'base64', 'media_type': m.group('mime'),
                                        'data': m.group('data')}}


def _user_blocks(content):
    if isinstance(content, str):
        return content
    blocks = []
    for part in content or []:
        ptype = part.get('type')
        if ptype == 'text':
            blocks.append({'type': 'text', 'text': part.get('text', '')})
        elif ptype == 'image_url':
            blocks.append(_image_block((part.get('image_url') or {}).get('url')))
    return blocks


def _parse_args(raw):
    if isinstance(raw, dict):
        return raw
    try:
        data = json.loads(raw or '{}')
    except (ValueError, TypeError):
        return {}
    return data if isinstance(data, dict) else {}


def convert_messages(messages):
    """OpenAI messages → (system_text|None, anthropic_messages)
    - system รวมเป็น system param
    - assistant ที่มี tool_calls → content blocks text + tool_use
    - tool (ผล tool) → user message ที่มี tool_result; ผล tool ติดกันหลายอันรวมใน user message เดียว
      (Anthropic ต้องการ tool_result ทุกอันของ turn นั้นใน user message เดียว)"""
    system_parts = []
    out = []
    for m in messages:
        role = m.get('role')
        content = m.get('content')
        if role == 'system':
            if content:
                system_parts.append(content if isinstance(content, str) else json.dumps(content, ensure_ascii=False))
        elif role == 'user':
            out.append({'role': 'user', 'content': _user_blocks(content)})
        elif role == 'assistant':
            blocks = []
            if content:
                blocks.append({'type': 'text', 'text': content})
            for tc in m.get('tool_calls') or []:
                fn = tc.get('function', {})
                blocks.append({'type': 'tool_use', 'id': tc.get('id'), 'name': fn.get('name'),
                               'input': _parse_args(fn.get('arguments'))})
            out.append({'role': 'assistant', 'content': blocks if blocks else (content or '')})
        elif role == 'tool':
            block = {'type': 'tool_result', 'tool_use_id': m.get('tool_call_id'),
                     'content': content if isinstance(content, str) else json.dumps(content, ensure_ascii=False)}
            last = out[-1] if out else None
            if last and last['role'] == 'user' and isinstance(last['content'], list) \
                    and last['content'] and last['content'][0].get('type') == 'tool_result':
                last['content'].append(block)
            else:
                out.append({'role': 'user', 'content': [block]})
    return ('\n'.join(system_parts) or None), out


def to_openai_like(resp):
    """anthropic Message → SimpleNamespace หน้าตาเหมือน OpenAI ChatCompletion (เฉพาะ field ที่ services ใช้)"""
    text_parts = []
    tool_calls = []
    for block in getattr(resp, 'content', None) or []:
        btype = getattr(block, 'type', None)
        if btype == 'text':
            text_parts.append(getattr(block, 'text', '') or '')
        elif btype == 'tool_use':
            tool_calls.append(SimpleNamespace(
                id=getattr(block, 'id', None), type='function',
                function=SimpleNamespace(name=getattr(block, 'name', None),
                                         arguments=json.dumps(getattr(block, 'input', None) or {},
                                                              ensure_ascii=False))))
    usage = getattr(resp, 'usage', None)
    prompt_t = getattr(usage, 'input_tokens', None)
    completion_t = getattr(usage, 'output_tokens', None)
    total = (prompt_t or 0) + (completion_t or 0) if usage is not None else None
    message = SimpleNamespace(content=''.join(text_parts), tool_calls=tool_calls or None)
    return SimpleNamespace(
        choices=[SimpleNamespace(message=message, finish_reason=getattr(resp, 'stop_reason', None))],
        usage=SimpleNamespace(prompt_tokens=prompt_t, completion_tokens=completion_t, total_tokens=total),
        model=getattr(resp, 'model', None),
    )


def create(model, messages, tools=None, max_tokens=1000, timeout=20):
    """เรียก Anthropic Messages API ด้วย messages/tools รูปแบบ OpenAI → response หน้าตา OpenAI
    exception ของ anthropic SDK (APIStatusError/APITimeoutError/…) หลุดออกไปให้ services ตัดสิน"""
    client = _client(timeout)
    system, msgs = convert_messages(messages)
    kwargs = {
        'model': model,
        'max_tokens': max_tokens,
        'messages': msgs,
        # งานสั้น ตอบไว — ไม่ส่ง temperature (Claude รุ่น 5.x รับเฉพาะค่า default)
        'output_config': {'effort': 'low'},
    }
    if system:
        kwargs['system'] = system
    if tools:
        kwargs['tools'] = convert_tools(tools)
    return to_openai_like(client.messages.create(**kwargs))
