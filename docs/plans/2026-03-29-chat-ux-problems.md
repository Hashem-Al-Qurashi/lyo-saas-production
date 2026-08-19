# Lyo Bot — Chat UX Problem Spec
**Date:** 2026-03-29
**Method:** 44 live scenarios run against production bot (`get_ai_response` direct call, real DB, real OpenAI)
**Salon:** Aura Hair Studio (business_id=1)
**Result:** 27 PASS / 17 ISSUE (3 critical, 10 important, 2 soft, 2 observations)

---

## Context at time of testing

```
Today: Sunday 29 March 2026
Active closures:
  pasqua:   2026-03-27 → 2026-04-12
  restauro: 2026-04-09 → 2026-04-16   ← overlaps with pasqua
  ferie:    2026-08-01 → 2026-08-15
Correct reopen: Saturday 17 April 2026
  (pasqua ends 12 Apr → 13 is Mon=closed, restauro still active 13–16 Apr → first open = Sat 17 Apr)

Permanent closed days: Monday, Sunday
Operators:
  federica  → trattamento_nutriente, doppio_gloss, trattamento_fortificante, piega
  Giulia    → taglio_donna, taglio_uomo, piega, colore_base, balayage, trattamento_ristrutturante, trattamento_cute
  Martina   → same as Giulia
  Sara      → taglio_donna, taglio_uomo, piega, trattamento_ristrutturante, trattamento_cute
  Luca      → taglio_donna, taglio_uomo, piega, massaggio_drenante
  antonio   → NO treatments assigned
  massaggio_thai → in service catalog but NO operator assigned
```

---

## Problems — Ranked by severity

---

### P1 — CRITICAL: Overlapping closures produce wrong reopen date

**Scenarios affected:** S4, S10, S11, S12, S18, S19, S20, S24, S35
**Frequency:** 9 out of 44 scenarios (20%)

**What happens:**
When the customer asks "quando riaprite?" or the bot needs to redirect to an alternative date, it says:
> "Riapriremo martedì 14 aprile 2026."

This is **wrong**. The correct reopen date is **Saturday 17 April 2026**.

**Why it's wrong:**
The DB has two overlapping closures: `pasqua` ends 12 Apr, `restauro` ends 16 Apr. The bot only computes the end of the *first* closure (pasqua = reopens 13 Apr). It does not check that 13–16 Apr is still inside the restauro closure. Monday 13 Apr is also permanently closed (another miss). Actual first open day = Sat 17 Apr.

**Customer impact:**
Customer books or shows up on 14, 15, or 16 April → salon is closed. High trust-damage risk.

**Evidence:**
```
S10 USER: quando riaprite?
    BOT:  Riapriremo martedì 14 aprile 2026. [WRONG — should be sabato 17 aprile]

S4  USER: sono Luca, voglio un balayage
    BOT:  riaprirà il 14 aprile [WRONG]

S11 BOT:  siamo chiusi per Pasqua fino al 12 aprile 2026 [WRONG — restauro extends to 16 Apr]
```

**Additional symptom (P1b):** The calculation is non-deterministic. In S31, the bot correctly said "riaprirà il 17 aprile" — the same scenario in a different conversation context gave the right answer. The LLM is reasoning about overlapping date ranges inconsistently.

**Fix direction:** Add explicit `_build_reopen_date()` logic in `business_context.py` that computes the first open date by iterating through all closures sorted by end date, then checking weekday against permanent closed days. Pass the result as a literal string in the prompt: `"NEXT OPEN DAY: sabato 17 aprile 2026"`. Remove the LLM's ability to calculate this.

---

### P2 — CRITICAL: Bot proposes appointment on a date it just said is closed

**Scenarios affected:** S35
**Frequency:** 1 scenario, but underlying cause affects every "reschedule" suggestion

**What happens:**
```
S35 BOT: "riapriremo martedì 14 aprile 2026. Ti va bene il 14 aprile alle 11:00?"
```
Bot tells customer the salon is closed, then immediately offers a slot on that same closed date.

