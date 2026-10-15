"""Install the official standalone Ollama runtime and a local Qwen model."""
from pathlib import Path
import json
import hashlib
import os
import shutil
import subprocess
import time
import zipfile
import re
import requests

ROOT = Path(__file__).resolve().parents[1]
STATE = ROOT / 'runtime_data/local_assistant'
RUNTIME = ROOT / 'local_models/ollama_runtime'
MODEL_DIR = ROOT / 'local_models/ollama'
MODEL = 'qwen3:4b'
LOCAL = 'http://127.0.0.1:11434'
session = requests.Session(); session.trust_env = False


def ready():
    try:
        response = session.get(LOCAL + '/api/tags', timeout=2)
        response.raise_for_status()
        return response.json()
    except (requests.RequestException, ValueError):
        return None


def download_official_cache():
    """Proxy fake-IP fallback: verify official layers and populate local cache."""
    url = 'https://registry.ollama.ai/v2/library/qwen3/manifests/4b'
    response = requests.get(url, timeout=30); response.raise_for_status()
    manifest = response.json()
    blobs = MODEL_DIR / 'blobs'; blobs.mkdir(parents=True, exist_ok=True)
    for layer in [manifest['config'], *manifest['layers']]:
        digest = layer['digest']; expected = layer['size']
        if not re.fullmatch(r'sha256:[a-f0-9]{64}', digest) or not 0 < expected < 12 * 1024**3:
            raise RuntimeError('官方模型清单格式无效。')
        target = blobs / digest.replace(':', '-')
        if target.is_file() and target.stat().st_size == expected:
            with target.open('rb') as stream:
                if hashlib.file_digest(stream, 'sha256').hexdigest() == digest.split(':')[1]:
                    continue
        partial = target.with_suffix('.download')
        total = 0; checksum = hashlib.sha256(); started = time.monotonic()
        with requests.get('https://registry.ollama.ai/v2/library/qwen3/blobs/' + digest,
                          stream=True, timeout=(20, 90)) as asset:
            asset.raise_for_status()
            with partial.open('wb') as stream:
                for chunk in asset.iter_content(1024 * 1024):
                    total += len(chunk)
                    if total > expected:
                        raise RuntimeError('模型数据超过官方清单大小，未安装。')
                    stream.write(chunk); checksum.update(chunk)
                    if time.monotonic() - started > 8:
                        print(f'Official model layer: {total/expected:.0%}', flush=True)
                        started = time.monotonic()
        if total != expected or checksum.hexdigest() != digest.split(':')[1]:
            raise RuntimeError('模型数据与官方清单不一致，未安装。')
        partial.replace(target)
    destination = MODEL_DIR / 'manifests/registry.ollama.ai/library/qwen3/4b'
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix('.tmp')
    temporary.write_bytes(response.content)
    temporary.replace(destination)


def main():
    STATE.mkdir(parents=True, exist_ok=True)
    if ready() is None:
        executable = RUNTIME / 'ollama.exe'
        if not executable.is_file():
            if shutil.disk_usage(ROOT).free < 14 * 1024**3:
                raise RuntimeError('本地模型安装至少需 14 GB 可用空间。')
            archive = STATE / 'ollama-windows-amd64.zip'
            if not archive.is_file():
                url = 'https://github.com/ollama/ollama/releases/latest/download/ollama-windows-amd64.zip'
                print('Downloading official Ollama Windows standalone runtime...', flush=True)
                with requests.get(url, stream=True, timeout=(20, 60)) as response:
                    response.raise_for_status()
                    total = int(response.headers.get('Content-Length') or 0)
                    count = 0; started = time.monotonic()
                    partial = archive.with_suffix('.zip.part')
                    with partial.open('wb') as stream:
                        for chunk in response.iter_content(1024 * 1024):
                            stream.write(chunk); count += len(chunk)
                            if time.monotonic() - started > 8:
                                print(f'Runtime: {count/1024**2:.0f} MB' + (f' / {total/1024**2:.0f} MB' if total else ''), flush=True)
                                started = time.monotonic()
                    if total and count != total:
                        raise RuntimeError('Ollama 下载不完整，请重试安装。')
                    partial.replace(archive)
            print('Extracting official runtime...', flush=True)
            RUNTIME.mkdir(parents=True, exist_ok=True)
            with zipfile.ZipFile(archive) as package:
                for info in package.infolist():
                    target = (RUNTIME / info.filename).resolve()
                    if not target.is_relative_to(RUNTIME.resolve()):
                        raise RuntimeError('安装归档包含无效路径。')
                package.extractall(RUNTIME)
        environment = dict(os.environ)
        environment.update(OLLAMA_HOST='127.0.0.1:11434', OLLAMA_MODELS=str(MODEL_DIR), OLLAMA_NO_CLOUD='1')
        proxy = requests.utils.get_environ_proxies('https://registry.ollama.ai').get('https')
        if proxy and not environment.get('HTTPS_PROXY'):
            environment['HTTPS_PROXY'] = proxy
        log = (STATE / 'ollama_server.log').open('ab')
        process = subprocess.Popen([str(executable), 'serve'], env=environment, stdout=log, stderr=log,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
        log.close()
        for _ in range(40):
            if ready() is not None:
                break
            if process.poll() is not None:
                raise RuntimeError('Ollama 启动失败，请查看本地安装日志。')
            time.sleep(.5)
        if ready() is None:
            raise RuntimeError('Ollama 本地接口尚未启动。')
    tags = ready()
    if not any(row.get('name') == MODEL for row in tags.get('models', [])):
        print('Downloading Qwen3 4B to local Ollama model storage...', flush=True)
        with session.post(LOCAL + '/api/pull', json={'model': MODEL, 'stream': True}, stream=True, timeout=(10, 600)) as response:
            response.raise_for_status(); started = 0
            for line in response.iter_lines():
                if not line:
                    continue
                data = json.loads(line)
                if data.get('error'):
                    if 'non-public 198.18.' in data['error']:
                        print('Proxy fake-IP detected; receiving verified official registry layers through the system proxy...', flush=True)
                        download_official_cache()
                        break
                    raise RuntimeError('模型下载失败：' + data['error'])
                if time.monotonic() - started > 8 or data.get('status') == 'success':
                    total = data.get('total', 0); done = data.get('completed', 0)
                    print(data.get('status', '') + (f' {done/total:.0%}' if total else ''), flush=True)
                    started = time.monotonic()
    if not any(row.get('name') == MODEL for row in ready().get('models', [])):
        raise RuntimeError('本机接口尚未识别模型，未声明安装完成。')
    config = {'model': MODEL, 'endpoint': LOCAL, 'local_only': True}
    (STATE / 'config.json').write_text(json.dumps(config, indent=2), encoding='utf-8')
    print('Local assistant ready: Qwen3 4B through Ollama.', flush=True)


if __name__ == '__main__':
    main()
