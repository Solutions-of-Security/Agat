import hashlib, importlib, json, pathlib, sys, unittest
root=pathlib.Path('/repo')
names=['agat_worker','embedding_http','embedding_transport','web_tools','telemetry','local_decisions']
sys.path.insert(0,'/opt/agat')
verified={}
for name in names:
 m=importlib.import_module(name); path=pathlib.Path(m.__file__); assert path==pathlib.Path('/opt/agat')/(name+'.py')
 raw=path.read_bytes(); assert raw==(root/'workers'/(name+'.py')).read_bytes()
 verified[name]=hashlib.sha256(raw).hexdigest()
print(json.dumps({'python':sys.version,'verifiedImageModules':verified}),flush=True)
suite=unittest.defaultTestLoader.discover('/repo/workers',pattern='test_*.py')
result=unittest.TextTestRunner(verbosity=2).run(suite)
for name in names: assert pathlib.Path(sys.modules[name].__file__)==pathlib.Path('/opt/agat')/(name+'.py')
raise SystemExit(not result.wasSuccessful())
