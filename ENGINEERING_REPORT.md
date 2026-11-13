# Engineering report

MerchantOps senior full-stack assessment. Every number here can be reproduced with the
commands in [Reproducing the results](#reproducing-the-results); raw output is in
[`measurements/`](measurements/).

## Summary

- **Performance:** a catalogue page went from **26 requests and 130 queries to 1 and 5**;
  opening the 50-variant product from **110 queries to 11**. Responses are byte-identical.
- **Inventory adjustments:** operators adjust a variant's stock with a required reason from the
  editor's Inventory tab and see its history. Each adjustment locks the inventory row, checks
  the reserved floor, and writes stock and an append-only audit record in one transaction.
- **Verification:** 96 tests on MySQL, including threads with independent connections, plus
  checks against the running app and a clean-clone reproduction.

## 1. Findings and priorities

I reviewed the views, serializers and Vue screens, then measured the request sequences the
frontend actually issues against the seeded data (5,000 products, 10,142 variants, two
merchants). Query counts include 3 fixed queries per request (session, user, membership).

| # | Finding | Evidence (before) | Decision |
| --- | --- | --- | --- |
| P1 | `CatalogueView.vue` fetched each row's detail, **one request at a time**, before rendering, for four fields the list already returns | 26 requests, 130 queries per page, next page or search | **Fixed** |
| P2 | `ProductListView` ran one category query per row (`category.name`, no join) | 30 queries per page | **Fixed** |
| P3 | `ProductEditorView` ran a price query and an inventory query per variant | 106 queries for 50 variants | **Fixed** |
| P4 | `EditorView.vue` makes a second request only for `image_url` | +1 parallel request | Next steps |
| P5 | Search uses `LIKE '%term%'`, which cannot use an index | ~8 ms at 2,500 products | Next steps |
| P6 | Activity feed sorts without a `(product_id, created_at)` index | table empty in seed data | Next steps |
| P7 | OFFSET pagination and `COUNT(*)` on every page | last page ~10 ms | Next steps |
| P8 | `ProductDetailView` joins a category it never uses | not measurable | Next steps |

I fixed P1-P3: they are the only places where work grows with rows on screen, they sit on
the two main screens, and each fix is a few lines with identical responses. P1 mattered most,
because its 25 requests ran in sequence, each paying a full round trip. The rest are small at
this size or would change behavior (full-text search matches words, not substrings).

## 2. Performance evidence

**Method.** The `measure_endpoints` command replays API requests in-process (Django test
client, signed in as `demo_operator`, seeded database): one warm-up, then N timed runs per case,
reporting requests, exact query count, median and p95 time, and a SHA-256 of each response.
"Workflow" cases replay what a Vue screen requests. I committed it **before any fix**
(`0de517d`) and used the same command, machine and data for every step. Query counts and hashes
repeat exactly; timings vary about 30%, so each file holds two runs. In-process timing leaves
out the network, which dominates P1, so for P1 I also counted requests in headless Chrome.

Environment: Linux x86_64, 12 cores, Docker 27.2.1, MySQL 8.4.13, Django 5.2.19,
`DEBUG=True`, READ COMMITTED (Django's default).

| Case | Before: queries, median ms | After P1-P3 |
| --- | --- | --- |
| Catalogue page load (requests) | 26 requests, 130 q, 95-105 ms | 1 request, 5 q, 5 ms |
| Catalogue in headless Chrome | 26 requests, 321-357 ms to table | 1 request, 113-130 ms |
| `GET products/` page 1 / last page | 30 q, 13-15 / 22 ms | 5 q, 4-6 / 9-12 ms |
| `GET products/<id>/editor/`, 2 variants | 10 q, 5-7 ms | 7 q, 4-6 ms |
| `GET products/<id>/editor/`, 50 variants | 106 q, 40-56 ms | 7 q, 8 ms |
| Open 50-variant product (editor + detail) | 110 q, 48-70 ms | 11 q, 11 ms |

Per step: [00 baseline](measurements/00-baseline.md),
[01 P1](measurements/01-after-p1-catalogue-requests.md),
[02 P2](measurements/02-after-p2-list-select-related.md),
[03 P3](measurements/03-after-p3-editor-prefetch.md).

**Responses unchanged.** Every endpoint hash after each fix equals the baseline. P1 removed
requests rather than changing one, so I also compared list and detail output for the four
fields the old loop overwrote, across all 5,000 products: 0 differences.

**Reproduced from a clean clone.** On a fresh database built by the setup steps, `0de517d` gave
30 / 106 / 130 queries with the baseline hashes, and `main` gave 5 / 7 / 11 with the final ones.

## 3. Implementation

### Performance (`c577f7d`, `b7d21d2`, `773e4ae`)

- **P1:** the catalogue renders the list response directly.
- **P2:** `.select_related("category")` on the list queryset.
- **P3:** variants load with `select_related("inventory")` and
  `Prefetch("price_rules", PriceRule.objects.order_by("id"))`; the response is built from
  memory as before.
- `tests/test_query_counts.py` pins the list at 5 queries and the editor at 7 for any variant
  count. Each test fails on the old code.

### Inventory adjustments (`2ed1dec`, `c380e4f`, `f508c8a`)

**API.** `POST /api/products/<product_id>/variants/<variant_id>/adjustments/?merchant_id=<id>`
with `{"delta": -5, "reason": "Cycle count correction"}`. `GET` on the same URL lists the
variant's adjustments, newest first, 25 per page.

| Status | When |
| --- | --- |
| 201 | Created; body is the audit record |
| 400 | Delta missing, zero, not a whole number or out of INT range; reason missing, blank or over 255 characters |
| 403 | Not signed in, or no CSRF token |
| 404 | Not a member of the merchant, or the variant isn't in that product and merchant |
| 409 | Would go below reserved (body has current `quantity` and `reserved`), or the variant has no inventory row |
| 405 | PUT, PATCH, DELETE: the audit trail is append-only |

**Data model.** New table `InventoryAdjustment` (migration `0004`): variant, actor, delta,
reason, quantity before and after, created at. `PROTECT` foreign keys stop history being
cascade-deleted, and CHECK constraints (`delta <> 0`, `after = before + delta`) reject a wrong
audit row even from code that bypasses the service. I didn't reuse `AuditLog`: its actor is
nullable and its details are free JSON, so the required fields couldn't be constrained.

**The transaction** (`catalog/services.py`):

```python
with transaction.atomic():
    inventory = Inventory.objects.select_for_update().get(variant=variant)
    if inventory.quantity + delta < inventory.reserved:
        raise InsufficientStock(...)
    inventory.quantity += delta
    inventory.save(update_fields=["quantity"])
    InventoryAdjustment.objects.create(...)
```

Why it is correct on InnoDB:
- `SELECT ... FOR UPDATE` locks the one inventory row; a second adjustment of the same variant
  waits until the first commits.
- A locking read returns the latest **committed** row at any isolation level, so the waiting
  adjustment starts from the first one's result: no lost update, and the floor check is never
  stale.
- Stock change and audit insert share one transaction. One row lock per adjustment means no
  deadlocks between adjustments.
- The floor is enforced twice: the application check returns a clear 409, and the existing
  `inventory_reservation_within_stock` constraint is the database backstop.

**Authorization** reuses the existing mechanism (validate `?merchant_id=`, require membership),
then looks the variant up by `pk`, `product_id` and `product__merchant_id`: anything outside the
operator's merchant is a 404, as elsewhere. It runs before body validation, so errors reveal
nothing about other merchants. I put the check in a helper for the new view and left the four
existing views, which repeat it inline, untouched.

**UI** (`components/InventoryPanel.vue`, on the editor's Inventory tab):
- **Adjust** opens an inline form with a live "New quantity" preview that flags zero or
  below-reserved. Success and errors show as status and alert messages; on a 409 the row
  refreshes to the server's current stock.
- **History** lists the variant's adjustments and refreshes after each one.
- The panel keeps its own copy of the rows, so adjusting never discards unsaved General-tab edits.

**Alternatives I rejected:** a conditional `UPDATE ... WHERE quantity + delta >= reserved`
(correct, but reporting before values and refusals needs extra queries, and it departs from
the lock-then-write pattern in `ProductDetailView`); a version column (schema change and
retries for no gain here); "set quantity to X" (concurrent counts overwrite each other);
`ATOMIC_REQUESTS` or SERIALIZABLE (global change for one endpoint).

## 4. Correctness

| Guarantee | How it holds | Evidence (`backend/catalog/tests/`) |
| --- | --- | --- |
| 1. Nonzero whole number, required reason | Serializer + `delta <> 0` CHECK | `test_inventory_adjustment_api.py`, validation tests |
| 2. Never below reserved | Checked under the lock; existing CHECK as backstop | Boundary tests; concurrent decrements |
| 3. Concurrent changes not lost | `select_for_update` | `test_inventory_adjustment_concurrency.py` |
| 4. Accurate audit record | Same transaction, locked values, two CHECKs | Field-by-field and constraint tests |
| 5. Both or neither | One `transaction.atomic()` | Rollback tests: mocked failure, and MySQL rejecting the row |
| 6. Merchant isolation | Existing membership check + variant scoped by product and merchant | `test_inventory_adjustment_auth.py` (14 cases) |
| 7. Usable from the existing UI | Inventory tab | Browser checks below |
| 8. Existing behavior intact | No existing migration, constraint or response changed | 46 original tests pass; hashes unchanged |

**Concurrency tests use real, independent MySQL connections:** `TransactionTestCase` (real
commits) with one thread, and so one MySQL session, per worker. Eight simultaneous +1s give
exactly +8; five −3s against 6 available units let exactly two through. A deterministic test
holds the row lock and asserts the worker waits, then reads the committed value. The module
passed 5 runs in a row.

**I checked the tests against deliberately broken code**, restoring it each time:

| Safeguard removed | Result |
| --- | --- |
| `select_for_update()` | 3 concurrency tests fail: 7 of 8 increments lost |
| Audit insert outside the transaction | Both rollback tests fail |
| Application floor check | Floor test fails with the DB's `IntegrityError` (data still safe) |
| Merchant or product filter on the variant lookup | 3 and 2 auth tests fail |

**Against the running app:** in headless Chrome I adjusted up and down and tried a zero change,
a below-reserved change, a blank reason and a stale form (the 409 refreshed the row). With
`curl`, missing session or CSRF token gave 403, and other merchants' or mismatched variants gave
404 with nothing changed.

## 5. Trade-offs and next steps

**Assumptions**
- `"5"` and `5.0` are accepted as the whole number 5 (DRF `IntegerField`); fractions and text
  are rejected.
- Reason is free text up to 255 characters.
- A variant without an inventory row gets 409 rather than a new row; every seeded variant has one.
- 409, not 400, for the floor: the request is valid but conflicts with current stock.

**Limitations**
- Adjustments appear in the Inventory tab's History, not on the product Activity page.
- No idempotency key: Save is disabled while in flight, but a retry after a lost response
  could apply twice.
- A stuck lock holder makes waiters fail after InnoDB's 50 s lock timeout (500).
- `PROTECT` blocks deleting a variant or user with adjustments, by design.
- No frontend unit tests (no runner in the repo); I verified the UI in a browser.
- Timings come from `runserver` with `DEBUG=True`; query and request counts are portable.

**Next steps, in order:** show adjustments on the Activity page; add an idempotency key; P4;
indexes on `(variant_id, created_at)` and `(product_id, created_at)` as history grows; move the
four views' merchant check into the shared helper; then P5 and P7 (keyset pagination, and a
search strategy agreed with product owners).

## Reproducing the results

Set up with `./setup.sh` as in `README.md`, then:

```bash
# Tests (MySQL), including concurrency
docker compose exec backend python manage.py test catalog --keepdb
docker compose exec backend python manage.py test \
  catalog.tests.test_inventory_adjustment_concurrency --keepdb -v 2

# Current numbers, then baseline numbers from the commit before any fix
docker compose exec backend python manage.py measure_endpoints --runs 20
git checkout 0de517d
docker compose exec backend python manage.py measure_endpoints --runs 20
git checkout main

# Formatters (once per container build)
docker compose exec backend python -m pip install -r requirements-dev.txt
```

Compare query counts and hashes with `measurements/`. The seed is deterministic, so hashes
match on any machine; timings won't. To see P1 in a browser, filter DevTools → Network by
`/api/` (or run `docker compose logs -f backend | grep /api/`) while paging: 1 request per page
now, 26 at `0de517d`. Avoid adjusting ASSESS-000001 and ASSESS-000005: the measurement uses
them, and adjustments change their hashes.
