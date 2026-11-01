# MERISTEM-BOUNDARY — Quickstart

Run the served proofs for the boundary rows from the repository root:

```
python3 -m pytest -q tests/gates/test_boundary_owner_guard.py tests/gates/test_boundary_removal_census.py tests/test_retired_quantum_security_standardization.py
```

- `MERISTEM-BOUNDARY-R028.1`: `tests/test_retired_quantum_security_standardization.py::test_standardization_retirement_holds`.
- `MERISTEM-BOUNDARY-R031`: `tests/gates/test_boundary_removal_census.py::test_graph_schema_dtos_replaced_by_engine_types`.
- `MERISTEM-BOUNDARY-R038`: `tests/gates/test_boundary_owner_guard.py::test_owner_guard_rejects_foreign_module_even_if_registered`.
