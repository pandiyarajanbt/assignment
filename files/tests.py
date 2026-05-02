from io import StringIO
from pathlib import Path

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import RequestFactory, TestCase
from rest_framework import status
from rest_framework.test import APIClient

from files.models import City, IngestionJob, PermanentJourneyPlan, Store, StoreBrand, User
from files.services import process_mapping_upload, process_store_upload, process_user_upload


def csv_file(name, content):
    return SimpleUploadedFile(name, content.encode("utf-8"), content_type="text/csv")


def data_csv_file(name):
    path = Path(__file__).resolve().parent.parent / "data" / name
    return SimpleUploadedFile(name, path.read_bytes(), content_type="text/csv")


class IngestionServiceTests(TestCase):
    def setUp(self):
        self.request = RequestFactory().post("/")

    def test_store_upload_normalizes_lookup_names_and_skips_invalid_rows(self):
        content = StringIO()
        content.write(
            "store_id,store_external_id,name,title,store_brand,store_type,city,state,country,region,latitude,longitude\n"
        )
        content.write(
            "STR-1,EXT-1,store-one,Store One, Chennai Brand ,Supermarket,Chennai,Tamil Nadu,India,South,12.971599,77.594566\n"
        )
        content.write(
            "STR-2,EXT-2,store-two,Store Two,chennai brand,Mini, chennai ,Tamil Nadu,India,South,200,77.594566\n"
        )

        result = process_store_upload(csv_file("stores.csv", content.getvalue()), request=self.request)

        self.assertEqual(result["total_rows"], 2)
        self.assertEqual(result["success_count"], 1)
        self.assertEqual(result["failed_count"], 1)
        self.assertEqual(Store.objects.count(), 1)
        self.assertEqual(StoreBrand.objects.count(), 1)
        self.assertEqual(City.objects.count(), 1)
        self.assertEqual(result["errors"][0]["column"], "latitude")

    def test_user_upload_validates_email_and_phone(self):
        content = (
            "username,first_name,last_name,email,user_type,phone_number,supervisor_username,is_active\n"
            "manager,Man,Ager,manager@example.com,1,+919999999999,,true\n"
            "agent,Ag,Ent,not-an-email,2,+919999999998,manager,true\n"
        )

        result = process_user_upload(csv_file("users.csv", content), request=self.request)

        self.assertEqual(result["success_count"], 1)
        self.assertEqual(result["failed_count"], 1)
        self.assertEqual(User.objects.count(), 1)
        self.assertEqual(result["errors"][0]["column"], "email")

    def test_user_upload_validates_user_type_choices_and_lengths(self):
        long_username = "u" * 151
        content = (
            "username,first_name,last_name,email,user_type,phone_number,supervisor_username,is_active\n"
            "badtype,Type,User,badtype@example.com,99,+919999999999,,true\n"
            f"{long_username},Long,User,long@example.com,1,+919999999998,,true\n"
        )

        result = process_user_upload(csv_file("users.csv", content), request=self.request)

        self.assertEqual(result["success_count"], 0)
        self.assertEqual(result["failed_count"], 2)
        self.assertEqual(User.objects.count(), 0)
        self.assertCountEqual(
            [error["column"] for error in result["errors"]],
            ["user_type", "username"],
        )

    def test_store_upload_validates_field_lengths_before_bulk_insert(self):
        long_title = "t" * 256
        content = (
            "store_id,store_external_id,name,title,store_brand,store_type,city,state,country,region,latitude,longitude\n"
            f"STR-1,EXT-1,store-one,{long_title},Brand,Supermarket,Chennai,Tamil Nadu,India,South,12.971599,77.594566\n"
        )

        result = process_store_upload(csv_file("stores.csv", content), request=self.request)

        self.assertEqual(result["success_count"], 0)
        self.assertEqual(result["failed_count"], 1)
        self.assertEqual(Store.objects.count(), 0)
        self.assertEqual(result["errors"][0]["column"], "title")

    def test_assignment_sample_files_ingest_in_required_order(self):
        stores_result = process_store_upload(data_csv_file("stores_master.csv"), request=self.request)
        users_result = process_user_upload(data_csv_file("users_master.csv"), request=self.request)
        mappings_result = process_mapping_upload(
            data_csv_file("store_user_mapping.csv"), request=self.request
        )

        self.assertEqual(stores_result["total_rows"], 100)
        self.assertEqual(users_result["total_rows"], 30)
        self.assertEqual(mappings_result["total_rows"], 150)
        self.assertGreater(stores_result["failed_count"], 0)
        self.assertGreater(users_result["failed_count"], 0)
        self.assertGreater(mappings_result["failed_count"], 0)
        self.assertEqual(Store.objects.count(), stores_result["success_count"])
        self.assertEqual(User.objects.count(), users_result["success_count"])
        self.assertEqual(PermanentJourneyPlan.objects.count(), mappings_result["success_count"])

    def test_mapping_upload_requires_existing_user_and_store(self):
        process_store_upload(
            csv_file(
                "stores.csv",
                "store_id,store_external_id,name,title,store_brand,store_type,city,state,country,region,latitude,longitude\n"
                "STR-1,EXT-1,store-one,Store One,Brand,Supermarket,Chennai,Tamil Nadu,India,South,12.971599,77.594566\n",
            ),
            request=self.request,
        )
        process_user_upload(
            csv_file(
                "users.csv",
                "username,first_name,last_name,email,user_type,phone_number,supervisor_username,is_active\n"
                "agent,Ag,Ent,agent@example.com,2,+919999999998,,true\n",
            ),
            request=self.request,
        )

        result = process_mapping_upload(
            csv_file(
                "mappings.csv",
                "username,store_id,date,is_active\n"
                "agent,STR-1,2026-03-21,true\n"
                "missing,STR-1,2026-03-22,true\n",
            ),
            request=self.request,
        )

        self.assertEqual(result["success_count"], 1)
        self.assertEqual(result["failed_count"], 1)
        self.assertEqual(PermanentJourneyPlan.objects.count(), 1)
        self.assertEqual(result["errors"][0]["column"], "username")

    def test_store_upload_rejects_duplicate_store_and_external_ids(self):
        content = (
            "store_id,store_external_id,name,title,store_brand,store_type,city,state,country,region,latitude,longitude\n"
            "STR-1,EXT-1,store-one,Store One,Brand,Supermarket,Chennai,Tamil Nadu,India,South,12.971599,77.594566\n"
            "STR-1,EXT-2,store-two,Store Two,Brand,Mini,Chennai,Tamil Nadu,India,South,12.971599,77.594566\n"
            "STR-3,EXT-1,store-three,Store Three,Brand,Mini,Chennai,Tamil Nadu,India,South,12.971599,77.594566\n"
        )

        result = process_store_upload(csv_file("stores.csv", content), request=self.request)

        self.assertEqual(result["success_count"], 1)
        self.assertEqual(result["failed_count"], 2)
        self.assertEqual(Store.objects.count(), 1)
        self.assertEqual(
            [error["column"] for error in result["errors"]],
            ["store_id", "store_external_id"],
        )

    def test_missing_required_columns_are_reported(self):
        content = "store_id,name\nSTR-1,Store One\n"

        result = process_store_upload(csv_file("stores.csv", content), request=self.request)

        self.assertEqual(result["total_rows"], 0)
        self.assertEqual(result["success_count"], 0)
        self.assertEqual(result["failed_count"], 1)
        self.assertIn("store_external_id", result["errors"][0]["column"])

    def test_user_upload_resolves_same_batch_supervisor(self):
        content = (
            "username,first_name,last_name,email,user_type,phone_number,supervisor_username,is_active\n"
            "manager,Man,Ager,manager@example.com,1,+919999999999,,true\n"
            "agent,Ag,Ent,agent@example.com,2,+919999999998,manager,true\n"
        )

        result = process_user_upload(csv_file("users.csv", content), request=self.request)

        self.assertEqual(result["success_count"], 2)
        self.assertEqual(result["failed_count"], 0)
        agent = User.objects.get(username="agent")
        self.assertEqual(agent.supervisor.username, "manager")

    def test_user_upload_rejects_bad_supervisor_dependencies(self):
        content = (
            "username,first_name,last_name,email,user_type,phone_number,supervisor_username,is_active\n"
            "manager,Man,Ager,manager@example.com,1,+919999999999,missing,true\n"
            "agent,Ag,Ent,agent@example.com,2,+919999999998,manager,true\n"
            "self,Se,Lf,self@example.com,2,+919999999997,self,true\n"
        )

        result = process_user_upload(csv_file("users.csv", content), request=self.request)

        self.assertEqual(result["success_count"], 0)
        self.assertEqual(result["failed_count"], 3)
        self.assertEqual(User.objects.count(), 0)
        self.assertCountEqual(
            [error["error"] for error in result["errors"]],
            ["Supervisor does not exist", "Supervisor row is invalid", "User cannot supervise themselves"],
        )

    def test_mapping_upload_rejects_duplicate_file_and_database_mappings(self):
        self._create_store_and_user()
        first_result = process_mapping_upload(
            csv_file(
                "mappings.csv",
                "username,store_id,date,is_active\n"
                "agent,STR-1,2026-03-21,true\n"
                "agent,STR-1,2026-03-21,true\n",
            ),
            request=self.request,
        )

        second_result = process_mapping_upload(
            csv_file(
                "mappings.csv",
                "username,store_id,date,is_active\n"
                "agent,STR-1,2026-03-21,true\n",
            ),
            request=self.request,
        )

        self.assertEqual(first_result["success_count"], 1)
        self.assertEqual(first_result["failed_count"], 1)
        self.assertEqual(second_result["success_count"], 0)
        self.assertEqual(second_result["failed_count"], 1)
        self.assertEqual(PermanentJourneyPlan.objects.count(), 1)

    def _create_store_and_user(self):
        process_store_upload(
            csv_file(
                "stores.csv",
                "store_id,store_external_id,name,title,store_brand,store_type,city,state,country,region,latitude,longitude\n"
                "STR-1,EXT-1,store-one,Store One,Brand,Supermarket,Chennai,Tamil Nadu,India,South,12.971599,77.594566\n",
            ),
            request=self.request,
        )
        process_user_upload(
            csv_file(
                "users.csv",
                "username,first_name,last_name,email,user_type,phone_number,supervisor_username,is_active\n"
                "agent,Ag,Ent,agent@example.com,2,+919999999998,,true\n",
            ),
            request=self.request,
        )


