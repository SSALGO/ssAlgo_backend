import argparse
import datetime
import hashlib
import json
import os
import socket
import time
from urllib.parse import parse_qs, urlparse

import pymongo
import pyotp
import requests
from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright


LOGIN_URL = "https://ant.aliceblueonline.com/?appcode={app_code}"
GET_SESSION_URL = "https://a3.aliceblueonline.com/open-api/od/v1/vendor/getUserDetails"
REDIRECT_HOST = "127.0.0.1:5000"
ALICEBLUE_DNS_HOSTS = {
    "a3.aliceblueonline.com",
    "ant.aliceblueonline.com",
}


def resolve_aliceblue_host(hostname):
    response = requests.get(
        f"https://1.1.1.1/dns-query?name={hostname}&type=A",
        headers={"accept": "application/dns-json", "Host": "cloudflare-dns.com"},
        verify=False,
        timeout=8,
    )
    response.raise_for_status()
    payload = response.json()
    addresses = [
        item.get("data")
        for item in payload.get("Answer", [])
        if item.get("type") == 1 and item.get("data")
    ]
    if not addresses:
        raise socket.gaierror(f"No DNS A record for {hostname}")
    return addresses


def chromium_host_resolver_args():
    rules = []
    for hostname in sorted(ALICEBLUE_DNS_HOSTS):
        try:
            addresses = resolve_aliceblue_host(hostname)
        except Exception:
            continue
        if addresses:
            rules.append(f"MAP {hostname} {addresses[0]}")
    return [f"--host-resolver-rules={','.join(rules)}"] if rules else []


def install_aliceblue_dns_fallback():
    original_getaddrinfo = socket.getaddrinfo
    cache = {}

    def resolve_with_doh(hostname):
        cached = cache.get(hostname)
        if cached:
            return cached
        addresses = resolve_aliceblue_host(hostname)
        cache[hostname] = addresses
        return addresses

    def patched_getaddrinfo(host, port, family=0, type=0, proto=0, flags=0):
        try:
            return original_getaddrinfo(host, port, family, type, proto, flags)
        except socket.gaierror:
            hostname = str(host).lower()
            if hostname not in ALICEBLUE_DNS_HOSTS:
                raise
            results = []
            for address in resolve_with_doh(hostname):
                results.extend(original_getaddrinfo(address, port, family, type, proto, flags))
            return results

    socket.getaddrinfo = patched_getaddrinfo


install_aliceblue_dns_fallback()


def mask_value(value):
    if not value:
        return None
    return f"<set:{len(str(value))}>"


def display_value(value, show_secrets=False):
    return value if show_secrets else mask_value(value)


def mask_url_query_value(url, sensitive_keys, show_secrets=False):
    if not url:
        return None
    if show_secrets:
        return url
    parsed = urlparse(url)
    query = parse_qs(parsed.query, keep_blank_values=True)
    parts = []
    for key, values in query.items():
        for value in values:
            masked_value = mask_value(value) if key.lower() in sensitive_keys else value
            parts.append(f"{key}={masked_value or ''}")
    query_string = "&".join(parts)
    base = parsed._replace(query="", fragment="").geturl()
    return f"{base}?{query_string}" if query_string else base


def dismiss_popups(page):
    try:
        page.evaluate(
            """() => {
                for (const selector of ['#wzrk_wrapper', '.wzrk-overlay', '.wzrk-alert']) {
                    document.querySelectorAll(selector).forEach((el) => el.remove());
                }
            }"""
        )
    except PlaywrightError:
        pass


def click_first(page, selectors, timeout=5000):
    for selector in selectors:
        locator = page.locator(selector).first
        try:
            dismiss_popups(page)
            if locator.is_visible(timeout=timeout):
                locator.click()
                return selector
        except PlaywrightTimeoutError:
            continue
        except PlaywrightError:
            try:
                dismiss_popups(page)
                locator.click(force=True, timeout=timeout)
                return selector
            except PlaywrightError:
                continue
    return None


