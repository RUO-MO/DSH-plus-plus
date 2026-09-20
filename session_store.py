# -*- coding: utf-8 -*-
"""DSH 会话与凭证只读通道（v2 会话管理/供应商配置的数据源）。

数据位置（2026-09-13 对照运行中的 host 进程实测确认）：
  - host home（dev 模式）：env DSH_HOME > config dsh_home >
    <harness_root>/apps/desktop/.desktop-build/development/home > ~/.dsh
  - host home（packaged 模式，v2.1）：config dsh_home > env DSH_HOME > ~/.dsh（默认，
    %USERPROFILE% 下的 .dsh，打包版数据根；会话/凭证面板据此读打包版数据）
  - 会话：home/sessions/<project-key>/session-<uuid>/session.v3.jsonl.zstd
    （zstd 压缩的 JSONL 事件流；首行 header，含 user/message、
     assistant/attempt、session/title、turn/* 等事件）
  - 凭证：home/.credentials.yaml（records: {<connection>: {kind, payload}}）
  - host 设置：home/settings.yaml（ui-theme / agent-presets / ui-onboarding）

铁律：本模块对 DSH 数据 **只读**；所有导出/备份写到 ~/.dsh-skins/ 下。
可选依赖：zstandard（会话正文解压）、PyYAML（凭证读取）；缺失时对应
能力优雅降级（列表退化为文件名级，凭证通道报依赖提示）。
"""
import glob
import io
import json
import os
import re
import shutil
import time
import zipfile

def _skin_root():
    """DSH++ 数据根（backups/exports 落点）。与 dsh_env.SKIN_ROOT 一致，支持 skin_root 重定向。"""
    try:
        import dsh_env
        return dsh_env.SKIN_ROOT
    except Exception:
        return os.path.join(os.path.expanduser('~'), '.dsh-skins')


BACKUP_DIR = os.path.join(_skin_root(), 'backups')
EXPORT_DIR = os.path.join(_skin_root(), 'exports')

_SESSION_RE = re.compile(r'^(session\.v\d+\.jsonl)(\.zstd)?$')


# ---------------- home 解析 ----------------
def home(cfg=None):
    """解析 DSH host home 目录（不校验存在性，返回路径或 None）。

    mode-aware（v2.1）：launcher_mode=packaged 时默认 ~/.dsh（%USERPROFILE% 下的 .dsh，
    打包版数据根），显式覆盖 config.dsh_home / env DSH_HOME 始终最高优先；
    dev 模式维持原链（config.dsh_home > env DSH_HOME > 仓库 development/home > ~/.dsh）。
    cfg 显式传入时沿用旧解析（dsh_env 不可用/自定义调用兜底）。
    """
    if cfg is None:
        try:
            import dsh_env
            path, _src = dsh_env.dsh_home()
            return path
        except Exception:
            pass
    cand = cfg.get('dsh_home') if cfg else None
    if cand and os.path.isdir(cand):
        return cand
    env = os.environ.get('DSH_HOME')
    if env and os.path.isdir(env):
        return env
    try:
        import dsh_env
        root = dsh_env.harness_root()
    except Exception:
        root = None
    if root:
        dev = os.path.join(root, 'apps', 'desktop', '.desktop-build', 'development', 'home')
        if os.path.isdir(dev):
            return dev
    fallback = os.path.join(os.path.expanduser('~'), '.dsh')
    return fallback if os.path.isdir(fallback) else None


def sessions_root(cfg=None):
    h = home(cfg)
    return os.path.join(h, 'sessions') if h else None


def credentials_path(cfg=None):
    h = home(cfg)
    return os.path.join(h, '.credentials.yaml') if h else None


# ---------------- 事件流解析 ----------------
def _zstd_bytes(path):
    try:
        import zstandard as zstd
    except ImportError:
        raise RuntimeError('缺少依赖 zstandard（pip install zstandard）')
    with open(path, 'rb') as fh:
        raw = fh.read()
    try:
        return zstd.ZstdDecompressor().stream_reader(raw).read()
    except Exception as e:
        raise RuntimeError('解压失败: {0}'.format(e))


def iter_events(path):
    """逐行产出事件 dict；解压失败/损坏行跳过"""
    if path.endswith('.zstd'):
        data = _zstd_bytes(path)
    else:
        with open(path, 'rb') as fh:
            data = fh.read()
    for ln in data.decode('utf-8', 'replace').splitlines():
        ln = ln.strip()
        if not ln:
            continue
        try:
            yield json.loads(ln)
        except Exception:
            continue


