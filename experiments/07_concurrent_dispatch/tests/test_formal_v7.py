"""Restricted unrelated /proc entries cannot hide possible owned members."""
import unittest,pathlib,sys,os
from unittest.mock import patch
sys.path.insert(0,str(pathlib.Path(os.environ['G_FORMAL_REVISION_ROOT'])/'code'))
import gformal_monitor as mon
class ProcScope(unittest.TestCase):
 def setup_scope(self):
  owner=dict(pid=100,pgid=100,session=100,pid_namespace='pid:[test]')
  paths=[pathlib.Path('/proc/1'),pathlib.Path('/proc/100')]
  return owner,paths
 def test_unrelated_denied_namespace_is_not_read(self):
  owner,paths=self.setup_scope()
  def identity(pid):
   if pid==1:raise PermissionError('unrelated protected init')
   return dict(owner,state='S')
  with patch.object(pathlib.Path,'iterdir',return_value=paths),patch.object(mon,'proc_group_hint',side_effect=lambda pid:('S',pid,pid)),patch.object(mon,'proc_identity',side_effect=identity) as fn:
   self.assertEqual(mon.local_group(owner),[dict(owner,state='S')]);fn.assert_called_once_with(100)
 def test_possible_member_denied_namespace_fails_closed(self):
  owner,paths=self.setup_scope()
  with patch.object(pathlib.Path,'iterdir',return_value=paths),patch.object(mon,'proc_group_hint',return_value=('S',100,100)),patch.object(mon,'proc_identity',side_effect=PermissionError('possible member')):
   with self.assertRaises(PermissionError):mon.local_group(owner)
 def test_unknown_stat_permissions_fail_closed(self):
  owner,paths=self.setup_scope()
  with patch.object(pathlib.Path,'iterdir',return_value=paths),patch.object(mon,'proc_group_hint',side_effect=PermissionError('unknown group')),patch.object(mon,'proc_identity') as fn:
   with self.assertRaises(PermissionError):mon.local_group(owner)
   fn.assert_not_called()
 def test_hint_cannot_override_final_identity(self):
  owner,paths=self.setup_scope()
  with patch.object(pathlib.Path,'iterdir',return_value=paths),patch.object(mon,'proc_group_hint',return_value=('S',100,100)),patch.object(mon,'proc_identity',return_value=dict(owner,state='S',pid_namespace='pid:[other]')):
   self.assertEqual(mon.local_group(owner),[])
