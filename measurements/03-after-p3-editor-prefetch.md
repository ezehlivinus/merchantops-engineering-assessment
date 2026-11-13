# 03: After P3 (product editor loads variant data in constant queries)

- Change: `ProductEditorView.get` loads variants with `select_related("inventory")` and
  `Prefetch("price_rules", queryset=PriceRule.objects.order_by("id"))`, then builds the same
  response from memory. Previously the loop ran one price query and one inventory query
  per variant.
- Same command, machine and dataset as `00-baseline.md`:
  `docker compose exec backend python manage.py measure_endpoints --runs 20`
- Regression guard: `catalog.tests.test_query_counts.ProductEditorQueryCountTests`
  (7 queries for 1 and 6 variants; fails with 8 and 18 without the fix), plus checks that
  prices and inventory stay attached to the right variant and that a variant without an
  inventory row still reports `null`.

## Result

| Case | Queries before (02) | After | Median ms before (02, runs 1 / 2) | After (runs 1 / 2) |
| --- | ---: | ---: | --- | --- |
| GET editor, 2 variants | 10 | 7 | 7.4 / 7.4 | 5.7 / 4.2 |
| GET editor, 50 variants | 106 | 7 | 58.1 / 53.5 | 8.4 / 8.1 |
| Workflow: open product, 50 variants | 110 | 11 | 54.1 / 59.6 | 11.4 / 11.5 |

The 7 queries: session, user, merchant membership, product with category, variants with
inventory, prices for all variants, availability.

## Unchanged data
- Editor body hashes for the 2-variant and 50-variant product match `00-baseline.md`
  byte for byte (`d1a7b6179da3`, `96846bf6f450`).
- Browser (headless Chrome, ASSESS-000001): General, Pricing (50 rows), Inventory (50 rows)
  and Availability (2 rows) tabs all render.

## Run 1

Django 5.2.19, MySQL 8.4.13, isolation READ-COMMITTED, DEBUG=True, runs=20 (plus 1 warm-up), in-process test client.
Dataset: 5000 products, 10142 variants, 20284 price rules, 10142 inventory rows.

| Case | Requests | Queries | Median ms | p95 ms | Bytes | Body SHA-256 |
| --- | ---: | ---: | ---: | ---: | ---: | --- |
| GET list, page 1 | 1 | 5 | 5.7 | 7.1 | 3926 | `9f473e3a786c` |
| GET list, last page (100) | 1 | 5 | 11.9 | 12.7 | 3984 | `5881d7a30ba7` |
| GET list, search "0012" | 1 | 5 | 8.0 | 10.7 | 3990 | `49555ad7f741` |
| GET detail | 1 | 4 | 3.0 | 4.6 | 224 | `e38c015a748b` |
| GET editor, 2 variants | 1 | 7 | 5.7 | 8.1 | 746 | `d1a7b6179da3` |
| GET editor, 50 variants | 1 | 7 | 8.4 | 11.4 | 11233 | `96846bf6f450` |
| GET activity | 1 | 5 | 4.7 | 5.6 | 52 | `0595424161ea` |
| Workflow: catalogue page load | 1 | 5 | 4.7 | 6.7 | 3926 | `9f473e3a786c` |
| Workflow: open product, 50 variants | 2 | 11 | 11.4 | 14.3 | 11458 | `40aaf2fad51e` |

## Run 2

Django 5.2.19, MySQL 8.4.13, isolation READ-COMMITTED, DEBUG=True, runs=20 (plus 1 warm-up), in-process test client.
Dataset: 5000 products, 10142 variants, 20284 price rules, 10142 inventory rows.

| Case | Requests | Queries | Median ms | p95 ms | Bytes | Body SHA-256 |
| --- | ---: | ---: | ---: | ---: | ---: | --- |
| GET list, page 1 | 1 | 5 | 5.3 | 7.5 | 3926 | `9f473e3a786c` |
| GET list, last page (100) | 1 | 5 | 9.8 | 12.6 | 3984 | `5881d7a30ba7` |
| GET list, search "0012" | 1 | 5 | 7.4 | 9.5 | 3990 | `49555ad7f741` |
| GET detail | 1 | 4 | 2.9 | 3.8 | 224 | `e38c015a748b` |
| GET editor, 2 variants | 1 | 7 | 4.2 | 6.4 | 746 | `d1a7b6179da3` |
| GET editor, 50 variants | 1 | 7 | 8.1 | 10.1 | 11233 | `96846bf6f450` |
| GET activity | 1 | 5 | 4.2 | 5.1 | 52 | `0595424161ea` |
| Workflow: catalogue page load | 1 | 5 | 5.6 | 7.5 | 3926 | `9f473e3a786c` |
| Workflow: open product, 50 variants | 2 | 11 | 11.5 | 13.9 | 11458 | `40aaf2fad51e` |
