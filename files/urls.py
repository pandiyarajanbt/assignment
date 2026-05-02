from django.urls import path

from files.views import ErrorReportCSVView, MappingUploadView, StoreUploadView, UserUploadView

urlpatterns = [
    path("upload/stores/", StoreUploadView.as_view(), name="upload-stores"),
    path("upload/users/", UserUploadView.as_view(), name="upload-users"),
    path("upload/mappings/", MappingUploadView.as_view(), name="upload-mappings"),
    path("uploads/<uuid:job_id>/errors.csv", ErrorReportCSVView.as_view(), name="upload-errors-csv"),
]
