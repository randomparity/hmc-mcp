# OpenSSF Scorecard baseline

The first repository Scorecard scan completed on 2026-08-14 with Scorecard CLI
v5.5.0 against `github.com/randomparity/hmc-mcp`. The overall score was **5.3**.
This is an observation for prioritizing later work, not a pass threshold.

| Check | Score | Observed reason |
|---|---:|---|
| Binary-Artifacts | 10 | No binaries found in the repository. |
| Branch-Protection | 0 | Branch protection was not enabled on development or release branches. |
| CI-Tests | 10 | All five sampled merged pull requests were checked by CI. |
| CII-Best-Practices | 0 | No OpenSSF best-practices badge effort was detected. |
| Code-Review | 0 | None of the five sampled changesets had an approval. |
| Contributors | 6 | Two contributing companies or organizations were detected. |
| Dangerous-Workflow | 10 | No dangerous workflow patterns were detected. |
| Dependency-Update-Tool | 10 | A dependency update tool was detected. |
| Fuzzing | 0 | No fuzzing was detected. |
| License | 0 | No license file was detected. |
| Maintained | 0 | The project was created within the previous 90 days. |
| Packaging | -1 | No packaging workflow was detected. |
| Pinned-Dependencies | 10 | All detected dependencies were pinned. |
| SAST | 0 | A SAST tool was not run on all commits. |
| Security-Policy | 0 | No security policy file was detected. |
| Signed-Releases | -1 | No releases were found. |
| Token-Permissions | 10 | Workflow tokens followed least privilege. |
| Vulnerabilities | 10 | No existing vulnerabilities were detected. |

Future scheduled workflow runs publish authenticated results and retain the SARIF
artifact for five days. Score changes should be reviewed as evidence; this repository
does not impose a numeric Scorecard gate.