def _parse_session(dir_path):
    """读一个 session 目录 → 元数据 + 事件列表（单次解压复用）"""
    zstd_file = None
    for fn in sorted(os.listdir(dir_path)):
        if _SESSION_RE.match(fn) and (fn.endswith('.zstd') or fn.endswith('.jsonl')):
            zstd_file = os.path.join(dir_path, fn)
            break
    if not zstd_file:
        return None
    meta = {"id": os.path.basename(dir_path), "file": zstd_file,
            "size": os.path.getsize(zstd_file), "mtime": os.path.getmtime(zstd_file)}
    events = list(iter_events(zstd_file))
    # 真实用量/规模统计（一次解压顺带算完，列表与详情复用）
    usage = {"input_tokens": 0, "output_tokens": 0, "cache_read_tokens": 0,
             "total_tokens": 0, "assistant_messages": 0}
    turns, tool_calls, user_msgs = 0, 0, 0
    for ev in events:
        t = ev.get('type')
        if t == 'session':
            meta['created_at'] = ev.get('createdAt', 0) / 1000.0
            meta['cwd'] = ev.get('cwd', '')
            meta['preset'] = ev.get('agentPreset', '')
        elif t == 'session/title':
            meta['title'] = (ev.get('data') or {}).get('title') or meta.get('title')
        elif t == 'turn/end':
            meta['updated_at'] = ev.get('time', 0) / 1000.0
            turns += 1
        elif t == 'turn/start':
            pass
        elif t == 'tool/call':
            tool_calls += 1
        elif t == 'user/message':
            if ((ev.get('data') or {}).get('source') or {}).get('kind', 'user') == 'user':
                user_msgs += 1
        elif t == 'assistant/message':
            usage['assistant_messages'] += 1
            u = (ev.get('data') or {}).get('usage') or {}
            # 权威用量：每条最终助手消息的 usage（非流式重复值）
            usage['input_tokens'] += int(u.get('inputTokens') or 0)
            usage['output_tokens'] += int(u.get('outputTokens') or 0)
            usage['cache_read_tokens'] += int(u.get('cacheReadTokens') or 0)
            usage['total_tokens'] += int(u.get('totalTokens') or 0)
    # totalTokens 偶尔缺失：用 input+output+cache 兜底
    if not usage['total_tokens']:
        usage['total_tokens'] = (usage['input_tokens'] + usage['output_tokens']
                                 + usage['cache_read_tokens'])
    meta['usage'] = usage
    meta['turns'] = turns
    meta['tool_calls'] = tool_calls
    meta['user_msgs'] = user_msgs
    meta.setdefault('updated_at', meta['mtime'])
    meta.setdefault('title', '')
    return meta, events


def _text_of(content):
    """content[] → 纯文本"""
    if isinstance(content, str):
        return content
    out = []
    for part in content or []:
        if isinstance(part, dict) and part.get('type') == 'text':
            out.append(part.get('text', ''))
    return '\n'.join(out)


def _assistant_text(ev):
    """assistant/attempt 事件 → 文本（成功拼 chunk，失败给错误说明）"""
    data = ev.get('data') or {}
    chunks = data.get('stream') or []
    texts, errors = [], []
    for ch in chunks:
        if not isinstance(ch, dict):
            continue
        c = ch.get('chunk') or {}
        ct = c.get('type')
        if ct in ('text', 'delta'):
            t = c.get('text') or c.get('delta')
            if isinstance(t, dict):
                t = _text_of(t.get('content'))
            if t:
                texts.append(t)
        elif ct == 'finish':
            reason = c.get('reason') or {}
            if reason.get('kind') == 'error':
                f = reason.get('failure') or {}
                errors.append(f.get('message') or '未知错误')
    body = '\n'.join(texts).strip()
    if not body and errors:
        body = '（本轮未产出内容：' + '；'.join(dict.fromkeys(errors)) + '）'
    return body


def export_markdown(meta, events):
    """事件流 → 可读 Markdown（用户/助手对话为主干，系统注入折叠为一行说明）"""
    lines = ['# {0}'.format(meta.get('title') or meta['id']), '']
    lines.append('- 会话 ID：`{0}`'.format(meta['id']))
    if meta.get('cwd'):
        lines.append('- 工作目录：`{0}`'.format(meta['cwd']))
    if meta.get('created_at'):
        lines.append('- 创建：{0}'.format(time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(meta['created_at']))))
    lines.append('')
    for ev in events:
        t = ev.get('type')
        d = ev.get('data') or {}
        ts = time.strftime('%H:%M:%S', time.localtime((ev.get('time') or 0) / 1000.0)) if ev.get('time') else ''
        if t == 'user/message':
            src = (d.get('source') or {}).get('kind', 'user')
            text = _text_of(d.get('content'))
            if not text.strip():
                continue
            if src != 'user':
                lines.append('> <sub>{0} 插件注入（{1}，已折叠，{2} 字符）</sub>'.format(ts, src, len(text)))
            else:
                lines += ['## 👤 用户 <sub>{0}</sub>'.format(ts), '', text, '']
        elif t == 'assistant/attempt':
            text = _assistant_text(ev)
            if text.strip():
                lines += ['## 🤖 助手 <sub>{0}</sub>'.format(ts), '', text, '']
        elif t == 'system/message':
            lines.append('> <sub>{0} 系统上下文注入（已折叠，{1} 字符）</sub>'.format(
                ts, len(_text_of((d.get('message') or {}).get('content')))))
    return '\n'.join(lines)


# ---------------- 对外 API ----------------
def _find_session_dir(sid, cfg=None):
    root = sessions_root(cfg)
    if not root:
        return None
    hits = glob.glob(os.path.join(root, '*', sid))
    return hits[0] if hits else None


def conversation_turns(events):
    """事件流 → 结构化对话序列 [{role, time, text}]（系统/插件注入不计入）"""
    turns = []
    for ev in events:
        t = ev.get('type')
        d = ev.get('data') or {}
        ts = (ev.get('time') or 0) / 1000.0
        if t == 'user/message':
            if (d.get('source') or {}).get('kind', 'user') != 'user':
                continue
            text = _text_of(d.get('content'))
            if text.strip():
                turns.append({'role': 'user', 'time': ts, 'text': text})
        elif t == 'assistant/message':
            text = _text_of((d.get('message') or {}).get('content')) or _assistant_text(ev)
            if text.strip():
                turns.append({'role': 'assistant', 'time': ts, 'text': text})
    return turns


