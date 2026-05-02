from django.contrib import admin

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


@admin.register(StoreBrand, StoreType, City, State, Country, Region)
class LookupAdmin(admin.ModelAdmin):
    list_display = ("name", "normalized_name", "created_at")
    search_fields = ("name", "normalized_name")
    readonly_fields = ("normalized_name", "created_at", "updated_at")


@admin.register(Store)
class StoreAdmin(admin.ModelAdmin):
    list_display = ("store_id", "title", "store_brand", "city", "is_active")
    list_select_related = ("store_brand", "city", "state", "country", "region", "store_type")
    search_fields = ("store_id", "store_external_id", "name", "title")


@admin.register(User)
class UserAdmin(admin.ModelAdmin):
    list_display = ("username", "email", "user_type", "supervisor", "is_active")
    list_select_related = ("supervisor",)
    search_fields = ("username", "email", "phone_number")


@admin.register(PermanentJourneyPlan)
class PermanentJourneyPlanAdmin(admin.ModelAdmin):
    list_display = ("user", "store", "date", "is_active")
    list_select_related = ("user", "store")
    list_filter = ("date", "is_active")


class IngestionErrorInline(admin.TabularInline):
    model = IngestionError
    extra = 0
    readonly_fields = ("row_number", "column", "message")
    can_delete = False


@admin.register(IngestionJob)
class IngestionJobAdmin(admin.ModelAdmin):
    list_display = ("id", "upload_type", "status", "total_rows", "success_count", "failed_count")
    readonly_fields = ("id", "created_at", "updated_at")
    inlines = (IngestionErrorInline,)
