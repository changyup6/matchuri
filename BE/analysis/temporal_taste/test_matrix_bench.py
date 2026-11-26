"""Small artificial IDs validate isolation/denominators, not model quality."""
import copy
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch
import numpy as np
from analysis.temporal_taste import matrix_bench as b

class MatrixContractTests(unittest.TestCase):
    def fixture(self):
        ids=[f's{i}' for i in range(8)]
        common={'family':'cutlet_cuisine','context':'cutlet','seed':0,'current':[['s2',True],['s3',False]],'context_ids':ids[2:],'heldout_ids':ids[4:],'evaluation_groups':{'a':['s4','s5'],'b':['s5','s6'],'empty':[]}}
        return {'schema_version':1,'catalog_ids':ids,'brand_keys':{s:s for s in ids},'runs':[{**copy.deepcopy(common),'id':'none','prior':'none','history_n':0,'past':[]},{**copy.deepcopy(common),'id':'neutral','prior':'neutral','history_n':2,'past':[['s0',True],['s1',False]]}]}
    def test_valid_overlapping_and_empty_groups(self):
        d=self.fixture();before=copy.deepcopy(d);b.validate(d,d['catalog_ids']);self.assertEqual(d,before)
    def test_missing_control(self):
        d=self.fixture();d['runs'].pop()
        with self.assertRaises(ValueError):b.validate(d,d['catalog_ids'])
    def test_changed_current_order(self):
        d=self.fixture();d['runs'][1]['current'].reverse()
        with self.assertRaises(ValueError):b.validate(d,d['catalog_ids'])
    def test_repeated_current_store_even_when_all_arms_match(self):
        d=self.fixture()
        for run in d['runs']:
            run['current'].append(['s2', True])
        with self.assertRaises(ValueError):b.validate(d,d['catalog_ids'])
    def test_changed_prior_order(self):
        d=self.fixture();r=copy.deepcopy(d['runs'][1]);r.update(id='j',prior='japanese');r['past'].reverse();d['runs'].append(r)
        with self.assertRaises(ValueError):b.validate(d,d['catalog_ids'])
    def test_heldout_brand_leak(self):
        d=self.fixture();d['brand_keys']['s4']='s0'
        with self.assertRaises(ValueError):b.validate(d,d['catalog_ids'])
    def test_current_prior_brand_leak(self):
        d=self.fixture();d['brand_keys']['s2']='s0'
        with self.assertRaises(ValueError):b.validate(d,d['catalog_ids'])
    def test_missing_group_member(self):
        d=self.fixture();d['runs'][1]['evaluation_groups']['a'].append('x')
        with self.assertRaises(ValueError):b.validate(d,d['catalog_ids'])
    def test_context_outside_catalogue(self):
        d=self.fixture();d['runs'][0]['context_ids'].append('x')
        with self.assertRaises(ValueError):b.validate(d,d['catalog_ids'])
    def test_metrics_fixed_denominators_and_empty_overlap(self):
        d=self.fixture();r=d['runs'][1];ids=d['catalog_ids'];art=SimpleNamespace(ids=ids,pos={s:i for i,s in enumerate(ids)})
        mu=np.arange(8,dtype=float);sd=np.ones(8);decision=SimpleNamespace(DEFAULT=SimpleNamespace(uncertainty_weight=.5),_diverse=lambda *x:True)
        diversity=SimpleNamespace(pair_redundancy=lambda *x:.25);slate={'store_ids':ids[4:],'qualified_count':4}
        result=b.describe(r,art,{s:s for s in ids},d['brand_keys'],mu,sd,slate,set(ids[4:]),decision,diversity)
        self.assertEqual(result['context_count'],6);self.assertEqual(result['eligible_count'],4);self.assertEqual(result['heldout_count'],4)
        self.assertEqual(result['groups']['a']['all']['count'],2);self.assertEqual(result['groups']['a']['all']['mean'],4.5)
        self.assertEqual(result['groups']['b']['all']['mean'],5.5);self.assertIsNone(result['groups']['empty']['heldout']['mean'])
        self.assertEqual(result['groups']['a']['heldout']['hits']['cards'],2);self.assertEqual(result['context_card_hits'],4)
        self.assertTrue(all(x['mean_pair_win'] is None for x in result['group_comparisons'] if x['b']=='empty'))
        self.assertEqual([x['store_id'] for x in result['top20']],list(reversed(ids[4:])))
        self.assertEqual(len(result['raw_global_top20']),8);np.testing.assert_array_equal(mu,np.arange(8))
    def test_runtime_copies_into_temporary_directory_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            source=Path(tmp)/'original.cache';source.write_bytes(b'cached-ranks');locations=[]
            @contextmanager
            def fake_runtime(data,serving):
                with tempfile.TemporaryDirectory() as state:
                    p=Path(state)/'review-ranks.cache';locations.append(p);self.assertEqual(p.read_bytes(),source.read_bytes());p.write_bytes(b'disposable');yield ('a','b','c')
            with patch.object(b,'offline_runtime',fake_runtime):
                with b.runtime(Path(tmp),Path(tmp),source) as value:self.assertEqual(value,('a','b','c'))
            self.assertEqual(source.read_bytes(),b'cached-ranks');self.assertFalse(locations[0].exists())

if __name__=='__main__':unittest.main(verbosity=2)
