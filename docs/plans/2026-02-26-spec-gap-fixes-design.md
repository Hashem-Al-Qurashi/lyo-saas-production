# Spec Gap Fixes - Design & Implementation Plan

**Date:** 2026-02-26
**Source:** PROJECT LYO ASSISTANT 2.pdf gap analysis
**Approach:** Minimal Patch (Approach A)

## Items

| # | Item | Scope |
|---|------|-------|
| 1 | Treatment description + notes in management UI | Expose existing DB columns in forms |
| 2 | Operator notes in management UI | Expose existing DB column in forms |
| 3 | Salon rules (deposit, cancellation, punctuality) | Structured fields in JSONB settings |
| 4 | Bot pause button | SKIP - Chatwoot built-in is sufficient |
| 5 | Role-based permissions (owner vs staff) | Permission checks + JWT role + nav hiding |
| 6 | Bot-to-user assignment | SKIP - Chatwoot account config, not code |

## Assembly Line (Per Task)

1. Planner context (read relevant files)
2. TDD - write failing tests first
3. Implement minimal code to pass
4. Code-reviewer agent - reviews against plan
5. Fix issues
6. Grumpy-tester agent - tries to break it
7. Fix anything found
8. Verification - manual curl tests as proof
9. Commit

## Hard Gates (Python)

```bash
python -m py_compile <changed_files>
python -m pytest tests/ -x
curl verification of each feature
```

---

## Task 1: Treatment Description + Notes in UI

### Files to modify:
- `management/templates/treatments.html`
- `management/routes/treatments.py`
- `app/services/ai.py` (system prompt to include descriptions)

### Steps:
1. Write test: POST to `/manage/treatments/add` with description_it, description_en, notes fields -> verify they're stored in DB
2. Write test: POST to `/manage/treatments/{id}/edit` with description/notes -> verify they're updated
3. Write test: GET `/manage/treatments/` -> verify description/notes appear in response HTML
4. Update `list_treatments` SELECT query to include `description_it, description_en, notes`
5. Update `add_treatment` route: add Form params for description_it, description_en, notes; update INSERT
6. Update `edit_treatment` route: add Form params; update UPDATE
7. Update `treatments.html` add form: add description_it textarea, description_en textarea, notes input
8. Update `treatments.html` edit form: add same 3 fields with existing values
9. Update AI system prompt builder to include treatment descriptions/notes in tenant data
10. Verify: `python -m py_compile management/routes/treatments.py`

## Task 2: Operator Notes in UI

### Files to modify:
- `management/templates/operators.html`
- `management/routes/operators.py`

### Steps:
1. Write test: POST to `/manage/operators/add` with notes field -> verify stored
2. Write test: POST to `/manage/operators/{id}/edit` with notes -> verify updated
3. Write test: GET `/manage/operators/` -> verify notes appear in response HTML
4. Update `list_operators` SELECT query to include `notes`
5. Update `add_operator` route: add Form param for notes; update INSERT
6. Update `edit_operator` route: add Form param for notes; update UPDATE
7. Update `operators.html` add form: add notes input
8. Update `operators.html` edit form: add notes input with existing value
9. Verify: `python -m py_compile management/routes/operators.py`

## Task 3: Salon Rules in Settings

### Files to modify:
- `management/templates/settings.html`
- `management/routes/settings.py`
- `app/services/ai.py` (inject rules into system prompt)

### Steps:
1. Write test: POST to `/manage/settings/save` with rules_deposit, rules_cancellation, rules_punctuality, rules_other -> verify stored in JSONB settings
2. Write test: GET `/manage/settings/` -> verify rules fields are populated from JSONB
3. Write test: AI system prompt includes rules when present
4. Update `show_settings`: SELECT `settings` column, extract rules, pass to template
5. Update `save_settings`: read 4 rules Form params, build rules dict, merge into JSONB settings, UPDATE
6. Update `settings.html`: add "Regole del Salone" section with 4 textareas
7. Update AI system prompt builder to inject rules from settings
8. Verify: `python -m py_compile management/routes/settings.py`

## Task 4: Role-Based Permissions

### Files to modify:
- `management/auth.py`
- `management/routes/settings.py`
- `management/routes/users.py`
- `management/templates/base.html`

### Steps:
1. Write test: staff user GET `/manage/settings/` -> redirected to dashboard (403 or redirect)
2. Write test: staff user GET `/manage/users/` -> redirected to dashboard
3. Write test: owner user GET `/manage/settings/` -> 200 OK
4. Write test: owner user GET `/manage/users/` -> 200 OK
5. Add `role` to JWT payload in `create_token` and `authenticate_user`
6. Update `decode_token` to return role
7. Add role check at top of settings routes: if not owner, redirect
8. Add role check at top of users routes: if not owner, redirect
9. Update `base.html`: conditionally hide Settings/Users nav links for non-owner
10. Verify: `python -m py_compile management/auth.py`
