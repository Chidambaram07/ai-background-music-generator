import json
from pathlib import Path


def test_checked_in_openapi_exactly_matches_running_app(assessment_client):
    expected = json.loads((Path(__file__).parents[2]/"openapi.yaml").read_text(encoding="utf-8"))
    assert assessment_client.get("/openapi.json").json() == expected
    for path, operations in expected["paths"].items():
        assert path.startswith("/api/v1/")
        if "post" in operations:
            header = next(item for item in operations["post"]["parameters"] if item["name"] == "Idempotency-Key")
            assert header["required"] is True
    assert "seed" in expected["components"]["schemas"]["MusicGenerationRequest"]["properties"]
    assert "audio/wav" in expected["paths"]["/api/v1/music/jobs/{job_id}/mix"]["get"]["responses"]["200"]["content"]
    assert "Location" in expected["paths"]["/api/v1/music/jobs/{job_id}/mix"]["get"]["responses"]["307"]["headers"]
    assert "200" not in expected["paths"]["/api/v1/music/jobs/{job_id}/artifacts/{artifact}"]["get"]["responses"]