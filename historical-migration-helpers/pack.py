import hashlib,json,os,pathlib,stat,tarfile
base=pathlib.Path.cwd();dest=base/'work/host-migration-20261008'; records=[];total=0
for directory,ds,fs in os.walk(base/'work',followlinks=False):
 if pathlib.Path(directory)==base/'work': ds[:]=sorted(d for d in ds if d not in {'ml-venv','host-migration-20261008'})
 else:ds.sort()
 for name in sorted(fs):
  p=pathlib.Path(directory)/name;s=p.lstat()
  if not stat.S_ISREG(s.st_mode):raise ValueError('non_regular_payload')
  h=hashlib.sha256()
  with p.open('rb') as f:
   for b in iter(lambda:f.read(1048576),b''):h.update(b)
  after=p.stat()
  if (s.st_size,s.st_mtime_ns)!=(after.st_size,after.st_mtime_ns):raise ValueError('active_payload_writer')
  records.append(dict(path=str(p.relative_to(base)),bytes=s.st_size,sha256=h.hexdigest(),mtime_ns=s.st_mtime_ns));total+=s.st_size
manifest=dest/'files.jsonl'
with manifest.open('x') as f:
 for r in records:f.write(json.dumps(r,sort_keys=True)+'\n')
with tarfile.open(dest/'research-work.tar.gz','x:gz',compresslevel=1) as archive:
 for r in records:
  p=base/r['path'];s=p.stat()
  if (s.st_size,s.st_mtime_ns)!=(r['bytes'],r['mtime_ns']):raise ValueError('active_payload_writer')
  archive.add(p,arcname=r['path'],recursive=False)
summary=dict(files=len(records),bytes=total,manifest_sha256=hashlib.sha256(manifest.read_bytes()).hexdigest(),archive_sha256=hashlib.sha256((dest/'research-work.tar.gz').read_bytes()).hexdigest(),source=str(base),target='/Users/root1/Developer/memecoin-research',excluded=['work/ml-venv','work/host-migration-20261008'],user_untracked_retained={'.codex/config.toml':hashlib.sha256((base/'.codex/config.toml').read_bytes()).hexdigest()})
(dest/'package.json').write_text(json.dumps(summary,sort_keys=True)+'\n');print(json.dumps(summary))
