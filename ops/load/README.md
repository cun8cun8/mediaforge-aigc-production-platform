# MediaForge Read-Only Load Baseline

`studio-readonly.mjs` only requests read endpoints. It never creates projects,
submits generation work, invokes a Provider, or causes billable media output.
Run it against a staging gateway that has the same authentication, database,
queue, artifact storage, Provider routing, and observability topology as the
release target.

Install a reviewed [k6](https://grafana.com/docs/k6/latest/set-up/install-k6/)
binary outside this repository, then run:

```sh
export MEDIAFORGE_LOAD_BASE_URL=https://staging.mediaforge.example
export MEDIAFORGE_LOAD_TOKEN='readiness-or-admin-token'
export MEDIAFORGE_LOAD_PROJECT_ID='approved-staging-project'
k6 run ops/load/studio-readonly.mjs
```

The default profile ramps to 10 virtual users for two minutes. Adjust
`MEDIAFORGE_LOAD_VUS`, `MEDIAFORGE_LOAD_RAMP_UP`, `MEDIAFORGE_LOAD_HOLD`,
`MEDIAFORGE_LOAD_RAMP_DOWN`, `MEDIAFORGE_LOAD_MAX_P95_MS`, and
`MEDIAFORGE_LOAD_MAX_FAILURE_RATE` to the approved capacity target. Keep the
load token outside source control. Record the k6 output with the deployed image
digest, infrastructure topology, provider pool, and database/queue sizing; a
successful local Mock run is not a production capacity result.
