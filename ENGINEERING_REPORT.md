# Engineering report

MerchantOps senior full-stack assessment. This report covers what I investigated, what I
changed and why, how it was measured and verified, and what I would do next. Every number
below can be reproduced with the commands in [Reproducing the results](#reproducing-the-results).

## Summary

- **Performance:** fixed the three problems on the two screens operators use most. One
  catalogue page went from **26 HTTP requests and 130 queries to 1 request and 5 queries**.
  Opening the 50-variant product went from **110 queries to 11**. Every API response is
  byte-identical to before (checked by SHA-256).
- **Inventory adjustments:** operators can raise or lower a variant's stock, with a required
  reason, from the editor's Inventory tab, and see each variant's adjustment history. Every
  adjustment locks the inventory row, checks the reserved floor, updates stock and writes an
  append-only audit record in one transaction.
- **Verification:** 96 backend tests on MySQL, including multi-threaded tests with independent
  connections. Each guarantee was also checked against the running app (browser and `curl`),
  and a clean clone was set up from scratch and reproduced both the baseline and the final
  numbers.

## 1. Findings and priorities

After a review of the views, serializers and Vue screens, the request sequences the
frontend actually issues were measured against the seeded dataset (5,000 products,
10,142 variants, two merchants). Query counts are per request and include 3 fixed queries (session, user,
merchant membership).

| # | Finding | Where | Evidence (before) | Decision |
| --- | --- | --- | --- | --- |
| P1 | Catalogue re-fetched every row's detail **one request at a time** before rendering, for four fields the list already returns | `frontend/src/views/CatalogueView.vue` | 26 requests, 130 queries per page load, next page or search | **Fixed** |
| P2 | List endpoint ran one category query per row (`category.name` in the serializer, no join) | `ProductListView.get_queryset` | 30 queries per page | **Fixed** |
| P3 | Editor endpoint ran a price query and an inventory query per variant | `ProductEditorView.get` | 106 queries for the 50-variant product | **Fixed** |
| P4 | Editor screen makes a second request only to get `image_url` | `EditorView.vue` | +1 parallel request (4 queries) | Future work |
| P5 | Substring search (`LIKE '%term%'`) cannot use an index; `COUNT(*)` repeats the filter | `ProductListView` | ~8 ms at 2,500 products per merchant | Future work |
| P6 | Activity feed sorts by `created_at` without a composite index | `ProductActivityView` | table empty in seed data | Future work |
| P7 | OFFSET pagination and `COUNT(*)` on every page | DRF `PageNumberPagination` | last page ~10 ms | Future work |
| P8 | Detail view joins a category it never serializes | `ProductDetailView` | not measurable | Future work |

**Why these three.** They are the only places where work grows with the number of rows on
screen, they sit on the catalogue and the editor (the screens every workflow passes through),
and each fix is a few lines that keep responses identical. P1 mattered most: its 25 requests
were sequential, so in a browser each paid a full network round trip before the table could
render. The rest are small at this data size, or would change behaviour (a FULLTEXT index
matches words, not substrings), so they are listed under next steps instead.

## 2. Performance evidence

### Method

`backend/catalog/management/commands/measure_endpoints.py` replays read-only API requests
in-process with Django's test client, signed in as `demo_operator` against the seeded
database. For each case it does one warm-up and N timed runs, and reports requests, exact
query count, median and p95 time, response size and a SHA-256 of the body. "Workflow" cases
replay the exact request sequence a Vue screen issues.

- It was committed **before any optimization** (`0de517d`), so the baseline can be rerun
  from that commit.
- The same command, machine and dataset were used for every step. Raw output of every run is
  in [`measurements/`](measurements/).
- Query counts and hashes are exact and repeat on every run. Timings vary by about 30%
  between runs on the same machine, so each file holds two runs.
- In-process timing excludes network and rendering. For P1, where those dominate, I also
  counted requests in headless Chrome against the running dev stack.

Environment: Linux x86_64, 12 cores, Docker 27.2.1, MySQL 8.4.13, Django 5.2.19,
`DEBUG=True`, Django's default READ COMMITTED isolation.

### Results

| Case | Baseline | After P1-P3 | Change |
| --- | --- | --- | --- |
| Catalogue page load (requests / queries) | 26 / 130 | 1 / 5 | −96% queries |
| Catalogue page load, median ms (in-process) | 104.6 / 94.5 | 5.0 / 5.0 | ~20× |
| Catalogue in headless Chrome: API requests, time to table | 26, 321-357 ms | 1, 113-130 ms | |
| `GET products/` page 1 (queries, median ms) | 30, 12.7 / 15.4 | 5, 3.9 / 6.2 | |
| `GET products/` last page (queries, median ms) | 30, 22.0 / 21.9 | 5, 8.9 / 11.5 | |
| `GET products/<id>/editor/`, 2 variants | 10, 4.6 / 7.3 | 7, 5.7 / 4.2 | |
| `GET products/<id>/editor/`, 50 variants | 106, 55.5 / 39.5 | 7, 8.4 / 8.1 | ~6× |
| Open 50-variant product (editor + detail) | 110, 69.8 / 47.7 | 11, 11.4 / 11.5 | ~5× |

Pairs of timings are run 1 / run 2. Per-step files:
[00 baseline](measurements/00-baseline.md),
[01 after P1](measurements/01-after-p1-catalogue-requests.md),
[02 after P2](measurements/02-after-p2-list-select-related.md),
[03 after P3](measurements/03-after-p3-editor-prefetch.md).

**Responses unchanged.** Every endpoint hash in 01-03 equals the baseline hash. P1 removed
requests rather than changing one, so I also compared list and detail output for the four
fields the old loop overwrote, across all 5,000 products: 0 differences.

**Reproduced from a clean clone.** On a fresh database built by the setup steps, checking out
`0de517d` gave 30 / 106 / 130 queries with the baseline hashes, and `main` gave 5 / 7 / 11
with the final hashes.

## 3. Implementation

### Performance (commits `c577f7d`, `b7d21d2`, `773e4ae`)

- **P1:** `CatalogueView.vue` renders the list response directly.
- **P2:** `.select_related("category")` on the list queryset.
- **P3:** the editor loads variants with `select_related("inventory")` and
  `Prefetch("price_rules", queryset=PriceRule.objects.order_by("id"))`, then builds the same
  dictionary from memory. A variant with no inventory row still returns `null`.
- **Regression guards:** `catalog/tests/test_query_counts.py` asserts 5 queries for the list
  (full page and search) and 7 for the editor regardless of variant count. I confirmed each
  test fails with the old code (30, 16, 8 and 18 queries).

### Inventory adjustments (commits `2ed1dec`, `c380e4f`, `f508c8a`)

**API.** `POST /api/products/<product_id>/variants/<variant_id>/adjustments/?merchant_id=<id>`
with `{"delta": -5, "reason": "Cycle count correction"}`.

| Status | When |
| --- | --- |
| 201 | Created; body is the audit record (`id, variant, delta, reason, quantity_before, quantity_after, actor, created_at`) |
| 400 | `delta` missing, zero, not a whole number or outside the INT range; `reason` missing, blank or over 255 characters; bad `merchant_id` |
| 403 | Not signed in, or CSRF token missing |
| 404 | Not a member of the merchant, or the variant is not in that product and merchant |
| 409 | Would take stock below reserved (body includes current `quantity` and `reserved`), or the variant has no inventory row |
| 405 | PUT, PATCH or DELETE: audit records are append-only |

`GET` on the same URL lists the variant's adjustments, newest first, 25 per page.

**Data model.** A new `InventoryAdjustment` table (migration `0004`): variant, actor, delta,
reason, quantity before, quantity after, created at.
- Both foreign keys are `PROTECT`, so history cannot be cascade-deleted.
- Two CHECK constraints: `delta <> 0`, and `quantity_after = quantity_before + delta`. A
  wrong audit row cannot be stored even by code that bypasses the service.
- I chose a dedicated table over the existing `AuditLog` because `AuditLog` is product-level,
  has a nullable actor and stores free-form JSON. None of the guaranteed fields could be
  constrained or queried there.

**The transaction** (`catalog/services.py`, `adjust_inventory`):

```python
with transaction.atomic():
    inventory = Inventory.objects.select_for_update().get(variant=variant)
    if inventory.quantity + delta < inventory.reserved:
        raise InsufficientStock(...)
    inventory.quantity += delta
    inventory.save(update_fields=["quantity"])
    InventoryAdjustment.objects.create(...)
```

Why this is correct on MySQL InnoDB:
- `SELECT ... FOR UPDATE` takes an exclusive lock on the one inventory row through its unique
  `variant_id` index. A second adjustment of the same variant waits until the first commits.
- A locking read always returns the latest **committed** row, at any isolation level. The
  waiting adjustment therefore starts from the first one's result, so no update is lost and
  the floor check can never be stale.
- The stock change and the audit insert share one transaction: both commit or neither does.
- Each adjustment takes exactly one row lock, so adjustments cannot deadlock each other.
- The reserved floor is enforced twice. The application check gives a clear 409, and the
  existing `inventory_reservation_within_stock` CHECK constraint is the database backstop.

**Authorization** reuses the existing mechanism: validate `?merchant_id=`, require membership
(`Merchant.objects.filter(members=request.user)`), then look the variant up by
`pk, product_id, product__merchant_id`. Anything outside the operator's merchant is a 404, as
in the existing endpoints. Authorization runs before body validation, so errors never reveal
details of another merchant's variant. The existing checks were duplicated inline in four
views; I put the same code in one helper used by the new view and left the four views
untouched (no drive-by refactor).

**UI.** The editor's read-only Inventory tab became `components/InventoryPanel.vue`:
- **Adjust** opens an inline form: signed whole-number change, required reason, and a live
  "New quantity" preview that flags zero or below-reserved and disables Save. That is a hint
  only; the server enforces the rules.
- Success shows `stock 82 → 89` as a status message. Errors show the API message as an alert.
- On a 409 the row refreshes to the server's current stock, so an operator working from a
  stale page sees the real numbers.
- **History** expands that variant's audit records with operator, change, before → after and
  reason, paginated with the existing component.
- The panel keeps its own copy of the rows. Replacing the editor's product record would have
  discarded unsaved edits on the General tab.

**Rejected alternatives:**
- *A single conditional `UPDATE ... WHERE quantity + delta >= reserved`*: also correct, but it
  needs extra queries to report the before value and the reason for a refusal, and it doesn't
  match the existing lock-then-write pattern in `ProductDetailView`.
- *Optimistic version column*: a schema change and a retry loop for no benefit at this level
  of contention.
- *"Set quantity to X"*: two operators' counts would overwrite each other.
- *`ATOMIC_REQUESTS` or SERIALIZABLE isolation*: a global behaviour change for one endpoint.

## 4. Correctness

| Guarantee | How it holds | Tests (`backend/catalog/tests/`) |
| --- | --- | --- |
| 1. Nonzero whole-number delta, required reason | Serializer validation + `delta <> 0` CHECK | `test_inventory_adjustment_api.py` `InventoryAdjustmentValidationTests`: 0, `"abc"`, 1.5, `"1.5"`, null, true, `""`, 2³¹, missing; reason `""`, `"   "`, null, 256 chars, missing |
| 2. Never below reserved | Checked under the row lock; existing CHECK as backstop | Boundary (down to exactly reserved: 201; one more: 409); `test_concurrent_decrements_respect_reserved_floor` |
| 3. Concurrent adjustments not lost | `select_for_update` on the inventory row | `test_inventory_adjustment_concurrency.py`: 8 simultaneous +1 → exactly +8 with a consistent before/after chain; a blocked adjustment reads the committed value |
| 4. Accurate audit record | Written in the same transaction from the locked values; two CHECK constraints | Field-by-field assertions; DB rejects zero delta and inconsistent quantities |
| 5. Both or neither | One `transaction.atomic()` | Audit insert failing (mocked) and rejected by MySQL itself (no mock): stock unchanged |
| 6. Merchant isolation | Existing membership check + variant scoped by product and merchant | `test_inventory_adjustment_auth.py`: 14 cases incl. member of both merchants, mismatched product/variant, CSRF, methods |
| 7. Usable from the existing UI | Inventory tab form and history | Headless Chrome against the dev stack (below) |
| 8. Existing behaviour intact | No existing migration, constraint or view contract changed | All 46 original tests pass; all endpoint hashes unchanged |

**The concurrency tests use real, independent MySQL connections.** They are
`TransactionTestCase` tests, which commit for real. Each worker thread gets its own Django
connection, and therefore its own MySQL session. One test is fully deterministic: the main
thread holds the row lock while a worker tries to adjust, and the test asserts the worker is
still blocked, then that it read the committed value. The module was run 5 times in a row,
and the full suite twice, without a failure.

**The tests were checked against deliberately broken code.** I reran them with each safeguard
removed, then restored it:

| Safeguard removed | Result |
| --- | --- |
| `select_for_update()` | All 3 concurrency tests fail: 7 of 8 increments lost, all 5 decrements passed the floor |
| Audit insert moved outside the transaction | Both rollback tests fail (stock committed without an audit row) |
| Application floor check | Floor test fails with the database's `IntegrityError` (the data stays safe; the 409 comes from the application check) |
| Merchant or product filter in the variant lookup | 3 and 2 authorization tests fail |

**Checked against the running app:**
- **Browser** (headless Chrome against the dev stack): adjust up and down, a zero change, a
  change below reserved, a blank reason (server 400 shown), and a stale form. In the stale-form
  case, a second change committed after the form loaded: the UI showed 82, stock was 62, the
  server returned 409, and the row refreshed to 62. History refreshed after each adjustment.
- **`curl`:** no session (403), no CSRF token (403), non-member merchant, foreign variant and
  mismatched product (404). The database was unchanged afterwards.
- Every adjustment made during these checks is in the audit table with its reason.

## 5. Trade-offs, limitations and next steps

**Assumptions**
- DRF's `IntegerField` accepts `"5"` and `5.0` as the whole number 5. Fractions and text are
  rejected.
- Reason is free text up to 255 characters. Reason codes would help reporting but were not
  asked for.
- A variant with no inventory row returns 409 rather than creating one. All seeded variants
  have a row, and creating one would need a different locking strategy.
- 409 rather than 400 for the floor: the request is valid but conflicts with current stock,
  and the right reaction is to refresh, not to fix the input.

**Limitations**
- Adjustments show in the Inventory tab's History, **not** on the product Activity page,
  because they live in their own table.
- There is no idempotency key. The button is disabled while a request is in flight, but a
  client retry after a lost response could apply twice.
- A transaction that held the row lock indefinitely would make waiters fail after InnoDB's
  50 s lock timeout with a 500.
- `PROTECT` means a variant or user with adjustments cannot be deleted. Nothing in the app
  deletes them today, and keeping audit history is the intent.
- No frontend unit tests: the repository has no frontend test runner, and adding one was out
  of budget. The UI was verified in a real browser and by `npm run build`.
- Timings come from `runserver` with `DEBUG=True` on one machine. Query and request counts are
  the portable evidence.

**Next steps, in priority order**
1. Show adjustments on the Activity page (merge the two feeds), or link to History from there.
2. Add an idempotency key to the adjustment POST.
3. P4: include `image_url` in the editor payload and drop the extra request.
4. Add a `(variant_id, created_at)` index to adjustments and a `(product_id, created_at)` index
   to `AuditLog` once history grows (P6).
5. Move the merchant check duplicated in the four existing views into the new helper (a pure
   refactor, covered by the existing tests).
6. P5, P7: keyset pagination, and a search strategy chosen with product owners (prefix
   matching can use an index; FULLTEXT changes matching semantics).

## Reproducing the results

Setup is as in `README.md` (`./setup.sh`). Then:

```bash
# One-off per backend container build: formatter tools (not needed for tests).
docker compose exec backend python -m pip install -r requirements-dev.txt

# Full test suite (MySQL), including the concurrency tests.
docker compose exec backend python manage.py test catalog --keepdb

# Only the concurrency and atomicity tests, verbose.
docker compose exec backend python manage.py test \
  catalog.tests.test_inventory_adjustment_concurrency --keepdb -v 2

# Performance numbers for the current checkout.
docker compose exec backend python manage.py measure_endpoints --runs 20

# Baseline numbers: run the same command on the commit before any fix.
git checkout 0de517d
docker compose exec backend python manage.py measure_endpoints --runs 20
git checkout main
```

Compare query counts and body hashes with the files in `measurements/`. The seed is
deterministic, so the hashes match on any machine. Timings will differ by machine.

To see P1 in a browser, open the catalogue with DevTools → Network filtered to `/api/`, or
watch `docker compose logs -f backend | grep /api/` while paging. One page load is one
request; at `0de517d` it is 26.

Note: the measurement command and browser checks use the existing seeded products. Making
adjustments to ASSESS-000005 (2 variants) or ASSESS-000001 (50 variants) changes their editor
response and therefore their hashes; adjust other products when experimenting.