def _snippet(text, ql, radius=48):
    """在 text 中定位首个命中，返回带上下文的片段与命中区间（相对片段）"""
    low = text.lower()
    pos = low.find(ql)
    if pos < 0:
        return None
    start = max(0, pos - radius)
    end = min(len(text), pos + len(ql) + radius)
    snippet = ('…' if start > 0 else '') + text[start:end].replace('\n', ' ').strip() + ('…' if end < len(text) else '')
    return {'snippet': snippet, 'match_start': pos - start + (1 if start > 0 else 0),
            'match_len': len(ql)}


def search_sessions(query, cfg=None, per_session=6, limit=30):
    """跨会话全文搜索（大小写不敏感，匹配用户/助手正文）。"""
    q = (query or '').strip()
    if not q:
        return {'query': query, 'total': 0, 'results': []}
    ql = q.lower()
    root = sessions_root(cfg)
    if not root or not os.path.isdir(root):
        raise RuntimeError('未找到会话目录')
    results, total = [], 0
    for dir_path in glob.glob(os.path.join(root, '*', 'session-*')):
        if not os.path.isdir(dir_path):
            continue
        try:
            meta, events = _parse_session(dir_path)
        except Exception:
            continue
        if not meta:
            continue
        hits = []
        for turn in conversation_turns(events):
            snip = _snippet(turn['text'], ql)
            if snip and len(hits) < per_session:
                hits.append({'role': turn['role'], 'time': turn['time'], **snip})
        if hits:
            meta['project'] = os.path.basename(os.path.dirname(dir_path))
            results.append({'id': meta['id'], 'title': meta.get('title', ''),
                            'project': meta['project'], 'updated_at': meta.get('updated_at'),
                            'matches': hits})
            total += len(hits)
        if len(results) >= limit:
            break
    results.sort(key=lambda r: r.get('updated_at') or 0, reverse=True)
    return {'query': q, 'total': total, 'results': results}


def session_detail(sid, cfg=None):
    """单个会话：元数据 + 对话序列（供面板预览/对比）"""
    d = _find_session_dir(sid, cfg)
    if not d:
        return None
    parsed = _parse_session(d)
    if not parsed:
        return None
    meta, events = parsed
    meta['project'] = os.path.basename(os.path.dirname(d))
    meta.pop('file', None)
    return {'meta': meta, 'turns': conversation_turns(events)}


def compare_sessions(ids, cfg=None):
    """多个会话的规模/用量横向对比。"""
    rows = []
    for sid in ids:
        d = _find_session_dir(sid, cfg)
        if not d:
            rows.append({'id': sid, 'missing': True})
            continue
        try:
            meta, _ = _parse_session(d)
        except Exception:
            rows.append({'id': sid, 'missing': True})
            continue
        if not meta:
            continue
        meta.pop('file', None)
        dur = 0
        if meta.get('created_at') and meta.get('updated_at'):
            dur = max(0, int(meta['updated_at'] - meta['created_at']))
        u = meta.get('usage', {})
        rows.append({'id': meta['id'], 'title': meta.get('title', ''),
                     'project': os.path.basename(os.path.dirname(d)),
                     'turns': meta.get('turns', 0), 'user_msgs': meta.get('user_msgs', 0),
                     'tool_calls': meta.get('tool_calls', 0),
                     'duration_sec': dur,
                     'input_tokens': u.get('input_tokens', 0),
                     'output_tokens': u.get('output_tokens', 0),
                     'cache_read_tokens': u.get('cache_read_tokens', 0),
                     'total_tokens': u.get('total_tokens', 0),
                     'created_at': meta.get('created_at'), 'updated_at': meta.get('updated_at')})
    # 合计行
    valid = [r for r in rows if not r.get('missing')]
    summary = {
        'count': len(valid),
        'total_tokens': sum(r['total_tokens'] for r in valid),
        'input_tokens': sum(r['input_tokens'] for r in valid),
        'output_tokens': sum(r['output_tokens'] for r in valid),
        'tool_calls': sum(r['tool_calls'] for r in valid),
        'duration_sec': sum(r['duration_sec'] for r in valid),
    }
    return {'rows': rows, 'summary': summary}


def list_sessions(cfg=None):
    """列出全部会话（按更新时间倒序）。zstandard 缺失时退化为文件名级列表"""
    root = sessions_root(cfg)
    if not root or not os.path.isdir(root):
        raise RuntimeError('未找到会话目录（DSH home 未解析或尚未产生会话）')
    out = []
    degraded = False
    for dir_path in glob.glob(os.path.join(root, '*', 'session-*')):
        if not os.path.isdir(dir_path):
            continue
        try:
            meta, _ = _parse_session(dir_path)
        except RuntimeError:
            degraded = True
            meta = {"id": os.path.basename(dir_path), "title": '',
                    "mtime": os.path.getmtime(dir_path), "updated_at": os.path.getmtime(dir_path)}
        if not meta:
            continue
        meta['project'] = os.path.basename(os.path.dirname(dir_path))
        out.append(meta)
    out.sort(key=lambda m: m.get('updated_at') or 0, reverse=True)
    for m in out:
        m.pop('file', None)
    if degraded:
        out.append({"id": "_degraded", "title": '（部分会话未能解析：缺 zstandard 依赖，仅显示文件级信息）'})
    return out


