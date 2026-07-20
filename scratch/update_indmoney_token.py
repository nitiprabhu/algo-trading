import os
import sys

sys.path.insert(0, '/Users/nithish-prabhu/Downloads/intra-day')

from services.chartedge_core.database import set_indmoney_token

# Retrieve token from environment variable to avoid hardcoding secrets
token = os.getenv("INDMONEY_TOKEN")
if not token:
    print("Error: INDMONEY_TOKEN environment variable is not set!")
    sys.exit(1)

result = set_indmoney_token(token)
if result:
    print("Token successfully updated.")
else:
    print("Failed to update token.")
