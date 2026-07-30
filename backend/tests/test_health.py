def test_health_endpoint_returns_a_minimal_readiness_contract(client):
    response = client.get("/api/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
