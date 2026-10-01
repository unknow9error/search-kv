def test_published_catalog_contract_has_typed_responses(app):
    schema = app.openapi()
    for path, method, expected in [
        ("/v1/search", "post", "ListingPage"),
        ("/v1/apartments/{apartment_id}", "get", "Listing"),
        ("/v1/apartments/{apartment_id}/verify", "post", "VerificationResponse"),
    ]:
        result = schema["paths"][path][method]["responses"]["200"]["content"]["application/json"][
            "schema"
        ]
        assert result["$ref"].endswith("/" + expected)
    assert "provenance" in schema["components"]["schemas"]["Listing"]["properties"]
