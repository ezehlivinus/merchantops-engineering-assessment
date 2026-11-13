# 02: After P2 (product list loads categories in the same query)

- Change: `ProductListView.get_queryset` adds `.select_related("category")`. The list
  serializer renders `category.name` per row, which previously ran one query per product.
- Same command, machine and dataset as `00-baseline.md`:
  `docker compose exec backend python manage.py measure_endpoints --runs 20`
- Regression guard: `catalog.tests.test_query_counts.ProductListQueryCountTests`
  (5 queries for a full page and for a search; fails with 30 and 16 without the fix).

## Result

| Case | Queries before (01) | After | Median ms before (01, runs 1 / 2) | After (runs 1 / 2) |
| --- | ---: | ---: | --- | --- |
| GET list, page 1 | 30 | 5 | 16.2 / 17.4 | 3.9 / 6.2 |
| GET list, last page (100) | 30 | 5 | 19.7 / 20.4 | 8.9 / 11.5 |
| GET list, search "0012" | 30 | 5 | 20.3 / 15.8 | 7.8 / 8.5 |

Cumulative for the catalogue screen against `00-baseline.md`: 26 requests / 130 queries /
~95-105 ms became 1 request / 5 queries / ~5 ms.

The 5 remaining queries: session, user, merchant membership check, paginator `COUNT(*)`,
and the page of products joined to categories.

## Unchanged data
All body hashes match `00-baseline.md` and `01-after-p1-catalogue-requests.md`.

## Run 1

Django 5.2.19, MySQL 8.4.13, isolation READ-COMMITTED, DEBUG=True, runs=20 (plus 1 warm-up), in-process test client.
Dataset: 5000 products, 10142 variants, 20284 price rules, 10142 inventory rows.

| Case | Requests | Queries | Median ms | p95 ms | Bytes | Body SHA-256 |
| --- | ---: | ---: | ---: | ---: | ---: | --- |
| GET list, page 1 | 1 | 5 | 3.9 | 7.3 | 3926 | `9f473e3a786c` |
| GET list, last page (100) | 1 | 5 | 8.9 | 11.6 | 3984 | `5881d7a30ba7` |
| GET list, search "0012" | 1 | 5 | 7.8 | 10.0 | 3990 | `49555ad7f741` |
| GET detail | 1 | 4 | 2.6 | 3.2 | 224 | `e38c015a748b` |
| GET editor, 2 variants | 1 | 10 | 7.4 | 8.4 | 746 | `d1a7b6179da3` |
| GET editor, 50 variants | 1 | 106 | 58.1 | 73.2 | 11233 | `96846bf6f450` |
| GET activity | 1 | 5 | 3.8 | 5.1 | 52 | `0595424161ea` |
| Workflow: catalogue page load | 1 | 5 | 5.0 | 6.3 | 3926 | `9f473e3a786c` |
| Workflow: open product, 50 variants | 2 | 110 | 54.1 | 70.2 | 11458 | `40aaf2fad51e` |

## Run 2

Django 5.2.19, MySQL 8.4.13, isolation READ-COMMITTED, DEBUG=True, runs=20 (plus 1 warm-up), in-process test client.
Dataset: 5000 products, 10142 variants, 20284 price rules, 10142 inventory rows.

| Case | Requests | Queries | Median ms | p95 ms | Bytes | Body SHA-256 |
| --- | ---: | ---: | ---: | ---: | ---: | --- |
| GET list, page 1 | 1 | 5 | 6.2 | 7.8 | 3926 | `9f473e3a786c` |
| GET list, last page (100) | 1 | 5 | 11.5 | 13.3 | 3984 | `5881d7a30ba7` |
| GET list, search "0012" | 1 | 5 | 8.5 | 11.0 | 3990 | `49555ad7f741` |
| GET detail | 1 | 4 | 3.4 | 4.7 | 224 | `e38c015a748b` |
| GET editor, 2 variants | 1 | 10 | 7.4 | 10.2 | 746 | `d1a7b6179da3` |
| GET editor, 50 variants | 1 | 106 | 53.5 | 69.4 | 11233 | `96846bf6f450` |
| GET activity | 1 | 5 | 3.9 | 5.0 | 52 | `0595424161ea` |
| Workflow: catalogue page load | 1 | 5 | 5.0 | 6.3 | 3926 | `9f473e3a786c` |
| Workflow: open product, 50 variants | 2 | 110 | 59.6 | 72.4 | 11458 | `40aaf2fad51e` |
