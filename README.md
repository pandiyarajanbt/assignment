# Data Ingestion API

Production-oriented Django REST API for streaming CSV ingestion into relational tables.

## Structure

```text
Infilect/
  settings.py
  urls.py
files/
  models/         # relational schema and ingestion audit tables
  serializers/    # request validation
  services/       # streaming CSV ingestion, validation, bulk persistence
  utils/          # normalization and typed validators
  views/          # thin DRF views
  urls.py
data/             # sample CSV files
```

## Database

The project is configured for PostgreSQL through environment variables and falls back to SQLite for local checks.

```bash
POSTGRES_DB=infilect
POSTGRES_USER=postgres
POSTGRES_PASSWORD=postgres
POSTGRES_HOST=localhost
POSTGRES_PORT=5432
DJANGO_SECRET_KEY=change-me
DJANGO_DEBUG=False
DJANGO_ALLOWED_HOSTS=localhost,127.0.0.1
```

Run locally:

```bash
./venv/bin/python manage.py migrate
./venv/bin/python manage.py runserver
```

## Endpoints

All uploads use `multipart/form-data` with a `file` field.

```bash
curl -X POST http://127.0.0.1:8000/api/upload/stores/ \
  -F "file=@data/stores_master.csv"

curl -X POST http://127.0.0.1:8000/api/upload/users/ \
  -F "file=@data/users_master.csv"

curl -X POST http://127.0.0.1:8000/api/upload/mappings/ \
  -F "file=@data/store_user_mapping.csv"
```

Example response:

```json
{
  "job_id": "0e11a7d0-9125-48d9-8e13-eed377f1dba0",
  "status": "completed",
  "total_rows": 100,
  "success_count": 90,
  "failed_count": 10,
  "errors": [
    {"row": 5, "column": "email", "error": "Invalid email format"}
  ],
  "errors_truncated": false,
  "error_report_url": "http://127.0.0.1:8000/api/uploads/0e11a7d0-9125-48d9-8e13-eed377f1dba0/errors.csv"
}
```

Download all row-level errors:

```bash
curl -OJ http://127.0.0.1:8000/api/uploads/<job_id>/errors.csv
```

## Postman

1. Set method to `POST`.
2. Use URL `http://127.0.0.1:8000/api/upload/stores/`, `/users/`, or `/mappings/`.
3. In `Body`, choose `form-data`.
4. Add key `file`, change its type to `File`, and select the CSV.
5. Send the request.

## Ingestion Notes

CSV files are streamed with Python's `csv.DictReader`; rows are processed in 3,000-row batches so a 500k-row file is never loaded into memory.

Invalid rows are skipped and valid rows continue to ingest. This prevents one bad record from blocking the entire file, while every row-level failure is persisted to `IngestionError` for audit and CSV export.

Lookup tables use both `name` and `normalized_name` uniqueness. Imports trim and casefold lookup values before matching, preventing duplicates such as `Chennai`, `chennai `, and ` CHENNAI`.

Writes use `bulk_create` inside transactions per batch. Foreign-key dependencies for mappings are fetched in batches to avoid N+1 queries.

Run tests:

```bash
./venv/bin/python manage.py test files
```
