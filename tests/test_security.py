from fastapi.testclient import TestClient
from pydantic import SecretStr

from firecrawlish_extractor.main import create_app
from firecrawlish_extractor.settings import Settings


def test_credentials_are_not_returned_or_logged_for_invalid_urls(
    capsys,
) -> None:
    api_key = "service-secret-not-for-logs"
    username = "url-user-not-for-logs"
    password = "url-password-not-for-logs"
    application = create_app(
        Settings(firecrawlish_api_key=SecretStr(api_key)),
    )

    response = TestClient(application).post(
        "/v2/scrape",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        json={"url": f"https://{username}:{password}@example.com/"},
    )
    logs = capsys.readouterr().out

    assert response.status_code == 400
    for secret in (api_key, username, password):
        assert secret not in response.text
        assert secret not in logs
