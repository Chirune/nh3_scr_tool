"""Portable paths shared by all launchers; importing does not create files."""
from pathlib import Path
import os


def repository_root():
    return Path(__file__).resolve().parent.parent


def data_root():
    custom = os.environ.get('CATALYST_DATA_DIR')
    return Path(custom).expanduser().resolve() if custom else repository_root() / 'runtime_data'


def translation_root():
    custom = os.environ.get('CATALYST_TRANSLATION_DIR')
    return Path(custom).expanduser().resolve() if custom else repository_root() / 'local_models/translation'


def guide_path():
    return repository_root() / 'docs/workbench/tutorial.html'
