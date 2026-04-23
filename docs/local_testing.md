# Local Testing

`ridges` supports a relaxed local Harbor runner for miner testing.

Use `docs/harbor_local_testing.md` for the current runtime boundary:
- validators execute promoted `harbor_remote_task` specs only
- local Harbor task experimentation is available through:
  - `ridges miner run-local`
  - the Python API at `miners.run_local_task(...)`
