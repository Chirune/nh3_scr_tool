"""Portable-entry, team import and synthetic round-trip regression tests."""
from pathlib import Path
import importlib.util
import json
import os
import tempfile
import unittest
from unittest.mock import patch

import shared_demo
import team_bridge
import paper_workspace
import workbench_paths
from intake import load_screened_input

ROOT=Path(__file__).resolve().parent.parent
spec=importlib.util.spec_from_file_location('shared_entry_test',ROOT/'start_workbench.py')
entry=importlib.util.module_from_spec(spec);spec.loader.exec_module(entry)


class SharedIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.folder=Path(self.temp.name).resolve()
    def tearDown(self):self.temp.cleanup()

    def test_stage_commands_share_selected_data_directory(self):
        with patch.dict(os.environ,{'CATALYST_DATA_DIR':str(self.folder)}):
            for stage in ['1','2','3','figures']:
                command=entry.command_for(stage,smoke=True)
                self.assertIn('--smoke',command)
                self.assertTrue(any(str(self.folder) in p for p in command))
                self.assertTrue(Path(command[4]).is_file())

    def test_saved_coding_session_has_correct_launch_flag(self):
        path=self.folder/'session.json'
        path.write_text(json.dumps({'schema_version':'coding-workbench-session/1.0'}))
        self.assertIn('--session',entry.command_for('3',path))
        path.write_text(json.dumps({'schema_version':'paper-handoff/1.0'}))
        self.assertIn('--handoff',entry.command_for('3',path))

    def test_synthetic_demo_enters_latest_paper_extraction(self):
        run=shared_demo.create_demo(self.folder/'demo')
        intake=load_screened_input(run)
        self.assertEqual(len(intake['papers']),1)
        project=paper_workspace.open_paper(intake['papers'][0],self.folder/'papers')
        paper_workspace.extract_document(project)
        self.assertTrue(project['evidence'])
        self.assertTrue(any('SYNTHETIC' in p.get('text','') for p in project['pages']))

    def make_team_project(self,decision='target'):
        run=shared_demo.create_demo(self.folder/'demo')
        original=json.loads(run.read_text(encoding='utf8'))['records'][0]
        project=run.parent/'project.json'
        value={'schema':'nh3scr-workbench-v1','papers':[{'record_id':'team1','title':original['title'],
            'doi':'','abstract':original['abstract'],'human_decision':decision}],
            'attachments':{'team1':[{'role':'primary','path':Path(original['local_path']).name}]}}
        project.write_text(json.dumps(value),encoding='utf8')
        return project,value

    def test_import_preserves_manual_exclusion_and_original_project(self):
        path,value=self.make_team_project('non_target');before=path.read_bytes()
        imported=team_bridge.import_project(path,self.folder/'imports')
        self.assertEqual(path.read_bytes(),before)
        received=load_screened_input(imported)
        self.assertEqual(received['excluded_count'],1)
        self.assertEqual(received['papers'],[])

    def test_import_with_missing_manual_decision_remains_pending(self):
        path,value=self.make_team_project(None)
        imported=team_bridge.import_project(path,self.folder/'imports')
        self.assertEqual(load_screened_input(imported)['papers'],[])
        self.assertEqual(json.loads(imported.read_text(encoding='utf8'))['records'][0]['manual_decision'],'review')

    def test_import_copies_primary_pdf_and_keeps_source_hash(self):
        path,value=self.make_team_project()
        imported=team_bridge.import_project(path,self.folder/'imports')
        papers=load_screened_input(imported)['papers']
        self.assertEqual(len(papers),1)
        self.assertTrue(Path(papers[0]['local_pdf']).is_relative_to(imported.parent))
        self.assertEqual(papers[0]['source_sha256'],paper_workspace.digest(papers[0]['local_pdf']))

    def test_multiple_primary_pdfs_are_not_silently_chosen(self):
        path,value=self.make_team_project()
        value['attachments']['team1']*=2;path.write_text(json.dumps(value))
        imported=team_bridge.import_project(path,self.folder/'imports')
        self.assertEqual(load_screened_input(imported)['papers'],[])

    def test_path_outside_original_project_is_not_imported(self):
        path,value=self.make_team_project()
        value['attachments']['team1'][0]['path']='../../outside.pdf';path.write_text(json.dumps(value))
        imported=team_bridge.import_project(path,self.folder/'imports')
        self.assertEqual(load_screened_input(imported)['papers'],[])


if __name__=='__main__':unittest.main()
