import unittest
from match_engine import normalize,area_m2,decide

class MatchingTests(unittest.TestCase):
    def test_inferred_identifier_never_becomes_automatic_evidence(self):
        self.assertEqual(decide(dict(contract_id='C',inferred_keys=['contract_id']),[dict(contract_id='C')])[0],'NEEDS_FUZZY')
    def test_archive_number_never_proves_or_blocks_project_identity(self):
        status,ranked=decide(dict(archive_id='same',name='甲方道路工程'),[dict(archive_id='same',name='乙方住宅工程')])
        self.assertNotEqual(status,'MATCH_EXACT')
        self.assertTrue(all('archive_id' not in c['exact'] for c in ranked))
        self.assertEqual(decide(dict(archive_id='a',contract_id='C'),[dict(archive_id='b',contract_id='C')])[0],'MATCH_EXACT')
    def test_normalization(self):
        self.assertEqual(normalize('赣〔２０２０〕 31 号'),normalize('赣[2020]31号'))
        self.assertEqual(normalize(None),'')
    def test_units(self):
        self.assertAlmostEqual(area_m2(15,'亩'),10000)
        self.assertEqual(area_m2(1,'公顷'),10000)
        self.assertIsNone(area_m2(10,'未知'))
    def test_blank_keys_do_not_match(self):
        self.assertEqual(decide({'name':'华佗路'},[{'name':'渊明路'}])[0],'NOT_FOUND')
    def test_conflicting_identifiers(self):
        status,_=decide({'name':'华佗路','project_code':'A','contract_id':'1'},[{'name':'华佗路','project_code':'A','contract_id':'2'}])
        self.assertEqual(status,'CONFLICT')
    def test_shared_project_code_is_not_unique(self):
        self.assertEqual(decide({'project_code':'A'},[{'project_code':'A','contract_id':'1'},{'project_code':'A','contract_id':'2'}])[0],'AMBIGUOUS')
    def test_specific_identifier_disambiguates(self):
        self.assertEqual(decide({'project_code':'A','contract_id':'1'},[{'project_code':'A','contract_id':'1'},{'project_code':'A','contract_id':'2'}])[0],'MATCH_EXACT')
    def test_fuzzy_is_never_auto_written(self):
        self.assertEqual(decide({'name':'华佗路道路工程'},[{'name':'华佗路道路建设工程'}])[0],'NEEDS_FUZZY')
    def test_area_only_is_a_candidate_without_automatic_assignment(self):
        status,candidates=decide({'area_m2':10000},[{'area_m2':10050}])
        self.assertEqual(status,'NEEDS_FUZZY');self.assertEqual(len(candidates),1)

if __name__=='__main__':unittest.main()
