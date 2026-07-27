# Demo corpus item

A tiny hand-made gold matrix + a sample extracted matrix so the eval harness
has a committed, runnable example (real corpus items live in `evals/corpus/`,
which is not committed — they contain full solicitations):

```bash
python -m evals.harness evals/corpus_demo/sample_extracted_matrix.json evals/corpus_demo/gold_matrix.csv
# recall: 100.0%  (G2 target >= 98%) PASS
```

The extracted sample deliberately contains one extra row (`REQ-001`) — extra
rows are cheap, misses are catastrophic; only recall gates.
