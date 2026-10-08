def describe_health_check():
    def it_returns_200(client):
        response = client.get("/health/")
        assert response.status_code == 200

    def it_returns_json_with_status_ok(client):
        response = client.get("/health/")
        from plfog.version import VERSION

        assert response.json() == {"status": "ok", "version": VERSION}

    def it_has_correct_content_type(client):
        response = client.get("/health/")
        assert response["Content-Type"] == "application/json"