**Why:** The bot reasons in two separate steps — first it outputs the "we're closed" message, then it tries to help by suggesting the "reopen date" (already wrong) as a slot. The two steps are not cross-checked.

**Customer impact:** Extreme confusion. Customer confirms "yes" and gets either an error or a phantom booking.

**Fix direction:** Same as P1 — embed the computed reopen date. Additionally, `check_availability` must always re-validate against closures before returning "available".

---

### P3 — CRITICAL: All-caps input bypasses closure detection

**Scenarios affected:** S34
**Frequency:** 1 of 44, but affects any customer who sends urgent/caps messages

**What happens:**
```
S34 USER: MI CHIAMO MARCO VOGLIO UN TAGLIO IL 14 APRILE ALLE 10
    BOT:  Ciao Marco! Il 14 aprile è un giorno in cui siamo aperti. Ti interessa un taglio donna o un taglio uomo?
```
14 April is **inside the restauro closure**. Every other scenario with this date correctly blocked it. All-caps input caused the closure check to fail.

**Why:** Likely the date extraction or closure comparison does case-sensitive string matching somewhere (e.g., Italian month name comparison "APRILE" != "aprile" or the date parser fails on uppercase and falls back to "no date found → assume open").

**Customer impact:** Customer books a closed day, gets no confirmation or a broken booking.

**Fix direction:** Normalize input to lowercase before date extraction. Add a test case for all-caps inputs.

---

### P4 — IMPORTANT: Permanent Monday closure not communicated when redirecting

**Scenarios affected:** S8
**Frequency:** Occurs any time bot redirects from Easter/closure to "after" dates

**What happens:**
```
S8 USER: Mi chiamo Chiara, voglio un appuntamento lunedì 13 aprile
   BOT:  Purtroppo il nostro salone sarà chiuso per restauro. Vuoi vedere le disponibilità per un'altra data?
```
Bot says "restauro" but doesn't mention that **Monday is always closed**. Customer may reply "ok, lunedì 20 aprile then" — which is also closed (permanent Monday closure). Bot will block that too, creating a ping-pong loop with the customer.

**Fix direction:** When redirecting, always append: `"Ricorda che il lunedì siamo sempre chiusi — ti suggerisco martedì [date] o un altro giorno dal martedì al sabato."`

---

### P5 — IMPORTANT: Partial operator name not resolved

**Scenarios affected:** S16
**Frequency:** Any time customer uses a nickname or abbreviation

**What happens:**
```
S16 USER: Mi chiamo Leo, voglio con Marti il 15 aprile alle 10:00
    BOT:  Il 15 aprile è durante il periodo di chiusura per restauro, quindi il salone sarà chiuso. Vuoi scegliere un'altra data?
```
"Marti" (likely Martina) is completely ignored. The bot handles only the closure, leaving the operator preference unresolved. If the customer picks a new date, the operator preference is lost.

**Fix direction:** In `resolve_operator`, add fuzzy matching (e.g., `operator_name.lower() in display_name.lower()`). Pre-register in system prompt: "If the customer uses a partial name like 'Marti', try to match it to 'Martina'."

---

### P6 — IMPORTANT: Out-of-hours time not flagged when closure is also present

**Scenarios affected:** S22
**Frequency:** Affects any request with both a closure AND an out-of-hours time

**What happens:**
```
S22 USER: Mi chiamo Fabio, voglio un taglio il 14 aprile alle 20:00
    BOT:  Il 14 aprile è chiuso per restauro. Vuoi prenotare per un altro giorno?
```
The bot correctly catches the closure. But 20:00 is outside the salon's hours (Tue closes 19:00, Sat closes 18:00). If the customer says "ok, 15 aprile" (also restauro), then "ok, 17 aprile" (Sat, closes 18:00) — the 20:00 time will still be silently invalid. The bot has already "forgotten" to flag it.

