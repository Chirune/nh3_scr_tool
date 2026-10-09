"""Isolated, offline EN -> ZH inference. JSON stdin/stdout; no network code."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import sys
import unicodedata


ENGINE_VERSION = 'argos-en-zh-1.9/reading-1.1'


def prepare_text(text):
    # Only the translator's input copy changes. The quoted evidence stays exact.
    text = unicodedata.normalize('NFKC', text)
    text = re.sub(r'(?<=[A-Za-z])[-‐‑]\s*\n\s*(?=[a-z])', '', text)
    text = re.sub(r'\bNH\s+3\b', 'NH3', text)
    text = re.sub(r'\s+', ' ', text).strip()
    # In catalysis, uppercase NO is a formula, not the English negation "no".
    text = re.sub(r'(?<![A-Za-z0-9])NO(?![A-Za-z0-9₀-₉])', 'nitric oxide (NO)', text)
    return text


def normalize_terminology(original, translated):
    """Correct a small, source-triggered vocabulary; never compute new facts."""
    edits=[]
    def replace(pattern,replacement):
        nonlocal translated
        updated,n=re.subn(pattern,replacement,translated)
        if n and updated!=translated:
            edits.append({'target_term':replacement,'reason':'原句命中对应催化专业术语'})
            translated=updated
    source=unicodedata.normalize('NFKC',original)
    if re.search(r'(?<![A-Za-z0-9])NO(?![A-Za-z0-9])',source):
        replace(r'(?:一氧化氮|氧化氮|硝(?:基)?氧化物|硝酸氧化物)\s*[（(]\s*NO\s*[）)]','一氧化氮（NO）')
    if re.search(r'\bhydrothermal\b',source,re.I):replace('热液','水热')
    if re.search(r'\badsorption\s+capacit(?:y|ies)\b',source,re.I):replace('吸附能力','吸附容量')
    if re.search(r'Br[øo]nsted\s+acid\s+sites?',source,re.I):replace(r'(Br[øo]nsted)酸基点',r'\1酸位')
    if re.search(r'\b(?:SCR|catalytic)\s+activit(?:y|ies)\b',source,re.I):
        replace('SCR活动','SCR活性');replace('催化活动','催化活性')
    if re.search(r'\bacidic?\s+sites?\b',source,re.I):replace('酸性地点','酸性位点')
    if re.search(r'\bactivation\b',source,re.I) and re.search(r'\b(?:reactant|catalyst|catalytic)\b',source,re.I):replace('激活','活化')
    if re.search(r'\bloading\b',source,re.I) and re.search(r'\bcatalyst\b',source,re.I):replace('装载量','负载量')
    return translated,edits


def split_chunks(text, tokenizer, limit=140):
    """Bound each inference input without silently truncating long PDF sentences."""
    chunks = []
    # Keep decimal points, initials, and abbreviations intact; further word splits
    # bound unusually long clauses. A split warning is returned for review.
    sentences = re.split(r'(?<=[!?;。！？；])\s*|(?<=[.])\s+(?=[A-Z])', text)
    for sentence in sentences:
        if not sentence.strip():
            continue
        words = sentence.split()
        current = ''
        for word in words:
            trial = (current + ' ' + word).strip()
            if len(tokenizer.encode(trial, out_type=str)) <= limit:
                current = trial
                continue
            if current:
                chunks.append(current)
                current = ''
            if len(tokenizer.encode(word, out_type=str)) > limit:
                raise ValueError('原句含过长公式或连续字符，当前本地模型不能完整翻译。')
            current = word
        if current:
            chunks.append(current)
    return chunks


def translate(text, runtime):
    sys.path.insert(0, str(runtime / 'packages'))
    import ctranslate2
    import sentencepiece

    model = runtime / 'models' / 'translate-en_zh-1_9'
    # Loading model bytes supports Chinese Windows paths in SentencePiece.
    tokenizer = sentencepiece.SentencePieceProcessor(
        model_proto=(model / 'sentencepiece.model').read_bytes())
    translator = ctranslate2.Translator(str(model / 'model'), device='cpu',
        compute_type='int8', inter_threads=1, intra_threads=2)
    metadata = json.loads((model / 'metadata.json').read_text(encoding='utf8'))
    chunks = split_chunks(prepare_text(text), tokenizer)
    tokenized = [tokenizer.encode(c, out_type=str) for c in chunks]
    prefix = metadata.get('target_prefix', '')
    predictions = translator.translate_batch(tokenized,
        target_prefix=[[prefix]] * len(chunks) if prefix else None,
        replace_unknowns=True, beam_size=4, length_penalty=.2,
        max_batch_size=512, batch_type='tokens', max_decoding_length=384)
    translated = []
    for result in predictions:
        tokens = result.hypotheses[0]
        if len(tokens) >= 384:
            raise ValueError('译文达到长度上限，暂不显示可能不完整的翻译。')
        value = tokenizer.decode(tokens).replace('▁', ' ').strip()
        if prefix and value.startswith(prefix):
            value = value[len(prefix):].lstrip()
        if not value or not re.search(r'[\u3400-\u9fff]', value):
            raise ValueError('本地模型未生成完整中文译文，请对照原句与术语解释。')
        translated.append(value)
    raw='\n'.join(translated)
    final,edits=normalize_terminology(text,raw)
    return {'translation': final, 'translation_raw':raw,'terminology_adjustments':edits,'segments': len(chunks),
        'engine': ENGINE_VERSION, 'model': 'Argos Translate EN→ZH 1.9 (OPUS-MT)',
        'offline': True, 'translator_version': ctranslate2.__version__,
        'input_preparation': '翻译副本规范连字与换行、NH 3；大写独立 NO 按 nitric oxide (NO) 解释；原证据不变'}


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument('--runtime', required=True)
    args = parser.parse_args(argv)
    payload = json.loads(sys.stdin.buffer.read().decode('utf8'))
    text = payload.get('text', '')
    try:
        if not isinstance(text, str) or not text.strip() or len(text) > 8000:
            raise ValueError('请选择不超过 8000 字符的单条语义原句。')
        result = {'ok': True, **translate(text, Path(args.runtime).resolve())}
    except Exception as exc:
        result = {'ok': False, 'error': str(exc)[:600]}
    sys.stdout.buffer.write(json.dumps(result, ensure_ascii=False).encode('utf8'))
    sys.stdout.buffer.flush()
    return 0 if result['ok'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
