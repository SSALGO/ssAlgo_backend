class ApiAuthManager:
    def __init__(self, users_collection, decode_token_func):
        self.users_collection = users_collection
        self.decode_token = decode_token_func

    def get_bearer_token(self, request):
        auth_header = request.headers.get('Authorization', '')
        if not auth_header.startswith('Bearer '):
            return None
        return auth_header.split(' ', 1)[1].strip()

    def get_user_from_access_token(self, access_token):
        try:
            token_claims = self.decode_token(access_token)
        except Exception:
            return None
        identity = token_claims.get('sub')
        if not identity:
            return None
        return self.users_collection.find_one({'username': identity})