# ---------------- Token 用量聚合（内置版 token-usage 数据源） ----------------
def _is_count(value):
    """与官方 isCount 一致：非负安全整数（json 解析后为 int）"""
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _stream_usage(stream):
    """assistant/message 流中最后一个 {type:'usage', usage}（反向查找，对齐插件）"""
    if not isinstance(stream, list):
        return None
    for entry in reversed(stream):
        if isinstance(entry, dict):
            chunk = entry.get('chunk')
            if isinstance(chunk, dict) and chunk.get('type') == 'usage' and isinstance(chunk.get('usage'), dict):
                return chunk['usage']
    return None


def _event_usage(data):
    """优先 data.usage，缺失回退流末尾 usage chunk（对齐官方 deriveTurnTokenUsage）"""
    if not isinstance(data, dict):
        return None
    usage = data.get('usage')
    if isinstance(usage, dict):
        return usage
    return _stream_usage(data.get('stream'))


def _usage_tokens(usage):
    """usage → tokens：优先 provider totalTokens（input+output <= total 时），否则四桶求和兜底"""
    inp = usage.get('inputTokens')
    out = usage.get('outputTokens')
    total = usage.get('totalTokens')
    if _is_count(total) and (inp or 0) + (out or 0) <= total:
        return total
    return max(0, (inp or 0) + (usage.get('cacheReadTokens') or 0)
               + (usage.get('cacheWriteTokens') or 0) + (out or 0))


def _event_route(data):
    """assistant/message 的 data.message.source.{provider, model} → 元组或 None"""
    if not isinstance(data, dict):
        return None
    msg = data.get('message')
    if not isinstance(msg, dict):
        return None
    src = msg.get('source')
    if not isinstance(src, dict):
        return None
    provider = src.get('provider')
    model = src.get('model')
    return (str(provider), str(model)) if provider and model else None


def aggregate_token_usage(cfg=None):
    """跨会话/跨目录聚合 Token 用量（token-usage 仪表盘内置数据源，纯只读）。

    口径与官方 deriveTurnTokenUsage 对齐（见插件 host 半）：
      - turn/start → turn/end 时间差为回合时长，maxTurnMs 取最大
      - assistant/message 用量：data.usage 或流末尾 usage chunk；tokens 优先
        provider totalTokens，否则 input+output+cacheRead+cacheWrite 兜底
      - 归因 data.message.source.{provider, model} → 模型调用次数 / 模型 Token /
        日×模型（dayModels）；无归因仍计入总量/热力图/时段
    events.time 为毫秒时间戳；任一会话解压/解析失败仅跳过，不中断整体统计。
    返回字段与插件 /api/usage 载荷一致（JSON 可序列化）。
    """
    root = sessions_root(cfg)
    if not root or not os.path.isdir(root):
        raise RuntimeError('未找到会话目录（DSH home 未解析或尚未产生会话）')
    now_l = time.localtime()
    days = {}          # 'YYYY-MM-DD' -> tokens
    hours = [0] * 24   # 全历史按本地小时
    hours_today = [0] * 24  # 仅今日（本地日历日）
    models = {}        # 'provider/model' -> 调用次数
    model_tokens = {}  # 'provider/model' -> tokens
    day_models = {}    # 'YYYY-MM-DD' -> {'provider/model': tokens}
    total_tokens = 0
    turns = 0
    max_turn_ms = 0
    first_used_at = None
    last_used_at = None

    for dir_path in glob.glob(os.path.join(root, '*', 'session-*')):
        if not os.path.isdir(dir_path):
            continue
        ev_file = None
        try:
            for fn in sorted(os.listdir(dir_path)):
                if _SESSION_RE.match(fn) and (fn.endswith('.zstd') or fn.endswith('.jsonl')):
                    ev_file = os.path.join(dir_path, fn)
                    break
        except OSError:
            continue
        if not ev_file:
            continue
        turn_start = None
        try:
            for ev in iter_events(ev_file):
                et = ev.get('time')
                if not isinstance(et, (int, float)):
                    continue
                t = ev.get('type')
                if t == 'turn/start':
                    turn_start = et
                    continue
                if t == 'turn/end':
                    turns += 1
                    if turn_start is not None and et > turn_start and (et - turn_start) > max_turn_ms:
                        max_turn_ms = et - turn_start
                    turn_start = None
                    continue
                if t != 'assistant/message':
                    continue
                data = ev.get('data') or {}
                if first_used_at is None or et < first_used_at:
                    first_used_at = et
                if last_used_at is None or et > last_used_at:
                    last_used_at = et
                usage = _event_usage(data)
                if not usage or not _is_count(usage.get('inputTokens')) or not _is_count(usage.get('outputTokens')):
                    continue
                tokens = _usage_tokens(usage)
                if tokens <= 0:
                    continue
                total_tokens += tokens
                lt = time.localtime(et / 1000.0)
                day = time.strftime('%Y-%m-%d', lt)
                days[day] = days.get(day, 0) + tokens
                hours[lt.tm_hour] += tokens
                if lt.tm_year == now_l.tm_year and lt.tm_mon == now_l.tm_mon and lt.tm_mday == now_l.tm_mday:
                    hours_today[lt.tm_hour] += tokens
                route = _event_route(data)
                if route:
                    key = ('%s/%s' % route) if route[0] else route[1]
                    models[key] = models.get(key, 0) + 1
                    model_tokens[key] = model_tokens.get(key, 0) + tokens
                    dm = day_models.get(day)
                    if dm is None:
                        dm = day_models[day] = {}
                    dm[key] = dm.get(key, 0) + tokens
        except Exception:
            continue  # 单会话损坏/依赖缺失只跳过，不中断整体统计

    return {
        'ok': True,
        'computedAt': int(time.time() * 1000),
        'totalTokens': total_tokens,
        'turns': turns,
        'maxTurnMs': max_turn_ms,
        'firstUsedAt': first_used_at,
        'lastUsedAt': last_used_at,
        'days': days,
        'hours': hours,
        'hoursToday': hours_today,
        'models': models,
        'modelTokens': model_tokens,
        'dayModels': day_models,
    }


