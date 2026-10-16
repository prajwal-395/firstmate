# Heavy-work admission

`library.tools.heavy_work_lock run` admits command-line work through the same
resource scheduler used by pipeline capabilities. Named profiles protect
declared exclusive resources; `--ram-gb` adds a measured memory reservation to
that profile. Reservations are checked against live available memory and the
reservations already running, while jobs without a reservation remain
unthrottled by memory.

For a model benchmark, wrap the runner with its measured peak resident
footprint, for example:

```sh
bin/vep -m library.tools.heavy_work_lock run --owner <benchmark-id> \
  --profile machine --ram-gb <measured-peak-gb> -- \
  bin/vep -m <benchmark.module> <arguments>
```

Use the narrowest applicable named profile when the command also needs a
declared exclusive resource. `--ram-gb` must be a finite positive number.
