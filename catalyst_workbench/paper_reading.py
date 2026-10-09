"""Read-only Chinese reading aids, stored outside all scientific records.

Model inference runs in a separate local process. Importing this module never
loads the model or starts a download. Cache keys bind the exact original quote,
source PDF fingerprint, evidence id, and translator version.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import queue
import re
import subprocess
import sys
import threading
import uuid

from workbench_paths import translation_root
from paper_glossary import terms_for_text, GLOSSARY_VERSION
from paper_translation_hints import reading_hints
from paper_translation_worker import ENGINE_VERSION


CACHE_SCHEMA = 'paper-reading-aid/1.0'


def runtime_path():
    return translation_root()


def engine_ready():
    root = runtime_path()
    return all((root / p).exists() for p in [
        'packages/ctranslate2', 'packages/sentencepiece',
        'models/translate-en_zh-1_9/model/model.bin',
        'models/translate-en_zh-1_9/sentencepiece.model'])


def source_key(project, evidence):
    fields = [project.get('article', {}).get('source_sha256', ''),
        evidence.get('evidence_id', ''), evidence.get('quote', ''), ENGINE_VERSION]
    return hashlib.sha256(json.dumps(fields, ensure_ascii=False).encode('utf8')).hexdigest()


def request_key(project, evidence):
    return str(Path(project['run_dir']).resolve()), source_key(project, evidence)


def cache_path(project, evidence):
    return Path(project['run_dir']) / '中文阅读备注' / (source_key(project, evidence) + '.json')


def cached_reading(project, evidence):
    path = cache_path(project, evidence)
    try:
        result = json.loads(path.read_text(encoding='utf8'))
        if (result.get('schema_version') != CACHE_SCHEMA or
            result.get('source_key') != source_key(project, evidence) or
            result.get('original') != evidence.get('quote', '') or
            result.get('engine') != ENGINE_VERSION or
            result.get('source_sha256') != project.get('article', {}).get('source_sha256', '') or
            not isinstance(result.get('translation'), str) or not result['translation'].strip()):
            return None
        return result
    except (OSError, ValueError, TypeError, AttributeError):
        return None


def save_reading(project, evidence, result):
    if not result.get('translation', '').strip():
        raise ValueError('不能保存空译文。')
    value = {**result, 'schema_version': CACHE_SCHEMA,
        'source_key': source_key(project, evidence),
        'original': evidence.get('quote', ''), 'evidence_id': evidence.get('evidence_id', ''),
        'source_sha256': project.get('article', {}).get('source_sha256', ''),
        'engine': ENGINE_VERSION, 'human_translation_reviewed': False,
        'created_at': datetime.now(timezone.utc).isoformat(),
        'usage': '中文阅读备注；不作为原始证据、实测数值或机器学习标签'}
    path = cache_path(project, evidence)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix('.' + uuid.uuid4().hex + '.tmp')
    try:
        temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf8')
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()
    return value


def translate_local(text):
    if not engine_ready():
        raise RuntimeError('尚未安装本地翻译组件。请回到整合入口点击“安装中文翻译”，完成后重开本窗口。')
    runtime = Path(sys.executable)
    if runtime.name.lower() == 'pythonw.exe':
        runtime = runtime.with_name('python.exe')
    command = [str(runtime), '-B', '-X', 'utf8',
        str(Path(__file__).with_name('paper_translation_worker.py')),
        '--runtime', str(runtime_path())]
    try:
        result = subprocess.run(command, input=json.dumps({'text': text}, ensure_ascii=False).encode('utf8'),
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=45,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError('本次本地翻译超过 45 秒，可稍后点“重试中文翻译”。') from exc
    try:
        payload = json.loads(result.stdout.decode('utf8'))
    except (ValueError, UnicodeError) as exc:
        raise RuntimeError('本地翻译进程未返回译文，原文和审核记录不受影响。') from exc
    if result.returncode or not payload.get('ok'):
        raise RuntimeError(payload.get('error') or '本地翻译未完成。')
    return payload


def reading_aid(project, evidence, transient=None):
    """Cheap read path for GUI/exports; NEVER triggers inference or changes facts."""
    quote = evidence.get('quote', '')
    result = cached_reading(project, evidence) or transient or {}
    cjk=len(re.findall(r'[\u3400-\u9fff]',quote));latin=len(re.findall(r'[A-Za-z]',quote))
    chinese=bool(cjk) and (not re.search(r'[A-Za-z]{3,}',quote) or cjk>=3 and cjk*2>=latin)
    if result.get('translation'):
        status, message = 'translated', '本机参考译文 · 未作人工翻译审校'
    elif chinese:
        status, message = 'original_chinese', '原句已是中文，无需再翻译。'
    elif not quote.strip():
        status, message = 'empty', '当前没有可翻译的原句。'
    elif result.get('status') in ('loading', 'error'):
        status, message = result['status'], result['message']
    elif not engine_ready():
        status, message = 'unavailable', '本地翻译组件未就绪；下方仍可查看术语解释。'
    else:
        status, message = 'not_translated', '选中后自动生成中文参考译文。'
    translation = result.get('translation', '')
    hints = reading_hints(quote, translation)
    if result.get('segments', 0) > 1:
        hints.append('长句已分段翻译；跨句指代与条件请连同原文核对。')
    return {'status': status, 'message': message, 'translation': translation,
        'terms': terms_for_text(quote), 'hints': hints,
        'engine': result.get('engine', ''), 'model': result.get('model', ''),
        'translation_raw':result.get('translation_raw',''),
        'terminology_adjustments':result.get('terminology_adjustments',[]),
        'created_at': result.get('created_at', ''), 'source_key': source_key(project, evidence),
        'glossary_version': GLOSSARY_VERSION, 'for_training': False}


def display_reading(aid):
    lines = ['中文参考译文（阅读备注）：', aid['translation'] or aid['message']]
    if aid['translation']:
        lines.append('〔本机机器翻译，未作人工翻译审校；科学含义以英文原文为准。〕')
    if aid['terms']:
        lines += ['', '本句术语：']
        lines += ['• ' + item['term'] + '：' + item['zh'] + '。' + item['explanation'] for item in aid['terms']]
    if aid['hints']:
        lines += ['', '对照时注意：'] + ['• ' + hint for hint in aid['hints']]
    lines.append('')
    return '\n'.join(lines)


class ReadingService:
    """One active inference plus the latest pending selection, never a big queue."""
    def __init__(self, translator=translate_local):
        self.translator = translator
        self.requests = queue.Queue(maxsize=1)
        self.completed = queue.Queue()
        self.states = {}
        self.thread = None
        self.closed = threading.Event()

    def request(self, project, evidence, force=False):
        key = request_key(project, evidence)
        if not force and (cached_reading(project, evidence) or key in self.states):
            return key
        if self.closed.is_set():
            return key
        # Take only source identity and quote. No review or training data enters worker.
        p = {'run_dir': project['run_dir'], 'article': {'source_sha256': project.get('article', {}).get('source_sha256', '')}}
        e = {'evidence_id': evidence.get('evidence_id', ''), 'quote': evidence.get('quote', '')}
        try:
            discarded = self.requests.get_nowait()
            self.states.pop(discarded[0], None)
        except queue.Empty:
            pass
        self.states[key] = {'status': 'loading', 'message': '正在本机翻译这条原句…首次加载约需几秒，可继续浏览。'}
        self.requests.put_nowait((key, p, e))
        if self.thread is None:
            self.thread = threading.Thread(target=self._work, daemon=True)
            self.thread.start()
        return key

    def _work(self):
        while not self.closed.is_set():
            try:
                key, project, evidence = self.requests.get(timeout=.3)
            except queue.Empty:
                continue
            try:
                result = self.translator(evidence['quote'])
                if self.closed.is_set():
                    return
                try:
                    result = save_reading(project, evidence, result)
                except OSError:
                    result = {**result, 'cache_warning': '译文已生成，本次未能保存阅读缓存。'}
                self.completed.put((key, result))
            except Exception as exc:
                self.completed.put((key, {'status': 'error', 'message': '本条尚未翻译：' + str(exc)}))

    def drain(self):
        changed = []
        while True:
            try:
                key, value = self.completed.get_nowait()
            except queue.Empty:
                break
            self.states[key] = value
            changed.append(key)
        return changed

    def close(self):
        self.closed.set()
