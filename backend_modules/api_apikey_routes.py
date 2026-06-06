def register_apikey_api_routes(app, services):
    def api_add_apikey():
        return services.create_apikey_for_user()

    def api_add_multi_apikey():
        return services.create_apikey_for_user(require_broker=True)

    def api_edit_apikey():
        return services.update_apikey_for_user()

    def api_edit_multi_apikey():
        return services.update_apikey_for_user(require_broker=True)

    app.add_url_rule('/api_add_apikey', view_func=api_add_apikey, methods=['POST'])
    app.add_url_rule('/api_add_multi_apikey', view_func=api_add_multi_apikey, methods=['POST'])
    app.add_url_rule('/api_edit_apikey', view_func=api_edit_apikey, methods=['POST'])
    app.add_url_rule('/api_edit_multi_apikey', view_func=api_edit_multi_apikey, methods=['POST'])
