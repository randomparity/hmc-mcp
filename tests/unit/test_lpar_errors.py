"""Direct contracts for LPAR write-error translation."""

from hmc_mcp.errors import HMCError
from hmc_mcp.operations.lpar.errors import translate_lpar_write_error


def test_lpar_write_error_translation_preserves_406_response_body():
    translated = translate_lpar_write_error(HMCError("original", 406, body="detail"))

    assert translated.status_code == 406
    assert translated.body == "detail"
    assert "HMC_SCHEMA_VERSION=V1_0" in str(translated)


def test_lpar_write_error_translation_preserves_other_errors_unchanged():
    error = HMCError("original", 409, body="conflict")

    assert translate_lpar_write_error(error) is error
