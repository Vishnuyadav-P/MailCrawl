from fastapi.testclient import TestClient
from web.server import app
import io

client = TestClient(app)

# Create a dummy CSV file
csv_content = b"email@example.com\ninvalid-email\nanother@test.com"

response = client.post(
    "/api/validate_file",
    files={"file": ("test.csv", io.BytesIO(csv_content), "text/csv")}
)

print(f"Status Code: {response.status_code}")
if response.status_code != 200:
    print(f"Error detail: {response.text}")
else:
    print("Success!")
