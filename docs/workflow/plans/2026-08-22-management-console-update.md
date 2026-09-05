# Management-console update implementation plan

1. Add failing tests for the documented operation, path, parameter names, and upgrade refusal.
2. Introduce the console-specific update source and builder; remove the phantom HMC upgrade builder.
3. Compare operation, path, all parameter names, and enumerated values against the named
   Power10 and Power11 snapshot files and record that evidence in the design.
4. Update the tool documentation and README contract.
5. Run focused tests, full repository guardrails, adversarial review, and simplification.
