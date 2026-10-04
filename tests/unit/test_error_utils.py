from app.core.error_utils import http_error_code, sanitize_validation_errors


def test_validation_errors_never_echo_submitted_input():
    raw = [
        {
            "type": "value_error",
            "loc": ("body", "password"),
            "msg": "Value error, Password must contain at least one number.",
            "input": "SuperSecretPassword",       # the user's actual password
            "ctx": {"error": ValueError("boom")},
        }
    ]
    cleaned = sanitize_validation_errors(raw)
    assert cleaned == [
        {"loc": ["body", "password"], "message": "Password must contain at least one number.", "type": "value_error"}
    ]
    assert "SuperSecretPassword" not in repr(cleaned)


def test_http_error_codes():
    assert http_error_code(404) == "not_found"
    assert http_error_code(405) == "method_not_allowed"
    assert http_error_code(418) == "http_418"
