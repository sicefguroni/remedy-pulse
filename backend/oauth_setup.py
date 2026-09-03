"""
oauth_setup.py — Run this ONCE to authorize Remedy Pulse against the Google
account that manages Remedy's Business Profile listings.

Prerequisites:
1. A Google Cloud project with the "Google My Business API" (Business
   Profile APIs) enabled. As of recent years this requires requesting
   access via Google's form — it is NOT auto-enabled for new projects.
   See: https://developers.google.com/my-business/content/prereqs
2. An OAuth 2.0 Client ID (type: Desktop app) downloaded as JSON from
   Cloud Console -> APIs & Services -> Credentials.
3. .env configured (copy .env.example -> .env and fill in the path to
   that downloaded JSON file).

This opens a browser window for you to log in as the Google account that
owns/manages Remedy's listings, and saves a refresh token to disk so
fetch_owned_reviews.py can run unattended afterward.

Usage:
    pip install -r requirements.txt
    python oauth_setup.py
"""

import os

from dotenv import load_dotenv
from google_auth_oauthlib.flow import InstalledAppFlow

load_dotenv()

CLIENT_SECRETS_FILE = os.getenv("GOOGLE_CLIENT_SECRETS_FILE", "./client_secret.json")
TOKEN_FILE = os.getenv("GOOGLE_TOKEN_FILE", "./token.json")

# This is the scope Business Profile management calls need.
SCOPES = ["https://www.googleapis.com/auth/business.manage"]


def main():
    if not os.path.exists(CLIENT_SECRETS_FILE):
        raise SystemExit(
            f"Client secrets file not found at '{CLIENT_SECRETS_FILE}'.\n"
            "Download it from Cloud Console -> APIs & Services -> Credentials\n"
            "and set GOOGLE_CLIENT_SECRETS_FILE in .env to point at it."
        )

    flow = InstalledAppFlow.from_client_secrets_file(CLIENT_SECRETS_FILE, SCOPES)
    # Opens a local browser window for the login/consent screen.
    credentials = flow.run_local_server(port=0)

    with open(TOKEN_FILE, "w") as f:
        f.write(credentials.to_json())

    print(f"Success. Refresh token saved to {TOKEN_FILE}.")
    print("Keep this file out of version control — it's a live credential.")


if __name__ == "__main__":
    main()
