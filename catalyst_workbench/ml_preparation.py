"""Model-specific, fold-local input preparation; never fits a prediction model.

Pure-Python transforms keep this data workbench independent of ML libraries.
Candidate model libraries can consume these matrices later. All learned
statistics and category vocabularies belong to a specific training fold.
"""
from __future__ import annotations

import copy
from datetime import datetime
import math
from pathlib import Path
import statistics
import uuid

import paper_encoding as encoding

SCHEMA = 'model-ready-facts-and-folds/1.0'
PREPROCESSOR_SCHEMA = 'fold-local-preprocessor/1.0'
PROFILES = {
    'design': '配方与条件预测（默认）',
    'characterized': '已完成表征后预测（只用预测前已知的表征量）',
    'dft_model': '指定原子模型与计算设置下的吸附能预测',
}
RECIPES = {
    'scaled_one_hot': {'label': '线性模型 / 岭回归 / SVR', 'models': ['LinearRegression', 'Ridge', 'SVR'],
        'numeric_missing': 'training_fold_median', 'scaling': 'training_fold_zscore', 'categorical': 'training_fold_one_hot',
        'note': '每一训练折内填补、标准化和建类别表。类别不编码成任意大小顺序。'},
    'tree_one_hot': {'label': '随机森林 / ExtraTrees / 普通梯度提升',
        'models': ['RandomForestRegressor', 'ExtraTreesRegressor', 'GradientBoostingRegressor'],
        'numeric_missing': 'training_fold_median', 'scaling': 'none', 'categorical': 'training_fold_one_hot',
        'note': '本方案采用明确的中位数填补，不要求树库特定版本具备原生缺失能力。'},
    'native_missing_one_hot': {'label': 'XGBoost / 支持缺失的梯度提升',
        'models': ['XGBRegressor', 'HistGradientBoostingRegressor'],
        'numeric_missing': 'preserve_null', 'scaling': 'none', 'categorical': 'training_fold_one_hot',
        'note': '保存缺失；训练库需把JSON null/表格空值转成其认可的NaN。XGBoost原生类别是另一个可比较方案，此处先用显式独热。'},
    'native_categories': {'label': 'CatBoost（保留类别）', 'models': ['CatBoostRegressor'],
        'numeric_missing': 'preserve_null', 'scaling': 'none', 'categorical': 'native_strings',
        'note': '普通类别保留字符串，明确缺失类别；元素列表仍用多热。后续训练需传入categorical_columns。'},
}
SNAPSHOT_FIELDS = ('input_packets', 'observations', 'excluded', 'split', 'encoder', 'encoded_rows')


def _check_encoding(result):
    if result.get('schema_version') != encoding.SCHEMA or encoding._fingerprint(
        {key: result[key] for key in SNAPSHOT_FIELDS}) != result.get('snapshot_fingerprint'):
        raise ValueError('编码预览已变化，请重新核对生成。')


def _plan_fingerprint(plan):
    return encoding._fingerprint({key: value for key, value in plan.items() if key != 'plan_fingerprint'})


def validate_plan(plan):
    if plan.get('schema_version') != SCHEMA or plan.get('plan_fingerprint') != _plan_fingerprint(plan):
        raise ValueError('机器学习准备包已改变或版本不符；请从当前核验数据重新导出。')


def _group_folds(rows):
    # The outer test papers remain sealed. All model and encoder decisions use
    # identical development folds, generated without consulting y.
    by_group = {}
    for row in rows:
        if row['split'] == 'train':
            by_group.setdefault(row['split_group'], []).append(row['observation_id'])
    if len(by_group) < 3:
        return []
    count = min(5, len(by_group))
    buckets = [[] for _ in range(count)]; sizes = [0] * count
    for group in sorted(by_group, key=lambda g: (-len(by_group[g]), encoding._fingerprint(['model-fold-v1', g]))):
        index = min(range(count), key=lambda i: (sizes[i], i))
        buckets[index].append(group); sizes[index] += len(by_group[group])
    folds = []
    for index, validation in enumerate(buckets):
        train = sorted(set(by_group) - set(validation))
        folds.append({'fold_id': f'fold-{index + 1}', 'train_groups': train,
            'validation_groups': sorted(validation),
            'train_ids': [r['observation_id'] for r in rows if r['split_group'] in train],
            'validation_ids': [r['observation_id'] for r in rows if r['split_group'] in validation]})
    return folds


