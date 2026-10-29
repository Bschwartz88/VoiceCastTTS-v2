"""Setup integrity/refusal tests use synthetic files and perform no network installs."""
import base64,hashlib,json,sys,tempfile,unittest,zipfile
from pathlib import Path
from unittest import mock
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import setup_v2 as setup
class SetupTests(unittest.TestCase):
 def setUp(self):
  self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name).resolve()
  patch=mock.patch.object(setup,'ROOT',self.root);patch.start();self.addCleanup(patch.stop)
 def test_containment(self):
  for p in [self.root,self.root/'../outside']:
   with self.assertRaises(ValueError):setup.inside(self.root,p)
  self.assertEqual(setup.inside(self.root,self.root/'data'),self.root/'data')
 def test_runtime_checksum(self):
  p=self.root/'runtime.zip';p.write_bytes(b'fixture')
  with self.assertRaises(ValueError):setup.verify_runtime_archive(p)
  value=base64.b64encode(hashlib.sha512(b'fixture').digest()).decode()
  with mock.patch.object(setup,'RUNTIME_SHA512',value):setup.verify_runtime_archive(p)
 def test_archive_traversal(self):
  p=self.root/'runtime.zip'
  with zipfile.ZipFile(p,'w') as z:z.writestr('../outside.txt','fixture')
  with self.assertRaises(ValueError):setup.extract_runtime(p,self.root/'destination')
 def test_archive_expansion_limit(self):
  p=self.root/'runtime.zip'
  with zipfile.ZipFile(p,'w') as z:z.writestr('inside.txt','fixture')
  with mock.patch.object(setup,'MAX_EXTRACTED_BYTES',2):
   with self.assertRaises(ValueError):setup.extract_runtime(p,self.root/'destination')
 def test_https_only(self):
  with self.assertRaises(ValueError):setup.download('http://example.invalid',self.root/'x',100)
 def test_archive_symlink(self):
  p=self.root/'runtime.zip';item=zipfile.ZipInfo('link');item.create_system=3;item.external_attr=0o120777<<16
  with zipfile.ZipFile(p,'w') as z:z.writestr(item,'target')
  with self.assertRaises(ValueError):setup.extract_runtime(p,self.root/'destination')
 def test_wrong_base_never_installs(self):
  p=self.root/'.venv/Scripts/python.exe';p.parent.mkdir(parents=True);p.write_bytes(b'fixture')
  data={'prefix':str(self.root/'.venv'),'base':str(self.root/'wrong'),'version':[3,14,10]}
  with mock.patch.object(setup.subprocess,'check_output',return_value=json.dumps(data)),mock.patch.object(setup.subprocess,'run') as run:
   with self.assertRaises(ValueError):setup.prepare_environment(self.root/'.runtime/tools/python.exe')
   run.assert_not_called()
 def test_existing_model_must_match(self):
  name='kokoro-v1.0.onnx';(self.root/name).write_bytes(b'corrupt')
  with mock.patch.object(setup,'model_manifest',return_value=('https://example.invalid',{name:'0'*64})):
   with self.assertRaises(ValueError):setup.prepare_models()
  self.assertEqual((self.root/name).read_bytes(),b'corrupt')
 def test_offline_model_copy(self):
  cache=self.root/'cache';cache.mkdir();name='voices-v1.0.bin';data=b'fixture';(cache/name).write_bytes(data)
  with mock.patch.object(setup,'model_manifest',return_value=('https://example.invalid',{name:hashlib.sha256(data).hexdigest()})):
   setup.prepare_models(cache)
  self.assertEqual((self.root/name).read_bytes(),data)
 def test_manifest_read_does_not_execute(self):
  (self.root/'voice_cast.py').write_text("raise RuntimeError('never execute')\nMODEL_RELEASE_URL='https://example.invalid'\nMODEL_FILES={'kokoro-v1.0.onnx':'x','voices-v1.0.bin':'y'}\n")
  self.assertEqual(set(setup.model_manifest()[1]),{'kokoro-v1.0.onnx','voices-v1.0.bin'})
