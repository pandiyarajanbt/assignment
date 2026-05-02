from rest_framework import serializers


class CSVUploadSerializer(serializers.Serializer):
    file = serializers.FileField()

    def validate_file(self, uploaded_file):
        if not uploaded_file.name.lower().endswith(".csv"):
            raise serializers.ValidationError("Only CSV files are supported.")
        return uploaded_file