def export_session(sid, fmt='md', cfg=None):
    """导出会话 → ~/.dsh-skins/exports/，返回文件路径；失败/为空返回 None"""
    root = sessions_root(cfg)
    if not root:
        return None
    dir_path = os.path.join(root, '*', sid)
    hits = glob.glob(dir_path)
    if not hits:
        return None
    parsed = _parse_session(hits[0])
    if not parsed:
        return None
    meta, events = parsed
    os.makedirs(EXPORT_DIR, exist_ok=True)
    stamp = time.strftime('%Y%m%d-%H%M%S')
    if fmt == 'json':
        out = os.path.join(EXPORT_DIR, '{0}-{1}.json'.format(sid, stamp))
        payload = {"meta": {k: v for k, v in meta.items()}, "events": events}
        with io.open(out, 'w', encoding='utf-8') as f:
            json.dump(payload, f, ensure_ascii=False, indent=1)
    else:
        base = '{0}-{1}'.format(meta.get('title') or sid, stamp)
        base = re.sub(r'[\\/:*?"<>|]', '_', base).strip('. ') or sid
        out = os.path.join(EXPORT_DIR, base + '.md')
        with io.open(out, 'w', encoding='utf-8') as f:
            f.write(export_markdown(meta, events))
    return out


def backup_all(cfg=None):
    """把 home/sessions 整树打包到 ~/.dsh-skins/backups/，返回 zip 路径"""
    root = sessions_root(cfg)
    if not root or not os.path.isdir(root):
        raise RuntimeError('未找到会话目录')
    os.makedirs(BACKUP_DIR, exist_ok=True)
    out = os.path.join(BACKUP_DIR, 'sessions-{0}.zip'.format(time.strftime('%Y%m%d-%H%M%S')))
    with zipfile.ZipFile(out, 'w', zipfile.ZIP_DEFLATED) as z:
        for fp in glob.glob(os.path.join(root, '**', '*'), recursive=True):
            if os.path.isfile(fp):
                z.write(fp, os.path.relpath(fp, root))
    return out


def delete_sessions(ids, cfg=None):
    """物理删除会话（显式用户授权）：先把受影响会话目录整体复制到备份，
    复制成功后才删除原目录；返回 {deleted, missing, backup}。

    备份落在 ~/.dsh-skins/backups/delete-<ts>/，保留 project/session-<id> 相对
    结构，可整目录还原。全程不动其它会话；备份失败即中止，不删任何数据。
    """
    if not ids:
        raise ValueError('未指定要删除的会话')
    root = sessions_root(cfg)
    if not root or not os.path.isdir(root):
        raise RuntimeError('未找到会话目录')
    os.makedirs(BACKUP_DIR, exist_ok=True)
    backup_root = os.path.join(BACKUP_DIR, 'delete-' + time.strftime('%Y%m%d-%H%M%S'))
    os.makedirs(backup_root, exist_ok=True)
    deleted, missing = [], []
    safe_root = root.rstrip(os.sep) + os.sep
    for sid in ids:
        sid = (sid or '').strip()
        # 防路径穿越：只接受纯「session-<uuid>」名字
        if not sid or sid != os.path.basename(sid) or sid == '..' or os.path.isabs(sid):
            missing.append(sid)
            continue
        d = _find_session_dir(sid, cfg)
        if not d or not os.path.isdir(d) or not d.startswith(safe_root):
            missing.append(sid)
            continue
        rel = os.path.relpath(d, root)
        dst = os.path.join(backup_root, rel)
        try:
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            shutil.copytree(d, dst)          # 先完整备份
            shutil.rmtree(d)                 # 备份成功后物理删除
        except Exception:
            shutil.rmtree(dst, ignore_errors=True)   # 备份不完整则回滚备份
            raise
        deleted.append(sid)
    if not deleted:
        shutil.rmtree(backup_root, ignore_errors=True)
        raise RuntimeError('没有可删除的会话' + ('（不存在: ' + '、'.join(missing) + '）' if missing else ''))
    return {'deleted': deleted, 'missing': missing, 'backup': backup_root}


def backup_credentials(cfg=None):
    """备份 .credentials.yaml + settings.yaml → backups/credentials-<ts>/"""
    h = home(cfg)
    if not h:
        raise RuntimeError('未找到 DSH home')
    files = [os.path.join(h, '.credentials.yaml'), os.path.join(h, 'settings.yaml')]
    found = [f for f in files if os.path.isfile(f)]
    if not found:
        raise RuntimeError('home 下没有凭证文件（可能尚未登录/写入）')
    dest = os.path.join(BACKUP_DIR, 'credentials-' + time.strftime('%Y%m%d-%H%M%S'))
    os.makedirs(dest, exist_ok=True)
    for f in found:
        shutil.copy2(f, os.path.join(dest, os.path.basename(f)))
    return dest


