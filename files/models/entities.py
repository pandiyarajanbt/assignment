import uuid

from django.db import models

from files.utils.normalization import normalize_key


class TimeStampedModel(models.Model):
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


class NamedLookup(TimeStampedModel):
    name = models.CharField(max_length=150, unique=True)
    normalized_name = models.CharField(max_length=150, unique=True, editable=False)

    class Meta:
        abstract = True
        ordering = ("name",)

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        self.normalized_name = normalize_key(self.name)
        super().save(*args, **kwargs)


class StoreBrand(NamedLookup):
    pass


class StoreType(NamedLookup):
    pass


class City(NamedLookup):
    pass


class State(NamedLookup):
    pass


class Country(NamedLookup):
    pass


class Region(NamedLookup):
    pass


class Store(TimeStampedModel):
    store_id = models.CharField(max_length=64, unique=True)
    store_external_id = models.CharField(max_length=64, unique=True)
    name = models.CharField(max_length=255)
    title = models.CharField(max_length=255)
    store_brand = models.ForeignKey(
        StoreBrand, null=True, on_delete=models.SET_NULL, related_name="stores"
    )
    store_type = models.ForeignKey(
        StoreType, null=True, on_delete=models.SET_NULL, related_name="stores"
    )
    city = models.ForeignKey(City, null=True, on_delete=models.SET_NULL, related_name="stores")
    state = models.ForeignKey(State, null=True, on_delete=models.SET_NULL, related_name="stores")
    country = models.ForeignKey(Country, null=True, on_delete=models.SET_NULL, related_name="stores")
    region = models.ForeignKey(Region, null=True, on_delete=models.SET_NULL, related_name="stores")
    latitude = models.DecimalField(max_digits=9, decimal_places=6)
    longitude = models.DecimalField(max_digits=9, decimal_places=6)
    is_active = models.BooleanField(default=True)

    class Meta:
        indexes = [
            models.Index(fields=("store_id",)),
            models.Index(fields=("store_external_id",)),
        ]

    def __str__(self):
        return self.store_id


class User(TimeStampedModel):
    class UserType(models.IntegerChoices):
        ADMIN = 1, "Admin"
        SUPERVISOR = 2, "Supervisor"
        MANAGER = 3, "Manager"
        FIELD_USER = 7, "Field User"

    username = models.CharField(max_length=150, unique=True)
    first_name = models.CharField(max_length=150)
    last_name = models.CharField(max_length=150)
    email = models.EmailField(unique=True)
    user_type = models.PositiveSmallIntegerField(choices=UserType.choices, default=UserType.ADMIN)
    phone_number = models.CharField(max_length=20)
    supervisor = models.ForeignKey(
        "self",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="direct_reports",
    )
    is_active = models.BooleanField(default=True)

    class Meta:
        indexes = [
            models.Index(fields=("username",)),
            models.Index(fields=("email",)),
        ]

    def __str__(self):
        return self.username


class PermanentJourneyPlan(TimeStampedModel):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="journey_plans")
    store = models.ForeignKey(Store, on_delete=models.CASCADE, related_name="journey_plans")
    date = models.DateField()
    is_active = models.BooleanField(default=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=("user", "store", "date"),
                name="unique_user_store_date_journey_plan",
            )
        ]
        indexes = [
            models.Index(fields=("date",)),
            models.Index(fields=("user", "date")),
            models.Index(fields=("store", "date")),
        ]

    def __str__(self):
        return f"{self.user_id}:{self.store_id}:{self.date}"


class IngestionJob(TimeStampedModel):
    class UploadType(models.TextChoices):
        STORES = "stores", "Stores"
        USERS = "users", "Users"
        MAPPINGS = "mappings", "Mappings"

    class Status(models.TextChoices):
        PROCESSING = "processing", "Processing"
        COMPLETED = "completed", "Completed"
        FAILED = "failed", "Failed"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    upload_type = models.CharField(max_length=32, choices=UploadType.choices)
    filename = models.CharField(max_length=255)
    status = models.CharField(max_length=32, choices=Status.choices, default=Status.PROCESSING)
    total_rows = models.PositiveIntegerField(default=0)
    success_count = models.PositiveIntegerField(default=0)
    failed_count = models.PositiveIntegerField(default=0)
    error_message = models.TextField(blank=True)

    class Meta:
        ordering = ("-created_at",)


class IngestionError(TimeStampedModel):
    job = models.ForeignKey(IngestionJob, on_delete=models.CASCADE, related_name="errors")
    row_number = models.PositiveIntegerField()
    column = models.CharField(max_length=150)
    message = models.TextField()

    class Meta:
        ordering = ("row_number", "id")
        indexes = [
            models.Index(fields=("job", "row_number")),
        ]
