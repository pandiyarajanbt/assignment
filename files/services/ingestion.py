import csv
import io
import logging
from dataclasses import dataclass, field

from django.db import transaction

from files.models import (
    City,
    Country,
    IngestionError,
    IngestionJob,
    PermanentJourneyPlan,
    Region,
    State,
    Store,
    StoreBrand,
    StoreType,
    User,
)
from files.utils.normalization import normalize_bool, normalize_key, normalize_text
from files.utils.validation import (
    optional_text,
    require,
    validate_date,
    validate_decimal,
    validate_email,
    validate_choice,
    validate_length,
    validate_phone,
)

logger = logging.getLogger(__name__)

BATCH_SIZE = 3000
RESPONSE_ERROR_LIMIT = 1000
LOOKUP_NAME_MAX_LENGTH = 150
STORE_ID_MAX_LENGTH = 64
STORE_EXTERNAL_ID_MAX_LENGTH = 64
STORE_TEXT_MAX_LENGTH = 255
USERNAME_MAX_LENGTH = 150
USER_NAME_MAX_LENGTH = 150
EMAIL_MAX_LENGTH = 254
PHONE_MAX_LENGTH = 20
ALLOWED_USER_TYPES = {1, 2, 3, 7}


@dataclass
class RowError:
    row: int
    column: str
    error: str


@dataclass
class IngestionSummary:
    job: IngestionJob
    total_rows: int = 0
    success_count: int = 0
    failed_count: int = 0
    errors: list[dict] = field(default_factory=list)
    errors_truncated: bool = False

    def as_response(self, request=None):
        data = {
            "job_id": str(self.job.id),
            "status": self.job.status,
            "total_rows": self.total_rows,
            "success_count": self.success_count,
            "failed_count": self.failed_count,
            "errors": self.errors,
            "errors_truncated": self.errors_truncated,
        }
        path = f"/api/uploads/{self.job.id}/errors.csv"
        data["error_report_url"] = request.build_absolute_uri(path) if request else path
        return data


class ErrorSink:
    def __init__(self, job):
        self.job = job
        self.buffer = []
        self.response_errors = []
        self.count = 0

    def add(self, row, column, message):
        self.count += 1
        if len(self.response_errors) < RESPONSE_ERROR_LIMIT:
            self.response_errors.append({"row": row, "column": column, "error": message})
        self.buffer.append(
            IngestionError(job=self.job, row_number=row, column=column, message=message)
        )
        if len(self.buffer) >= BATCH_SIZE:
            self.flush()

    def extend(self, errors):
        for error in errors:
            self.add(error.row, error.column, error.error)

    def flush(self):
        if self.buffer:
            IngestionError.objects.bulk_create(self.buffer, batch_size=BATCH_SIZE)
            self.buffer.clear()


def process_store_upload(uploaded_file, request=None):
    job = _create_job(IngestionJob.UploadType.STORES, uploaded_file)
    processor = StoreIngestionProcessor(job, uploaded_file)
    return processor.run(request=request)


def process_user_upload(uploaded_file, request=None):
    job = _create_job(IngestionJob.UploadType.USERS, uploaded_file)
    processor = UserIngestionProcessor(job, uploaded_file)
    return processor.run(request=request)


def process_mapping_upload(uploaded_file, request=None):
    job = _create_job(IngestionJob.UploadType.MAPPINGS, uploaded_file)
    processor = MappingIngestionProcessor(job, uploaded_file)
    return processor.run(request=request)


def _create_job(upload_type, uploaded_file):
    return IngestionJob.objects.create(upload_type=upload_type, filename=uploaded_file.name)


