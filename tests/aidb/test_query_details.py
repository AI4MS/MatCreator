import json
import tempfile
import unittest
from pathlib import Path

from matcreator.aidb.service import SessionAIDB


class QueryDetailsTests(unittest.TestCase):
    def test_query_and_detail_remain_bound_to_the_selected_session_database(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            a = root / 'a.json'
            b = root / 'b.json'
            a.write_text('{}')
            b.write_text('{}')
            def database(config, workspace, conditions):
                record = {'record_id': 'same-id', 'formula_reduced': 'Si' if config == a else 'NaCl'}
                return [record], {'conditions': conditions, 'match_count': 1, 'complete': True}
            first = SessionAIDB(root, 'session-a', query_database=database)
            second = SessionAIDB(root, 'session-b', query_database=database)
            first.bind(a)
            second.bind(b)
            self.assertEqual(first.query({'formula': 'Si'})['records'][0]['formula_reduced'], 'Si')
            self.assertEqual(second.detail('same-id')['record']['formula_reduced'], 'NaCl')
            self.assertEqual(SessionAIDB(root, 'session-a', query_database=database).detail('same-id')['record']['formula_reduced'], 'Si')


class CrystalQueryTests(unittest.TestCase):
    def test_initial_spacegroup_and_target_temperature_use_their_evidence(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            config = root / 'config.json'
            config.write_text('{}')
            records = [
                {'record_id': 'initial-diamond', 'source_metadata': {'vasp_facts': {'initial_structure': {'spacegroup': 'Fd-3m'}, 'spacegroup': 'P1', 'sampling': {'target_start_kelvin': 300, 'target_end_kelvin': 300}}}},
                {'record_id': 'final-only', 'source_metadata': {'vasp_facts': {'spacegroup': 'Fd-3m', 'sampling': {'target_start_kelvin': 300, 'target_end_kelvin': 300}}}},
                {'record_id': 'observed-only', 'source_metadata': {'vasp_facts': {'initial_structure': {'spacegroup': 'Fd-3m'}, 'sampling': {'observed_temperature_kelvin': 300}}}},
            ]
            def database(config, workspace, conditions):
                self.assertEqual(conditions, {'formula': 'Si'})
                return records, {'conditions': conditions, 'match_count': 3, 'complete': True}
            service = SessionAIDB(root, 'crystal', query_database=database)
            service.bind(config)
            result = service.query({'formula': 'Si', 'structure_role': 'initial', 'spacegroup': 'Fd-3m', 'target_kelvin': 300})
            self.assertEqual([r['record_id'] for r in result['records']], ['initial-diamond'])
            self.assertEqual(result['report']['match_count'], 3)
            self.assertEqual(result['matched_count'], 1)

class ReceiptTests(unittest.TestCase):
    def test_duplicate_record_ids_are_incomplete_not_a_successful_query(self):
        import sys
        from matcreator.aidb.service import InstalledSkillQuery, AIDBError
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            script = root / 'external_skill.py'
            script.write_text('''import json,sys
from pathlib import Path
root=Path(sys.argv[sys.argv.index('--workspace')+1]); root.mkdir(exist_ok=True)
conditions=json.loads(Path(sys.argv[sys.argv.index('--conditions-json')+1]).read_text())
records=root/'records.jsonl'; records.write_text('{}'.format(json.dumps({'record_id':'same'}))+'\\n'+json.dumps({'record_id':'same'})+'\\n')
report={'status':'query_complete','complete':True,'conditions':conditions,'match_count':2,'report_path':str(root/'report.json'),'details_path':str(records)}
(root/'report.json').write_text(json.dumps(report)); print(json.dumps(report))
''')
            config = root / 'config.json'; config.write_text('{}')
            service = SessionAIDB(root, 'receipt', query_database=InstalledSkillQuery(script, sys.executable))
            service.bind(config)
            with self.assertRaises(AIDBError) as caught:
                service.query({'formula':'Si'})
            self.assertEqual(caught.exception.status, 'incomplete')

class QueryFailureTests(unittest.TestCase):
    def test_ambiguous_unsupported_and_zero_are_distinct(self):
        from matcreator.aidb.service import AIDBError
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); config = root/'config.json'; config.write_text('{}')
            def database(config, workspace, conditions):
                return [], {'complete':True,'conditions':conditions,'match_count':0}
            service = SessionAIDB(root,'query-states',query_database=database); service.bind(config)
            for conditions, status in [({},'needs_clarification'),({'formula':'Si','training_ready':True},'unsupported'),({'formula':'Si','spacegroup':'Fd-3m'},'needs_clarification')]:
                with self.subTest(conditions=conditions), self.assertRaises(AIDBError) as error:
                    service.query(conditions)
                self.assertEqual(error.exception.status,status)
            self.assertEqual(service.query({'formula':'C'})['matched_count'],0)

    def test_old_result_actions_expire_when_configuration_changes(self):
        from matcreator.aidb.service import AIDBError
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder); config=root/'config.json'; config.write_text('{}')
            service=SessionAIDB(root,'config-change',query_database=lambda *args: ([],{}))
            original=service.bind(config)
            service.bind(config)
            with self.assertRaises(AIDBError) as error:
                service.detail('old',binding_id=original['binding_id'])
            self.assertEqual(error.exception.status,'stale_binding')
            config.write_text('{"changed":true}')
            with self.assertRaises(AIDBError) as error:
                service.query({'formula':'Si'})
            self.assertEqual(error.exception.status,'stale_binding')

class FrameCountTests(unittest.TestCase):
    def test_same_source_copies_fold_but_independent_run_stays_visible(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder); config=root/'config.json'; config.write_text('{}')
            def record(record_id, digest):
                return {'record_id':record_id,'frame_index':2,'source_metadata':{'vasp_facts':{'source':{'sha256':{'vasprun.xml':digest}}}}}
            records=[record('copy-a','run-a'),record('copy-b','run-a'),record('independent','run-b')]
            service=SessionAIDB(root,'frame-counts',query_database=lambda c,w,q:(records,{'conditions':q,'match_count':3,'complete':True}))
            service.bind(config)
            result=service.query({'formula':'Si'})
            self.assertEqual(len(result['records']),2)
            self.assertEqual(result['coverage']['verified_frames'],2)
            self.assertEqual(result['duplicates'],1)
            self.assertEqual(result['report']['match_count'],3)


class MalformedReceiptTests(unittest.TestCase):
    def test_non_object_receipt_is_reported_as_incomplete(self):
        from types import SimpleNamespace
        from unittest.mock import patch
        from matcreator.aidb.service import InstalledSkillQuery, AIDBError
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder); script=root/'skill.py'; script.write_text('')
            with patch('subprocess.run', return_value=SimpleNamespace(stdout='[]',returncode=0)):
                with self.assertRaises(AIDBError) as error:
                    InstalledSkillQuery(script)(root/'config.json',root,{'formula':'Si'})
            self.assertEqual(error.exception.status,'incomplete')

if __name__ == '__main__':
    unittest.main()