def save_model_config(cfg=None, provider='', model='', context_window=None,
                      max_tokens=None, vision=None, reasoning=None,
                      model_reasoning=None, enabled=None):
    """修改 settings.yaml：单个模型能力（contextWindow/maxTokens/input）与
    agent-default-model 思考模式（reasoningEffort）。
    行级手术只改目标行，保留注释/锚点/其它命名空间；写前备份 + 原子替换。
    - context_window / max_tokens: int 或 None(不改)
    - vision: True/False/None(不改)
    - reasoning: 'default'|'off'|'low'|'high'|'max' 或 None(不改)
    返回 (ok, msg)。"""
    h = home(cfg)
    if not h:
        return False, '未找到 DSH home'
    sf = os.path.join(h, 'settings.yaml')
    if not os.path.isfile(sf):
        return False, 'settings.yaml 不存在: ' + sf
    import re as _re
    try:
        with io.open(sf, encoding='utf-8') as f:
            raw = f.read()
        nl = '\r\n' if '\r\n' in raw else '\n'
        lines = raw.splitlines(keepends=True)
    except Exception as e:
        return False, '读取 settings.yaml 失败: {0}'.format(e)

    def _indent(s):
        return len(s) - len(s.lstrip(' '))

    def _body(s):
        return s.rstrip('\r\n').split('#', 1)[0].strip()

    n = len(lines)
    changed = []

    # ---- 1. 定位 provider.models[] 中的目标模型块 ----
    block = None
    i_llm = i_prov = i_pid = i_models = None
    for i in range(n):
        b = _body(lines[i])
        if not b:
            continue
        if i_llm is None and b == 'llm-pi-ai:':
            i_llm = i
        elif i_llm is not None and i_prov is None and _indent(lines[i]) == 2 and b == 'providers:':
            i_prov = i
        elif i_prov is not None and i_pid is None and _indent(lines[i]) == 4 and b == provider + ':':
            i_pid = i
        elif i_pid is not None and i_models is None and _indent(lines[i]) == 6 and b == 'models:':
            i_models = i
        elif i_models is not None and _indent(lines[i]) == 8 and b.startswith('- id:'):
            if b[len('- id:'):].strip() == model:
                block = i
                break
    if block is None:
        return False, '未找到模型 {0}/{1}（settings.yaml 模型表）'.format(provider, model)

    end = n
    for j in range(block + 1, n):
        bj = _body(lines[j])
        if not bj:
            continue
        if _indent(lines[j]) < 8 or (_indent(lines[j]) == 8 and bj.startswith('- ')):
            end = j
            break

    def _set_field(field, newval, raw=False):
        for k in range(block, end):
            lk = lines[k].rstrip('\r\n')
            if _re.match(r'^\s*' + re.escape(field) + r':', lk):
                if raw:
                    m = _re.match(r'^(\s*' + re.escape(field) + r':)\s*\[.*?\](.*)$', lk)
                    if m:
                        lines[k] = m.group(1) + ' ' + newval + m.group(2) + nl
                        return True
                    return False
                m = _re.match(r'^(\s*' + re.escape(field) + r':)\s*\S*(.*)$', lk)
                if m:
                    lines[k] = m.group(1) + ' ' + str(newval) + m.group(2) + nl
                    return True
                return False
        lines[end:end] = [' ' * 10 + field + ': ' + str(newval) + nl]
        return True

    if context_window is not None:
        if not _set_field('contextWindow', int(context_window)):
            return False, '模型字段 contextWindow 行格式异常'
        changed.append('contextWindow')
    if max_tokens is not None:
        if not _set_field('maxTokens', int(max_tokens)):
            return False, '模型字段 maxTokens 行格式异常'
        changed.append('maxTokens')
    if vision is not None:
        if not _set_field('input', '[ text, image ]' if vision else '[ text ]', raw=True):
            return False, '模型字段 input 行格式异常'
        changed.append('input')

    # ---- 1.5 模型级思考模式 reasoningEffort / 启用开关 enabled ----
    if model_reasoning is not None:
        vals = ('off', 'low', 'high', 'max')
        if model_reasoning != 'default' and model_reasoning not in vals:
            return False, '模型思考模式取值非法: {0}（应为 default/off/low/high/max）'.format(model_reasoning)
        if model_reasoning == 'default':
            for k in range(block, end):
                if _re.match(r'^\s*reasoningEffort:', lines[k].rstrip('\r\n')):
                    del lines[k]
                    changed.append('reasoningEffort(跟随默认)')
                    break
        else:
            if not _set_field('reasoningEffort', model_reasoning):
                return False, '模型字段 reasoningEffort 行格式异常'
            changed.append('reasoningEffort')
    if enabled is not None:
        if not _set_field('enabled', 'true' if enabled else 'false'):
            return False, '模型字段 enabled 行格式异常'
        changed.append('enabled')

    # ---- 2. agent-default-model.reasoningEffort ----
    if reasoning is not None:
        vals = ('off', 'low', 'high', 'max')
        if reasoning != 'default' and reasoning not in vals:
            return False, 'reasoningEffort 取值非法: {0}（应为 default/off/low/high/max）'.format(reasoning)
        i_adm = None
        for k in range(n):
            if _body(lines[k]) == 'agent-default-model:':
                i_adm = k
                break
        if i_adm is None:
            return False, 'settings.yaml 缺少 agent-default-model 节'
        a_end = n
        for j in range(i_adm + 1, n):
            bj = _body(lines[j])
            if not bj:
                continue
            if _indent(lines[j]) < 2:
                a_end = j
                break
        re_line = None
        for k in range(i_adm + 1, a_end):
            if _re.match(r'^\s*reasoningEffort:', lines[k].rstrip('\r\n')):
                re_line = k
                break
        if reasoning == 'default':
            if re_line is not None:
                del lines[re_line]
                changed.append('reasoningEffort(跟随默认)')
        else:
            if re_line is not None:
                m = _re.match(r'^(\s*reasoningEffort:)\s*\S*(.*)$', lines[re_line].rstrip('\r\n'))
                if m:
                    lines[re_line] = m.group(1) + ' ' + reasoning + m.group(2) + nl
                    changed.append('reasoningEffort')
            else:
                ins = i_adm + 1
                for k in range(i_adm + 1, a_end):
                    if _re.match(r'^\s*model:', lines[k].rstrip('\r\n')):
                        ins = k + 1
                        break
                lines[ins:ins] = ['  reasoningEffort: ' + reasoning + nl]
                changed.append('reasoningEffort')

    if not changed:
        return True, '无变更（参数与当前配置一致）'
    try:
        backup_credentials(cfg)
    except Exception as e:
        return False, '写前备份失败，已中止: {0}'.format(e)
    tmp = sf + '.tmp'
    try:
        with io.open(tmp, 'w', encoding='utf-8', newline='') as f:
            f.writelines(lines)
        os.replace(tmp, sf)
    except Exception as e:
        try:
            if os.path.exists(tmp):
                os.remove(tmp)
        except Exception:
            pass
        return False, '写入 settings.yaml 失败: {0}'.format(e)
    return True, '已更新: ' + ', '.join(sorted(set(changed)))


