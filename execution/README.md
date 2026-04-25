# execution/

Runs one evaluation task end-to-end and turns the outcome into either an
`ExecutionResult` (success) or a classified `EvaluationRunException` (failure).

Pure host-side code — the container work lives in `ridges_harbor/`.

## Flow

```
                       evaluate(spec)
                            │
        prepare             │   parse spec
                            │   resolve task dir
                            │   materialize agent source
                            │   build run request
                            ▼
        run ────────────────┤   ridges_harbor.run_task()
        (ridges_harbor)     │       ▸ returns HarborRunSummary
                            ▼
        interpret           │   result_from_summary()
                            │
                    ┌───────┴───────┐
                    ▼               ▼
            numeric reward    runtime/verifier
                    │         failure
                    │               │
                    │        classify via
                    │        failure_classifier.py
                    ▼               ▼
            ExecutionResult   EvaluationRunException
              (patch, tests,    (error_code, detail,
               reward, logs)     logs)
```

## Files

- `engine.py`              entry point; owns `ExecutionEngine.evaluate()`
- `artifacts.py`           reads reward, tests, patch, and logs off the trial dir
- `failure_classifier.py`  maps a failure shape to a platform error code
- `types.py`               `ExecutionResult`, `ExecutionRunRequest`, `FailureContext`
- `errors.py`              `EvaluationRunException`

## How a run ends

There are exactly two outcomes:

- **Scored completion.** Harbor's verifier reported a numeric reward, a patch
  was produced, test results parsed. `ExecutionResult` is returned. Platform
  scoring treats `reward >= 1` as solved and lower rewards as unsolved.
- **Failure.** Miner crash, invalid patch, timeout, missing/non-numeric reward,
  unparseable artifacts, or Harbor infra break. `EvaluationRunException` is
  raised with a platform error code attached.

## Error codes, at a glance

Every raise carries an `EvaluationRunErrorCode`. Two families:

- `AGENT_*` — the miner is to blame. Crashed, produced an invalid patch,
  timed out during its own work, or failed the evaluation.
- `VALIDATOR_*` — infra is to blame. Harbor broke, the task spec was invalid,
  the runtime payload was unparseable, or the classifier can't tell what
  went wrong.

The whole point of `failure_classifier.py` is picking the right code. 
Each helper is a rule mapping one concrete failure shape to one code. 
Treat each function as a separate lookup, not an algorithm.

## Where the failure signal comes from

Most failure classification reads two sources:

- Harbor's own `ExceptionInfo` on the trial — timeout, non-zero exit, etc.
- `ridges_runtime.json`, written by the container-side runtime in
  `ridges_harbor/ridges_miner_runtime.py` when the miner itself crashes.

The second one is why `execution/` and `ridges_harbor/` are coupled loosely.
See `ridges_harbor/README.md` for what the container writes and why.
