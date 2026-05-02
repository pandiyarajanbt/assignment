import csv

from django.http import StreamingHttpResponse
from django.shortcuts import get_object_or_404
from rest_framework import status
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.response import Response
from rest_framework.views import APIView

from files.models import IngestionJob
from files.serializers import CSVUploadSerializer
from files.services import process_mapping_upload, process_store_upload, process_user_upload


class Echo:
    def write(self, value):
        return value


class BaseUploadView(APIView):
    parser_classes = (MultiPartParser, FormParser)
    processor = None

    def post(self, request, *args, **kwargs):
        serializer = CSVUploadSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        result = self.processor(serializer.validated_data["file"], request=request)
        response_status = (
            status.HTTP_207_MULTI_STATUS if result["failed_count"] else status.HTTP_201_CREATED
        )
        if result["status"] == IngestionJob.Status.FAILED:
            response_status = status.HTTP_500_INTERNAL_SERVER_ERROR
        return Response(result, status=response_status)


class StoreUploadView(BaseUploadView):
    processor = staticmethod(process_store_upload)


class UserUploadView(BaseUploadView):
    processor = staticmethod(process_user_upload)


class MappingUploadView(BaseUploadView):
    processor = staticmethod(process_mapping_upload)


class ErrorReportCSVView(APIView):
    def get(self, request, job_id):
        job = get_object_or_404(IngestionJob, id=job_id)
        pseudo_buffer = Echo()
        writer = csv.writer(pseudo_buffer)

        def rows():
            yield writer.writerow(["row", "column", "error"])
            for error in job.errors.iterator(chunk_size=5000):
                yield writer.writerow([error.row_number, error.column, error.message])

        response = StreamingHttpResponse(rows(), content_type="text/csv")
        response["Content-Disposition"] = f'attachment; filename="{job.upload_type}-{job.id}-errors.csv"'
        return response