def _tonum_mini(s):
    """极简 YAML 标量转类型：int → float → 原样（null 处理）。"""
    v = (s or '').strip()
    if v in ('', 'null', '~', 'None'):
        return None
    try:
        return int(v)
    except ValueError:
        pass
    try:
        return float(v)
    except ValueError:
        pass
    low = v.lower()
    if low == 'true':
        return True
    if low == 'false':
        return False
    return v


def _parse_llm_pi_ai_mini(raw):
    """PyYAML 缺失时的定向解析：仅提取 settings.yaml 的
    llm-pi-ai.providers.<pid>.{models[],displayName,apiKeyEnv,api,baseURL} 与
    agent-default-model 节。返回 (providers_list, default_model) 或 (None, None)。"""
    import re as _re
    lines = raw.split('\n')
    provs = []
    dm = {}
    cur = None          # 当前 provider dict
    cm = None           # 当前 model dict
    in_adm = False      # 是否在 agent-default-model 段

    def _body(s):
        return s.split('#', 1)[0].rstrip()

    def _kv(t):
        m = _re.match(r'^([A-Za-z][\w-]*):\s*(.*)$', t)
        return (m.group(1), m.group(2).strip()) if m else (None, None)

    for ln in lines:
        b = _body(ln)
        if not b.strip():
            continue
        indent = len(b) - len(b.lstrip(' '))
        t = b.strip()
        if indent == 0:
            if t == 'agent-default-model:':
                dm = {}
                in_adm, cur, cm = True, None, None
                continue
            if t == 'llm-pi-ai:':
                in_adm, cur, cm = False, None, None
                continue
            if in_adm:
                k, v = _kv(t)
                if k:
                    dm[k] = _tonum_mini(v)
            in_adm, cur, cm = False, None, None
            continue
        if indent <= 2 and t == 'providers:':
            continue
        if in_adm and indent == 2:
            k, v = _kv(t)
            if k:
                dm[k] = _tonum_mini(v)
            continue
        if indent == 4 and t.endswith(':'):
            pid = t[:-1].strip()
            cur = {'id': pid, 'displayName': pid, 'apiKeyEnv': '', 'api': '', 'baseURL': '', 'models': []}
            provs.append(cur)
            cm = None
            continue
        if cur is None:
            continue
        if indent == 6:
            k, v = _kv(t)
            if k == 'models':
                pass
            elif k in ('displayName', 'apiKeyEnv', 'api', 'baseURL'):
                cur[k] = _tonum_mini(v)
            continue
        if indent == 8 and t.startswith('- id:'):
            mid = t[len('- id:'):].strip().strip('"\'')
            cm = {'id': mid, 'name': mid, 'contextWindow': None, 'maxTokens': None, 'input': []}
            cur['models'].append(cm)
            continue
        if indent == 10 and cm is not None:
            k, v = _kv(t)
            if k == 'name':
                cm['name'] = v.strip('"\'')
            elif k in ('contextWindow', 'maxTokens'):
                cm[k] = _tonum_mini(v)
            elif k == 'input':
                inner = v.strip().strip('[]').strip()
                cm['input'] = [p.strip().strip('"\'') for p in inner.split(',')] if inner else []
            elif k == 'reasoningEffort':
                cm['reasoningEffort'] = v.strip('"\'')
            elif k == 'enabled':
                cm['enabled'] = _tonum_mini(v)
            continue
    return provs, dm


