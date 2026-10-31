import pytest
from google.api_core.exceptions import from_http_status

from retail_agent.db.bigquery import as_query_error


def bigquery_error(status: int, reason: str | None, message: str):
    """An error shaped like the ones the BigQuery client raises: a status, plus BigQuery's reason and message."""
    if reason is None:
        return from_http_status(status, message)
    return from_http_status(
        status, f"{message}; reason: {reason}, message: {message}", errors=[{"reason": reason, "message": message}]
    )


def test_a_reason_we_have_never_seen_goes_to_the_model():
    error = bigquery_error(400, "someNewReason", "Something about the query is off")

    assert str(as_query_error(error)) == "Something about the query is off"


def test_a_query_over_the_bytes_cap_goes_to_the_model_although_the_client_raises_a_500():
    error = bigquery_error(500, "bytesBilledLimitExceeded", "Query exceeded limit for bytes billed: 1000000000.")

    assert str(as_query_error(error)) == "Query exceeded limit for bytes billed: 1000000000."


@pytest.mark.parametrize(
    ("status", "reason"),
    [(500, "backendError"), (429, "rateLimitExceeded"), (403, "accessDenied"), (403, "quotaExceeded")],
)
def test_infrastructure_errors_are_not_sent_to_the_model(status, reason):
    assert as_query_error(bigquery_error(status, reason, "Not the query's fault")) is None


@pytest.mark.parametrize("status", [500, 503, 401, 403, 429])
def test_without_a_reason_infrastructure_statuses_are_not_sent_to_the_model(status):
    assert as_query_error(bigquery_error(status, None, "Not the query's fault")) is None


@pytest.mark.parametrize("status", [400, 404, 413])
def test_without_a_reason_every_other_status_goes_to_the_model(status):
    assert str(as_query_error(bigquery_error(status, None, "Something about the query"))) == "Something about the query"
