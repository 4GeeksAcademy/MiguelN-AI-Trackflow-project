def authenticated_headers(client):
    credentials = {
        "email": "supplier-audit@trackflow.com",
        "password": "supplier-password-42",
    }
    registered = client.post("/users", json=credentials)
    assert registered.status_code == 201
    token = client.post("/auth/login", json=credentials).json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


def test_create_and_list_return_directory_projection_while_detail_keeps_safe_fields(
    client,
):
    headers = authenticated_headers(client)
    payload = {
        "name": "Estafeta",
        "country": "USA",
        "categories": ["carrier_last_mile"],
        "rate_per_shipment": 4.5,
        "currency": "USD",
        "status": "active",
        "service_zone": "Northern Mexico",
        "contact_email": "dispatch@example.com",
        "notes": "Negotiated terms",
    }
    directory_fields = {
        "id",
        "name",
        "country",
        "categories",
        "rate_per_shipment",
        "currency",
        "status",
        "service_zone",
    }

    created = client.post("/suppliers", headers=headers, json=payload)
    assert created.status_code == 201, created.text
    assert set(created.json()) == directory_fields

    listed = client.get("/suppliers", headers=headers)
    assert listed.status_code == 200
    assert len(listed.json()) == 1
    assert set(listed.json()[0]) == directory_fields
    assert listed.json()[0] == created.json()

    detail = client.get(f"/suppliers/{created.json()['id']}", headers=headers)
    assert detail.status_code == 200
    assert set(detail.json()) == directory_fields | {
        "contact_email",
        "notes",
        "updated_at",
    }


def test_delete_routes_document_no_content_responses(client):
    specification = client.get("/openapi.json").json()

    for path in ("/users/{user_id}", "/suppliers/{supplier_id}"):
        response = specification["paths"][path]["delete"]["responses"]["204"]
        assert "content" not in response
