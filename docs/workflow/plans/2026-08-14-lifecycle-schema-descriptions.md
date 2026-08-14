# Lifecycle schema descriptions implementation plan

1. Add a failing exhaustive rendered-schema contract test for the eight module
   registries and the three nested dataclass schemas.
2. Add Google-style argument documentation to every in-scope public tool.
3. Add standard dataclass field description metadata to nested inputs.
4. Pin job/wait, ADR 0011 ownership, units, terminal-state, provisioning-result,
   and README semantics with focused assertions.
5. Run focused tests, format/lint checks, ``just verify``, adversarial review,
   security review, simplification, and delivery checks.
