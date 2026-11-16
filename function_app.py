
import azure.functions as func
from azure.storage.blob import BlobServiceClient, ContentSettings
from sendgrid import SendGridAPIClient
from sendgrid.helpers.mail import Mail
from datetime import timedelta
import json
import os
import uuid
import logging

from datetime import datetime, timezone

app = func.FunctionApp(
    http_auth_level=func.AuthLevel.ANONYMOUS
)


@app.route(route="submit", methods=["POST"])
def submit_form(req: func.HttpRequest) -> func.HttpResponse:

    try:
        data = req.get_json()

        name = data.get("name", "").strip()
        email = data.get("email", "").strip()
        message = data.get("message", "").strip()

        if not name or not email or not message:
            return func.HttpResponse(
                json.dumps({"error": "All fields are required."}),
                status_code=400,
                mimetype="application/json"
            )

        if "@" not in email or "." not in email:
            return func.HttpResponse(
                json.dumps({"error": "Please enter a valid email address."}),
                status_code=400,
                mimetype="application/json"
            )

        submission_id = str(uuid.uuid4())

        submission = {
            "id": submission_id,
            "name": name,
            "email": email,
            "message": message,
            "submittedAt": datetime.now(timezone.utc).isoformat()
        }

        connection_string = os.environ.get("AzureWebJobsStorage")

        if not connection_string:
            raise RuntimeError("Azure Storage is not configured.")

        blob_service = BlobServiceClient.from_connection_string(
            connection_string
        )

        container_client = blob_service.get_container_client(
            "form-submissions"
        )

        blob_name = f"incoming/submission-{submission_id}.json"

        blob_client = container_client.get_blob_client(blob_name)

        blob_client.upload_blob(
            json.dumps(submission, indent=4),
            overwrite=False,
            content_settings=ContentSettings(
                content_type="application/json"
            )
        )

        return func.HttpResponse(
            json.dumps({
                "status": "success",
                "message": "Submission saved successfully.",
                "submissionId": submission_id
            }),
            status_code=201,
            mimetype="application/json"
        )

    except ValueError:
        return func.HttpResponse(
            json.dumps({"error": "Invalid JSON request."}),
            status_code=400,
            mimetype="application/json"
        )

    except Exception:
        logging.exception("Failed to process submission.")

        return func.HttpResponse(
            json.dumps({"error": "Unable to process submission."}),
            status_code=500,
            mimetype="application/json"
        )




@app.timer_trigger(
    schedule="0 */15 * * * *",
    arg_name="mytimer",
    run_on_startup=False,
    use_monitor=True
)
def daily_report(mytimer: func.TimerRequest):

    logging.info("Daily report timer triggered.")

    try:
        connection_string = os.environ.get("AzureWebJobsStorage")

        blob_service = BlobServiceClient.from_connection_string(
            connection_string
        )

        container_client = blob_service.get_container_client(
            "form-submissions"
        )

        now = datetime.now(timezone.utc)
        start_time = now - timedelta(hours=24)

        submissions = []

        for blob in container_client.list_blobs(
            name_starts_with="incoming/"
        ):

            blob_client = container_client.get_blob_client(blob.name)

            content = blob_client.download_blob().readall()

            submission = json.loads(content.decode("utf-8"))

            submitted_at = datetime.fromisoformat(
                submission["submittedAt"].replace("Z", "+00:00")
            )

            if start_time <= submitted_at <= now:
                submissions.append(submission)

        api_key = os.environ.get("SENDGRID_API_KEY")
        from_email = os.environ.get("SENDGRID_FROM_EMAIL")
        to_email = os.environ.get("SENDGRID_TO_EMAIL")

        if not all([api_key, from_email, to_email]):
            raise RuntimeError("SendGrid configuration is incomplete.")

        if submissions:
            report_lines = []

            for item in submissions:
                report_lines.append(
                    f"""
Name: {item['name']}
Email: {item['email']}
Message: {item['message']}
Submitted At: {item['submittedAt']}
"""
                )

            report_content = "\n".join(report_lines)

        else:
            report_content = (
                "No new submissions were received in the last 24 hours."
            )

        email_message = Mail(
            from_email=from_email,
            to_emails=to_email,
            subject=f"Daily Submission Report - {now.date()}",
            plain_text_content=f"""
Daily Submission Report

Total submissions in the last 24 hours: {len(submissions)}

{report_content}
"""
        )

        sendgrid_client = SendGridAPIClient(api_key)
        response = sendgrid_client.send(email_message)

        logging.info(
            "Daily report sent. SendGrid status: %s",
            response.status_code
        )

    except Exception:
        logging.exception("Failed to generate daily report.")
        raise
