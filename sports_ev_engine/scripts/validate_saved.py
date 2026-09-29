"""Reproduce the bundled raw-model report without keys or network."""
import sys,json
from pathlib import Path
import pandas as pd
root=Path(__file__).resolve().parents[1];sys.path.insert(0,str(root))
from sports_ev_engine.validation import validate
blob=json.loads((root/'data/validation_history.json').read_text())
for r in blob['records']:r['date']=pd.Timestamp(r['date']).date()
report,_=validate(blob['records'],blob['as_of'],max_events=900)
print(json.dumps(report,ensure_ascii=False,indent=2))
