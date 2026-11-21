"""
tests/test_ads.py
-----------------
Reklama (target) natijalari modulining testlari. Haqiqiy Meta kerak emas: soxta javoblar.

Ishga tushirish:
    python -m tests.test_ads
"""

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

TMP = tempfile.mkdtemp(prefix="jarvis-ads-test-")
os.environ["DATA_DIR"] = TMP
os.environ.pop("META_ADS_TOKEN", None)
os.environ.pop("META_AD_ACCOUNT_ID", None)

import ads_api  # noqa: E402

TOKEN = "EAA" + "x" * 40
calls = []


class FakeResp:
    def __init__(self, body, status=200):
        self.body, self.status_code = body, status

    def json(self):
        return self.body


def fake_get(responses):
    def get(url, params=None, timeout=None):
        calls.append((url, dict(params or {})))
        for key, resp in responses:
            if key(url, params or {}):
                return resp
        return FakeResp({"data": []})
    return get


def good_responses():
    total = {"data": [{"reach": "48200", "impressions": "91300", "clicks": "1240", "inline_link_clicks": "860",
                       "spend": "42.50", "ctr": "1.358", "cpm": "0.46",
                       "actions": [{"action_type": "link_click", "value": "860"},
                                   {"action_type": "onsite_conversion.messaging_conversation_started_7d",
                                    "value": "37"}]}]}
    camps = {"data": [
        {"campaign_id": "2", "campaign_name": "Rolikli krasofka", "reach": "17200", "impressions": "30000",
         "clicks": "340", "spend": "14.40"},
        {"campaign_id": "1", "campaign_name": "G100 samokat Reels", "reach": "31000", "impressions": "61300",
         "clicks": "900", "spend": "28.10",
         "actions": [{"action_type": "onsite_conversion.messaging_conversation_started_7d", "value": "29"}]},
    ]}
    info = {"name": "bolalar_dunyosi", "currency": "USD", "account_status": 1, "amount_spent": "4250"}
    return [
        (lambda u, p: u.endswith("/insights") and p.get("level") == "campaign", FakeResp(camps)),
        (lambda u, p: u.endswith("/insights"), FakeResp(total)),
        (lambda u, p: True, FakeResp(info)),
    ]


def test_not_configured():
    assert not ads_api.is_configured()
    assert ads_api.account_id() == "act_3201139930118828"
    try:
        ads_api.get_summary("7")
        raise AssertionError("token yo'q bo'lsa xato bo'lishi kerak")
    except ads_api.AdsError as e:
        assert e.fatal
    print("  ✅ Token yo'q: ulanmagan deb aytadi, so'rov yubormaydi")


def test_set_token_and_summary():
    ads_api.requests.get = fake_get(good_responses())
    name = ads_api.set_token(TOKEN)
    assert name == "bolalar_dunyosi"
    assert ads_api.is_configured() and ads_api.get_token() == TOKEN

    calls.clear()
    s = ads_api.get_summary("yesterday")
    assert all(p.get("access_token") == TOKEN for _, p in calls)
    assert any(p.get("date_preset") == "yesterday" for _, p in calls)
    t = s["total"]
    assert t["reach"] == 48200 and t["clicks"] == 1240 and t["spend"] == 42.5
    assert t["messages"] == 37 and t["cost_per_message"] == round(42.5 / 37, 2)
    assert t["cpc"] == round(42.5 / 1240, 2)
    assert [c["name"] for c in s["campaigns"]] == ["G100 samokat Reels", "Rolikli krasofka"]
    assert s["account"]["problem"] is False and s["label"] == "Kecha"

    text = ads_api.summary_text(s)
    assert "Sarflandi: 42.50 $" in text and "Qamrov: 48 200" in text and "Direktga yozganlar: 37" in text
    assert TOKEN not in text
    print("  ✅ Kecha: sarf, qamrov, klik, direktga yozganlar to'g'ri hisoblandi")


def test_unpaid_account_warning():
    resp = good_responses()
    resp[-1] = (lambda u, p: True, FakeResp({"name": "x", "currency": "USD", "account_status": 3}))
    ads_api.requests.get = fake_get(resp)
    s = ads_api.get_summary("7")
    assert s["account"]["problem"] and s["account"]["status"] == "to'lov o'tmagan"
    assert "to'lov o'tmagan" in ads_api.summary_text(s)
    print("  ✅ To'lov o'tmagan kabinet haqida ogohlantiradi")


def test_errors_hide_token():
    ads_api.requests.get = fake_get([(lambda u, p: True, FakeResp(
        {"error": {"code": 190, "message": "Invalid OAuth access_token=" + TOKEN}}, 400))])
    try:
        ads_api.get_summary("7")
        raise AssertionError
    except ads_api.AdsError as e:
        assert TOKEN not in str(e) and e.fatal
    ads_api.requests.get = fake_get([(lambda u, p: True, FakeResp(
        {"error": {"code": 100, "message": "bad thing access_token=" + TOKEN}}, 400))])
    try:
        ads_api.get_summary("7")
        raise AssertionError
    except ads_api.AdsError as e:
        assert TOKEN not in str(e)
    print("  ✅ Xato matnida token chiqmaydi")


def test_empty_period():
    ads_api.requests.get = fake_get([(lambda u, p: u.endswith("/insights"), FakeResp({"data": []})),
                                     (lambda u, p: True, FakeResp({"name": "x", "currency": "USD",
                                                                    "account_status": 1}))])
    s = ads_api.get_summary("today")
    assert s["total"]["spend"] == 0 and s["campaigns"] == []
    assert "reklama yurmagan" in ads_api.summary_text(s)
    print("  ✅ Reklama yurmagan kun: bo'sh natija, xato emas")


if __name__ == "__main__":
    test_not_configured()
    test_set_token_and_summary()
    test_unpaid_account_warning()
    test_errors_hide_token()
    test_empty_period()
    print("\n✅ Reklama testlari o'tdi")
