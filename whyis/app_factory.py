from whyis.config.utils import import_config_module

def app_factory(blueprints=None):
    from whyis.app import App, PROJECT_PATH
    # you can use Empty directly if you wish
    app = App("Whyis", root_path=PROJECT_PATH, instance_relative_config=True)
    #print dir(config)
    import_config_module(app)
    if blueprints:
        app.add_blueprint_list(blueprints)
    app.setup()

    return app
