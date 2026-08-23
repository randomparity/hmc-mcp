# Safe storage mapping detach implementation plan

1. Add transport tests that demonstrate the unsupported DELETE and define exact parent
   read-modify-write behavior, preservation, and fail-closed errors.
2. Replace `delete_storage_mapping` with strict parent XML selection and POST behavior,
   sharing only existing narrow parsing helpers where their contracts already fit.
3. Add operation and MCP-tool tests proving the existing UUID selector crosses each
   boundary unchanged. Correct CLI inventory to display parsed `UUID`, add a realistic
   list-to-detach boundary test, and adjust wording that implies a child-resource delete.
4. Run focused storage and API tests, then `just test`, `just smoke`, and `just verify`.
5. Review the branch adversarially, disposition findings, simplify, and deliver a green,
   mergeable PR without merging it.