class BaseCSVProcessor:
    required_columns = ()

    def __init__(self, job, uploaded_file):
        self.job = job
        self.uploaded_file = uploaded_file
        self.error_sink = ErrorSink(job)
        self.total_rows = 0
        self.success_count = 0

    def run(self, request=None):
        logger.info("Starting %s ingestion job %s", self.job.upload_type, self.job.id)
        try:
            for batch in self._read_batches():
                self._process_batch(batch)
            self.error_sink.flush()
            status = IngestionJob.Status.COMPLETED
            error_message = ""
        except Exception as exc:
            logger.exception("Ingestion job %s failed", self.job.id)
            status = IngestionJob.Status.FAILED
            error_message = str(exc)
        failed_count = self.error_sink.count
        self.job.status = status
        self.job.total_rows = self.total_rows
        self.job.success_count = self.success_count
        self.job.failed_count = failed_count
        self.job.error_message = error_message
        self.job.save(
            update_fields=[
                "status",
                "total_rows",
                "success_count",
                "failed_count",
                "error_message",
                "updated_at",
            ]
        )
        summary = IngestionSummary(
            job=self.job,
            total_rows=self.total_rows,
            success_count=self.success_count,
            failed_count=failed_count,
            errors=self.error_sink.response_errors,
            errors_truncated=failed_count > len(self.error_sink.response_errors),
        )
        logger.info("Completed %s ingestion job %s", self.job.upload_type, self.job.id)
        return summary.as_response(request=request)

    def _read_batches(self):
        self.uploaded_file.seek(0)
        wrapper = io.TextIOWrapper(self.uploaded_file.file, encoding="utf-8-sig", newline="")
        reader = csv.DictReader(wrapper)
        missing = [column for column in self.required_columns if column not in (reader.fieldnames or [])]
        if missing:
            self.error_sink.add(1, ",".join(missing), "Missing required CSV column(s)")
            return

        batch = []
        for row_number, row in enumerate(reader, start=2):
            self.total_rows += 1
            batch.append((row_number, row))
            if len(batch) >= BATCH_SIZE:
                yield batch
                batch = []
        if batch:
            yield batch

    def _process_batch(self, batch):
        raise NotImplementedError


class LookupResolver:
    model_fields = {
        "store_brand": StoreBrand,
        "store_type": StoreType,
        "city": City,
        "state": State,
        "country": Country,
        "region": Region,
    }

    def resolve(self, field_names):
        resolved = {}
        for field_name, raw_names in field_names.items():
            model = self.model_fields[field_name]
            normalized_to_name = {
                normalize_key(name): normalize_text(name)
                for name in raw_names
                if normalize_text(name)
            }
            if not normalized_to_name:
                resolved[field_name] = {}
                continue

            existing = {
                item.normalized_name: item
                for item in model.objects.filter(normalized_name__in=normalized_to_name.keys())
            }
            missing = [
                model(name=name, normalized_name=normalized)
                for normalized, name in normalized_to_name.items()
                if normalized not in existing
            ]
            if missing:
                # ignore_conflicts handles concurrent uploads racing to create the same normalized lookup.
                model.objects.bulk_create(missing, ignore_conflicts=True, batch_size=BATCH_SIZE)
                existing = {
                    item.normalized_name: item
                    for item in model.objects.filter(normalized_name__in=normalized_to_name.keys())
                }
            resolved[field_name] = existing
        return resolved