def build_plan(result):
    _check_encoding(result)
    task = result['task_id']; specs = encoding._feature_specs(task)
    rows = [{'observation_id': r['observation_id'], 'split_group': r['split_group'], 'split': r['split'],
        'sample_label': r['sample_label'], 'doi': r['doi'], 'experiment_id': r['experiment_id'],
        'X_facts': copy.deepcopy(r['X_raw']), 'y': r['y'], 'y_unit': r['y_unit'],
        'feature_availability': copy.deepcopy(r['raw_record'].get('feature_availability', {})),
        'approximate': r['approximate'], 'source_sha256': r['source_sha256'],
        'evidence_ids': [e['evidence_id'] for e in r['evidence']]} for r in result['observations']]
    if task == 'nh3_adsorption_energy':
        profiles = {'dft_model': {'label': PROFILES['dft_model'], 'fields': [k for k in specs
            if k in encoding.DFT_FIELDS | {'active_metals', 'support', 'support_detail', 'cu_zn_atomic_ratio'}],
            'note': '只选原子模型与计算设置的现有结构字段；原子构型和能量定义仍须核对，不能仅靠这几列声称描述完整。'}}
        default = 'dft_model'
    else:
        profiles = {
            'design': {'label': PROFILES['design'], 'fields': [k for k in specs if k not in encoding.CHARACTERIZATION_FIELDS],
                'note': '适用于先根据配方和工况选候选；不使用需要测量后才知道的BET、粒径等。'},
            'characterized': {'label': PROFILES['characterized'], 'fields': list(specs),
                'note': '每条记录的表征值须在第二板块标为“预测之前已知”；未知时点或反应后值只留在事实表，不送进此模型。'}}
        default = 'design'
    coverage = [{ 'field': key, **copy.deepcopy(spec),
        'reported': sum(r['X_facts'][key] is not None for r in rows), 'total': len(rows),
        'availability_requirement': '逐条确认预测前已知' if key in encoding.CHARACTERIZATION_FIELDS else '设计/条件或指定计算设置',
        'missing': sum(r['X_facts'][key] is None for r in rows),
        'available_before_prediction': sum(r['X_facts'][key] is not None and
            (key not in encoding.CHARACTERIZATION_FIELDS or r['feature_availability'].get(key) == 'before_prediction') for r in rows),
        'reported_but_unavailable': sum(r['X_facts'][key] is not None and
            key in encoding.CHARACTERIZATION_FIELDS and r['feature_availability'].get(key) != 'before_prediction' for r in rows)
        } for key, spec in specs.items()]
    folds = _group_folds(rows)
    by_id = {r['observation_id']: r for r in rows}
    for fold in folds:
        validation_y = [by_id[key]['y'] for key in fold['validation_ids']]
        fold['validation_observation_count'] = len(validation_y)
        fold['validation_group_count'] = len(fold['validation_groups'])
        fold['r2_applicability'] = ('undefined_single_observation' if len(validation_y) < 2 else
            'undefined_constant_targets' if len(set(validation_y)) == 1 else 'defined_if_model_predictions_exist')
    plan = {'schema_version': SCHEMA, 'task_id': task, 'task_title': result['task_title'],
        'encoding_snapshot': result['snapshot_fingerprint'], 'input_packets': copy.deepcopy(result['input_packets']),
        'rows': rows, 'fields': copy.deepcopy(specs), 'profiles': profiles, 'default_profile': default,
        'recipes': copy.deepcopy(RECIPES), 'coverage': coverage, 'folds': folds,
        'outer_test_ids': [r['observation_id'] for r in rows if r['split'] == 'test'],
        'outer_test_groups': result['split']['test_groups'],
        'status': 'development_folds_prepared' if folds else 'facts_only_not_enough_training_papers',
        'model_trained': False, 'winning_model': None, 'accuracy_estimated': False,
        'selection_protocol': {
            'baseline': '仅用训练折目标均值的DummyRegressor',
            'comparison': '相同任务、相同事实行、相同论文分组；比较特征场景＋折内预处理＋模型。',
            'selection_metric': '训练开发折的MAE为主要比较量，结合RMSE、R²、逐论文误差和稳定性；预先固定规则。',
            'target_units': '误差按原物理单位报告；fraction指标乘100后才叫百分点。R²不是成功率。',
            'small_validation_policy': '某验证折只有一条观察或标签恒定时，不报告该折R²；保留绝对误差，等待足够独立论文后评价稳定性。',
            'test_policy': '预留论文不参与缺失填补、类别表、缩放、特征选择、调参或模型选择；冻结方案后才评价一次。',
            'tuning_policy': '若用开发折挑超参数，该开发分数是选择用分数；需外部预留测试或额外嵌套分组验证评估泛化。',
            'ranking_policy': '需要Top-K时另用折外预测评价候选召回/富集，并控制实验条件；不把拟合训练集的排序当验证。',
        },
        'excluded_from_X': ['y及其他性能目标', '质量分', '性能分', 'DOI/论文/样品编号', '原文结论',
            '预测后才获得的表征', '本轮后续DFT验证结果'],
        'dft_handoff': {'status': '待模型比较和候选选择，当前未生成真实验证任务',
            'required': ['可回溯的候选配方及参考样', '预测目标、固定工况及适用范围', '模型/编码版本及折外误差',
                '明确待验证的微观假设', '原子结构、表面/缺陷/位点映射', '一致的能量定义与计算参数'],
            'note': '吸附能/能垒用于检验微观假设；不能直接替代实验转化率或选择性测量。预测本身就是DFT吸附能时，才可对同定义的独立新DFT结果做直接数值误差比较。'}}
    plan['plan_fingerprint'] = _plan_fingerprint(plan)
    return plan


