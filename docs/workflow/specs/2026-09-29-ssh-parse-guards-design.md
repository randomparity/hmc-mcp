# One empty-result and parse guard for header-bearing SSH reads (#892)

## Problem

The SSH readers of header-bearing (`-F … --header`) output each check the HMC's
`No results were found.` sentinel and wrap `parse_hmc_delimited_rows`' `ValueError` their own
way. `vnic.py`'s `list_vnic_rows`, `list_vnic_backing_rows` and `read_vios_identity` do not wrap
at all, so a malformed response escapes as a bare `ValueError` where `sriov.py` and `refcodes.py`
raise `HMCCLIError` for the same fault.

## Scope

`ssh/commands.py` owns the guard (clean extension beside the parser it wraps; no ownership move).

- `HMC_NO_RESULTS = "No results were found."` and
  `parse_hmc_result_rows(text, fields, operation, *, blank_is_empty=False)`: returns `[]` for
  the sentinel (and for blank text when `blank_is_empty`), otherwise returns
  `parse_hmc_delimited_rows(text, fields)` and re-raises its `ValueError` as `HMCCLIError`:
  `"{operation} response did not match the expected {','.join(fields)} fields: {detail}"`.
- `sriov.py` drops `_parse_admitted_rows` for the helper with operation `SR-IOV inventory`.
- `vnic.py` `list_vnic_rows`, `list_vnic_backing_rows`, `read_vios_identity` use it. The
  one-row check in `read_vios_identity` stays a `ValueError`: zero rows means the caller named
  a VIOS the system does not have, which is an input fault, not a malformed response.
- `refcodes.py` uses it with `blank_is_empty=True`, dropping its private `_NO_RESULTS` and the
  comment that lists the other readers' copies of the literal.
- `vios_labels.py` keeps its `csv.reader` parser — its header is whatever attributes the HMC
  prints, so there is no field tuple to match — and imports `HMC_NO_RESULTS`; a comment records
  that reason. It already raises `HMCCLIError` naming the operation.
- Blank output keeps each reader's current behaviour: `[]` in `refcodes.py` and
  `vios_labels.py`, a header fault (now `HMCCLIError`) in `sriov.py` and `vnic.py`.
- `CHANGELOG.md` records the `ValueError` → `HMCCLIError` change on the three `vnic.py` reads.

### Failure model

1. Actors: an operator's CLI or an MCP agent reading inventory; the HMC as the untrusted
   response source.
2. Invariants: every header-bearing reader in scope raises `HMCCLIError` for a response it
   cannot parse; the sentinel is never parsed as data.
3. Accepted: a programming error in a constant field tuple would also surface as
   `HMCCLIError`, as it already does in `sriov.py` and `refcodes.py`. The new type reaches
   PCIe-assignment prevalidation (`assignments.py` → `dlpar.py`, `provision.py`,
   `workflows.py`) and vNIC add/remove preflight; none of those catches `ValueError`
   specifically, no handler above them distinguishes the two types, and the post-mutation readback
   catches `Exception`.
4. Covered elsewhere: `list_vnics` and other `parse_hmc_delimited_rows` callers (`affinity.py`,
   `io_inventory.py`, `profiles.py`) — excluded by the operator; `validate_hmc_name` — #887.

## Success

- A header mismatch raises `HMCCLIError` naming the operation and fields from every reader in
  scope; the sentinel returns `[]` (or, for `read_vios_identity`, the zero-row `ValueError`).

## Validation

- Helper sentinel, blank and wrap cases — focused-test, `tests/unit/test_ssh_commands.py` or
  the nearest existing parser test module; red: helper absent.
- Header mismatch per reader — focused-test in `tests/unit/test_vnic_ssh_contract.py`,
  `test_sriov_ssh_contract.py`, `test_ssh_refcodes.py`, `tests/vios/test_vios_labels.py`;
  bite: revert one reader to the bare parser and observe red. The existing
  `test_collectors_reject_malformed_rows` moves from `ValueError` to `HMCCLIError`.
- `just test`, `just verify`, `prek run --all-files`.