def _settings_doc(cfg, h):
    """读 settings.yaml 原始文本（供解析用）。"""
    sf = os.path.join(h, 'settings.yaml')
    if not os.path.isfile(sf):
        return None, None
    try:
        with io.open(sf, encoding='utf-8') as f:
            return sf, f.read()
    except Exception:
        return sf, None


def providers_info(cfg=None):
    """凭证状态（密钥打码）。来源标注 env / credentials 文件 / 未设置；
    顺带读 settings.yaml 的 llm-deepseek 节（Models 设置页写入处，热重载）"""
    info = {"route": 'deepseek-official', "api_key": '', "source": '', "last_backup": ''}
    env_key = os.environ.get('DEEPSEEK_API_KEY')
    cred = credentials_path(cfg)
    if cred and os.path.isfile(cred):
        try:
            import yaml
            with io.open(cred, encoding='utf-8') as f:
                data = yaml.safe_load(f) or {}
            records = data.get('records') or {}
            info['records'] = sorted(records.keys())
            grants = [k for k, v in records.items()
                      if isinstance(v, dict) and v.get('kind') == 'grant']
            if grants:
                info['source'] = 'credentials 文件（' + '、'.join(grants) + '）'
        except ImportError:
            info['source'] = 'credentials 文件存在（读取需 PyYAML）'
        except Exception as e:
            info['source'] = 'credentials 读取失败: {0}'.format(e)
    if env_key:
        info['api_key'] = env_key
        info['source'] = (info['source'] + ' + ' if info['source'] else '') + '环境变量 DEEPSEEK_API_KEY'
    if not info['source']:
        info['source'] = '未设置（会话推理会报 MISSING_CREDENTIAL）'
    # settings.yaml 的 llm-deepseek 节 = Models 页写入处（热重载）
    h = home(cfg)
    settings_file = os.path.join(h, 'settings.yaml') if h else None
    if settings_file and os.path.isfile(settings_file):
        try:
            import yaml
            with io.open(settings_file, encoding='utf-8') as f:
                st = yaml.safe_load(f) or {}
            ns = st.get('llm-deepseek') or {}
            if ns:
                info['llm_settings'] = {k: ns[k] for k in sorted(ns) if k != 'credential-ref'}
                if ns.get('baseUrl'):
                    info['route'] = str(ns['baseUrl'])
            pi = st.get('llm-pi-ai') or {}
            provs = pi.get('providers') or {}
            dm = st.get('agent-default-model') or {}
            if not isinstance(provs, dict) or not isinstance(dm, dict):
                raise ValueError('yaml 结构异常，降级 mini 解析重试')
            info['settings_source'] = 'yaml'
        except Exception:
            # PyYAML 缺失/损坏 → 纯文本定向解析（零依赖）
            st = None
            provs = {}
            dm = {}
            try:
                with io.open(settings_file, encoding='utf-8') as f:
                    raw = f.read()
                provs, dm = _parse_llm_pi_ai_mini(raw)
                provs = {p['id']: p for p in provs}
                info['settings_source'] = 'mini（无 PyYAML）'
            except Exception:
                info['settings_source'] = '解析失败'
                provs, dm = {}, {}
            # 打包版/生产数据根：llm-pi-ai.providers（供应商表）+ agent-default-model（默认模型）
        if isinstance(provs, dict):
            prov_list = []
            for pid, pv in sorted(provs.items()):
                if not isinstance(pv, dict):
                    continue
                models = pv.get('models')
                env_name = pv.get('apiKeyEnv') or ''
                prov_detail = {
                    'id': pid,
                    'displayName': pv.get('displayName') or pid,
                    'baseURL': pv.get('baseURL') or '',
                    'api': pv.get('api') or '',
                    'apiKeyEnv': env_name,
                    'apiKeyEnvSet': bool(env_name and os.environ.get(env_name)),
                    'models': len(models) if isinstance(models, list) else 0,
                }
                # 模型明细（配置中心 llm-pi-ai.providers[] 语义见页内注释）
                model_list = []
                if isinstance(models, list):
                    for m in models:
                        if not isinstance(m, dict):
                            continue
                        model_list.append({
                            'id': m.get('id') or '',
                            'name': m.get('name') or m.get('id') or '',
                            'contextWindow': m.get('contextWindow'),
                            'maxTokens': m.get('maxTokens'),
                            'input': m.get('input') if isinstance(m.get('input'), list) else [],
                            'reasoningEffort': m.get('reasoningEffort'),
                            'enabled': m.get('enabled'),
                        })
                if model_list:
                    prov_detail['model_list'] = model_list
                prov_list.append(prov_detail)
            if prov_list:
                info['providers'] = prov_list
        if isinstance(dm, dict) and dm.get('provider'):
            # 只在实际提供 reasoningEffort 时才带上该键：yaml 与 mini 两条解析路径
            # 结构保持一致（否则 yaml 路径会多出 reasoningEffort: None，前端与
            # 测试都按缺省处理，无谓地让两个来源形状不同）。
            default_model = {'provider': dm.get('provider'),
                             'model': dm.get('model') or ''}
            if dm.get('reasoningEffort') is not None:
                default_model['reasoningEffort'] = dm.get('reasoningEffort')
            info['default_model'] = default_model
    try:
        backups = sorted(glob.glob(os.path.join(BACKUP_DIR, 'credentials-*')), reverse=True)
        if backups:
            info['last_backup'] = time.strftime(
                '%Y-%m-%d %H:%M', time.localtime(os.path.getmtime(backups[0])))
    except Exception:
        pass
    return info
