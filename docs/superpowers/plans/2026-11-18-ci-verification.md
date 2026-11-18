# CI and installed MCP verification

**Spec:** https://github.com/hrabosh/wos-mcp-bridge/issues/10 and the agreed type-checking, Python matrix and stdio scope.

- Pin one development type checker in the existing uv lock. Check the production package and compatibility entrypoint, fixing diagnostics without global ignores.
- Install and test the locked project on Python 3.12 and 3.14. Keep export, lint and type checks on the minimum Python.
- Extend the existing distribution smoke check with SDK validation of stdout, a successful synthetic provider request and a tool error. Verify both archives from outside the checkout. Keep synthetic transport code outside the production package.
- Document the verified SDK and protocol combination, dependency audit, and remaining hosted verification under #8.
- Run the Python matrix, Ruff, type checks, workflow lint and artifact checks locally.

## Verification

Python 3.12.15 and 3.14.10 each passed 258 offline tests with one opt-in live
test skipped. Ruff, ty, actionlint and the lock check passed. Strict Twine and
archive validation passed for both distributions. The installed smoke check
validated discovery, a synthetic WoS result, a tool error and the complete
stdout stream through process exit. An injected shutdown log was rejected.

An audit found two advisories in the previous FastMCP 4.0.10 pin. The updated
4.1.7 lock passed the runtime dependency audit on both interpreters. The
remaining authenticated Streamable HTTP and target-client verification belongs to #8.
