"""Generate local suggestions in a review copy, retaining all source evidence."""
import argparse
import copy
import json
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'catalyst_workbench'))
import paper_workspace as work
import semantic_assistant as assistant


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--paper', required=True)
    parser.add_argument('--output-root', required=True)
    args = parser.parse_args()
    if not assistant.model_ready():
        raise ValueError('请先安装或启动本机模型。')
    original = work.load_project(args.paper)
    root = Path(args.output_root).resolve() / original['project_id']
    root.mkdir(parents=True, exist_ok=True)
    if (root / 'paper.json').is_file():
        project = work.load_project(root / 'paper.json')
    else:
        project = copy.deepcopy(original)
        project.update(run_dir=str(root), revision=0, exports=[])
        work.save_project(project, check_revision=False)
    rows = [e for e in project['evidence'] if e.get('branch') == 'semantic' and not e.get('stale')
            and not e.get('semantic_adoption')]
    started = time.monotonic(); done = 0; errors = []
    for i, evidence in enumerate(rows, 1):
        if evidence.get('assistant_suggestion', {}).get('source_quote') == evidence.get('quote'):
            done += 1
            continue
        try:
            suggestion = assistant.suggest(project, evidence)
            work.save_assistant_suggestion(project, evidence['evidence_id'], suggestion)
            done += 1
        except assistant.AssistantError as exc:
            errors.append({'evidence_id': evidence['evidence_id'], 'message': str(exc)})
            print('Suggestion stopped:', str(exc), flush=True)
            break
        if i % 10 == 0 or i == len(rows):
            print(f'Suggestions: {done}/{len(rows)}; all unconfirmed.', flush=True)
    report = {'model': assistant.configured_model(), 'suggestions': done, 'eligible': len(rows),
              'errors': errors, 'elapsed_s': round(time.monotonic() - started, 1),
              'paper_path': str(work.workspace_path(project)), 'human_reviews_added': 0,
              'performance_records_added': 0, 'source_paper': str(Path(args.paper).resolve())}
    (Path(args.output_root) / 'validation.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(report, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
