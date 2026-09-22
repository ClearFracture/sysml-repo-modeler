# Architecture classifier evaluation

This helper measures the architecture classification boundary independently from
SysML generation. It compares deterministic evidence candidates and repeated Jev
answers with a source-backed gold case, then reports:

- candidate precision and recall;
- accepted-decision accuracy, coverage, and abstention;
- probability calibration and run-to-run decision flips;
- latency, token use, and estimated Jev cost;
- optional structural and source-reference scores for generated SysML files.

An abstention is not counted as a wrong accepted answer. `selectiveScore` combines
accuracy and coverage by dividing correct accepted answers by all gold decisions.
The probability metrics include answers below the acceptance threshold, allowing
confidence calibration to be evaluated without forcing a classification.

## OSSN evaluation

Check out the case's pinned commit, install the backend with its optional Jev
dependency, set `TYPESAFE_API_KEY`, and run:

```powershell
python src/eval-helpers/architecture-eval/run.py `
  --case src/eval-helpers/architecture-eval/cases/ossn.json `
  --repo C:\path\to\opensource-socialnetwork `
  --jev-runs 10
```

The JSON report is written to `dist/architecture-eval/` by default. Generated
reports are intentionally ignored by Git. Use `--inventory` with a captured
architecture JSON document to score a non-Jev baseline, and `--sysml` to score
one or more generated models against the case's source-backed concepts. The URL
equivalents accept `LABEL=URL`, for example `--inventory-url no-jev=http://...`.

This is a selective-classification evaluation, not a claim that one repository is
representative of every architecture. Add independent gold cases before treating
the aggregate as a general accuracy number.
