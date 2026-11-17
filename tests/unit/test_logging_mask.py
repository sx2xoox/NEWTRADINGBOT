import logging

from kbot.core.logging import SecretMaskingFilter


def test_registered_secrets_masked():
    f = SecretMaskingFilter(["APPKEY_SECRET_VALUE", "abcd"])
    assert f.mask("key=APPKEY_SECRET_VALUE, x=abcd") == "key=***, x=***"


def test_generic_patterns_masked():
    f = SecretMaskingFilter()
    assert "sk-ant-api03" not in f.mask("using sk-ant-api03-AbCdEfGh123456")
    assert f.mask("Authorization: Bearer eyJhbGciOi.abc.def") == "Authorization: Bearer ***"
    assert f.mask("GET /api/list.json?crtfc_key=0123456789abcdef&x=1") == "GET /api/list.json?crtfc_key=***&x=1"
    assert f.mask('{"appsecret": "zzzzzzzzzz"}') == '{"appsecret": "***"}'


def test_filter_masks_log_record_args():
    f = SecretMaskingFilter(["TOPSECRET123"])
    rec = logging.LogRecord("t", logging.INFO, __file__, 1, "token=%s", ("TOPSECRET123",), None)
    f.filter(rec)
    assert rec.getMessage() == "token=***"
