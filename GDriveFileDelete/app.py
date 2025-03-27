import os
import json
import boto3
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError


def lambda_handler(event, context):
    """
    Moves all files (any extension/type) from the top-level of the given folder_id
    into a target folder (hard-coded: "11T856S4Jf7tWdMdKZ5p6Dt2BEgvvfXwN"),
    ignoring any subfolders.
    """
    # -----------------------------------------------------------
    # 0. Configuration
    # -----------------------------------------------------------
    folder_id = event.get('FOLDER_ID') or os.getenv('FOLDER_ID')
    target_folder_id = event.get('TARGET_FOLDER_ID') or os.getenv('TARGET_FOLDER_ID')
    if not (folder_id and target_folder_id):
        raise ValueError("No folder_id provided via event or environment.")

    region_name = event.get('REGION_NAME') or os.getenv('REGION_NAME', 'us-east-1')
    secret_name = event.get('SECRET_NAME') or os.getenv('SECRET_NAME', 'google_drive_api')

    # -----------------------------------------------------------
    # 1. Retrieve Service Account credentials from AWS Secrets Manager
    # -----------------------------------------------------------
    try:
        secrets_client = boto3.client('secretsmanager', region_name=region_name)
        response = secrets_client.get_secret_value(SecretId=secret_name)
        secret_json = json.loads(response['SecretString'])

        service_account_info = {
            "type": secret_json["type"],
            "project_id": secret_json["project_id"],
            "private_key_id": secret_json["private_key_id"],
            "private_key": secret_json["private_key"],
            "client_email": secret_json["client_email"],
            "client_id": secret_json["client_id"],
            "auth_uri": secret_json["auth_uri"],
            "token_uri": secret_json["token_uri"],
            "auth_provider_x509_cert_url": secret_json["auth_provider_x509_cert_url"],
            "client_x509_cert_url": secret_json["client_x509_cert_url"],
        }

        credentials = Credentials.from_service_account_info(
            service_account_info,
            scopes=["https://www.googleapis.com/auth/drive"]
        )
        drive_service = build('drive', 'v3', credentials=credentials)
    except Exception as e:
        raise RuntimeError(f"Error retrieving credentials from Secrets Manager: {e}")

    # -----------------------------------------------------------
    # 2. List items in the parent folder (top-level only)
    # -----------------------------------------------------------
    try:
        query = f"'{folder_id}' in parents and trashed = false"
        response = drive_service.files().list(
            q=query,
            spaces='drive',
            fields="files(id, name, mimeType, parents)"
        ).execute()
        items = response.get('files', [])
    except HttpError as error:
        raise RuntimeError(f"Error listing items in parent folder: {error}")

    # -----------------------------------------------------------
    # 3. Move files from 'folder_id' to 'target_folder_id'
    #    (skipping any subfolders)
    # -----------------------------------------------------------
    processed_count = 0
    for info in items:
        file_id = info['id']
        file_name = info['name']
        mime_type = info.get('mimeType')

        # Skip if this item is a folder
        if mime_type == 'application/vnd.google-apps.folder':
            print(f"Skipping subfolder '{file_name}' (id={file_id}).")
            continue

        # Move the file by updating parents
        try:
            current_parents = info.get('parents', [])
            if folder_id not in current_parents:
                print(f"File '{file_name}' is not in folder {folder_id}, skipping.")
                continue

            # Move: remove the old parent (folder_id) and add the new parent (target_folder_id)
            updated_file = drive_service.files().update(
                fileId=file_id,
                addParents=target_folder_id,
                removeParents=folder_id,
                fields='id, parents'
            ).execute()

            processed_count += 1
            new_parents = updated_file.get('parents', [])
            print(
                f"Moved file '{file_name}' (id={file_id}) "
                f"from folder {folder_id} to {target_folder_id}. "
                f"New parents: {new_parents}"
            )
        except HttpError as e:
            print(f"Error moving file '{file_name}' (id={file_id}): {e}")
        except Exception as e:
            print(f"General error moving file '{file_name}' (id={file_id}): {e}")

    return {
        "statusCode": 200,
        "body": json.dumps({
            "message": f"Moved {processed_count} files from folder {folder_id} to {target_folder_id}.",
            "totalItemsFound": len(items)
        })
    }