def _facts_for_profile(row, profile):
    result = {key: row['X_facts'].get(key) for key in profile['fields']}
    for key in encoding.CHARACTERIZATION_FIELDS & set(result):
        if row.get('feature_availability', {}).get(key) != 'before_prediction':
            result[key] = None
    return result


def fit_fold_preprocessor(plan, recipe_id, fold_id, profile_id=None):
    validate_plan(plan)
    profile_id = profile_id or plan['default_profile']
    if recipe_id not in RECIPES or profile_id not in plan['profiles']:
        raise ValueError('请选择支持的编码方案和预测场景。')
    fold = next((f for f in plan['folds'] if f['fold_id'] == fold_id), None)
    if not fold:
        raise ValueError('没有该训练折；论文不足时只保留事实表，不在整批数据上拟合预处理。')
    rows = {r['observation_id']: r for r in plan['rows']}
    train_ids = fold['train_ids']
    if not train_ids or set(train_ids) & (set(plan['outer_test_ids']) | set(fold['validation_ids'])):
        raise ValueError('训练、验证或测试观察发生重叠。')
    if set(fold['train_groups']) & (set(fold['validation_groups']) | set(plan['outer_test_groups'])):
        raise ValueError('同一论文进入了不同用途。')
    recipe = RECIPES[recipe_id]; profile = plan['profiles'][profile_id]
    training = [_facts_for_profile(rows[key], profile) for key in train_ids]
    numeric, vocab, columns, categorical_columns, dropped = {}, {}, [], [], []
    for field in profile['fields']:
        spec = plan['fields'][field]
        values = [r[field] for r in training]
        if spec['kind'] == 'numeric':
            observed = [float(v) for v in values if v is not None]
            if not observed:
                dropped.append({'field': field, 'reason': '本训练折全部缺失或在预测时不可用；不造出0或偷看验证集。'})
                continue
            median = statistics.median(observed)
            filled = [median if v is None else float(v) for v in values]
            mean = statistics.fmean(filled) if recipe['scaling'] == 'training_fold_zscore' else 0.0
            scale = (statistics.pstdev(filled) or 1.0) if recipe['scaling'] == 'training_fold_zscore' else 1.0
            if not all(math.isfinite(v) for v in (median, mean, scale)):
                raise ValueError('训练折数值尺度异常，请核对单位与异常值：' + field)
            numeric[field] = {'median': median, 'mean': mean, 'scale': scale, 'observed_count': len(observed)}
            columns.extend([field, field + '__missing'])
        else:
            tokens = sorted({token for value in values if value is not None
                             for token in (value if isinstance(value, list) else [value])})
            vocab[field] = tokens
            if recipe['categorical'] == 'native_strings' and spec['kind'] == 'category':
                columns.append(field); categorical_columns.append(field)
            else:
                columns.extend(field + '=' + token for token in tokens)
                columns.extend([field + '__missing', field + '__unknown'])
    state = {'schema_version': PREPROCESSOR_SCHEMA, 'plan_fingerprint': plan['plan_fingerprint'],
        'recipe_id': recipe_id, 'profile_id': profile_id, 'fold_id': fold_id,
        'fit_observation_ids': list(train_ids), 'fit_groups': fold['train_groups'],
        'columns': columns, 'categorical_columns': categorical_columns,
        'numeric': numeric, 'vocabulary': vocab, 'dropped_fields': dropped,
        'model_trained': False}
    state['state_fingerprint'] = encoding._fingerprint(state)
    return state


