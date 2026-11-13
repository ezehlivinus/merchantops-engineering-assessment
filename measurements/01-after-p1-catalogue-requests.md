# 01: After P1 (catalogue no longer fetches each row's detail)

- Change: `frontend/src/views/CatalogueView.vue` renders the list response directly instead of
  awaiting `GET products/<id>/` sequentially for each of the 25 rows.
- The command's "Workflow: catalogue page load" now replays 1 request, matching the new view.
  Baseline sequence (list + 25 details) is in `00-baseline.md`.
- Same command, machine and dataset as `00-baseline.md`:
  `docker compose exec backend python manage.py measure_endpoints --runs 20`

## Result

| Workflow: catalogue page load | Requests | Queries | Median ms |
| --- | ---: | ---: | ---: |
| Before (00, runs 1 / 2) | 26 | 130 | 104.6 / 94.5 |
| After (01, runs 1 / 2) | 1 | 30 | 17.9 / 18.0 |

The 26 requests were sequential, so in the browser each one also paid a full
network and proxy round trip before the table could render; the in-process
timing above understates the real-world gain.

## Unchanged data
- All single-endpoint body hashes match `00-baseline.md`.
- The removed loop overwrote `name`, `sku`, `status`, `image_url` with the detail
  response. A comparison of `ProductListSerializer` and `ProductUpdateSerializer`
  output for those fields over all 5000 seeded products found 0 differences.

## Run 1

Django 5.2.19, MySQL 8.4.13, isolation READ-COMMITTED, DEBUG=True, runs=20 (plus 1 warm-up), in-process test client.
Dataset: 5000 products, 10142 variants, 20284 price rules, 10142 inventory rows.

| Case | Requests | Queries | Median ms | p95 ms | Bytes | Body SHA-256 |
| --- | ---: | ---: | ---: | ---: | ---: | --- |
| GET list, page 1 | 1 | 30 | 16.2 | 18.4 | 3926 | `9f473e3a786c` |
| GET list, last page (100) | 1 | 30 | 19.7 | 24.7 | 3984 | `5881d7a30ba7` |
| GET list, search "0012" | 1 | 30 | 20.3 | 24.5 | 3990 | `49555ad7f741` |
| GET detail | 1 | 4 | 4.3 | 5.8 | 224 | `e38c015a748b` |
| GET editor, 2 variants | 1 | 10 | 8.8 | 11.5 | 746 | `d1a7b6179da3` |
| GET editor, 50 variants | 1 | 106 | 64.9 | 79.1 | 11233 | `96846bf6f450` |
| GET activity | 1 | 5 | 2.8 | 5.1 | 52 | `0595424161ea` |
| Workflow: catalogue page load | 1 | 30 | 17.9 | 24.9 | 3926 | `9f473e3a786c` |
| Workflow: open product, 50 variants | 2 | 110 | 72.0 | 80.1 | 11458 | `40aaf2fad51e` |

## Run 2

Django 5.2.19, MySQL 8.4.13, isolation READ-COMMITTED, DEBUG=True, runs=20 (plus 1 warm-up), in-process test client.
Dataset: 5000 products, 10142 variants, 20284 price rules, 10142 inventory rows.

| Case | Requests | Queries | Median ms | p95 ms | Bytes | Body SHA-256 |
| --- | ---: | ---: | ---: | ---: | ---: | --- |
| GET list, page 1 | 1 | 30 | 17.4 | 21.6 | 3926 | `9f473e3a786c` |
| GET list, last page (100) | 1 | 30 | 20.4 | 25.9 | 3984 | `5881d7a30ba7` |
| GET list, search "0012" | 1 | 30 | 15.8 | 22.6 | 3990 | `49555ad7f741` |
| GET detail | 1 | 4 | 3.1 | 3.4 | 224 | `e38c015a748b` |
| GET editor, 2 variants | 1 | 10 | 8.0 | 8.9 | 746 | `d1a7b6179da3` |
| GET editor, 50 variants | 1 | 106 | 69.5 | 75.8 | 11233 | `96846bf6f450` |
| GET activity | 1 | 5 | 5.2 | 6.1 | 52 | `0595424161ea` |
| Workflow: catalogue page load | 1 | 30 | 18.0 | 20.0 | 3926 | `9f473e3a786c` |
| Workflow: open product, 50 variants | 2 | 110 | 78.4 | 126.8 | 11458 | `40aaf2fad51e` |
