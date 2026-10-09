"""Explicit model installation. Translation inference itself is offline."""
from pathlib import Path
import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from urllib.request import urlopen
from zipfile import ZipFile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'catalyst_workbench'))
from workbench_paths import translation_root


def digest(path):
    h=hashlib.sha256()
    with Path(path).open('rb') as f:
        for block in iter(lambda:f.read(1024*1024),b''):h.update(block)
    return h.hexdigest()


def install_model(archive, runtime, manifest):
    if digest(archive) != manifest['archive_sha256']:
        raise ValueError('Model archive checksum mismatch; nothing installed.')
    runtime=Path(runtime).resolve();runtime.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='translation_setup_',dir=runtime) as folder:
        folder=Path(folder).resolve()
        with ZipFile(archive) as z:
            for info in z.infolist():
                target=(folder/info.filename).resolve()
                if not target.is_relative_to(folder) or (info.external_attr >> 16) & 0o170000 == 0o120000:
                    raise ValueError('Invalid model archive path.')
            z.extractall(folder)
        source=folder/'translate-en_zh-1_9'
        if digest(source/'model/model.bin') != manifest['model_sha256'] or digest(source/'sentencepiece.model') != manifest['tokenizer_sha256']:
            raise ValueError('Model contents do not match the pinned manifest.')
        destination=runtime/'models/translate-en_zh-1_9'
        destination.parent.mkdir(parents=True,exist_ok=True)
        if destination.exists():
            if digest(destination/'model/model.bin') != manifest['model_sha256']:
                raise ValueError('An existing model differs. Choose a different CATALYST_TRANSLATION_DIR.')
        else:
            shutil.copytree(source,destination)
    (runtime/'model-source.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf8')


def main(argv=None):
    parser=argparse.ArgumentParser()
    parser.add_argument('--archive',type=Path,help='use an already downloaded official .argosmodel archive')
    args=parser.parse_args(argv)
    runtime=translation_root();runtime.mkdir(parents=True,exist_ok=True)
    manifest=json.loads((ROOT/'docs/workbench/translation_model.json').read_text(encoding='utf8'))
    archive=args.archive
    if archive is None:
        archive=runtime/'translate-en_zh-1_9.argosmodel'
        if not archive.is_file() or digest(archive)!=manifest['archive_sha256']:
            partial=archive.with_suffix('.download')
            print('Downloading pinned public EN-ZH model (~71 MB); no paper content is sent.',flush=True)
            try:
                with urlopen(manifest['source'],timeout=90) as response, partial.open('wb') as out:
                    size=0
                    while chunk:=response.read(1024*1024):
                        size+=len(chunk)
                        if size>150*1024*1024:raise ValueError('Download exceeds expected size bound.')
                        out.write(chunk)
                if digest(partial)!=manifest['archive_sha256']:raise ValueError('Downloaded model checksum mismatch.')
                os.replace(partial,archive)
            finally:
                if partial.exists():partial.unlink()
    install_model(archive,runtime,manifest)
    subprocess.run([sys.executable,'-m','pip','install','--target',str(runtime/'packages'),
                    'ctranslate2==4.8.2','sentencepiece==0.2.2'],check=True)
    from paper_reading import translate_local
    result=translate_local('The catalyst showed higher NO conversion.')
    if not result.get('translation'):raise RuntimeError('Local translation smoke check failed.')
    print('Local Chinese translation is ready. Reopen stage 2.',flush=True)
    return 0


if __name__=='__main__':raise SystemExit(main())
