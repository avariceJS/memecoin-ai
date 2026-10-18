import hashlib,json,pathlib,subprocess,time,urllib.request
root=pathlib.Path('/Users/root1/Desktop/memecoin-ai')
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest() if p.exists() else None
with urllib.request.urlopen('http://127.0.0.1:8792/api/status',timeout=10) as r:s=json.load(r)
paths=['work/brain-b13/operator04/deployment.json','work/brain-b13/operator04/config.json','work/brain-b13/source-repaired02/config.json','work/brain-b13/source-repaired02/universe.json','work/brain-b13/source-repaired02/STOP']
result=dict(at_ns=time.time_ns(),owner=str(root),head=subprocess.check_output(['git','rev-parse','HEAD'],cwd=root,text=True).strip(),dirty=subprocess.check_output(['git','status','--porcelain'],cwd=root,text=True).strip(),deployment=json.loads((root/paths[0]).read_text()),files={p:sha(root/p) for p in paths},status=dict(revision=s.get('revision'),mode=s.get('mode'),champion=s.get('champion'),busy_jobs=[dict(job_id=j['job_id'],status=j['status']) for j in s.get('jobs',[]) if j['status'] in {'queued','running','stopping'}]))
print(json.dumps(result,sort_keys=True))
