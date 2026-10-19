"""Protocol-critical regression cases: native normalization, scoring and assembly."""
import unittest
from .metrics import normalize_answer,normalize_trivia_answer,qa_score,vote,score
from .prompts import messages
from .analysis import paired_test
class ProtocolTests(unittest.TestCase):
 def test_native_punctuation_difference(self):
  self.assertEqual(normalize_answer('The U.S.'),'us')
  self.assertEqual(normalize_trivia_answer('The United-States'),'united states')
  self.assertEqual(normalize_trivia_answer('The_United_States'),'united states')
 def test_alias_em_and_token_overlap(self):
  self.assertEqual(qa_score('United States',['USA','The United States'])['em'],1)
  self.assertAlmostEqual(qa_score('New York City',['New York'],hotpot=True)['f1'],.8)
  self.assertEqual(qa_score('yes',['yes indeed'],hotpot=True)['f1'],0)
 def test_deterministic_vote(self):
  self.assertEqual(vote(['A','B','B','A','C']),'A')
 def test_anchor_citation_ranges(self):
  record={'task':'synthetic','expected_records':list(range(1,6)),'topic_terms':['goal']}
  text='Records 1-5 describe how the planner sets goals, checks dependencies, estimates time, updates schedules with documented reasons, and reports incomplete work explicitly.'
  self.assertEqual(score(record,text)['joint'],1)
  self.assertEqual(score(record,text+' Record 9.')['joint'],0)
 def test_shared_context_and_marker_placement(self):
  record={'task':'hotpotqa','context':'Immutable retrieved context.','question':'What is stated?'}
  a=messages(record,1,'front');b=messages(record,2,'front')
  self.assertEqual(a[0],b[0]);self.assertTrue(a[1]['content'].startswith('<SHARED_CONTEXT>\n\u200b\n'+record['context']))
  self.assertTrue(b[1]['content'].startswith('<SHARED_CONTEXT>\n\u200b\u200b\n'+record['context']))
 def test_identical_pairs_have_no_difference(self):self.assertEqual(paired_test([0.0]*30),1.0)
if __name__=='__main__':unittest.main()
