# 00: Baseline (before any optimization)

- Code measured: commit `0d1602b` (initial assessment commit) plus the measurement command itself, which changes no application code.
- Command: `docker compose exec backend python manage.py measure_endpoints --runs 20`
- Machine: Linux 7.0 x86_64, 12 cores, Docker 27.2.1. Stack otherwise idle.
- Signed in as `demo_operator`, merchant Northstar Retail (`assessment-merchant-01`).

Query counts and body hashes are exact and repeat on every run. Timings vary
by roughly 30% between runs on this machine, so both runs are shown; compare
medians only as orders of magnitude.

## Run 1

Django 5.2.19, MySQL 8.4.13, isolation READ-COMMITTED, DEBUG=True, runs=20 (plus 1 warm-up), in-process test client.
Dataset: 5000 products, 10142 variants, 20284 price rules, 10142 inventory rows.

| Case | Requests | Queries | Median ms | p95 ms | Bytes | Body SHA-256 |
| --- | ---: | ---: | ---: | ---: | ---: | --- |
| GET list, page 1 | 1 | 30 | 12.7 | 19.6 | 3926 | `9f473e3a786c` |
| GET list, last page (100) | 1 | 30 | 22.0 | 25.7 | 3984 | `5881d7a30ba7` |
| GET list, search "0012" | 1 | 30 | 18.3 | 22.4 | 3990 | `49555ad7f741` |
| GET detail | 1 | 4 | 2.4 | 2.7 | 224 | `e38c015a748b` |
| GET editor, 2 variants | 1 | 10 | 4.6 | 6.1 | 746 | `d1a7b6179da3` |
| GET editor, 50 variants | 1 | 106 | 55.5 | 74.4 | 11233 | `96846bf6f450` |
| GET activity | 1 | 5 | 2.9 | 4.0 | 52 | `0595424161ea` |
| Workflow: catalogue page load | 26 | 130 | 104.6 | 117.6 | 9599 | `0a1c987dcb71` |
| Workflow: open product, 50 variants | 2 | 110 | 69.8 | 76.0 | 11458 | `40aaf2fad51e` |

## Run 2

Django 5.2.19, MySQL 8.4.13, isolation READ-COMMITTED, DEBUG=True, runs=20 (plus 1 warm-up), in-process test client.
Dataset: 5000 products, 10142 variants, 20284 price rules, 10142 inventory rows.

| Case | Requests | Queries | Median ms | p95 ms | Bytes | Body SHA-256 |
| --- | ---: | ---: | ---: | ---: | ---: | --- |
| GET list, page 1 | 1 | 30 | 15.4 | 18.3 | 3926 | `9f473e3a786c` |
| GET list, last page (100) | 1 | 30 | 21.9 | 25.8 | 3984 | `5881d7a30ba7` |
| GET list, search "0012" | 1 | 30 | 16.5 | 22.8 | 3990 | `49555ad7f741` |
| GET detail | 1 | 4 | 3.1 | 4.7 | 224 | `e38c015a748b` |
| GET editor, 2 variants | 1 | 10 | 7.3 | 8.0 | 746 | `d1a7b6179da3` |
| GET editor, 50 variants | 1 | 106 | 39.5 | 57.6 | 11233 | `96846bf6f450` |
| GET activity | 1 | 5 | 2.8 | 4.1 | 52 | `0595424161ea` |
| Workflow: catalogue page load | 26 | 130 | 94.5 | 121.0 | 9599 | `0a1c987dcb71` |
| Workflow: open product, 50 variants | 2 | 110 | 47.7 | 70.7 | 11458 | `40aaf2fad51e` |