class UploadAPITests(TestCase):
    def setUp(self):
        self.client = APIClient()

    def test_store_upload_api_returns_multi_status_with_row_errors(self):
        response = self.client.post(
            "/api/upload/stores/",
            {
                "file": csv_file(
                    "stores.csv",
                    "store_id,store_external_id,name,title,store_brand,store_type,city,state,country,region,latitude,longitude\n"
                    "STR-1,EXT-1,store-one,Store One,Brand,Supermarket,Chennai,Tamil Nadu,India,South,12.971599,77.594566\n"
                    "STR-2,EXT-2,store-two,Store Two,Brand,Mini,Chennai,Tamil Nadu,India,South,100,77.594566\n",
                )
            },
            format="multipart",
        )

        self.assertEqual(response.status_code, status.HTTP_207_MULTI_STATUS)
        self.assertEqual(response.data["success_count"], 1)
        self.assertEqual(response.data["failed_count"], 1)
        self.assertIn("error_report_url", response.data)

    def test_upload_api_rejects_non_csv_files(self):
        response = self.client.post(
            "/api/upload/users/",
            {"file": SimpleUploadedFile("users.txt", b"not,csv", content_type="text/plain")},
            format="multipart",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("file", response.data)

    def test_error_report_download_streams_csv(self):
        result = process_user_upload(
            csv_file(
                "users.csv",
                "username,first_name,last_name,email,user_type,phone_number,supervisor_username,is_active\n"
                "agent,Ag,Ent,bad-email,2,+919999999998,,true\n",
            )
        )
        job = IngestionJob.objects.get(id=result["job_id"])

        response = self.client.get(f"/api/uploads/{job.id}/errors.csv")
        content = b"".join(response.streaming_content).decode("utf-8")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response["Content-Type"], "text/csv")
        self.assertIn("row,column,error", content)
        self.assertIn("2,email,Invalid email format", content)
