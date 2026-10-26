# Tutorial dataset

Small versions of real NOMAD runs, for the walkthrough notebooks, the README examples and CI.
Each file is the **first part of its run**, cut by time, so it is a genuine short run with its own
proton charge and duration rather than a thinned copy of the whole run.

| File | Role | Run | Kept | Size |
| --- | --- | --- | --- | --- |
| `NOM_244114.nxs.h5` | Sample: diamond, NeXus | 244114 (IPTS-37098) | first 40 pulses, 0.67 s | 4.8 MB |
| `m00000001-f00000001-run-244114.adara` | Sample: the same diamond run, ADARA | 244114 (IPTS-37098) | first 40 pulses, 0.67 s, closed by the run's END_RUN | 7.9 MB |
| `NOM_243710.nxs.h5` | Background: empty PAC06 can | 243710 (IPTS-37412) | first 150 pulses, 2.5 s | 3.0 MB |
| `NOM_243712.nxs.h5` | Normalization: vanadium rod | 243712 (IPTS-37412) | first 30 pulses, 0.5 s | 3.2 MB |

The two sample files hold the same 40 pulses of the same run, so they give the same I(Q). The
instrument definition and calibration the preparer needs are in `tests/data/idf/` and
`tests/data/calibration/`. `manifest.json` lists each file's source, events, charge and SHA-256.

Reduced and corrected together, either sample reproduces the full-data I(Q) to 95.9 %, with every
strong diamond peak within 0.002 1/Å of its full-data position. These sizes, and how they were
chosen, are in `scripts/tutorial_dataset/README.md`.

Two things differ from the full runs:

- A 0.67 s sample needs a short error-decay sampling interval:
  `--statistics-sample-interval-seconds 0.02` gives about 20 samples.
- User groups are removed from the NeXus files, and only the metadata the analyzer and preparer
  read is kept.

Regenerate the files from the full data, which needs `nexus_files/` and the carbon NFS share, with:

```bash
uv run python scripts/tutorial_dataset/make_tutorial_dataset.py
```