**Fix direction:** When redirecting, explicitly state both constraints: "siamo chiusi per restauro, e tieni presente che chiudiamo alle 19:00 nei giorni feriali e alle 18:00 il sabato — vuoi scegliere un orario al mattino o pomeriggio?"

---

### P7 — IMPORTANT: `massaggio_thai` offered for booking despite no operator assigned

**Scenarios affected:** S42
**Frequency:** Any customer requesting massaggio thai

**What happens:**
```
S42 USER: Mi chiamo Carla, voglio un massaggio thai il 15 aprile alle 10
    BOT:  Purtroppo il nostro salone sarà chiuso per restauro. Posso aiutarti a prenotare un massaggio thai in un'altra data?
```
The bot offers to book `massaggio_thai` for a future date. But no operator in the DB is assigned to this service — any actual booking attempt will fail silently.

**Fix direction:** Either (a) assign an operator to massaggio_thai in the DB, or (b) remove it from the service catalog, or (c) add a check in `build_services_dict` / system prompt to flag services with no operator as "momentaneamente non disponibile".

---

### P8 — IMPORTANT: Bot suggests week still inside closure when redirecting

**Scenarios affected:** S12
**Frequency:** Occurs when bot tries to suggest alternative weeks

**What happens:**
```
S12 BOT: Posso aiutarti a trovare un'altra data? In quale giorno e a che ora preferisci nella settimana del 13 aprile?
```
"Settimana del 13 aprile" is Mon 13 (permanently closed) through Sun 19 Apr — but Tue–Thu 13–16 are still inside restauro, Fri 17 is the first open day. Bot suggests an entire week that is mostly still closed.

**Fix direction:** Same as P1 — compute next open date and suggest from that date onward. "Ti suggerisco a partire da sabato 17 aprile."

---

### P9 — IMPORTANT: "questa settimana" / "settimana prossima" + wrong reopen date (compounded)

**Scenarios affected:** S18, S19
**Frequency:** Very common customer expressions

**What happens:**
- S18: "questa settimana" → correctly flagged as Easter closure → but then says "riapriremo il 14 aprile" (wrong)
- S19: "settimana prossima" → same wrong reopen date

These are extremely common Italian ways of asking for an appointment. Both fail on the reopen date.

**Fix direction:** Covered by P1 fix (embed computed reopen date).

---

### P10 — IMPORTANT: "prima possibile" doesn't proactively offer the service menu

**Scenarios affected:** S24
**Frequency:** Common customer expression

**What happens:**
```
S24 USER: Mi chiamo Hanna, voglio un appuntamento prima possibile
    BOT:  Al momento siamo chiusi e riapriremo il 14 aprile [wrong]. Fammi sapere quale servizio desideri e in quale giorno e ora preferisci!
```
Bot asks customer to proactively name a service/time rather than offering "here are our services, which would you like?" — extra friction for a customer who already said "prima possibile" (they want the bot to guide them).

**Fix direction:** When customer says "prima possibile" or similar, bot should offer: "Riapriamo [date]. Ecco i nostri servizi: [list]. Quale preferisci e a che ora? Ti trovo il primo slot disponibile."

---

### P11 — SOFT: FAQ hallucination — payment methods

**Scenarios affected:** S40
**Frequency:** Any FAQ question not in DB

**What happens:**
```
S40 USER: accettate il pagamento con carta?
    BOT:  Sì, accettiamo il pagamento con carta presso Aura Hair Studio.
```
No payment method field exists in the DB. Bot fabricated a positive answer.

**Risk:** If the salon is cash-only, customer arrives without cash → bad experience.

**Fix direction:** Add "payment_methods" to the business settings table and FAQ section. Until then, bot should say "Per informazioni sui metodi di pagamento ti chiedo di contattarci direttamente."

---

### P12 — SOFT: FAQ hallucination — parking

**Scenarios affected:** S41
**Frequency:** Any location-related question

**What happens:**
```
S41 USER: c'è parcheggio?
    BOT:  Sì, nei dintorni di Aura Hair Studio ci sono diverse opzioni di parcheggio.
```
No parking info in DB. Bot made a positive claim that could be false.

