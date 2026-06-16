BROKER_REQUIREMENTS = {
    'paper': [],
    'zerodha': [
    ],
    'fyers': [
        {'id': 'fy_id', 'label': 'FY User ID', 'type': 'text'},
        {'id': 'client_id', 'label': 'App ID', 'type': 'text'},
        {'id': 'secret_key', 'label': 'Secret Key', 'type': 'text'},
        {'id': 'totp_key', 'label': 'TOTP Key', 'type': 'text'},
        {'id': 'pin', 'label': 'PIN', 'type': 'password'},
        {'id': 'redirect_uri', 'label': 'Redirect URI', 'type': 'text', 'default_value': 'https://127.0.0.1:5000/'},
    ],
    'angelone': [
        {'id': 'client_id', 'label': 'Client ID', 'type': 'text'},
        {'id': 'apikey', 'label': 'API Key', 'type': 'text'},
        {'id': 'pwd', 'label': 'PIN', 'type': 'password', 'toggle_function': 'togglePassword'},
        {'id': 'totp_key', 'label': 'TOTP Key', 'type': 'text'},
    ],
    'dhan': [
        {'id': 'client_id', 'label': 'Client ID', 'type': 'text'},
        {'id': 'access_token', 'label': 'Access Token', 'type': 'text'},
    ],
    'mofs': [
        {'id': 'client_id', 'label': 'Client ID', 'type': 'text'},
        {'id': 'password', 'label': 'Password', 'type': 'password', 'toggle_function': 'toggleMofsPassword'},
        {'id': '_2_FA', 'label': '2FA (DOB: DD/MM/YYYY)', 'type': 'text'},
        {'id': 'totp_key', 'label': 'TOTP Key', 'type': 'text'},
        {'id': 'api_key', 'label': 'API Key', 'type': 'text'},
    ],
    'aliceblue': [
    ],
    'shoonya': [
        {'id': 'usr', 'label': 'Shoonya ID', 'type': 'text'},
        {'id': 'pwd', 'label': 'Password', 'type': 'password', 'toggle_function': 'togglePassword'},
        {'id': 'factor2', 'label': 'Factor2', 'type': 'text'},
        {'id': 'apikey', 'label': 'Api Key', 'type': 'text'},
    ],
    'smc': [
        {'id': 'client_id', 'label': 'Client ID', 'type': 'text'},
        {'id': 'interactive_key', 'label': 'Interactive API Key', 'type': 'text'},
        {'id': 'interactive_secret', 'label': 'Interactive Secret Key', 'type': 'text'},
        {'id': 'source', 'label': 'Source', 'type': 'text', 'default_value': 'WEBAPI'},
    ],
    'mstock': [
        {'id': 'userid', 'label': 'User ID', 'type': 'text'},
        {'id': 'password', 'label': 'Password', 'type': 'password', 'toggle_function': 'toggleMstockPassword'},
        {'id': 'apikey', 'label': 'API Key', 'type': 'text'},
        {'id': 'eemail', 'label': 'Email', 'type': 'text'},
        {'id': 'epassword', 'label': 'Email Password', 'type': 'password', 'toggle_function': 'toggleEmailPassword'},
    ],
    'delta_exchange_india': [
        {'id': 'api_key', 'label': 'API Key', 'type': 'text'},
        {'id': 'api_secret', 'label': 'API Secret', 'type': 'password'},
    ],
}

BROKER_DISPLAY_NAMES = {
    'paper': 'Paper Trading',
    'aliceblue': 'Aliceblue',
    'angelone': 'Angelone',
    'delta_exchange_india': 'Delta Exchange India',
    'dhan': 'Dhan',
    'fyers': 'Fyers',
    'mofs': 'Motilal Oswal',
    'mstock': 'mStock',
    'shoonya': 'Shoonya',
    'smc': 'SMC',
    'zerodha': 'Zerodha Kite',
}

BROKER_ACTIONS = {
    'fyers': {
        'has_activation': True,
        'activation_url': 'https://api-t1.fyers.in/api/v3/generate-authcode?client_id={client_id}&redirect_uri={redirect_uri}&response_type=code&state=sample_state',
    }
}

BROKER_STATUS = {
    'paper': {'enabled': True, 'status': 'paper_only', 'notes': 'Safe simulated broker with fills, slippage, brokerage, positions, and lifecycle tracking.'},
    'aliceblue': {'enabled': True, 'status': 'redirect_auth', 'notes': 'Connect through AliceBlue redirect login. Password and TOTP storage are disabled.'},
    'angelone': {'enabled': True, 'status': 'wired', 'notes': 'Login and order placement branches exist; needs broker smoke testing.'},
    'dhan': {'enabled': True, 'status': 'wired', 'notes': 'Access-token login and order placement branches exist; needs response handling hardening.'},
    'fyers': {'enabled': True, 'status': 'wired', 'notes': 'Login, token refresh, and order placement branches exist.'},
    'mofs': {'enabled': True, 'status': 'wired', 'notes': 'Login and order placement branches exist; dependency availability must be verified.'},
    'mstock': {'enabled': True, 'status': 'wired', 'notes': 'Login and order placement branches exist; email OTP flow needs production hardening.'},
    'shoonya': {'enabled': True, 'status': 'wired', 'notes': 'Login and order placement branches exist; import-time login was disabled.'},
    'smc': {'enabled': True, 'status': 'wired', 'notes': 'Login and order placement branches exist; dependency availability must be verified.'},
    'zerodha': {'enabled': True, 'status': 'redirect_auth', 'notes': 'Connect through Kite redirect login. Server-side order placement uses encrypted daily access tokens.'},
    'delta_exchange_india': {'enabled': False, 'status': 'coming_soon', 'notes': 'Visible for roadmap only. No login/order adapter exists yet.'},
}

BROKER_ALIASES = {
    'delta': 'delta_exchange_india',
    'alice': 'aliceblue',
    'angel': 'angelone',
    'kite': 'zerodha',
}


def normalize_broker_id(broker):
    normalized = str(broker or '').strip().lower()
    return BROKER_ALIASES.get(normalized, normalized)


def broker_lookup_ids(broker):
    canonical = normalize_broker_id(broker)
    aliases = [alias for alias, target in BROKER_ALIASES.items() if target == canonical]
    return [canonical, *aliases]


def broker_payload():
    return {
        'broker_requirements': BROKER_REQUIREMENTS,
        'broker_actions': BROKER_ACTIONS,
        'broker_display_names': BROKER_DISPLAY_NAMES,
        'broker_status': BROKER_STATUS,
    }
