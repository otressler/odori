def orbit_access_allowed(request):
    """Restrict cross-household telemetry to the platform operator."""
    return bool(request.user.is_authenticated and request.user.is_superuser)


class OrbitDatabaseRouter:
    """Keep optional Orbit telemetry storage isolated from application data."""

    orbit_app_label = "orbit"

    def db_for_read(self, model, **hints):
        if model._meta.app_label == self.orbit_app_label:
            return "orbit"
        return None

    def db_for_write(self, model, **hints):
        if model._meta.app_label == self.orbit_app_label:
            return "orbit"
        return None

    def allow_migrate(self, db, app_label, model_name=None, **hints):
        if app_label == self.orbit_app_label:
            return db == "orbit"
        if db == "orbit":
            return False
        return None