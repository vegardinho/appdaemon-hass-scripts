import requests
import json

class MattermostCustom:
    def __init__(self, user_id, token):
        self.user_id = user_id
        self.token = token

    def set_status(self, emoji=None, text=None, expires=None):
        api_endpoint = f"https://mattermost.uio.no/api/v4/users/{self.user_id}/status/custom"

        # Base custom status
        custom_status = {
            "emoji": emoji,
            "text": text
        }

        # Conditionally add expires_at if it is not None
        if expires is not None:
            custom_status["expires_at"] = expires

        # Convert custom_status to JSON and ensure UTF-8 encoding
        custom_status_json = json.dumps(custom_status, ensure_ascii=False).encode('utf-8')

        # Make the request
        response = requests.put(
            api_endpoint, 
            data=custom_status_json, 
            headers={
                'Authorization': f'Bearer {self.token}', 
                'Content-Type': 'application/json'
            }
        )

        # Log the response
        print(response.json())