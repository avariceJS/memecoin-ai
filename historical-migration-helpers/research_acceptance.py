import json,pathlib,platform,subprocess
import numpy as np
import torch
from memecoin_experience import generations,observations
from memecoin_experience.storage import Snapshot,file_hash
from memecoin_experience.learning import batches
from memecoin_experience.predict import Predictor
root=pathlib.Path('/Users/root1/Developer/memecoin-research');case=root/'work/brain-sparse-qualification-20261008/real02'
report=json.loads((case/'result.json').read_text());job=json.loads((case/'declaration.json').read_text())['job']
source=observations.Source(case/'observations',report['source'],role='train')
assert sum(1 for _ in source.rows())==report['source_counts']['source_facts']
snapshot=Snapshot(case/'experience',report['snapshot']);rows=0
for i in range(len(snapshot.parts)):
 _,metadata=snapshot.partition(i);rows+=len(metadata)
assert rows==report['source_counts']['emitted_origins']
record,artifact=generations.get(case/'models',report['training']['generation']['generation'])
assert record['artifact']==report['training']['generation']['artifact']
x=next(batches(snapshot,job['split'],'test',16))[0]
y=Predictor(artifact,record['artifact']).predict(x);assert np.isfinite(y).all()
print(json.dumps(dict(host='mac1',research=str(root),head=subprocess.check_output(['git','rev-parse','HEAD'],cwd=root,text=True).strip(),python=platform.python_version(),torch=torch.__version__,numpy=np.__version__,source=report['source'],snapshot=report['snapshot'],artifact=record['artifact'],source_facts=source.manifest['records'],origins=rows,portable_prediction_finite=True,receipt=file_hash(root/'docs/evidence/brain-sparse-experience-2026-10-08.json')),sort_keys=True))
