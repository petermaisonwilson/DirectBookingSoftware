Describe the problem and resulting behaviour, including any business-rule or
architecture decision requiring owner approval.

Validation (link exact workflow/commit and explain any outstanding check):

- [ ] SQLite central integration and complete operational acceptance pass.
- [ ] Genuine PostgreSQL central integration and complete operational acceptance pass.
- [ ] Fresh initialisation, native backup/restore and packaged startup pass on both engines.
- [ ] Tenant scope, monetary constraints, pricing/payment rules and atomic history preserved.
- [ ] Schema changes include both-engine upgrade and failure-rollback verification.
- [ ] No unapproved Setup/tenant data deletion or historical conversion utility.