def transform_partition(plan, state, partition='validation'):
    validate_plan(plan)
    if partition not in ('train', 'validation'):
        raise ValueError('此入口只用于开发训练折与验证折；预留测试集保持封存。')
    if (state.get('schema_version') != PREPROCESSOR_SCHEMA or state.get('plan_fingerprint') != plan['plan_fingerprint'] or
        state.get('state_fingerprint') != encoding._fingerprint({k: v for k, v in state.items() if k != 'state_fingerprint'})):
        raise ValueError('预处理器与当前事实表不匹配或已被改写。')
    fold = next(f for f in plan['folds'] if f['fold_id'] == state['fold_id'])
    wanted = set(fold[partition + '_ids']); recipe = RECIPES[state['recipe_id']]
    profile = plan['profiles'][state['profile_id']]
    result = []
    for row in plan['rows']:
        if row['observation_id'] not in wanted:
            continue
        facts = _facts_for_profile(row, profile)
        x, unknown = {}, {}
        for field, numeric in state['numeric'].items():
            value = facts[field]
            x[field + '__missing'] = int(value is None)
            if value is None and recipe['numeric_missing'] == 'preserve_null':
                x[field] = None
            else:
                value = numeric['median'] if value is None else float(value)
                x[field] = (value - numeric['mean']) / numeric['scale']
                if not math.isfinite(x[field]):
                    raise ValueError('变换结果溢出，请核对单位或极端值。')
        for field, vocabulary in state['vocabulary'].items():
            value = facts[field]
            tokens = set(value if isinstance(value, list) else [] if value is None else [value])
            outside = sorted(tokens - set(vocabulary))
            if outside:
                unknown[field] = outside
            if field in state['categorical_columns']:
                x[field] = '__MISSING__' if value is None else value
            else:
                for token in vocabulary:
                    x[field + '=' + token] = int(token in tokens)
                x[field + '__missing'] = int(value is None)
                x[field + '__unknown'] = int(bool(outside))
        result.append({'observation_id': row['observation_id'], 'split_group': row['split_group'],
            'partition': partition, 'X': {key: x[key] for key in state['columns']},
            'y': row['y'], 'y_unit': row['y_unit'], 'unknown_categories': unknown})
    return result


def prepare_fold(plan, recipe_id, fold_id, profile_id=None):
    state = fit_fold_preprocessor(plan, recipe_id, fold_id, profile_id)
    return {'state': state, 'train': transform_partition(plan, state, 'train'),
            'validation': transform_partition(plan, state, 'validation'),
            'model_trained': False, 'performance_evaluated': False}


