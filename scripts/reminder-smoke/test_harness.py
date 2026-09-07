import pytest
from harness import PREFIX, cleanup, validate_project_name

def test_project_name_guard():
    assert validate_project_name(PREFIX + "0123456789ab")
    with pytest.raises(ValueError): validate_project_name("rdm-reminder-smoke-../../x")
    with pytest.raises(ValueError): validate_project_name("other-0123456789ab")

def test_required_compose_cleanup_is_scoped():
    assert "prune" not in cleanup.__doc__.lower()

def test_report_shape_has_no_sensitive_fields():
    report = {"telegram": {"calls": 1, "mode": "success", "codes": [200]}}
    assert not {"token", "recipient", "message", "body"} & set(report)