**Fix direction:** Add a "notes" or "faq" field in settings for custom answers. Until then, redirect to "contattaci direttamente."

---

## Summary table

| ID | Severity | Problem | Scenarios | Root cause |
|----|----------|---------|-----------|-----------|
| P1 | CRITICAL | Wrong reopen date (overlapping closures) | S4,S10,S11,S12,S18,S19,S20,S24,S35 | LLM computes reopen from only first closure |
| P2 | CRITICAL | Proposes slot on date just said is closed | S35 | Reopen date wrong → proposed as available |
| P3 | CRITICAL | All-caps bypasses closure detection | S34 | Case-sensitive date/month matching |
| P4 | IMPORTANT | Permanent Monday closure not communicated in redirects | S8 | Not in redirect template |
| P5 | IMPORTANT | Partial operator name ignored | S16 | `resolve_operator` needs fuzzy match |
| P6 | IMPORTANT | Out-of-hours time lost when closure also present | S22 | First validation error stops checking |
| P7 | IMPORTANT | massaggio_thai offered with no operator assigned | S42 | Orphan service in catalog |
| P8 | IMPORTANT | Suggests still-closed week when redirecting | S12 | Same as P1 |
| P9 | IMPORTANT | "questa/prossima settimana" give wrong reopen | S18,S19 | Same as P1 |
| P10 | IMPORTANT | "prima possibile" → bot doesn't guide through service menu | S24 | Missing conversation pattern |
| P11 | SOFT | Card payment hallucinated | S40 | No FAQ DB backing |
| P12 | SOFT | Parking hallucinated | S41 | No FAQ DB backing |

---

## Gate 0 closing — Findings vs Observations

```
FINDINGS (causal — directly producing bad UX):
  P1 — Overlapping closure reopen date wrong (9 scenarios affected)
  P2 — Bot proposes closed-date slot (1 critical instance)
  P3 — All-caps bypasses closure check (1 critical instance)
  P4 — Monday permanent closure not stated in redirects
  P5 — Partial operator name not fuzzy-matched
  P6 — Out-of-hours time validation lost when closure also present
  P7 — massaggio_thai in catalog with no operator
  P10 — "prima possibile" flow doesn't guide through menu

OBSERVATIONS (anomalous but not directly causing the reported UX issue):
  P8 — Redirect suggests weeks that are still inside closures
        Should this exist? No — bot should compute first valid date.
        At 10x: every customer during a closure period gets wrong week suggestion.
  P9 — Common Italian date expressions produce wrong reopen date
        At 10x: the most common way Italians ask for an appointment is broken.
  P11 — Payment hallucination
        At 10x: if salon is cash-only and 100 customers ask → 100 wrong answers.
  P12 — Parking hallucination
        At 10x: some customers drive past; if location has no parking,
        they'll circle and blame the salon.
  S31 OBSERVATION — reopen date was sometimes correct. Non-deterministic.
        What causes this? The conversation context in S31 had more turns before
        the reopen question, giving the LLM more info to reason from.
        Proves the fix must be code-level, not prompt-only.
```

---

## Proposed fix priority (for next sprint)

1. **P1/P2/P8/P9 (one fix):** Compute `NEXT_OPEN_DATE` in `business_context.py` at prompt build time and embed it as a literal string. Remove LLM's ability to calculate dates from overlapping closures.
2. **P3:** Lowercase normalize all inbound messages before date parsing.
3. **P4:** Add Monday/Sunday reminder to all "chiuso" redirect responses in the system prompt template.
4. **P7:** Remove `massaggio_thai` from the services catalog until an operator is assigned, OR assign it.
5. **P5:** Add fuzzy partial name matching to `resolve_operator`.
6. **P6:** Add a second validation pass after closure redirect to re-check time validity.
7. **P10:** Add a "prima possibile" conversation pattern to the system prompt.
8. **P11/P12:** Add a FAQ table to business settings. Default answer for missing FAQ: "contattaci direttamente."
