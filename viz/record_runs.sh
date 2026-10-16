#!/bin/zsh
# Re-run every stage on the real model and keep its terminal output next to it (<stage>/output.txt).
# Needs `make platform` running and GEMINI_API_KEY set. About 25 minutes on the free tier.
set -u
cd "$(dirname "$0")/.."
export PYTHONUNBUFFERED=1
run() { local dir=$1; shift; echo "\$ python $dir/$*" > $dir/output.txt; .venv/bin/python "$dir/$@" >> $dir/output.txt 2>&1; }
run 01-agent-loop run.py
run 02-explicit-graph run.py
run 03-supersteps run.py
run 04-checkpoints run.py
run 05-side-effects run.py
{ echo "\$ python 06-reliability/arithmetic.py"; .venv/bin/python 06-reliability/arithmetic.py; echo; } > 06-reliability/output.txt
{ echo "\$ python 06-reliability/run.py"; .venv/bin/python 06-reliability/run.py 2>&1; } >> 06-reliability/output.txt
run 07-langgraph run.py
curl -s http://127.0.0.1:8700/runs > viz/data/runs-index.json
for id in $(python3 -c "import json;print(' '.join(r['id'] for r in json.load(open('viz/data/runs-index.json'))))"); do
  curl -s "http://127.0.0.1:8700/runs/$id" > "viz/data/run-$id.json"
done
echo done