def write_preparation_files(folder, plan):
    validate_plan(plan); folder = Path(folder)
    encoding._write_json(folder / '机器学习准备包.json', plan)
    encoding._write_json(folder / '模型编码方案.json', {key: plan[key] for key in
        ('schema_version', 'profiles', 'default_profile', 'recipes', 'selection_protocol', 'excluded_from_X', 'dft_handoff')})
    encoding._write_json(folder / '模型比较_论文分组.json', {key: plan[key] for key in
        ('plan_fingerprint', 'status', 'folds', 'outer_test_ids', 'outer_test_groups')})
    facts = [{**{k: r[k] for k in ('observation_id', 'doi', 'sample_label', 'experiment_id', 'split_group', 'split', 'y', 'y_unit')},
              **r['X_facts'], 'feature_availability': r['feature_availability']} for r in plan['rows']]
    encoding._write_csv(folder / '建模事实表_尚未拟合填补缩放.csv', facts,
        ['observation_id', 'doi', 'sample_label', 'experiment_id', 'split_group', 'split', 'y', 'y_unit', *plan['fields'], 'feature_availability'])
    encoding._write_csv(folder / '建模字段覆盖与获得时点.csv', plan['coverage'],
        ['field', 'label', 'kind', 'unit', 'reported', 'missing', 'total', 'available_before_prediction',
         'reported_but_unavailable', 'availability_requirement'])


def export_fold(result, output_root, recipe_id, fold_id, profile_id=None):
    """Export one revalidated fold with aligned matrices and untouched test set."""
    current = encoding.revalidate_snapshot(result)
    plan = build_plan(current)
    prepared = prepare_fold(plan, recipe_id, fold_id, profile_id)
    state = prepared['state']
    folder = Path(output_root).resolve() / ('训练折编码_' + fold_id + '_' + recipe_id + '_' +
        datetime.now().strftime('%Y%m%d_%H%M%S') + '_' + uuid.uuid4().hex[:6])
    folder.mkdir(parents=True, exist_ok=False)
    encoding._write_json(folder / '仅输入变换_未训练模型.json', prepared)
    encoding._write_json(folder / '本训练折拟合的预处理器.json', state)
    lookup = {row['observation_id']: row for row in plan['rows']}
    for partition, label in (('train', '训练'), ('validation', '验证')):
        rows = prepared[partition]
        encoding._write_csv(folder / ('X_' + partition + '.csv'), [r['X'] for r in rows], state['columns'])
        encoding._write_csv(folder / ('y_' + partition + '.csv'), [{'y': r['y']} for r in rows], ['y'])
        mapping = [{'row_index_0_based': index, 'observation_id': row['observation_id'],
            'sample_label': lookup[row['observation_id']]['sample_label'],
            'doi': lookup[row['observation_id']]['doi'], 'split_group': row['split_group'],
            'y_unit': row['y_unit'], 'unknown_categories': row['unknown_categories']}
            for index, row in enumerate(rows)]
        encoding._write_csv(folder / ('行与来源_' + partition + '.csv'), mapping,
            ['row_index_0_based', 'observation_id', 'sample_label', 'doi', 'split_group', 'y_unit', 'unknown_categories'])
    lines = ['这是所选训练折的输入变换；没有训练预测模型，也没有计算准确率。',
        '方案：' + RECIPES[recipe_id]['label'], '预测场景：' + plan['profiles'][state['profile_id']]['label'],
        f"训练{len(prepared['train'])}条，验证{len(prepared['validation'])}条。没有导出预留测试集的输入或标签。",
        '每组X、y和行与来源文件行序严格一致；DOI和样品编号只在来源文件，未进入X。',
        '数值填补、标准化、类别表仅使用本训练折；其他折必须分别拟合，不能共用此预处理器。',
        'null/CSV空白保留为缺失。CatBoost方案须传入预处理器内的categorical_columns；其他列保持数值。',
        '预留测试未参与本次拟合或变换；模型与参数冻结后才评价一次。',
        '模型比较前还需核对进料、定义、测量方法和适用范围。',
        '快照：' + plan['encoding_snapshot'], '计划：' + plan['plan_fingerprint']]
    (folder / '00_训练折说明.txt').write_text('\n'.join(lines) + '\n', encoding='utf8')
    return {'directory': str(folder), 'files': sorted(p.name for p in folder.iterdir()),
        'train_count': len(prepared['train']), 'validation_count': len(prepared['validation']),
        'model_trained': False, 'test_exported': False, 'state_fingerprint': state['state_fingerprint']}