def fill_first(page, selectors, value, timeout=5000):
    for selector in selectors:
        locator = page.locator(selector).first
        try:
            if locator.is_visible(timeout=timeout) and locator.is_enabled(timeout=timeout):
                locator.fill(value)
                return selector
        except PlaywrightTimeoutError:
            continue
    return None


def install_redirect_capture(page):
    captured = {"url": None}

    def remember_url(url):
        lowered = url.lower()
        if "authcode=" in lowered and ("userid=" in lowered or "clientid=" in lowered):
            captured["url"] = url

    page.on("request", lambda request: remember_url(request.url))
    page.on("requestfailed", lambda request: remember_url(request.url))

    def handle_route(route):
        captured["url"] = route.request.url
        route.fulfill(
            status=200,
            content_type="text/html",
            body="<html><body>AliceBlue auth redirect captured.</body></html>",
        )

    page.route(f"**://{REDIRECT_HOST}/**", handle_route)
    return captured


def capture_redirect_url(page, timeout_ms, captured=None):
    captured = captured or install_redirect_capture(page)
    if captured["url"]:
        return captured["url"]
    if "authcode=" in page.url.lower():
        return page.url
    try:
        page.wait_for_function(
            """() => window.location.href.includes('authCode=') || window.location.href.includes('authcode=')""",
            timeout=timeout_ms,
        )
        captured["url"] = page.url
    except PlaywrightTimeoutError:
        if captured["url"]:
            return captured["url"]
        raise
    return captured["url"]


def parse_auth_redirect(redirect_url):
    parsed = urlparse(redirect_url)
    query = parse_qs(parsed.query)
    auth_code = (query.get("authCode") or query.get("authcode") or [""])[0]
    alice_user_id = (query.get("userId") or query.get("userid") or query.get("clientId") or [""])[0]
    return auth_code, alice_user_id


def get_user_session(alice_user_id, auth_code, app_secret, timeout=20):
    result = {
        "session_ok": False,
        "response_keys": [],
        "user_session": None,
        "error": None,
    }
    try:
        checksum = hashlib.sha256(f"{alice_user_id}{auth_code}{app_secret}".encode("utf-8")).hexdigest()
        response = requests.post(
            GET_SESSION_URL,
            json={"checkSum": checksum},
            headers={"Content-Type": "application/json"},
            timeout=timeout,
        )
        session = response.json()
        result["response_keys"] = sorted(session.keys()) if isinstance(session, dict) else [type(session).__name__]
        result["session_ok"] = isinstance(session, dict) and bool(session.get("userSession"))
        result["user_session"] = session.get("userSession") if isinstance(session, dict) else None
        if not result["session_ok"]:
            if isinstance(session, dict):
                result["error"] = session.get("emsg") or session.get("message") or session.get("stat") or session.get("status")
            else:
                result["error"] = f"Unexpected response type: {type(session).__name__}"
    except Exception as exc:
        result["error"] = f"{type(exc).__name__}: {exc}"
    return result


