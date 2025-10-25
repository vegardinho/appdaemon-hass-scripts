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
            "text": text,
            "expires_at": expires
        }


        # Convert custom_status to JSON and ensure UTF-8 encoding
        custom_status_json = json.dumps(custom_status, ensure_ascii=False).encode('utf-8')

        # Make the request
        try:
            response = requests.put(
                api_endpoint, 
                data=custom_status_json, 
                headers={
                    'Authorization': f'Bearer {self.token}', 
                    'Content-Type': 'application/json'
                },
                timeout=10
            )
            
            
            # Check for authentication errors specifically
            if response.status_code == 401:
                error_msg = "Mattermost API token is expired or invalid. Please generate a new Personal Access Token from Mattermost (Profile → Security → Personal Access Tokens) and update mm_token in secrets.yaml"
                print(f"✗ {error_msg}")
                raise ValueError(error_msg)
            
            response.raise_for_status()  # Raise exception for other 4xx/5xx status codes
            
            # Log the response
            try:
                print(f"✓ Mattermost status updated successfully: {response.json()}")
            except Exception:
                print(f"✓ Mattermost status updated (status code: {response.status_code})")
        except ValueError:
            # Re-raise ValueError (token expired) so it bubbles up
            raise
        except requests.exceptions.RequestException as e:
            error_msg = f"Error updating Mattermost status: {e}"
            print(f"✗ {error_msg}")
            if hasattr(e, 'response') and e.response is not None:
                print(f"  Response text: {e.response.text}")
            raise Exception(error_msg)
        except Exception as e:
            error_msg = f"Unexpected error in set_status: {e}"
            print(f"✗ {error_msg}")
            raise