class StoreIngestionProcessor(BaseCSVProcessor):
    required_columns = (
        "store_id",
        "store_external_id",
        "name",
        "title",
        "store_brand",
        "store_type",
        "city",
        "state",
        "country",
        "region",
        "latitude",
        "longitude",
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.seen_store_ids = set()
        self.seen_external_ids = set()
        self.lookup_resolver = LookupResolver()

    def _process_batch(self, batch):
        valid_rows = []
        lookup_inputs = {field: set() for field in LookupResolver.model_fields}
        batch_store_ids = set()
        batch_external_ids = set()

        existing_store_ids = set(
            Store.objects.filter(
                store_id__in=[normalize_text(row.get("store_id")) for _, row in batch]
            ).values_list("store_id", flat=True)
        )
        existing_external_ids = set(
            Store.objects.filter(
                store_external_id__in=[normalize_text(row.get("store_external_id")) for _, row in batch]
            ).values_list("store_external_id", flat=True)
        )

        for row_number, row in batch:
            cleaned, errors = self._validate_store_row(
                row_number,
                row,
                existing_store_ids,
                existing_external_ids,
                batch_store_ids,
                batch_external_ids,
            )
            if errors:
                self.error_sink.extend(errors)
                continue
            valid_rows.append(cleaned)
            for field in lookup_inputs:
                lookup_inputs[field].add(cleaned[field])

        if not valid_rows:
            return

        lookups = self.lookup_resolver.resolve(lookup_inputs)
        stores = [
            Store(
                store_id=row["store_id"],
                store_external_id=row["store_external_id"],
                name=row["name"],
                title=row["title"],
                store_brand=lookups["store_brand"][normalize_key(row["store_brand"])],
                store_type=lookups["store_type"][normalize_key(row["store_type"])],
                city=lookups["city"][normalize_key(row["city"])],
                state=lookups["state"][normalize_key(row["state"])],
                country=lookups["country"][normalize_key(row["country"])],
                region=lookups["region"][normalize_key(row["region"])],
                latitude=row["latitude"],
                longitude=row["longitude"],
            )
            for row in valid_rows
        ]
        with transaction.atomic():
            Store.objects.bulk_create(stores, batch_size=BATCH_SIZE)
        self.success_count += len(stores)

    def _validate_store_row(
        self,
        row_number,
        row,
        existing_store_ids,
        existing_external_ids,
        batch_store_ids,
        batch_external_ids,
    ):
        errors = []
        cleaned = {}
        for column in self.required_columns:
            try:
                cleaned[column] = require(row, column)
            except ValueError as exc:
                errors.append(RowError(row_number, column, str(exc)))

        if errors:
            return None, errors

        try:
            cleaned["latitude"] = validate_decimal(cleaned["latitude"], -90, 90)
        except ValueError as exc:
            errors.append(RowError(row_number, "latitude", str(exc)))
        try:
            cleaned["longitude"] = validate_decimal(cleaned["longitude"], -180, 180)
        except ValueError as exc:
            errors.append(RowError(row_number, "longitude", str(exc)))

        store_id = cleaned["store_id"]
        external_id = cleaned["store_external_id"]
        length_checks = (
            ("store_id", STORE_ID_MAX_LENGTH),
            ("store_external_id", STORE_EXTERNAL_ID_MAX_LENGTH),
            ("name", STORE_TEXT_MAX_LENGTH),
            ("title", STORE_TEXT_MAX_LENGTH),
            ("store_brand", LOOKUP_NAME_MAX_LENGTH),
            ("store_type", LOOKUP_NAME_MAX_LENGTH),
            ("city", LOOKUP_NAME_MAX_LENGTH),
            ("state", LOOKUP_NAME_MAX_LENGTH),
            ("country", LOOKUP_NAME_MAX_LENGTH),
            ("region", LOOKUP_NAME_MAX_LENGTH),
        )
        for column, max_length in length_checks:
            try:
                cleaned[column] = validate_length(cleaned[column], max_length)
            except ValueError as exc:
                errors.append(RowError(row_number, column, str(exc)))

        if store_id in existing_store_ids or store_id in self.seen_store_ids or store_id in batch_store_ids:
            errors.append(RowError(row_number, "store_id", "Store ID already exists"))
        if (
            external_id in existing_external_ids
            or external_id in self.seen_external_ids
            or external_id in batch_external_ids
        ):
            errors.append(RowError(row_number, "store_external_id", "Store external ID already exists"))

        batch_store_ids.add(store_id)
        batch_external_ids.add(external_id)
        if errors:
            return None, errors

        self.seen_store_ids.add(store_id)
        self.seen_external_ids.add(external_id)
        return cleaned, []


class UserIngestionProcessor(BaseCSVProcessor):
    required_columns = (
        "username",
        "first_name",
        "last_name",
        "email",
        "user_type",
        "phone_number",
        "supervisor_username",
        "is_active",
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.seen_usernames = set()
        self.seen_emails = set()

    def _process_batch(self, batch):
        valid_rows = []
        batch_usernames = set()
        batch_emails = set()
        usernames = [normalize_key(row.get("username")) for _, row in batch]
        emails = [normalize_text(row.get("email")).lower() for _, row in batch]
        existing_usernames = set(
            User.objects.filter(username__in=usernames).values_list("username", flat=True)
        )
        existing_emails = set(User.objects.filter(email__in=emails).values_list("email", flat=True))

        for row_number, row in batch:
            cleaned, errors = self._validate_user_row(
                row_number,
                row,
                existing_usernames,
                existing_emails,
                batch_usernames,
                batch_emails,
            )
            if errors:
                self.error_sink.extend(errors)
                continue
            valid_rows.append(cleaned)

        if not valid_rows:
            return

        valid_usernames = {row["username"] for row in valid_rows}
        supervisor_usernames = {
            row["supervisor_username"] for row in valid_rows if row["supervisor_username"]
        }
        supervisors = {
            user.username: user
            for user in User.objects.filter(username__in=supervisor_usernames)
        }
        creatable_rows = self._filter_rows_with_valid_supervisors(
            valid_rows, supervisors, valid_usernames
        )

        users = [
            User(
                username=row["username"],
                first_name=row["first_name"],
                last_name=row["last_name"],
                email=row["email"],
                user_type=row["user_type"],
                phone_number=row["phone_number"],
                is_active=row["is_active"],
            )
            for row in creatable_rows
        ]
        if users:
            with transaction.atomic():
                User.objects.bulk_create(users, batch_size=BATCH_SIZE)
                created_or_existing = {
                    user.username: user
                    for user in User.objects.filter(
                        username__in=valid_usernames | supervisor_usernames
                    )
                }
                users_to_update = []
                for row in creatable_rows:
                    supervisor_username = row["supervisor_username"]
                    if not supervisor_username:
                        continue
                    user = created_or_existing[row["username"]]
                    supervisor = created_or_existing.get(supervisor_username)
                    if supervisor:
                        user.supervisor = supervisor
                        users_to_update.append(user)
                if users_to_update:
                    User.objects.bulk_update(users_to_update, ["supervisor"], batch_size=BATCH_SIZE)
            self.success_count += len(users)

    def _filter_rows_with_valid_supervisors(self, rows, supervisors, valid_usernames):
        rows_by_username = {row["username"]: row for row in rows}
        creatable = {}

        def can_create(row, path):
            username = row["username"]
            if username in creatable:
                return creatable[username]

            supervisor_username = row["supervisor_username"]
            if not supervisor_username or supervisor_username in supervisors:
                creatable[username] = True
                return True

            if supervisor_username not in valid_usernames:
                self.error_sink.add(
                    row["row_number"], "supervisor_username", "Supervisor does not exist"
                )
                creatable[username] = False
                return False

            if supervisor_username in path:
                self.error_sink.add(
                    row["row_number"], "supervisor_username", "Circular supervisor relationship"
                )
                creatable[username] = False
                return False

            supervisor_row = rows_by_username[supervisor_username]
            if not can_create(supervisor_row, path | {username}):
                self.error_sink.add(
                    row["row_number"], "supervisor_username", "Supervisor row is invalid"
                )
                creatable[username] = False
                return False

            creatable[username] = True
            return True

        return [row for row in rows if can_create(row, set())]

    def _validate_user_row(
        self,
        row_number,
        row,
        existing_usernames,
        existing_emails,
        batch_usernames,
        batch_emails,
    ):
        errors = []
        cleaned = {}
        cleaned["row_number"] = row_number
        for column in ("username", "first_name", "last_name", "email", "user_type", "phone_number", "is_active"):
            try:
                cleaned[column] = require(row, column)
            except ValueError as exc:
                errors.append(RowError(row_number, column, str(exc)))

        if errors:
            return None, errors

        cleaned["username"] = normalize_key(cleaned["username"])
        try:
            cleaned["username"] = validate_length(cleaned["username"], USERNAME_MAX_LENGTH)
        except ValueError as exc:
            errors.append(RowError(row_number, "username", str(exc)))
        try:
            cleaned["first_name"] = validate_length(cleaned["first_name"], USER_NAME_MAX_LENGTH)
        except ValueError as exc:
            errors.append(RowError(row_number, "first_name", str(exc)))
        try:
            cleaned["last_name"] = validate_length(cleaned["last_name"], USER_NAME_MAX_LENGTH)
        except ValueError as exc:
            errors.append(RowError(row_number, "last_name", str(exc)))
        try:
            cleaned["email"] = validate_email(cleaned["email"])
            cleaned["email"] = validate_length(cleaned["email"], EMAIL_MAX_LENGTH)
        except ValueError as exc:
            errors.append(RowError(row_number, "email", str(exc)))
        try:
            cleaned["phone_number"] = validate_phone(cleaned["phone_number"])
            cleaned["phone_number"] = validate_length(cleaned["phone_number"], PHONE_MAX_LENGTH)
        except ValueError as exc:
            errors.append(RowError(row_number, "phone_number", str(exc)))
        try:
            cleaned["user_type"] = validate_choice(cleaned["user_type"], ALLOWED_USER_TYPES)
        except ValueError as exc:
            errors.append(RowError(row_number, "user_type", str(exc)))
        try:
            cleaned["is_active"] = normalize_bool(cleaned["is_active"])
        except ValueError as exc:
            errors.append(RowError(row_number, "is_active", str(exc)))

        username = cleaned["username"]
        email = cleaned.get("email")
        if username in existing_usernames or username in self.seen_usernames or username in batch_usernames:
            errors.append(RowError(row_number, "username", "Username already exists"))
        if email and (email in existing_emails or email in self.seen_emails or email in batch_emails):
            errors.append(RowError(row_number, "email", "Email already exists"))

        supervisor_username = normalize_key(optional_text(row, "supervisor_username"))
        cleaned["supervisor_username"] = supervisor_username
        try:
            cleaned["supervisor_username"] = validate_length(supervisor_username, USERNAME_MAX_LENGTH)
        except ValueError as exc:
            errors.append(RowError(row_number, "supervisor_username", str(exc)))
        if supervisor_username == username:
            errors.append(RowError(row_number, "supervisor_username", "User cannot supervise themselves"))

        batch_usernames.add(username)
        if email:
            batch_emails.add(email)
        if errors:
            return None, errors

        self.seen_usernames.add(username)
        if email:
            self.seen_emails.add(email)
        return cleaned, []


class MappingIngestionProcessor(BaseCSVProcessor):
    required_columns = ("username", "store_id", "date", "is_active")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.seen_keys = set()

    def _process_batch(self, batch):
        usernames = {normalize_key(row.get("username")) for _, row in batch}
        store_ids = {normalize_text(row.get("store_id")) for _, row in batch}
        users = {user.username: user for user in User.objects.filter(username__in=usernames)}
        stores = {store.store_id: store for store in Store.objects.filter(store_id__in=store_ids)}

        valid_rows = []
        keys = []
        batch_keys = set()
        for row_number, row in batch:
            cleaned, errors = self._validate_mapping_row(
                row_number, row, users, stores, batch_keys
            )
            if errors:
                self.error_sink.extend(errors)
                continue
            valid_rows.append(cleaned)
            keys.append((cleaned["user"].id, cleaned["store"].id, cleaned["date"]))

        existing_keys = set()
        if keys:
            user_ids = {key[0] for key in keys}
            store_ids = {key[1] for key in keys}
            dates = {key[2] for key in keys}
            existing_keys = set(
                PermanentJourneyPlan.objects.filter(
                    user_id__in=user_ids,
                    store_id__in=store_ids,
                    date__in=dates,
                ).values_list("user_id", "store_id", "date")
            )

        plans = []
        for cleaned in valid_rows:
            key = (cleaned["user"].id, cleaned["store"].id, cleaned["date"])
            if key in existing_keys or key in self.seen_keys:
                self.error_sink.add(cleaned["row_number"], "mapping", "Mapping already exists")
                continue
            self.seen_keys.add(key)
            plans.append(
                PermanentJourneyPlan(
                    user=cleaned["user"],
                    store=cleaned["store"],
                    date=cleaned["date"],
                    is_active=cleaned["is_active"],
                )
            )

        if plans:
            with transaction.atomic():
                PermanentJourneyPlan.objects.bulk_create(plans, batch_size=BATCH_SIZE)
            self.success_count += len(plans)

    def _validate_mapping_row(self, row_number, row, users, stores, batch_keys):
        errors = []
        try:
            username = normalize_key(require(row, "username"))
        except ValueError as exc:
            errors.append(RowError(row_number, "username", str(exc)))
            username = ""
        try:
            store_id = require(row, "store_id")
        except ValueError as exc:
            errors.append(RowError(row_number, "store_id", str(exc)))
            store_id = ""
        try:
            date = validate_date(require(row, "date"))
        except ValueError as exc:
            errors.append(RowError(row_number, "date", str(exc)))
            date = None
        try:
            is_active = normalize_bool(require(row, "is_active"))
        except ValueError as exc:
            errors.append(RowError(row_number, "is_active", str(exc)))
            is_active = True

        user = users.get(username)
        store = stores.get(store_id)
        if username and not user:
            errors.append(RowError(row_number, "username", "User does not exist"))
        if store_id and not store:
            errors.append(RowError(row_number, "store_id", "Store does not exist"))

        key = (username, store_id, date)
        if None not in key and key in batch_keys:
            errors.append(RowError(row_number, "mapping", "Duplicate mapping in file batch"))
        batch_keys.add(key)
        if errors:
            return None, errors
        return {
            "row_number": row_number,
            "user": user,
            "store": store,
            "date": date,
            "is_active": is_active,
        }, []