def main():
    parser = argparse.ArgumentParser(description="Headless AliceBlue web login plus Ant-A3 API session login.")
    parser.add_argument("--user", default="kinguniverse129", help="Application username.")
    parser.add_argument("--mongo-uri", default="mongodb://localhost:27017", help="MongoDB URI.")
    parser.add_argument("--db", default="demo", help="MongoDB database name.")
    parser.add_argument("--app-code", default=os.getenv("ALICEBLUE_APP_CODE"), help="AliceBlue App Key/App Code.")
    parser.add_argument("--app-secret", default=os.getenv("ALICEBLUE_APP_SECRET"), help="AliceBlue App Secret Key.")
    parser.add_argument("--alice-user-id", default=os.getenv("ALICEBLUE_USER_ID"), help="AliceBlue login user ID.")
    parser.add_argument("--alice-password", default=os.getenv("ALICEBLUE_PASSWORD"), help="AliceBlue login password.")
    parser.add_argument("--otp", default=os.getenv("ALICEBLUE_OTP"), help="Optional one-time OTP if the login flow asks for it.")
    parser.add_argument("--totp-secret", default=os.getenv("ALICEBLUE_TOTP_SECRET"), help="TOTP secret used to auto-generate OTP.")
    parser.add_argument("--headful", action="store_true", help="Run browser visibly for debugging.")
    parser.add_argument("--save", action="store_true", help="Save returned userId/authCode/userSession/app_key into Mongo.")
    parser.add_argument("--api-only", action="store_true", help="Skip browser login and test saved/provided auth_code directly.")
    parser.add_argument("--auth-code", default=os.getenv("ALICEBLUE_AUTH_CODE"), help="Existing returned authCode for --api-only.")
    parser.add_argument("--show-secrets", action="store_true", help="Print raw credential-bearing URLs and values in local terminal output.")
    parser.add_argument("--timeout-ms", type=int, default=90000, help="Login/redirect timeout.")
    args = parser.parse_args()

    client = pymongo.MongoClient(args.mongo_uri, serverSelectionTimeoutMS=3000)
    db = client[args.db]
    api = dict(db["apis"].find_one({"user": args.user, "broker": "aliceblue"}, {"_id": 0}) or {})

    stored_app_code = api.get("app_key") or api.get("app_code")
    if not stored_app_code and api.get("auth_code") and len(str(api.get("auth_code"))) <= 20:
        stored_app_code = api.get("auth_code")
    app_code = args.app_code or stored_app_code
    app_secret = args.app_secret or api.get("apisecret")
    alice_user_id = args.alice_user_id or api.get("apikey")
    password = args.alice_password or api.get("alice_password") or api.get("password") or api.get("pwd")
    auth_code = args.auth_code or api.get("auth_code")
    totp_secret = args.totp_secret or api.get("totp_key") or api.get("totp_secret")
    otp = args.otp

    result = {
        "user": args.user,
        "inputs": {
            "app_code": display_value(app_code, args.show_secrets),
            "app_secret": display_value(app_secret, args.show_secrets),
            "alice_user_id": display_value(alice_user_id, args.show_secrets),
            "password": display_value(password, args.show_secrets),
            "otp": display_value(otp or ("generated" if totp_secret else None), args.show_secrets),
            "totp_secret": display_value(totp_secret, args.show_secrets),
        },
        "captured": {
            "auth_code": None,
            "alice_user_id": None,
        },
        "urls": {
            "login_url_template": LOGIN_URL,
            "login_url": LOGIN_URL.format(app_code=display_value(app_code, args.show_secrets)),
            "session_generation_url": GET_SESSION_URL,
            "redirect_host": REDIRECT_HOST,
            "captured_redirect_url": None,
        },
        "saved": False,
        "session_ok": False,
        "response_keys": [],
        "error": None,
    }

    required_values = [
        ("app_secret", app_secret),
        ("alice_user_id", alice_user_id),
    ]
    if args.api_only:
        required_values.append(("auth_code", auth_code))
    else:
        required_values.extend([
            ("app_code", app_code),
            ("alice_password", password),
        ])
    missing = [name for name, value in required_values if not str(value or "").strip()]
    if missing:
        result["error"] = f"Missing required input(s): {', '.join(missing)}"
        print(json.dumps(result, indent=2))
        return 1

    try:
        returned_user_id = alice_user_id
        verify_response_keys = []

        if not args.api_only:
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(
                    headless=not args.headful,
                    args=chromium_host_resolver_args(),
                )
                context = browser.new_context(ignore_https_errors=True)
                page = context.new_page()
                captured_redirect = install_redirect_capture(page)
                page.goto(LOGIN_URL.format(app_code=app_code), wait_until="domcontentloaded", timeout=args.timeout_ms)

                filled_user = fill_first(
                    page,
                    [
                        "#new_login_userId",
                        "input[placeholder*='User ID']",
                        "input[placeholder*='Mobile']",
                        "input[type='text']",
                    ],
                    alice_user_id,
                    timeout=10000,
                )
                filled_password = fill_first(
                    page,
                    [
                        "#new_login_password",
                        "input[placeholder*='Password']",
                        "input[type='password']",
                    ],
                    password,
                    timeout=10000,
                )
                if not filled_user or not filled_password:
                    browser.close()
                    result["error"] = "Could not find AliceBlue login fields."
                    print(json.dumps(result, indent=2))
                    return 2

                click_first(page, ["button:has-text('Next')", "text=Next", "button:has-text('Login')", "text=Login"], timeout=10000)

                if otp or totp_secret:
                    current_otp = otp
                    if not current_otp and totp_secret:
                        cleaned_secret = str(totp_secret).strip().replace(" ", "")
                        remaining = 30 - (time.time() % 30)
                        if remaining < 6:
                            time.sleep(remaining + 1)
                        current_otp = pyotp.TOTP(cleaned_secret).now()
                    page.wait_for_selector("#new_login_otp", state="visible", timeout=15000)
                    page.locator("#new_login_otp").fill(current_otp)
                    otp_selectors = [
                        "#buttonLabel_Next",
                        "button[type='submit']",
                        "button:has-text('Next')",
                        "button:has-text('Login')",
                        "button:has-text('Submit')",
                        "button:has-text('Verify')",
                    ]
                    clicked_otp = None
                    try:
                        with page.expect_response(
                            lambda response: "/omk/auth/access/topt/verify" in response.url,
                            timeout=args.timeout_ms,
                        ) as verify_info:
                            clicked_otp = click_first(page, otp_selectors, timeout=10000)
                        verify_response = verify_info.value
                        verify_payload = verify_response.json()
                        if isinstance(verify_payload, dict):
                            verify_response_keys = sorted(verify_payload.keys())
                    except PlaywrightTimeoutError:
                        clicked_otp = clicked_otp or click_first(page, otp_selectors, timeout=10000)
                    if not clicked_otp:
                        browser.close()
                        result["error"] = "Could not find AliceBlue OTP submit button."
                        print(json.dumps(result, indent=2))
                        return 2

                if not captured_redirect["url"] and "authcode=" not in page.url.lower():
                    try:
                        page.wait_for_selector("button:has-text('Authorize')", state="visible", timeout=10000)
                        page.locator("button:has-text('Authorize')").click()
                    except PlaywrightTimeoutError:
                        pass

                redirect_url = None
                redirect_url = capture_redirect_url(page, args.timeout_ms, captured_redirect)
                browser.close()

            if redirect_url:
                auth_code, returned_user_id = parse_auth_redirect(redirect_url)
                result["captured"]["auth_code"] = display_value(auth_code, args.show_secrets)
                result["captured"]["alice_user_id"] = returned_user_id or None
                result["urls"]["captured_redirect_url"] = mask_url_query_value(
                    redirect_url,
                    {"authcode", "auth_code", "code", "token", "sessionid", "usersession"},
                    args.show_secrets,
                )
                if not auth_code or not returned_user_id:
                    result["error"] = "Redirect did not include authCode and userId."
                    print(json.dumps(result, indent=2))
                    return 2
        else:
            result["captured"]["auth_code"] = display_value(auth_code, args.show_secrets)
            result["captured"]["alice_user_id"] = returned_user_id or None

        session_result = get_user_session(returned_user_id, auth_code, app_secret)
        result.update({
            key: value
            for key, value in session_result.items()
            if key != "user_session"
        })

        if args.save and result["session_ok"]:
            db["apis"].update_one(
                {"user": args.user, "broker": "aliceblue"},
                {
                    "$set": {
                        "user": args.user,
                        "broker": "aliceblue",
                        "apikey": returned_user_id,
                        "app_key": app_code,
                        "auth_code": auth_code or api.get("auth_code") or "",
                        "apisecret": app_secret,
                        "totp_key": totp_secret or api.get("totp_key") or "",
                        "user_session": session_result["user_session"],
                        "sessionID": session_result["user_session"],
                        "session_date": datetime.datetime.now().strftime("%Y-%m-%d"),
                    }
                },
                upsert=True,
            )
            result["saved"] = True
        elif args.save:
            result["saved"] = False
    except Exception as exc:
        result["error"] = f"{type(exc).__name__}: {exc}"

    print(json.dumps(result, indent=2))
    return 0 if result["session_ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
