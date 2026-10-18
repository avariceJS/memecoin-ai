import hashlib,json,pathlib,tarfile,time,os
stage=pathlib.Path('/Users/root1/Developer/.memecoin-migration-20261008'); target=pathlib.Path('/Users/root1/Developer/memecoin-research')
def sha(path):
 h=hashlib.sha256()
 with path.open('rb') as f:
  for b in iter(lambda:f.read(1048576),b''):h.update(b)
 return h.hexdigest()
package=json.loads((stage/'package.json').read_text())
assert sha(stage/'files.jsonl')==package['manifest_sha256']
assert sha(stage/'research-work.tar.gz')==package['archive_sha256']
assert not target.exists()
repo=stage/'research'; assert (repo/'.git').is_dir(); assert not (repo/'work').exists()
count=0
with tarfile.open(stage/'research-work.tar.gz','r:gz') as archive:
 for member in archive:
  path=pathlib.PurePosixPath(member.name)
  assert member.isfile() and path.parts[0]=='work' and '..' not in path.parts
  archive.extract(member,repo,filter='data');count+=1
assert count==package['files']
seen=set();total=0
with (stage/'files.jsonl').open() as f:
 for line in f:
  record=json.loads(line);p=repo/record['path'];assert record['path'] not in seen;seen.add(record['path'])
  assert p.is_file() and not p.is_symlink() and p.stat().st_size==record['bytes'] and sha(p)==record['sha256']
  total+=record['bytes']
assert total==package['bytes']
actual={str(p.relative_to(repo)) for p in (repo/'work').rglob('*') if p.is_file()}
assert actual==seen
os.rename(repo,target)
report=dict(at_ns=time.time_ns(),target=str(target),files=count,bytes=total,manifest_sha256=package['manifest_sha256'],archive_sha256=package['archive_sha256'],all_payload_hashes_verified=True,atomic_checkout_activation=True)
(stage/'acceptance.json').write_text(json.dumps(report,sort_keys=True)+'\n');print(json.dumps(report))
